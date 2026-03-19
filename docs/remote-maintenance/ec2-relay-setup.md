# EC2 Relay Server Setup

## 1. 목적

이 문서는 MatterHub reverse SSH tunnel용 EC2 중계서버를 AWS에 생성하고 운영하는 절차를 정의한다.

## 2. 프로파일 분리 원칙

- 기존 프로파일(`dev`, `led-display`)과 분리해 `matterhub-relay` 프로파일만 사용한다.
- 모든 명령에 `--profile matterhub-relay --region ap-northeast-2`를 강제한다.

## 3. 생성 스크립트

실행 파일:

- `device_config/provision_relay_ec2.sh`

기본 동작:

- 기본 VPC/Subnet 자동 탐색
- `t4g.nano` 인스턴스 생성
- SSH 443 포트 전용 Security Group 생성
- Operator Key Pair 생성(없으면 생성, 있으면 재사용)
- 최신 Amazon Linux 2023 ARM64 AMI 사용
- Elastic IP 할당/연결
- SSH 서버 reverse tunnel 친화 설정
- `j`/`register-hub` 유틸 설치
- 결과 JSON 저장

실행 예시:

```bash
cd /Users/wm-mac-01/Documents/matterhub-flask/matterhub-flask
bash device_config/provision_relay_ec2.sh \
  --profile matterhub-relay \
  --region ap-northeast-2
```

## 4. 허브 연결 등록

중계서버 접속:

```bash
ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@<relay_host>
```

허브 매핑 등록:

```bash
sudo register-hub <hub_id> <remote_port> [device_user]
```

예시:

```bash
sudo register-hub whatsmatter-nipa_SN-1770784749 22608 whatsmatter
```

Hub 공개키 등록 위치:

- `/home/whatsmatter/.ssh/authorized_keys`

라즈베리파이 `setup_support_tunnel.sh` 출력의 `authorized_keys` 한 줄을 그대로 추가한다.

또는 로컬 자동 등록 스크립트를 사용한다:

- `device_config/register_hub_on_relay.sh`

예시:

```bash
bash device_config/register_hub_on_relay.sh \
  --relay-host <relay_host> \
  --relay-user ec2-user \
  --hub-id whatsmatter-nipa_SN-1770784749 \
  --remote-port 22608 \
  --device-user whatsmatter \
  --hub-pubkey /path/to/matterhub_support_tunnel_ed25519.pub
```

## 5. 접속 단축

중계서버 안에서:

```bash
j <hub_id>
```

예시:

```bash
j whatsmatter-nipa_SN-1770784749
```

허브에서 비밀번호 입력 없이 접속하려면 relay hub-access 공개키를 허브 `authorized_keys`에 추가해야 한다.
자세한 절차는 [리버스 터널 접속방법](./reverse-tunnel-access-method.md)을 따른다.

## 6. 100대 이상 확장 가이드

- 포트 계획: 기본 `22000-23999` (2,000대 수용 가능)
- 허브당 고유 remote port 사용
- `hubs.map`를 Git/DB 등 중앙 저장소로 이전 검토
- 동시 접속 증가 시 인스턴스 타입 상향(`t4g.small` 이상)
- 단일 장애점 제거 필요 시 ALB/NLB 앞단이 아니라, relay 다중화 + 장비별 failover host 전략 권장
