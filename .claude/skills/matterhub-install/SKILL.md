---
name: matterhub-install
description: MatterHub Flask 서버를 .deb 패키지로 빌드하여 라즈베리파이에 설치. 스킬 1(플랫폼) 완료 후 스킬 2로 실행.
disable-model-invocation: true
argument-hint: "[장비IP]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# 스킬 2: MatterHub Flask 서버 패키징 설치

MatterHub Flask 서버를 .deb로 빌드하여 라즈베리파이에 설치한다. 전체 절차의 상세 내용은 [플레이북](../../../docs/operations/raspi-server-setup-playbook.md)의 "스킬 2" 섹션 참조.

## 전제 조건

- 스킬 1(플랫폼 설치) 완료
- Mac에 `dpkg` 설치됨 (`brew install dpkg`)

## 인자

- `$ARGUMENTS`: 장비 IP (예: 192.168.1.96)
- SSH user: whatsmatter / PW: mat458496ad!
- 프로젝트: /Users/wm-mac-01/Documents/matterhub-flask/matterhub-flask/

## 실행 절차

### 2-1. .deb 빌드 (Mac에서)

```bash
cd /Users/wm-mac-01/Documents/matterhub-flask/matterhub-flask
bash device_config/build_matterhub_deb.sh
ls -la dist/matterhub_*_arm64.deb
```

> `--mode source` (기본값) 사용. Mac/Pi Python 버전 차이로 `--mode pyc` 쓰지 않음.

### 2-2. 전송 및 설치

```bash
# Mac에서
scp dist/matterhub_*_arm64.deb whatsmatter@<장비IP>:/tmp/

# Pi에서
sudo dpkg -i /tmp/matterhub_*.deb
```

> postinst의 pip install이 실패하지만 파일은 `/opt/matterhub/app/`에 정상 설치됨.

### 2-3. venv 구성

서버 신규 세팅 시 직접 생성:

```bash
sudo python3 -m venv /opt/matterhub/venv
sudo /opt/matterhub/venv/bin/pip install --upgrade pip
sudo /opt/matterhub/venv/bin/pip install -r /opt/matterhub/app/requirements.txt
```

기존 Desktop 버전에서 전환 시:

```bash
sudo cp -a ~/Desktop/matterhub/venv /opt/matterhub/venv
sudo chown -R whatsmatter:whatsmatter /opt/matterhub/venv
```

### 2-4. postinst 수동 완료

```bash
# .pyc 컴파일
sudo /opt/matterhub/venv/bin/python -m compileall -q -b /opt/matterhub/app

# .py 삭제 (__init__.py 유지)
sudo find /opt/matterhub/app -type f -name '*.py' ! -name '__init__.py' -delete

# __pycache__ + macOS ._파일 정리
sudo find /opt/matterhub/app -type d -name '__pycache__' -prune -exec rm -rf {} +
sudo find /opt/matterhub/app -name "._*" -delete

# 런처 확장자 수정
for launcher in /opt/matterhub/bin/matterhub-*; do
  [ -f "$launcher" ] && sudo sed -i 's/\.py"/\.pyc"/g' "$launcher"
done
```

### 2-5. dpkg 상태 수복

```bash
# postinst pip 라인 스킵
sudo sed -i '30,31s|.*|echo SKIP #|' /var/lib/dpkg/info/matterhub.postinst

# depends 수정 (서버 버전용)
sudo sed -i '/^Package: matterhub$/,/^$/s/^Depends:.*/Depends: python3, python3-venv, openssh-client, openssh-server/' /var/lib/dpkg/status

# 수복
sudo dpkg --configure matterhub

# 확인: ii 상태
dpkg -l matterhub | grep matterhub
```

### 2-6. .env 설정 (핵심 - 순환 symlink 방지!)

