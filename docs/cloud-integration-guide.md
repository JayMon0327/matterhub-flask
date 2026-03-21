# MatterHub 클라우드 연동 가이드 — 디바이스 상태 발행

> 최종 수정: 2026-03-21
> 대상: 클라우드 백엔드 개발자

---

## 1. 개요

MatterHub 허브는 Home Assistant에서 수집한 디바이스 상태를 **이벤트 기반 커스텀 토픽**으로 AWS IoT Core에 발행한다.
기존 AWS IoT Shadow(`$aws/things/{id}/shadow/update`)는 8KB 제한으로 전체 디바이스 전송이 불가하여 **제거**되었다.

### 발행 채널 요약

| 채널 | 토픽 패턴 | 주기 | 용도 |
|------|-----------|------|------|
| 디바이스 전체 상태 | `matterhub/{hub_id}/state/devices` | 시작 시 1회 + 60초 주기 | 전체 디바이스 상태 동기화 |
| 디바이스 알림 | `matterhub/{hub_id}/event/device_alerts` | 실시간 (상태 변경 시) | unavailable 전환, 배터리 부족 알림 |

---

## 2. 디바이스 전체 상태 (`state/devices`)

### 토픽

```
matterhub/{hub_id}/state/devices
```

- `{hub_id}` 예시: `whatsmatter-nipa_SN-1774090901`

### 발행 주기

- MQTT 연결 직후 **즉시 1회** 발행
- 이후 **60초 간격** 주기 발행 (환경변수 `MQTT_DEVICE_STATE_INTERVAL_SEC`로 조정 가능)

### QoS

- **QoS 1** (At Least Once)

### 페이로드 (단일 메시지)

디바이스 수가 적어 전체 크기가 100KB 이하인 경우:

```json
{
  "hub_id": "whatsmatter-nipa_SN-1774090901",
  "ts": "2026-03-21T11:01:00.000Z",
  "devices": {
    "light.living_room": {
      "state": "on",
      "last_changed": "2026-03-21T10:55:00.000Z",
      "attributes": {
        "friendly_name": "거실 조명",
        "brightness": 255,
        "color_temp": 370
      }
    },
    "sensor.temperature": {
      "state": "23.5",
      "last_changed": "2026-03-21T10:59:00.000Z",
      "attributes": {
        "friendly_name": "온도 센서",
        "unit_of_measurement": "°C",
        "device_class": "temperature"
      }
    }
  }
}
```

### 페이로드 (청크 분할)

전체 크기가 100KB를 초과하면 자동으로 청크 분할된다:

```json
{
  "hub_id": "whatsmatter-nipa_SN-1774090901",
  "ts": "2026-03-21T11:01:00.000Z",
  "chunk": 1,
  "total_chunks": 3,
  "devices": {
    "light.living_room": { ... },
    "light.bedroom": { ... }
  }
}
```

### 청크 처리 규칙

| 필드 | 타입 | 설명 |
|------|------|------|
| `hub_id` | string | 허브 식별자 |
| `ts` | string (ISO8601) | 발행 시각 (UTC) |
| `chunk` | int (1-based) | 현재 청크 번호. **이 필드가 없으면 단일 메시지** |
| `total_chunks` | int | 전체 청크 수. **이 필드가 없으면 단일 메시지** |
| `devices` | object | `entity_id` → 상태 객체 맵 |

**클라우드 수신 로직:**
1. `chunk` 필드가 없으면 → 단일 메시지, 즉시 처리
2. `chunk` 필드가 있으면 → `hub_id` + `ts` 기준으로 모든 청크를 모은 뒤 병합
3. 청크 크기 기본값: 100KB (`MQTT_DEVICE_STATE_CHUNK_SIZE_KB` 환경변수로 조정)

### devices 객체 내 상태 구조

각 디바이스(`entity_id` 키)의 값:

| 필드 | 타입 | 설명 |
|------|------|------|
| `state` | string | Home Assistant 상태 값 (`on`, `off`, `23.5`, `unavailable` 등) |
| `last_changed` | string (ISO8601) | 마지막 상태 변경 시각 |
| `attributes` | object | HA attributes 전체 (friendly_name, device_class, unit_of_measurement 등) |

