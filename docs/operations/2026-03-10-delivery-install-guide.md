# MatterHub 납품 설치 가이드

> **작성일**: 2026-03-10
> **기준 장비**: Device 1 (192.168.1.94)
> **브랜치**: `konai/20260211-v1.1`
> **원칙**: 이 문서는 Device 1 배포에서 **실제로 성공한 방법만** 기록한다. 자동화 스크립트의 expect/pip/DNS 이슈를 모두 우회한 수동 절차이다.

---

## 개요

- **목적**: MatterHub 납품용 패키징 및 설치 (코드보안: .pyc only)
- **대상**: Raspberry Pi (Ubuntu 24.04), Python 3.12
- **패키지 형식**: Debian `.deb` (arm64), 소스 모드 빌드 후 디바이스에서 .pyc 컴파일
- **서비스 구성** (6개 systemd 서비스):

| 서비스 | 설명 | 실행 유저 |
|--------|------|-----------|
| `matterhub-api` | Flask API (포트 8100) | whatsmatter |
| `matterhub-mqtt` | MQTT Worker (Konai 토픽) | whatsmatter |
| `matterhub-rule-engine` | Rule Engine | whatsmatter |
| `matterhub-notifier` | Notifier | whatsmatter |
| `matterhub-update-agent` | Update Agent | root |
| `matterhub-support-tunnel` | 리버스 SSH 터널 | whatsmatter |

---

## 사전 준비

### Mac 빌드 환경

```bash
brew install dpkg
python3 --version   # 버전 무관 (소스 모드 빌드)
```

### 필요 파일 확인

| 파일 | 위치 | 용도 |
|------|------|------|
| `.env` | 프로젝트 루트 | 환경변수 (matterhub_id, hass_token 등) |
| `konai_certificates/` | 프로젝트 루트 | Konai MQTT TLS 인증서 |
| Relay 운영자 키 | `~/.ssh/matterhub-relay-operator-key.pem` | 릴레이 서버 접속 |

### Pi 전제 조건

| 항목 | 값 |
|------|-----|
| OS | Ubuntu 24.04 LTS (aarch64) |
| Python | 3.12.x |
| 사용자 | whatsmatter |
| 기존 venv | `~/Desktop/matterhub/venv` 존재 (pip 실패 시 복사용) |

### 변수 설정 (이후 모든 명령에서 사용)

```bash
DEVICE_IP="192.168.1.94"
DEVICE_USER="whatsmatter"
```

---

## Step 1: .deb 패키지 빌드 (Mac에서)

```bash
cd /path/to/matterhub-flask
bash device_config/build_matterhub_deb.sh
```

빌드 결과물 확인:

```bash
ls -la dist/matterhub_*_arm64.deb
```

> **중요**: `--mode pyc` 옵션을 사용하지 않는다. Mac Python 3.11과 Pi Python 3.12의 바이트코드가 호환되지 않으므로, 기본값 `--mode source`로 빌드한다. postinst가 디바이스에서 .pyc 컴파일 + .py 삭제를 수행하도록 설계되어 있다.

postinst가 자동으로 수행하는 작업:
1. `matterhub` 시스템 유저 생성
2. venv 생성 + `pip install -r requirements.txt`
3. `.env` symlink 생성 (`/opt/matterhub/app/.env` -> `/etc/matterhub/matterhub.env`)
4. `.pyc` 컴파일 (`python -m compileall -q -b`)
5. `.py` 소스 삭제 (`__init__.py` 제외)
6. 런처 스크립트 확장자 `.py` -> `.pyc` 변경
7. systemd 서비스 enable
8. UFW 포트 허용 (8100, 8123)

> **현실**: pip install 단계에서 DNS 장애로 실패하므로, 아래 Step 2에서 수동 처리한다.

---

## Step 2: 파일 전송 및 설치 (수동 - expect가 불안정하므로)

> **주의**: `deploy_matterhub_deb.sh`와 `provision_device_full.sh`의 expect 패턴이 sudo 비밀번호 전달 시 실패한다. 수동 SCP + SSH로 진행한다.

### 2-1. .deb 전송

```bash
scp dist/matterhub_*_arm64.deb ${DEVICE_USER}@${DEVICE_IP}:/tmp/
```

