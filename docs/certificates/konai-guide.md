# 코나이(Konai) MQTT 연동 가이드

현재 MQTT는 whatsmatter NIPA Claim 인증·AWS 프로비저닝·`matterhub/{id}/...` 토픽 구조를 사용합니다.  
**코나이 측 MQTT URL, Client ID, Topic prefix, 인증서**를 사용해 통신을 재구성할 때 참고하는 가이드입니다.

---

## 1. 코나이 측 엔드포인트 및 인증 정보

| 항목 | 값 |
|------|-----|
| **MQTT URL (엔드포인트)** | `a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com` |
| **Client ID** | `c3c6d27d5f2f353991afac4e3af69029303795a2-matter-{suffix}`  
  예: `k3O6TL` 또는 임의 값 → `c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL` |
| **Topic prefix** | `update/reported/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL`  
  (디바이스 ID·suffix는 환경에 맞게 사용) |
| **인증서** | `konai_certificates/` 디렉토리  
  - `ca_cert.pem` — 루트 인증서  
  - `cert.pem` — 허브용 인증서  
  - `key.pem` — 허브용 개인키 |

---

## 2. 디렉토리·파일 배치

코나이 인증서는 **`konai_certificates/`** 에 두고, 아래 파일명을 유지합니다.

| 파일 | 용도 |
|------|------|
| `konai_certificates/ca_cert.pem` | TLS 서버 검증용 루트 CA (연결 시 옵션으로 지정 가능) |
| `konai_certificates/cert.pem` | 허브(디바이스) 인증서 — MQTT 연결 시 사용 |
| `konai_certificates/key.pem` | 허브(디바이스) 개인키 — MQTT 연결 시 사용 |

**참고:** 코나이는 **프로비저닝(Claim 인증서 발급·사물 등록) 없이** 미리 발급된 `cert.pem` + `key.pem`로 바로 연결하는 구조로 가정합니다.

---

## 3. mqtt.py 수정 개요

수정은 **`mqtt.py` 한 파일**에서 이루어지며, 다음 세 가지를 맞춥니다.

1. **연결 정보** — 엔드포인트, Client ID, 인증서 경로·파일명
2. **프로비저닝 제거** — 코나이 인증서만 사용하므로 `provision_device()` 호출 제거
3. **토픽 구조** — 기존 `matterhub/{matterhub_id}/...` / `$aws/things/...` 를 코나이 Topic prefix 기반으로 변경

---

## 4. 연결 정보 수정 (인증·엔드포인트·Client ID)

**위치:** `mqtt.py` → `AWSIoTClient` 클래스 (`__init__`, `check_certificate`, `connect_mqtt`)

### 4.1 `__init__` (대략 452~457행)

| 현재 | 코나이용 수정 |
|------|----------------|
| `self.cert_path = "certificates/"` | `self.cert_path = "konai_certificates/"` |
| `self.claim_cert = "whatsmatter_nipa_claim_cert.cert.pem"` | 사용 안 함 (아래 4.3에서 프로비저닝 제거) |
| `self.claim_key = "whatsmatter_nipa_claim_cert.private.key"` | 사용 안 함 |
| `self.endpoint = "a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com"` | `self.endpoint = "a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com"` |
| `self.client_id = "whatsmatter-nipa-claim-thing"` | `self.client_id = "c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL"` (또는 suffix를 env 등으로 설정) |

### 4.2 `check_certificate()` (대략 459~466행)

코나이는 **발급된 기기 인증서·키만** 사용하므로, 확인 대상 파일명을 코나이 파일명으로 통일합니다.

| 현재 | 코나이용 수정 |
|------|----------------|
| `cert_file = os.path.join(self.cert_path, "device.pem.crt")` | `cert_file = os.path.join(self.cert_path, "cert.pem")` |
| `key_file = os.path.join(self.cert_path, "private.pem.key")` | `key_file = os.path.join(self.cert_path, "key.pem")` |

### 4.3 `connect_mqtt()` — 프로비저닝 제거 (대략 620~631행)

- **현재:** 인증서가 없으면 `provision_device()` 호출 후 `device.pem.crt` / `private.pem.key` 생성.
- **코나이:** 프로비저닝 없음. `cert.pem`·`key.pem`이 없으면 에러로 종료하도록 변경.

