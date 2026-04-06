#!/usr/bin/env bash
# ==============================================================================
# MQTT 연결 안정성 패치 스크립트
# ==============================================================================
# 용도: konai/20260211-v1.1 브랜치 배포 장비에 MQTT 연결 안정성 패치 적용
# 사용: bash patch_mqtt_stability.sh [--rollback] [--dry-run]
#
# 패치 내용:
#   1. 부팅 후 네트워크 대기 + 서비스 레벨 무한 재시도 (crash loop 방지)
#   2. 재연결 무한 재시도 + 점진적 백오프 (zombie 서비스 방지)
#   3. keep_alive 300→30초, 연결 체크 60→30초 (빠른 끊김 감지)
#   4. 발행 실패 시 구조화된 로그 (진단 가능)
# ==============================================================================

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/matterhub/app}"
BACKUP_DIR="${APP_DIR}/.patch_backup_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/tmp/patch_mqtt_stability_$(date +%Y%m%d).log"
DRY_RUN=false
ROLLBACK=false

# --- 인자 파싱 ---
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --rollback) ROLLBACK=true ;;
        --help|-h)
            echo "사용법: bash $0 [--dry-run] [--rollback]"
            echo ""
            echo "옵션:"
            echo "  --dry-run    실제 변경 없이 계획만 출력"
            echo "  --rollback   가장 최근 백업으로 롤백"
            exit 0
            ;;
    esac
done

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

die() {
    log "❌ 오류: $1"
    exit 1
}

# --- 롤백 모드 ---
if [ "$ROLLBACK" = true ]; then
    LATEST_BACKUP=$(ls -dt "${APP_DIR}/.patch_backup_"* 2>/dev/null | head -1)
    if [ -z "$LATEST_BACKUP" ]; then
        die "백업 디렉토리를 찾을 수 없습니다."
    fi
    log "롤백: $LATEST_BACKUP"
    for f in mqtt.py mqtt_pkg/runtime.py mqtt_pkg/publisher.py mqtt_pkg/state.py; do
        if [ -f "$LATEST_BACKUP/$f" ]; then
            cp "$LATEST_BACKUP/$f" "$APP_DIR/$f"
            log "  복원: $f"
        fi
    done
    log "서비스 재시작..."
    sudo systemctl restart matterhub-mqtt.service || true
    log "✅ 롤백 완료"
    exit 0
fi

# --- 사전 체크 ---
log "=== MQTT 연결 안정성 패치 ==="
log "대상: $APP_DIR"

[ -d "$APP_DIR" ] || die "앱 디렉토리가 없습니다: $APP_DIR"
[ -f "$APP_DIR/mqtt.py" ] || die "mqtt.py를 찾을 수 없습니다: $APP_DIR/mqtt.py"
[ -f "$APP_DIR/mqtt_pkg/runtime.py" ] || die "runtime.py를 찾을 수 없습니다"

if [ "$DRY_RUN" = true ]; then
    log "[DRY-RUN] 변경 대상 파일:"
    log "  - $APP_DIR/mqtt.py"
    log "  - $APP_DIR/mqtt_pkg/runtime.py"
    log "  - $APP_DIR/mqtt_pkg/publisher.py"
    log "  - $APP_DIR/mqtt_pkg/state.py"
    log "[DRY-RUN] 백업 위치: $BACKUP_DIR"
    log "[DRY-RUN] 실제 변경 없이 종료합니다."
    exit 0
fi

# --- 백업 ---
log "백업 생성: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR/mqtt_pkg"
cp "$APP_DIR/mqtt.py" "$BACKUP_DIR/mqtt.py"
cp "$APP_DIR/mqtt_pkg/runtime.py" "$BACKUP_DIR/mqtt_pkg/runtime.py"
cp "$APP_DIR/mqtt_pkg/publisher.py" "$BACKUP_DIR/mqtt_pkg/publisher.py"
cp "$APP_DIR/mqtt_pkg/state.py" "$BACKUP_DIR/mqtt_pkg/state.py"
log "  백업 완료: 4개 파일"

# ==============================================================================
# 패치 1: mqtt.py — 네트워크 대기 + 서비스 레벨 무한 재시도
# ==============================================================================
log "패치 적용: mqtt.py"

