from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Set, Tuple

import requests

from . import publisher, runtime, settings


class StateChangeDetector:
    def __init__(self) -> None:
        self.last_states: Dict[str, str] = {}
        self.is_initialized = False
        self.change_threshold = 5
        self.excluded_sensors = {
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

            lower_entity_id = entity_id.lower()
            is_ondo_or_humidity_sensor = any(
                keyword in lower_entity_id for keyword in ("ondo", "seubdo", "seoudo")
            )
            if (
                entity_id in self.excluded_sensors
                and not is_ondo_or_humidity_sensor
                and entity_id not in settings.MQTT_REPORT_ENTITY_IDS
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
bootstrap_done = False
last_entity_publish: Dict[str, Tuple[float, str]] = {}


def _auth_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if settings.HASS_TOKEN:
        headers["Authorization"] = f"Bearer {settings.HASS_TOKEN}"
    return headers


def publish_bootstrap_all_states() -> None:
    global bootstrap_done

    if bootstrap_done:
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
            print(f"[MQTT][BOOTSTRAP] 로컬 API 실패 HTTP {response.status_code}")
            return

        data = response.json()
        payload = {
            "type": "bootstrap_all_states",
            "correlation_id": None,
            "ts": publisher.utc_timestamp(),
            "data": data,
        }
        if settings.MATTERHUB_ID:
            payload["hub_id"] = settings.MATTERHUB_ID

        publisher.publish(payload)
        bootstrap_done = True
        count = len(data) if isinstance(data, list) else 0
        print(f"[MQTT][BOOTSTRAP] 발행 완료: 전체 {count} entities")

    except Exception as exc:
        print(f"[MQTT][BOOTSTRAP] 실패: {exc}")


def _fetch_ha_states() -> Optional[List[Dict[str, object]]]:
    try:
        response = requests.get(
            f"{settings.HA_HOST}/api/states",
            headers=_auth_headers(),
            timeout=10,
        )
        if response.status_code != 200:
            return None
        states = response.json()
        return states if isinstance(states, list) else None
    except Exception as exc:
        print(f"[MQTT] HA 상태 조회 실패: {exc}")
        return None


def publish_device_state() -> None:
    global last_entity_publish

    if not runtime.is_connected():
        return

    states = _fetch_ha_states()
    if states is None:
        return

    try:
        state_map: Dict[str, Dict[str, object]] = {}
        for item in states:
            if isinstance(item, dict):
                entity_id = item.get("entity_id")
                if entity_id:
                    state_map[str(entity_id)] = item

        for entity_id in settings.MQTT_REPORT_ENTITY_IDS:
            state_entry = state_map.get(entity_id)
            if not state_entry:
                continue

            state_str = json.dumps(state_entry, sort_keys=True, ensure_ascii=False)
            last_info = last_entity_publish.get(entity_id)
            now = time.time()
            if last_info:
                last_ts, last_val = last_info
                if now - last_ts < settings.MQTT_EVENT_THROTTLE_SEC:
                    continue
                if (
                    settings.MQTT_EVENT_DEDUP_WINDOW_SEC > 0
                    and (now - last_ts) < settings.MQTT_EVENT_DEDUP_WINDOW_SEC
                    and last_val == state_str
                ):
                    continue

            last_entity_publish[entity_id] = (now, state_str)
            payload = {
                "type": "entity_changed",
                "correlation_id": None,
                "event_id": f"evt-{int(now * 1000)}-{entity_id.replace('.', '_')}",
                "ts": publisher.utc_timestamp(),
                "entity_id": entity_id,
                "state": state_entry,
            }
            if settings.MATTERHUB_ID:
                payload["hub_id"] = settings.MATTERHUB_ID

            publisher.publish(payload)
            print(f"[MQTT][PUBLISH] entity_changed: {entity_id} → {settings.MQTT_TOPIC_PUBLISH}")

    except Exception as exc:
        print(f"상태 발행(이벤트) 실패: {exc}")


def _load_managed_entity_ids() -> Optional[Set[str]]:
    path = settings.DEVICES_FILE_PATH
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read().strip() or "[]")
        return {d["entity_id"] for d in data if isinstance(d, dict) and "entity_id" in d}
    except Exception:
        return None


_last_device_state_publish: float = 0.0


def publish_device_states_bulk() -> None:
    global _last_device_state_publish

    if not runtime.is_connected() or not settings.MATTERHUB_ID:
        return

    now = time.time()
    if _last_device_state_publish > 0 and (now - _last_device_state_publish) < settings.MQTT_DEVICE_STATE_INTERVAL_SEC:
        return

    states = _fetch_ha_states()
    if states is None:
        return

    managed_ids = _load_managed_entity_ids()
    devices: Dict[str, Dict[str, object]] = {}
    for item in states:
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        if not entity_id:
            continue
        if managed_ids is not None and entity_id not in managed_ids:
            continue
        devices[entity_id] = {
            "state": item.get("state"),
            "last_changed": item.get("last_changed"),
            "attributes": item.get("attributes", {}),
        }

    if not devices:
        return

    topic = f"matterhub/{settings.MATTERHUB_ID}/state/devices"
    _publish_devices_with_chunking(topic, devices)
    _last_device_state_publish = time.time()
    print(f"[MQTT][DEVICE_STATE] 발행 완료: {len(devices)}개 디바이스 → {topic}")


def _extract_battery(attributes: dict) -> Optional[int]:
    for key in ("battery", "battery_level"):
        val = attributes.get(key)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


class DeviceAlertPublisher:
    def __init__(self) -> None:
        self._prev_states: Dict[str, str] = {}
        self._alerted: Dict[str, Set[str]] = {}
        self._last_check: float = 0.0
        self._initialized: bool = False

    def check_and_publish(self) -> None:
        if not runtime.is_connected() or not settings.MATTERHUB_ID:
            return

        now = time.time()
        if self._last_check > 0 and (now - self._last_check) < settings.MQTT_ALERT_CHECK_INTERVAL_SEC:
            return

        states = _fetch_ha_states()
        if states is None:
            return

        managed_ids = _load_managed_entity_ids()

        if not self._initialized:
            for item in states:
                if not isinstance(item, dict):
                    continue
                entity_id = item.get("entity_id")
                if not entity_id:
                    continue
                if managed_ids is not None and entity_id not in managed_ids:
                    continue
                current = str(item.get("state", ""))
                self._prev_states[entity_id] = current
                if current == "unavailable":
                    self._alerted.setdefault(entity_id, set()).add("UNAVAILABLE")
                attrs = item.get("attributes", {})
                battery = _extract_battery(attrs)
                if (
                    settings.MQTT_ALERT_BATTERY_THRESHOLD > 0
                    and battery is not None
                    and battery <= settings.MQTT_ALERT_BATTERY_THRESHOLD
                ):
                    self._alerted.setdefault(entity_id, set()).add("BATTERY_EMPTY")
            self._initialized = True
            self._last_check = time.time()
            print(f"[MQTT][ALERT] 초기화 완료: {len(self._prev_states)}개 엔티티, "
                  f"기존 알림 {sum(len(v) for v in self._alerted.values())}건 seed")
            return

        for item in states:
            if not isinstance(item, dict):
                continue
            entity_id = item.get("entity_id")
            if not entity_id:
                continue
            if managed_ids is not None and entity_id not in managed_ids:
                continue

            current = str(item.get("state", ""))
            prev = self._prev_states.get(entity_id)
            attrs = item.get("attributes", {})
            battery = _extract_battery(attrs)

            alerted_set = self._alerted.get(entity_id, set())

            # UNAVAILABLE 감지
            if current == "unavailable" and prev is not None and prev != "unavailable":
                if "UNAVAILABLE" not in alerted_set:
                    self._publish_alert(
                        entity_id=entity_id,
                        alert_type="UNAVAILABLE",
                        prev_state=prev,
                        current_state=current,
                        battery=battery,
                        attributes=attrs,
                    )
                    self._alerted.setdefault(entity_id, set()).add("UNAVAILABLE")
            elif current != "unavailable" and "UNAVAILABLE" in alerted_set:
                alerted_set.discard("UNAVAILABLE")

            # BATTERY_EMPTY 감지
            if settings.MQTT_ALERT_BATTERY_THRESHOLD > 0 and battery is not None:
                if battery <= settings.MQTT_ALERT_BATTERY_THRESHOLD:
                    if "BATTERY_EMPTY" not in alerted_set:
                        self._publish_alert(
                            entity_id=entity_id,
                            alert_type="BATTERY_EMPTY",
                            prev_state=prev or "",
                            current_state=current,
                            battery=battery,
                            attributes=attrs,
                        )
                        self._alerted.setdefault(entity_id, set()).add("BATTERY_EMPTY")
                else:
                    if "BATTERY_EMPTY" in alerted_set:
                        alerted_set.discard("BATTERY_EMPTY")

            self._prev_states[entity_id] = current

        self._last_check = time.time()

    def _publish_alert(
        self,
        entity_id: str,
        alert_type: str,
        prev_state: str,
        current_state: str,
        battery: Optional[int],
        attributes: dict,
    ) -> None:
        topic = f"matterhub/{settings.MATTERHUB_ID}/event/device_alerts"
        payload = {
            "hub_id": settings.MATTERHUB_ID,
            "ts": int(time.time()),
            "entity_id": entity_id,
            "alert_type": alert_type,
            "prev_state": prev_state,
            "current_state": current_state,
            "battery": battery,
            "attributes": {
                "friendly_name": attributes.get("friendly_name", ""),
                "device_class": attributes.get("device_class", ""),
            },
        }
        publisher.publish(payload, response_topic=topic)
        print(f"[MQTT][ALERT] {alert_type}: {entity_id} ({prev_state} → {current_state}) → {topic}")


_alert_publisher = DeviceAlertPublisher()


def check_and_publish_alerts() -> None:
    try:
        _alert_publisher.check_and_publish()
    except Exception as exc:
        print(f"[MQTT][ALERT] 실패: {exc}")


def _publish_devices_with_chunking(topic: str, devices: Dict[str, Dict[str, object]]) -> None:
    payload = {
        "hub_id": settings.MATTERHUB_ID,
        "ts": publisher.utc_timestamp(),
        "devices": devices,
    }
    serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    max_bytes = settings.MQTT_DEVICE_STATE_CHUNK_SIZE_KB * 1024

    if len(serialized) <= max_bytes:
        publisher.publish(payload, response_topic=topic)
        return

    # 청크 분할
    entity_ids = list(devices.keys())
    avg_size = len(serialized) / len(entity_ids)
    per_chunk = max(1, int((max_bytes - 500) / avg_size))

    chunks = [entity_ids[i:i + per_chunk] for i in range(0, len(entity_ids), per_chunk)]
    total = len(chunks)
    for idx, chunk_ids in enumerate(chunks, start=1):
        chunk_payload = {
            "hub_id": settings.MATTERHUB_ID,
            "ts": publisher.utc_timestamp(),
            "chunk": idx,
            "total_chunks": total,
            "devices": {eid: devices[eid] for eid in chunk_ids},
        }
        publisher.publish(chunk_payload, response_topic=topic)