수정 예:

```python
has_cert, cert_file, key_file = self.check_certificate()
if not has_cert:
    raise Exception("konai_certificates/cert.pem 또는 key.pem이 없습니다. 코나이 인증서를 넣어 주세요.")
# provision_device() 호출 제거
```

그리고 **연결 시 사용하는 client_id**를 코나이 형식으로 유지합니다 (대략 631행 부근에서 `self.client_id`를 바꾸지 않거나, `__init__`에서 넣은 코나이 client_id를 그대로 사용).

### 4.4 루트 CA 사용 (선택)

코나이 브로커가 별도 루트 CA로 서명했을 경우, AWS IoT Python SDK v2의 `mtls_from_path`에 **`ca_filepath`** 를 넘길 수 있습니다.  
대략 655행 부근 `mqtt_connection_builder.mtls_from_path(...)` 호출에 다음을 추가합니다.

- `ca_filepath=os.path.join(self.cert_path, "ca_cert.pem")`  
  (파일이 있을 때만 넘기거나, 코나이 측 안내에 따라 적용)

---

## 5. Topic prefix 및 토픽 문자열 재구성

코나이 Topic prefix 예:

- `update/reported/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL`

기존 코드는 **`matterhub_id`** 와 **`matterhub/...`**, **`$aws/things/...`** 를 사용합니다.  
코나이로 재구성할 때는 아래와 같이 **디바이스 ID·suffix**를 상수 또는 환경변수로 두고, 코나이 규칙에 맞는 토픽으로 치환합니다.

| 용도 | 현재 (예시) | 코나이용 (예시) |
|------|-------------|------------------|
| Shadow/상태 보고 | `$aws/things/{matterhub_id}/shadow/update` | `update/reported/dev/{device_id}/matter/{suffix}` 등 코나이 규칙에 맞는 토픽 |
| API 응답 | `matterhub/{matterhub_id}/api/response` | 코나이에서 정한 API 응답 토픽 |
| 구독 (API 요청) | `matterhub/{matterhub_id}/api` | 코나이에서 정한 API 요청 토픽 |
| 알림 이벤트 | `matterhub/{matterhub_id}/event/device_alerts` | 코나이에서 정한 알림 토픽 |
| 헬스체크 | `matterhub/{matterhub_id}/health` | 코나이에서 정한 헬스 토픽 |
| 업데이트 응답 | `matterhub/{matterhub_id}/update/response` | 코나이에서 정한 업데이트 응답 토픽 |
| Git 업데이트 구독 | `matterhub/{matterhub_id}/git/update`, `matterhub/update/specific/{matterhub_id}` | 코나이에서 정한 업데이트 토픽 |

**mqtt.py에서 수정할 위치 (참고용 행 번호):**

- **336** — 알림 발행 토픽
- **414~417** — 재연결 시 구독 토픽
- **861** — Shadow 업데이트 토픽
- **898** — 헬스체크 토픽
- **950, 955** — HA 요청 응답 토픽
- **965, 991, 1018** — 업데이트 응답 토픽
- **1535** — Git 업데이트 명령 수신 분기 (토픽 비교)
- **1629~1632** — 최초 구독 토픽 목록

**적용 방법:**  
코나이에서 제공하는 **토픽 명세(구독/발행 토픽 목록)**에 맞춰, 위 구간의 문자열을 코나이 prefix·경로 규칙으로 바꾸면 됩니다.  
`matterhub_id` 대신 코나이 **device_id**·**suffix**를 쓰려면, 상단에서 `matterhub_id = os.environ.get('matterhub_id')` 를 쓰는 부분을 코나이용 ID/suffix로 읽도록 바꾸거나, 별도 변수(예: `konai_device_id`, `konai_suffix`)를 두고 토픽 문자열만 그에 맞게 조합하면 됩니다.

### 5.5 코나이 토픽 분리 (delta / reported) + 메시지 규격 (구현됨)

**토픽**  
core ↔ 허브 방향에 따라 **토픽 2개**를 사용합니다.  
- **구독(요청 수신)**: core → 허브 방향 → **delta** 토픽  
- **발행(응답·이벤트)**: 허브 → core 방향 → **reported** 토픽  

