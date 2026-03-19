#!/usr/bin/env python3
"""
코나이/와츠매터 토픽 요청·응답 테스트

요청을 delta 토픽으로 발행하고, 허브가 응답을 보내는 reported 토픽을 구독해 수신합니다.
(코나이 토픽 규격: correlation_id 필수, entity_id 있으면 단일 조회)

사용법:
  venv/bin/python3 test_konai_request.py              # 전체 조회
  venv/bin/python3 test_konai_request.py sensor.xxx   # 단일 entity 조회
"""
import json
import os
import sys
import time

try:
    from dotenv import load_dotenv
except ImportError:
    print("venv/bin/python3 run_provision.py 처럼 가상환경 Python으로 실행하세요.")
    sys.exit(1)

load_dotenv(dotenv_path='.env')

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
os.chdir(_script_dir)

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

from mqtt_pkg import settings as mqtt_settings

# 구독 = delta(요청 수신), 발행 = reported(응답 수신) → 테스트는 요청을 delta로 보내고 reported를 구독
KONAI_TOPIC_REQUEST = mqtt_settings.KONAI_TOPIC_REQUEST   # 요청 발행 대상 (허브가 구독 중)
KONAI_TOPIC_RESPONSE = mqtt_settings.KONAI_TOPIC_RESPONSE  # 응답 수신용 구독 (허브가 발행)
CERT_PATH = "konai_certificates/"
ENDPOINT = "a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com"
# main mqtt와 충돌 방지용 client_id (접미사 -test 추가)
BASE_CLIENT_ID = os.environ.get("KONAI_CLIENT_ID", "c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL").strip('"')
CLIENT_ID = f"{BASE_CLIENT_ID}-test"


def main():
    entity_id = sys.argv[1].strip() if len(sys.argv) > 1 else None
    correlation_id = f"test-{int(time.time())}"

    cert_file = os.path.join(CERT_PATH, "cert.pem")
    key_file = os.path.join(CERT_PATH, "key.pem")
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        print(f"konai_certificates/cert.pem, key.pem 필요")
        sys.exit(1)

    request = {"correlation_id": correlation_id}
    if entity_id:
        request["entity_id"] = entity_id

    received = []

    def on_message(topic, payload, **kwargs):
        try:
            body = json.loads(payload.decode("utf-8"))
        except Exception:
            body = payload.decode("utf-8", errors="ignore")
        received.append(body)
        print("\n[수신 응답]")
        print(json.dumps(body, ensure_ascii=False, indent=2))

    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

    conn = mqtt_connection_builder.mtls_from_path(
        endpoint=ENDPOINT,
        cert_filepath=cert_file,
        pri_key_filepath=key_file,
        client_bootstrap=client_bootstrap,
        client_id=CLIENT_ID,
        keep_alive_secs=300,
    )

    print(f"연결: {ENDPOINT}, client_id={CLIENT_ID}")
    print(f"요청 발행( delta ): {KONAI_TOPIC_REQUEST}")
    print(f"응답 구독(reported): {KONAI_TOPIC_RESPONSE}")
    print(f"요청: {json.dumps(request, ensure_ascii=False)}")
    print("")

    connect_future = conn.connect()
    connect_future.result(timeout=10)

    sub_future, _ = conn.subscribe(
        topic=KONAI_TOPIC_RESPONSE, qos=mqtt.QoS.AT_LEAST_ONCE, callback=on_message
    )
    sub_future.result(timeout=5)

    pub_future, _ = conn.publish(
        topic=KONAI_TOPIC_REQUEST,
        payload=json.dumps(request, ensure_ascii=False),
        qos=mqtt.QoS.AT_LEAST_ONCE,
    )
    pub_future.result(timeout=5)

    timeout = time.time() + 10
    while not received and time.time() < timeout:
        time.sleep(0.2)

    conn.disconnect()

    if received:
        print("\n응답 수신 완료")
    else:
        print("\n응답 없음 (mqtt.py 구독·응답 확인)")


if __name__ == "__main__":
    main()
