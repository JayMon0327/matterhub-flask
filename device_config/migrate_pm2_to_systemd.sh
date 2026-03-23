#!/bin/bash
# ========================================
# PM2 → systemd 마이그레이션 스크립트
# ========================================
#
# MatterHub 프로세스만 PM2에서 systemd로 전환한다.
# Hyodol 프로세스(mqtt-api, check, heartbeat)는 PM2에 유지.
#
# 멱등성: systemd unit이 이미 설치되어 있고 active면 skip.
#
# 사용법:
#   bash device_config/migrate_pm2_to_systemd.sh [--force] [--dry-run]
#
# 플래그:
#   --force:   이미 설치된 unit이 있어도 재설치
#   --dry-run: 실제 변경 없이 수행할 작업만 출력
#
# 전제조건:
#   - sudo NOPASSWD 설정 (systemctl, install)
#   - render_systemd_units.py 존재
# ========================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/migrate_pm2_to_systemd.log"

FORCE=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --dry-run) DRY_RUN=true ;;
    esac
done

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== PM2 → systemd 마이그레이션 시작 ==="
log "PROJECT_ROOT=$PROJECT_ROOT"
log "FORCE=$FORCE, DRY_RUN=$DRY_RUN"

# ── sudo 가용성 확인 ──
if ! sudo -n systemctl --version &>/dev/null; then
    log "[ERROR] sudo NOPASSWD 사용 불가 — 마이그레이션 중단"
    log "[INFO] 먼저 sudoers 설정 필요: /etc/sudoers.d/matterhub-update"
    exit 1
fi

# ── 멱등성 확인: systemd unit이 이미 설치되어 있고 active면 skip ──
SYSTEMD_SERVICES=(
    "matterhub-api.service"
    "matterhub-mqtt.service"
    "matterhub-rule-engine.service"
    "matterhub-notifier.service"
    "matterhub-update-agent.service"
)

if [ "$FORCE" = "false" ]; then
    active_count=0
    for svc in "${SYSTEMD_SERVICES[@]}"; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            active_count=$((active_count + 1))
        fi
    done
    if [ $active_count -ge 2 ]; then
        log "[INFO] systemd 서비스 이미 활성화됨 (${active_count}개 active) — 마이그레이션 불필요"
        exit 0
    fi
fi

# ── 실행 유저 감지 ──
detect_run_user() {
    stat -c '%U' "$PROJECT_ROOT" 2>/dev/null || ls -ld "$PROJECT_ROOT" | awk '{print $3}'
}

RUN_USER="$(detect_run_user)"
log "[INFO] 실행 유저: $RUN_USER"

# ── venv 확인/생성 ──
cleanup_broken_venv() {
    local venv_dir="$PROJECT_ROOT/venv"
    if [ -d "$venv_dir" ] && [ -f "$venv_dir/bin/python" ]; then
        if ! "$venv_dir/bin/python" -c "import requests" 2>/dev/null; then
            log "[INFO] 부서진 venv 삭제 ($venv_dir)"
            rm -rf "$venv_dir"
        fi
    elif [ -d "$venv_dir" ] && [ ! -f "$venv_dir/bin/python" ]; then
        log "[INFO] 불완전한 venv 삭제"
        rm -rf "$venv_dir"
    fi
}

ensure_venv() {
    local venv_dir="$PROJECT_ROOT/venv"
    cleanup_broken_venv
    if [ ! -f "$venv_dir/bin/python" ]; then
        log "[INFO] venv 생성 중 (--system-site-packages)..."
        if python3 -m venv --system-site-packages "$venv_dir" 2>/dev/null; then
            log "[INFO] venv 생성 완료"
        else
            log "[WARN] venv 생성 실패 — 부서진 venv 삭제, 시스템 python3 사용"
            rm -rf "$venv_dir"
        fi
    fi
}

# ── Step 1: systemd unit 렌더링 및 설치 ──
install_systemd_units() {
    local systemd_dir="/etc/systemd/system"
    local tmp_dir
    tmp_dir="$(mktemp -d)"

    log "[INFO] systemd unit 렌더링 (user=$RUN_USER, root=$PROJECT_ROOT)"

    ensure_venv

    if [ "$DRY_RUN" = "true" ]; then
        log "[DRY-RUN] python3 render_systemd_units.py → $tmp_dir"
        python3 "$PROJECT_ROOT/device_config/render_systemd_units.py" \
            --project-root "$PROJECT_ROOT" \
            --run-user "$RUN_USER" \
            --output-dir "$tmp_dir"
        log "[DRY-RUN] 렌더링 결과:"
        for f in "$tmp_dir"/matterhub-*.service; do
            [ -f "$f" ] || continue
            log "[DRY-RUN]   $(basename "$f")"
        done
        rm -rf "$tmp_dir"
        return 0
    fi

    python3 "$PROJECT_ROOT/device_config/render_systemd_units.py" \
        --project-root "$PROJECT_ROOT" \
        --run-user "$RUN_USER" \
        --output-dir "$tmp_dir"

    for unit_file in "$tmp_dir"/matterhub-*.service; do
        [ -f "$unit_file" ] || continue
        local unit_name
        unit_name="$(basename "$unit_file")"
        sudo install -m 0644 "$unit_file" "$systemd_dir/$unit_name"
        log "[INFO] 설치됨: $systemd_dir/$unit_name"
    done

    rm -rf "$tmp_dir"

    sudo systemctl daemon-reload
    log "[INFO] systemd daemon-reload 완료"
}

