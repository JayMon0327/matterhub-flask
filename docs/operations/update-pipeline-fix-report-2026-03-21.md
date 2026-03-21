# 엣지서버 원격 업데이트 파이프라인 수정 완료 보고서

**작성일:** 2026-03-21
**대상:** 클라우드 백엔드 연동팀
**브랜치:** `develop` → `master` 병합

---

## 1. 수정 배경

배포된 버전(4b4d5b8)과 현재 HEAD 사이에 94개 커밋이 존재하며, 원격 MQTT `git_update` 명령으로 102대 엣지서버를 업데이트해야 합니다. 기존 업데이트 파이프라인에 **치명적 버그 7건**이 발견되어 수정 없이 배포 시 서버 벽돌화, 상태 추적 불가, 원격 접근 영구 불가 위험이 있었습니다.

---

## 2. 수정된 이슈 목록

| # | 이슈 | 심각도 | 수정 파일 | 수정 내용 |
|---|------|--------|-----------|-----------|
| 1 | Self-Termination Race | CRITICAL | `update.py`, `update_server.sh` | 2단계 분리: git pull → 응답 전송 → 서비스 재시작 |
| 2 | PM2→systemd 전환 미반영 | CRITICAL | `update_server.sh` | `detect_process_manager()` 자동 감지 |
| 3 | QoS 0 응답 (유실 가능) | HIGH | `update.py` | QoS 1 + PUBACK 대기 (10초 timeout) |
| 4 | clean_session=True | HIGH | `runtime.py` | `clean_session=False` 설정 |
| 5 | 롤백 미구현 | CRITICAL | `update_server.sh` | healthcheck 실패 시 자동 `git reset --hard` |
| 6 | 하드코딩된 경로 | MEDIUM | `update_server.sh` | `SCRIPT_DIR`/`PROJECT_ROOT` 자동 감지 |
| 7 | SUBSCRIBE_MATTERHUB_TOPICS 기본값 "0" | CRITICAL | `update_server.sh` | `.env` 마이그레이션으로 자동 주입 |

---

## 3. 클라우드 백엔드 연동 변경사항

### 3.1 MQTT 응답 토픽 (변경 없음)

```
matterhub/{matterhub_id}/update/response
```

### 3.2 응답 QoS 변경: QoS 0 → QoS 1

- **이전:** QoS 0 (AT_MOST_ONCE) — 응답 유실 가능
- **이후:** QoS 1 (AT_LEAST_ONCE) + PUBACK 대기
- **클라우드 영향:** 중복 수신 가능성 있음 → `update_id` 기준 멱등성 처리 권장

### 3.3 Persistent Session 활성화

- **이전:** `clean_session=True` — 오프라인 중 메시지 유실
- **이후:** `clean_session=False` — AWS IoT Core가 오프라인 디바이스의 QoS 1 메시지 최대 1시간 보관
- **클라우드 영향:** 디바이스가 일시적으로 오프라인이어도 `git_update` 명령이 재연결 시 전달됨

### 3.4 업데이트 응답 페이로드 (변경 없음)

```json
{
    "update_id": "update_20260321_143022",
    "hub_id": "whatsmatter-nipa_SN-1752303557",
    "timestamp": 1742554222,
    "command": "git_update",
    "status": "success | failed | processing",
    "result": {
        "success": true,
        "commit": "abc1234...",
        "pre_commit": "4b4d5b8...",
        "branch": "master",
        "message": "Update script started successfully",
        "exit_code": 0
    }
}
```

### 3.5 상태 전이 변경

**이전 흐름 (문제):**
```
명령 수신 → "processing" 응답 → nohup 스크립트 실행 → (PM2 kill) → "success" 응답 전송 불가
```

**수정 흐름:**
```
명령 수신 → "processing" 응답 (QoS 1)
  → Phase A: git pull (--skip-restart, MQTT 프로세스 유지)
  → Phase B: 스크립트 완료 대기 + 상태 파일 읽기
  → Phase C: "success/failed" 최종 응답 (QoS 1, PUBACK 확인)
  → Phase D: 서비스 재시작 (nohup --restart-only)
```

**클라우드 영향:**
- "processing" 후 "success/failed" 응답이 **확실하게** 도착함
- "pending" 10분 초과 시나리오가 대폭 감소
- 롤백 발생 시 `result.rollback: true` 포함 가능

### 3.6 .env 마이그레이션 자동 실행

업데이트 후 서비스 재시작 전에 아래 환경변수가 `.env`에 없으면 자동 추가됩니다:

| 변수 | 값 | 효과 |
|------|-----|------|
| `SUBSCRIBE_MATTERHUB_TOPICS` | `"1"` | 업데이트 토픽 구독 활성화 (미래 원격 업데이트 보장) |
| `MATTERHUB_VENDOR` | `"konai"` | 벤더 프로바이더 명시 |

---

## 4. 수정된 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `device_config/update_server.sh` | 전면 재작성: PM2/systemd 자동감지, 경로 자동감지, 2단계 플래그, 자동 롤백, .env 마이그레이션 |
| `mqtt_pkg/update.py` | 2단계 분리(pull→응답→restart), QoS 1 + PUBACK 대기, 헬퍼 함수 추가 |
| `mqtt_pkg/runtime.py` | `clean_session=False` 추가 |
| `tests/test_update_server_sh.py` | 11개 테스트로 확장 (플래그, 롤백, 프로세스 매니저, 하드코딩 제거 검증) |

---

## 5. 배포 권장 순서

1. **카나리 테스트:** 3호기 (tunnel port 22343) 1대
2. **소규모 지역:** rehab (4대)
3. **전체 순차 배포:** rehab(4) → gangnam(9) → chuncheon(10) → gwangjin(10) → suseo(12) → gwangwon(14) → jerontech(16) → jeongseong(27)
4. 각 지역 배포 후 5분 대기 → `GET /update/status/{updateId}` 확인 → 다음 지역

### 모니터링 기준
- "pending" 10분 초과 → SSH 터널로 수동 확인
- "failed" + `rollback: true` → 자동 롤백 성공, 다음 배포 시 재시도
- 응답 없음 → support_tunnel 경유 SSH 접속

---

## 6. 롤백 시나리오

| 상황 | 동작 |
|------|------|
| healthcheck 실패 (서비스 기동 안됨) | 자동 `git reset --hard` + 재시작 |
| git pull 실패 | 즉시 종료, 기존 버전 유지 |
| MQTT 응답 전송 실패 | PUBACK 실패 로그, 서비스 재시작은 계속 진행 |
| 수동 롤백 필요 | SSH 접속 → `git reset --hard 4b4d5b8` → 서비스 재시작 |
