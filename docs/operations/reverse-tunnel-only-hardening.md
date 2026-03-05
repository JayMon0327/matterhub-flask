# Reverse Tunnel Only 하드닝 가이드

## 1. 목적

이 문서는 라즈베리파이를 "reverse tunnel 접속만 허용" 상태로 잠그는 절차를 정리한다.

적용 결과:

- 장비 inbound SSH(22) 직접 접속 차단
- SSHD는 `127.0.0.1` 바인딩
- UFW 기본 정책: incoming deny, outgoing allow
- 운영자는 relay 경유(`j <hub_id>`)로만 접속

## 2. 실행 스크립트

- `device_config/harden_reverse_tunnel_only.sh`

필수 전제:

- `.env`에 `SUPPORT_TUNNEL_ENABLED=1`
- `.env`에 `SUPPORT_TUNNEL_HOST`, `SUPPORT_TUNNEL_USER`, `SUPPORT_TUNNEL_REMOTE_PORT` 존재
- `matterhub-support-tunnel.service` 활성 상태

## 3. 단독 적용

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/harden_reverse_tunnel_only.sh --run-user whatsmatter
```

inbound 예외 포트가 필요하면:

```bash
bash device_config/harden_reverse_tunnel_only.sh \
  --run-user whatsmatter \
  --allow-inbound-port 80 \
  --allow-inbound-port 443
```

## 4. 통합 설치 스크립트에서 같이 적용

```bash
cd /home/whatsmatter/Desktop/matterhub
RELAY_HUB_ACCESS_PUBKEY="$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 'cat /home/ec2-user/.ssh/hub_access_ed25519.pub')"
bash device_config/setup_initial_device.sh \
  --setup-support-tunnel \
  --support-host 3.38.126.167 \
  --support-user whatsmatter \
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-reverse-tunnel-only
```

## 5. 검증

장비에서:

```bash
systemctl status matterhub-support-tunnel.service --no-pager
sudo ufw status verbose
sudo sshd -t
```

운영자 PC에서:

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
j <hub_id>
```

## 6. 주의사항

- 하드닝 적용 후에는 장비 내부 IP로 직접 SSH 접속이 차단된다.
- relay 또는 key 설정이 잘못된 상태에서 적용하면 복구 작업이 어려워진다.
- 반드시 `--dry-run`으로 계획을 먼저 확인하고 적용한다.

## 7. 로컬 콘솔 로그인(PAM) 제한

물리 모니터/키보드 연결 시 로그인까지 제한하려면 아래 스크립트를 추가 적용한다.

- `device_config/harden_local_console_pam.sh`

실행:

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/harden_local_console_pam.sh --run-user whatsmatter
```

적용 내용:

- `/etc/pam.d/login` 에 `pam_access.so` 활성화
- `/etc/pam.d/gdm-password`, `/etc/pam.d/gdm-autologin` 에 `pam_access.so` 활성화 (파일 존재 시)
- `/etc/security/access.conf` 에 아래 정책 추가
  - `+:root:LOCAL`
  - `-:whatsmatter:LOCAL`
- `/etc/gdm3/custom.conf` 에서 자동로그인 비활성화 (`AutomaticLoginEnable=false`)

통합 설치에서 같이 적용:

```bash
bash device_config/setup_initial_device.sh \
  --setup-support-tunnel \
  --harden-reverse-tunnel-only \
  --harden-local-console-pam
```
