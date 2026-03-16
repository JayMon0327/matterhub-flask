# MatterHub 라즈베리파이 서버 세팅 플레이북

> **최종 검증일**: 2026-03-13
> **기준 장비**: Device 1 (192.168.1.94, Ubuntu Server 24.04)
> **원칙**: 이 문서는 **실제 성공한 절차만** 기록한다. 서버 버전(Ubuntu Server) 기준.

---

## 개요

라즈베리파이에 MatterHub 전체 스택을 세팅하는 과정을 2개 스킬로 나눈다.

| 스킬 | 내용 | 소요시간 |
|------|------|----------|
| **스킬 1** | 플랫폼 설치 (OTBR + HA + Matter Server) | ~30분 (OTBR 소스 빌드 포함) |
| **스킬 2** | MatterHub Flask 서버 패키징 설치 (.deb) | ~20분 |

### 대상 하드웨어

- Raspberry Pi 5
- Ubuntu Server 24.04 LTS (aarch64) — **Desktop 아님**
- Thread RCP 모듈 (NRF52840, /dev/ttyACM0)
- Wi-Fi: wlan0

### 공통 변수 (세팅 시작 전 확정)

```bash
DEVICE_IP="<장비_IP>"
DEVICE_USER="whatsmatter"
DEVICE_PW="mat458496ad!"
```

### SSH 접속 참고

비밀번호에 `!`가 포함되어 있어 `sshpass`는 동작하지 않음. `expect` 또는 수동 SSH 사용.

---

# 스킬 1: 플랫폼 설치 (OTBR + HA + Matter Server)

> OTBR은 로컬(systemd) 서비스로 설치한다. 총 소요 시간 약 30분 (빌드 포함).
>
> **OTBR 방식 결정 근거**: Docker OTBR은 D-Bus/Bluetooth/mDNS 공유 문제로
> HA Thread 커미셔닝이 실패하는 케이스가 있다. 로컬 빌드 방식은 안정적으로 동작한다.

## 1-0. unattended-upgrades 즉시 비활성화

**반드시 OS 설치 후 가장 먼저 실행!**

```bash
sudo systemctl stop unattended-upgrades
sudo systemctl disable unattended-upgrades
while sudo fuser /var/lib/dpkg/lock-frontend 2>/dev/null; do sleep 5; done
```

## 1-1. Docker 설치

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker && sudo systemctl start docker
sudo usermod -aG docker whatsmatter
```

## 1-2. apt 소스 noble-updates 추가

Ubuntu 24.04 기본 설치 시 `noble-updates`가 누락되어 빌드 의존성 버전 충돌이 발생한다.

```bash
sudo tee -a /etc/apt/sources.list.d/ubuntu.sources << 'EOF'

Types: deb
URIs: http://ports.ubuntu.com/ubuntu-ports/
Suites: noble-updates
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

sudo apt update
sudo apt upgrade -y
```

## 1-3. OTBR 로컬 빌드 및 설치

> 빌드에 약 15~20분 소요 (Raspberry Pi 4/5 기준)

```bash
# 빌드 의존성
sudo apt install -y git build-essential cmake libdbus-1-dev libsystemd-dev python3-pip bluez

# 소스 클론 및 빌드
cd ~
git clone https://github.com/openthread/ot-br-posix.git
cd ot-br-posix
./script/bootstrap
INFRA_IF_NAME=wlan0 ./script/setup
```

## 1-4. OTBR REST API 외부 접근 허용

기본값은 `127.0.0.1`만 바인딩되어 HA에서 보더라우터 재설정 등 일부 기능이 제한된다.

```bash
sudo sed -i 's|trel://wlan0"|trel://wlan0 --rest-listen-address 0.0.0.0"|' /etc/default/otbr-agent
```

변경 후 확인:

```bash
cat /etc/default/otbr-agent
# OTBR_AGENT_OPTS="-I wpan0 -B wlan0 spinel+hdlc+uart:///dev/ttyACM0 trel://wlan0 --rest-listen-address 0.0.0.0"
```

> USB 포트가 `/dev/ttyACM1`인 경우: `sudo sed -i 's|/dev/ttyACM0|/dev/ttyACM1|' /etc/default/otbr-agent`

## 1-5. OTBR 서비스 시작 및 Thread 네트워크 초기화

```bash
sudo systemctl enable otbr-agent
sudo systemctl start otbr-agent

# Thread 네트워크 생성 (채널 15)
sudo ot-ctl dataset init new
sudo ot-ctl dataset channel 15
sudo ot-ctl dataset commit active
sudo ot-ctl ifconfig up
sudo ot-ctl thread start

