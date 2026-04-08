---
name: mqtt-ha-websocket
description: HA WebSocket state_changed 이벤트 구독으로 entity_changed 실시간 발행. 폴링 기반 변화 감지의 한계(반응 지연, 임계치 미달 누락)를 해소하고 push 방식으로 전환한다. "/mqtt-ha-websocket" 또는 "웹소켓 연동", "실시간 감지" 시 사용.
---

# HA WebSocket 실시간 entity_changed 발행

`mqtt_pkg/state.py`의 폴링 기반 변화 감지를 HA WebSocket 이벤트 기반으로 대체하는 스킬.
별도 daemon 스레드에서 `ws://HA_HOST/api/websocket`에 연결하여 `state_changed` 이벤트를 push 방식으로 수신, 즉시 MQTT로 발행한다.

## 배경

기존 폴링 방식 한계:
- HA REST API를 5초마다 호출 → 반응 지연 (최대 5초)
- 폴링 호출의 90% 이상이 변화 없는 무의미한 호출
- 폴링 사이의 짧은 변화는 dedup window에 묻혀 누락 가능

WebSocket 방식 장점:
- HA가 `state_changed` 이벤트를 push로 전달 → 0초 지연
- 필요한 entity만 필터링 → 트래픽 절감
- HA/Matter Server 자동 재연결 시에도 자동 재구독

## 사전 조건

| 항목 | 확인 방법 |
|------|-----------|
| `websockets` Python 패키지 설치 | `pip show websockets` (16.0 이상) |
| `.env`의 `HA_host`, `hass_token` | `grep -E '^HA_host\|^hass_token' .env` |
| `MQTT_REPORT_ENTITY_IDS` 환경변수 | `mqtt_pkg/settings.py`에서 로드되는 entity 목록 |

## 구현 절차

### Step 1: `mqtt_pkg/state.py` 상단 import 추가

```python
import asyncio
import threading
from typing import Any, Optional
```

### Step 2: WebSocket 리스너 함수 추가

`mqtt_pkg/state.py` 파일 끝에 다음 코드 블록을 추가한다:

```python
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
        "ts": publisher.utc_timestamp(),
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
    report_ids = set(settings.MQTT_REPORT_ENTITY_IDS)

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
```

### Step 3: 기존 폴링의 변화 감지 로직 제거

`publish_device_state()`에서 entity_changed 발행 부분을 삭제하거나 주석 처리한다.
폴링은 `mqtt-periodic-publish` 스킬의 periodic_state 발행 전용으로 남긴다.
WebSocket이 변화 감지를 전담하므로 폴링과 중복 발행되지 않도록 한다.

### Step 4: `mqtt.py` main()에서 리스너 시작

```python
state.publish_bootstrap_all_states()
state.start_ha_websocket_listener()  # ← 추가
```

`publish_bootstrap_all_states()` 호출 직후, main loop 진입 전에 호출한다.

## 로그 형식

| 로그 | 의미 | 빈도 |
|------|------|------|
| `[MQTT][WS] 리스너 스레드 시작됨` | 백그라운드 스레드 시작 | 부팅 시 1회 |
| `[MQTT][WS] connected entity_filter=N` | HA 연결+구독 성공, N개 entity 필터링 | 연결마다 |
| `[MQTT][WS] 인증 실패: ...` | hass_token 무효 | 인증 실패 시 |
| `[MQTT][WS] 연결 끊김: <Exception>, 5초 후 재연결` | 끊김 감지, 자동 재연결 | 끊길 때 |
| `[MQTT][WS][ENTITY_CHANGED] entity_id old→new` | 실시간 발행 | 변화마다 |

## 검증

```bash
# 1. 부팅 후 로그 확인
journalctl -u matterhub-mqtt.service -b | grep "WS"
# → 리스너 스레드 시작됨, connected entity_filter=N

# 2. 센서를 물리적으로 변화시킨 후 즉시 로그 확인
journalctl -u matterhub-mqtt.service -f | grep "WS.*ENTITY_CHANGED"
# → 변화 즉시 entity_changed 로그 출력

# 3. HA 재시작 후 자동 재연결 확인
docker restart homeassistant_core
sleep 10
journalctl -u matterhub-mqtt.service --since "30 seconds ago" | grep "WS"
# → 연결 끊김 → 5초 후 재연결 → connected entity_filter=N
```

## 알려진 한계

1. **센서 펌웨어 의존성**: HA에 값이 안 올라오면 WebSocket 이벤트도 없음. Matter 센서의 reportable change 임계치 / ICD 절전 주기는 펌웨어 레벨이라 코드로 제어 불가.
2. **MQTT 미연결 시**: `runtime.is_connected()` False면 발행 skip. WebSocket 이벤트는 유실되지 않지만 MQTT 발행은 되지 않음.

## 함께 사용할 스킬

- `mqtt-periodic-publish`: 주기적 상태 발행 (WebSocket과 보완 관계)
