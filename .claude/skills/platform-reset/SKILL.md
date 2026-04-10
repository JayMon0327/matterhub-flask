---
name: platform-reset
description: 라즈베리파이 플랫폼 runtime state 초기화 (OTBR Thread 네트워크 재생성 + Matter Server fabric 리셋 + HA OTBR/Matter 통합 재등록). 이미 설치된 장비에서 커미셔닝 반복 실패/fabric 오염/Thread 네트워크 꼬임 복구 시 사용.
disable-model-invocation: true
argument-hint: "[장비IP] [SSH_USER] [SSH_PW]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# 스킬: 플랫폼 런타임 state 초기화 (platform-reset)

이미 `/platform-install` 또는 `/platform-activate`로 설치 완료된 장비에서 **바이너리/OS는 그대로 두고 runtime state만 초기화**한다. OTBR 바이너리 재빌드, Docker 재설치, avahi/iptables 재설정은 하지 않는다.

**사용 케이스**:
- Matter 기기 커미셔닝 반복 실패 (특히 `Failed Device Attestation`, `Error on commissioning step 'AttestationRevocationCheck'`)
- Matter Server fabric 파일 오염 (예: 152KB의 `<compressed_fabric_id>.json` 잔존)
- Thread 네트워크(`OpenThread-XXXX`)가 stale한 dataset(`activeTimestamp: 1`, preferred_ba 불일치 등)로 꼬임
- HA `otbr/matter/thread` 통합이 `setup_error` 또는 엔트리 중복 상태

> **전제**: OTBR + HA + Matter Server가 이미 설치되어 있고, SSH 접근 가능하며, `/opt/matterhub/app/.env`에 `hass_token`이 존재. 클린 OS 상태라면 대신 `/platform-install`을 사용한다.

> **네트워크 주의**: 이 스킬은 `wlan0` 기준 설정을 유지한다 (`-B wlan0`, `--primary-interface wlan0`). eth0가 동시 연결된 환경이고 사내망에 SSL/HTTPS 프록시가 있는 경우, 리셋 후 커미셔닝 시 Matter attestation 외부 HTTPS가 default route 경유 프록시를 타서 MITM으로 인식되어 실패한다. **리셋 후 커미셔닝 테스트는 eth0 물리 제거 또는 프록시 예외 처리된 망에서 진행**할 것.

## 대상

- Raspberry Pi 5, Ubuntu Server 24.04 LTS (aarch64)
- OTBR + HA + Matter Server 이미 설치 상태

## 사전 입력 (스킬 시작 시 AskUserQuestion으로 수집)

| 변수 | 설명 | 예시 |
|------|------|------|
| `HOST_IP` | 장비 IP | 192.168.43.244 |
| `SSH_USER` | SSH 사용자명 | whatsmatter |
| `SSH_PW` | SSH/sudo 비밀번호 | (사용자 입력) |

> `HA_TOKEN`은 `/opt/matterhub/app/.env`의 `hass_token`에서 스킬 내에서 자동 추출한다. 없으면 사용자에게 별도 요청.

## 실행 절차

아래 순서대로 Pi에 SSH 접속하여 명령을 실행한다. **expect를 사용**한다 (sshpass는 비밀번호에 `!` 등 특수문자 포함 시 동작 안 함).

> **SSH 접속 전**: `ssh-keygen -R $HOST_IP && ssh-keyscan -H $HOST_IP >> ~/.ssh/known_hosts`

> **expect + sudo 주의**: heredoc + `sudo -S` 조합은 stdin 점유로 패스워드 전달 실패한다. Python/복잡한 명령은 파일로 저장 후 `sudo python3 /tmp/wipe_*.py` 로 실행.

---

### R-0. 사전 확인 + 백업 (롤백용, 필수)

```bash
# 장비 상태 기본 체크
systemctl is-active otbr-agent
docker ps --format "{{.Names}}" | grep -E "homeassistant_core|matter-server"
# 둘 다 active/실행 중이어야 안전하게 리셋 가능

# 백업 디렉토리 (/root 에 타임스탬프)
BACKUP=/root/platform_reset_backup_$(date +%Y%m%d_%H%M%S)
sudo mkdir -p $BACKUP

# Matter Server data 전체 백업 (chip.json, fabric 파일들, credentials)
sudo cp -a /home/$SSH_USER/docker/matter-server/data $BACKUP/matter_data

# HA .storage 전체 백업
sudo cp -a /home/$SSH_USER/matterhub-install/config/.storage $BACKUP/ha_storage

# OTBR active dataset TLV 백업 (롤백 또는 기존 Thread 네트워크 이름 복원 시 사용)
sudo ot-ctl dataset active -x | sudo tee $BACKUP/otbr_dataset_tlv.txt

# 확인
sudo ls -la $BACKUP/
```

