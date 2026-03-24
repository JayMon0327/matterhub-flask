---
name: device-bulk-deploy
description: MatterHub 릴레이 경유 일괄 배포. 여러 디바이스에 git pull + auto_bootstrap + systemd 마이그레이션을 일괄 수행한다. "/device-bulk-deploy" 또는 "일괄 배포", "bulk deploy" 시 사용.
---

# MatterHub 릴레이 경유 일괄 배포

릴레이 서버를 경유하여 여러 디바이스에 최신 코드를 배포하고 systemd 마이그레이션을 수행하는 스킬.

## 사전 조건

사용자에게 다음 정보를 확인한다:

| 항목 | 예시 | 필수 |
|------|------|------|
| 릴레이 호스트 | 4.230.8.65 | Y |
| 릴레이 SSH 유저 | kh-kim | Y |
| 릴레이 SSH 키 경로 (Mac) | /tmp/hyodol-slm-server-key.pem | Y |
| 디바이스 SSH 유저 | hyodol | Y |
| 디바이스 sudo 비밀번호 | tech8123 | Y |
| 배포 대상 포트 목록 | 15093, 15094 ... | Y |
| 배포 브랜치 | master (기본값) | N |

## 배포 절차

### Step 1: 포트 목록 설정

대상 디바이스의 릴레이 터널 포트를 `device_config/device_ports.txt`에 설정한다:

```bash
cat device_config/device_ports.txt
# 형식: 한 줄에 포트 번호 하나, # 주석 가능
# 15093
# 15094
```

포트 목록이 없으면 사용자에게 대상 장비 포트를 확인한다.

### Step 2: 릴레이 접속 테스트

```bash
ssh -i {relay_key} -o StrictHostKeyChecking=no {relay_user}@{relay_host} "echo RELAY_OK"
```

### Step 3: dry-run 실행

```bash
RELAY_HOST={relay_host} \
RELAY_USER={relay_user} \
RELAY_KEY={relay_key} \
DEVICE_USER={device_user} \
SUDO_PASS={sudo_pass} \
bash device_config/bulk_initial_deploy.sh --dry-run
```

각 디바이스 접속 가능 여부를 확인한다.

### Step 4: 실제 배포

```bash
RELAY_HOST={relay_host} \
RELAY_USER={relay_user} \
RELAY_KEY={relay_key} \
DEVICE_USER={device_user} \
DEVICE_KEY_ON_RELAY={device_key_on_relay} \
SUDO_PASS={sudo_pass} \
DEPLOY_BRANCH={branch} \
bash device_config/bulk_initial_deploy.sh
```

**스크립트가 각 디바이스에서 수행하는 작업:**
1. `git fetch + git reset --hard origin/{branch}` — 최신 코드 동기화
2. NOPASSWD sudoers 설정 (`/etc/sudoers.d/matterhub-update`)
3. `update_server.sh` 실행 — auto_bootstrap + systemd 마이그레이션
4. 검증: 커밋, MQTT/API 서비스 상태, hub_id, SUBSCRIBE 플래그 확인

### Step 5: 결과 확인

스크립트가 출력하는 요약을 확인한다:

```
==========================================
 배포 결과: 총 N / 성공 M / 실패 K
==========================================
```

실패한 디바이스는 개별 로그를 확인하고 수동 대응한다.

## 환경변수 참조

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `RELAY_HOST` | 4.230.8.65 | 릴레이 서버 IP |
| `RELAY_USER` | kh-kim | 릴레이 SSH 유저 |
| `RELAY_KEY` | /tmp/hyodol-slm-server-key.pem | 릴레이 SSH 키 |
| `DEVICE_USER` | hyodol | 디바이스 SSH 유저 |
| `DEVICE_KEY_ON_RELAY` | /home/kh-kim/.ssh/id_s2edge | 릴레이 내 디바이스 키 경로 |
| `SUDO_PASS` | tech8123 | 디바이스 sudo 비밀번호 |
| `DEPLOY_BRANCH` | master | 배포 브랜치 |
| `PORTS_FILE` | device_config/device_ports.txt | 포트 목록 파일 |

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 디바이스 접속 불가 | 릴레이 터널 끊김 | 릴레이에서 `ss -tlnp \| grep {port}` 확인 |
| git pull 실패 | DNS 불안정 (Wi-Fi) | 디바이스에서 `ping github.com` 확인, 수동 재시도 |
| sudoers 설정 실패 | 비밀번호 틀림 | SUDO_PASS 확인 |
| systemd 불안정 | venv 없음 / 깨짐 | 개별 디바이스에서 `/device-migrate-systemd` 실행 |

## 완료 후 안내

일괄 배포 완료 후:
1. 모든 디바이스가 MQTT 원격 업데이트 가능 상태
2. 이후 업데이트는 `/device-remote-update`로 MQTT 토픽 전송
3. 토픽: `matterhub/update/all` (전체) 또는 `matterhub/update/specific/{hub_id}` (개별)
