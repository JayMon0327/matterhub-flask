---
name: platform-activate
description: 베이스 이미지 SD카드 장착 후 장비별 활성화 (Thread 초기화 + HA/Matter 통합 등록).
disable-model-invocation: true
argument-hint: "[장비IP] [SSH_USER] [SSH_PW]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# 장비별 활성화 (베이스 이미지 SD카드 장착 후)

`/platform-base-image`로 생성한 SD카드를 장비에 장착한 후, 장비별 설정을 완료한다.

> 사전 조건: `/platform-base-image` 완료된 SD카드가 장착되어 있어야 한다.
> 전체 원스톱 설치는 `/platform-install` 참조.

## 대상

- Raspberry Pi 5, Ubuntu Server 24.04 LTS (aarch64)
- Thread RCP 모듈 (NRF52840, /dev/ttyACM0 또는 /dev/ttyACM1)

## 사전 입력 (스킬 시작 시 AskUserQuestion으로 수집)

| 변수 | 설명 | 예시 |
|------|------|------|
| `HOST_IP` | 장비 IP | 192.168.1.96 |
| `SSH_USER` | SSH 사용자명 | whatsmatter |
| `SSH_PW` | SSH/sudo 비밀번호 | (사용자 입력) |
| `HA_TOKEN` | HA Long-Lived Access Token (2-2 단계 전에 수집) | eyJhbG... |

> `HA_TOKEN`은 HA 초기 설정 완료 후에만 발급 가능하므로, 2-1 완료 후 사용자에게 별도 요청한다.

## 실행 절차

SSH 접속은 **expect**를 사용한다. `HOST_IP`, `SSH_USER`, `SSH_PW`를 사용.

---

### 2-0. 사전 확인

**SSH known_hosts 갱신** (같은 IP에 새 SD카드를 넣은 경우 호스트 키 충돌 방지):
```bash
ssh-keygen -R $HOST_IP
ssh-keyscan -H $HOST_IP >> ~/.ssh/known_hosts
```

장비별로 USB 포트가 다를 수 있으므로 먼저 확인한다.

```bash
# Thread RCP 모듈 포트 확인
ls /dev/ttyACM*

# ttyACM1인 경우 OTBR 설정 수정
sudo sed -i 's|/dev/ttyACM0|/dev/ttyACM1|' /etc/default/otbr-agent
```

베이스 이미지 검증:

```bash
# OTBR 빌드 확인
which ot-ctl && echo "OTBR_OK"
# Docker 이미지 확인
docker images | grep -E "home-assistant|matter-server" | wc -l   # 2
# 컨테이너 미실행 확인
docker ps -q | wc -l   # 0
```

**ip6tables 규칙 확인/복구** (베이스 이미지에서 영구화가 누락될 수 있음):

```bash
sudo ip6tables -L INPUT -n | grep -q 5353 || {
    sudo ip6tables -I INPUT -i wpan0 -p udp --dport 5353 -j DROP
    sudo ip6tables -I OUTPUT -o wpan0 -p udp --dport 5353 -j DROP
    sudo netfilter-persistent save 2>/dev/null || sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 > /dev/null
    echo "ip6tables 규칙 복구 완료"
}
```

> **주의**: 재부팅 시 Docker/OTBR가 ip6tables 체인을 flush하여 규칙이 소실될 수 있음.
> systemd 서비스(`matterhub-ip6tables-mdns.service`)로 영구화 권장. 상세: `docs/troubleshooting/2026년 04월 3주/wifi-matter-mdns-commissioning-failure.md`

---

### 병렬 구간: 2-1a OTBR 시작 + 2-1b Docker 컨테이너 기동

> **두 에이전트를 병렬로 실행**한다.
> - 에이전트 A: OTBR 서비스 시작 + Thread 네트워크 초기화
> - 에이전트 B: docker compose up -d (HA + Matter Server 기동)

#### 에이전트 A: 2-1a. OTBR 서비스 시작 + Thread 네트워크 초기화

> **중요**: Thread 초기화 전에 반드시 `thread stop` + `ifconfig down`으로 기존 상태를 정리해야 한다.
> OTBR 빌드 시 자동 시작된 기본 dataset(channel 11)이 남아 있으면 새 dataset이 적용되지 않는다.

