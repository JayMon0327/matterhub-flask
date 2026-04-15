---
name: platform-install
description: 라즈베리파이에 MatterHub 플랫폼(OTBR + HomeAssistant + Matter Server) 설치. 새 장비 세팅 시 스킬 1로 실행.
disable-model-invocation: true
argument-hint: "[장비IP] [SSH_USER] [SSH_PW]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# 스킬 1: 플랫폼 설치 (OTBR + HA + Matter Server)

라즈베리파이에 MatterHub 플랫폼을 설치한다. 전체 절차의 상세 내용은 [플레이북](../../../docs/operations/raspi-server-setup-playbook.md)의 "스킬 1" 섹션 참조.

> **SD카드 대량 복제 워크플로우**: `/platform-base-image` (공통 설치) → SD카드 복제 → `/platform-activate` (장비별 활성화)

> **OTBR 방식**: 로컬 소스 빌드 → systemd 서비스. Docker OTBR은 D-Bus/BLE/mDNS 공유 문제로
> Thread 커미셔닝이 실패하는 케이스가 있어 사용하지 않는다.

## 대상

- Raspberry Pi 5, Ubuntu Server 24.04 LTS (aarch64)
- Thread RCP 모듈 (NRF52840, /dev/ttyACM0)

## 사전 입력 (스킬 시작 시 AskUserQuestion으로 수집)

스킬 시작 시 다음 정보를 사용자에게 요청한다. `$ARGUMENTS`로 일부가 전달될 수 있으나 누락된 항목은 반드시 질문한다.

| 변수 | 설명 | 예시 |
|------|------|------|
| `HOST_IP` | 장비 IP | 192.168.1.96 |
| `SSH_USER` | SSH 사용자명 | whatsmatter |
| `SSH_PW` | SSH/sudo 비밀번호 | (사용자 입력) |
| `HA_TOKEN` | HA Long-Lived Access Token (1-8 단계 전에 수집 가능) | eyJhbG... |

> `HA_TOKEN`은 HA 초기 설정 완료 후에만 발급 가능하므로, 1-6 완료 후 사용자에게 별도 요청한다.

## 실행 절차

아래 순서대로 Pi에 SSH 접속하여 명령을 실행한다. **expect를 사용**하여 자동화한다 (sshpass는 비밀번호에 `!` 등 특수문자가 포함될 수 있어 동작 안 함).
SSH 접속 시 `HOST_IP`, `SSH_USER`, `SSH_PW`를 사용한다.

---

### 1-0. unattended-upgrades 비활성화 (필수 최우선)

```bash
sudo systemctl stop unattended-upgrades
sudo systemctl disable unattended-upgrades
while sudo fuser /var/lib/dpkg/lock-frontend 2>/dev/null; do sleep 5; done
```

---

### 1-1. Docker 설치

> HA와 Matter Server는 Docker 컨테이너로 실행한다. OTBR은 로컬 빌드이므로 Docker 불필요.

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
sudo usermod -aG docker $SSH_USER
```

---

### 1-2. apt 소스 noble-updates 추가

> Ubuntu 24.04 기본 설치 시 `noble-updates`가 누락되어 OTBR 빌드 의존성 버전 충돌이 발생한다.

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

---

### 1-3. OTBR 로컬 빌드 및 설치

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

> `held broken packages` 에러 발생 시 `sudo apt upgrade -y` 후 재시도

---

### 1-4. OTBR REST API 외부 접근 허용

> 기본값은 `127.0.0.1`만 바인딩되어 HA에서 보더라우터 재설정 등 일부 기능이 제한된다.

```bash
sudo sed -i 's|trel://wlan0"|trel://wlan0 --rest-listen-address 0.0.0.0"|' /etc/default/otbr-agent
```

변경 후 확인:

```bash
cat /etc/default/otbr-agent
# OTBR_AGENT_OPTS="-I wpan0 -B wlan0 spinel+hdlc+uart:///dev/ttyACM0 trel://wlan0 --rest-listen-address 0.0.0.0"
```

> USB 포트가 `/dev/ttyACM1`인 경우: `sudo sed -i 's|/dev/ttyACM0|/dev/ttyACM1|' /etc/default/otbr-agent`

---

### 1-5. OTBR 서비스 시작 + Thread 네트워크 초기화

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

---

### 1-6. docker-compose.yml 작성 + HA/Matter Server 기동

> OTBR은 로컬 systemd 서비스이므로 docker-compose에 포함하지 않는다.
> matter-server에 `--primary-interface wlan0`을 반드시 포함한다 (link-local 라우팅 충돌 방지).

```bash
mkdir -p /home/$SSH_USER/matterhub-install/config
mkdir -p /home/$SSH_USER/docker/matter-server/data

cat > /home/$SSH_USER/matterhub-install/docker-compose.yml << 'EOF'
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
    command: --storage-path /data --paa-root-cert-dir /data/credentials --bluetooth-adapter 0 --log-level info --primary-interface wlan0
EOF

cd /home/$SSH_USER/matterhub-install
docker compose up -d
```

---

### 1-7. mDNS 충돌 수정

> OTBR(otbr-agent) + avahi-daemon + matter-server가 UDP 5353을 동시 바인딩하여 mDNS 충돌 발생.
> avahi에서 wpan0을 제외하고, ip6tables로 wpan0의 mDNS 트래픽을 차단한다.

```bash
# avahi-daemon: wpan0 인터페이스 제외
if grep -q "^deny-interfaces=" /etc/avahi/avahi-daemon.conf; then
    grep -q "wpan0" /etc/avahi/avahi-daemon.conf || \
        sudo sed -i 's/^deny-interfaces=.*/&,wpan0/' /etc/avahi/avahi-daemon.conf
