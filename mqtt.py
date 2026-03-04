from __future__ import annotations

import time
from typing import Iterable, List

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
    _append_unique_topic(topics, settings.KONAI_TOPIC_REQUEST)
    _append_unique_topic(topics, settings.KONAI_TEST_TOPIC_REQUEST)
    if settings.SUBSCRIBE_MATTERHUB_TOPICS and settings.MATTERHUB_ID:
        _append_unique_topic(topics, f"matterhub/{settings.MATTERHUB_ID}/git/update")
        _append_unique_topic(topics, f"matterhub/update/specific/{settings.MATTERHUB_ID}")
    return topics


def subscribe_topics(topics: Iterable[str]) -> None:
    topics = list(topics)
    for topic in topics:
        max_retries = 3
        base_delay = 1
        for attempt in range(max_retries):
            try:
                runtime.subscribe(topic, callbacks.mqtt_callback)
                break
            except Exception as exc:
                print(
                    f"❌ 토픽 구독 실패 (시도 {attempt + 1}/{max_retries}): {topic} - {exc!r} ({type(exc).__name__})"
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"구독 재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ 토픽 구독 최종 실패: {topic}")


def build_startup_report(aws_client: AWSIoTClient, topics: Iterable[str]) -> List[str]:
    connection_info = aws_client.describe_connection()
    subscribe_topics = list(topics)
    lines = [
        "[MQTT] 시작 설정",
        f"[MQTT] endpoint={connection_info['endpoint']}",
        f"[MQTT] client_id={connection_info['client_id']}",
        (
            "[MQTT] cert_path="
            f"{connection_info['cert_path']} "
            f"(cert={'yes' if connection_info['cert_exists'] else 'no'}, "
            f"key={'yes' if connection_info['key_exists'] else 'no'}, "
            f"ca={'yes' if connection_info['ca_exists'] else 'no'})"
        ),
        f"[MQTT] request_topic={settings.KONAI_TOPIC_REQUEST or '(미설정)'}",
        f"[MQTT] response_topic={settings.KONAI_TOPIC_RESPONSE or '(미설정)'}",
        f"[MQTT] test_request_topic={settings.KONAI_TEST_TOPIC_REQUEST or '(미설정)'}",
        f"[MQTT] test_response_topic={settings.KONAI_TEST_TOPIC_RESPONSE or '(미설정)'}",
        f"[MQTT] matterhub_id={settings.MATTERHUB_ID or '(미설정)'}",
        f"[MQTT] subscribe_count={len(subscribe_topics)}",
    ]
    lines.extend(
        f"[MQTT] subscribe[{index}]={topic}"
        for index, topic in enumerate(subscribe_topics, start=1)
    )
    return lines


def log_startup_report(aws_client: AWSIoTClient, topics: Iterable[str]) -> None:
    for line in build_startup_report(aws_client, topics):
        print(line)


def main() -> None:
    log_matterhub_status()
    update.start_queue_worker()

    aws_client = AWSIoTClient()
    topics = build_subscribe_topics()
    log_startup_report(aws_client, topics)
    connection = aws_client.connect_mqtt()
    runtime.set_connection(connection)

    print(f"matterhub_id: {settings.MATTERHUB_ID or '(미설정)'}")
    print(f"토픽 구독 시작 (총 {len(topics)}개)")
    subscribe_topics(topics)
    print("모든 토픽 구독 완료")

    state.publish_bootstrap_all_states()
    test_subscriber.start_konai_test_subscriber_if_enabled()

    try:
        connection_check_counter = 0
        while True:
            state.publish_device_state()
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
