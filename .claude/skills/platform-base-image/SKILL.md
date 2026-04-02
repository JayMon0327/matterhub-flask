---
name: platform-base-image
description: 라즈베리파이 베이스 이미지 생성 (Docker + OTBR 빌드 + Docker 이미지 pull + mDNS 수정). SD카드 대량 복제용.
disable-model-invocation: true
argument-hint: "[장비IP] [SSH_USER] [SSH_PW]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# 베이스 이미지 생성 (SD카드 대량 복제용)

라즈베리파이에 MatterHub 플랫폼의 **공통 부분**을 설치한다.
이 스킬로 만든 SD카드를 복제하여 여러 장비에 사용할 수 있다.

> 장비별 활성화(Thread 초기화, HA 통합 등록)는 `/platform-activate`로 진행한다.
> 전체 원스톱 설치는 `/platform-install` 참조.

## 대상

- Raspberry Pi 5, Ubuntu Server 24.04 LTS (aarch64)
- Thread RCP 모듈 (NRF52840, /dev/ttyACM0)

## 사전 입력 (스킬 시작 시 AskUserQuestion으로 수집)

| 변수 | 설명 | 예시 |
|------|------|------|
| `HOST_IP` | 장비 IP | 192.168.1.96 |
| `SSH_USER` | SSH 사용자명 | whatsmatter |
| `SSH_PW` | SSH/sudo 비밀번호 | (사용자 입력) |

> HA_TOKEN은 불필요 (이 스킬에서는 HA를 시작하지 않음)

## 실행 절차

SSH 접속은 **expect**를 사용한다. `HOST_IP`, `SSH_USER`, `SSH_PW`를 사용.

> **SSH 접속 전**: `ssh-keygen -R $HOST_IP && ssh-keyscan -H $HOST_IP >> ~/.ssh/known_hosts` (호스트 키 충돌 방지)
> **expect + sudo 주의**: sudo 명령은 `&&`로 연결하지 말고 개별 send/expect로 분리. `sudo cmd | tail` 사용 금지 (tail 버퍼링으로 sudo 프롬프트 누락). 복잡한 명령은 bash 스크립트를 scp 후 `sudo bash /tmp/script.sh`로 실행.

---

### 1-0. unattended-upgrades 비활성화 (필수 최우선)

```bash
sudo systemctl stop unattended-upgrades
sudo systemctl disable unattended-upgrades
while sudo fuser /var/lib/dpkg/lock-frontend 2>/dev/null; do sleep 5; done
```

---

### 1-1. Docker 설치

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

### 병렬 구간: 1-3 OTBR 빌드 + 1-6a Docker 이미지 pull

> 1-2 완료 후, **두 에이전트를 병렬로 실행**한다.
> - 에이전트 A: OTBR 빌드 (1-3, ~15분)
> - 에이전트 B: docker-compose.yml 작성 + `docker compose pull` (이미지 다운로드)

#### 에이전트 A: 1-3. OTBR 로컬 빌드

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

#### 에이전트 B: 1-6a. docker-compose.yml 작성 + 이미지 pull

> 컨테이너는 시작하지 않는다 (`pull`만). HA 첫 기동 시 장비별 config가 생성되므로 베이스 이미지에서는 `up -d`를 하지 않는다.

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
docker compose pull
```

---

### 1-4. OTBR REST API 외부 접근 허용

> 에이전트 A (OTBR 빌드) 완료 후 실행.

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

### 1-7. mDNS 충돌 수정

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

---

### 검증

```bash
# Docker 설치 확인
docker --version

# OTBR 빌드 확인
which ot-ctl && echo "OTBR_OK"
cat /etc/default/otbr-agent   # --rest-listen-address 0.0.0.0 포함

# Docker 이미지 확인 (pull 완료)
docker images | grep -E "home-assistant|matter-server"

# docker-compose.yml 존재
cat /home/$SSH_USER/matterhub-install/docker-compose.yml | head -3

# mDNS 수정 확인
grep "deny-interfaces" /etc/avahi/avahi-daemon.conf   # wpan0 포함
sudo ip6tables -L INPUT -n | grep 5353                 # wpan0 DROP

# 컨테이너가 실행 중이지 않은지 확인 (베이스 이미지이므로)
docker ps -q | wc -l   # 0
```

> 검증 통과 후 이 SD카드를 복제하여 여러 장비에 사용한다.
> 각 장비에서 `/platform-activate`를 실행하여 Thread 초기화 + HA 통합 등록을 완료한다.

---

## 알려진 이슈

| 이슈 | 원인 | 해결 |
|------|------|------|
| OTBR 빌드 의존성 충돌 (held broken packages) | `noble-updates` 누락 | 1-2에서 noble-updates 추가 후 `apt upgrade -y` |
| apt -dev 패키지 unmet dependencies | unattended-upgrades 실행 중 | 1-0에서 즉시 비활성화 |
| sshpass 접속 실패 | 비밀번호 `!` 특수문자 | expect 사용 |
| `matterhub-install/` 디렉토리 root 소유 | 이전 설치 잔재 | `sudo chown -R $SSH_USER:$SSH_USER /home/$SSH_USER/matterhub-install/` |
