---
name: device-online-update
description: "레거시 허브 업데이트 스크립트(코나이 비활성화). 오프라인→온라인 복귀 장비의 안전한 git 업데이트 + 서비스 점검. \"/device-online-update\" 또는 \"온라인 복귀 업데이트\", \"오프라인 장비 업데이트\" 시 사용."
---

# 오프라인→온라인 복귀 장비 업데이트 (레거시 허브, 코나이 비활성화)

오프라인이었다가 온라인으로 복귀한 에지서버를 최신 코드로 업데이트하고, 서비스 정상 동작을 확인한다.
Konai 토픽 잔존 여부를 검증하고 비활성화 상태를 보장한다.

## 사전 조건

| 항목 | 확인 방법 |
|------|-----------|
| 릴레이 SSH 키 | `ls /tmp/hyodol-slm-server-key.pem` (없으면 PPK→PEM 변환: `puttygen {ppk경로} -O private-openssh -o /tmp/hyodol-slm-server-key.pem && chmod 600 /tmp/hyodol-slm-server-key.pem`) |
| 릴레이 접속 | `ssh -i /tmp/hyodol-slm-server-key.pem kh-kim@4.230.8.65 "echo OK"` |
| 대상 포트 | 사용자에게 확인 (15001~15103 범위) |

PPK 원본 위치: `/Users/wm-mac-01/Downloads/06_개발자료/04_프로젝트폴더/ssh_key모음/hyodol-slm-server-key.ppk`

## 릴레이 SSH 접속 패턴

모든 장비 접속은 릴레이 경유 2-hop SSH:

```bash
RELAY_KEY="/tmp/hyodol-slm-server-key.pem"
RELAY_HOST="4.230.8.65"
RELAY_USER="kh-kim"
DEVICE_USER="hyodol"
DEVICE_KEY_ON_RELAY="/home/kh-kim/.ssh/id_s2edge"

# 단일 명령 실행
ssh -i "$RELAY_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o LogLevel=ERROR \
    "${RELAY_USER}@${RELAY_HOST}" \
    "ssh -p {PORT} -i $DEVICE_KEY_ON_RELAY -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o LogLevel=ERROR \
        ${DEVICE_USER}@localhost '{COMMAND}'"
```

**주의**: heredoc(`bash -s <<EOF`)은 일부 장비에서 실패. 짧은 단일 명령 여러 번 실행 권장.

## 절차

### Phase 1: 사전 점검 (업데이트 전)

대상 포트에 대해 아래 5가지를 순서대로 확인한다.

#### 1-1. 접속 확인

```bash
ssh ... hyodol@localhost 'echo OK'
```

#### 1-2. 앱 경로 탐색

아래 후보를 순차 시도:

```bash
for candidate in \
    /opt/matterhub/app \
    /home/hyodol/whatsmatter-hub-flask-server \
    /home/hyodol/Desktop/matterhub \
    /home/hyodol/Desktop/whatsmatter-hub-flask-server \
    /home/hyodol/matterhub; do
    ssh ... "test -d $candidate/.git && echo $candidate"
done
```

#### 1-3. 현재 상태 수집

```bash
# Git 브랜치 + 커밋
ssh ... "cd {APP_PATH} && git branch --show-current && git rev-parse --short HEAD && git log -1 --format=%cd --date=short"

# matterhub_id
ssh ... "grep ^matterhub_id= {APP_PATH}/.env 2>/dev/null | sed 's/^matterhub_id=//;s/\"//g'"

# .env 필수 변수 존재 확인
ssh ... "grep -cE '^(HA_host|hass_token|matterhub_id)=' {APP_PATH}/.env"
# 결과 3이면 정상, 미만이면 .env 보완 필요
```

#### 1-4. Konai 코드 잔존 확인

구버전 코드에는 Konai 하드코딩이 있을 수 있음. 반드시 확인:

```bash
# 엔드포인트 확인
ssh ... "grep ^ENDPOINT {APP_PATH}/providers/konai/settings.py 2>/dev/null"
# → a34vuzhubahjfj = KONAI (위험), a206qwcndl23az = MatterHub (정상)

# 벤더 토픽 구독 확인
ssh ... "grep '_append_unique_topic.*MQTT_TOPIC_SUBSCRIBE' {APP_PATH}/mqtt.py 2>/dev/null"
# → 주석(#) 있으면 정상, 없으면 Konai 토픽 활성 (위험)
```

#### 1-5. 프로세스 매니저 확인

