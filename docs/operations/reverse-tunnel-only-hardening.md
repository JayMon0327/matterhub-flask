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
  --allow-inbound-port 8100 \
  --allow-inbound-port 8123
```

현재 운영 기준에서 아래 두 포트는 영구 허용 대상이다.

- `8100/tcp`: MatterHub Wi-Fi 설정 Web UI
- `8123/tcp`: Home Assistant

운영 원칙:

- `8100/tcp`, `8123/tcp`는 reverse-tunnel-only 하드닝 이후에도 항상 유지한다.
- `22/tcp`(직접 SSH), `8110/tcp`는 유지보수 시에만 임시 허용하고 작업 종료 후 다시 닫는다.

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
  --harden-reverse-tunnel-only \
  --harden-allow-inbound-port 8100 \
  --harden-allow-inbound-port 8123
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

## 6. 임시 유지보수 포트 운영

direct SSH 또는 별도 로컬 서비스 접근이 잠깐 필요하면 reverse tunnel로 장비에 접속한 뒤 아래처럼 임시 예외를 추가한다.

임시 오픈:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 8110/tcp
sudo ufw status numbered
```

작업 종료 후 즉시 원복:

```bash
sudo ufw delete allow 22/tcp
sudo ufw delete allow 8110/tcp
sudo ufw status numbered
```

## 7. 주의사항

- 하드닝 적용 후에는 장비 내부 IP로 직접 SSH 접속이 차단된다.
- relay 또는 key 설정이 잘못된 상태에서 적용하면 복구 작업이 어려워진다.
- 반드시 `--dry-run`으로 계획을 먼저 확인하고 적용한다.

## 8. 로컬 콘솔 로그인(PAM) 제한

물리 모니터/키보드 연결 시 로그인까지 제한하려면 아래 스크립트를 추가 적용한다.

- `device_config/harden_local_console_pam.sh`

실행:

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/harden_local_console_pam.sh --run-user whatsmatter
```

적용 내용:

- `/etc/pam.d/login` 에 `pam_access.so` 활성화
- `/etc/pam.d/gdm-password`, `/etc/pam.d/gdm-autologin` 에도 `pam_access.so` 활성화
- `/etc/security/access.conf` 에 아래 정책 추가
  - `+:root:LOCAL`
  - `-:ALL EXCEPT root:LOCAL`
- GDM 자동로그인 비활성화 (`AutomaticLoginEnable=false`)
- 로컬 UI/콘솔 노출 차단(systemd):
  - display manager(`gdm3/gdm/lightdm/sddm`) disable+mask
  - `getty@tty1..6`, `serial-getty@ttyAMA0` mask
  - 기본 target `multi-user.target`

주의:

- 이 정책 적용 후 모니터/키보드/마우스를 연결해도 로컬 로그인 UI/TTY가 노출되지 않는다.
- 장비 유지보수는 reverse tunnel 경로를 먼저 확보한 뒤 적용해야 한다.
- 로그인 전 터널 미동작 이슈 점검은 상위 기획서의
  `16.4 로그인 전 tunnel 실패 대응 표준` 절을 따른다.
  - [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)

통합 설치에서 같이 적용:

```bash
bash device_config/setup_initial_device.sh \
  --setup-support-tunnel \
  --harden-reverse-tunnel-only \
  --harden-local-console-pam
```