```bash
sudo systemctl enable otbr-agent
sudo systemctl start otbr-agent

# 기존 상태 정리 (필수)
sudo ot-ctl thread stop
sudo ot-ctl ifconfig down

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

#### 에이전트 B: 2-1b. HA + Matter Server 컨테이너 기동

```bash
cd /home/$SSH_USER/matterhub-install
docker compose up -d
```

> 이미지는 베이스 이미지에서 이미 pull 완료. 첫 기동이므로 HA가 초기 설정 모드로 시작된다.

---

### 2-2. HA 초기 설정 + OTBR/Matter 통합 등록

> 에이전트 A, B 모두 완료된 후 순차 실행.

**HA 초기 설정 (브라우저 — 사용자 수동):**
1. `http://$HOST_IP:8123` 접속 → HA 초기 설정 완료 (계정 생성, 위치 설정 등)
2. 사용자 프로필 → 보안 → 장기 액세스 토큰 생성
3. 토큰을 `HA_TOKEN`으로 전달

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

# URL 제출
curl -s -X POST "http://127.0.0.1:8123/api/config/config_entries/flow/$FLOW_ID" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"url":"http://127.0.0.1:8081"}'
# "type":"create_entry" 응답 확인
```

**Matter 통합 등록:**

```bash
# config flow 시작
FLOW=$(curl -s -X POST http://127.0.0.1:8123/api/config/config_entries/flow \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"handler":"matter","show_advanced_options":false}')
FLOW_ID=$(echo $FLOW | python3 -c "import sys,json; print(json.load(sys.stdin)['flow_id'])")

# URL 제출
curl -s -X POST "http://127.0.0.1:8123/api/config/config_entries/flow/$FLOW_ID" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"url":"ws://127.0.0.1:5580/ws"}'
# "type":"create_entry" 응답 확인
```

또는 HA UI에서:
- **설정 → 기기 및 서비스 → 통합 추가 → Open Thread Border Router → URL: `http://127.0.0.1:8081`**
- **설정 → 기기 및 서비스 → 통합 추가 → Matter (BETA) → URL: `ws://127.0.0.1:5580/ws`**

---

### 2-3. 검증

> **메인 에이전트 + iot-stack-health-checker 에이전트를 병렬로 실행**한다.

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
grep "deny-interfaces" /etc/avahi/avahi-daemon.conf   # wpan0 포함
sudo ip6tables -L INPUT -n | grep 5353                 # wpan0 DROP

# HA에서 통합 확인
curl -s http://127.0.0.1:8123/api/config/config_entries/entry \
  -H "Authorization: Bearer $HA_TOKEN" | python3 -c \
  "import sys,json; [print(e['domain'], e['state']) for e in json.load(sys.stdin) if e['domain'] in ('otbr','thread','matter')]"
# thread loaded
# otbr loaded
# matter loaded
```

---

## 알려진 이슈

| 이슈 | 원인 | 해결 |
|------|------|------|
| HA OTBR 통합 반쪽 동작 (재설정 불가) | REST API가 127.0.0.1만 바인딩 | 베이스 이미지에서 `--rest-listen-address 0.0.0.0` 이미 적용 |
| WiFi Matter 커미셔닝 PASE 타임아웃 | link-local 라우팅 충돌 + mDNS 3중 경쟁 | 베이스 이미지에서 `--primary-interface wlan0` + mDNS 수정 이미 적용 |
| HA OTBR 통합 연결 실패 (192.168.x.x:8081) | HA와 OTBR이 같은 호스트이므로 `localhost:8081`만 유효 | URL을 `http://127.0.0.1:8081`로 등록 |
| wpan0 Device busy (재시작 시) | 이전 프로세스의 wpan0 잔존 | `sudo ip link delete wpan0` 후 `sudo systemctl restart otbr-agent` |
| OTBR CCA_FAILURE | 채널 간섭 (특히 13) | 채널 15로 변경 |
| /dev/ttyACM0 vs ttyACM1 | USB 포트 위치가 장비마다 다름 | 2-0에서 확인 후 `/etc/default/otbr-agent` 수정 |
| sshpass 접속 실패 | 비밀번호 `!` 특수문자 | expect 사용 |
| Thread 초기화 후 detached/channel 11 | OTBR 자동 시작 시 기본 dataset 적용 | `thread stop` + `ifconfig down` 후 dataset 재설정 |
| ip6tables 규칙 재부팅 후 소실 | Docker/OTBR가 부팅 시 체인 flush | systemd 서비스로 영구화 (`matterhub-ip6tables-mdns.service`) |
| SSH 호스트 키 충돌 | 같은 IP에 새 SD카드 | `ssh-keygen -R` + `ssh-keyscan` |
| Matter 등록 시 abort 응답 | OTBR 등록 시 자동 포함됨 | 정상 동작, 통합 목록에서 확인 |
| expect + sudo + tail 조합 실패 | tail 버퍼링으로 sudo 프롬프트 누락 | sudo 명령은 tail 없이 실행, 스크립트 파일 사용 권장 |
