# 라즈베리파이 반복 설치 가이드 (동일 구성 재현)

## 1. 목적

이 문서는 새 라즈베리파이에 현재 운영 장비와 동일한 구성을 반복 적용하기 위한 실행 절차를 정의한다.

동일 구성 범위:

- systemd 기반 서비스 실행 (`matterhub-api`, `matterhub-mqtt`, `matterhub-update-agent`, `matterhub-support-tunnel` 등)
- Wi-Fi 설정 Web UI + AP 복구 모드 + 롤백 로직
- 로컬 mDNS 호스트명(`matterhub-setup-whatsmatter.local`) 접속 경로
- reverse SSH tunnel 연동
- reverse tunnel only 하드닝 + 로컬 콘솔 비노출 하드닝(PAM + UI/TTY 마스킹)
- `openssh-server` 설치 및 `ssh` 서비스 자동 시작
- relay host key(`known_hosts`) 사전 등록으로 로그인 전 tunnel 안정성 확보

## 2. 사전 준비

- OS: Ubuntu 24.04 LTS (Raspberry Pi)
- sudo 가능한 계정
- 인터넷 연결(최초 설치 시)
- 운영자 키 파일:
  - `~/.ssh/matterhub-relay-operator-key.pem`

## 3. 최초 1회 설치 명령

라즈베리파이 Linux 셸에서 아래를 순서대로 실행한다.

```bash
sudo apt update
sudo apt install -y git

cd ~/Desktop
git clone -b konai/20260211-v1.1 https://github.com/JayMon0327/matterhub-flask.git matterhub
cd ~/Desktop/matterhub

RELAY_HUB_ACCESS_PUBKEY="$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 'cat /home/ec2-user/.ssh/hub_access_ed25519.pub')"

bash device_config/setup_initial_device.sh \
  --setup-support-tunnel \
  --enable-support-tunnel-now \
  --support-host 3.38.126.167 \
  --support-user whatsmatter \
  --support-remote-port <장비별_고유_포트> \
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-reverse-tunnel-only \
  --harden-allow-inbound-port 8100 \
  --harden-allow-inbound-port 8123 \
  --harden-local-console-pam
```

## 4. 재설치/재적용(반복 실행) 명령

이미 동일 장비에서 한 번 설치를 마친 뒤 재적용하는 경우:

```bash
cd ~/Desktop/matterhub
git pull --ff-only origin konai/20260211-v1.1

RELAY_HUB_ACCESS_PUBKEY="$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 'cat /home/ec2-user/.ssh/hub_access_ed25519.pub')"

bash device_config/setup_initial_device.sh \
  --skip-os-packages \
  --setup-support-tunnel \
  --enable-support-tunnel-now \
  --support-host 3.38.126.167 \
  --support-user whatsmatter \
  --support-remote-port <장비별_고유_포트> \
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-reverse-tunnel-only \
  --harden-allow-inbound-port 8100 \
  --harden-allow-inbound-port 8123 \
  --harden-local-console-pam
```

`--skip-os-packages`는 OS 패키지 설치 단계를 건너뛰므로, 최초 설치에서는 사용하지 않는다.

`--support-remote-port`는 장비마다 반드시 다르게 잡는다. 예: 1호기 `22608`, 2호기 `22609`, 3호기 `22610`.

`--harden-allow-inbound-port 8100 --harden-allow-inbound-port 8123`를 빼면 1호기와 달리 로컬 Wi-Fi 설정 페이지와 Home Assistant 접근이 막힌다.

현재 통합 스크립트는 기본적으로 아래도 같이 맞춘다.

- `WIFI_COUNTRY_CODE=KR`
- `WIFI_AP_CONFLICT_SERVICES=named.service`

즉, 신규 장비는 별도 수동 작업 없이 Wi-Fi 국가코드 고정과 AP 모드 충돌 서비스 제어까지 같이 적용된다.

## 5. 설치 직후 필수 후속 작업

초기 설치만으로는 1호기와 완전히 같지 않다. 아래 후속 작업까지 끝나야 parity가 맞는다.

### 5.1 matterhub_id 발급

```bash
cd ~/Desktop/matterhub
venv/bin/python3 run_provision.py
```

성공하면 `.env`에 `matterhub_id="..."`가 저장된다.

### 5.2 MQTT 재시작

```bash
sudo systemctl restart matterhub-mqtt.service
```

재시작 후 기대 로그:

- `matterhub_id 로드됨: <발급값>`
- `[MQTT][SUBSCRIBE] complete total=2 success=2 failed=0 status=success`

### 5.3 relay 등록

장비 공개키와 `hub_id`, `remote_port`를 relay에 등록해야 `j <hub_id>`가 동작한다.

장비 셸에서 공개키 확인:

```bash
cat /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub
```

운영자 PC에서 위 공개키 한 줄을 `/tmp/matterhub_support_tunnel_ed25519.pub`로 저장한 뒤:

```bash
cd /path/to/local/matterhub-flask
bash device_config/register_hub_on_relay.sh \
  --relay-host 3.38.126.167 \
  --relay-port 443 \
  --relay-user ec2-user \
  --relay-key ~/.ssh/matterhub-relay-operator-key.pem \
  --hub-id <matterhub_id> \
  --remote-port <장비별_고유_포트> \
  --hub-pubkey /tmp/matterhub_support_tunnel_ed25519.pub \
  --device-user whatsmatter
```