### 필터링

- `resources/devices.json` 파일이 존재하면 해당 파일에 등록된 `entity_id`만 발행
- 파일이 없으면 HA의 전체 엔티티를 발행

---

## 3. 디바이스 알림 (`event/device_alerts`)

### 토픽

```
matterhub/{hub_id}/event/device_alerts
```

### 발행 조건

- 디바이스가 **unavailable 상태로 전환**될 때 (복구 후 재전환 시 다시 발행)
- 디바이스 **배터리가 임계값 이하**일 때 (`MQTT_ALERT_BATTERY_THRESHOLD`, 기본 0=비활성)

### 페이로드

```json
{
  "hub_id": "whatsmatter-nipa_SN-1774090901",
  "ts": 1711015260,
  "entity_id": "light.living_room",
  "alert_type": "UNAVAILABLE",
  "prev_state": "on",
  "current_state": "unavailable",
  "battery": null,
  "attributes": {
    "friendly_name": "거실 조명",
    "device_class": "light"
  }
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `hub_id` | string | 허브 식별자 |
| `ts` | int (unix epoch) | 알림 발생 시각 |
| `entity_id` | string | 디바이스 식별자 |
| `alert_type` | string | `UNAVAILABLE` 또는 `BATTERY_EMPTY` |
| `prev_state` | string | 이전 상태 |
| `current_state` | string | 현재 상태 |
| `battery` | int \| null | 배터리 잔량 (%) |
| `attributes` | object | `friendly_name`, `device_class`만 포함 |

### 중복 방지

- 동일 엔티티의 동일 alert_type은 **상태가 복구된 후 재전환**될 때만 재발행
- 예: `unavailable → on → unavailable` 시 두 번째 전환에서 다시 발행

---

## 4. IoT Rule 설정

### Rule #3 — 디바이스 상태 수집

| 항목 | 값 |
|------|-----|
| Rule 이름 | (기존) `shadow-state-ingest` 또는 신규 생성 |
| SQL | `SELECT * FROM 'matterhub/+/state/devices'` |
| Action | Lambda (`shadow-state-ingest`) → DynamoDB |

### Rule #4 — 디바이스 알림 수집 (선택)

| 항목 | 값 |
|------|-----|
| SQL | `SELECT * FROM 'matterhub/+/event/device_alerts'` |
| Action | Lambda → DynamoDB / SNS / CloudWatch |

---

## 5. 변경 이력 (Shadow → 이벤트 전환)

| 날짜 | 변경 |
|------|------|
| 2026-03-21 | `publish_initial_shadow_report()` 제거. Shadow 토픽 발행 완전 중단 |
| 2026-03-21 | Konai 전용 토픽 구독 비활성화 (`update/delta/...`, `update/reported/...`) |
| 기존 유지 | `publish_device_states_bulk()` — `matterhub/{hub_id}/state/devices` 토픽으로 시작 시 + 60초 주기 발행 |
| 기존 유지 | `check_and_publish_alerts()` — `matterhub/{hub_id}/event/device_alerts` 토픽으로 실시간 알림 |

### 제거된 토픽

| 토픽 | 상태 |
|------|------|
| `$aws/things/{hub_id}/shadow/update` | **제거됨** — 코드 삭제 완료 |
| `update/delta/dev/.../matter/...` | **비활성화** — 구독 주석 처리 |
| `update/reported/dev/.../matter/...` | **비활성화** — 구독 주석 처리 |

---

## 6. 환경변수 참조

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MQTT_DEVICE_STATE_INTERVAL_SEC` | `60` | 전체 상태 발행 주기 (초) |
| `MQTT_DEVICE_STATE_CHUNK_SIZE_KB` | `100` | 청크 분할 임계치 (KB) |
| `MQTT_ALERT_CHECK_INTERVAL_SEC` | `30` | 알림 체크 주기 (초) |
| `MQTT_ALERT_BATTERY_THRESHOLD` | `0` | 배터리 알림 임계값 (0=비활성) |
| `SUBSCRIBE_MATTERHUB_TOPICS` | `0` | `1`이면 `matterhub/*` 토픽 구독 활성화 |