```bash
# systemd 서비스 존재 여부
ssh ... "systemctl is-active matterhub-mqtt.service 2>/dev/null || echo missing"

# PM2 프로세스
ssh ... "pm2 list 2>/dev/null | head -5 || echo no_pm2"

# sudo NOPASSWD 가용성
ssh ... "sudo -n systemctl --version 2>/dev/null && echo sudo_ok || echo sudo_fail"
```

### Phase 2: Git 업데이트

#### 2-1. .env 백업

```bash
ssh ... "cp {APP_PATH}/.env {APP_PATH}/.env.update_bak"
```

#### 2-2. resources/ 백업

```bash
ssh ... "test -d {APP_PATH}/resources && cp -a {APP_PATH}/resources /tmp/mh_resources_bak"
```

#### 2-3. git fetch + reset

```bash
ssh ... "cd {APP_PATH} && git fetch origin master 2>&1 && git reset --hard origin/master 2>&1"
```

#### 2-4. .env 복원

```bash
ssh ... "cp {APP_PATH}/.env.update_bak {APP_PATH}/.env"
```

#### 2-5. resources/ 복원

```bash
ssh ... "test -d /tmp/mh_resources_bak && cp -a /tmp/mh_resources_bak/. {APP_PATH}/resources/ && rm -rf /tmp/mh_resources_bak"
```

#### 2-6. 커밋 확인

```bash
ssh ... "cd {APP_PATH} && git rev-parse --short HEAD"
# → 최신 커밋 해시 확인
```

### Phase 3: .env 보완 (필요 시)

`.env`에 필수 변수 3개(`HA_host`, `hass_token`, `matterhub_id`) 중 하나라도 없으면 보완한다.

#### matterhub_id 없는 경우

재프로비저닝으로 새 ID 발급:

```bash
ssh ... "cd {APP_PATH} && python3 -c \"
from mqtt_pkg.provisioning import AWSProvisioningClient
c = AWSProvisioningClient()
result = c.provision_device()
print(f'결과: {result}')
\""
```

#### HA_host / hass_token 없는 경우

1. HA 실행 확인: `curl -s http://127.0.0.1:8123/api/` → `401: Unauthorized` = HA 정상
2. HA auth 파일에서 long-lived token 확인:

```bash
ssh ... "docker exec homeassistant_core cat /config/.storage/auth" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); [print(t['id'],t['token_type']) for t in d['data']['refresh_tokens']]"
```

3. JWT 재구성 (Docker 내부):

```bash
ssh ... "docker exec homeassistant_core python3 -c \"
import jwt, json
with open('/config/.storage/auth') as f:
    data = json.load(f)
for t in data['data']['refresh_tokens']:
    if t['token_type'] == 'long_lived_access_token':
        token = jwt.encode({'iss': t['id'], 'iat': 20250716, 'exp': 2068019630}, t['jwt_key'], algorithm='HS256')
        print(token)
        break
\""
```

4. `.env`에 추가:

```bash
ssh ... "cat >> {APP_PATH}/.env << 'EOF'
HA_host=\"http://127.0.0.1:8123\"
hass_token={TOKEN}
res_file_path=\"resources\"
schedules_file_path=\"resources/schedule.json\"
rules_file_path=\"resources/rules.json\"
rooms_file_path=\"resources/rooms.json\"
devices_file_path=\"resources/devices.json\"
cert_file_path=\"cert\"
notifications_file_path=\"resources/notifications.json\"
EOF"
```

### Phase 4: sudoers 설정 (필요 시)

sudo가 안 되면 systemd 마이그레이션 불가. Phase 1-5에서 `sudo_fail`이었으면:

```bash
ssh ... "echo tech8123 | sudo -S bash -c 'echo \"hyodol ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /usr/bin/install, /usr/bin/systemd-run\" > /etc/sudoers.d/matterhub-update && chmod 0440 /etc/sudoers.d/matterhub-update'"

# 검증
ssh ... "sudo -n systemctl --version 2>/dev/null && echo sudo_ok || echo sudo_fail"
```

### Phase 5: 서비스 재시작

`update_server.sh --restart-only` 실행. 이 스크립트가 자동으로:
- 프로세스 매니저 감지 (systemd/PM2/legacy)
- systemd 유닛 렌더링 + 설치 (필요 시)
- PM2→systemd 마이그레이션 (필요 시)
- 서비스 재시작 + healthcheck (30초 대기, 2개 이상 active)
- healthcheck 실패 시 자동 롤백

```bash
ssh ... "cd {APP_PATH} && bash device_config/update_server.sh master false update_{PORT} {HUB_ID} --restart-only 2>&1 | tail -10"
```

**성공 판별**: 출력에 `healthcheck 통과` + `완료` 포함

### Phase 6: 사후 검증 (업데이트 후)

