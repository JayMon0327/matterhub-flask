---
name: device-manual-patch
description: MatterHub 수동 패치 전략. MQTT 원격 업데이트 불가 장비에 릴레이 SSH 경유로 수동 패치 + .env 복구 + 장애 대응을 수행한다. "/device-manual-patch" 또는 "수동 패치", "manual patch" 시 사용.
---

# MatterHub 수동 패치 (MQTT 미실행 장비)

MQTT 원격 업데이트가 불가능한 장비에 릴레이 SSH 경유로 수동 패치를 수행하는 스킬.
`bulk_initial_deploy.sh`로 1차 배포 후, 실패 장비별 개별 진단 및 .env 복구까지 포함한다.

## 사전 조건

사용자에게 다음 정보를 확인한다:

| 항목 | 기본값 | 필수 |
|------|--------|------|
| 릴레이 호스트 | 4.230.8.65 | Y |
| 릴레이 SSH 유저 | kh-kim | Y |
| 릴레이 SSH 키 경로 (Mac) | /tmp/hyodol-slm-server-key.pem | Y |
| 디바이스 SSH 유저 | hyodol | Y |
| 디바이스 sudo 비밀번호 | tech8123 | Y |
| 대상 포트 목록 | - | Y |
| 배포 브랜치 | master | N |

## 절차

### Step 1: 포트 파일 생성 + dry-run

대상 포트를 `/tmp/patch_ports.txt`에 기록하고 접속 테스트한다:

```bash
# 포트 파일 생성 (예시)
cat > /tmp/patch_ports.txt << 'EOF'
15011
15037
EOF

# dry-run: 접속 가능 여부만 확인
PORTS_FILE=/tmp/patch_ports.txt BATCH_SIZE=5 \
  bash device_config/bulk_initial_deploy.sh --dry-run
```

### Step 2: 본 배포 (BATCH_SIZE=3)

```bash
PORTS_FILE=/tmp/patch_ports.txt BATCH_SIZE=3 \
  bash device_config/bulk_initial_deploy.sh
```

결과 CSV를 확인하고, 성공/실패 장비를 분류한다.

### Step 3: 실패 장비 진단 + 대응

실패 유형별 대응 플로우:

| 실패 유형 | 진단 명령 | 대응 |
|-----------|----------|------|
| `FAIL_GIT` + `No space left` | `df -h /` + `ls -lah /tmp/.*` | `/tmp` nvidia 숨김 디렉토리 삭제 + `/var/tmp` 정리 + journal vacuum → 재배포 |
| `matterhub_id=unknown` | `.env` 파일 크기 확인 (0바이트 = 유실) | PM2 dump 복구 (Step 4) |
| `FAIL_UPDATE` + pyc 에러 | `journalctl -u matterhub-api -n 20` | `find . -name __pycache__ -exec rm -rf {} +` → 재시작 |
| API 502 | HA Docker 상태 확인 → `curl -H "Authorization: Bearer {token}" http://127.0.0.1:8123/api/` | 401이면 hass_token 재발급 (Step 4-1) |

**디스크 정리 명령 (nvidia 악성코드 잔여):**
```bash
find /tmp -maxdepth 1 -name '.*' -user nvidia -type d -exec rm -rf {} +
sudo find /var/tmp -maxdepth 1 -name '.*' -user nvidia -type d -exec rm -rf {} +
sudo journalctl --vacuum-size=100M
```

### Step 4: .env 복구 (matterhub_id=unknown 시)

**검색 우선순위:**
1. `~/.pm2/dump.pm2.bak` — `grep "matterhub_id\|hass_token"` (가장 신뢰)
2. `~/.pm2/dump.pm2` — 동일 검색
3. `~/.pm2/logs/` — `grep -r "matterhub_id를 .env 파일에 저장" *.log`
4. `~/.bash_history` — `grep "hass_token\|matterhub_id"`
5. 모두 없으면 → 사용자에게 보고, 프로비저닝(`/device-provision`) 필요

**복구한 값으로 .env 작성:**

릴레이 경유 SSH로 디바이스에 접속하여:

```bash
cd ~/whatsmatter-hub-flask-server

cat > .env << 'ENVEOF'
HA_host = "http://127.0.0.1:8123"
res_file_path = "resources"
schedules_file_path = "resources/schedule.json"
rules_file_path = "resources/rules.json"
rooms_file_path = "resources/rooms.json"
devices_file_path = "resources/devices.json"
cert_file_path = "cert"
notifications_file_path = "resources/notifications.json"
hass_token={복구한_토큰}
matterhub_id={복구한_ID}
MATTERHUB_REGION="gwangwon"
SUBSCRIBE_MATTERHUB_TOPICS="1"
MATTERHUB_VENDOR="konai"
ENVEOF

sudo systemctl restart matterhub-mqtt matterhub-api
```

