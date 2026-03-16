# MatterHub Platform 설치 가이드 (OTBR + HA + Matter Server)

## 1단계: 플랫폼 설치 (이 문서)
## 2단계: matterhub-flask 프로젝트 설치 (별도 - repeatable-raspi-setup.md 참조)

---

## 대상 하드웨어
- Raspberry Pi (Ubuntu 24.04 LTS, aarch64)
- Thread RCP 모듈 (NRF52840, /dev/ttyACM0)
- Wi-Fi: wlan0

## 스크립트 위치
- 로컬: /Users/wm-mac-01/Desktop/matterhub-install/
- 서버: /home/whatsmatter/matterhub-install/

## 설치 전 필수 조치 (Ubuntu 24.04 fresh install 직후)

### unattended-upgrades 비활성화 (먼저!)
새 OS 설치 직후 unattended-upgrades가 보안 패치를 적용하면서
-dev 패키지와 버전 불일치를 일으킴. 반드시 먼저 비활성화할 것.

```bash
sudo systemctl stop unattended-upgrades
sudo systemctl disable unattended-upgrades
```

### 패키지 버전 동기화
unattended-upgrades가 이미 돌았으면 아래 패키지들이 버전 불일치 발생:
- libbz2-1.0 vs bzip2
- libdbus-1-3 vs libdbus-1-dev
- zlib1g vs zlib1g-dev

해결:
```bash
sudo apt-get install -y --allow-downgrades \
  libbz2-1.0=1.0.8-5.1 bzip2=1.0.8-5.1 \
  libdbus-1-3=1.14.10-4ubuntu4 \
  zlib1g=1:1.3.dfsg-3.1ubuntu2
```
(버전 번호는 `apt-cache policy <pkg>`로 확인)

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

### 2. 빌드 도구 + OTBR 의존성 설치
```bash
export DEBIAN_FRONTEND=noninteractive
sudo -E apt-get install -y \
  git build-essential cmake ninja-build pkgconf nodejs npm \
  libprotobuf-dev protobuf-compiler \
  libdbus-1-dev \
  libboost-dev libboost-filesystem-dev libboost-system-dev \
  libavahi-client-dev libavahi-common-dev \
  libjsoncpp-dev
```

### 3. OTBR 클론 + 부트스트랩 + 빌드
```bash
cd /home/whatsmatter
git clone https://github.com/openthread/ot-br-posix.git
cd ot-br-posix
git submodule update --init
sudo DEBIAN_FRONTEND=noninteractive ./script/bootstrap
sudo FIREWALL=0 INFRA_IF_NAME=wlan0 ./script/setup
```
- 라즈베리파이에서 빌드 20-40분 소요
- MRT6 패치는 라즈베리파이에서 불필요 (Jetson 전용)

### 4. OTBR 서비스 설정
```bash
sudo tee /etc/default/otbr-agent > /dev/null <<'EOF'
OTBR_AGENT_OPTS="-I wpan0 -B wlan0 spinel+hdlc+uart:///dev/ttyACM0 trel://wlan0"
OTBR_NO_AUTO_ATTACH=0
EOF

sudo systemctl enable systemd-resolved
sudo systemctl restart systemd-resolved
sudo systemctl daemon-reexec
sudo systemctl enable otbr-agent
sudo systemctl restart otbr-agent

# Avahi 비활성화 (systemd-resolved와 충돌 방지)
sudo systemctl disable avahi-daemon.socket avahi-daemon 2>/dev/null || true
sudo systemctl stop avahi-daemon.socket avahi-daemon 2>/dev/null || true
```

### 5. HA + Matter Server Docker 기동
```bash
mkdir -p /home/whatsmatter/matterhub-install/config
mkdir -p /home/whatsmatter/docker/matter-server/data
cd /home/whatsmatter/matterhub-install
docker compose up -d
```

docker-compose.yml:
- homeassistant: ghcr.io/home-assistant/home-assistant:stable (host network, port 8123)
- matter-server: ghcr.io/home-assistant-libs/python-matter-server:stable (host network, port 5580)

### 6. 검증
```bash
sudo systemctl status otbr-agent
sudo ot-ctl state        # leader 기대
sudo ot-ctl channel      # 15 권장
docker ps                # homeassistant_core, matter-server 확인
curl -s http://localhost:8123 | head -5  # HA 응답 확인
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
- OTBR WARN 로그에 빌드 경로가 남음 (기능 영향 없음)
- CCA_FAILURE: 채널 13에서 빈번 발생 → 채널 15로 변경하면 해결
- `Device or resource busy` on wpan0: otbr-agent 재시작 시 발생 가능
  → `sudo ip link delete wpan0` 후 재시작