---

### R-1. 서비스 정지 + 컨테이너 완전 삭제

```bash
cd /home/$SSH_USER/matterhub-install

# 컨테이너를 stop 아니라 down 으로 완전 삭제
docker compose down

# 잔존 컨테이너 강제 제거 (compose 외부에서 생성된 경우 대비)
docker rm -f homeassistant_core matter-server 2>/dev/null || true

# 확인: 둘 다 없어야 함
docker ps -a --format "{{.Names}}" | grep -E "homeassistant_core|matter-server" && echo "!!! 컨테이너 잔존" || echo "OK: 컨테이너 전부 삭제됨"

# OTBR 정지
sudo systemctl stop otbr-agent
systemctl is-active otbr-agent || echo "OK: otbr-agent stopped"
```

> **`docker compose stop` 아니라 `down`**: stop만 하면 컨테이너가 남아있어서 fabric bind mount의 변경이 반영되지 않는 경우가 있다. 완전히 삭제한 후 R-4에서 재생성해야 fresh fabric 로드가 보장된다.

---

### R-2. 런타임 state 삭제

#### R-2-1. Matter Server fabric state

```bash
# fabric 설정/카운터/저장 파일 모두 삭제
# credentials/ (PAA 인증서) 는 보존
sudo rm -f /home/$SSH_USER/docker/matter-server/data/chip.json
sudo rm -f /home/$SSH_USER/docker/matter-server/data/chip_counters.ini
sudo rm -f /home/$SSH_USER/docker/matter-server/data/chip_counters.ini-*
sudo rm -f /home/$SSH_USER/docker/matter-server/data/chip_config.ini
sudo rm -f /home/$SSH_USER/docker/matter-server/data/chip_factory.ini
# fabric/node 저장 JSON 파일 (예: 1710124163950804317.json — 152KB 크기로 잔존하는 경우 원인)
sudo rm -f /home/$SSH_USER/docker/matter-server/data/*.json
sudo rm -f /home/$SSH_USER/docker/matter-server/data/*.json.backup

# 확인: credentials/ 만 남아야 함
ls /home/$SSH_USER/docker/matter-server/data/
# 기대: credentials
```

> **`*.json` 와일드카드 삭제는 필수**: `<compressed_fabric_id>.json` 형태의 파일이 이전 커미셔닝 fabric/node 데이터를 담고 있어 그대로 두면 리셋 후에도 stale 상태 유지.

#### R-2-2. HA thread.datasets

```bash
sudo rm -f /home/$SSH_USER/matterhub-install/config/.storage/thread.datasets
```

#### R-2-3. HA `.storage/` 중 otbr/matter/thread 관련 엔트리 제거

heredoc + `sudo -S` 조합을 피하기 위해 Python 스크립트를 먼저 파일로 scp 후 실행한다.

**먼저 로컬에서 3개 Python 스크립트 작성 후 scp**:

`/tmp/wipe_config_entries.py`:
```python
#!/usr/bin/env python3
import json, shutil

p = '/home/SSH_USER_PLACEHOLDER/matterhub-install/config/.storage/core.config_entries'
shutil.copy(p, p + '.bak_reset')
d = json.load(open(p))
before = len(d['data']['entries'])
removed = [(e['domain'], e.get('title')) for e in d['data']['entries'] if e.get('domain') in ('otbr','matter','thread')]
d['data']['entries'] = [e for e in d['data']['entries'] if e.get('domain') not in ('otbr','matter','thread')]
json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)
print(f"entries: {before} -> {len(d['data']['entries'])}")
print(f"removed: {removed}")
```

`/tmp/wipe_device_registry.py`:
```python
#!/usr/bin/env python3
import json, shutil

p = '/home/SSH_USER_PLACEHOLDER/matterhub-install/config/.storage/core.device_registry'
shutil.copy(p, p + '.bak_reset')
d = json.load(open(p))
before = len(d['data']['devices'])
removed = []
def keep(dev):
    ids = dev.get('identifiers') or []
    is_target = any(i and i[0] in ('otbr','matter','thread') for i in ids)
    if is_target:
        removed.append(dev.get('name') or str(ids[:1]))
    return not is_target
d['data']['devices'] = [dev for dev in d['data']['devices'] if keep(dev)]
json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)
print(f"devices: {before} -> {len(d['data']['devices'])}")
if removed: print(f"removed: {removed}")
```