# 약 10~15초 대기 후 확인
sleep 15
sudo ot-ctl state              # leader
sudo ot-ctl channel             # 15
curl -s http://127.0.0.1:8081/node/state   # "leader"
```

기존 Thread 데이터셋 복원 시 (TLV 백업 있는 경우):

```bash
sudo ot-ctl dataset set active <TLV_HEX>
sudo ot-ctl ifconfig up
sudo ot-ctl thread start
sleep 15
sudo ot-ctl state
```

## 1-6. docker-compose.yml 작성 및 HA/Matter Server 기동

> OTBR은 로컬 서비스이므로 docker-compose에 포함하지 않는다.

```bash
mkdir -p /home/whatsmatter/matterhub-install/config
mkdir -p /home/whatsmatter/docker/matter-server/data

cat > /home/whatsmatter/matterhub-install/docker-compose.yml << 'EOF'
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    container_name: homeassistant_core
    restart: unless-stopped
    privileged: true
    network_mode: host
    volumes:
      - ./config:/config
      - /etc/localtime:/etc/localtime:ro
      - /run/dbus:/run/dbus:ro
    environment:
      - TZ=Asia/Seoul

  matter-server:
    image: ghcr.io/home-assistant-libs/python-matter-server:stable
    container_name: matter-server
    restart: unless-stopped
    privileged: true
    network_mode: host
    security_opt:
      - apparmor:unconfined
    volumes:
      - ${USERDIR:-$HOME}/docker/matter-server/data:/data/
      - /run/dbus:/run/dbus:ro
    command: --storage-path /data --paa-root-cert-dir /data/credentials --bluetooth-adapter 0 --log-level info
EOF

cd /home/whatsmatter/matterhub-install
docker compose up -d
```

## 1-7. HA OTBR 통합 등록

HA 초기 설정 완료(브라우저에서 `http://<장비IP>:8123`) 후:

1. **설정 → 기기 및 서비스 → 통합 추가**
2. **Open Thread Border Router** 검색 후 선택
3. URL 입력: `http://127.0.0.1:8081`
4. Thread 통합이 자동으로 함께 등록됨

또는 Long-Lived Token 발급 후 CLI로 등록:

```bash
HA_TOKEN="<발급받은_토큰>"

FLOW=$(curl -s -X POST http://127.0.0.1:8123/api/config/config_entries/flow \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"handler":"otbr","show_advanced_options":false}')
FLOW_ID=$(echo $FLOW | python3 -c "import sys,json; print(json.load(sys.stdin)['flow_id'])")

curl -s -X POST "http://127.0.0.1:8123/api/config/config_entries/flow/$FLOW_ID" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"url":"http://127.0.0.1:8081"}'
# "type":"create_entry" 응답 = 성공
```

## 1-8. 스킬 1 검증

```bash
# 로컬 OTBR 상태
sudo ot-ctl state              # leader
sudo ot-ctl channel             # 15
curl -s http://127.0.0.1:8081/node/state   # "leader"
systemctl is-active otbr-agent             # active

# Docker 컨테이너 확인 (2개)
docker ps --format "table {{.Names}}\t{{.Status}}"
# homeassistant_core  Up ...
# matter-server       Up ...

# HA 응답
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8123  # 200

# HA에서 OTBR 통합 확인: 설정 → 기기 및 서비스 → OTBR
# → 보더라우터 재설정 등 옵션이 활성화되어야 정상
```

---

# 스킬 2: MatterHub Flask 서버 패키징 설치

## 2-1. .deb 패키지 빌드 (Mac에서)

```bash
cd /Users/wm-mac-01/Documents/matterhub-flask/matterhub-flask
brew install dpkg  # 최초 1회
bash device_config/build_matterhub_deb.sh
ls -la dist/matterhub_*_arm64.deb
```

> `--mode source` (기본값) 사용. Mac Python 3.11 ↔ Pi Python 3.12 바이트코드 비호환으로 `--mode pyc` 쓰지 않음.

## 2-2. .deb 전송 및 설치 (Pi에서)

```bash
# Mac에서 전송
scp dist/matterhub_*_arm64.deb ${DEVICE_USER}@${DEVICE_IP}:/tmp/

# Pi에서 SSH 접속 후
sudo dpkg -i /tmp/matterhub_*.deb
```

> **예상 결과**: postinst의 `pip install` 단계에서 실패. 파일은 `/opt/matterhub/app/`에 정상 설치됨.

## 2-3. venv 구성

Pi에 기존 venv가 있으면 복사, 없으면 직접 생성:

