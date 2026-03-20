"""Konai 벤더 전용 기본 설정값.

이 모듈은 konai 환경에서 사용하는 하드코딩 기본값을 한곳에 모은다.
환경변수가 설정되면 환경변수 값이 우선한다 (mqtt_pkg/settings.py, runtime.py에서 처리).
"""

from providers.base import MQTTProviderSettings

# AWS IoT Core 엔드포인트
ENDPOINT = "a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com"

# MQTT 클라이언트 ID (Thing Name 기반)
CLIENT_ID = "c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL"

# 인증서 디렉토리
CERT_DIR = "konai_certificates/"

# 코나이 프로토콜 토픽 기본값 (delta=구독, reported=발행)
TOPIC_DELTA = (
    "update/delta/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL"
)
TOPIC_REPORTED = (
    "update/reported/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL"
)


def build_default_report_entity_ids() -> list[str]:
    """Konai 센서 엔티티 ID 기본 목록 생성."""
    defaults = [
        "sensor.smart_ht_sensor_ondo",
        "sensor.smart_ht_sensor_seubdo",
    ]
    defaults.extend([f"sensor.smart_ht_sensor_ondo_{i}" for i in range(1, 21)])
    defaults.extend([f"sensor.smart_ht_sensor_seubdo_{i}" for i in range(1, 21)])
    return defaults


class KonaiSettings(MQTTProviderSettings):
    """Konai 벤더 설정 인터페이스 구현."""

    def get_endpoint(self) -> str:
        return ENDPOINT

    def get_client_id(self) -> str:
        return CLIENT_ID

    def get_cert_dir(self) -> str:
        return CERT_DIR

    def get_topic_subscribe(self) -> str:
        return TOPIC_DELTA

    def get_topic_publish(self) -> str:
        return TOPIC_REPORTED

    def get_default_report_entity_ids(self) -> list[str]:
        return build_default_report_entity_ids()
