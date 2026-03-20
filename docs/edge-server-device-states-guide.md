# 엣지 서버 디바이스 상태 전송 가이드

> MatterHub 엣지 서버 개발자를 위한 디바이스 상태 데이터 전송 지시서
>
> 작성일: 2026-03-21

---

## 1. 문제 상황

### 증상

WebSocket 실시간 디바이스 상태 기능이 백엔드에 구현 완료되었으나, **모든 허브에서 디바이스 상태가 빈 객체(`{}`)로 전달**되어 프론트엔드에 디바이스 정보가 표시되지 않음.

### CloudWatch 로그 (`/aws/lambda/matterhub-ws-connection-dev`)

```
Sent initial snapshot to ahqAfeYsIE0CFHQ= for whatsmatter-nipa_SN-1752564460: 0 devices
Sent initial snapshot to ahqj7cI0oE0AbPw= for whatsmatter-nipa_SN-1752563007: 0 devices
Sent initial snapshot to ah2L0cHooE0CJ-g= for whatsmatter-nipa_SN-1752555902: 0 devices, online=True
```

### DynamoDB 데이터 (`matterhub-device-states-dev` 테이블)

| 필드 | 값 | 상태 |
|------|-----|------|
| `devices` | `{}` | **비정상** — 항상 빈 객체 |
| `device_count` | `21` | 정상 |
| `total_devices` | `207` | 정상 |
| `online` | `true` | 정상 |
| `ha_reachable` | `true` | 정상 |

- `timestamp=0` latest marker 포함 최근 20개 레코드 전부 `devices` 맵이 빈 객체
- hub-level 필드(online, device_count, ha_reachable 등)는 정상 수신 중

---

## 2. 문제 원인

엣지 서버의 shadow 업데이트(`$aws/things/{thingName}/shadow/update`)에 `state.reported.devices` 필드가 **포함되지 않음(빈 객체 `{}`)**. hub-level 메타정보만 전송되고 있음:

```json
{
  "state": {
    "reported": {
      "hub_id": "whatsmatter-nipa_SN-xxx",
      "device_count": 21,
      "total_devices": 207,
      "managed_devices": 38,
      "online": true,
      "ha_reachable": true,
      "devices": {}
    }
  }
}
```

**`devices` 필드가 항상 빈 객체** → DynamoDB에 빈 상태로 저장 → WebSocket 스냅샷/실시간 push 모두 빈 응답

---

## 3. 확인 필요 사항

엣지 서버 코드에서 아래 항목을 확인해주세요:

1. **Shadow publish 코드에서 `devices` 필드 생성 로직**
   - `devices` 맵을 빌드하는 코드가 존재하는지
   - 존재한다면 왜 빈 객체로 전송되는지 (조건 분기, 에러 핸들링 등)

2. **Home Assistant API에서 entity 상태 조회 여부**
   - HA REST API (`/api/states`) 또는 WebSocket API로 entity 목록을 가져오는지
   - 가져온 entity를 `devices` 맵으로 변환하는 로직이 있는지

3. **Shadow 페이로드 사이즈**
   - AWS IoT Classic Shadow는 **8KB 페이로드 제한**
   - 207개 디바이스를 shadow에 직접 포함하면 8KB를 초과할 가능성이 높음
   - 이것이 `devices: {}`로 전송하는 원인일 수 있음

---

## 4. 해결 방안

### 옵션 A (권장): 커스텀 MQTT 토픽으로 디바이스 상태 별도 전송

기존 shadow 업데이트는 hub-level 정보만 유지(현행 유지)하고, **새 커스텀 토픽**에 디바이스 상태를 발행합니다.

#### 토픽

```
matterhub/{hub_id}/state/devices
```

> 기존 패턴과 일관: `matterhub/+/event/device_alerts` 토픽이 이미 운영 중

#### 발행 페이로드

```json
{
  "hub_id": "whatsmatter-nipa_SN-1752562976",
  "devices": {
    "light.living_room": {
      "state": "on",
      "last_changed": "2024-01-01T12:00:00+00:00",
      "attributes": {
        "brightness": 255,
        "color_temp": 153
      }
    },
    "switch.kitchen": {
      "state": "off",
      "last_changed": "2024-01-01T11:55:00+00:00",
      "attributes": {}
    },
    "binary_sensor.front_door": {
      "state": "on",
      "last_changed": "2024-01-01T12:01:00+00:00",
      "attributes": {
        "device_class": "door"
      }
    }
  }
}
```

