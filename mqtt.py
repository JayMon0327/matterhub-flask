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


def build_subscribe_topics() -> List[str]:
    topics: List[str] = []
    if settings.KONAI_TOPIC_REQUEST:
        topics.append(settings.KONAI_TOPIC_REQUEST)
    if settings.KONAI_TEST_TOPIC_REQUEST:
        topics.append(settings.KONAI_TEST_TOPIC_REQUEST)
    if settings.SUBSCRIBE_MATTERHUB_TOPICS and settings.MATTERHUB_ID:
        topics.extend([
            f"matterhub/{settings.MATTERHUB_ID}/git/update",
            f"matterhub/update/specific/{settings.MATTERHUB_ID}",
        ])
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


def main() -> None:
    log_matterhub_status()
    update.start_queue_worker()

    aws_client = AWSIoTClient()
    connection = aws_client.connect_mqtt()
    runtime.set_connection(connection)

    topics = build_subscribe_topics()
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
