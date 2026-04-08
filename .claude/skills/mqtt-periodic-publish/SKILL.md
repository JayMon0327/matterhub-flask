---
name: mqtt-periodic-publish
description: 등록 센서의 현재 상태를 30초마다 무조건 MQTT 발행. 변화 여부와 무관하게 주기적으로 periodic_state 메시지를 보내 센서 생존 여부를 외부에서 확인 가능하게 한다. POLL/PERIODIC 로그도 추가. "/mqtt-periodic-publish" 또는 "주기적 발행", "periodic state" 시 사용.
---

# 주기적 상태 발행 (periodic_state)

`mqtt_pkg/state.py`의 `publish_device_state()`를 개편하여 30초마다 등록 센서의 현재 상태를 MQTT로 무조건 발행하는 스킬.
변화 감지는 `mqtt-ha-websocket` 스킬(WebSocket 기반)이 담당하며, 이 스킬은 **상태 변화 여부와 무관한 주기적 heartbeat 발행**을 담당한다.

## 배경

변화 기반 발행만으로는 부족한 이유:
- 센서가 리포트를 안 보내면 외부(클라우드 서버)는 센서가 살아있는지 알 수 없음
- "마지막 entity_changed가 1시간 전" → 센서 고장? 아니면 변화 없음? 구분 불가
- 주기적 발행이 있으면 30초 단위로 센서 생존 신호 확인 가능

이 스킬의 역할:
- 5초마다 HA REST API 폴링 → 호출 결과 로그 (`[MQTT][POLL]`)
- 6회 폴링 = 30초마다 등록 센서 전체를 `periodic_state` 타입으로 발행
- 발행 로그 (`[MQTT][PERIODIC]`)

## 사전 조건

| 항목 | 확인 방법 |
|------|-----------|
| `MQTT_REPORT_ENTITY_IDS` 환경변수 | `.env`에 등록된 센서 entity_id 목록 |
| `HA_host`, `hass_token` | HA REST API 호출에 사용 |
| `publisher.publish()` 동작 | MQTT publisher가 정상 동작 중 |

## 구현 절차

### Step 1: 전역 상수/카운터 추가

`mqtt_pkg/state.py` 상단의 전역 변수 영역에 다음을 추가한다:

```python
_periodic_counter: int = 0
_PERIODIC_INTERVAL: int = 6  # 5초 × 6 = 30초마다 주기적 발행
```

### Step 2: `publish_device_state()` 함수 교체

기존 함수 본문을 다음 코드로 교체한다:

```python
def publish_device_state() -> None:
    """5초마다 호출. 30초(6회)마다 등록 센서를 periodic_state로 발행."""
    global _periodic_counter

    if not runtime.is_connected():
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
        matched_count = sum(
            1 for eid in settings.MQTT_REPORT_ENTITY_IDS if eid in state_map
        )
        print(f"[MQTT][POLL] status=200 entities={matched_count}")

        # 주기적 발행 카운터
        _periodic_counter += 1
        is_periodic = _periodic_counter >= _PERIODIC_INTERVAL

        if not is_periodic:
            return

        now = time.time()
        periodic_published = 0

        for entity_id in settings.MQTT_REPORT_ENTITY_IDS:
            state_entry = state_map.get(entity_id)
            if not state_entry:
                continue

            payload = {
                "type": "periodic_state",
                "correlation_id": None,
                "event_id": f"periodic-{int(now * 1000)}-{entity_id.replace('.', '_')}",
                "ts": publisher.utc_timestamp(),
                "entity_id": entity_id,
                "state": state_entry,
            }
            if settings.MATTERHUB_ID:
                payload["hub_id"] = settings.MATTERHUB_ID

            publisher.publish(payload)
            periodic_published += 1

        _periodic_counter = 0
        print(f"[MQTT][PERIODIC] {periodic_published} entities 발행 완료")

    except Exception as exc:
        print(f"상태 발행 실패: {exc}")
```

### Step 3: `_auth_headers()` / `_fetch_ha_states()` 의존성 확인

위 코드는 `_auth_headers()`를 사용한다. 기존 master에 이미 정의되어 있으면 그대로 사용하고, 없으면 다음을 추가한다:

```python
def _auth_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if settings.HASS_TOKEN:
        headers["Authorization"] = f"Bearer {settings.HASS_TOKEN}"
    return headers
```

### Step 4: main loop는 변경 없음

`mqtt.py`의 main loop는 이미 5초마다 `state.publish_device_state()`를 호출하므로 변경 불필요.

## 로그 형식

| 로그 | 의미 | 빈도 |
|------|------|------|
| `[MQTT][POLL] status=200 entities=N` | HA API 호출 성공, 매칭된 등록 센서 N개 | 5초마다 |
| `[MQTT][POLL] status=502 entities=0` | HA API 호출 실패 (HA 미기동 등) | 실패 시 |
| `[MQTT][PERIODIC] N entities 발행 완료` | 30초마다 등록 센서 N개를 periodic_state로 발행 | 30초마다 |

## 발행 메시지 형식

```json
{
  "type": "periodic_state",
  "correlation_id": null,
  "event_id": "periodic-1712558400000-sensor_temperature_room1",
  "ts": "2026-04-08T10:00:00.000Z",
  "entity_id": "sensor.temperature_room1",
  "state": {
    "entity_id": "sensor.temperature_room1",
    "state": "23.5",
    "attributes": {...},
    "last_changed": "...",
    "last_updated": "..."
  },
  "hub_id": "matterhub-XXX"
}
```

## 검증

```bash
# 1. 5초마다 POLL 로그 확인
journalctl -u matterhub-mqtt.service -f | grep POLL
# → [MQTT][POLL] status=200 entities=N (5초 간격)

# 2. 30초마다 PERIODIC 로그 확인
journalctl -u matterhub-mqtt.service -f | grep PERIODIC
# → [MQTT][PERIODIC] N entities 발행 완료 (30초 간격)

# 3. MQTT 발행 확인
journalctl -u matterhub-mqtt.service --since "1 minute ago" | grep "type=periodic_state"
# → publish_result status=success type=periodic_state
```

## 주기 변경 방법

| 폴링 주기 | mqtt.py main loop의 `time.sleep(5)` 값 |
|-----------|---------------------------------------|
| 주기적 발행 주기 | `_PERIODIC_INTERVAL` 상수 (기본 6 = 30초) |

예: 60초마다 발행하려면 `_PERIODIC_INTERVAL = 12`

## 함께 사용할 스킬

- `mqtt-ha-websocket`: 변화 감지 실시간 발행 (이 스킬과 보완 관계)
- 두 스킬을 함께 적용하면:
  - 변화 발생 → WebSocket이 entity_changed 즉시 발행
  - 변화 없어도 → 30초마다 periodic_state 발행 (생존 신호)
