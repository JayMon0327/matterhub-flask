from __future__ import annotations

import os
import random
import time
from typing import Callable, Iterable, Optional, Sequence

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

from . import settings


SUBSCRIBED_TOPICS: set[str] = set()
global_mqtt_connection: Optional[mqtt.Connection] = None
is_connected_flag: bool = False
reconnect_attempts: int = 0
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 30  # seconds


class AWSIoTClient:
    """Konai certificate based MQTT client (no provisioning)."""

    def __init__(self) -> None:
        self.cert_path = os.environ.get("KONAI_CERT_PATH", "konai_certificates/")
        self.endpoint = os.environ.get(
            "KONAI_ENDPOINT", "a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com"
        ).strip('"')
        self.client_id = os.environ.get(
            "KONAI_CLIENT_ID",
            "c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL",
        ).strip('"')

    def connect_mqtt(self) -> mqtt.Connection:
        has_cert, cert_file, key_file = self._check_certificate()
        if not has_cert:
            raise FileNotFoundError(
                "konai_certificates/cert.pem 또는 key.pem이 없습니다. "
                "코나이 인증서를 konai_certificates/ 디렉토리에 넣어 주세요."
            )

        event_loop_group = io.EventLoopGroup(1)
        host_resolver = io.DefaultHostResolver(event_loop_group)
        client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

        def on_interrupted(connection, error, **kwargs):
            mark_connected(False)
            print(f"MQTT 연결 끊김: {error}")
            if SUBSCRIBED_TOPICS:
                topics = ", ".join(sorted(SUBSCRIBED_TOPICS))
                print(f"구독 중이던 토픽: {topics}")
            print(f"재연결 시도 ({reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS})")

        def on_resumed(connection, return_code, session_present, **kwargs):
            mark_connected(return_code == 0)
            if return_code == 0:
                reset_reconnect_attempts()
                print(
                    f"✅ MQTT 연결 재개됨 (return_code={return_code}, session_present={session_present})"
                )
            else:
                print(f"❌ MQTT 재연결 실패 (return_code={return_code})")

        mtls_kw = dict(
            endpoint=self.endpoint,
            cert_filepath=cert_file,
            pri_key_filepath=key_file,
            client_bootstrap=client_bootstrap,
            client_id=self.client_id,
            keep_alive_secs=300,
            on_connection_interrupted=on_interrupted,
            on_connection_resumed=on_resumed,
        )

        ca_path = os.path.join(self.cert_path, "ca_cert.pem")
        if os.path.exists(ca_path):
            mtls_kw["ca_filepath"] = ca_path

        mqtt_conn = mqtt_connection_builder.mtls_from_path(**mtls_kw)

        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            try:
                print(f"새 인증서로 MQTT 연결 시도 중... (시도 {attempt + 1}/{max_retries})")
                if attempt > 0:
                    random_delay = random.uniform(1, 3)
                    print(f"연결 재시도 전 랜덤 대기: {random_delay:.1f}초")
                    time.sleep(random_delay)

                connect_future = mqtt_conn.connect()
                connect_future.result(timeout=10)
                print("새 인증서로 MQTT 연결 성공")
                set_connection(mqtt_conn)
                mark_connected(True)
                reset_reconnect_attempts()
                return mqtt_conn

            except Exception as connection_error:
                print(f"❌ MQTT 연결 실패 (시도 {attempt + 1}/{max_retries}): {connection_error}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    print(f"재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    raise

        raise RuntimeError("MQTT 연결 실패: 최대 재시도 횟수를 초과했습니다.")

    def _check_certificate(self) -> tuple[bool, Optional[str], Optional[str]]:
        cert_file = os.path.join(self.cert_path, "cert.pem")
        key_file = os.path.join(self.cert_path, "key.pem")
        if os.path.exists(cert_file) and os.path.exists(key_file):
            return True, cert_file, key_file
        return False, None, None


def set_connection(connection: Optional[mqtt.Connection]) -> None:
    global global_mqtt_connection
    global_mqtt_connection = connection


def get_connection() -> Optional[mqtt.Connection]:
    return global_mqtt_connection


def mark_connected(status: bool) -> None:
    global is_connected_flag
    is_connected_flag = status


def reset_reconnect_attempts() -> None:
    global reconnect_attempts
    reconnect_attempts = 0


def increase_reconnect_attempt() -> None:
    global reconnect_attempts
    reconnect_attempts += 1


def is_connected() -> bool:
    return bool(global_mqtt_connection) and is_connected_flag


def subscribe(topic: str, callback: Callable) -> None:
    connection = get_connection()
    if connection is None:
        raise RuntimeError("MQTT connection is not established.")

    subscribe_future, _ = connection.subscribe(
        topic=topic,
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=callback,
    )
    subscribe_future.result(timeout=10)
    SUBSCRIBED_TOPICS.add(topic)
    print(f"✅ SUBSCRIBE 성공: {topic}")


def resubscribe(topics: Sequence[str], callback: Callable) -> None:
    for topic in topics:
        try:
            print(f"SUBSCRIBE 재요청: {topic}")
            subscribe(topic, callback)
        except Exception as exc:
            print(f"❌ 토픽 재구독 실패: {topic} - {exc!r} ({type(exc).__name__})")


def check_mqtt_connection(
    topics: Iterable[str],
    callback: Callable,
    client_factory: Optional[Callable[[], AWSIoTClient]] = None,
) -> bool:
    """Ensure MQTT connection is alive, reconnecting and resubscribing if necessary."""
    if is_connected():
        reset_reconnect_attempts()
        return True

    print(f"MQTT 재연결 시도: {reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS}")
    if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
        print("MQTT 재연결 실패: 최대 시도 횟수 초과")
        return False

    increase_reconnect_attempt()

    connection = get_connection()
    if connection:
        try:
            connection.disconnect()
        except Exception:
            pass

    client = client_factory() if client_factory else AWSIoTClient()
    try:
        client.connect_mqtt()
    except Exception as exc:
        print(f"❌ 재연결 실패: {exc}")
        return False

    reset_reconnect_attempts()
    resubscribe(list(topics), callback)
    print("MQTT 재연결 성공")
    return True