### 방법 A: 기존 venv 복사 (Desktop 버전에서 전환 시)

```bash
sudo cp -a ~/Desktop/matterhub/venv /opt/matterhub/venv
sudo chown -R whatsmatter:whatsmatter /opt/matterhub/venv
```

### 방법 B: 직접 생성 (서버 신규 세팅)

```bash
sudo python3 -m venv /opt/matterhub/venv
sudo /opt/matterhub/venv/bin/pip install --upgrade pip
sudo /opt/matterhub/venv/bin/pip install -r /opt/matterhub/app/requirements.txt
```

확인:

```bash
/opt/matterhub/venv/bin/python --version   # Python 3.12.x
```

## 2-4. postinst 수동 완료

pip 실패로 중단된 postinst의 남은 작업을 수행:

```bash
# .pyc 컴파일
sudo /opt/matterhub/venv/bin/python -m compileall -q -b /opt/matterhub/app

# .py 소스 삭제 (__init__.py 유지)
sudo find /opt/matterhub/app -type f -name '*.py' ! -name '__init__.py' -delete

# __pycache__ 정리
sudo find /opt/matterhub/app -type d -name '__pycache__' -prune -exec rm -rf {} +

# macOS 리소스 포크 파일 삭제
sudo find /opt/matterhub/app -name "._*" -delete

# 런처 스크립트 확장자 수정 (.py -> .pyc)
for launcher in /opt/matterhub/bin/matterhub-*; do
  [ -f "$launcher" ] && sudo sed -i 's/\.py"/\.pyc"/g' "$launcher"
done
```

## 2-5. dpkg 상태 수복

```bash
# postinst에서 pip 라인 2개 스킵 처리 (30~31번 줄)
sudo sed -i '30,31s|.*|echo SKIP #|' /var/lib/dpkg/info/matterhub.postinst

# dpkg depends 수정 (서버 버전에서 python3-pip, network-manager 불필요)
sudo sed -i '/^Package: matterhub$/,/^$/s/^Depends:.*/Depends: python3, python3-venv, openssh-client, openssh-server/' /var/lib/dpkg/status

# 상태 수복
sudo dpkg --configure matterhub

# 확인: ii 상태여야 정상
dpkg -l matterhub | grep matterhub
# 출력: ii  matterhub  ...
```

> provision 서비스 실패 메시지는 무시해도 됨 (이후 수동 설정)

## 2-6. .env 설정 (핵심!)

**주의**: postinst가 .env symlink를 생성하는데, 순환 심볼릭 링크가 발생할 수 있다.
반드시 아래 순서로 실제 파일을 먼저 만들고, `/etc/matterhub/matterhub.env`는 그것을 가리키게 한다.

```bash
# 기존 깨진 symlink 제거
sudo rm -f /etc/matterhub/matterhub.env /opt/matterhub/app/.env

# 실제 .env 파일 생성
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

# /etc/matterhub/matterhub.env -> 실제 파일을 가리키는 symlink
sudo ln -sf /opt/matterhub/app/.env /etc/matterhub/matterhub.env
```

**장비별로 추가 설정이 필요한 항목:**

```bash
# matterhub_id (프로비저닝 후 자동 생성, 또는 수동 추가)
echo 'matterhub_id="whatsmatter-nipa_SN-<시리얼>"' | sudo tee -a /opt/matterhub/app/.env

# SUPPORT_TUNNEL_REMOTE_PORT (장비마다 다름)
echo 'SUPPORT_TUNNEL_REMOTE_PORT=<포트번호>' | sudo tee -a /opt/matterhub/app/.env
```

## 2-7. 소유권 + 퍼미션 수정 (핵심!)

dpkg 설치 시 `matterhub` 시스템 유저 소유로 파일이 생성되지만, 서비스는 `whatsmatter` 유저로 실행됨.
또한 systemd 서비스에 `UMask=0077`이 설정되어 있어 group/other 접근이 차단됨.

**반드시** 아래를 실행:

```bash
sudo chown -R whatsmatter:whatsmatter /opt/matterhub/app/
sudo chown -R whatsmatter:whatsmatter /home/whatsmatter/.ssh/
sudo chown whatsmatter:whatsmatter /etc/matterhub/matterhub.env 2>/dev/null
```

> `.ssh/` 소유권도 수정해야 터널 서비스가 키를 읽을 수 있음. `.env` 소유권도 수정해야 서비스 시작 시 환경변수 로딩 가능.

## 2-8. systemd 서비스 설정

