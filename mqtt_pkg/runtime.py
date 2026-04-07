from __future__ import annotations

import os
import random
import time
from typing import Callable, Dict, Iterable, Optional, Sequence

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

from . import settings


SUBSCRIBED_TOPICS: set[str] = set()
global_mqtt_connection: Optional[mqtt.Connection] = None
is_connected_flag: bool = False
reconnect_attempts: int = 0
_pending_resubscribe: bool = False

# 재연결 설정: 무한 재시도 + 점진적 백오프
RECONNECT_BACKOFF_THRESHOLD = 5   # 이 횟수까지는 즉시 재시도
RECONNECT_BASE_DELAY = 10         # 백오프 시작 대기(초)
RECONNECT_MAX_DELAY = 300         # 최대 대기(초, 5분)


def _certificate_paths(cert_path: str) -> tuple[str, str, str]:
    normalized_cert_path = os.path.normpath(cert_path)
    cert_file = os.path.join(normalized_cert_path, "cert.pem")
    key_file = os.path.join(normalized_cert_path, "key.pem")
    ca_file = os.path.join(normalized_cert_path, "ca_cert.pem")
    return cert_file, key_file, ca_file


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

    def describe_connection(self) -> dict[str, object]:
        cert_file, key_file, ca_file = _certificate_paths(self.cert_path)
        return {
            "endpoint": self.endpoint,
            "client_id": self.client_id,
            "cert_path": os.path.normpath(self.cert_path),
            "cert_file": cert_file,
            "key_file": key_file,
            "ca_file": ca_file,
            "cert_exists": os.path.exists(cert_file),
            "key_exists": os.path.exists(key_file),
            "ca_exists": os.path.exists(ca_file),
        }

    def connect_mqtt(self) -> mqtt.Connection:
        has_cert, cert_file, key_file = self._check_certificate()
        if not has_cert:
            connection_info = self.describe_connection()
            raise FileNotFoundError(
                "konai_certificates/cert.pem 또는 key.pem이 없습니다. "
                "코나이 인증서를 konai_certificates/ 디렉토리에 넣어 주세요. "
                f"(cert={connection_info['cert_file']}, key={connection_info['key_file']})"
            )

        event_loop_group = io.EventLoopGroup(1)
        host_resolver = io.DefaultHostResolver(event_loop_group)
        client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

        def on_interrupted(connection, error, **kwargs):
            mark_connected(False)
            print(f"[MQTT][CONNECT][INTERRUPTED] error={error}")
            if SUBSCRIBED_TOPICS:
                topics = ", ".join(sorted(SUBSCRIBED_TOPICS))
                print(f"[MQTT][CONNECT][INTERRUPTED] subscribed_topics={topics}")
            print(
                f"[MQTT][CONNECT] reconnect_attempt={reconnect_attempts + 1}"
            )

        def on_resumed(connection, return_code, session_present, **kwargs):
            global _pending_resubscribe
            mark_connected(return_code == 0)
            if return_code == 0:
                reset_reconnect_attempts()
                if not session_present:
                    _pending_resubscribe = True
                    print(
                        "[MQTT][CONNECT][OK] resumed "
                        f"return_code={return_code} session_present={session_present} "
                        "resubscribe_pending=true"
                    )
                else:
                    print(
                        "[MQTT][CONNECT][OK] resumed "
                        f"return_code={return_code} session_present={session_present}"
                    )
            else:
                print(f"[MQTT][CONNECT][FAIL] resumed return_code={return_code}")

        mtls_kw = dict(
            endpoint=self.endpoint,
            cert_filepath=cert_file,
            pri_key_filepath=key_file,
            client_bootstrap=client_bootstrap,
            client_id=self.client_id,
            keep_alive_secs=120,
            on_connection_interrupted=on_interrupted,
            on_connection_resumed=on_resumed,
        )

        _, _, ca_path = _certificate_paths(self.cert_path)
        if os.path.exists(ca_path):
            mtls_kw["ca_filepath"] = ca_path

        mqtt_conn = mqtt_connection_builder.mtls_from_path(**mtls_kw)

        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            try:
                print(
                    f"[MQTT][CONNECT] attempting connection try={attempt + 1}/{max_retries}"
                )
                if attempt > 0:
                    random_delay = random.uniform(1, 3)
                    print(f"[MQTT][CONNECT] jitter_sleep={random_delay:.1f}s")
                    time.sleep(random_delay)

                connect_future = mqtt_conn.connect()
                connect_future.result(timeout=10)
                print("[MQTT][CONNECT][OK] connected to broker")
                set_connection(mqtt_conn)
                mark_connected(True)
                reset_reconnect_attempts()
                return mqtt_conn

            except Exception as connection_error:
                print(
                    "[MQTT][CONNECT][FAIL] "
                    f"try={attempt + 1}/{max_retries} error={type(connection_error).__name__}"
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    print(f"[MQTT][CONNECT] retry_after={delay}s")
                    time.sleep(delay)
                else:
                    raise

        raise RuntimeError("MQTT 연결 실패: 최대 재시도 횟수를 초과했습니다.")

    def _check_certificate(self) -> tuple[bool, Optional[str], Optional[str]]:
        cert_file, key_file, _ = _certificate_paths(self.cert_path)
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


def resubscribe(topics: Sequence[str], callback: Callable) -> Dict[str, bool]:
    results: Dict[str, bool] = {}
    for topic in topics:
        try:
            subscribe(topic, callback)
            results[topic] = True
        except Exception:
            results[topic] = False
    return results


def log_resubscribe_results(results: Dict[str, bool]) -> None:
    for topic, success in results.items():
        if success:
            print(f"[MQTT][SUBSCRIBE][RETRY][OK] topic={topic}")
        else:
            print(f"[MQTT][SUBSCRIBE][RETRY][FAIL] topic={topic}")


def summarize_resubscribe_results(results: Dict[str, bool]) -> tuple[int, int]:
    success_count = sum(1 for success in results.values() if success)
    failed_count = len(results) - success_count
    return success_count, failed_count


def needs_resubscribe() -> bool:
    """SDK on_connection_resumed에서 session_present=False 시 설정된 플래그 확인."""
    return _pending_resubscribe


def clear_resubscribe_flag() -> None:
    global _pending_resubscribe
    _pending_resubscribe = False


def check_mqtt_connection(
    topics: Iterable[str],
    callback: Callable,
    client_factory: Optional[Callable[[], AWSIoTClient]] = None,
) -> bool:
    """Ensure MQTT connection is alive, reconnecting and resubscribing if necessary.

    재연결 실패 시 포기하지 않고 점진적 백오프로 계속 재시도한다.
    """
    # SDK 자동 재연결 후 session이 없으면 resubscribe 필요
    if needs_resubscribe() and is_connected():
        clear_resubscribe_flag()
        print("[MQTT][RECONNECT] resubscribe after session loss")
        resubscribe_results = resubscribe(list(topics), callback)
        log_resubscribe_results(resubscribe_results)
        return True

    if is_connected():
        reset_reconnect_attempts()
        return True

    increase_reconnect_attempt()

    # 점진적 백오프: threshold 초과 시 대기
    if reconnect_attempts > RECONNECT_BACKOFF_THRESHOLD:
        backoff_exp = reconnect_attempts - RECONNECT_BACKOFF_THRESHOLD
        delay = min(RECONNECT_BASE_DELAY * (2 ** (backoff_exp - 1)), RECONNECT_MAX_DELAY)
        print(
            f"[MQTT][RECONNECT] attempt={reconnect_attempts} "
            f"backoff_delay={delay}s"
        )
        time.sleep(delay)
    else:
        print(f"[MQTT][RECONNECT] attempt={reconnect_attempts}")

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
        print(f"[MQTT][RECONNECT][FAIL] error={type(exc).__name__}")
        return False

    reset_reconnect_attempts()
    resubscribe_results = resubscribe(list(topics), callback)
    log_resubscribe_results(resubscribe_results)
    success_count, failed_count = summarize_resubscribe_results(resubscribe_results)
    overall_status = "success" if failed_count == 0 else "partial_failed"
    print(
        "[MQTT][RECONNECT] result "
        f"success={success_count} failed={failed_count} status={overall_status}"
    )
    return failed_count == 0