### 2-2. SSH 접속 후 설치

```bash
ssh ${DEVICE_USER}@${DEVICE_IP}

# dpkg로 설치
sudo dpkg -i /tmp/matterhub_*.deb
```

> **예상 결과**: postinst의 `pip install` 단계에서 DNS/네트워크 이슈로 실패한다. 그러나 파일 자체는 `/opt/matterhub/app/`에 정상 설치된다.

### 2-3. pip install DNS 실패 시 기존 venv 복사

```bash
sudo cp -a ~/Desktop/matterhub/venv /opt/matterhub/venv
sudo chown -R whatsmatter:whatsmatter /opt/matterhub/venv
```

venv 정상 확인:

```bash
/opt/matterhub/venv/bin/python --version
# 출력: Python 3.12.3
```

### 2-4. dpkg postinst 실패 시 수동 처리

pip 실패로 postinst가 중단되었으므로, 남은 작업을 수동으로 수행한다:

```bash
# .pyc 컴파일 (-b: 같은 디렉터리에 .pyc 생성)
sudo /opt/matterhub/venv/bin/python -m compileall -q -b /opt/matterhub/app

# .py 소스 삭제 (__init__.py는 유지 - 패키지 임포트용)
sudo find /opt/matterhub/app -type f -name '*.py' ! -name '__init__.py' -delete

# __pycache__ 정리
sudo find /opt/matterhub/app -type d -name '__pycache__' -prune -exec rm -rf {} +

# macOS 리소스 포크 파일 삭제 (Mac tar가 자동 포함시킴)
sudo find /opt/matterhub/app -name "._*" -delete

# 런처 스크립트 확장자 수정 (.py -> .pyc)
for launcher in /opt/matterhub/bin/matterhub-*; do
  [ -f "$launcher" ] && sudo sed -i 's/\.py"/\.pyc"/g' "$launcher"
done

# .env symlink 생성
sudo ln -sf /etc/matterhub/matterhub.env /opt/matterhub/app/.env

# 소유권 복구
sudo chown -R whatsmatter:whatsmatter /opt/matterhub
```

### 2-5. dpkg 상태 수복

pip install 실패로 dpkg가 broken 상태에 남아있으면 이후 apt 명령이 실패한다:

```bash
# postinst에서 pip 라인을 스킵하도록 수정
sudo sed -i 's|/opt/matterhub/venv/bin/pip install|echo SKIP pip install #|g' \
  /var/lib/dpkg/info/matterhub.postinst

# dpkg 상태 정리
sudo dpkg --configure -a
```

---

## Step 3: systemd 서비스 설정

### 3-1. 서비스 파일 복사

`.deb` 설치 시 서비스 파일이 `/usr/lib/systemd/system/`에 설치된다. Ubuntu 24.04에서 `list-unit-files`에 표시되지 않는 경우가 있으므로 `/etc/systemd/system/`으로 복사한다:

```bash
sudo cp /usr/lib/systemd/system/matterhub-*.service /etc/systemd/system/
```

### 3-2. User 변경 (matterhub -> whatsmatter)

deb 패키지 기본값은 `User=matterhub`이다. 실제 디바이스 유저 `whatsmatter`로 변경한다:

```bash
sudo sed -i 's/User=matterhub/User=whatsmatter/g; s/Group=matterhub/Group=whatsmatter/g' \
  /etc/systemd/system/matterhub-*.service
```

> **참고**: `matterhub-update-agent`는 `User=root`이므로 위 sed 명령에 영향받지 않는다.

### 3-3. .env symlink 및 퍼미션

```bash
# symlink 확인 (2-4에서 이미 생성했을 수 있음)
ls -la /opt/matterhub/app/.env
# 결과: /opt/matterhub/app/.env -> /etc/matterhub/matterhub.env

# 없으면 생성
sudo ln -sf /etc/matterhub/matterhub.env /opt/matterhub/app/.env

# 퍼미션 설정 (664 - 660이면 일부 환경에서 읽기 실패)
sudo chown root:whatsmatter /etc/matterhub/matterhub.env
sudo chmod 664 /etc/matterhub/matterhub.env
```

### 3-4. 서비스 활성화 및 시작

