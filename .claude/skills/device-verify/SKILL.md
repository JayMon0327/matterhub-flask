---
name: device-verify
description: MatterHub 배포 후 검증. app.py와 mqtt.py를 실행하고 로그를 분석하여 정상 동작을 확인한다. "/device-verify" 또는 "디바이스 테스트", "배포 검증" 시 사용.
---

# MatterHub 배포 후 검증

디바이스에서 app.py(Flask)와 mqtt.py(MQTT)를 실행하고 로그를 분석하여 정상 동작을 확인하는 스킬.

## 사전 조건

- `/device-setup` + `/device-provision` 완료 상태
- `.env`에 `matterhub_id`, `MQTT_CLIENT_ID`, `MQTT_CERT_PATH` 설정 완료
- 인증서 심링크 (`cert.pem`, `key.pem`, `ca_cert.pem`) 생성 완료

사용자에게 다음 정보를 확인한다:

| 항목 | 예시 | 필수 |
|------|------|------|
| 디바이스 IP | 192.168.219.191 | Y |
| SSH User | matterhub | Y |
| SSH Password | whatsmatter1234 | Y |

## 검증 절차

### Step 1: 최신 코드 동기화

```bash
cd ~/Desktop/matterhub && git pull origin master
```

### Step 2: 구 프로세스 정리

```bash
pkill -f "python3 app.py" 2>/dev/null
pkill -f "python3 mqtt.py" 2>/dev/null
sudo systemctl stop matterhub.service 2>/dev/null
sleep 1
ps aux | grep -E 'app.py|mqtt.py' | grep -v grep || echo "ALL_KILLED"
```

### Step 3: Flask API(app.py) 실행

```bash
cd ~/Desktop/matterhub && nohup python3 -u app.py > /tmp/matterhub-app.log 2>&1 &
sleep 3
ss -tlnp | grep 8100
```

**정상 확인:**
- 포트 8100이 LISTEN 상태
- `/tmp/matterhub-app.log`에 `Running on http://0.0.0.0:8100` 출력

**실패 시 확인:**
```bash
cat /tmp/matterhub-app.log
```

흔한 실패 원인:
| 에러 | 원인 | 해결 |
|------|------|------|
| `TypeError: stat: path should be string...not NoneType` | `.env`에 `res_file_path` 등 리소스 경로 누락 | `/device-setup` Step 3 참조하여 경로 추가 |
| `Address already in use` | 이전 프로세스가 8100 점유 중 | `pkill -f "python3 app.py"` 후 재시작 |
| WiFi watchdog 에러 | `wlan0` 없는 환경 (유선 연결) | 무시해도 됨, 정상 동작에 영향 없음 |

### Step 4: MQTT(mqtt.py) 실행 및 로그 분석

```bash
cd ~/Desktop/matterhub && timeout 40 python3 -u mqtt.py 2>&1
```

40초 동안 실행 후 자동 종료. 로그를 분석한다.

### Step 5: 로그 체크리스트

아래 항목을 순서대로 확인하고 결과를 사용자에게 보고한다:

#### 필수 성공 항목

| # | 확인 항목 | 정상 로그 | 비정상 시 |
|---|----------|----------|----------|
| 1 | matterhub_id 로드 | `matterhub_id 로드됨: whatsmatter-nipa_SN-...` | `.env`의 `matterhub_id` 확인 |
| 2 | 인증서 경로 | `cert_path=certificates cert=ok key=ok ca=ok` | 심링크 재생성 필요 |
| 3 | endpoint | `endpoint=a206qwcndl23az-ats...` | `konai` endpoint면 `.env`의 `MQTT_ENDPOINT` 수정 |
| 4 | client_id | `client_id=whatsmatter-nipa_SN-...` | `.env`의 `MQTT_CLIENT_ID` 수정 |
| 5 | MQTT 연결 | `[MQTT][CONNECT][OK] connected to broker` | 인증서/endpoint 확인 |
| 6 | 토픽 구독 | `[MQTT][SUBSCRIBE][OK]` (matterhub/* 토픽만) | 정책 확인 |
| 7 | 디바이스 상태 발행 | `[MQTT][DEVICE_STATE] 발행 완료: N개 디바이스` | 아래 트러블슈팅 참조 |
| 8 | publish 성공 | `publish_result ... status=success ... qos1` | 토픽 권한 확인 |

#### 비정상 징후 (하나라도 있으면 실패)

| 징후 | 의미 | 조치 |
|------|------|------|
| `[MQTT][SHADOW]` 로그 출력 | Shadow 코드가 제거되지 않음 | 최신 코드로 `git pull` |
| `update/delta/dev/...` 토픽 구독 | Konai 토픽 비활성화 안 됨 | 최신 코드로 `git pull` |
| `UNEXPECTED_HANGUP` 반복 | 인증서/client_id 불일치 | `/device-provision` 재실행 |
| `cert_path=konai_certificates` | 구 인증서 경로 사용 중 | `.env`의 `MQTT_CERT_PATH=certificates/` 확인 |

#### DEVICE_STATE 미발행 트러블슈팅

`[MQTT][DEVICE_STATE]` 로그가 안 나오는 경우:

1. `matterhub_id`가 비어있으면 → 프로비저닝 필요
2. `resources/devices.json`이 빈 배열 `[]`이면 → 파일 삭제: `rm resources/devices.json`
3. HA 연결 실패면 → `HA_host`, `hass_token` 확인
4. MQTT 미연결이면 → 인증서/endpoint 확인

### Step 6: 결과 보고

사용자에게 다음 형식으로 보고한다:

```
| 검증 항목          | 결과 |
|-------------------|------|
| Flask(8100) 기동   | OK / FAIL |
| MQTT 연결          | OK / FAIL |
| Shadow 로그 없음   | OK / FAIL |
| Konai 토픽 없음    | OK / FAIL |
| 연결 안정성(hangup) | OK / FAIL |
| 디바이스 상태 발행   | OK (N개) / FAIL |
| publish QoS1 성공  | OK / FAIL |
| 토픽               | matterhub/{hub_id}/state/devices |
```

모든 항목이 OK이면 사용자에게 클라우드 DynamoDB 수신 확인을 요청한다.