호환성 주의:

- 현재 운영 코드에서는 v1.0 회귀 방지를 위해 **기본값은 단일 reported 토픽**을 유지합니다.
- `delta/reported` 분리를 실제로 사용할 때는 `.env` 또는 실행 환경에서 `KONAI_TOPIC_REQUEST`, `KONAI_TOPIC_RESPONSE` 를 **명시적으로 설정**해야 합니다.
- `KONAI_TOPIC` 만 설정하면 구독/발행 모두 같은 단일 토픽을 사용합니다.

| 구분 | 토픽 예시 |
|------|-----------|
| 구독(REQUEST) | `update/delta/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL` |
| 발행(RESPONSE) | `update/reported/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL` |

응답 타입: `query_response_all`, `query_response_single` 등은 모두 **reported** 토픽으로 발행합니다.

| 환경변수 | 용도 | 기본값(예시) |
|----------|------|------------------|
| `KONAI_TOPIC` | (선택) 레거시 단일 토픽. 설정 시 구독·발행 모두 이 토픽 사용 | 미설정 |
| `KONAI_TOPIC_REQUEST` | 구독 전용 토픽 (core 요청 수신). 명시 시 split 활성화 | 미설정 |
| `KONAI_TOPIC_RESPONSE` | 발행 전용 토픽 (응답·이벤트). 명시 시 split 활성화 | 미설정 |
| `LOCAL_API_BASE` | 로컬 API base URL | `http://localhost:8100` |
| `KONAI_REPORT_ENTITY_IDS` | 변경 시 `entity_changed` 발행할 entity_id 목록 (쉼표 구분) | `sensor.smart_ht_sensor_ondo` |
| `KONAI_EVENT_THROTTLE_SEC` | 동일 entity_id 최소 발행 간격(초) | `2` |
| `KONAI_EVENT_DEDUP_WINDOW_SEC` | 동일 값 연속 발행 방지 구간(초) | `3` |

**요청(Request) 규격**  
- payload: JSON 객체, **`correlation_id` 필수** (또는 `request_id`). 선택: `entity_id` (있으면 단일 조회).  
- `correlation_id` 없음 → `type: "error"`, `code: "MISSING_CORRELATION_ID"` 발행.  
- JSON 파싱 실패 → `type: "error"`, `code: "INVALID_JSON"` 발행.

**응답(Response) 규격**  
- 공통: `type`, `correlation_id`, `ts`(ISO8601).  
- 성공: `type: "query_response_single"` 또는 `"query_response_all"`, `data`에 본문.  
- 실패: `type: "error"`, `error: { code, message, detail? }`. 코드 예: `INVALID_JSON`, `MISSING_CORRELATION_ID`, `INVALID_ENTITY_ID`, `LOCAL_API_ERROR`, `TIMEOUT`.

**이벤트(요청 무관)**  
- **bootstrap 1회:** 구독 완료 후 1회만 `type: "bootstrap_all_states"`, `correlation_id: null`, `data: [전체 states]` 발행.  
- **변경 알림:** `KONAI_REPORT_ENTITY_IDS`에 있는 entity만 변경 시 `type: "entity_changed"`, `correlation_id: null`, `event_id`, `ts`, `entity_id`, `state`(HA 엔티티 스냅샷) 발행.  
- 전체 상태는 **초기 1회만** 발행하며, 이후 반복 발행 없음. 실시간 갱신은 `entity_changed`만 사용.

---

## 6. matterhub_id 사용처 정리

코나이에서는 **thingName/matterhub_id** 대신 **디바이스 ID + suffix** 구조를 쓰므로, 다음을 정리하는 것이 좋습니다.

- **환경변수:** `.env`의 `matterhub_id`를 그대로 쓸지, 코나이용 `KONAI_DEVICE_ID`, `KONAI_SUFFIX` 등을 추가할지 결정.
- **payload 내 hub_id:** Shadow, 알림, 업데이트 응답 등에 들어가는 `hub_id` 필드를 코나이 디바이스 식별자로 매핑할지 결정.

위 5장 토픽을 바꿀 때, 같은 규칙으로 `hub_id`만 코나이 식별자로 통일하면 됩니다.

---

## 7. Dockerfile (배포 시)