#### 6-1. 서비스 상태

```bash
ssh ... "systemctl is-active matterhub-api.service matterhub-mqtt.service matterhub-rule-engine.service matterhub-notifier.service matterhub-update-agent.service"
# → 5줄 모두 "active"
```

#### 6-2. MQTT 구독 토픽 확인 (코나이 비활성화 검증)

```bash
ssh ... "journalctl -u matterhub-mqtt --no-pager -n 30 | grep SUBSCRIBE"
```

확인 사항:
- `[MQTT][SUBSCRIBE] complete total=N success=N failed=0` (failed=0)
- 토픽이 `matterhub/` 프리픽스만 있어야 함
- `k3O6TL`, `update/delta/dev`, `update/reported/dev` 토픽이 **없어야** 함 (Konai 토픽)

#### 6-3. MQTT 연결 확인

```bash
ssh ... "journalctl -u matterhub-mqtt --no-pager -n 30 | grep CONNECT"
# → [MQTT][CONNECT][OK] connected to broker
```

#### 6-4. API 동작 확인

```bash
ssh ... "curl -s -o /dev/null -w '%{http_code}' http://localhost:8100/local/api/states"
# → 200 = 정상 (HA 연동 OK)
# → 502 = HA 연결 실패 (HA_host/hass_token 확인)
# → 000 = API 서비스 미기동 (systemctl restart matterhub-api)
```

#### 6-5. MQTT 로그 오류 확인

```bash
ssh ... "journalctl -u matterhub-mqtt --no-pager -n 50 | grep -ciE 'ERROR|FAIL|실패|Traceback|Exception'"
# → 0이면 정상
```

#### 6-6. Konai 코드 최종 확인 (비활성화 검증)

```bash
ssh ... "grep ^ENDPOINT {APP_PATH}/providers/konai/settings.py"
# → a206qwcndl23az (MatterHub) 확인

ssh ... "grep _append_unique_topic.*MQTT_TOPIC {APP_PATH}/mqtt.py | head -2"
# → 모두 주석(#) 처리 확인
```

## 일괄 처리 스크립트

여러 대를 한번에 처리할 때:

```bash
# Git 업데이트 (Phase 2)
RELAY_KEY=/tmp/hyodol-slm-server-key.pem PORT_START={시작} PORT_END={끝} \
    bash "device_config/에지서버_일괄_git업데이트.sh"

# 서비스 상태 점검 (Phase 6)
RELAY_KEY=/tmp/hyodol-slm-server-key.pem PORT_START={시작} PORT_END={끝} \
    bash "device_config/에지서버_서비스상태_일괄점검.sh"

# Konai 코드 탐지
RELAY_KEY=/tmp/hyodol-slm-server-key.pem PORT_START={시작} PORT_END={끝} \
    bash "device_config/코나이토픽_코드기반_일괄조사.sh"
```

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `ssh: Connection refused` | 리버스터널 끊김 | 장비 재부팅 또는 릴레이 터널 재설정 대기 |
| `api=inactive missing` | systemd 유닛 미설치 (PM2 기반 장비) | Phase 4 sudoers → Phase 5 update_server.sh |
| `BOOTSTRAP 실패: localhost:8100 Connection refused` | 업데이트 후 서비스 미재시작 | Phase 5 `--restart-only` 실행 (일시적이 아님, 반드시 재시작 필요) |
| `HA_host=None` | .env에 HA_host 미설정 | Phase 3 .env 보완 |
| `matterhub_id 없음` | 프로비저닝 미완료 | Phase 3 재프로비저닝 |
| `KONAI endpoint` | 구버전 코드 하드코딩 | Phase 2 git reset --hard로 최신 코드 적용 |
| `healthcheck 실패 → 롤백` | 서비스 기동 불가 | 로그 확인: `journalctl -u matterhub-mqtt -n 50` |
| `pm2 restart만 되고 systemd 안 됨` | sudo NOPASSWD 미설정 | Phase 4 sudoers 설정 후 재시도 |
| `git pull conflict` | 로컬 변경사항 충돌 | `git reset --hard origin/master` (강제, .env는 백업/복원으로 보호됨) |
| `venv 생성 실패` | `python3-venv` 미설치 | update_server.sh가 자동으로 부서진 venv 삭제 후 시스템 Python 사용 |

## 완료 후 안내

1. 업데이트 완료된 장비의 **포트, matterhub_id, 커밋, 서비스 상태**를 표로 정리하여 사용자에게 보고
2. Konai 토픽 발행이 없는지 최종 확인 (6-2, 6-6)
3. 필요 시 `/device-verify`로 개별 장비 심층 검증
