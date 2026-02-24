from __future__ import annotations

import json
import time
from typing import Dict, List, Tuple

import requests

from . import publisher, runtime, settings


class StateChangeDetector:
    def __init__(self) -> None:
        self.last_states: Dict[str, str] = {}
        self.is_initialized = False
        self.change_threshold = 5
        self.excluded_sensors = {
            "sensor.smart_ht_sensor_ondo_1",
            "sensor.smart_ht_sensor_ondo_2",
            "sensor.smart_ht_sensor_ondo_3",
            "sensor.smart_ht_sensor_seubdo_1",
            "sensor.smart_ht_sensor_seubdo_2",
            "sensor.smart_ht_sensor_seubdo_3",
            "sensor.smart_presence_sensor_jodo",
            "sensor.smart_presence_sensor_jodo_1",
            "sensor.smart_presence_sensor_jodo_2",
            "sensor.smart_presence_sensor_jodo_3",
        }

    def detect_changes(self, current_states: List[Dict[str, object]]) -> Tuple[bool, List[Dict[str, object]]]:
        changes: List[Dict[str, object]] = []

        if not self.is_initialized:
            for state in current_states:
                entity_id = state.get("entity_id")
                current_state = state.get("state")
                if entity_id:
                    self.last_states[str(entity_id)] = str(current_state)
            self.is_initialized = True
            print(f"디바이스 상태 초기화 완료: {len(self.last_states)}개")
            return False, []

        for state in current_states:
            entity_id = state.get("entity_id")
            current_state = state.get("state")
            if not entity_id:
                continue

            entity_id = str(entity_id)
            current_state = "" if current_state is None else str(current_state)

            if (
                entity_id in self.excluded_sensors
                and entity_id not in settings.KONAI_REPORT_ENTITY_IDS
            ):
                continue

            previous_state = self.last_states.get(entity_id)
            if previous_state is None:
                changes.append(
                    {"type": "new_device", "entity_id": entity_id, "state": current_state}
                )
                self.last_states[entity_id] = current_state
            elif previous_state != current_state:
                changes.append(
                    {
                        "type": "state_change",
                        "entity_id": entity_id,
                        "previous": previous_state,
                        "current": current_state,
                    }
                )
                self.last_states[entity_id] = current_state

        return bool(changes), changes


state_detector = StateChangeDetector()
konai_bootstrap_done = False
konai_last_entity_publish: Dict[str, Tuple[float, str]] = {}


def _auth_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if settings.HASS_TOKEN:
        headers["Authorization"] = f"Bearer {settings.HASS_TOKEN}"
    return headers


def publish_bootstrap_all_states() -> None:
    global konai_bootstrap_done

    if konai_bootstrap_done:
        return

    if not runtime.is_connected():
        return

    try:
        response = requests.get(
            f"{settings.LOCAL_API_BASE}/local/api/states",
            headers=_auth_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            print(f"❌ 코나이 bootstrap: 로컬 API 실패 HTTP {response.status_code}")
            return

        data = response.json()
        payload = {
            "type": "bootstrap_all_states",
            "correlation_id": None,
            "ts": publisher.konai_timestamp(),
            "data": data,
        }
        if settings.MATTERHUB_ID:
            payload["hub_id"] = settings.MATTERHUB_ID

        publisher.publish(payload)
        konai_bootstrap_done = True
        count = len(data) if isinstance(data, list) else 0
        print(f"✅ 코나이 bootstrap 발행: 전체 {count} entities")

    except Exception as exc:
        print(f"❌ 코나이 bootstrap 실패: {exc}")


def publish_device_state() -> None:
    global konai_last_entity_publish

    if not runtime.is_connected():
        return

    try:
        response = requests.get(
            f"{settings.HA_HOST}/api/states",
            headers=_auth_headers(),
            timeout=10,
        )
        if response.status_code != 200:
            return

        states = response.json()
        state_map: Dict[str, Dict[str, object]] = {}
        if isinstance(states, list):
            for item in states:
                if isinstance(item, dict):
                    entity_id = item.get("entity_id")
                    if entity_id:
                        state_map[str(entity_id)] = item

        for entity_id in settings.KONAI_REPORT_ENTITY_IDS:
            state_entry = state_map.get(entity_id)
            if not state_entry:
                continue

            state_str = json.dumps(state_entry, sort_keys=True, ensure_ascii=False)
            last_info = konai_last_entity_publish.get(entity_id)
            now = time.time()
            if last_info:
                last_ts, last_val = last_info
                if now - last_ts < settings.KONAI_EVENT_THROTTLE_SEC:
                    continue
                if (
                    settings.KONAI_EVENT_DEDUP_WINDOW_SEC > 0
                    and (now - last_ts) < settings.KONAI_EVENT_DEDUP_WINDOW_SEC
                    and last_val == state_str
                ):
                    continue

            konai_last_entity_publish[entity_id] = (now, state_str)
            payload = {
                "type": "entity_changed",
                "correlation_id": None,
                "event_id": f"evt-{int(now * 1000)}-{entity_id.replace('.', '_')}",
                "ts": publisher.konai_timestamp(),
                "entity_id": entity_id,
                "state": state_entry,
            }
            if settings.MATTERHUB_ID:
                payload["hub_id"] = settings.MATTERHUB_ID

            publisher.publish(payload)
            print(f"코나이 entity_changed: {entity_id} → {settings.KONAI_TOPIC_RESPONSE}")

    except Exception as exc:
        print(f"상태 발행(이벤트) 실패: {exc}")

