---
name: device-remote-update
description: MatterHub MQTT 원격 명령 전송. git_update, set_env, bundle_update, bundle_check 4가지 원격 명령을 AWS IoT MQTT 토픽으로 전송한다. "/device-remote-update" 또는 "원격 업데이트", "MQTT 명령" 시 사용.
---

# MatterHub MQTT 원격 명령 전송

AWS IoT Core MQTT 토픽을 통해 디바이스에 원격 명령을 전송하는 스킬.

## 사전 조건

- 디바이스가 systemd로 동작 중이고 `matterhub-mqtt` 서비스 active
- 디바이스의 `SUBSCRIBE_MATTERHUB_TOPICS=1` 설정 완료
- AWS IoT Core 콘솔 또는 MQTT 클라이언트 접근 가능

사용자에게 다음을 확인한다:

| 항목 | 예시 | 필수 |
|------|------|------|
| 대상 | 특정 hub_id / all / region | Y |
| 명령 | git_update / set_env / bundle_update / bundle_check | Y |
| 명령별 파라미터 | 아래 참조 | Y |

## MQTT 토픽 구조

| 대상 | 토픽 |
|------|------|
| 특정 장비 | `matterhub/update/specific/{matterhub_id}` |
| 전체 장비 | `matterhub/update/all` |
| 지역별 | `matterhub/update/region/{region_name}` |

**응답 토픽:** `matterhub/{matterhub_id}/update/response`

## update_id 생성 규칙

`update_id`는 모든 응답 추적의 키이므로 **전역적으로 고유**해야 한다.
특히 `matterhub/update/all`이나 `matterhub/update/region/*`으로
다수 장비에 동시 전송할 때, 동일한 update_id가 모든 장비에 전달되므로
장비별 응답을 구분할 수 있도록 고유 ID를 사용한다.

**권장: UUID v4**
```bash
uuidgen                                          # macOS/Linux
python3 -c "import uuid; print(uuid.uuid4())"   # Python
```

**명명 규칙:** `{command}-{uuid}` 형태 권장
- `git_update`: `update-550e8400-e29b-41d4-a716-446655440000`
- `set_env`: `setenv-a1b2c3d4-5678-9abc-def0-123456789abc`
- `bundle_update`: `bundle-f47ac10b-58cc-4372-a567-0e02b2c3d479`
- `bundle_check`: `check-7c9e6679-7425-40de-944b-e07fc1f90ae7`

## 명령 1: git_update (코드 업데이트)

최신 코드를 git pull하고 서비스를 재시작한다.

```json
{
  "command": "git_update",
  "update_id": "update-550e8400-e29b-41d4-a716-446655440000",
  "branch": "master",
  "force_update": false
}
```

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `command` | string | - | `"git_update"` |
| `update_id` | string | - | 고유 식별자 (UUID 권장) |
| `branch` | string | `"master"` | 배포 브랜치 |
| `force_update` | bool | `false` | `true`면 로컬 변경 무시하고 강제 pull |

**동작 순서:** 수신 → `ack` 즉시응답 → git pull (skip-restart) → PID 모니터링 → `result` 최종응답 → 서비스 재시작

## 명령 2: set_env (.env 원격 변경)

디바이스의 `.env` 파일에서 허용된 키의 값을 변경한다.

```json
{
  "command": "set_env",
  "update_id": "setenv-a1b2c3d4-5678-9abc-def0-123456789abc",
  "key": "MATTERHUB_REGION",
  "value": "seoul",
  "restart": false
}
```

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `command` | string | - | `"set_env"` |
| `update_id` | string | - | 고유 식별자 (UUID 권장) |
| `key` | string | - | 변경할 .env 키 (화이트리스트) |
| `value` | string | - | 새 값 |
| `restart` | bool | `false` | `true`면 변경 후 서비스 재시작 |

**참고:** `MATTERHUB_REGION` 변경 시에는 `restart` 값과 무관하게 **항상 자동 재시작**된다 (새 지역 토픽 구독을 위해).

**허용된 키 (화이트리스트):**
- `MATTERHUB_REGION`
- `SUBSCRIBE_MATTERHUB_TOPICS`
- `MQTT_EVENT_THROTTLE_SEC`
- `MQTT_EVENT_DEDUP_WINDOW_SEC`
- `MQTT_DEVICE_STATE_INTERVAL_SEC`
- `MQTT_ALERT_CHECK_INTERVAL_SEC`
- `MQTT_ALERT_BATTERY_THRESHOLD`

