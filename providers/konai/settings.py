"""Konai 벤더 설정값.

벤더 분리 구조를 유지하되, 기본값은 MatterHub 자체 인프라를 사용한다.
Konai 외주 연동이 필요할 때 아래 주석 해제 후 사용.
환경변수가 설정되면 환경변수 값이 우선한다 (mqtt_pkg/settings.py, runtime.py에서 처리).
"""

import os

from providers.base import MQTTProviderSettings

# === MatterHub 기본 인프라 ===
ENDPOINT = "a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com"
CERT_DIR = "certificates/"

# === Konai 외주 인프라 (현재 미사용, 외주 연동 시 위 값 교체) ===
# ENDPOINT = "a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com"
# CERT_DIR = "konai_certificates/"
# TOPIC_DELTA = "update/delta/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL"
# TOPIC_REPORTED = "update/reported/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL"

# 코나이 토픽 (비활성화)
TOPIC_DELTA = ""
TOPIC_REPORTED = ""


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
        # matterhub_id를 client_id로 사용 (Thing Name 기반)
        hub_id = os.environ.get("matterhub_id", "").strip().strip('"')
        if hub_id:
            return hub_id
        return f"matterhub-{os.getpid()}"

    def get_cert_dir(self) -> str:
        return CERT_DIR

    def get_topic_subscribe(self) -> str:
        return TOPIC_DELTA

    def get_topic_publish(self) -> str:
        return TOPIC_REPORTED

    def get_default_report_entity_ids(self) -> list[str]:
        return build_default_report_entity_ids()