```bash
# 서비스 파일 복사 (Ubuntu 24.04에서 /usr/lib/systemd/system/ 인식 이슈)
sudo cp /usr/lib/systemd/system/matterhub-*.service /etc/systemd/system/

# User 변경 (matterhub -> whatsmatter)
sudo sed -i 's/User=matterhub/User=whatsmatter/g; s/Group=matterhub/Group=whatsmatter/g' \
  /etc/systemd/system/matterhub-*.service

# 서비스 활성화 + 시작
sudo systemctl daemon-reload
sudo systemctl enable --now \
  matterhub-api.service \
  matterhub-mqtt.service \
  matterhub-rule-engine.service \
  matterhub-notifier.service \
  matterhub-update-agent.service
```

## 2-9. HA 토큰 설정

1. 브라우저에서 `http://<DEVICE_IP>:8123` 접속
2. 사용자 프로필 → 보안 → "장기 액세스 토큰" 생성
3. `.env`에 추가:

```bash
echo 'hass_token=<발급받은_토큰>' | sudo tee -a /opt/matterhub/app/.env
sudo systemctl restart matterhub-api matterhub-mqtt
```

## 2-10. 리버스 SSH 터널 설정 (3단계: Pi 설정 → relay 등록 → relay→Pi 키 등록)

> **주의**: 3단계를 모두 완료해야 터널이 양방향으로 동작한다.
> 하나라도 빠지면 서비스는 active이지만 실제로는 `Permission denied (publickey)`로 실패.

### 2-10a. Pi에서 터널 설정 + 서비스 시작

```bash
# Pi에서 (SSH 접속 후)
sudo bash /opt/matterhub/device_config/setup_support_tunnel.sh \
  --host 3.38.126.167 \
  --user whatsmatter \
  --port 443 \
  --remote-port <장비별_포트> \
  --command ssh \
  --run-user whatsmatter \
  --device-user whatsmatter \
  --relay-operator-user ec2-user \
  --env-file /etc/matterhub/matterhub.env \
  --skip-install-unit \
  --enable-now
```

키 확인 (전용 키가 생성되었으면 그것을, 아니면 기본 키 사용):

```bash
ls /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub 2>/dev/null \
  || ls /home/whatsmatter/.ssh/id_ed25519.pub
```

### 2-10b. Mac에서 relay에 Pi 공개키 등록

```bash
# Pi 공개키 가져오기 (전용 키 우선, 없으면 기본 키)
scp ${DEVICE_USER}@${DEVICE_IP}:/home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub /tmp/hub_tunnel_key.pub 2>/dev/null \
  || scp ${DEVICE_USER}@${DEVICE_IP}:/home/whatsmatter/.ssh/id_ed25519.pub /tmp/hub_tunnel_key.pub

# relay에 등록
cd /Users/wm-mac-01/Documents/matterhub-flask/matterhub-flask
bash device_config/register_hub_on_relay.sh \
  --relay-host 3.38.126.167 \
  --relay-port 443 \
  --relay-user ec2-user \
  --relay-key ~/.ssh/matterhub-relay-operator-key.pem \
  --hub-id "<matterhub_id>" \
  --remote-port <장비별_포트> \
  --hub-pubkey /tmp/hub_tunnel_key.pub \
  --device-user whatsmatter
```

### 2-10c. Pi에 relay hub-access 공개키 등록 (필수!)

relay에서 `j <hub_id>`로 Pi에 접속하려면, relay의 hub-access 공개키가 Pi의 `authorized_keys`에 있어야 한다.
**이 단계를 빠뜨리면 relay→Pi 접속이 Permission denied로 실패한다.**

```bash
# Mac에서 한 줄로 실행
RELAY_PUBKEY=$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 -o StrictHostKeyChecking=no ec2-user@3.38.126.167 "cat /home/ec2-user/.ssh/hub_access_ed25519.pub")
ssh ${DEVICE_USER}@${DEVICE_IP} "mkdir -p ~/.ssh && echo '${RELAY_PUBKEY}' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```

또는 Pi에서 직접:

```bash
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILz8d991jif0znz0/mAT0bNiV5zbVTFXHjMvYmbcEVHJ relay-hub-access' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### 2-10d. 터널 재시작 + 검증

```bash
# Pi에서
sudo systemctl restart matterhub-support-tunnel
sudo journalctl -u matterhub-support-tunnel --no-pager -n 10
# Permission denied 없이 attempt=1 이후 에러 없으면 성공
```

```bash
# Mac에서 relay 경유 접속 테스트
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
j <matterhub_id>
# Pi 셸 진입 성공
```

## 2-11. 방화벽 (UFW)

```bash
sudo ufw allow 8100/tcp    # Flask API
sudo ufw allow 8123/tcp    # Home Assistant
sudo ufw status
```

## 2-12. 스킬 2 검증

### 서비스 전체 상태

```bash
for svc in api mqtt rule-engine notifier update-agent support-tunnel; do
  echo -n "matterhub-${svc}: "
  systemctl is-active matterhub-${svc}.service