이미지에 코나이 인증서를 포함할 때:

| 현재 | 수정 |
|------|------|
| `COPY certificates/ /app/certificates/` | `COPY konai_certificates/ /app/konai_certificates/` |

실행 시 작업 디렉토리가 `/app`이면, `mqtt.py`의 `cert_path = "konai_certificates/"` 와 일치하도록 위 경로를 사용하면 됩니다.

---

## 8. 적용 시 발생할 수 있는 문제점

아래는 현재 코드 구조를 기준으로, 코나이 방식으로 바꿀 때 **실제로 생길 수 있는 문제**와 대응 요약입니다.

### 8.1 matterhub_id가 비어 있거나 None인 경우

- **원인:** 기존에는 `register_thing()` 성공 시 `matterhub_id`가 설정되고 `.env`에 저장됩니다. 코나이는 프로비저닝을 제거하므로 `register_thing()`을 타지 않아, **모듈 로드 시 `os.environ.get('matterhub_id')`만 사용**합니다.
- **증상:** `.env`에 `matterhub_id`가 없거나 빈 값이면 `matterhub_id`가 `None`이 되고, `f"matterhub/{matterhub_id}/api"` 등 모든 토픽/페이로드가 `"matterhub/None/..."` 형태로 잘못됩니다. 연결은 될 수 있으나 구독·발행이 코나이와 맞지 않습니다.
- **대응:** 코나이용 디바이스 식별자(예: `c3c6d27d5f2f353991afac4e3af69029303795a2` 또는 코나이에서 준 ID)를 **반드시 `.env`에 `matterhub_id`로 넣거나**, 코드에서 `KONAI_DEVICE_ID` 등 별도 env로 읽어 토픽·payload에만 사용하도록 합니다.

### 8.2 client_id가 덮어쓰기되는 경우

- **원인:** `connect_mqtt()` 대략 631행에서 `self.client_id = f"device_{int(time.time())}"` 로 **항상 새 client_id로 덮어씁니다**. 코나이 브로커가 특정 Client ID 형식을 요구하면 이 값이 거절될 수 있습니다.
- **증상:** 연결 실패(Connection Refused 등) 또는 “Client ID not allowed” 유형 오류.
- **대응:** 코나이 연동 시에는 위 줄을 **수정·제거**하고, `__init__`에서 설정한 코나이 형식 client_id(`c3c6d27d5f2f353991afac4e3af69029303795a2-matter-{suffix}`)가 유지되도록 합니다.

### 8.3 토픽을 일부만 바꾼 경우

- **원인:** 토픽 문자열이 **최초 구독(1629~1632행)**, **재연결 시 구독(414~417행)**, **mqtt_callback 내 분기(1535행 등)**, **발행(336, 861, 898, 950, 965, 991, 1018행 등)** 여러 곳에 흩어져 있습니다. 한 곳만 바꾸면 구독은 새 토픽, 콜백은 예전 토픽으로 비교하거나 그 반대가 되어 **메시지를 받지 못하거나 응답이 안 나갑니다**.
- **증상:** 연결·구독은 성공하는데 명령이 안 오거나, 발행한 메시지가 서버/앱에 안 보임.
- **대응:** 가이드 §5의 **모든 행**을 코나이 토픽 명세에 맞게 바꾸고, **구독 목록과 콜백 내 `topic == ...` / `topic.startswith(...)` 조건을 동일한 토픽 규칙으로** 맞춥니다.

### 8.4 AWS Device Shadow와 코나이 토픽 구조 차이

- **원인:** 현재 `update_device_shadow()`는 `$aws/things/{matterhub_id}/shadow/update` 로 AWS IoT Device Shadow 규격에 맞춰 발행합니다. 코나이가 **Shadow를 쓰지 않고** `update/reported/dev/.../matter/...` 같은 별도 토픽을 쓰면, 그대로 두면 **코나이 쪽에서 상태를 인식하지 못합니다**.
- **증상:** 코나이 앱/서버에서 디바이스 상태가 안 보이거나, “reported” 데이터가 없다고 나옴.
- **대응:** 코나이에서 “상태 보고용 토픽”을 명시해 주면, 해당 토픽과 payload 형식에 맞춰 **Shadow 업데이트 발행 부분만** 코나이 규격으로 바꿉니다. 필요하면 `$aws/things/...` 발행은 제거하거나 비활성화합니다.