```bash
sudo systemctl daemon-reload

# 전체 서비스 enable + start
sudo systemctl enable --now \
  matterhub-api.service \
  matterhub-mqtt.service \
  matterhub-rule-engine.service \
  matterhub-notifier.service \
  matterhub-update-agent.service
```

> `matterhub-support-tunnel`은 Step 6에서 별도로 설정 후 활성화한다.

---

## Step 4: 방화벽 (UFW)

```bash
sudo ufw allow 8100/tcp    # Flask API
sudo ufw allow 8123/tcp    # Home Assistant

# 확인
sudo ufw status
```

---

## Step 5: 환경설정 (.env)

### 5-1. matterhub_id 발급

claim provisioning이 이미 실행되었다면 `.env`에 `matterhub_id`가 설정되어 있다:

```bash
grep '^matterhub_id=' /etc/matterhub/matterhub.env
# 예시: matterhub_id=whatsmatter-nipa_SN-1773129896
```

설정되어 있지 않으면 프로비저닝을 실행한다:

```bash
sudo /opt/matterhub/venv/bin/python /opt/matterhub/app/run_provision.pyc --ensure --non-interactive
```

### 5-2. hass_token 설정 (HA Long-Lived Access Token)

1. 브라우저에서 `http://<DEVICE_IP>:8123` 접속
2. 좌측 하단 사용자 프로필 -> 보안 탭 -> "장기 액세스 토큰" 생성
3. API를 통해 설정 (사용자가 직접):

```bash
curl -X POST http://localhost:8100/api/settings \
  -H "Content-Type: application/json" \
  -d '{"hass_token": "<발급받은_토큰>"}'
```

또는 직접 .env 수정:

```bash
sudo sed -i "s/^hass_token=.*/hass_token=<발급받은_토큰>/" /etc/matterhub/matterhub.env
```

### 5-3. load_dotenv(dotenv_path='.env') 수정 (필수)

`.pyc` 환경에서 `load_dotenv()`는 `find_dotenv()` 스택 프레임 추적이 실패하여 `.env` 파일을 찾지 못한다. 반드시 `load_dotenv(dotenv_path='.env')`로 수정해야 한다.

해당 파일 목록:
- `libs/edit.py`
- `sub/notifier.py`
- `sub/ruleEngine.py`
- `sub/scheduler.py`
- `run_provision.py`

> **권장**: 빌드 전에 소스 코드에서 미리 수정해두면 배포 후 추가 작업이 불필요하다.

이미 `.pyc`로 배포된 경우 해당 파일만 재배포:

```bash
# Mac에서: 수정된 .py 파일을 Pi로 전송
scp libs/edit.py sub/notifier.py sub/ruleEngine.py sub/scheduler.py run_provision.py \
  ${DEVICE_USER}@${DEVICE_IP}:/tmp/

# Pi에서: 파일 교체 후 재컴파일
ssh ${DEVICE_USER}@${DEVICE_IP}

sudo cp /tmp/edit.py /opt/matterhub/app/libs/
sudo cp /tmp/notifier.py /tmp/ruleEngine.py /tmp/scheduler.py /opt/matterhub/app/sub/
sudo cp /tmp/run_provision.py /opt/matterhub/app/

# 재컴파일
sudo /opt/matterhub/venv/bin/python -m compileall -q -b \
  /opt/matterhub/app/libs/edit.py \
  /opt/matterhub/app/sub/notifier.py \
  /opt/matterhub/app/sub/ruleEngine.py \
  /opt/matterhub/app/sub/scheduler.py \
  /opt/matterhub/app/run_provision.py

# .py 삭제
sudo rm -f /opt/matterhub/app/libs/edit.py \
  /opt/matterhub/app/sub/notifier.py \
  /opt/matterhub/app/sub/ruleEngine.py \
  /opt/matterhub/app/sub/scheduler.py \
  /opt/matterhub/app/run_provision.py

# 서비스 재시작
sudo systemctl restart matterhub-api matterhub-mqtt matterhub-rule-engine matterhub-notifier
```

---

## Step 6: 리버스 SSH 터널

### 6-1. .env 키 경로 수정

deb 패키지 기본값은 `/home/matterhub/`이므로 실제 사용자 경로 `/home/whatsmatter/`로 변경한다:

