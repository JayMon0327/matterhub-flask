---
name: device-migrate-systemd
description: MatterHub PM2→systemd 마이그레이션. 디바이스에 SSH 접속하여 migrate_pm2_to_systemd.sh를 실행한다. "/device-migrate-systemd" 또는 "PM2 마이그레이션", "systemd 전환" 시 사용.
---

# MatterHub PM2→systemd 마이그레이션

디바이스의 MatterHub 프로세스를 PM2에서 systemd로 전환하는 스킬.
Hyodol 프로세스(mqtt-api, check, heartbeat)는 PM2에 유지하고, MatterHub 5개 서비스만 systemd로 이전한다.

## 사전 조건

사용자에게 다음 정보를 확인한다:

| 항목 | 예시 | 필수 |
|------|------|------|
| 디바이스 IP (또는 릴레이 경유 접속 방법) | 192.168.219.191 | Y |
| SSH User | matterhub / whatsmatter / hyodol | Y |
| SSH Password 또는 Key | mat458496ad! | Y |

## 마이그레이션 절차

### Step 1: SSH 접속 + 현재 상태 확인

```bash
# 현재 서비스 관리 방식 확인
pm2 list 2>/dev/null || echo "PM2 미설치"
systemctl is-active matterhub-mqtt 2>/dev/null || echo "systemd 미설치"
```

이미 systemd로 동작 중이면 (2개 이상 active) 마이그레이션 불필요를 안내한다.

### Step 2: sudo NOPASSWD 확인

```bash
sudo -n systemctl --version 2>/dev/null && echo "SUDO_OK" || echo "SUDO_FAIL"
```

SUDO_FAIL이면 sudoers 설정이 필요하다:

```bash
echo '{sudo_password}' | sudo -S bash -c 'cat > /etc/sudoers.d/matterhub-update << EOF
# MatterHub 업데이트용 NOPASSWD 설정
{user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /usr/bin/install, /usr/bin/systemd-run
EOF
chmod 0440 /etc/sudoers.d/matterhub-update'
```

### Step 3: 최신 코드 동기화

```bash
cd ~/Desktop/matterhub && git pull origin master
# 또는 .deb 설치 환경
cd /opt/matterhub/app && git pull origin master
```

### Step 4: 마이그레이션 실행

```bash
# dry-run으로 먼저 확인
bash device_config/migrate_pm2_to_systemd.sh --dry-run

# 실제 실행
bash device_config/migrate_pm2_to_systemd.sh
```

**플래그:**
- `--dry-run`: 실제 변경 없이 수행할 작업만 출력
- `--force`: 이미 설치된 unit이 있어도 재설치

### Step 5: 검증

```bash
# systemd 서비스 5개 상태 확인
for svc in api mqtt rule-engine notifier update-agent; do
  echo -n "matterhub-${svc}: "; systemctl is-active matterhub-${svc}.service
done

# PM2에서 MatterHub 프로세스 제거 확인
pm2 list 2>/dev/null | grep -E 'wm-|matter|slm-server' || echo "PM2 MatterHub 프로세스 없음"

# 로그 확인
cat logs/migrate_pm2_to_systemd.log
```

**정상 결과:**
- systemd 서비스 5개 모두 `active`
- PM2에 MatterHub 관련 프로세스 없음 (Hyodol 프로세스는 유지)

## 스크립트 동작 순서

1. systemd unit 렌더링 (`render_systemd_units.py`) → `/etc/systemd/system/` 설치
2. 구형 단일 서비스 (`matterhub.service`) disable
3. systemd 서비스 5개 enable + start
4. 3초 대기 후 active 상태 확인 (2개 미만이면 PM2 정리 건너뜀)
5. PM2에서 MatterHub 프로세스만 삭제 + pm2 save

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `sudo NOPASSWD 사용 불가` | sudoers 미설정 | Step 2 참조 |
| `systemd 서비스 불안정 — PM2 유지` | unit 렌더링 실패 또는 venv 없음 | `journalctl -u matterhub-mqtt -n 20`으로 확인 |
| venv 관련 ExecStart 실패 | 부서진 venv | `rm -rf venv && python3 -m venv --system-site-packages venv` |
| render_systemd_units.py 없음 | 코드 미업데이트 | `git pull origin master` |
| PM2 cgroup kill | PM2 안에서 systemctl restart 실행 | 스크립트가 자동으로 `systemd-run --scope` 사용 |

## 완료 후 안내

마이그레이션 완료 후:
1. MQTT 원격 업데이트 사용 가능 (`/device-remote-update`)
2. `journalctl -u matterhub-mqtt -f`로 실시간 로그 확인 가능
