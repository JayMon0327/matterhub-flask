# 리버스 터널 접속방법

## 1. 현재 운영 기준

- Relay Host: `3.38.126.167`
- Relay SSH Port: `443`
- Relay Operator User: `ec2-user`
- Hub Tunnel User: `whatsmatter`
- 현재 허브 포트 예시: `22608` (`whatsmatter-nipa_SN-1770784749`)

## 2. 운영자 기본 접속(권장)

### 2.1 Relay 서버 접속

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167
```

### 2.2 허브 단축 접속

Relay 서버 안에서:

```bash
j <hub_id 또는 키워드>
```

예시:

```bash
j whatsmatter-nipa_SN-1770784749
j 1770784749
```

## 3. one-liner 접속

운영자 로컬에서 직접:

```bash
ssh -o ProxyCommand='ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 -W %h:%p' \
  -p 22608 whatsmatter@127.0.0.1
```

주의:

- one-liner는 허브 계정(`whatsmatter`) 비밀번호를 물을 수 있다.
- 비밀번호 입력 없이 운영하려면 2단계 `j` 방식 사용을 권장한다.

## 4. 신규 허브 등록 절차

1. 허브에서 `setup_support_tunnel.sh` 실행해 공개키와 remote port 확보
2. 운영자 PC에서 아래 스크립트 실행:

```bash
bash device_config/register_hub_on_relay.sh \
  --relay-host 3.38.126.167 \
  --relay-user ec2-user \
  --hub-id <hub_id> \
  --remote-port <remote_port> \
  --device-user whatsmatter \
  --hub-pubkey /path/to/matterhub_support_tunnel_ed25519.pub
```

## 5. 비밀번호 없는 j 접속(허브측 설정)

`j`가 비밀번호를 요구하지 않게 하려면, Relay의 hub-access 공개키를 허브 `authorized_keys`에 넣어야 한다.

### 5.1 Relay hub-access 공개키 조회

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 \
  'cat /home/ec2-user/.ssh/hub_access_ed25519.pub'
```

### 5.2 허브 설치 시 함께 반영

`install_ubuntu24.sh` 또는 `setup_support_tunnel.sh` 실행 시 아래 옵션으로 전달:

```bash
--support-relay-access-pubkey "<위에서 조회한 공개키 한 줄>"
```

또는 `setup_support_tunnel.sh` 직접 사용:

```bash
--relay-access-pubkey "<위에서 조회한 공개키 한 줄>"
```

## 6. 확장 운영 규칙(100대 이상)

- remote port는 허브별 고유값 사용
- 권장 범위: `22000-23999`
- `register-hub`로 `hubs.map` 갱신 후 접속
- 운영자 접속은 반드시 relay operator key 기반으로 수행
