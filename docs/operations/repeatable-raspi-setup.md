# 라즈베리파이 반복 설치 가이드 (동일 구성 재현)

## 1. 목적

이 문서는 새 라즈베리파이에 현재 운영 장비와 동일한 구성을 반복 적용하기 위한 실행 절차를 정의한다.

동일 구성 범위:

- systemd 기반 서비스 실행 (`matterhub-api`, `matterhub-mqtt`, `matterhub-support-tunnel` 등)
- Wi-Fi 설정 Web UI + AP 복구 모드 + 롤백 로직
- reverse SSH tunnel 연동
- reverse tunnel only 하드닝 + 로컬 콘솔 PAM 하드닝
- `openssh-server` 설치 및 `ssh` 서비스 자동 시작

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
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-reverse-tunnel-only \
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
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-reverse-tunnel-only \
  --harden-local-console-pam
```

`--skip-os-packages`는 OS 패키지 설치 단계를 건너뛰므로, 최초 설치에서는 사용하지 않는다.

## 5. 설치 후 검증

```bash
systemctl is-active matterhub-api.service
systemctl is-active matterhub-mqtt.service
systemctl is-active matterhub-support-tunnel.service
systemctl is-active ssh
```

모두 `active`가 기대값이다.

추가 점검:

```bash
journalctl -u matterhub-api.service -n 50 --no-pager
journalctl -u matterhub-mqtt.service -n 50 --no-pager
journalctl -u matterhub-support-tunnel.service -n 50 --no-pager
```

Wi-Fi 설정 페이지:

- 일반 모드: `http://<라즈베리파이_IP>:8100/local/admin/network`
- AP 모드: `http://10.42.0.1:8100/local/admin/network`

## 6. 선택 옵션 (필요 시)

AP 기본값을 변경하고 싶다면 설치 명령에 옵션을 추가한다.

```bash
--wifi-ap-ssid "Matterhub-Setup-WhatsMatter"
--wifi-ap-password "matterhub1234"
--wifi-ap-ipv4-cidr "10.42.0.1/24"
```

## 7. 참고

- [납품 및 운영 런북](./delivery-runbook.md)
- [Wi-Fi 설정 방법](../network/wifi-setup-guide.md)
- [Reverse Tunnel Only 하드닝 가이드](./reverse-tunnel-only-hardening.md)
