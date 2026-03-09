# Reverse SSH Tunnel 빠른 적용 가이드

## 1. 목적

이 문서는 라즈베리파이에 reverse SSH tunnel을 빠르게 설치하고, 운영자가 실제 접속하는 명령까지 확인하기 위한 실행 절차를 정리한다.

상세 설계는 아래 문서를 함께 참조한다.

- [Reverse SSH Tunnel 설계](./reverse-ssh-tunnel-design.md)

## 2. 기본 가정값

사용자 요청 기준으로 아래 값을 기본값으로 둔다.

- support server user: `whatsmatter`
- device ssh user: `whatsmatter`
- support server host: `3.38.126.167` (현재 운영 relay EIP)
- support server ssh port: `443`
- tunnel command: `autossh`

주의:

- reverse tunnel 인증은 비밀번호가 아니라 SSH 키 기반으로 사용한다.
- 비밀번호 로그인은 운영 보안 정책상 비권장이다.

## 3. 장비(라즈베리파이) 1회 설치

기본 설치 + reverse tunnel 설정을 한 번에 하려면 아래 통합 명령을 사용한다.

```bash
cd /home/whatsmatter/Desktop/matterhub
RELAY_HUB_ACCESS_PUBKEY="$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 'cat /home/ec2-user/.ssh/hub_access_ed25519.pub')"
bash device_config/install_ubuntu24.sh \
  --setup-support-tunnel \
  --support-host 3.38.126.167 \
  --support-user whatsmatter \
  --support-device-user whatsmatter \
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-allow-inbound-port 8100 \
  --harden-allow-inbound-port 8123
```

reverse tunnel만 허용(직접 SSH 차단)까지 같이 적용하려면:

```bash
bash device_config/install_ubuntu24.sh \
  --setup-support-tunnel \
  --support-host 3.38.126.167 \
  --support-user whatsmatter \
  --support-device-user whatsmatter \
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-reverse-tunnel-only \
  --harden-allow-inbound-port 8100 \
  --harden-allow-inbound-port 8123
```

reverse tunnel만 별도로 구성하려면 아래 명령을 사용한다.

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/setup_support_tunnel.sh \
  --host 3.38.126.167 \
  --user whatsmatter \
  --device-user whatsmatter \
  --relay-operator-user ec2-user \
  --relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY"
```

이 스크립트가 수행하는 작업:

- device key 생성 (`/home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519`)
- `.env`에 `SUPPORT_TUNNEL_*` 변수 반영
- `matterhub-support-tunnel.service` 유닛 설치
- support server `authorized_keys` 등록용 문자열 출력
- 운영자 접속용 SSH 명령 출력

## 4. support server에 공개키 등록

설치 스크립트 출력에 있는 `authorized_keys` 한 줄을 support server의 유지보수 계정(`whatsmatter`)에 추가해야 실제 터널이 붙는다.

예시 형태:

```text
restrict,port-forwarding,permitlisten="127.0.0.1:<REMOTE_PORT>" ssh-ed25519 AAAA... matterhub-support-tunnel@...
```

`j <hub_id>`까지 사용하려면 아래 두 단계가 모두 끝나야 한다.

1. 장비에서 `matterhub_id` 발급

```bash
cd /home/whatsmatter/Desktop/matterhub
venv/bin/python3 run_provision.py
sudo systemctl restart matterhub-mqtt.service
```

2. relay `hubs.map` 등록

장비 셸에서 공개키 확인:

```bash
cat /home/whatsmatter/.ssh/matterhub_support_tunnel_ed25519.pub
```

운영자 PC에서:

```bash
bash device_config/register_hub_on_relay.sh \
  --relay-host 3.38.126.167 \
  --relay-port 443 \
  --relay-user ec2-user \
  --relay-key ~/.ssh/matterhub-relay-operator-key.pem \
  --hub-id <matterhub_id> \
  --remote-port <REMOTE_PORT> \
  --hub-pubkey /tmp/matterhub_support_tunnel_ed25519.pub \
  --device-user whatsmatter
```

## 5. 터널 시작/상태 확인

```bash
sudo systemctl enable --now matterhub-support-tunnel.service
systemctl status matterhub-support-tunnel.service --no-pager
journalctl -u matterhub-support-tunnel.service -n 100 --no-pager
```

## 6. 운영자 접속 방법

장비 쪽 터널이 살아 있으면 운영자는 아래 두 단계 방식으로 접속한다.

```bash
ssh -i <relay-operator-key.pem> -p 443 ec2-user@support.whatsmatter.local
j <hub_id>
```

현재 운영값 기준 예시:

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
j whatsmatter-nipa_SN-1770784749
```

원라이너가 필요하면 아래 형식을 사용한다.

```bash
ssh -o ProxyCommand='ssh -i <relay-operator-key.pem> -p 443 ec2-user@support.whatsmatter.local -W %h:%p' \
  -p <REMOTE_PORT> whatsmatter@127.0.0.1
```

코드 기반 자동 출력:

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/setup_support_tunnel.sh --dry-run --host support.whatsmatter.local --user whatsmatter
```

## 7. 여러 장비 반복 설치

동일 명령을 각 장비에서 반복 실행하면 된다.

- `--remote-port`를 생략하면 `matterhub_id`를 기반으로 포트가 자동 산출된다.
- 포트 충돌이 우려되면 `--remote-port`를 장비별로 명시한다.

예시:

```bash
bash device_config/setup_support_tunnel.sh \
  --host support.whatsmatter.local \
  --user whatsmatter \
  --remote-port 22321 \
  --device-user whatsmatter \
  --enable-now
```