cat > /tmp/_patch_mqtt.py << 'PATCH_EOF'
import re, sys

filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

# 1. import socket 추가
if 'import socket' not in content:
    content = content.replace(
        'import time\n',
        'import socket\nimport time\n'
    )

# 2. CONNECTION_CHECK_INTERVAL 상수 추가
if 'CONNECTION_CHECK_INTERVAL' not in content:
    content = content.replace(
        'from mqtt_pkg.runtime import AWSIoTClient\n',
        'from mqtt_pkg.runtime import AWSIoTClient\n\nCONNECTION_CHECK_INTERVAL = 6  # 5초 × 6 = 30초마다 연결 상태 확인\n'
    )

# 3. _wait_for_network + _connect_with_service_retry 함수 추가
new_functions = '''

def _wait_for_network(timeout_per_check: int = 3, interval: int = 10) -> None:
    """네트워크 연결 가능할 때까지 대기 (부팅 직후 네트워크 미준비 대응)."""
    while True:
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=timeout_per_check)
            print("[MQTT][INIT] network_ready=true")
            return
        except OSError:
            print(f"[MQTT][INIT] network_ready=false retry_after={interval}s")
            time.sleep(interval)


def _connect_with_service_retry(aws_client: AWSIoTClient) -> object:
    """connect_mqtt()를 서비스 레벨에서 무한 재시도 (서비스가 crash하지 않도록)."""
    attempt = 0
    base_delay = 10
    max_delay = 120
    while True:
        try:
            return aws_client.connect_mqtt()
        except Exception as exc:
            attempt += 1
            delay = min(base_delay * (2 ** min(attempt - 1, 6)), max_delay)
            print(
                f"[MQTT][CONNECT] service_retry attempt={attempt} "
                f"error={type(exc).__name__} next_retry={delay}s"
            )
            time.sleep(delay)

'''

if '_wait_for_network' not in content:
    # log_startup_report 함수 뒤에 삽입
    content = content.replace(
        '\ndef main() -> None:',
        new_functions + '\ndef main() -> None:'
    )

# 4. main()에서 connect_mqtt() 직접 호출을 래핑
content = content.replace(
    '    connection = aws_client.connect_mqtt()\n    runtime.set_connection(connection)',
    '    _wait_for_network()\n    connection = _connect_with_service_retry(aws_client)\n    runtime.set_connection(connection)'
)

# 5. connection check 주기 변경: 12 → CONNECTION_CHECK_INTERVAL + 즉시 감지
old_loop = '''    try:
        connection_check_counter = 0
        while True:
            state.publish_device_state()
            connection_check_counter += 1
            if connection_check_counter >= 12:'''

new_loop = '''    try:
        connection_check_counter = 0
        while True:
            # 연결 끊김 감지 시 즉시 재연결 시도
            if not runtime.is_connected():
                connection_check_counter = CONNECTION_CHECK_INTERVAL

            state.publish_device_state()
            connection_check_counter += 1
            if connection_check_counter >= CONNECTION_CHECK_INTERVAL:'''

content = content.replace(old_loop, new_loop)

with open(filepath, 'w') as f:
    f.write(content)

print("  mqtt.py 패치 완료")
PATCH_EOF

python3 /tmp/_patch_mqtt.py "$APP_DIR/mqtt.py"
log "  mqtt.py 패치 완료"

# ==============================================================================
# 패치 2: mqtt_pkg/runtime.py — 재연결 무한 재시도 + keep_alive 30초
# ==============================================================================
log "패치 적용: mqtt_pkg/runtime.py"

cat > /tmp/_patch_runtime.py << 'PATCH_EOF'
import sys

filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

# 1. 상수 교체
content = content.replace(
    'MAX_RECONNECT_ATTEMPTS = 5\nRECONNECT_DELAY = 30  # seconds',
    '''_pending_resubscribe: bool = False

# 재연결 설정: 무한 재시도 + 점진적 백오프
RECONNECT_BACKOFF_THRESHOLD = 5   # 이 횟수까지는 즉시 재시도
RECONNECT_BASE_DELAY = 10         # 백오프 시작 대기(초)
RECONNECT_MAX_DELAY = 300         # 최대 대기(초, 5분)'''
)

