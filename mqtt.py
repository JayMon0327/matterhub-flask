from __future__ import annotations

import time
from typing import Callable, Dict, Iterable, List, Optional

from libs.device_binding import enforce_mac_binding
from mqtt_pkg import callbacks, runtime, settings, state, test_subscriber, update
from mqtt_pkg.runtime import AWSIoTClient


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
    _append_unique_topic(topics, settings.MQTT_TOPIC_SUBSCRIBE)
    _append_unique_topic(topics, settings.MQTT_TOPIC_PUBLISH)
    _append_unique_topic(topics, settings.MQTT_TEST_TOPIC_SUBSCRIBE)
    _append_unique_topic(topics, settings.MQTT_TEST_TOPIC_PUBLISH)
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


def main() -> None:
    if not enforce_mac_binding():
        raise SystemExit(1)

    log_matterhub_status()
    update.start_queue_worker()

    aws_client = AWSIoTClient()
    topics = build_subscribe_topics()
    log_startup_report(aws_client, topics)
    connection = aws_client.connect_mqtt()
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
    state.publish_device_states_bulk()
    test_subscriber.start_test_subscriber_if_enabled()

    try:
        connection_check_counter = 0
        while True:
            state.publish_device_state()
            state.publish_device_states_bulk()
            connection_check_counter += 1
            if connection_check_counter >= 12:
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