# ── Step 2: 구형 단일 서비스 비활성화 ──
disable_legacy_units() {
    for legacy_svc in matterhub.service matterhub-once.service; do
        if systemctl list-unit-files "$legacy_svc" &>/dev/null 2>&1; then
            if [ "$DRY_RUN" = "true" ]; then
                log "[DRY-RUN] disable + stop: $legacy_svc"
            else
                sudo systemctl disable "$legacy_svc" 2>/dev/null || true
                sudo systemctl stop "$legacy_svc" 2>/dev/null || true
                log "[INFO] 구형 $legacy_svc 비활성화"
            fi
        fi
    done
}

# ── Step 3: systemd 서비스 활성화 + 시작 ──
start_systemd_services() {
    if [ "$DRY_RUN" = "true" ]; then
        log "[DRY-RUN] systemctl enable + start: ${SYSTEMD_SERVICES[*]}"
        return 0
    fi

    sudo systemctl enable "${SYSTEMD_SERVICES[@]}" 2>/dev/null || true
    sudo systemctl restart "${SYSTEMD_SERVICES[@]}"
    log "[INFO] systemd 서비스 시작 완료"

    # 기동 확인 (3초 대기)
    sleep 3
    local active_count=0
    for svc in "${SYSTEMD_SERVICES[@]}"; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            active_count=$((active_count + 1))
            log "[INFO]   $svc: active"
        else
            log "[WARN]   $svc: inactive/failed"
        fi
    done
    log "[INFO] systemd 서비스 상태: ${active_count}/${#SYSTEMD_SERVICES[@]} active"

    if [ $active_count -lt 2 ]; then
        log "[ERROR] systemd 서비스 불안정 — PM2 정리 건너뜀"
        return 1
    fi
    return 0
}

# ── Step 4: PM2에서 MatterHub 프로세스 제거 ──
cleanup_pm2() {
    # PM2 바이너리 찾기
    local pm2_bin=""
    pm2_bin="$(command -v pm2 2>/dev/null || echo "")"
    if [ -z "$pm2_bin" ]; then
        for candidate in /home/*/.nvm/versions/node/*/bin/pm2; do
            [ -x "$candidate" ] && pm2_bin="$candidate" && break
        done
    fi

    if [ -z "$pm2_bin" ]; then
        log "[INFO] PM2 미설치 — 정리 불필요"
        return 0
    fi

    log "[INFO] PM2 정리 시작 (바이너리: $pm2_bin)"

    # MatterHub 관련 프로세스만 삭제 (wm-*, matter*, slm-server)
    # Hyodol 프로세스(mqtt-api, check, heartbeat)는 유지
    local pm2_procs
    pm2_procs=$("$pm2_bin" list 2>/dev/null | grep -oP '(wm-\S+|matter(?!hub)\S*|slm-server)' || true)

    if [ -z "$pm2_procs" ]; then
        log "[INFO] PM2에 MatterHub 프로세스 없음"
    else
        while IFS= read -r proc; do
            [ -z "$proc" ] && continue
            if [ "$DRY_RUN" = "true" ]; then
                log "[DRY-RUN] pm2 delete: $proc"
            else
                "$pm2_bin" delete "$proc" 2>/dev/null || true
                log "[INFO] PM2 삭제: $proc"
            fi
        done <<< "$pm2_procs"
    fi

    if [ "$DRY_RUN" = "false" ]; then
        "$pm2_bin" save 2>/dev/null || true
    fi

    # PM2 startup 서비스 disable (stop은 하지 않음 — Hyodol 프로세스 유지)
    for svc in $(systemctl list-unit-files 'pm2-*.service' --no-legend 2>/dev/null | awk '{print $1}'); do
        if [ "$DRY_RUN" = "true" ]; then
            log "[DRY-RUN] systemctl disable: $svc"
        else
            sudo systemctl disable "$svc" 2>/dev/null || true
            log "[INFO] PM2 서비스 disable: $svc"
        fi
    done

    log "[INFO] PM2 정리 완료"
}

# ── 실행 ──
install_systemd_units
disable_legacy_units

if start_systemd_services; then
    cleanup_pm2
else
    log "[WARN] systemd 불안정 — PM2 유지. 수동 확인 필요"
    exit 1
fi

log "=== PM2 → systemd 마이그레이션 완료 ==="
exit 0
