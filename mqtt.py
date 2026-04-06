from __future__ import annotations

import socket
import time
from typing import Callable, Dict, Iterable, List, Optional

import requests

from libs.device_binding import enforce_mac_binding
from mqtt_pkg import callbacks, runtime, settings, state, test_subscriber, update
from mqtt_pkg.runtime import AWSIoTClient

CONNECTION_CHECK_INTERVAL = 6  # 5초 × 6 = 30초마다 연결 상태 확인


def log_matterhub_status() -> None:
    if settings.MATTERHUB_ID:
        print(f"matterhub_id 로드됨: {settings.MATTERHUB_ID}")
    else:
        print("matterhub_id 없음 (Claim 프로비저닝 후 .env 등록, 가이드: MATTERHUB_ID_GUIDE.md)")


def _append_unique_topic(topics: List[str], topic: str | None) -> None:
    if topic and topic not in topics:
        topics.append(topic)


def build_subscribe_topics() -> List[str]:
    topics: List[str] = []
    _append_unique_topic(topics, settings.KONAI_TOPIC_REQUEST)
    _append_unique_topic(topics, settings.KONAI_TOPIC_RESPONSE)
    _append_unique_topic(topics, settings.KONAI_TEST_TOPIC_REQUEST)
    _append_unique_topic(topics, settings.KONAI_TEST_TOPIC_RESPONSE)
    if settings.SUBSCRIBE_MATTERHUB_TOPICS and settings.MATTERHUB_ID:
        _append_unique_topic(topics, f"matterhub/{settings.MATTERHUB_ID}/git/update")
        _append_unique_topic(topics, f"matterhub/update/specific/{settings.MATTERHUB_ID}")
    return topics


def _recover_connection_for_subscribe(client_factory: Optional[Callable[[], AWSIoTClient]]) -> None:
    if not client_factory:
        return
    connection = runtime.get_connection()
    if connection:
        try:
            connection.disconnect()
        except Exception:
            pass
    new_connection = client_factory().connect_mqtt()
    runtime.set_connection(new_connection)


def subscribe_topics(
    topics: Iterable[str],
    client_factory: Optional[Callable[[], AWSIoTClient]] = None,
) -> Dict[str, bool]:
    topics = list(topics)
    results: Dict[str, bool] = {}
    for topic in topics:
        max_retries = 3
        base_delay = 1
        results[topic] = False
        for attempt in range(max_retries):
            try:
                runtime.subscribe(topic, callbacks.mqtt_callback)
                results[topic] = True
                break
            except Exception:
                if attempt < max_retries - 1:
                    try:
                        _recover_connection_for_subscribe(client_factory)
                    except Exception:
                        pass
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
    return results


def summarize_subscribe_results(results: Dict[str, bool]) -> tuple[int, int]:
    success_count = sum(1 for success in results.values() if success)
    failed_count = len(results) - success_count
    return success_count, failed_count


def log_subscribe_results(results: Dict[str, bool], phase: str) -> None:
    for index, (topic, success) in enumerate(results.items(), start=1):
        if success:
            print(f"[MQTT][SUBSCRIBE][OK] topic[{index}]={topic}")
        else:
            print(f"[MQTT][SUBSCRIBE][FAIL] topic[{index}]={topic}")


def build_startup_report(aws_client: AWSIoTClient, topics: Iterable[str]) -> List[str]:
    connection_info = aws_client.describe_connection()
    subscribe_topics = list(topics)
    cert_status = "ok" if connection_info["cert_exists"] else "missing"
    key_status = "ok" if connection_info["key_exists"] else "missing"
    ca_status = "ok" if connection_info["ca_exists"] else "missing"
    lines = [
        "[MQTT][INIT] start initialization",
        f"[MQTT][INIT] endpoint={connection_info['endpoint']}",
        f"[MQTT][INIT] client_id={connection_info['client_id']}",
        (
            "[MQTT][INIT] cert_path="
            f"{connection_info['cert_path']} "
            f"cert={cert_status} key={key_status} ca={ca_status}"
        ),
        "[MQTT][SUBSCRIBE] setup start",
    ]
    lines.extend(
        f"[MQTT][SUBSCRIBE] topic[{index}]={topic}"
        for index, topic in enumerate(subscribe_topics, start=1)
    )
    lines.append(f"[MQTT][SUBSCRIBE] count={len(subscribe_topics)}")
    return lines


