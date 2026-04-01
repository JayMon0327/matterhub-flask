# MatterHub Platform 설치 가이드 (OTBR + HA + Matter Server)

## 1단계: 플랫폼 설치 (이 문서)
## 2단계: matterhub-flask 프로젝트 설치 (별도 - repeatable-raspi-setup.md 참조)

> 상세 플레이북: [raspi-server-setup-playbook.md](operations/raspi-server-setup-playbook.md)
> Claude Code 스킬: `/platform-install <장비IP>`

---

## 대상 하드웨어
- Raspberry Pi 5 (Ubuntu Server 24.04 LTS, aarch64) — **Desktop 아님**
- Thread RCP 모듈 (NRF52840, /dev/ttyACM0)
- Wi-Fi: wlan0

## 아키텍처

| 컴포넌트 | 실행 방식 | 포트 |
|----------|----------|------|
| OTBR (OpenThread Border Router) | 로컬 소스 빌드 → systemd (`otbr-agent`) | 8081 (REST) |
| Home Assistant | Docker 컨테이너 (`host` 네트워크) | 8123 |
| Matter Server | Docker 컨테이너 (`host` 네트워크) | 5580 |

> **OTBR은 Docker가 아닌 로컬 빌드를 사용한다.** Docker OTBR은 D-Bus/BLE/mDNS 공유 문제로
> HA Thread 커미셔닝이 실패하는 케이스가 있다. 로컬 빌드 방식은 안정적으로 동작한다.

## 설치 전 필수 조치 (Ubuntu 24.04 fresh install 직후)

### unattended-upgrades 비활성화 (먼저!)
새 OS 설치 직후 unattended-upgrades가 보안 패치를 적용하면서
-dev 패키지와 버전 불일치를 일으킴. 반드시 먼저 비활성화할 것.

```bash
sudo systemctl stop unattended-upgrades
sudo systemctl disable unattended-upgrades
while sudo fuser /var/lib/dpkg/lock-frontend 2>/dev/null; do sleep 5; done
```

## 순서대로 실행할 명령어

### 1. Docker 설치
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker whatsmatter
```

### 2. apt 소스 noble-updates 추가
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

### 3. OTBR 로컬 빌드 및 설치
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
- 라즈베리파이에서 빌드 15-20분 소요

### 4. OTBR 서비스 설정
```bash
# REST API 외부 접근 허용
sudo sed -i 's|trel://wlan0"|trel://wlan0 --rest-listen-address 0.0.0.0"|' /etc/default/otbr-agent

# 서비스 활성화 및 시작
sudo systemctl enable otbr-agent
sudo systemctl start otbr-agent
```

> USB 포트가 `/dev/ttyACM1`인 경우: `sudo sed -i 's|/dev/ttyACM0|/dev/ttyACM1|' /etc/default/otbr-agent`

### 5. Thread 네트워크 초기화 (채널 15)
```bash
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

### 6. HA + Matter Server Docker 기동
```bash
mkdir -p /home/whatsmatter/matterhub-install/config
mkdir -p /home/whatsmatter/docker/matter-server/data
```

docker-compose.yml (`/home/whatsmatter/matterhub-install/docker-compose.yml`):
- homeassistant: ghcr.io/home-assistant/home-assistant:stable (host network, port 8123)
- matter-server: ghcr.io/home-assistant-libs/python-matter-server:stable (host network, port 5580)
  - `--primary-interface wlan0` 필수 (link-local 라우팅 충돌 방지)

```bash
cd /home/whatsmatter/matterhub-install
docker compose up -d
```

### 7. mDNS 충돌 수정
OTBR + avahi-daemon + matter-server의 UDP 5353 동시 바인딩 충돌을 방지한다.

```bash
# avahi-daemon: wpan0 제외
sudo sed -i '/^\[server\]/a deny-interfaces=wpan0' /etc/avahi/avahi-daemon.conf
sudo systemctl restart avahi-daemon

# ip6tables: wpan0 mDNS 차단 + 영구화
sudo ip6tables -I INPUT -i wpan0 -p udp --dport 5353 -j DROP
sudo ip6tables -I OUTPUT -o wpan0 -p udp --dport 5353 -j DROP
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

> 별도 스크립트: `device_config/fix_otbr_mdns_conflict.sh` (멱등성 있는 전체 수정)

### 8. 검증
```bash
# OTBR 상태
systemctl is-active otbr-agent         # active
sudo ot-ctl state                       # leader
sudo ot-ctl channel                     # 15

# Docker 컨테이너 (2개)
docker ps                               # homeassistant_core, matter-server

# HA 응답
curl -s http://localhost:8123 | head -5

# matter-server primary interface
docker logs matter-server --tail 30 2>&1 | grep "primary interface"
# Using 'wlan0' as primary interface
```

## Thread 채널 변경 방법
```bash
sudo ot-ctl thread stop
sudo ot-ctl dataset init active
sudo ot-ctl dataset channel 15
sudo ot-ctl dataset commit active
sudo ot-ctl thread start
```

## 알려진 이슈
- OTBR 빌드 의존성 충돌: `noble-updates` 추가 + `apt upgrade -y`로 해결
- HA OTBR 통합 재설정 불가: `--rest-listen-address 0.0.0.0` 추가 (4단계)
- WiFi Matter 커미셔닝 PASE 타임아웃: `--primary-interface wlan0` + mDNS 수정 (6~7단계)
- CCA_FAILURE: 채널 13에서 빈번 발생 → 채널 15로 변경
- `Device or resource busy` on wpan0: `sudo ip link delete wpan0` 후 재시작
- HA OTBR 통합 URL: 반드시 `http://127.0.0.1:8081` (외부 IP 불가)
