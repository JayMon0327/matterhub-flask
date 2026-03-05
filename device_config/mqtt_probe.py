from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_settings():
    from mqtt_pkg import settings

    return settings


def resolve_probe_targets(topic_mode: str, custom_topic: Optional[str] = None) -> list[tuple[str, str]]:
    if topic_mode == "both":
        targets = [
            ("request", resolve_probe_topic("request", custom_topic)),
            ("response", resolve_probe_topic("response", custom_topic)),
        ]
        unique_targets: list[tuple[str, str]] = []
        seen_topics: set[str] = set()
        for label, topic in targets:
            if topic in seen_topics:
                continue
            seen_topics.add(topic)
            unique_targets.append((label, topic))
        return unique_targets
    return [(topic_mode, resolve_probe_topic(topic_mode, custom_topic))]


def resolve_probe_topic(topic_mode: str, custom_topic: Optional[str] = None) -> str:
    settings = _load_settings()
    if topic_mode in {"request", "delta"}:
        topic = settings.KONAI_TOPIC_REQUEST
    elif topic_mode in {"response", "reported"}:
        topic = settings.KONAI_TOPIC_RESPONSE
    elif topic_mode == "test-request":
        topic = settings.KONAI_TEST_TOPIC_REQUEST
    elif topic_mode == "test-response":
        topic = settings.KONAI_TEST_TOPIC_RESPONSE or settings.KONAI_TEST_TOPIC_REQUEST
    elif topic_mode == "custom":
        topic = (custom_topic or "").strip()
        if not topic:
            raise ValueError("--topic-mode custom 사용 시 --topic 값이 필요합니다.")
    else:
        raise ValueError(f"지원하지 않는 topic_mode 입니다: {topic_mode}")

    normalized_topic = (topic or "").strip()
    if not normalized_topic:
        raise ValueError(f"{topic_mode} 용 토픽이 비어 있습니다.")
    return normalized_topic


def build_probe_plan(
    connection_info: dict[str, object],
    topic_mode: str,
    topic: str,
    listen_seconds: float,
    uses_default_client_id: bool,
) -> list[str]:
    lines = [
        "[PROBE] MQTT 토픽 점검 시작",
        f"[PROBE] topic_mode={topic_mode}",
        f"[PROBE] topic={topic}",
        f"[PROBE] endpoint={connection_info['endpoint']}",
        f"[PROBE] client_id={connection_info['client_id']}",
        (
            "[PROBE] cert_path="
            f"{connection_info['cert_path']} "
            f"(cert={'yes' if connection_info['cert_exists'] else 'no'}, "
            f"key={'yes' if connection_info['key_exists'] else 'no'}, "
            f"ca={'yes' if connection_info['ca_exists'] else 'no'})"
        ),
        f"[PROBE] listen_seconds={listen_seconds:.1f}",
    ]
    if uses_default_client_id:
        lines.append(
            "[PROBE] 주의: 기본 client_id를 사용합니다. matterhub-mqtt.service와 동시에 실행하지 마세요."
        )
    return lines


def build_probe_result_lines(label: str, topic: str, success: bool) -> list[str]:
    result = "success" if success else "failed"
    return [f"[PROBE] result label={label} status={result} topic={topic}"]


def print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def run_probe(
    topic_mode: str,
    topic: str,
    listen_seconds: float,
    client_id: Optional[str] = None,
) -> int:
    from awscrt import mqtt

    from mqtt_pkg.runtime import AWSIoTClient

    mqtt_client = AWSIoTClient()
    if client_id:
        mqtt_client.client_id = client_id.strip()

    connection_info = mqtt_client.describe_connection()
    print_lines(
        build_probe_plan(
            connection_info=connection_info,
            topic_mode=topic_mode,
            topic=topic,
            listen_seconds=listen_seconds,
            uses_default_client_id=not bool(client_id),
        )
    )

    connection = mqtt_client.connect_mqtt()

    def on_message(received_topic, payload, **kwargs):
        print(f"[PROBE] 메시지 수신: {received_topic}")
        print(payload.decode("utf-8", errors="ignore"))

    try:
        subscribe_future, _ = connection.subscribe(
            topic=topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_message,
        )
        subscribe_future.result(timeout=10)
        print(f"[PROBE] SUBSCRIBE 성공: {topic}")
        if listen_seconds > 0:
            print(f"[PROBE] 메시지 대기: {listen_seconds:.1f}초")
            time.sleep(listen_seconds)
        return 0
    except Exception as exc:
        print(f"[PROBE] SUBSCRIBE 실패: {topic} - {exc!r} ({type(exc).__name__})")
        return 1
    finally:
        try:
            connection.disconnect()
        except Exception as exc:
            print(f"[PROBE] disconnect 경고: {exc!r} ({type(exc).__name__})")


def run_probe_targets(
    targets: Iterable[tuple[str, str]],
    listen_seconds: float,
    client_id: Optional[str] = None,
) -> int:
    overall_success = True
    for label, topic in targets:
        probe_exit_code = run_probe(
            topic_mode=label,
            topic=topic,
            listen_seconds=listen_seconds,
            client_id=client_id,
        )
        success = probe_exit_code == 0
        print_lines(build_probe_result_lines(label, topic, success))
        overall_success = overall_success and success
    return 0 if overall_success else 1


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Konai MQTT request/response topic subscription probe"
    )
    parser.add_argument(
        "--topic-mode",
        choices=[
            "request",
            "response",
            "both",
            "delta",
            "reported",
            "test-request",
            "test-response",
            "custom",
        ],
        default="request",
        help="점검할 토픽 종류",
    )
    parser.add_argument(
        "--topic",
        default="",
        help="custom 모드에서 사용할 MQTT 토픽",
    )
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=5.0,
        help="SUBSCRIBE 성공 후 추가로 메시지를 기다릴 시간",
    )
    parser.add_argument(
        "--client-id",
        default="",
        help="필요 시 probe 전용 client_id를 지정",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    resolved_targets = resolve_probe_targets(args.topic_mode, args.topic)
    resolved_client_id = (args.client_id or "").strip() or None
    return run_probe_targets(
        targets=resolved_targets,
        listen_seconds=max(0.0, args.listen_seconds),
        client_id=resolved_client_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