```bash
sudo sed -i 's|/home/matterhub/|/home/whatsmatter/|g' /etc/matterhub/matterhub.env
```

### 6-2. 터널 우회 옵션 설정

```bash
# PREFLIGHT_TCP_CHECK: Python socket 기반 TCP 체크가 실패하므로 비활성화
grep -q '^PREFLIGHT_TCP_CHECK=' /etc/matterhub/matterhub.env \
  && sudo sed -i 's/^PREFLIGHT_TCP_CHECK=.*/PREFLIGHT_TCP_CHECK=0/' /etc/matterhub/matterhub.env \
  || echo 'PREFLIGHT_TCP_CHECK=0' | sudo tee -a /etc/matterhub/matterhub.env

# STRICT_HOST_KEY_CHECKING: 초기 연결 시 known_hosts 검증 스킵
grep -q '^SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING=' /etc/matterhub/matterhub.env \
  && sudo sed -i 's/^SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING=.*/SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING=0/' /etc/matterhub/matterhub.env \
  || echo 'SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING=0' | sudo tee -a /etc/matterhub/matterhub.env
```

### 6-3. setup_support_tunnel.sh 실행 (Pi에서)

```bash
sudo bash /opt/matterhub/device_config/setup_support_tunnel.sh \
  --host 3.38.126.167 \
  --user whatsmatter \
  --port 443 \
  --remote-port 22341 \
  --command ssh \
  --run-user whatsmatter \
  --device-user whatsmatter \
  --relay-operator-user ec2-user \
  --env-file /etc/matterhub/matterhub.env \
  --skip-install-unit \
  --enable-now
```

> **참고**: `--skip-install-unit` 사용 이유: 이미 /etc/systemd/system/에 서비스 파일이 있으므로 새로 렌더링하지 않고 기존 유닛을 재사용한다.

실행 완료 후 출력되는 **공개키**를 기록한다.

### 6-4. 터널 키 경로 확인

```bash
# 키가 /home/whatsmatter/.ssh/ 에 있는지 확인 (NOT /home/matterhub/.ssh/)
ls -la /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519
ls -la /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub
```

### 6-5. Relay에 공개키 등록 (Mac에서)

```bash
# 디바이스 공개키 가져오기
scp ${DEVICE_USER}@${DEVICE_IP}:/home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub /tmp/hub_tunnel_key.pub

# relay에 등록
bash device_config/register_hub_on_relay.sh \
  --relay-host 3.38.126.167 \
  --relay-port 443 \
  --relay-user ec2-user \
  --relay-key ~/.ssh/matterhub-relay-operator-key.pem \
  --hub-id "whatsmatter-nipa_SN-1773129896" \
  --remote-port 22341 \
  --hub-pubkey /tmp/hub_tunnel_key.pub \
  --device-user whatsmatter
```

> **주의**: 같은 공개키가 relay의 `authorized_keys`에 이미 다른 포트로 등록되어 있으면 중복 항목을 수동으로 제거해야 한다:
> ```bash
> ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
> sudo vi /home/whatsmatter/.ssh/authorized_keys
> # 중복된 키 라인 제거 (예: 22608 포트 항목)
> ```

### 6-6. 터널 서비스 시작 및 확인

```bash
# Pi에서
sudo systemctl enable --now matterhub-support-tunnel.service
sudo systemctl status matterhub-support-tunnel.service --no-pager
```

---

## Step 7: 검증

### 7-1. 전체 서비스 상태 확인 (Pi에서)

```bash
for svc in api mqtt rule-engine notifier update-agent support-tunnel; do
  echo -n "matterhub-${svc}: "
  systemctl is-active matterhub-${svc}.service
done
```

6개 서비스 모두 `active` 출력 확인.

### 7-2. Flask API 응답 확인

```bash
curl http://localhost:8100
```

### 7-3. MQTT Konai 토픽 확인

```bash
sudo journalctl -u matterhub-mqtt --no-pager -n 30 | grep -i "subscri"
```

구독 확인할 토픽 (예시):
- `update/delta/dev/<claim_id>/matter/<hub_short_id>`
- `update/reported/dev/<claim_id>/matter/<hub_short_id>`

### 7-4. 리버스 터널 접속 확인 (Mac에서)