# 1-1. MAX_RECONNECT_ATTEMPTS 참조 제거 (on_interrupted 로그)
content = content.replace(
    'f"[MQTT][CONNECT] reconnect_attempt={reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS}"',
    'f"[MQTT][CONNECT] reconnect_attempt={reconnect_attempts + 1}"'
)

# 2. keep_alive 300 → 30
content = content.replace('keep_alive_secs=300', 'keep_alive_secs=30')

# 3. on_resumed 콜백에 _pending_resubscribe 추가
old_on_resumed = '''        def on_resumed(connection, return_code, session_present, **kwargs):
            mark_connected(return_code == 0)
            if return_code == 0:
                reset_reconnect_attempts()
                print(
                    "[MQTT][CONNECT][OK] resumed "
                    f"return_code={return_code} session_present={session_present}"
                )
            else:
                print(f"[MQTT][CONNECT][FAIL] resumed return_code={return_code}")'''

new_on_resumed = '''        def on_resumed(connection, return_code, session_present, **kwargs):
            global _pending_resubscribe
            mark_connected(return_code == 0)
            if return_code == 0:
                reset_reconnect_attempts()
                if not session_present:
                    _pending_resubscribe = True
                    print(
                        "[MQTT][CONNECT][OK] resumed "
                        f"return_code={return_code} session_present={session_present} "
                        "resubscribe_pending=true"
                    )
                else:
                    print(
                        "[MQTT][CONNECT][OK] resumed "
                        f"return_code={return_code} session_present={session_present}"
                    )
            else:
                print(f"[MQTT][CONNECT][FAIL] resumed return_code={return_code}")'''

content = content.replace(old_on_resumed, new_on_resumed)

# 4. check_mqtt_connection 전체 교체
old_check = '''def check_mqtt_connection(
    topics: Iterable[str],
    callback: Callable,
    client_factory: Optional[Callable[[], AWSIoTClient]] = None,
) -> bool:
    """Ensure MQTT connection is alive, reconnecting and resubscribing if necessary."""
    if is_connected():
        reset_reconnect_attempts()
        return True

    print(f"[MQTT][RECONNECT] attempt={reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS}")
    if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
        print("[MQTT][RECONNECT][FAIL] reason=max_attempts_exceeded")
        return False

    increase_reconnect_attempt()

    connection = get_connection()
    if connection:
        try:
            connection.disconnect()
        except Exception:
            pass

    client = client_factory() if client_factory else AWSIoTClient()
    try:
        client.connect_mqtt()
    except Exception as exc:
        print(f"[MQTT][RECONNECT][FAIL] error={type(exc).__name__}")
        return False

    reset_reconnect_attempts()
    resubscribe_results = resubscribe(list(topics), callback)
    log_resubscribe_results(resubscribe_results)
    success_count, failed_count = summarize_resubscribe_results(resubscribe_results)
    overall_status = "success" if failed_count == 0 else "partial_failed"
    print(
        "[MQTT][RECONNECT] result "
        f"success={success_count} failed={failed_count} status={overall_status}"
    )
    return failed_count == 0'''