`/tmp/wipe_entity_registry.py`:
```python
#!/usr/bin/env python3
import json, shutil

p = '/home/SSH_USER_PLACEHOLDER/matterhub-install/config/.storage/core.entity_registry'
try:
    shutil.copy(p, p + '.bak_reset')
    d = json.load(open(p))
    before = len(d['data']['entities'])
    d['data']['entities'] = [e for e in d['data']['entities'] if e.get('platform') not in ('matter','otbr','thread')]
    json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)
    print(f"entities: {before} -> {len(d['data']['entities'])}")
except FileNotFoundError:
    print("core.entity_registry not found, skipping")
```

> 스킬 실행 시 `SSH_USER_PLACEHOLDER`를 실제 `$SSH_USER` 값으로 `sed` 치환 후 scp 한다.

**Pi에서 실행**:
```bash
# scp로 /tmp에 올려두고
sudo python3 /tmp/wipe_config_entries.py
sudo python3 /tmp/wipe_device_registry.py
sudo python3 /tmp/wipe_entity_registry.py

# 확인
grep -oE '"domain":\s*"[^"]*"' /home/$SSH_USER/matterhub-install/config/.storage/core.config_entries | sort -u | grep -E "otbr|matter|thread" && echo "!!! still has entries" || echo "OK: otbr/matter/thread 모두 제거됨"
```

---

### R-3. OTBR dataset 리셋 + 새 Thread 네트워크 (wlan0 기준)

```bash
# OTBR 재시작
sudo systemctl start otbr-agent
sleep 3
systemctl is-active otbr-agent

# 기존 dataset 정리 (platform-activate 2-1a 패턴)
sudo ot-ctl thread stop
sudo ot-ctl ifconfig down
sudo ot-ctl dataset clear

# 새 Thread 네트워크 (채널 15)
sudo ot-ctl dataset init new
sudo ot-ctl dataset channel 15
sudo ot-ctl dataset commit active
sudo ot-ctl ifconfig up
sudo ot-ctl thread start

# 안정화 대기 + 확인
sleep 15
sudo ot-ctl state                                    # leader
sudo ot-ctl channel                                  # 15
sudo ot-ctl dataset active | grep "Network Name"     # OpenThread-XXXX (새 이름)
curl -s http://127.0.0.1:8081/node/state             # "leader"
curl -s http://127.0.0.1:8081/node/ba-id             # Border Agent ID

# wlan0 바인딩 재확인 (eth0 존재 여부 무관)
sudo ss -lunp | grep 5353 | grep otbr-agent
# 기대: 0.0.0.0%wlan0:5353 (otbr-agent)
```

> `thread stop` + `ifconfig down` 없이 `dataset init new` 바로 하면 기존 dataset이 잔존해서 새 네트워크가 적용되지 않는다.

---

### R-4. 컨테이너 재생성 + HA 통합 재등록

```bash
# 컨테이너 새로 생성
cd /home/$SSH_USER/matterhub-install
docker compose up -d

# 확인: CREATED 시간이 방금이어야 함
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"

# HA 부팅 대기 (최대 120초)
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8123 || echo "000")
  if [ "$code" = "200" ]; then
    echo "HA ready after ${i}x2s"
    break
  fi
  sleep 2
done

# matter-server 초기화 확인 (primary interface = wlan0 인지)
docker logs matter-server --tail 50 2>&1 | grep -E "primary interface|initialized"
# 기대:
#   Using 'wlan0' as primary interface (for link-local addresses)
#   Matter Server successfully initialized.
```

**HA OTBR/Matter 통합 등록** (기존 `hass_token` 재사용):

```bash
HA_TOKEN=$(grep hass_token /opt/matterhub/app/.env | cut -d= -f2- | tr -d '"')

# OTBR 통합 등록
FLOW=$(curl -s -X POST http://127.0.0.1:8123/api/config/config_entries/flow \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"handler":"otbr","show_advanced_options":false}')
FLOW_ID=$(echo "$FLOW" | python3 -c "import sys,json; print(json.load(sys.stdin)['flow_id'])")
curl -s -X POST "http://127.0.0.1:8123/api/config/config_entries/flow/$FLOW_ID" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"url":"http://127.0.0.1:8081"}' | python3 -m json.tool
# 응답에 "type":"create_entry" 및 "state":"loaded" 확인

# Matter 통합 등록
FLOW=$(curl -s -X POST http://127.0.0.1:8123/api/config/config_entries/flow \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"handler":"matter","show_advanced_options":false}')
FLOW_ID=$(echo "$FLOW" | python3 -c "import sys,json; print(json.load(sys.stdin)['flow_id'])")
curl -s -X POST "http://127.0.0.1:8123/api/config/config_entries/flow/$FLOW_ID" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -d '{"url":"ws://127.0.0.1:5580/ws"}' | python3 -m json.tool
# 응답에 "type":"create_entry" 및 "state":"loaded" 확인
```