done
# 6개 모두 active
```

### API 응답

```bash
curl -s http://localhost:8100/local/api/states | head -50
# HA 엔티티 목록 JSON 반환
```

### MQTT 코나아이 토픽

```bash
sudo journalctl -u matterhub-mqtt --no-pager -n 30 | grep -E "SUBSCRIBE|CONNECT"
# [MQTT][CONNECT][OK] connected to broker
# [MQTT][SUBSCRIBE] complete total=2 success=2 failed=0
```

### 리버스 터널 접속 (Mac에서)

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
j <matterhub_id>
```

### 코드보안

```bash
find /opt/matterhub/app -type f -name '*.py' ! -name '__init__.py' | wc -l
# 결과: 0
```

---

# 장비 대장

| 장비 | IP | matterhub_id | tunnel port | 상태 |
|------|-----|-------------|-------------|------|
| 1호기 | 192.168.1.94 | whatsmatter-nipa_SN-1773129896 | 22341 | 완료 |
| 2호기 | 192.168.1.96 | whatsmatter-nipa_SN-1773147203 | 22342 | 완료 |
| 3호기 | 192.168.1.97 | whatsmatter-nipa_SN-1773195051 | 22343 | 완료 |

### Relay 서버

| 항목 | 값 |
|------|-----|
| Host | 3.38.126.167 |
| Port | 443 |
| User | ec2-user |
| Operator Key | `~/.ssh/matterhub-relay-operator-key.pem` |

---

# 알려진 이슈 & 해결 (서버 버전)

| 이슈 | 원인 | 해결 |
|------|------|------|
| Docker OTBR Thread 커미셔닝 실패 | D-Bus/BLE/mDNS 공유 문제 | 로컬 OTBR로 전환 (1-3 참조) |
| OTBR 빌드 의존성 충돌 (held broken packages) | noble-updates 누락 | 1-2에서 noble-updates 추가 후 `apt upgrade -y` |
| HA OTBR 통합 반쪽 동작 (재설정 불가) | REST API가 127.0.0.1만 바인딩 | `--rest-listen-address 0.0.0.0` 추가 (1-4 참조) |
| HA OTBR 통합 연결 실패 | 기존 Docker OTBR 통합 항목 잔존 | HA에서 OTBR/Thread 통합 삭제 후 재등록 |
| wpan0 Device busy (재시작 시) | 이전 프로세스의 wpan0 잔존 | `sudo ip link delete wpan0` 후 `sudo systemctl restart otbr-agent` |
| postinst pip install 실패 | DNS 불안정 / pypi.org 접근 불가 | venv 직접 생성 또는 기존 복사 후 postinst pip 라인 스킵 |
| dpkg `iF` (half-configured) | postinst 중간 실패 | postinst pip 스킵 + depends 수정 + `dpkg --configure` |
| dpkg depends 불충족 (python3-pip, network-manager) | 서버 버전에 불필요한 패키지 | `/var/lib/dpkg/status`에서 Depends 수정 |
| `.env` 순환 symlink | postinst가 양방향 symlink 생성 | 양쪽 삭제 → 실제 파일 생성 → 단방향 symlink |
| `PermissionError: resources/schedule.json` | `/opt/matterhub/app/` 소유자가 `matterhub` (시스템유저) | `chown -R whatsmatter:whatsmatter /opt/matterhub/app/` |
| 터널 `.ssh/` 키 읽기 실패 | dpkg postinst가 `matterhub` 유저로 .ssh/ 파일 생성 | `chown -R whatsmatter:whatsmatter /home/whatsmatter/.ssh/` |
| 터널 `.env` Permission denied | `/etc/matterhub/matterhub.env` 소유자 불일치 | `chown whatsmatter:whatsmatter /etc/matterhub/matterhub.env` |
| MQTT `None/api/states` URL 에러 | .env 읽기 실패로 HA_host가 None | .env 파일 복구 후 서비스 재시작 |
| unattended-upgrades 버전 불일치 | -dev 패키지와 런타임 패키지 버전 차이 | 1-0에서 즉시 비활성화 |
| OTBR CCA_FAILURE | 채널 간섭 (특히 13) | 채널 15로 변경 |
| sshpass 접속 실패 | 비밀번호 `!` 특수문자 | expect 사용 |