### Step 4-1: hass_token 재발급 (API 502 + HA 401 시)

복구한 토큰이 HA에서 revoke된 경우, HA auth flow로 long-lived token을 재발급한다:

```bash
# 1. 로그인 플로우 시작
FLOW=$(curl -s -X POST http://127.0.0.1:8123/auth/login_flow \
  -H "Content-Type: application/json" \
  -d '{"client_id":"http://localhost:8100/","handler":["homeassistant",null],"redirect_uri":"http://localhost:8100/"}')
FLOW_ID=$(echo $FLOW | python3 -c "import sys,json; print(json.load(sys.stdin)['flow_id'])")

# 2. 로그인 (HA 계정: whatsmatter / whatsmatter1234)
RESULT=$(curl -s -X POST http://127.0.0.1:8123/auth/login_flow/$FLOW_ID \
  -H "Content-Type: application/json" \
  -d '{"client_id":"http://localhost:8100/","username":"whatsmatter","password":"whatsmatter1234"}')
CODE=$(echo $RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['result'])")

# 3. 단기 토큰 발급
TOKEN_RESP=$(curl -s -X POST http://127.0.0.1:8123/auth/token \
  -d "grant_type=authorization_code&code=$CODE&client_id=http://localhost:8100/")
SHORT_TOKEN=$(echo $TOKEN_RESP | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 4. WebSocket으로 long-lived token 발급 (10년)
python3 -c "
import json, websocket
ws = websocket.create_connection('ws://127.0.0.1:8123/api/websocket')
ws.recv()
ws.send(json.dumps({'type':'auth','access_token':'$SHORT_TOKEN'}))
ws.recv()
ws.send(json.dumps({'id':1,'type':'auth/long_lived_access_token','client_name':'MatterHub','lifespan':3650}))
r = json.loads(ws.recv())
print(r['result'])
ws.close()
"
```

발급된 토큰을 `.env`의 `hass_token=`에 설정 후 서비스 재시작.

### Step 5: 최종 검증

4대 모두 아래를 확인:

```bash
# MQTT 연결 + 토픽 구독
journalctl -u matterhub-mqtt -n 10
# → CONNECT OK + SUBSCRIBE 5/5 확인

# API 상태
curl -s -o /dev/null -w "%{http_code}" http://localhost:8100/local/api/states
# → 200 확인

# matterhub_id 유효 확인
grep matterhub_id .env
# → "unknown"이 아닌 유효한 값
```

### Step 6: 완료보고서

`docs/reported/` 하위에 작업 결과를 기록한다. 기존 보고서가 있으면 업데이트, 없으면 신규 작성.

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 디바이스 접속 불가 | 릴레이 터널 끊김 | 릴레이에서 `ss -tlnp \| grep {port}` 확인 |
| git pull 실패 | DNS 불안정 (Wi-Fi) | 디바이스에서 `ping github.com` 확인, 수동 재시도 |
| 디스크 100% + nvidia 숨김 폴더 | 채굴 악성코드 잔여 | `find /tmp -maxdepth 1 -name '.*' -user nvidia -type d -exec rm -rf {} +` |
| .env 0바이트 / matterhub_id 없음 | .env 유실 | PM2 dump/로그/history에서 복구 (Step 4) |
| EOFError: marshal data too short | pyc 캐시 손상 | `find . -name __pycache__ -exec rm -rf {} +` |
| API 60000+ restart | pyc 손상으로 무한 크래시 | pyc 정리 후 재시작 |
| sudoers 설정 실패 | 비밀번호 틀림 | SUDO_PASS 확인 |
| systemd 불안정 | venv 없음 / 깨짐 | 개별 디바이스에서 `/device-migrate-systemd` 실행 |
| API 502 + HA 401 | hass_token 만료/revoke | Step 4-1로 long-lived token 재발급 |
| API 502 + HA Docker down | HA Docker 비정상 | `docker ps` 확인, `docker restart homeassistant_core` |

## 완료 후 안내

수동 패치 완료 후:
1. 모든 대상 장비가 MQTT 원격 업데이트 가능 상태
2. 이후 업데이트는 `/device-remote-update`로 MQTT 토픽 전송
3. 토픽: `matterhub/update/all` (전체) 또는 `matterhub/update/specific/{hub_id}` (개별)