등록 후 relay에서 검증:

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
j <matterhub_id>
```

## 6. 설치 후 검증

```bash
systemctl is-active matterhub-api.service
systemctl is-active matterhub-mqtt.service
systemctl is-active matterhub-update-agent.service
systemctl is-active matterhub-support-tunnel.service
systemctl is-active ssh
```

모두 `active`가 기대값이다.

추가 점검:

```bash
journalctl -u matterhub-api.service -n 50 --no-pager
journalctl -u matterhub-mqtt.service -n 50 --no-pager
journalctl -u matterhub-update-agent.service -n 50 --no-pager
journalctl -u matterhub-support-tunnel.service -n 50 --no-pager
```

Wi-Fi 설정 페이지:

- 권장: `http://matterhub-setup-whatsmatter.local:8100/local/admin/network`
- 일반 모드: `http://<라즈베리파이_IP>:8100/local/admin/network`
- AP 모드: `http://10.42.0.1:8100/local/admin/network`

`.local` 접속이 안 되는 단말/망에서는 기존 IP 또는 AP 주소를 사용한다.

## 7. 2호기 적용에서 실제 확인된 차이와 보정

- 차이 1: `--harden-reverse-tunnel-only`만 적용하면 `8100/8123` inbound가 막혀 1호기와 달라진다.
  - 보정: 설치 명령에 `--harden-allow-inbound-port 8100 --harden-allow-inbound-port 8123`를 반드시 포함한다.
- 차이 2: support tunnel은 설치 직후 바로 살아나지 않을 수 있다.
  - 원인: relay `authorized_keys`와 `hubs.map`에 장비 공개키/매핑이 아직 없기 때문이다.
  - 보정: `register_hub_on_relay.sh` 단계까지 완료한다.
- 차이 3: `matterhub_id`는 초기 설치만으로 자동 반영되지 않을 수 있다.
  - 보정: `venv/bin/python3 run_provision.py` 실행 후 `matterhub-mqtt.service`를 재시작한다.
- 차이 4: mDNS hostname만 바꾸고 `/etc/hosts`를 갱신하지 않으면 `sudo: unable to resolve host ...` 경고가 생긴다.
  - 보정: `setup_local_hostname_mdns.sh`가 `/etc/hosts`의 `127.0.1.1` 엔트리까지 같이 관리하도록 수정했다.

## 8. 업데이트 번들 적용 (운영 중 버전 교체)

### 6.1 수동 적용

```bash
cd ~/Desktop/matterhub
bash device_config/apply_update_bundle.sh \
  --bundle /tmp/matterhub-update-1.2.3.tar.gz \
  --project-root ~/Desktop/matterhub \
  --healthcheck-cmd "systemctl is-active matterhub-api.service matterhub-mqtt.service matterhub-rule-engine.service matterhub-notifier.service"
```

### 6.2 update-agent 자동 적용

`matterhub-update-agent.service`는 기본으로 `update/inbox/*.tar.gz`를 감시한다.

```bash
cd ~/Desktop/matterhub
mkdir -p update/inbox
cp /tmp/matterhub-update-1.2.3.tar.gz update/inbox/
systemctl status matterhub-update-agent.service --no-pager
journalctl -u matterhub-update-agent.service -n 50 --no-pager
```

기본 검증 정책:

- `UPDATE_AGENT_REQUIRE_MANIFEST=1`
- `UPDATE_AGENT_ALLOWED_BUNDLE_TYPES=matterhub-runtime,matterhub-update`
- `UPDATE_AGENT_REQUIRE_SHA256=0` (필요 시 1로 강화)

SHA256 검증까지 강제하려면:

```bash
cd ~/Desktop/matterhub
bash device_config/setup_initial_device.sh \
  --update-agent-require-sha256 1 \
  --update-agent-poll-seconds 15
```

이 경우 `update/inbox/<bundle>.tar.gz.sha256` 사이드카 파일도 함께 넣어야 한다.

## 9. 실행파일 전용 납품 흐름 (Git 비의존 운영)

빌드 서버(개발 PC)에서:

```bash
cd /path/to/matterhub-flask
bash device_config/build_runtime_binaries.sh
bash device_config/build_runtime_bundle.sh \
  --output-bundle dist/matterhub-runtime-1.2.3.tar.gz
```

라즈베리파이에서(운영 설치):

```bash
bash device_config/install_runtime_bundle.sh \
  --bundle /tmp/matterhub-runtime-1.2.3.tar.gz \
  --runtime-root /opt/matterhub \
  --run-user whatsmatter
```

위 경로는 `.py` 원본 없이 runtime bundle만 설치하는 절차다.

## 10. 선택 옵션 (필요 시)

AP 기본값을 변경하고 싶다면 설치 명령에 옵션을 추가한다.

```bash
--wifi-ap-ssid "Matterhub-Setup-WhatsMatter"
--wifi-ap-password "00000000"
--wifi-ap-ipv4-cidr "10.42.0.1/24"
--local-hostname "matterhub-setup-whatsmatter"
--local-service-name "MatterHub Wi-Fi Setup"
```

## 11. 참고

- [납품 및 운영 런북](./delivery-runbook.md)
- [Wi-Fi 설정 방법](../network/wifi-setup-guide.md)
- [Reverse Tunnel Only 하드닝 가이드](./reverse-tunnel-only-hardening.md)
