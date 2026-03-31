# PM2 start.json 삭제 문제 수정 및 15103 장비 복구

**작성일:** 2026-03-31
**커밋:** `4bc0241` (`fix(deploy): git clean 시 start.json 보존 + PM2 startup 조건부 disable`)

---

## 1. 문제 발생 상황

15103 장비(192.168.1.15, Hyodol SLM 릴레이 경유)에서 MatterHub git 업데이트 후 재부팅 시 **Hyodol 프로세스 전체가 죽는** 문제가 확인되었다.

### 증상
- PM2 list가 비어있음 (프로세스 0개)
- `start.json` 파일 삭제됨
- 디스크 100% 사용 (467GB 중 443GB)

### 원인 2가지

**원인 1: `git clean -fd`가 start.json 삭제**
- `bulk_initial_deploy.sh`의 git 정리 단계에서 `git clean -fd` 실행
- `start.json`은 git에 추적되지 않는 파일(untracked)이므로 삭제 대상
- Hyodol의 PM2 ecosystem config가 날아감

**원인 2: systemd 마이그레이션이 PM2 startup 무조건 disable**
- `update_server.sh`, `migrate_pm2_to_systemd.sh` 모두 PM2 startup 서비스를 무조건 disable
- PM2에 MatterHub 프로세스만 삭제한 후에도, 남아있는 Hyodol 프로세스와 무관하게 disable 처리
- 재부팅 시 `pm2-hyodol.service`가 시작되지 않아 `pm2 resurrect` 실행 안 됨

---

## 2. 코드 수정 (4개 파일)

### 2-1. `device_config/bulk_initial_deploy.sh` (라인 160)

`git clean -fd` 전후로 `start.json` 백업/복원 추가:

```bash
# 변경 전
device_ssh "$port" "cd ~/$PROJECT_DIR && git stash drop; git checkout -- .; git clean -fd"

# 변경 후
device_ssh "$port" "cd ~/$PROJECT_DIR && \
  if [ -f start.json ]; then cp start.json /tmp/start.json.bak_${port}; fi; \
  git stash drop; git checkout -- .; git clean -fd; \
  if [ -f /tmp/start.json.bak_${port} ]; then cp /tmp/start.json.bak_${port} start.json; fi"
```

### 2-2. `device_config/update_server.sh` (라인 288-292)

PM2에 프로세스가 남아있으면 startup disable 건너뜀:

```bash
remaining_count=$("$pm2_bin" jlist | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$remaining_count" -gt 0 ]; then
    echo "[INFO] PM2에 고객사 프로세스 남아있음 — startup 서비스 유지"
else
    # 기존 disable 로직
fi
```

### 2-3. `device_config/migrate_pm2_to_systemd.sh` (라인 245-253)

동일한 조건부 disable 로직 적용. DRY-RUN 모드 지원 유지.

### 2-4. `.gitignore`

`start.json` 추가 — `git reset --hard` 시 안전장치 (untracked 파일은 reset 대상이 아니지만, 향후 실수로 add되는 것 방지).

---

## 3. 15103 장비 복구

릴레이(4.230.8.65) → SSH 15103 경유 접속.

### 3-1. 상태 진단

| 항목 | 상태 |
|------|------|
| PM2 바이너리 | `/home/hyodol/.nvm/versions/node/v22.17.0/bin/pm2` |
| PM2 God Daemon | 실행 중 (resurrect 시도했으나 프로세스 0개) |
| pm2-hyodol.service | **enabled** (다행히 disable 안 됨) |
| start.json | **삭제됨** |
| dump.pm2 | 존재 (7개 프로세스: check, heartbeat, mqtt-api + wm-* 4개) |
| 디스크 | 100% (467GB/467GB) |

### 3-2. 복구 작업

1. **PM2 로그 정리**: `pm2 flush` + 로그 파일 truncate → 663MB → 474MB 확보
2. **start.json 재구성**: dump.pm2에서 Hyodol 프로세스 3개(check, heartbeat, mqtt-api)의 script/cwd/interpreter 추출하여 생성
3. **MatterHub wm-* 프로세스 제거**: dump에서 wm-ruleEngine, wm-notifier, wm-app, wm-mqtt 삭제
4. **Hyodol 프로세스 시작**: `pm2 start start.json` → 3개 모두 online
5. **pm2 save**: dump.pm2에 Hyodol 프로세스만 저장

### 3-3. 복구 확인

```
┌────┬──────────────┬─────────┬──────────┬────────┬───────────┐
│ id │ name         │ mode    │ pid      │ uptime │ status    │
├────┼──────────────┼─────────┼──────────┼────────┼───────────┤
│ 0  │ check        │ fork    │ 1016520  │ 13s    │ online    │
│ 1  │ heartbeat    │ fork    │ 1016521  │ 13s    │ online    │
│ 2  │ mqtt-api     │ fork    │ 1016522  │ 13s    │ online    │
└────┴──────────────┴─────────┴──────────┴────────┴───────────┘
pm2-hyodol.service: enabled
```

restart 횟수 0, 13초 후에도 안정적으로 online 유지 확인.

---

## 4. 재생성한 start.json 내용

```json
{
  "apps": [
    { "name": "check",     "script": "check.py",       "cwd": "/home/hyodol/Hyodol", "interpreter": "python3" },
    { "name": "heartbeat", "script": "spy.py",          "cwd": "/home/hyodol/Hyodol", "interpreter": "python3" },
    { "name": "mqtt-api",  "script": "mqtt-server.py",  "cwd": "/home/hyodol/Hyodol", "interpreter": "python"  }
  ]
}
```

---

## 5. 영향 범위

| 업데이트 경로 | start.json 보존 | PM2 startup 유지 |
|--------------|:-:|:-:|
| MQTT git_update 토픽 (`update_server.sh`) | O (git clean 없음) | O (조건부 disable) |
| MQTT bundle_update 토픽 (`update_server.sh`) | O | O |
| SSH 일괄 배포 (`bulk_initial_deploy.sh`) | O (백업/복원) | - (이 스크립트는 PM2 안 건드림) |
| 수동 마이그레이션 (`migrate_pm2_to_systemd.sh`) | - | O (조건부 disable) |

---

## 6. 잔여 이슈

- **15103 디스크 100%**: 467GB 중 443GB 사용. PM2 로그 정리로 474MB만 확보. 근본적인 디스크 정리 필요 (Hyodol 측 확인 필요)
- **다른 Hyodol 장비**: 15103 외에도 start.json이 삭제된 장비가 있을 수 있음. 향후 일괄 배포 시 확인 필요