### 8.5 재연결 시 구독 토픽 불일치

- **원인:** `check_mqtt_connection()` 안에서 재연결 후 다시 구독하는 토픽 목록(414~417행)이 **최초 구독(1629~1632행)과 다르면**, 재연결 후에는 다른 토픽만 구독하게 됩니다.
- **증상:** 한동안 잘 되다가 끊겼다 재연결된 뒤부터 명령 수신이 안 됨.
- **대응:** **한 곳에서 토픽 목록을 상수/리스트로 정의**하고, 최초 구독과 재연결 구독이 **같은 리스트를 참조**하도록 하면 됩니다.

### 8.6 update_server.sh / 업데이트 스크립트의 hub_id

- **원인:** `execute_external_update_script()` 등에서 `update_server.sh`에 `matterhub_id`를 인자로 넘깁니다(1207행 등). 코나이만 사용할 때 이 값을 다른 식별자로 바꾸지 않으면, 업데이트 로그/모니터링에서 혼동이 생길 수 있습니다.
- **대응:** 코나이 환경에서는 `.env`의 `matterhub_id`를 코나이 디바이스 ID(또는 허브 식별자)로 두면, 스크립트는 수정하지 않아도 됩니다. 별도 변수를 쓰는 경우, 스크립트에 넘기는 인자를 그 변수로 맞춥니다.

### 8.7 인증서 파일 없음

- **원인:** `check_certificate()`가 `cert.pem`·`key.pem`만 보는데, 프로비저닝을 제거했으므로 **파일이 없으면** `connect_mqtt()`에서 바로 예외 처리합니다.
- **증상:** 기동 시 “konai_certificates/cert.pem 또는 key.pem이 없습니다” 등으로 즉시 종료.
- **대응:** 배포/실행 전에 `konai_certificates/` 에 `cert.pem`, `key.pem`(및 필요 시 `ca_cert.pem`)이 있는지 확인하고, Dockerfile 등에서는 해당 디렉터리를 올바르게 복사하도록 합니다.

---

## 9. 적용 후 정상 동작 테스트 방법

코나이 적용 후 **연결·구독이 정상인지** 아래 순서로 확인할 수 있습니다.

### 9.1 사전 확인

- [ ] `konai_certificates/` 에 `cert.pem`, `key.pem` (및 필요 시 `ca_cert.pem`) 존재.
- [ ] `.env`에 `matterhub_id` 또는 코나이용 디바이스 ID가 설정되어 있음 (코나이 토픽 규칙과 일치).
- [ ] `mqtt.py`에서 endpoint, client_id, 인증서 경로·파일명, 프로비저닝 제거, 토픽 문자열을 가이드대로 반영했는지 확인.

### 9.2 로컬 실행 및 로그로 연결·구독 확인

1. **실행**
   ```bash
   cd /path/to/matterhub-flask
   python3 mqtt.py
   ```
2. **연결 성공 여부**
   - 터미널에 **"MQTT 연결 성공"** 이 출력되는지 확인.
   - 그 전에 **"인증서 발급 실패"** / **"cert.pem 또는 key.pem이 없습니다"** 등이 나오면 인증서 경로·파일명·프로비저닝 제거 처리를 다시 확인.
3. **구독 성공 여부**
   - **"토픽 구독 시작..."** 다음에 **"✅ {토픽명} 토픽 구독 완료"** 가 코나이에서 사용하는 **모든 구독 토픽**에 대해 나오는지 확인.
   - **"❌ 토픽 구독 실패"** 가 있으면 해당 토픽 문자열이 코나이 명세와 일치하는지 확인.
4. **정상 구독 상태 유지**
   - **"📡 모든 토픽 구독 완료"** 후에도 프로세스가 종료되지 않고, 주기적으로 Shadow/상태 업데이트나 헬스체크 로그가 나오면 **연결·구독이 유지되는 상태**로 볼 수 있습니다.
   - 재연결이 발생하면 **"✅ MQTT 연결 재개됨"**, **"✅ 토픽 재구독 성공"** 이 나오는지 확인.

### 9.3 발행( publish ) 동작 확인 (선택)