```bash
# 깨진 symlink 제거
sudo rm -f /etc/matterhub/matterhub.env /opt/matterhub/app/.env

# 실제 파일 생성
sudo tee /opt/matterhub/app/.env > /dev/null <<'EOF'
HA_host = "http://127.0.0.1:8123"
res_file_path = "resources"
schedules_file_path = "resources/schedule.json"
rules_file_path = "resources/rules.json"
rooms_file_path = "resources/rooms.json"
devices_file_path = "resources/devices.json"
cert_file_path = "cert"
notifications_file_path = "resources/notifications.json"
SUPPORT_TUNNEL_ENABLED=1
SUPPORT_TUNNEL_COMMAND=ssh
SUPPORT_TUNNEL_PORT=443
SUPPORT_TUNNEL_LOCAL_PORT=22
SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS=127.0.0.1
SUPPORT_TUNNEL_HOST=3.38.126.167
SUPPORT_TUNNEL_USER=whatsmatter
MATTERHUB_AUTO_PROVISION=1
WIFI_AUTO_AP_ON_BOOT=0
WIFI_AUTO_AP_ON_DISCONNECT=0
WIFI_AP_AUTO_RECONNECT_ENABLED=0
MQTT_CERT_PATH="certificates/"
MQTT_ENDPOINT="a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com"
MQTT_CLIENT_ID=""
SUBSCRIBE_MATTERHUB_TOPICS="1"
UPDATE_AGENT_PROJECT_ROOT=/opt/matterhub
SUPPORT_TUNNEL_PRIVATE_KEY_PATH=/home/whatsmatter/.ssh/id_ed25519
SUPPORT_TUNNEL_KNOWN_HOSTS_PATH=/home/whatsmatter/.ssh/known_hosts
SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING=0
SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL=30
SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX=3
SUPPORT_TUNNEL_AUTOSSH_GATETIME=0
SUPPORT_TUNNEL_DEVICE_USER=whatsmatter
SUPPORT_TUNNEL_RELAY_OPERATOR_USER=ec2-user
SUPPORT_TUNNEL_PREFLIGHT_TCP_CHECK=0
EOF

# symlink: /etc → /opt 방향 (단방향!)
sudo ln -sf /opt/matterhub/app/.env /etc/matterhub/matterhub.env
```

장비별 추가:

```bash
echo 'matterhub_id="whatsmatter-nipa_SN-<시리얼>"' | sudo tee -a /opt/matterhub/app/.env
echo 'SUPPORT_TUNNEL_REMOTE_PORT=<포트번호>' | sudo tee -a /opt/matterhub/app/.env
```

### 2-7. 소유권 수정 (핵심! — 반드시 실행)

> **주의**: postinst가 `matterhub` 시스템유저로 파일/디렉토리를 생성한다. 아래 3가지를 반드시 모두 실행해야 한다. 하나라도 빠지면 서비스 시작 후 즉시 Permission denied로 실패한다.

```bash
# 1) app 디렉토리 — 빠지면 resources/ PermissionError
sudo chown -R whatsmatter:whatsmatter /opt/matterhub/app/

# 2) .ssh 디렉토리 전체 — 빠지면 tunnel이 키 읽기 실패 (Permission denied: Identity file not accessible)
#    dpkg install 후 .ssh/가 root:root 또는 matterhub:matterhub로 생성되므로 반드시 수정
sudo chown -R whatsmatter:whatsmatter /home/whatsmatter/.ssh/
chmod 700 /home/whatsmatter/.ssh/
chmod 600 /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519 2>/dev/null || true
chmod 644 /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub 2>/dev/null || true

# 3) .env symlink — 빠지면 tunnel이 .env Permission denied
sudo chown whatsmatter:whatsmatter /etc/matterhub/matterhub.env 2>/dev/null || true
```

> **확인**: `ls -la /home/whatsmatter/.ssh/` 실행 시 모든 파일이 `whatsmatter whatsmatter` 소유여야 한다. `root:root` 또는 `matterhub:matterhub`이면 위 명령 재실행.

### 2-8. systemd 서비스 설정

