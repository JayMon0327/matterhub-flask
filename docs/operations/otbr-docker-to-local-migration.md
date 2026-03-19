# OTBR 로컬 전환: Docker → 로컬 설치

Docker 기반 OTBR에서 Thread 커미셔닝이 실패하는 문제를 해결하기 위해,
OTBR을 로컬(systemd) 서비스로 전환하는 절차입니다.

## 환경

- OS: Ubuntu 24.04 LTS (Raspberry Pi)
- Thread 동글: NRF52840 USB RCP (`/dev/ttyACM0`)
- Home Assistant: Docker Container 방식
- matter-server: Docker Container 방식

## 전체 소요 시간

약 20~30분 (빌드 포함)

---

## 1단계: Docker OTBR 중지 및 제거

```bash
cd ~/matterhub-install

# Docker OTBR 컨테이너 중지/제거
docker stop otbr
docker rm otbr
```

## 2단계: docker-compose.yml에서 OTBR 서비스 제거

`docker-compose.yml`에서 `otbr:` 서비스 블록 전체를 삭제합니다.
최종 docker-compose.yml은 아래와 같아야 합니다:

```yaml
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
```

## 3단계: apt 소스에 noble-updates 추가

Ubuntu 24.04 기본 설치 시 `noble-updates`가 누락되어 빌드 의존성 설치가 실패할 수 있습니다.

```bash
sudo tee -a /etc/apt/sources.list.d/ubuntu.sources << 'EOF'

Types: deb
URIs: http://ports.ubuntu.com/ubuntu-ports/
Suites: noble-updates
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

sudo apt update
```

## 4단계: 빌드 의존성 설치

```bash
sudo apt install -y git build-essential cmake libdbus-1-dev libsystemd-dev python3-pip
```

> 만약 `held broken packages` 에러가 발생하면 `sudo apt upgrade -y` 후 재시도

## 5단계: ot-br-posix 빌드 및 설치

```bash
cd ~
git clone https://github.com/openthread/ot-br-posix.git
cd ot-br-posix

# 의존성 설치 (sudo 권한 필요)
./script/bootstrap

# 빌드 및 설치 (wlan0 = Wi-Fi 인터페이스)
INFRA_IF_NAME=wlan0 ./script/setup
```

> 빌드에 약 15~20분 소요됩니다 (Raspberry Pi 4 기준)

## 6단계: REST API 외부 접근 허용

기본값은 `127.0.0.1`만 바인딩되어 HA에서 일부 기능이 제한됩니다.
`0.0.0.0`으로 변경합니다:

```bash
sudo sed -i 's|trel://wlan0"|trel://wlan0 --rest-listen-address 0.0.0.0"|' /etc/default/otbr-agent
```

변경 후 파일 내용 확인:

```bash
cat /etc/default/otbr-agent
# 아래처럼 되어야 합니다:
# OTBR_AGENT_OPTS="-I wpan0 -B wlan0 spinel+hdlc+uart:///dev/ttyACM0 trel://wlan0 --rest-listen-address 0.0.0.0"
```

## 7단계: OTBR 서비스 시작

```bash
sudo systemctl enable otbr-agent
sudo systemctl start otbr-agent

# 상태 확인
systemctl status otbr-agent
```

## 8단계: Thread 네트워크 초기화

```bash
sudo ot-ctl dataset init new
sudo ot-ctl dataset channel 15
sudo ot-ctl dataset commit active
sudo ot-ctl ifconfig up
sudo ot-ctl thread start
```

약 10~15초 후 leader 상태 확인:

```bash
sudo ot-ctl state
# 출력: leader

sudo ot-ctl dataset active | grep 'Network Name'
# 출력 예: Network Name: OpenThread-xxxx
```

REST API 동작 확인:

```bash
curl http://127.0.0.1:8081/node/state
# 출력: "leader"
```

## 9단계: HA에서 기존 OTBR/Thread 통합 제거 및 재등록

기존 Docker OTBR 기반 통합이 남아있으면 로컬 OTBR과 충돌합니다.

### 9-1. HA 중지 후 설정 정리

```bash
docker stop homeassistant_core

# 기존 OTBR, Thread 통합 항목 제거
docker run --rm -v ~/matterhub-install/config:/config \
  ghcr.io/home-assistant/home-assistant:stable python3 -c '
import json

with open("/config/.storage/core.config_entries", "r") as f:
    data = json.load(f)

remaining = []
for entry in data["data"]["entries"]:
    if entry["domain"] in ("otbr", "thread"):
        print(f"Removed: {entry[\"domain\"]}: {entry[\"title\"]}")
    else:
        remaining.append(entry)

data["data"]["entries"] = remaining

with open("/config/.storage/core.config_entries", "w") as f:
    json.dump(data, f, indent=2)

print("Done.")
'
```

### 9-2. Docker 전체 재시작

```bash
cd ~/matterhub-install
docker compose down
docker compose up -d
```

### 9-3. HA UI에서 OTBR 재등록

1. HA 웹 UI 접속 (`http://<장비IP>:8123`)
2. **설정 → 기기 및 서비스 → Open Thread Border Router 삭제**
3. **설정 → 기기 및 서비스 → 통합 추가**
4. **Open Thread Border Router** 검색 후 선택
5. URL 입력: `http://127.0.0.1:8081`
6. Thread 통합이 자동으로 함께 등록됩니다
7. Thread 자격증명 가져오기(가이드 참고)

---

## 검증

### OTBR 상태 확인

```bash
sudo ot-ctl state          # leader
curl http://<장비IP>:8081/node/state   # "leader"
```
---

## 참고: USB 포트가 다른 경우

Thread 동글이 `/dev/ttyACM1` 등 다른 포트에 연결된 경우:

```bash
# /etc/default/otbr-agent 에서 경로 변경
sudo sed -i 's|/dev/ttyACM0|/dev/ttyACM1|' /etc/default/otbr-agent
sudo systemctl restart otbr-agent
```