- 코나이 쪽에서 **특정 토픽을 구독**하고 있다면, 해당 토픽으로 테스트 메시지를 한 번 발행해 보는 방법이 있습니다.
- 예: 상태 보고 토픽(`update/reported/dev/...`)으로 보내는 `update_device_shadow()` 가 실제로 호출되는지 로그로 확인하고, 코나이 앱/대시보드에서 해당 디바이스 상태가 갱신되는지 봅니다.
- **주의:** 코나이 브로커가 허용하는 토픽·QoS·payload 형식을 지켜야 합니다.

### 9.4 외부 클라이언트로 구독 확인 (선택)

- 코나이 브로커가 **일반 MQTT** 접속을 허용한다면, 같은 네트워크에서 `mosquitto_sub` 등으로 **코나이에서 지정한 토픽**을 구독해 봅니다.
- 예: `mosquitto_sub -h a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com -p 8883 ...` (실제로는 TLS·인증서 옵션 필요).  
  이 경우 **브로커 접속 방식·인증서 사용 가능 여부**는 코나이 측 문서를 따릅니다.
- 허브(mqtt.py)가 **발행하는 토픽**을 여기서 수신할 수 있으면, “정상 구독·발행”이 동작하는 것으로 추가 확인할 수 있습니다.

### 9.5 체크리스트 요약

| 확인 항목 | 기대 결과 |
|-----------|-----------|
| 프로세스 기동 | `python3 mqtt.py` 실행 후 예외 없이 진입 |
| 연결 | 로그에 "MQTT 연결 성공" 출력 |
| 구독 | 사용하는 모든 토픽에 대해 "✅ ... 토픽 구독 완료" 출력 |
| 구독 유지 | "📡 모든 토픽 구독 완료" 후 프로세스 유지, 주기 로그(Shadow/헬스 등) 출력 |
| 재연결 | 끊김 후 "✅ MQTT 연결 재개됨", "✅ 토픽 재구독 성공" 등으로 복구 |
| (선택) 상태 반영 | 코나이 앱/대시보드에서 해당 디바이스 상태 또는 메시지 수신 확인 |

위를 통과하면 **적용이 정상적으로 되었고, 정상 구독 상태로 동작 중**으로 볼 수 있습니다.

---

## 10. 수정 후 확인 (요약)

1. **인증서:** `konai_certificates/` 에 `ca_cert.pem`, `cert.pem`, `key.pem` 존재 여부.
2. **연결:** `mqtt.py`에서 `cert_path`, `cert.pem`/`key.pem` 사용, `endpoint`, `client_id`가 1장·4장과 일치하는지.
3. **프로비저닝:** `connect_mqtt()`에서 `provision_device()` 호출이 제거되었는지.
4. **토픽:** 구독·발행·콜백 분기 토픽이 코나이 명세와 일치하는지.
5. **실행·테스트:** §9에 따라 `python3 mqtt.py` 실행 후 연결·구독 로그와(선택) 발행/외부 구독으로 정상 여부 확인.

---

## 11. 요약

| 구분 | 내용 |
|------|------|
| **인증서** | `konai_certificates/` — `ca_cert.pem`, `cert.pem`, `key.pem` 사용. 연결 시 `cert.pem`·`key.pem` 필수, 필요 시 `ca_filepath`로 `ca_cert.pem` 지정. |
| **연결** | MQTT URL·Client ID는 1장 값으로 고정 또는 env로 설정. 프로비저닝 없이 `check_certificate()` 통과 시 바로 연결. |
| **토픽** | 기존 `matterhub/...`·`$aws/things/...` 를 코나이 Topic prefix 기반으로 치환. device_id·suffix는 1장 규칙에 맞게 설정. |
| **수정 파일** | `mqtt.py` (연결 정보, 프로비저닝 제거, 토픽 문자열). 배포 시 `Dockerfile` 인증서 복사 경로. |

이 가이드대로 적용하면 코나이 MQTT URL, Client ID, Topic prefix, `konai_certificates` 인증서까지 포함해 MQTT 통신을 재구성할 수 있습니다. 적용 전 §8(잠재적 문제점)을 확인하고, 적용 후 §9(테스트 방법)으로 연결·구독 상태를 검증하면 됩니다.