```bash
sudo cp /usr/lib/systemd/system/matterhub-*.service /etc/systemd/system/
sudo sed -i 's/User=matterhub/User=whatsmatter/g; s/Group=matterhub/Group=whatsmatter/g' \
  /etc/systemd/system/matterhub-*.service
sudo systemctl daemon-reload
sudo systemctl enable --now \
  matterhub-api.service \
  matterhub-mqtt.service \
  matterhub-rule-engine.service \
  matterhub-notifier.service \
  matterhub-update-agent.service
```

### 2-9. AWS IoT 프로비저닝 + 인증서 심링크

Claim 인증서(`certificates/` 디렉토리)를 사용하여 AWS IoT Thing을 등록하고 matterhub_id를 발급받는다.

```bash
# Pi에서
cd /opt/matterhub/app

# 프로비저닝 실행 (Claim 인증서로 Thing 등록 + 디바이스 인증서 발급)
/opt/matterhub/venv/bin/python -u -c "
from mqtt_pkg.provisioning import AWSProvisioningClient
client = AWSProvisioningClient()
has_cert, cert_file, key_file = client.check_certificate()
print(f'기존 인증서: {has_cert}, cert={cert_file}, key={key_file}')
if not has_cert:
    print('프로비저닝 시작...')
    result = client.provision_device()
    print(f'결과: {result}')
else:
    print('이미 프로비저닝된 인증서 존재')
"
```

성공 시 `✅ [PROVISION] matterhub_id 발급 완료: whatsmatter-nipa_SN-XXXXXXXXXX` 출력.
`matterhub_id`는 자동으로 `.env`에 저장됨.

#### 인증서 심링크 생성

runtime이 기대하는 파일명(`cert.pem`, `key.pem`, `ca_cert.pem`)으로 심링크를 만든다:

```bash
cd /opt/matterhub/app/certificates/
sudo ln -sf device.pem.crt cert.pem
sudo ln -sf private.pem.key key.pem
sudo ln -sf AmazonRootCA1.pem ca_cert.pem
```

> **주의:** `konai_certificates/`는 사용하지 않는다. 반드시 `certificates/`를 사용.

#### .env에 MQTT_CLIENT_ID 설정

프로비저닝으로 발급된 `matterhub_id`를 `MQTT_CLIENT_ID`에도 설정:

```bash
# 발급된 ID 확인
grep matterhub_id /opt/matterhub/app/.env

# MQTT_CLIENT_ID 설정 (matterhub_id와 동일 값)
sudo sed -i 's/^MQTT_CLIENT_ID=""/MQTT_CLIENT_ID="<발급된_matterhub_id>"/' /opt/matterhub/app/.env
```

#### 프로비저닝 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `인증서 발급 실패: 응답 없음` | Claim 인증서 만료/IoT 정책 문제 | AWS 콘솔에서 Claim 인증서 확인 |
| `사물 등록 거부됨` | 템플릿명 불일치 | `AWS_PROVISION_TEMPLATE_NAME` 확인 |
| `UNEXPECTED_HANGUP` 반복 | `konai_certificates` 사용 중 | `MQTT_CERT_PATH=certificates/` + 심링크 확인 |
| `MQTT_CLIENT_ID` 불일치 | 프로비저닝 ID와 다른 client_id | `MQTT_CLIENT_ID`를 matterhub_id와 동일하게 |

### 2-10. HA 토큰

1. 브라우저에서 `http://<장비IP>:8123` → 사용자 프로필 → 보안 → 장기 액세스 토큰 생성
2. `.env`에 추가:

```bash
echo 'hass_token=<토큰>' | sudo tee -a /opt/matterhub/app/.env
sudo systemctl restart matterhub-api matterhub-mqtt
```

### 2-11. 리버스 SSH 터널 (3단계: Pi 설정 → relay 등록 → relay→Pi 키 등록)

> **주의**: 이 3단계를 모두 완료해야 터널이 동작한다. 하나라도 빠지면 서비스는 active이지만 실제로는 `Permission denied (publickey)`로 실패.