new_check = '''def needs_resubscribe() -> bool:
    """SDK on_connection_resumed에서 session_present=False 시 설정된 플래그 확인."""
    return _pending_resubscribe


def clear_resubscribe_flag() -> None:
    global _pending_resubscribe
    _pending_resubscribe = False


def check_mqtt_connection(
    topics: Iterable[str],
    callback: Callable,
    client_factory: Optional[Callable[[], AWSIoTClient]] = None,
) -> bool:
    """Ensure MQTT connection is alive, reconnecting and resubscribing if necessary.

    재연결 실패 시 포기하지 않고 점진적 백오프로 계속 재시도한다.
    """
    # SDK 자동 재연결 후 session이 없으면 resubscribe 필요
    if needs_resubscribe() and is_connected():
        clear_resubscribe_flag()
        print("[MQTT][RECONNECT] resubscribe after session loss")
        resubscribe_results = resubscribe(list(topics), callback)
        log_resubscribe_results(resubscribe_results)
        return True

    if is_connected():
        reset_reconnect_attempts()
        return True

    increase_reconnect_attempt()

    # 점진적 백오프: threshold 초과 시 대기
    if reconnect_attempts > RECONNECT_BACKOFF_THRESHOLD:
        backoff_exp = reconnect_attempts - RECONNECT_BACKOFF_THRESHOLD
        delay = min(RECONNECT_BASE_DELAY * (2 ** (backoff_exp - 1)), RECONNECT_MAX_DELAY)
        print(
            f"[MQTT][RECONNECT] attempt={reconnect_attempts} "
            f"backoff_delay={delay}s"
        )
        time.sleep(delay)
    else:
        print(f"[MQTT][RECONNECT] attempt={reconnect_attempts}")

    connection = get_connection()
    if connection:
        try:
            connection.disconnect()
        except Exception:
            pass

    client = client_factory() if client_factory else AWSIoTClient()
    try:
        client.connect_mqtt()
    except Exception as exc:
        print(f"[MQTT][RECONNECT][FAIL] error={type(exc).__name__}")
        return False

    reset_reconnect_attempts()
    resubscribe_results = resubscribe(list(topics), callback)
    log_resubscribe_results(resubscribe_results)
    success_count, failed_count = summarize_resubscribe_results(resubscribe_results)
    overall_status = "success" if failed_count == 0 else "partial_failed"
    print(
        "[MQTT][RECONNECT] result "
        f"success={success_count} failed={failed_count} status={overall_status}"
    )
    return failed_count == 0'''

content = content.replace(old_check, new_check)

with open(filepath, 'w') as f:
    f.write(content)

print("  runtime.py 패치 완료")
PATCH_EOF

python3 /tmp/_patch_runtime.py "$APP_DIR/mqtt_pkg/runtime.py"
log "  mqtt_pkg/runtime.py 패치 완료"

# ==============================================================================
# 패치 3: mqtt_pkg/publisher.py — 연결 상태 체크 강화
# ==============================================================================
log "패치 적용: mqtt_pkg/publisher.py"

cat > /tmp/_patch_publisher.py << 'PATCH_EOF'
import sys

filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

old_publish_check = '''def publish(payload: Dict[str, Any], response_topic: Optional[str] = None) -> None:
    connection = runtime.get_connection()
    if connection is None:
        print("❌ Konai publish 실패: MQTT 연결이 설정되지 않았습니다.")
        return

    target_topic = response_topic or settings.KONAI_TOPIC_RESPONSE
    if not target_topic:
        print("❌ Konai publish 실패: 대상 토픽을 확인할 수 없습니다.")
        return

    payload_type = payload.get("type", "(미설정)")'''

new_publish_check = '''def publish(payload: Dict[str, Any], response_topic: Optional[str] = None) -> None:
    connection = runtime.get_connection()
    target_topic = response_topic or settings.KONAI_TOPIC_RESPONSE
    payload_type = payload.get("type", "(미설정)")

    if connection is None or not runtime.is_connected():
        reason = "no_connection" if connection is None else "disconnected"
        print(
            f"[MQTT][PUBLISH][SKIP] topic={target_topic or '(없음)'} "
            f"type={payload_type} reason={reason}"
        )
        return

    if not target_topic:
        print(f"[MQTT][PUBLISH][SKIP] type={payload_type} reason=no_topic")
        return'''

content = content.replace(old_publish_check, new_publish_check)

with open(filepath, 'w') as f:
    f.write(content)

print("  publisher.py 패치 완료")
PATCH_EOF

python3 /tmp/_patch_publisher.py "$APP_DIR/mqtt_pkg/publisher.py"
log "  mqtt_pkg/publisher.py 패치 완료"

# ==============================================================================
# 패치 4: mqtt_pkg/state.py — 연결 끊김 로그 추가
# ==============================================================================
log "패치 적용: mqtt_pkg/state.py"

cat > /tmp/_patch_state.py << 'PATCH_EOF'
import sys

filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

# _log_disconnected_once 함수 추가
if '_log_disconnected_once' not in content:
    old_globals = "konai_last_entity_publish: Dict[str, Tuple[float, str]] = {}"
    new_globals = '''konai_last_entity_publish: Dict[str, Tuple[float, str]] = {}
_last_disconnected_log: float = 0.0


def _log_disconnected_once(caller: str) -> None:
    """연결 끊김 시 30초에 1번만 로그 출력 (로그 폭주 방지)."""
    global _last_disconnected_log
    now = time.time()
    if now - _last_disconnected_log >= 30:
        print(f"[MQTT][{caller}][SKIP] reason=disconnected")
        _last_disconnected_log = now'''
    content = content.replace(old_globals, new_globals)

