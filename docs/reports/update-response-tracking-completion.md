# 업데이트 응답 추적 — 수정 완료 보고서

**작성일:** 2026-03-22
**작업자:** 엣지서버 팀

---

## 배경

클라우드 백엔드 `7b98d56` 커밋에서 업데이트 응답 추적 기능이 대부분 구현되었으나, 2가지 문제가 남아있었습니다:

1. DynamoDB에 `results` 필드가 초기화되지 않아 발행 직후 status 조회 시 pending 카운트가 0으로 잡히는 문제
2. 엣지서버의 응답 함수가 `command: "git_update"`를 하드코딩하여, `set_env` 등 다른 명령의 응답도 `git_update`로 보고되는 문제

---

## 수정 내용

### 1. 클라우드 — DynamoDB results 초기화

**파일:** `AWS/shadow-state-ingest/remoteUpdateCommand/update_command.py` (426행)

**변경 전:**
```python
update_jobs_table.put_item(
    Item={
        'update_id': update_id,
        'command': command,
        'targets': targets,
        'options': options,
        'target_mqtt_ids': target_mqtt_ids,
        'status': 'pending',
        ...
    }
)
```

**변경 후:**
```python
update_jobs_table.put_item(
    Item={
        'update_id': update_id,
        'command': command,
        'targets': targets,
        'options': options,
        'target_mqtt_ids': target_mqtt_ids,
        'results': {mqtt_id: {'status': 'pending'} for mqtt_id in target_mqtt_ids},
        'status': 'pending',
        ...
    }
)
```

**효과:**
- `GET /update/status/{update_id}` 발행 직후 조회 → `pending: 104 / success: 0` 정확히 표시
- Response Handler가 도착하면 개별 항목이 `pending → success/failed`로 갱신
- 오프라인 디바이스는 영구 `pending` 유지 (타임아웃 기반 감지 가능)

---

### 2. 엣지서버 — 응답 command 필드 동적화

**파일:** `mqtt_pkg/update.py` (49행, 63행, 80행)

3개 함수 모두 동일한 패턴으로 수정:

| 함수 | 행 | 변경 전 | 변경 후 |
|------|-----|---------|---------|
| `send_immediate_response()` | 49 | `"command": "git_update"` | `"command": message.get("command", "git_update")` |
| `send_final_response()` | 63 | `"command": "git_update"` | `"command": message.get("command", "git_update")` |
| `send_error_response()` | 80 | `"command": "git_update"` | `"command": message.get("command", "git_update")` |

**효과:**
- `git_update` 명령 → 응답에 `command: "git_update"` (기존과 동일, 하위 호환)
- `set_env` 명령 → 응답에 `command: "set_env"` (수정됨)
- 향후 새 명령 추가 시에도 자동 반영

---

## 테스트 결과

```
$ python -m unittest tests.mqtt_pkg.test_update -v

test_enqueues_message ... ok
test_sends_error_on_exception ... ok
test_set_env_rejects_disallowed_key ... ok
test_set_env_rejects_empty_key ... ok
test_set_env_updates_allowed_key ... ok
test_set_env_with_restart ... ok

Ran 6 tests in 0.017s — OK
```

엣지서버 update 관련 테스트 6개 전부 통과.

---

## 검증 시나리오

배포 후 다음 순서로 검증 가능합니다:

| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 테스트 디바이스에 `git_update` 발행 | MQTT 명령 정상 전달 |
| 2 | 발행 직후 `GET /update/status/{update_id}` | 전체 대상이 `pending`으로 표시 (예: pending=104) |
| 3 | 디바이스 응답 수신 후 재조회 | 성공 디바이스 → `success`, 실패 → `failed`, 오프라인 → `pending` 유지 |
| 4 | `set_env` 명령 발행 후 응답 확인 | 응답의 `command` 필드가 `"set_env"`로 표시 |

---

## 수정 대상 파일 요약

| 레포 | 파일 | 수정 라인 |
|------|------|-----------|
| 클라우드 (EdgeServer-adminSolution-AWS-dev) | `AWS/shadow-state-ingest/remoteUpdateCommand/update_command.py` | 426행 (results 필드 추가) |
| 엣지 (matterhub-flask) | `mqtt_pkg/update.py` | 49, 63, 80행 (command 동적화) |

---

## 클라우드 팀 액션 아이템

1. **`update_command.py`에 위 diff 적용 후 SAM 빌드 & 배포**
2. **`update_response_handler.py`와의 호환성 확인** — Response Handler가 `results.{mqtt_id}.status`를 업데이트하는 로직이 이 초기 구조와 일치하는지 확인
3. **`update_status.py`의 pending 카운트 로직 확인** — `results` dict에서 `status == "pending"` 개수를 세는 방식이면 정상 동작

> 엣지서버 측 수정(`mqtt_pkg/update.py`)은 다음 `git_update` 배포 시 자동 반영됩니다.