#### 2-11a. Pi에서 터널 설정 + 서비스 시작

```bash
# Pi에서 (SSH 접속 후)
sudo bash /opt/matterhub/device_config/setup_support_tunnel.sh \
  --host 3.38.126.167 --user whatsmatter --port 443 \
  --remote-port <장비별_포트> --command ssh \
  --run-user whatsmatter --device-user whatsmatter \
  --relay-operator-user ec2-user \
  --env-file /etc/matterhub/matterhub.env \
  --skip-install-unit --enable-now
```

키 확인 (전용 키가 생성되었으면 그것을, 아니면 기본 키 사용):

```bash
ls /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub 2>/dev/null \
  || ls /home/whatsmatter/.ssh/id_ed25519.pub
```

#### 2-11b. Mac에서 relay에 Pi 공개키 등록

> **주의**: `register_hub_on_relay.sh`는 relay의 `/home/whatsmatter/.ssh/authorized_keys`에 쓰기 권한이 없어서 키 등록이 **항상 실패**한다 (`grep: Permission denied`). 스크립트 실행 후 반드시 수동 등록 단계를 추가로 실행해야 한다.

```bash
# Mac에서 - Pi 공개키 가져오기 (expect 사용, 비밀번호 ! 때문에 sshpass 불가)
expect -c "
spawn scp -o StrictHostKeyChecking=no whatsmatter@<장비IP>:/home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub /tmp/hub_tunnel_key.pub
expect \"password:\" { send \"mat458496ad!\r\" }
expect eof
"

# hubs.map 등록 (키 등록은 실패해도 맵 등록은 됨)
cd /Users/wm-mac-01/Documents/matterhub-flask/matterhub-flask
bash device_config/register_hub_on_relay.sh \
  --relay-host 3.38.126.167 --relay-port 443 --relay-user ec2-user \
  --relay-key ~/.ssh/matterhub-relay-operator-key.pem \
  --hub-id "<matterhub_id>" --remote-port <장비별_포트> \
  --hub-pubkey /tmp/hub_tunnel_key.pub --device-user whatsmatter

# ★ 필수: relay authorized_keys에 수동 등록 (스크립트가 못하는 부분)
HUB_PUBKEY=$(cat /tmp/hub_tunnel_key.pub)
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 -o StrictHostKeyChecking=no ec2-user@3.38.126.167 \
  "echo 'restrict,port-forwarding,permitlisten=\"127.0.0.1:<장비별_포트>\" ${HUB_PUBKEY}' | sudo tee -a /home/whatsmatter/.ssh/authorized_keys"
```

#### 2-11c. Pi에 relay hub-access 공개키 등록 (필수!)

relay에서 `j <hub_id>`로 Pi에 접속하려면, relay의 hub-access 공개키가 Pi의 `authorized_keys`에 있어야 한다.
**이 단계를 빠뜨리면 relay→Pi 접속이 Permission denied로 실패한다.**

```bash
# Mac에서 한 줄로 실행
RELAY_PUBKEY=$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 -o StrictHostKeyChecking=no ec2-user@3.38.126.167 "cat /home/ec2-user/.ssh/hub_access_ed25519.pub")
ssh whatsmatter@<장비IP> "mkdir -p ~/.ssh && echo '${RELAY_PUBKEY}' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```

또는 Pi에서 직접:

```bash
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILz8d991jif0znz0/mAT0bNiV5zbVTFXHjMvYmbcEVHJ relay-hub-access' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

#### 2-11d. 터널 재시작 + 검증

```bash
# Pi에서
sudo systemctl restart matterhub-support-tunnel
sudo journalctl -u matterhub-support-tunnel --no-pager -n 10
# Permission denied 없이 attempt=1 이후 에러 없으면 성공
```

```bash
# Mac에서 relay 경유 접속
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
j <matterhub_id>
# Pi 셸 진입 성공
```

### 2-12. 방화벽

```bash
sudo ufw allow 8100/tcp
sudo ufw allow 8123/tcp
```

### 2-13. 검증

```bash
# 서비스 6개 active
for svc in api mqtt rule-engine notifier update-agent support-tunnel; do
  echo -n "matterhub-${svc}: "; systemctl is-active matterhub-${svc}.service