#### 장점

- **Shadow 8KB 제한 회피** — 커스텀 토픽은 128KB까지 가능
- **기존 shadow 흐름 영향 없음** — hub-level 업데이트는 현행 유지
- **기존 패턴과 일관** — `matterhub/+/event/device_alerts`와 동일한 네이밍 컨벤션
- **페이로드 분할 가능** — 디바이스가 많으면 여러 메시지로 나눠 전송 가능

#### 엣지 서버 구현 사항

1. Home Assistant API에서 entity 상태 조회
2. entity 목록을 `devices` 맵으로 변환 (아래 [데이터 형식](#5-디바이스-데이터-형식) 참조)
3. `matterhub/{hub_id}/state/devices` 토픽에 MQTT publish
4. 주기: shadow 업데이트와 동일 주기 또는 디바이스 상태 변경 시

#### 페이로드가 큰 경우 분할 전송

207개 디바이스를 한 번에 보내기 어려운 경우, 여러 메시지로 분할 가능:

```json
{
  "hub_id": "whatsmatter-nipa_SN-xxx",
  "chunk": 1,
  "total_chunks": 3,
  "devices": { ... 약 70개 디바이스 ... }
}
```

백엔드에서 chunk를 merge하여 처리합니다.

---

### 옵션 B: Named Shadow 활용

디바이스 그룹별 Named Shadow를 생성하여 분산 저장합니다.

#### 구조

```
$aws/things/{thingName}/shadow/name/devices-group-1/update  (디바이스 1~50)
$aws/things/{thingName}/shadow/name/devices-group-2/update  (디바이스 51~100)
$aws/things/{thingName}/shadow/name/devices-group-3/update  (디바이스 101~150)
$aws/things/{thingName}/shadow/name/devices-group-4/update  (디바이스 151~207)
```

#### 단점

- 복잡도 높음 — Named Shadow 개수 관리 필요
- 백엔드 IoT Rule 대폭 수정 필요
- 디바이스 수 변동 시 그룹 재분배 로직 필요

**옵션 A를 권장합니다.**

---

## 5. 디바이스 데이터 형식

### devices 맵 구조

```json
{
  "{entity_id}": {
    "state": "on | off | unavailable | 숫자값 등",
    "last_changed": "ISO 8601 타임스탬프 (선택)",
    "attributes": {
      "key": "value"
    }
  }
}
```

### entity_id 형식

Home Assistant 컨벤션을 따릅니다: `{entity_type}.{entity_name}`

| entity_type | 예시 | 설명 |
|------------|------|------|
| `light` | `light.living_room` | 조명 |
| `switch` | `switch.kitchen` | 스위치 |
| `binary_sensor` | `binary_sensor.front_door` | 이진 센서 |
| `sensor` | `sensor.temperature` | 센서 |
| `climate` | `climate.bedroom_ac` | 에어컨/난방 |
| `cover` | `cover.curtain` | 커튼/블라인드 |
| `lock` | `lock.front_door` | 도어락 |
| `fan` | `fan.living_room` | 선풍기/환풍기 |

### state 값

| entity_type | 가능한 state 값 |
|------------|----------------|
| `light`, `switch`, `fan` | `on`, `off`, `unavailable` |
| `binary_sensor` | `on`, `off`, `unavailable` |
| `sensor` | 숫자값 (예: `"23.5"`), `unavailable` |
| `climate` | `heat`, `cool`, `auto`, `off`, `unavailable` |
| `cover` | `open`, `closed`, `opening`, `closing`, `unavailable` |
| `lock` | `locked`, `unlocked`, `unavailable` |

### attributes 예시

```json
// light
{ "brightness": 255, "color_temp": 153, "friendly_name": "거실 조명" }

// sensor
{ "unit_of_measurement": "°C", "device_class": "temperature", "friendly_name": "온도 센서" }

// binary_sensor
{ "device_class": "door", "friendly_name": "현관 도어 센서" }

// climate
{ "temperature": 24, "current_temperature": 23.5, "hvac_action": "heating" }
```

---

## 6. 필터링 접미사 안내

백엔드 WebSocket 처리에서 아래 접미사로 끝나는 entity는 **자동 제외**됩니다. 전송해도 무방하나 프론트엔드에는 전달되지 않습니다:

```
_firmware
_battery
_update
_identify
_level
_charge_state
_voltage
_time_remaining
```

예시: `sensor.living_room_battery`, `binary_sensor.hub_firmware` → 필터됨

> 이 필터는 `ws_connection.py`와 `ws_device_state_processor.py`에서 `FILTERED_SUFFIXES`로 정의되어 있으며, 빈번하게 변경되거나 UI에 불필요한 entity를 제외하기 위한 것입니다.

---

## 7. 옵션 A 선택 시 백엔드 추가 작업

엣지 서버가 커스텀 토픽(`matterhub/{hub_id}/state/devices`)으로 전송을 시작하면, **백엔드에서 아래 작업을 추가로 진행**합니다:

### 7-1. IoT Rule 추가 (`template.yaml`)

```yaml
DeviceStateRule:
  Type: AWS::IoT::TopicRule
  Properties:
    RuleName: !Sub matterhub_device_state_rule_${Environment}
    TopicRulePayload:
      RuleDisabled: false
      Sql: |
        SELECT
          hub_id,
          devices,
          chunk,
          total_chunks,
          timestamp() as ingest_ts
        FROM 'matterhub/+/state/devices'
      Actions:
        - Lambda:
            FunctionArn: !GetAtt ShadowStateIngestFunction.Arn
```

### 7-2. Lambda 처리 확장

기존 `lambda_function.py`에서 새 토픽의 메시지도 처리하도록 확장:
- `devices` 데이터를 DynamoDB의 기존 레코드(`timestamp=0` latest marker)에 merge
- chunk 메시지인 경우 모든 chunk 수신 후 merge

### 7-3. WebSocket 실시간 push 연동

`ws_device_state_processor.py`가 이미 devices diff 감지 + WebSocket push 로직을 갖추고 있으므로, DynamoDB에 devices 데이터가 저장되면 **자동으로 실시간 push가 작동**합니다.

---

## 8. 전체 데이터 흐름 (옵션 A)

```
┌─────────────────┐
│  MatterHub Edge  │
│     Server       │
└────────┬────────┘
         │
         ├── $aws/things/{thingName}/shadow/update     ← hub-level (현행 유지)
         │   { device_count, online, ha_reachable, ... }
         │
         └── matterhub/{hub_id}/state/devices          ← 신규 추가
             { hub_id, devices: { entity_id: {...} } }
         │
    ─────┼─────────────────────────────────────────
         │  AWS IoT Core
         ├── IoT Rule: ShadowUpdateRule  ──→ Lambda ──→ DynamoDB (hub-level)
         └── IoT Rule: DeviceStateRule   ──→ Lambda ──→ DynamoDB (devices merge)
                                                            │
                                                    DynamoDB Stream
                                                            │
                                                    WebSocket Push
                                                            │
                                                      Frontend
```

---

## 9. 빠른 검증 방법

엣지 서버 코드 수정 전, AWS IoT Core 콘솔의 **MQTT test client**에서 수동 테스트 가능:

### 테스트 publish

**토픽:** `matterhub/whatsmatter-nipa_SN-1752562976/state/devices`

```json
{
  "hub_id": "whatsmatter-nipa_SN-1752562976",
  "devices": {
    "light.living_room": {
      "state": "on",
      "last_changed": "2024-01-01T12:00:00+00:00",
      "attributes": { "brightness": 255 }
    },
    "switch.kitchen": {
      "state": "off",
      "last_changed": "2024-01-01T11:55:00+00:00",
      "attributes": {}
    }
  }
}
```

> **참고:** IoT Rule이 아직 배포되지 않은 상태에서는 이 메시지가 Lambda로 전달되지 않습니다. 백엔드 IoT Rule 배포 후 테스트해주세요.

### 검증 체크리스트

- [ ] MQTT test client에서 publish 성공
- [ ] CloudWatch 로그에서 Lambda 호출 확인
- [ ] DynamoDB `timestamp=0` 레코드에 `devices` 맵이 채워졌는지 확인
- [ ] WebSocket 연결 시 스냅샷에 디바이스가 포함되는지 확인

---

## 10. 질문 및 연락

구현 중 궁금한 점이 있으면 아래 내용을 포함하여 문의해주세요:

- 엣지 서버에서 현재 HA API 호출 방식 (REST / WebSocket)
- entity 목록 샘플 (5~10개)
- shadow publish 코드 스니펫
- 예상 페이로드 사이즈