def log_startup_report(aws_client: AWSIoTClient, topics: Iterable[str]) -> None:
    for line in build_startup_report(aws_client, topics):
        print(line)


def _wait_for_network(timeout_per_check: int = 3, interval: int = 10) -> None:
    """네트워크 연결 가능할 때까지 대기 (부팅 직후 네트워크 미준비 대응)."""
    while True:
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=timeout_per_check)
            print("[MQTT][INIT] network_ready=true")
            return
        except OSError:
            print(f"[MQTT][INIT] network_ready=false retry_after={interval}s")
            time.sleep(interval)


def _wait_for_api(timeout_per_check: int = 2, interval: int = 5) -> None:
    """로컬 API(matterhub-api)가 응답할 때까지 대기."""
    api_url = f"{settings.LOCAL_API_BASE}/local/api/states"
    while True:
        try:
            resp = requests.get(api_url, timeout=timeout_per_check)
            if resp.status_code in (200, 401):
                print("[MQTT][INIT] api_ready=true")
                return
        except Exception:
            pass
        print(f"[MQTT][INIT] api_ready=false retry_after={interval}s")
        time.sleep(interval)


def _connect_with_service_retry(aws_client: AWSIoTClient) -> object:
    """connect_mqtt()를 서비스 레벨에서 무한 재시도 (서비스가 crash하지 않도록)."""
    attempt = 0
    base_delay = 10
    max_delay = 120
    while True:
        try:
            return aws_client.connect_mqtt()
        except Exception as exc:
            attempt += 1
            delay = min(base_delay * (2 ** min(attempt - 1, 6)), max_delay)
            print(
                f"[MQTT][CONNECT] service_retry attempt={attempt} "
                f"error={type(exc).__name__} next_retry={delay}s"
            )
            time.sleep(delay)


def main() -> None:
    if not enforce_mac_binding():
        raise SystemExit(1)

    log_matterhub_status()
    update.start_queue_worker()

    aws_client = AWSIoTClient()
    topics = build_subscribe_topics()
    log_startup_report(aws_client, topics)
    _wait_for_network()
    _wait_for_api()
    connection = _connect_with_service_retry(aws_client)
    runtime.set_connection(connection)

    print(f"[MQTT][INIT] matterhub_id={settings.MATTERHUB_ID or '(미설정)'}")
    subscribe_results = subscribe_topics(topics, client_factory=lambda: aws_client)
    log_subscribe_results(subscribe_results, phase="startup")
    success_count, failed_count = summarize_subscribe_results(subscribe_results)
    overall_status = "success" if failed_count == 0 else "partial_failed"
    print(
        "[MQTT][SUBSCRIBE] complete "
        f"total={len(subscribe_results)} "
        f"success={success_count} failed={failed_count} status={overall_status}"
    )

    state.publish_bootstrap_all_states()
    test_subscriber.start_konai_test_subscriber_if_enabled()

    try:
        connection_check_counter = 0
        while True:
            # 연결 끊김 감지 시 즉시 재연결 시도
            if not runtime.is_connected():
                connection_check_counter = CONNECTION_CHECK_INTERVAL

            state.publish_device_state()
            connection_check_counter += 1
            if connection_check_counter >= CONNECTION_CHECK_INTERVAL:
                runtime.check_mqtt_connection(
                    topics,
                    callbacks.mqtt_callback,
                    lambda: aws_client,
                )
                connection_check_counter = 0
            time.sleep(5)
    except KeyboardInterrupt:
        print("프로그램 종료")
        current_connection = runtime.get_connection()
        if current_connection:
            current_connection.disconnect()


if __name__ == "__main__":
    main()