> HA UI에서 하려면: **설정 → 기기 및 서비스 → 통합 추가 → Open Thread Border Router → URL: `http://127.0.0.1:8081`** 후 동일하게 **Matter (BETA) → URL: `ws://127.0.0.1:5580/ws`**.

---

### R-5. 검증

```bash
HA_TOKEN=$(grep hass_token /opt/matterhub/app/.env | cut -d= -f2- | tr -d '"')

# R-5-1. 서비스 건강
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "homeassistant|matter-server"
systemctl is-active otbr-agent
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8123
# 기대: 200

# R-5-2. 3개 통합 모두 loaded
curl -s http://127.0.0.1:8123/api/config/config_entries/entry \
  -H "Authorization: Bearer $HA_TOKEN" | \
  python3 -c "import sys,json; [print(f\"{e['domain']:8} {e['state']}\") for e in json.load(sys.stdin) if e['domain'] in ('otbr','thread','matter')]"
# 기대:
#   thread   loaded
#   otbr     loaded
#   matter   loaded

# R-5-3. _meshcop._udp.local mDNS 응답 (wlan0 IP로부터)
python3 << 'PYEOF'
import socket, struct, subprocess
def q(n):
    h=struct.pack('!HHHHHH',0,0,1,0,0,0)
    parts=n.split('.')
    nm=b''.join(bytes([len(p)])+p.encode() for p in parts)+b'\x00'
    return h+nm+struct.pack('!HH',12,1)
r=subprocess.run(['ip','-4','-br','addr','show','wlan0'],capture_output=True,text=True)
wlan0_ip=None
for p in r.stdout.split():
    if '/' in p: wlan0_ip=p.split('/')[0]; break
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.settimeout(5)
if wlan0_ip:
    s.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_IF,socket.inet_aton(wlan0_ip))
    s.bind((wlan0_ip,0))
try:
    s.sendto(q('_meshcop._udp.local'),('224.0.0.251',5353))
    d,a=s.recvfrom(4096); print(f'{len(d)}B from {a}, meshcop={b"meshcop" in d}')
except socket.timeout:
    print('TIMEOUT')
PYEOF
# 기대: 400~600B 응답, meshcop=True

# R-5-4. Matter Server 상태 + fresh fabric 확인
/opt/matterhub/venv/bin/python3 << 'PYEOF'
import asyncio, json
try: import websockets
except ImportError:
    import subprocess; subprocess.run(['/opt/matterhub/venv/bin/pip','install','-q','websockets']); import websockets
async def main():
    async with websockets.connect("ws://127.0.0.1:5580/ws") as ws:
        r = json.loads(await asyncio.wait_for(ws.recv(), 5))
        print(f"fabric_id             = {r.get('fabric_id')}")
        print(f"compressed_fabric_id  = {r.get('compressed_fabric_id')}")
        print(f"sdk_version           = {r.get('sdk_version')}")
        await ws.send(json.dumps({"message_id":"1","command":"get_nodes"}))
        r = json.loads(await asyncio.wait_for(ws.recv(), 5))
        print(f"nodes_count           = {len(r.get('result',[]))}")
asyncio.run(main())
PYEOF
# 기대:
#   fabric_id = 1
#   compressed_fabric_id = <이전과 다른 값> (fresh fabric 증거)
#   nodes_count = 0

# R-5-5. OTBR 5353 wlan0 바인딩 재확인
sudo ss -lunp | grep 5353 | grep otbr-agent
# 기대: 0.0.0.0%wlan0:5353
```

---

### R-6. (선택) 커미셔닝 테스트

리셋 완료 후 실제 Matter 기기로 커미셔닝을 시도한다.

> **방화벽 주의**: eth0가 연결된 환경에서 default route가 eth0로 잡혀 있고 사내 HTTPS 프록시가 있는 경우, Matter attestation이 프록시 SSL MITM으로 실패한다 (`Failed Device Attestation`, `CHIP Error 0x00000020`). 이 경우 **eth0 케이블 물리 제거 후 재부팅** 또는 **프록시 예외 처리된 망으로 이전** 필요.