허용되지 않은 키를 보내면 에러 응답이 반환된다.

## 명령 3: bundle_update (번들 배포)

URL에서 `.deb` 번들을 다운로드하여 inbox에 저장한다. 실제 적용은 `update-agent` 서비스가 수행.

```json
{
  "command": "bundle_update",
  "update_id": "bundle-f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "url": "https://s3.ap-northeast-2.amazonaws.com/bucket/matterhub_1.2.0_arm64.deb",
  "sha256": "abc123..."
}
```

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `command` | string | - | `"bundle_update"` |
| `update_id` | string | - | 고유 식별자 (UUID 권장) |
| `url` | string | - | `.deb` 번들 다운로드 URL |
| `sha256` | string | `""` | SHA256 해시 (검증용, 선택) |

**동작 순서:** 수신 → `ack` 즉시응답 → 다운로드 → inbox 저장 → `result` 최종응답

## 명령 4: bundle_check (inbox 상태 확인)

디바이스의 inbox에 대기 중인 번들 목록을 조회한다.

```json
{
  "command": "bundle_check",
  "update_id": "check-7c9e6679-7425-40de-944b-e07fc1f90ae7"
}
```

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `command` | string | - | `"bundle_check"` |
| `update_id` | string | - | 고유 식별자 (UUID 권장) |

## AWS IoT Core 콘솔에서 전송하는 방법

1. AWS IoT Core 콘솔 → **MQTT test client** 접속
2. **Publish to a topic** 탭 선택
3. Topic: 위 토픽 구조에 맞게 입력
4. Message payload: 위 JSON 입력
5. **Publish** 클릭

### 응답 확인

1. **Subscribe to a topic** 탭에서 `matterhub/+/update/response` 구독
2. 명령 전송 후 응답 메시지 확인

## 응답 메시지 구조

```json
{
  "update_id": "update-550e8400-e29b-41d4-a716-446655440000",
  "hub_id": "whatsmatter-nipa_SN-1773129896",
  "timestamp": 1711180800,
  "command": "git_update",
  "phase": "ack",
  "status": "processing",
  "message": "Update command received and processing"
}
```

| phase | status | 의미 |
|-------|--------|------|
| `ack` | `processing` / `downloading` | 수신 확인 (즉시 응답) |
| `result` | `success` | 명령 완료 |
| `result` | `failed` | 명령 실패 (error 필드에 상세) |

**`phase` 필드로 즉시 응답(ack)과 최종 결과(result)를 구분한다.**

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 응답 없음 | `SUBSCRIBE_MATTERHUB_TOPICS=0` | `set_env`로 1로 변경 + restart |
| 응답 없음 | matterhub-mqtt 서비스 다운 | 디바이스에서 `systemctl status matterhub-mqtt` 확인 |
| PUBACK 타임아웃 | AWS IoT 정책에 response Publish 권한 없음 | IoT 정책에 `matterhub/*/update/response` Publish 추가 |
| `key not allowed` | set_env에 허용되지 않은 키 | 위 화이트리스트 확인 |
| bundle 다운로드 실패 | URL 접근 불가 / DNS 실패 | URL을 디바이스에서 curl로 직접 테스트 |

## 활용 예시

### 전체 장비에 코드 업데이트

```json
토픽: matterhub/update/all
{
  "command": "git_update",
  "update_id": "update-550e8400-e29b-41d4-a716-446655440000",
  "branch": "master",
  "force_update": false
}
```

### 특정 장비에 .env 변경

```json
토픽: matterhub/update/specific/whatsmatter-nipa_SN-1773129896
{
  "command": "set_env",
  "update_id": "setenv-a1b2c3d4-5678-9abc-def0-123456789abc",
  "key": "MATTERHUB_REGION",
  "value": "seoul"
}
```

**참고:** `MATTERHUB_REGION` 변경 시 `restart` 플래그 불필요 (자동 재시작됨).

### 전체 장비에 번들 배포

```json
토픽: matterhub/update/all
{
  "command": "bundle_update",
  "update_id": "bundle-f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "url": "https://s3.ap-northeast-2.amazonaws.com/bucket/matterhub_1.3.0_arm64.deb",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb924..."
}
```
