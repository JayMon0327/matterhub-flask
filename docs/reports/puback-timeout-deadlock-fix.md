# PUBACK 타임아웃 교착 해소 — 트러블슈팅 보고서

**작성일:** 2026-03-23
**커밋:** `1109f52` (`fix(mqtt): PUBACK 타임아웃 교착 해소 — 모든 명령 핸들러를 큐 워커 스레드로 이동`)

---

## 1. 문제 발생 상황

15045 장비(Hyodol SLM, 릴레이 경유)에서 `bundle_check` MQTT 명령 수신 시 응답 publish에서 **PUBACK 10초 타임아웃**이 발생했다.

- AWS IoT 정책은 `iot:* on *`으로 권한 문제 없음 확인
- `git_update` 명령은 정상 동작하지만, `set_env`/`bundle_update`/`bundle_check` 세 명령만 실패
- 증상: `⚠️ PUBACK 대기 실패` 로그 출력, 클라우드 백엔드에서 응답 미수신

---

## 2. 문제 원인

awscrt SDK의 `EventLoopGroup(1)` — **단일 이벤트 루프 스레드** 구조가 근본 원인이다.

이 스레드가 **MQTT 콜백 실행**과 **PUBACK 수신** 두 가지를 모두 담당한다.

### 교착 발생 메커니즘

```
콜백 스레드(EventLoopGroup)
│
├─ on_message 콜백 진입
│   ├─ handle_update_command() 호출
│   │   ├─ (구) set_env/bundle_update/bundle_check: 콜백 스레드에서 직접 실행
│   │   │   └─ _publish_response()
│   │   │       └─ pub_future.result(timeout=10)  ← 동기 대기 (블록!)
│   │   │           └─ ⏳ PUBACK 수신 대기...
│   │   │               └─ ❌ PUBACK은 이 스레드가 처리해야 하지만
│   │   │                     이 스레드는 지금 블록 중 → 교착!
│   │   │
│   │   └─ (구) git_update: update_queue.put() → 큐 워커 스레드에서 실행
│   │       └─ 워커 스레드에서 _publish_response() → ✅ 이벤트 루프 스레드 자유 → PUBACK 정상 수신
```

- `set_env`/`bundle_update`/`bundle_check`는 콜백 스레드에서 직접 `_publish_response()` → `pub_future.result(timeout=10)` 동기 대기 → 이벤트 루프 블록 → PUBACK 수신 불가 → **교착**
- `git_update`만 큐 워커 스레드를 사용했기 때문에 이벤트 루프 스레드가 자유로워 PUBACK이 정상 수신됨

---

## 3. 해결책

**기술:** Python `queue.Queue` + 데몬 스레드 기반 비동기 디스패치

**변경 파일:** 2개
- `mqtt_pkg/update.py` — 명령 핸들러 구조 변경
- `tests/mqtt_pkg/test_update.py` — 테스트 추가/수정

### 구체적 변경

#### `handle_update_command()` — 모든 명령을 큐에 넣고 즉시 반환

```python
def handle_update_command(message: Dict[str, Any]) -> None:
    try:
        command = message.get("command")
        update_id = message.get("update_id", "unknown")
        print(f"📥 업데이트 명령 수신: command={command}, update_id={update_id}")
        update_queue.put(message)         # ← 큐에 넣고 즉시 반환 (수 마이크로초)
        print(f"📋 업데이트 큐에 추가됨: {update_id}")
    except Exception as exc:
        print(f"❌ 업데이트 명령 큐 추가 실패: {exc}")
```

기존에는 `set_env`/`bundle_update`/`bundle_check`가 이 함수 안에서 직접 처리됐지만, 수정 후에는 **모든 명령**이 `update_queue.put()`만 하고 콜백 스레드를 즉시 해방한다.

#### `process_update_queue()` — 큐 워커 스레드에서 command별 분기 처리

```python
def process_update_queue() -> None:
    global is_processing_update
    while True:
        message = update_queue.get()
        command = message.get("command")

        if command == "set_env":
            _handle_set_env(message)
        elif command == "bundle_update":
            _handle_bundle_update(message)
        elif command == "bundle_check":
            _handle_bundle_check(message)
        else:
            # git_update (default)
            send_immediate_response(message, status="processing")
            execute_update_async(message)
```

### 스레드 구조 도식

```
┌──────────────────────┐
│    메인 스레드        │  mqtt.py 시작, AWSIoTClient 연결
│    (main thread)     │
└──────────┬───────────┘
           │
           │ EventLoopGroup(1) 생성
           ▼
┌──────────────────────┐
│ 이벤트 루프 스레드    │  MQTT 수신/송신, PUBACK 처리
│ (awscrt thread)      │
│                      │  on_message 콜백 → handle_update_command()
│                      │  → update_queue.put(msg)   ← 수 μs, 즉시 반환
│                      │  → 이벤트 루프 계속 동작 → PUBACK 수신 가능 ✅
└──────────────────────┘
           │
           │ queue.Queue
           ▼
┌──────────────────────┐
│ 큐 워커 스레드        │  process_update_queue() 데몬 스레드
│ (update-queue-worker)│
│                      │  command 분기 → _handle_set_env / _handle_bundle_* / execute_update_async
│                      │  → _publish_response() → pub_future.result(timeout=10)
│                      │  → 이벤트 루프 스레드가 자유 → PUBACK 정상 수신 ✅
└──────────────────────┘
```

### 수치

| 지표 | 변경 전 | 변경 후 |
|------|---------|---------|
| PUBACK 타임아웃 발생 명령 | 3개 (set_env, bundle_update, bundle_check) — 100% 실패 | 0개 — 4개 명령 모두 정상 |
| 콜백 스레드 점유 시간 | 수 초 ~ 10초 (타임아웃) | 수 마이크로초 (`queue.put()`) |
| PUBACK 대기 실패 로그 | 명령 실행 시마다 발생 | 0건 |

---

## 4. 검증 결과

### 단위 테스트

```
$ python -m unittest tests.mqtt_pkg.test_update -v

test_bundle_check_returns_inbox_status ... ok
test_bundle_update_downloads_and_responds ... ok
test_bundle_update_rejects_missing_url ... ok
test_enqueues_all_commands ... ok
test_enqueues_message ... ok
test_set_env_rejects_disallowed_key ... ok
test_set_env_rejects_empty_key ... ok
test_set_env_updates_allowed_key ... ok
test_set_env_with_restart ... ok

Ran 9 tests — OK
```

### 15045 장비 실기 검증

- `bundle_check` MQTT 명령 발행 → PUBACK 정상 수신 → 응답 메시지 클라우드에 도착 확인
- journalctl 로그에서 `⚠️ PUBACK 대기 실패` 메시지 **0건**
