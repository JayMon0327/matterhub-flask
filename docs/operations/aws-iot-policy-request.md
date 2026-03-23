# AWS IoT Thing 정책 업데이트 요청서

**작성일:** 2026-03-23
**요청자:** 엣지 서버 개발팀
**대상:** 클라우드 백엔드팀

---

## 1. 배경

MatterHub 엣지 허브에 MQTT 기반 원격 업데이트 기능(`bundle_update`, `bundle_check`)을 추가했습니다.
엣지 서버가 업데이트 명령을 수신한 뒤 처리 결과를 **응답 토픽으로 발행(publish)**해야 하는데,
현재 IoT Thing 정책에 해당 토픽에 대한 `iot:Publish` 권한이 없어 PUBACK 타임아웃이 발생합니다.

**검증 환경:** `whatsmatter-nipa_SN-1752564460` (15045 장비)
- 명령 수신(Subscribe) → 핸들러 처리: **정상**
- 응답 발행(Publish) → `matterhub/{hub_id}/update/response`: **PUBACK 실패**

---

## 2. 요청 사항

### 2-1. MatterHub 전체 허브 Thing에 적용할 IoT 정책 업데이트

개별 Thing이 아닌, **MatterHub 허브 Thing 전체에 적용되는 공통 정책**에 아래 Publish 권한을 추가해 주세요.
(프로비저닝 시 Thing에 부착되는 정책 — `config.py` 또는 프로비저닝 템플릿에서 관리하는 정책)

### 2-2. 추가할 Publish 토픽

엣지 서버가 현재 사용하는 **전체 Publish 토픽 패턴**은 아래와 같습니다.
기존에 이미 허용된 항목도 포함하여 누락 없이 정리했으니, 현재 정책과 대조하여 빠진 항목을 추가해 주세요.

| # | 토픽 패턴 | 용도 | 비고 |
|---|----------|------|------|
| 1 | `matterhub/${iot:Connection.Thing.ThingName}/update/response` | **업데이트 명령 응답** (git_update, set_env, bundle_update, bundle_check) | **신규 — 현재 누락** |
| 2 | `matterhub/${iot:Connection.Thing.ThingName}/state/devices` | 디바이스 상태 주기 보고 (60초 간격) | 기존 |
| 3 | `matterhub/${iot:Connection.Thing.ThingName}/event/device_alerts` | 디바이스 알림 (배터리 부족, unavailable 등) | 기존 |

> **참고:** 코나이 외주 토픽(`update/reported/dev/...`, `update/delta/dev/...`)은 현재 비활성화 상태이므로 별도 추가 불필요합니다.

### 2-3. 정책 JSON 예시 (Publish 부분)

```json
{
  "Effect": "Allow",
  "Action": "iot:Publish",
  "Resource": [
    "arn:aws:iot:ap-northeast-2:{ACCOUNT_ID}:topic/matterhub/${iot:Connection.Thing.ThingName}/update/response",
    "arn:aws:iot:ap-northeast-2:{ACCOUNT_ID}:topic/matterhub/${iot:Connection.Thing.ThingName}/state/devices",
    "arn:aws:iot:ap-northeast-2:{ACCOUNT_ID}:topic/matterhub/${iot:Connection.Thing.ThingName}/event/device_alerts"
  ]
}
```

> `${iot:Connection.Thing.ThingName}`은 AWS IoT 정책 변수로, 각 Thing의 이름(= matterhub_id)으로 자동 치환됩니다.
> 이렇게 하면 개별 Thing마다 정책을 수정할 필요 없이 한 정책으로 모든 허브를 커버할 수 있습니다.

---

## 3. Subscribe 토픽 (참고 — 변경 없음)

현재 엣지 서버가 구독하는 토픽입니다. 변경 사항은 없으나 대조용으로 첨부합니다.

| # | 토픽 패턴 | 용도 |
|---|----------|------|
| 1 | `matterhub/update/specific/${iot:Connection.Thing.ThingName}` | 개별 허브 업데이트 명령 |
| 2 | `matterhub/update/all` | 전체 허브 업데이트 명령 |
| 3 | `matterhub/update/region/*` | 지역별 업데이트 명령 (MATTERHUB_REGION 설정 시) |
| 4 | `matterhub/${iot:Connection.Thing.ThingName}/state-changed` | HA 상태 변경 트리거 |

---

## 4. 업데이트 명령 흐름 (참고)

```
클라우드 → Publish → matterhub/update/specific/{hub_id}
                      matterhub/update/all
                      matterhub/update/region/{region}
    ↓
엣지 허브 (Subscribe → 명령 수신 → 처리)
    ↓
엣지 허브 → Publish → matterhub/{hub_id}/update/response  ← 이 토픽이 현재 차단됨
    ↓
클라우드 (Subscribe → 결과 수신)
```

### 지원하는 명령 (command 필드)

| command | 설명 | 비고 |
|---------|------|------|
| `git_update` | Git pull + 서비스 재시작 | 기존 |
| `set_env` | .env 환경변수 원격 수정 | 기존 |
| `bundle_update` | URL에서 번들 다운로드 → inbox 전달 | **신규** |
| `bundle_check` | inbox 대기 번들 상태 조회 | **신규** |

---

## 5. 검증 방법

정책 적용 후 아래 명령으로 검증 가능합니다:

```bash
# AWS IoT 콘솔 MQTT 테스트 클라이언트에서:

# 1) 응답 토픽 구독
Subscribe: matterhub/whatsmatter-nipa_SN-1752564460/update/response

# 2) bundle_check 명령 발행
Publish to: matterhub/update/specific/whatsmatter-nipa_SN-1752564460
Payload:
{
  "command": "bundle_check",
  "update_id": "policy-test-001"
}

# 3) 기대 응답 (2건):
#    - {"status": "processing", "command": "bundle_check", ...}
#    - {"status": "success", "command": "bundle_check", "result": {"inbox_pending": 0, ...}}
```

---

## 6. 긴급도

- **중간** — 업데이트 명령 자체는 정상 동작하나, 클라우드에서 결과를 확인할 수 없는 상태
- 정책 적용 전까지는 장비 로그(`journalctl -u matterhub-mqtt`)에서만 결과 확인 가능
