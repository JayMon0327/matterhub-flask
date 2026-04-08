from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

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
konai_last_entity_state: Dict[str, str] = {}  # state 값만 저장 (변화 감지용)
_last_disconnected_log: float = 0.0
_periodic_counter: int = 0
_PERIODIC_INTERVAL: int = 6  # 5초 × 6 = 30초마다 주기적 발행


def _log_disconnected_once(caller: str) -> None:
    """연결 끊김 시 30초에 1번만 로그 출력 (로그 폭주 방지)."""
    global _last_disconnected_log
    now = time.time()
    if now - _last_disconnected_log >= 30:
        print(f"[MQTT][{caller}][SKIP] reason=disconnected")
        _last_disconnected_log = now


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
        _log_disconnected_once("BOOTSTRAP")
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
    global konai_last_entity_state, _periodic_counter

    if not runtime.is_connected():
        _log_disconnected_once("ENTITY_CHANGED")
        return

    try:
        response = requests.get(
            f"{settings.HA_HOST}/api/states",
            headers=_auth_headers(),
            timeout=10,
        )
        if response.status_code != 200:
            print(f"[MQTT][POLL] status={response.status_code} entities=0")
            return

        states = response.json()
        state_map: Dict[str, Dict[str, object]] = {}
        if isinstance(states, list):
            for item in states:
                if isinstance(item, dict):
                    entity_id = item.get("entity_id")
                    if entity_id:
                        state_map[str(entity_id)] = item

        # 매칭된 entity 수 계산 + 로그
        matched_count = sum(1 for eid in settings.KONAI_REPORT_ENTITY_IDS if eid in state_map)
        print(f"[MQTT][POLL] status=200 entities={matched_count}")

        # 주기적 발행 카운터
        _periodic_counter += 1
        is_periodic = _periodic_counter >= _PERIODIC_INTERVAL

        now = time.time()
        periodic_published = 0

        for entity_id in settings.KONAI_REPORT_ENTITY_IDS:
            state_entry = state_map.get(entity_id)
            if not state_entry:
                continue

            # 주기적 발행 (30초마다 무조건)
            # 변화 감지는 WebSocket(start_ha_websocket_listener)이 담당
            if is_periodic:
                payload = {
                    "type": "periodic_state",
                    "correlation_id": None,
                    "event_id": f"periodic-{int(now * 1000)}-{entity_id.replace('.', '_')}",
                    "ts": publisher.konai_timestamp(),
                    "entity_id": entity_id,
                    "state": state_entry,
                }
                if settings.MATTERHUB_ID:
                    payload["hub_id"] = settings.MATTERHUB_ID
                publisher.publish(payload)
                periodic_published += 1

        if is_periodic:
            _periodic_counter = 0
            print(f"[MQTT][PERIODIC] {periodic_published} entities 발행 완료")

    except Exception as exc:
        print(f"상태 발행 실패: {exc}")


# ==============================================================================
# HA WebSocket 기반 실시간 state_changed 감지
# ==============================================================================

_ws_thread: Optional[threading.Thread] = None
_ws_running: bool = False


def _publish_entity_changed_from_event(
    entity_id: str,
    new_state: Dict[str, Any],
    old_state: Optional[Dict[str, Any]],
) -> None:
    """WebSocket state_changed 이벤트에서 entity_changed 발행."""
    if not runtime.is_connected():
        return

    old_val = old_state.get("state", "") if old_state else "(init)"
    new_val = new_state.get("state", "")

    now = time.time()
    payload = {
        "type": "entity_changed",
        "correlation_id": None,
        "event_id": f"ws-{int(now * 1000)}-{entity_id.replace('.', '_')}",
        "ts": publisher.konai_timestamp(),
        "entity_id": entity_id,
        "state": new_state,
    }
    if settings.MATTERHUB_ID:
        payload["hub_id"] = settings.MATTERHUB_ID

    publisher.publish(payload)
    print(f"[MQTT][WS][ENTITY_CHANGED] {entity_id} {old_val}→{new_val}")


async def _ha_websocket_loop() -> None:
    """HA WebSocket에 연결하여 state_changed 이벤트를 실시간 수신."""
    try:
        import websockets
    except ImportError:
        print("[MQTT][WS] websockets 패키지 미설치, WebSocket 감지 비활성화")
        return

    ha_host = (settings.HA_HOST or "http://127.0.0.1:8123").replace("http://", "").replace("https://", "")
    ws_url = f"ws://{ha_host}/api/websocket"
    token = settings.HASS_TOKEN
    report_ids = set(settings.KONAI_REPORT_ENTITY_IDS)

    while _ws_running:
        try:
            async with websockets.connect(ws_url) as ws:
                # 1. auth
                msg = json.loads(await ws.recv())
                if msg.get("type") != "auth_required":
                    print(f"[MQTT][WS] 예상치 못한 메시지: {msg.get('type')}")
                    continue

                await ws.send(json.dumps({"type": "auth", "access_token": token}))
                msg = json.loads(await ws.recv())
                if msg.get("type") != "auth_ok":
                    print(f"[MQTT][WS] 인증 실패: {msg}")
                    await asyncio.sleep(10)
                    continue

                # 2. subscribe state_changed
                await ws.send(json.dumps({
                    "id": 1,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }))
                msg = json.loads(await ws.recv())
                if not msg.get("success"):
                    print(f"[MQTT][WS] 구독 실패: {msg}")
                    await asyncio.sleep(10)
                    continue

                print(f"[MQTT][WS] connected entity_filter={len(report_ids)}")

                # 3. 이벤트 수신 루프
                async for raw in ws:
                    if not _ws_running:
                        break
                    msg = json.loads(raw)
                    if msg.get("type") != "event":
                        continue
                    data = msg.get("event", {}).get("data", {})
                    entity_id = data.get("entity_id", "")
                    if entity_id not in report_ids:
                        continue
                    new_state = data.get("new_state")
                    old_state = data.get("old_state")
                    if new_state is None:
                        continue
                    _publish_entity_changed_from_event(entity_id, new_state, old_state)

        except Exception as exc:
            if _ws_running:
                print(f"[MQTT][WS] 연결 끊김: {type(exc).__name__}, 5초 후 재연결")
                await asyncio.sleep(5)


def _ws_thread_target() -> None:
    """WebSocket 리스너를 별도 스레드에서 asyncio로 실행."""
    asyncio.run(_ha_websocket_loop())


def start_ha_websocket_listener() -> None:
    """HA WebSocket state_changed 리스너를 백그라운드 스레드로 시작."""
    global _ws_thread, _ws_running

    if not settings.HASS_TOKEN:
        print("[MQTT][WS] hass_token 미설정, WebSocket 감지 비활성화")
        return

    _ws_running = True
    _ws_thread = threading.Thread(target=_ws_thread_target, daemon=True, name="ha-ws-listener")
    _ws_thread.start()
    print("[MQTT][WS] 리스너 스레드 시작됨")