elif grep -q "^#deny-interfaces=" /etc/avahi/avahi-daemon.conf; then
    sudo sed -i 's/^#deny-interfaces=.*/deny-interfaces=wpan0/' /etc/avahi/avahi-daemon.conf
else
    sudo sed -i '/^\[server\]/a deny-interfaces=wpan0' /etc/avahi/avahi-daemon.conf
fi
sudo systemctl restart avahi-daemon

# ip6tables: wpan0에서 mDNS(UDP 5353) 차단
sudo ip6tables -C INPUT -i wpan0 -p udp --dport 5353 -j DROP 2>/dev/null || \
    sudo ip6tables -I INPUT -i wpan0 -p udp --dport 5353 -j DROP
sudo ip6tables -C OUTPUT -o wpan0 -p udp --dport 5353 -j DROP 2>/dev/null || \
    sudo ip6tables -I OUTPUT -o wpan0 -p udp --dport 5353 -j DROP

# 영구화
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent 2>/dev/null || true
sudo netfilter-persistent save 2>/dev/null || sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 > /dev/null
```

> 이 수정이 없으면 WiFi Matter 기기 커미셔닝 시 PASE handshake 타임아웃 발생.
> **주의**: 재부팅 시 Docker/OTBR가 체인을 flush하여 규칙 소실 가능. systemd 서비스로 영구화 권장.
> 상세: `docs/troubleshooting/2026년 04월 3주/wifi-matter-mdns-commissioning-failure.md`

---

### 1-8. HA OTBR 통합 등록

> HA가 완전히 기동된 후 (docker ps에서 Up으로 표시, 약 30-60초) 실행.

**HA Long-Lived Token 발급 (브라우저):**
1. `http://$HOST_IP:8123` 접속 → HA 초기 설정 완료
2. 사용자 프로필 → 보안 → 장기 액세스 토큰 생성
3. 토큰을 복사해 아래 명령에 사용

**HA REST API로 OTBR 통합 등록:**

```bash
HA_TOKEN="<발급받은_토큰>"

# config flow 시작
FLOW=$(curl -s -X POST http://127.0.0.1:8123/api/config/config_entries/flow \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"handler":"otbr","show_advanced_options":false}')
FLOW_ID=$(echo $FLOW | python3 -c "import sys,json; print(json.load(sys.stdin)['flow_id'])")
echo "Flow ID: $FLOW_ID"

# URL 제출 (localhost:8081 — HA와 OTBR 모두 host 네트워크/로컬)
curl -s -X POST "http://127.0.0.1:8123/api/config/config_entries/flow/$FLOW_ID" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"url":"http://127.0.0.1:8081"}'
# "type":"create_entry" 응답 확인
```

또는 HA UI에서: **설정 → 기기 및 서비스 → 통합 추가 → Open Thread Border Router → URL: `http://127.0.0.1:8081`**

---

### 1-9. 검증

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

# matter-server primary interface 확인
docker logs matter-server --tail 30 2>&1 | grep "primary interface"
# Using 'wlan0' as primary interface

# mDNS 수정 확인
grep "^deny-interfaces" /etc/avahi/avahi-daemon.conf   # wpan0 포함
sudo ip6tables -L INPUT -n | grep 5353                 # wpan0 DROP

# HA에서 OTBR 통합 확인
curl -s http://127.0.0.1:8123/api/config/config_entries/entry \
  -H "Authorization: Bearer $HA_TOKEN" | python3 -c \
  "import sys,json; [print(e['domain'], e['state']) for e in json.load(sys.stdin) if e['domain'] in ('otbr','thread','matter')]"
```

---

## 알려진 이슈

| 이슈 | 원인 | 해결 |
|------|------|------|
| OTBR 빌드 의존성 충돌 (held broken packages) | `noble-updates` 누락 | 1-2에서 noble-updates 추가 후 `apt upgrade -y` |
| HA OTBR 통합 반쪽 동작 (재설정 불가) | REST API가 127.0.0.1만 바인딩 | 1-4에서 `--rest-listen-address 0.0.0.0` 추가 |
| WiFi Matter 커미셔닝 PASE 타임아웃 | link-local 라우팅 충돌 + mDNS 3중 경쟁 | 1-6에서 `--primary-interface wlan0` + 1-7 mDNS 수정 |
| HA OTBR 통합 연결 실패 (192.168.x.x:8081) | HA와 OTBR이 같은 호스트이므로 `localhost:8081`만 유효 | URL을 `http://127.0.0.1:8081`로 등록 |
| wpan0 Device busy (재시작 시) | 이전 프로세스의 wpan0 잔존 | `sudo ip link delete wpan0` 후 `sudo systemctl restart otbr-agent` |
| OTBR CCA_FAILURE | 채널 간섭 (특히 13) | 채널 15로 변경 |
| apt -dev 패키지 unmet dependencies | unattended-upgrades 실행 중 | 1-0에서 즉시 비활성화 |
| sshpass 접속 실패 | 비밀번호 `!` 특수문자 | expect 사용 |
| HA 기존 OTBR 통합 잔존 (Docker→로컬 전환 시) | HA config에 이전 통합 항목 남음 | HA에서 OTBR/Thread 통합 삭제 후 재등록 |
| `matterhub-install/` 디렉토리 root 소유 | 이전 설치 잔재 | `sudo chown -R $SSH_USER:$SSH_USER /home/$SSH_USER/matterhub-install/` |
