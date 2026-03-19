from __future__ import annotations

import json
import threading
import time
from typing import Optional

import os

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

from . import settings
from .provisioning import AWSProvisioningClient


def _build_konai_test_subscriber_connection() -> Optional[mqtt.Connection]:
    provisioning_client = AWSProvisioningClient()
    has_cert, cert_file, key_file = provisioning_client.check_certificate()
    if not has_cert:
        print("[TEST] device 인증서 없음, Claim 프로비저닝 실행")
        success = provisioning_client.provision_device()
        if not success:
            print("❌ [TEST] Claim 프로비저닝 실패 - 테스트 구독을 시작하지 않습니다.")
            return None
        has_cert, cert_file, key_file = provisioning_client.check_certificate()
        if not has_cert:
            print("❌ [TEST] 프로비저닝 후에도 device 인증서를 찾을 수 없습니다.")
            return None

    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

    test_client_id = os.environ.get("AWS_TEST_CLIENT_ID", "whatsmatter-nipa-test-subscriber")
    print(
        f"[TEST] AWS IoT Core 테스트 구독 MQTT 연결 생성 "
        f"(endpoint={provisioning_client.endpoint}, client_id={test_client_id})"
    )

    mqtt_conn = mqtt_connection_builder.mtls_from_path(
        endpoint=provisioning_client.endpoint,
        cert_filepath=cert_file,
        pri_key_filepath=key_file,
        client_bootstrap=client_bootstrap,
        client_id=test_client_id,
        keep_alive_secs=300,
    )
    return mqtt_conn


def _run_konai_test_subscriber_loop() -> None:
    test_topic = settings.KONAI_TEST_TOPIC_REQUEST or settings.KONAI_TEST_TOPIC
    if not test_topic:
        print("[TEST] KONAI_TEST_TOPIC 미설정, 테스트 구독 스킵")
        return

    try:
        mqtt_conn = _build_konai_test_subscriber_connection()
        if mqtt_conn is None:
            return

        print("[TEST] AWS IoT Core 테스트 구독 MQTT 연결 시도")
        connect_future = mqtt_conn.connect()
        connect_future.result()
        print("✅ [TEST] 테스트 구독용 MQTT 연결 성공")

        def on_message(topic, payload, **kwargs):
            try:
                body = json.loads(payload.decode("utf-8"))
            except Exception:
                body = payload.decode("utf-8", errors="ignore")
            print("\n📩 [TEST 수신] ===============================")
            print(f"topic = {topic}")
            print(json.dumps(body, ensure_ascii=False, indent=2))
            print("===========================================\n")

        print(f"[TEST] 테스트 토픽 구독: {test_topic}")
        subscribe_future, _ = mqtt_conn.subscribe(
            topic=test_topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_message,
        )
        subscribe_future.result()
        print(f"✅ [TEST] 테스트 토픽 구독 완료: {test_topic}")
        print("[TEST] 테스트 구독 루프 진입")

        while True:
            time.sleep(5)

    except Exception as exc:
        print(f"❌ [TEST] 테스트 구독 루프 오류: {exc}")


def start_konai_test_subscriber_if_enabled() -> None:
    if os.environ.get("ENABLE_KONAI_TEST_SUBSCRIBER", "0") != "1":
        return

    print("[TEST] ENABLE_KONAI_TEST_SUBSCRIBER=1, 테스트 구독 스레드 시작")
    worker = threading.Thread(target=_run_konai_test_subscriber_loop, name="konai-test-subscriber")
    worker.daemon = True
    worker.start()

