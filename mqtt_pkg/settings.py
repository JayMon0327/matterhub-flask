import os
from typing import Dict

from dotenv import load_dotenv

from providers import load_provider

load_dotenv(dotenv_path='.env')

_provider = load_provider()


def _strip_quotes(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip('"').strip("'")
    return normalized or None


def _env_with_fallback(*keys, default=None):
    """여러 환경변수 키를 순서대로 조회, 첫 번째 유효한 값 반환."""
    for key in keys:
        val = os.environ.get(key)
        if val:
            return _strip_quotes(val)
    return default


# Environment-derived settings
HA_HOST = os.environ.get("HA_host")
HASS_TOKEN = os.environ.get("hass_token")
LOCAL_API_BASE = os.environ.get("LOCAL_API_BASE", "http://localhost:8100")

# === MQTT 토픽 (벤더 중립) ===
MQTT_TOPIC_SUBSCRIBE = (
    _env_with_fallback("MQTT_TOPIC_SUBSCRIBE")
    or _provider.get_topic_subscribe()
)
MQTT_TOPIC_PUBLISH = (
    _env_with_fallback("MQTT_TOPIC_PUBLISH")
    or _provider.get_topic_publish()
)

MQTT_TEST_TOPIC = _env_with_fallback("MQTT_TEST_TOPIC") or ""
MQTT_TEST_TOPIC_SUBSCRIBE = _env_with_fallback("MQTT_TEST_TOPIC_SUBSCRIBE") or MQTT_TEST_TOPIC or ""
MQTT_TEST_TOPIC_PUBLISH = _env_with_fallback("MQTT_TEST_TOPIC_PUBLISH") or MQTT_TEST_TOPIC or ""

# === 센서 보고 ===
_report_ids_raw = _env_with_fallback("MQTT_REPORT_ENTITY_IDS")
if not _report_ids_raw:
    _report_ids_raw = ",".join(_provider.get_default_report_entity_ids())
_report_ids_list = [
    entity_id.strip()
    for entity_id in _report_ids_raw.split(",")
    if entity_id.strip()
]
MQTT_REPORT_ENTITY_IDS = list(dict.fromkeys(_report_ids_list))

# === 이벤트 조절 ===
MQTT_EVENT_THROTTLE_SEC = max(0.0, float(
    _env_with_fallback("MQTT_EVENT_THROTTLE_SEC") or "2"
))
MQTT_EVENT_DEDUP_WINDOW_SEC = max(0.0, float(
    _env_with_fallback("MQTT_EVENT_DEDUP_WINDOW_SEC") or "3"
))

# === 발행 QoS 제어 ===
MQTT_PUBLISH_TIMEOUT_SEC = max(1, int(
    _env_with_fallback("MQTT_PUBLISH_TIMEOUT_SEC") or "3"
))

# === 디바이스 상태 발행 ===
MQTT_DEVICE_STATE_INTERVAL_SEC = max(10, int(
    _env_with_fallback("MQTT_DEVICE_STATE_INTERVAL_SEC") or "60"
))
MQTT_DEVICE_STATE_CHUNK_SIZE_KB = max(10, int(
    _env_with_fallback("MQTT_DEVICE_STATE_CHUNK_SIZE_KB") or "100"
))

# === 디바이스 알림 발행 ===
MQTT_ALERT_CHECK_INTERVAL_SEC = max(5, int(
    _env_with_fallback("MQTT_ALERT_CHECK_INTERVAL_SEC") or "30"
))
MQTT_ALERT_BATTERY_THRESHOLD = max(0, int(
    _env_with_fallback("MQTT_ALERT_BATTERY_THRESHOLD") or "0"
))

DEVICES_FILE_PATH = os.environ.get("devices_file_path")

SUBSCRIBE_MATTERHUB_TOPICS = os.environ.get("SUBSCRIBE_MATTERHUB_TOPICS", "0") == "1"

MATTERHUB_ID = _strip_quotes(os.environ.get("matterhub_id"))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def update_matterhub_id(new_id: str) -> None:
    """Update the in-memory and persisted matterhub_id value."""
    global MATTERHUB_ID
    normalized = _strip_quotes(new_id)
    if not normalized:
        raise ValueError("matterhub_id cannot be empty.")

    MATTERHUB_ID = normalized
    os.environ["matterhub_id"] = f'"{normalized}"'
    _persist_env_value("matterhub_id", f'"{normalized}"')


def _persist_env_value(key: str, value: str) -> None:
    env_data: Dict[str, str] = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as env_file:
            for line in env_file:
                if "=" not in line:
                    continue
                k, v = line.rstrip("\n").split("=", 1)
                env_data[k] = v

    env_data[key] = value

    with open(ENV_PATH, "w", encoding="utf-8") as env_file:
        for env_key, env_value in env_data.items():
            env_file.write(f"{env_key}={env_value}\n")
