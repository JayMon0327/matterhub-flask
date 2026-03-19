import os
from typing import Dict

from dotenv import load_dotenv

from providers.konai import settings as konai_defaults

load_dotenv(dotenv_path='.env')


def _strip_quotes(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip('"').strip("'")
    return normalized or None


# Environment-derived settings
HA_HOST = os.environ.get("HA_host")
HASS_TOKEN = os.environ.get("hass_token")
LOCAL_API_BASE = os.environ.get("LOCAL_API_BASE", "http://localhost:8100")

# 레거시: 단일 토픽 설정 시 구독/발행 모두 이 값 사용
KONAI_TOPIC = _strip_quotes(os.environ.get("KONAI_TOPIC"))

_req_raw = os.environ.get("KONAI_TOPIC_REQUEST") or os.environ.get("KONAI_TOPIC")
KONAI_TOPIC_REQUEST = _strip_quotes(_req_raw) or konai_defaults.TOPIC_DELTA

_res_raw = os.environ.get("KONAI_TOPIC_RESPONSE") or os.environ.get("KONAI_TOPIC")
KONAI_TOPIC_RESPONSE = _strip_quotes(_res_raw) or konai_defaults.TOPIC_REPORTED

KONAI_TEST_TOPIC = _strip_quotes(os.environ.get("KONAI_TEST_TOPIC"))
KONAI_TEST_TOPIC_REQUEST = _strip_quotes(
    os.environ.get("KONAI_TEST_TOPIC_REQUEST", KONAI_TEST_TOPIC or "")
)
KONAI_TEST_TOPIC_RESPONSE = _strip_quotes(
    os.environ.get("KONAI_TEST_TOPIC_RESPONSE", KONAI_TEST_TOPIC or "")
)

KONAI_REPORT_ENTITY_IDS_RAW = os.environ.get(
    "KONAI_REPORT_ENTITY_IDS",
    ",".join(konai_defaults.build_default_report_entity_ids()),
)
_konai_report_entity_ids = [
    entity_id.strip()
    for entity_id in KONAI_REPORT_ENTITY_IDS_RAW.split(",")
    if entity_id.strip()
]
KONAI_REPORT_ENTITY_IDS = list(dict.fromkeys(_konai_report_entity_ids))

KONAI_EVENT_THROTTLE_SEC = max(0.0, float(os.environ.get("KONAI_EVENT_THROTTLE_SEC", "2")))
KONAI_EVENT_DEDUP_WINDOW_SEC = max(0.0, float(os.environ.get("KONAI_EVENT_DEDUP_WINDOW_SEC", "3")))

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