done

# API 응답
curl -s http://localhost:8100/local/api/states | head -50

# MQTT 연결 + 디바이스 상태 발행 확인
sudo journalctl -u matterhub-mqtt --no-pager -n 50 | grep -E "SUBSCRIBE|CONNECT|DEVICE_STATE|SHADOW|publish_result"
# 정상: [MQTT][CONNECT][OK], [MQTT][DEVICE_STATE] 발행 완료, publish_result status=success qos1
# 비정상: [MQTT][SHADOW] 로그 출력, UNEXPECTED_HANGUP 반복, update/delta/dev/... 토픽 구독

# 코드보안 (.py 파일 없음)
find /opt/matterhub/app -type f -name '*.py' ! -name '__init__.py' | wc -l
```

## 알려진 이슈 (서버 버전)

| 이슈 | 해결 |
|------|------|
| postinst pip 실패 | venv 직접 생성 + postinst pip 스킵 |
| dpkg iF (half-configured) | postinst 스킵 + depends 수정 + dpkg --configure |
| .env 순환 symlink | 양쪽 삭제 → 실제 파일 → 단방향 symlink |
| PermissionError resources/ | chown -R whatsmatter:whatsmatter /opt/matterhub/app/ |
| MQTT None/api/states | .env 복구 후 서비스 재시작 |
| `register_hub_on_relay.sh` 실행 후 터널 Permission denied | 스크립트가 relay의 `/home/whatsmatter/.ssh/authorized_keys` 쓰기 권한 오류로 키 미등록 → Mac에서 수동 추가: `ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 "echo 'restrict,port-forwarding,permitlisten=\"127.0.0.1:<포트>\" $(cat /tmp/hub_tunnel_key.pub)' \| sudo tee -a /home/whatsmatter/.ssh/authorized_keys"` |
| 터널 active인데 Permission denied | relay에 Pi 공개키 미등록 또는 Pi에 relay hub-access 키 미등록 → 2-10 3단계 모두 수행 |
| relay `j` 접속 시 Permission denied | Pi의 authorized_keys에 relay hub-access 공개키 미등록 → 2-10c 수행 |
| 터널 .ssh/ 키 읽기 실패 | .ssh/ 파일이 matterhub 소유 → chown -R whatsmatter:whatsmatter /home/whatsmatter/.ssh/ |
| 터널 .env Permission denied | /etc/matterhub/matterhub.env가 root:matterhub 소유 → chown whatsmatter:whatsmatter |
| 터널 로그에 `Failed to add the host to the list of known hosts` 반복 | known_hosts 파일 권한 문제 (쓰기 불가) → 연결 자체에는 영향 없음. 해결 시: `chmod 600 ~/.ssh/known_hosts` 또는 파일 생성: `touch ~/.ssh/known_hosts && chmod 600 ~/.ssh/known_hosts` |
| HA OTBR 통합 연결실패 | OTBR Docker 미설치 또는 URL을 IP로 등록 → 스킬 1 재실행 후 `http://127.0.0.1:8081`로 등록 |

## 장비 대장

| 장비 | IP | matterhub_id | tunnel port | 상태 |
|------|-----|-------------|-------------|------|
| 1호기 | 192.168.1.94 | whatsmatter-nipa_SN-1773129896 | 22341 | 완료 |
| 2호기 | 192.168.1.96 | whatsmatter-nipa_SN-1773147203 | 22342 | 완료 |
| 3호기 | 192.168.1.97 | whatsmatter-nipa_SN-1773195051 | 22343 | 완료 |

Relay: 3.38.126.167:443 (ec2-user, key: ~/.ssh/matterhub-relay-operator-key.pem)
