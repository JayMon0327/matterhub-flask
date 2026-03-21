---
name: device-setup
description: MatterHub 디바이스 초기 설치. 새 장비에 SSH 접속하여 repo clone, .env 생성, 인증서 심링크, 리소스 디렉토리를 설정한다. "/device-setup" 또는 "디바이스 설치", "새 장비 세팅" 시 사용.
---

# MatterHub 디바이스 초기 설치

새 디바이스(Raspberry Pi / Ubuntu)에 MatterHub를 처음 설치하는 스킬.

## 사전 조건

사용자에게 다음 정보를 확인한다 (모르면 물어본다):

| 항목 | 예시 | 필수 |
|------|------|------|
| 디바이스 IP | 192.168.219.191 | Y |
| SSH User | matterhub | Y |
| SSH Password | whatsmatter1234 | Y |
| HA 토큰 | eyJhbG... (장문) | Y |
| HA 포트 | 8123 (기본값) | N |
| GitHub repo URL | https://github.com/JayMon0327/matterhub-flask.git | N (기본값 사용) |

## 설치 절차

### Step 1: SSH 접속 확인

```bash
sshpass -p '{password}' ssh -o StrictHostKeyChecking=no {user}@{ip} "echo 'SSH OK' && python3 --version"
```

접속 실패 시 IP, 비밀번호를 사용자에게 재확인한다.

### Step 2: 기존 코드 정리 + Clone

기존 `~/Desktop/matterhub` 디렉토리가 있으면 삭제 후 새로 clone한다.

```bash
rm -rf ~/Desktop/matterhub
git clone https://github.com/JayMon0327/matterhub-flask.git ~/Desktop/matterhub
```

**주의:** 기존 remote가 `nano-2-ly/whatsmatter-hub-flask-server.git` 등 구버전이면 반드시 삭제 후 새 repo로 clone한다.

clone 후 `git log --oneline -3`으로 최신 커밋 확인.

### Step 3: .env 생성

`~/Desktop/matterhub/.env` 파일을 생성한다:

```bash
cat <<'EOF' > ~/Desktop/matterhub/.env
HA_host="http://localhost:8123"
hass_token="{사용자가 제공한 HA 토큰}"
matterhub_id=""
MATTERHUB_VENDOR="konai"
SUBSCRIBE_MATTERHUB_TOPICS="1"
MATTERHUB_AUTO_PROVISION="1"
MQTT_CERT_PATH="certificates/"
MQTT_ENDPOINT="a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com"
MQTT_CLIENT_ID=""
res_file_path="resources"
cert_file_path="certificates"
schedules_file_path="resources/schedule.json"
rules_file_path="resources/rules.json"
rooms_file_path="resources/rooms.json"
devices_file_path="resources/devices.json"
notifications_file_path="resources/notifications.json"
EOF
```

**중요:**
- `MQTT_CERT_PATH`는 반드시 `certificates/`로 설정 (konai_certificates 아님)
- `MQTT_ENDPOINT`는 프로비저닝 endpoint 사용
- `MQTT_CLIENT_ID`는 프로비저닝 후 matterhub_id와 동일하게 설정

### Step 4: 인증서 심링크 생성

`certificates/` 디렉토리에 runtime이 기대하는 파일명으로 심링크를 만든다:

```bash
cd ~/Desktop/matterhub/certificates/
ln -sf device.pem.crt cert.pem
ln -sf private.pem.key key.pem
ln -sf AmazonRootCA1.pem ca_cert.pem
```

**파일명 매핑:**
| 원본 (프로비저닝 발급) | 심링크 (runtime 기대) |
|------------------------|----------------------|
| device.pem.crt | cert.pem |
| private.pem.key | key.pem |
| AmazonRootCA1.pem | ca_cert.pem |

**주의:** 프로비저닝 전에는 `device.pem.crt`, `private.pem.key`가 없다. 이 경우 프로비저닝(`/device-provision`)을 먼저 실행해야 한다.

Claim 인증서 3개가 `certificates/`에 있는지 확인:
- `AmazonRootCA1.pem`
- `whatsmatter_nipa_claim_cert.cert.pem`
- `whatsmatter_nipa_claim_cert.private.key`

없으면 사용자에게 인증서 파일 위치를 확인한다.

### Step 5: 리소스 디렉토리 확인

```bash
mkdir -p ~/Desktop/matterhub/resources
```

`resources/devices.json`이 빈 배열 `[]`이면 삭제한다 (전체 엔티티 발행을 위해):

```bash
[ "$(cat resources/devices.json 2>/dev/null)" = "[]" ] && rm resources/devices.json
```

### Step 6: 구 프로세스 정리

기존 matterhub 프로세스가 있으면 정리한다:

```bash
# systemd 서비스 확인 및 중지
sudo systemctl stop matterhub.service 2>/dev/null

# PM2 확인 (다른 프로젝트와 혼용 가능하므로 matterhub 관련만 확인)
pm2 list 2>/dev/null

# 잔존 프로세스 kill
pkill -f "python3 app.py" 2>/dev/null
pkill -f "python3 mqtt.py" 2>/dev/null
```

## 완료 후 안내

설치 완료 후 사용자에게 다음 단계를 안내한다:
1. `/device-provision` — AWS IoT 프로비저닝 (matterhub_id 발급)
2. `/device-verify` — 서비스 실행 및 검증