```bash
# Relay 접속
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167

# 허브 접속 (relay 내에서)
j whatsmatter-nipa_SN-1773129896
```

### 7-5. 코드보안 확인 (Pi에서)

```bash
find /opt/matterhub/app -type f -name '*.py' ! -name '__init__.py' | wc -l
# 결과: 0 (.py 소스 파일 없음)
```

---

## 트러블슈팅

### load_dotenv() -> load_dotenv(dotenv_path='.env')

**증상**: 서비스가 환경변수를 읽지 못함, `.env` 파일을 찾지 못하는 에러
**원인**: `.pyc` 실행 시 `find_dotenv()`가 스택 프레임 기반 경로 추적에 실패
**해결**: 소스 코드에서 `load_dotenv()` -> `load_dotenv(dotenv_path='.env')` 수정 후 재컴파일 (Step 5-3 참조)

### macOS `._*` 리소스 포크 파일

**증상**: Python import 에러 또는 예상치 못한 파일 발견
**원인**: macOS에서 scp/tar 시 `._` 접두사 메타데이터 파일이 자동 포함됨
**해결**:

```bash
sudo find /opt/matterhub/app -name "._*" -delete
```

### Python 버전 불일치 (Mac 3.11 vs Pi 3.12)

**증상**: `.pyc` 파일이 로드되지 않음 (magic number 에러)
**원인**: Mac Python 3.11에서 컴파일한 `.pyc`를 Pi Python 3.12에서 실행
**해결**: `--mode source`로 빌드 후 디바이스에서 컴파일

```bash
# 빌드 시 (Mac)
bash device_config/build_matterhub_deb.sh   # 기본값 --mode source

# 디바이스에서 수동 컴파일
sudo /opt/matterhub/venv/bin/python -m compileall -q -b /opt/matterhub/app
sudo find /opt/matterhub/app -type f -name '*.py' ! -name '__init__.py' -delete
```

### DNS 불안정으로 pip install 실패

**증상**: postinst에서 `pip install -r requirements.txt` 타임아웃
**원인**: Pi Wi-Fi 환경에서 DNS 해석 실패, pypi.org resolve 불가
**해결**:

```bash
sudo cp -a ~/Desktop/matterhub/venv /opt/matterhub/venv
sudo chown -R whatsmatter:whatsmatter /opt/matterhub/venv
```

### dpkg 상태 수복

**증상**: `dpkg -l matterhub`에서 `iF` (install failed) 상태, `apt` 명령 실패
**원인**: postinst 스크립트 중간 실패
**해결**:

```bash
sudo sed -i 's|/opt/matterhub/venv/bin/pip install|echo SKIP pip install #|g' \
  /var/lib/dpkg/info/matterhub.postinst
sudo dpkg --configure -a
```

### SSH 터널 키 경로

**주의**: 터널 SSH 키는 `/home/whatsmatter/.ssh/`에 있어야 한다. `/home/matterhub/.ssh/`가 아니다 (matterhub는 `--shell /usr/sbin/nologin` 시스템 유저로 홈 디렉토리 구조가 다름).

```bash
ls -la /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519
```

### Relay authorized_keys 중복 포트

**증상**: 터널은 연결되나 `j` 접속 시 잘못된 포트로 라우팅됨
**원인**: relay의 `authorized_keys`에 같은 허브의 키가 여러 포트로 등록됨
**해결**: relay에서 이전 포트 항목을 수동 제거

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
sudo vi /home/whatsmatter/.ssh/authorized_keys
# 중복된 키 라인 제거
```

---

## 장비 정보

| 장비 | IP | matterhub_id | tunnel port | 상태 |
|------|-----|-------------|-------------|------|
| 1 | 192.168.1.94 | whatsmatter-nipa\_SN-1773129896 | 22341 | 완료 |
| 2 | TBD | TBD | TBD | 미착수 |

### Relay 서버

| 항목 | 값 |
|------|-----|
| Host | 3.38.126.167 |
| Port | 443 |
| User | ec2-user |
| Operator Key | `~/.ssh/matterhub-relay-operator-key.pem` |

---

## 소요 시간

장비 1 기준 약 2-3시간 소요 (문제 해결 포함). 본 가이드 순서대로 진행하면 약 30-40분 예상.