커미셔닝 시도 중 로그 수집:
```bash
docker logs -f --tail 0 --timestamps matter-server 2>&1 | tee /home/$SSH_USER/matter_commission_$(date +%H%M%S).log
```

성공 시 `Commissioning complete` + `Matter commissioning of Node ID N successful` 라인 확인.

OTBR child table에 새 노드가 나타나는지도 확인:
```bash
sudo ot-ctl child table
# 커미셔닝 성공 시 Matter 기기가 Child(C) 역할로 등장
```

---

## 알려진 이슈

| 이슈 | 원인 | 해결 |
|------|------|------|
| `docker compose stop` 만 하고 재시작 시 fabric 파일이 재생성 안 됨 | stopped 컨테이너의 runtime storage가 stale | R-1에서 반드시 `docker compose down` + `docker rm -f` |
| 리셋 후 OTBR `dataset init new` 가 이전 네트워크 이름 유지 | `thread stop` + `ifconfig down` 없이 init → 기존 dataset 잔존 | R-3에서 반드시 stop → down → clear → init 순서 |
| HA 통합 재등록 후 `loaded` 안 뜨고 `setup_error` | Matter Server 컨테이너가 아직 준비 안 됨 | HA 200 응답 후 추가 5초 대기 후 등록, 또는 `docker logs matter-server` 에서 `Matter Server successfully initialized` 확인 후 진행 |
| 리셋 + 커미셔닝 재시도 시 `Failed Device Attestation` (CHIP Error 0x00000020) | 외부 HTTPS가 사내 프록시(KONA-PROXY 등) 경유로 SSL MITM 당함 | 네트워크를 wlan0 (프록시 없는 망)로 고정. eth0 있으면 물리 제거 후 재부팅. 또는 프록시에서 `*.dcl.csa-iot.org` 및 PAA/OCSP 엔드포인트 예외 처리 |
| R-5-4에서 `nodes_count=1` 로 나오며 이전 fabric 잔재 | R-2-1에서 `*.json` 미삭제 (예: 152KB 크기의 `<compressed_fabric_id>.json`) | R-2-1에서 `*.json` 와일드카드 삭제 포함 확인 |
| HA `thread.datasets` 재생성 후 `preferred_dataset: null` | OTBR 통합 등록 시 auto fill이지만 null로 유지되는 경우 있음 | 정상. Matter 커미셔닝에는 영향 없음 (HA 앱 Thread credentials sync 기능만 영향) |
| `heredoc + sudo -S` 로 Python 실행 시 패스워드 전달 실패 | heredoc stdin 점유로 `-S`가 stdin 읽기 실패 | Python 코드를 파일로 분리하여 `sudo python3 /tmp/script.py` |
| expect + sudo + tail/pipe 조합 실패 | tail 버퍼링으로 sudo 프롬프트 누락 | sudo 명령은 파이프 없이 단독 실행, 스크립트 파일 사용 |
| 리셋 후에도 동일한 커미셔닝 에러 반복 | 원인이 runtime state 오염이 아니라 외부 요인 (방화벽, RCP 펌웨어, 기기 자체 fabric 잔재) | R-6 로그 분석 후 다른 방향 진단. 이 스킬의 한계 |

---

## 롤백

R-6 테스트 실패 또는 예상과 다른 결과가 나올 경우, R-0 백업으로 복원:

```bash
cd /home/$SSH_USER/matterhub-install
docker compose down
docker rm -f homeassistant_core matter-server 2>/dev/null || true
sudo systemctl stop otbr-agent

# R-0의 BACKUP 경로 사용 (실제 타임스탬프로 대체)
BACKUP=/root/platform_reset_backup_<YYYYMMDD_HHMMSS>

sudo rm -rf /home/$SSH_USER/docker/matter-server/data
sudo cp -a $BACKUP/matter_data /home/$SSH_USER/docker/matter-server/data

sudo rm -rf /home/$SSH_USER/matterhub-install/config/.storage
sudo cp -a $BACKUP/ha_storage /home/$SSH_USER/matterhub-install/config/.storage

sudo systemctl start otbr-agent
docker compose up -d
```

---

## 참고

- 전체 클린 OS 설치: `/platform-install`
- 베이스 이미지 활성화 (SD카드 복제 후): `/platform-activate`
- 베이스 이미지 생성: `/platform-base-image`
- 이 스킬은 **이미 설치된 장비의 런타임 초기화 전용**이다.
- wlan0 하드코딩은 의도적이다 (기존 스킬 3개와 동일한 기준). 유선랜만 있는 환경에서는 현재 지원하지 않는다.