# publish_bootstrap_all_states의 silent return에 로그 추가
content = content.replace(
    '''    if not runtime.is_connected():
        return

    try:
        response = requests.get(
            f"{settings.LOCAL_API_BASE}/local/api/states",''',
    '''    if not runtime.is_connected():
        _log_disconnected_once("BOOTSTRAP")
        return

    try:
        response = requests.get(
            f"{settings.LOCAL_API_BASE}/local/api/states",'''
)

# publish_device_state의 silent return에 로그 추가
content = content.replace(
    '''def publish_device_state() -> None:
    global konai_last_entity_publish

    if not runtime.is_connected():
        return''',
    '''def publish_device_state() -> None:
    global konai_last_entity_publish

    if not runtime.is_connected():
        _log_disconnected_once("ENTITY_CHANGED")
        return'''
)

# entity_changed 발행 로직: dedup window → 상태 변화 기반으로 변경
# 부팅 후 최초 1회는 전체 발행, 이후 변화 있을 때만 발행
old_dedup = '''            state_str = json.dumps(state_entry, sort_keys=True, ensure_ascii=False)
            last_info = konai_last_entity_publish.get(entity_id)
            now = time.time()
            if last_info:
                last_ts, last_val = last_info
                if now - last_ts < settings.KONAI_EVENT_THROTTLE_SEC:
                    continue
                if (
                    settings.KONAI_EVENT_DEDUP_WINDOW_SEC > 0
                    and (now - last_ts) < settings.KONAI_EVENT_DEDUP_WINDOW_SEC
                    and last_val == state_str
                ):
                    continue'''

new_dedup = '''            state_str = json.dumps(state_entry, sort_keys=True, ensure_ascii=False)
            last_info = konai_last_entity_publish.get(entity_id)
            now = time.time()
            if last_info:
                last_ts, last_val = last_info
                if now - last_ts < settings.KONAI_EVENT_THROTTLE_SEC:
                    continue
                if last_val == state_str:
                    continue  # 상태 변화 없으면 skip (부팅 후 최초 1회는 last_info 없어 발행됨)'''

content = content.replace(old_dedup, new_dedup)

with open(filepath, 'w') as f:
    f.write(content)

print("  state.py 패치 완료")
PATCH_EOF

python3 /tmp/_patch_state.py "$APP_DIR/mqtt_pkg/state.py"
log "  mqtt_pkg/state.py 패치 완료"

# --- 임시 파일 정리 ---
rm -f /tmp/_patch_mqtt.py /tmp/_patch_runtime.py /tmp/_patch_publisher.py /tmp/_patch_state.py

# --- __pycache__ 정리 (Python이 자동 재컴파일하도록) ---
log ".pyc 캐시 정리..."
find "$APP_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$APP_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true
log "  캐시 정리 완료"

# --- 서비스 재시작 ---
log "matterhub-mqtt 서비스 재시작..."
if systemctl is-active --quiet matterhub-mqtt.service 2>/dev/null; then
    sudo systemctl restart matterhub-mqtt.service
    log "  서비스 재시작 완료"
else
    log "  서비스가 실행 중이 아닙니다. 수동으로 시작하세요:"
    log "    sudo systemctl start matterhub-mqtt.service"
fi

# --- 검증 ---
log ""
log "=== 패치 적용 결과 ==="
sleep 3
if systemctl is-active --quiet matterhub-mqtt.service 2>/dev/null; then
    log "✅ matterhub-mqtt 서비스: active"
else
    log "⚠️  matterhub-mqtt 서비스: inactive (로그 확인 필요)"
fi

log ""
log "최근 로그 (15초):"
journalctl -u matterhub-mqtt.service --since "15 seconds ago" --no-pager 2>/dev/null || true

log ""
log "=== 패치 완료 ==="
log "백업 위치: $BACKUP_DIR"
log "로그 파일: $LOG_FILE"
log ""
log "검증 방법:"
log "  journalctl -u matterhub-mqtt.service -f"
log ""
log "롤백 방법:"
log "  bash $0 --rollback"
