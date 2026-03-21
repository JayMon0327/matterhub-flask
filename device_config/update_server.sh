#!/bin/bash

# ========================================
# WhatsMatter Hub 자동 업데이트 스크립트
# ========================================
#
# 사용법:
# ./update_server.sh [branch] [force_update] [update_id] [hub_id] [flags]
#
# 매개변수:
#   branch: Git 브랜치 (기본값: master)
#   force_update: 강제 업데이트 여부 (기본값: false)
#   update_id: 업데이트 ID (기본값: unknown)
#   hub_id: Hub ID (기본값: unknown)
#
# 플래그:
#   --skip-restart: git pull만 수행하고 서비스 재시작 건너뜀
#   --restart-only: 서비스 재시작만 수행 (git pull 건너뜀)
#
# 예시:
#   ./update_server.sh master false update_20241201_143022 whatsmatter-nipa_SN-1752303557
#   ./update_server.sh master false uid-001 hub-001 --skip-restart
#   ./update_server.sh master false uid-001 hub-001 --restart-only
# ========================================

set -uo pipefail

# ── 경로 자동 감지 ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/update.log"

cd "$PROJECT_ROOT"

echo "=== MQTT 자동 업데이트 시작 $(date) ===" | tee -a "$LOG_FILE"

# ── 매개변수 처리 ──
BRANCH=${1:-"master"}
FORCE_UPDATE=${2:-"false"}
UPDATE_ID=${3:-"unknown"}
HUB_ID=${4:-"unknown"}
SKIP_RESTART=false
RESTART_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --skip-restart) SKIP_RESTART=true ;;
        --restart-only) RESTART_ONLY=true ;;
    esac
done

echo "[INFO] 업데이트 매개변수:" | tee -a "$LOG_FILE"
echo "[INFO]   - 브랜치: $BRANCH" | tee -a "$LOG_FILE"
echo "[INFO]   - 강제 업데이트: $FORCE_UPDATE" | tee -a "$LOG_FILE"
echo "[INFO]   - 업데이트 ID: $UPDATE_ID" | tee -a "$LOG_FILE"
echo "[INFO]   - Hub ID: $HUB_ID" | tee -a "$LOG_FILE"
echo "[INFO]   - skip-restart: $SKIP_RESTART" | tee -a "$LOG_FILE"
echo "[INFO]   - restart-only: $RESTART_ONLY" | tee -a "$LOG_FILE"

# ── 프로세스 매니저 감지 ──
detect_process_manager() {
    # 1. 개별 systemd 서비스 우선 확인
    if systemctl list-unit-files matterhub-mqtt.service &>/dev/null 2>&1; then
        echo "systemd"
        return
    fi
    # 2. PM2 감지 (wm-* 또는 matter 프로세스)
    local pm2_bin=""
    pm2_bin="$(command -v pm2 2>/dev/null || echo "")"
    if [ -z "$pm2_bin" ]; then
        for candidate in /home/*/.nvm/versions/node/*/bin/pm2; do
            [ -x "$candidate" ] && pm2_bin="$candidate" && break
        done
    fi
    if [ -n "$pm2_bin" ] && "$pm2_bin" list 2>/dev/null | grep -qE 'wm-|matter'; then
        echo "pm2:$pm2_bin"
        return
    fi
    # 3. 구형 단일 서비스 감지 (matterhub.service 또는 matterhub-once.service)
    if systemctl list-unit-files matterhub.service &>/dev/null 2>&1 || \
       systemctl list-unit-files matterhub-once.service &>/dev/null 2>&1; then
        echo "legacy-systemd"
        return
    fi
    echo "unknown"
}

PROC_MANAGER="$(detect_process_manager)"
echo "[INFO] 프로세스 매니저: $PROC_MANAGER" | tee -a "$LOG_FILE"

# ── systemd 서비스 목록 ──
SYSTEMD_SERVICES=(
    "matterhub-api.service"
    "matterhub-mqtt.service"
    "matterhub-rule-engine.service"
    "matterhub-notifier.service"
    "matterhub-update-agent.service"
)

# ── venv / 사용자 자동 감지 ──
ensure_venv() {
    local venv_dir="$PROJECT_ROOT/venv"
    if [ ! -f "$venv_dir/bin/python" ]; then
        echo "[INFO] venv 생성 중 (--system-site-packages)..." | tee -a "$LOG_FILE"
        if python3 -m venv --system-site-packages "$venv_dir" 2>/dev/null; then
            echo "[INFO] venv 생성 완료: $venv_dir" | tee -a "$LOG_FILE"
        else
            echo "[WARN] venv 생성 실패 (python3-venv 미설치?). 시스템 Python으로 계속" | tee -a "$LOG_FILE"
        fi
    fi
}

detect_run_user() {
    stat -c '%U' "$PROJECT_ROOT" 2>/dev/null || ls -ld "$PROJECT_ROOT" | awk '{print $3}'
}

# ── systemd 유닛 설치 ──
install_systemd_units() {
    local run_user
    run_user="$(detect_run_user)"
    local systemd_dir="/etc/systemd/system"
    local tmp_dir
    tmp_dir="$(mktemp -d)"

    echo "[INFO] systemd 유닛 렌더링 (user=$run_user, root=$PROJECT_ROOT)" | tee -a "$LOG_FILE"

    ensure_venv

    python3 "$PROJECT_ROOT/device_config/render_systemd_units.py" \
        --project-root "$PROJECT_ROOT" \
        --run-user "$run_user" \
        --output-dir "$tmp_dir"

    for unit_file in "$tmp_dir"/matterhub-*.service; do
        [ -f "$unit_file" ] || continue
        local unit_name
        unit_name="$(basename "$unit_file")"
        sudo install -m 0644 "$unit_file" "$systemd_dir/$unit_name"
        echo "[INFO] 설치됨: $systemd_dir/$unit_name" | tee -a "$LOG_FILE"
    done

    rm -rf "$tmp_dir"

    sudo systemctl daemon-reload
    echo "[INFO] systemd daemon-reload 완료" | tee -a "$LOG_FILE"
}

# ── sudo 가용성 확인 (systemctl NOPASSWD 기준) ──
has_sudo() {
    sudo -n systemctl --version &>/dev/null
}

# ── PM2 프로세스 정의 (고객사 프로세스 제외) ──
PM2_WM_PROCESSES=("wm-app" "wm-mqtt" "wm-ruleEngine" "wm-notifier")

# ── PM2 전용 재시작 (sudo 없이 동작) ──
pm2_restart_services() {
    local pm2_bin="${PROC_MANAGER#pm2:}"
    echo "[INFO] PM2 전용 재시작 (sudo 미사용)" | tee -a "$LOG_FILE"

    for proc in "${PM2_WM_PROCESSES[@]}"; do
        if "$pm2_bin" describe "$proc" &>/dev/null; then
            "$pm2_bin" restart "$proc" --update-env 2>/dev/null || true
            echo "[INFO] PM2 재시작: $proc" | tee -a "$LOG_FILE"
        else
            echo "[WARN] PM2 프로세스 없음 (건너뜀): $proc" | tee -a "$LOG_FILE"
        fi
    done
    "$pm2_bin" save 2>/dev/null || true
    echo "[INFO] PM2 재시작 완료" | tee -a "$LOG_FILE"
}

# ── 서비스 제어 함수 ──
stop_services() {
    echo "[INFO] 서비스 중지 시작" | tee -a "$LOG_FILE"
    case "$PROC_MANAGER" in
        systemd)
            sudo systemctl stop "${SYSTEMD_SERVICES[@]}" 2>/dev/null || true
            ;;
        pm2:*)
            local pm2_bin="${PROC_MANAGER#pm2:}"
            if has_sudo; then
                # sudo 가능: systemd로 마이그레이션 예정이므로 PM2에서 삭제
                "$pm2_bin" list 2>/dev/null | grep -oP '(wm-\S+|matter(?!hub)\S*|slm-server)' | while read -r proc; do
                    "$pm2_bin" delete "$proc" 2>/dev/null || true
                    echo "[INFO] PM2 삭제: $proc" | tee -a "$LOG_FILE"
                done
                "$pm2_bin" save 2>/dev/null || true
            else
                # sudo 불가: PM2 유지, stop만 수행
                for proc in "${PM2_WM_PROCESSES[@]}"; do
                    "$pm2_bin" stop "$proc" 2>/dev/null || true
                    echo "[INFO] PM2 중지: $proc" | tee -a "$LOG_FILE"
                done
            fi
            ;;
        legacy-systemd)
            sudo systemctl stop matterhub.service 2>/dev/null || true
            sudo systemctl stop matterhub-once.service 2>/dev/null || true
            echo "[INFO] 구형 서비스 중지" | tee -a "$LOG_FILE"
            ;;
        *)
            echo "[WARN] 프로세스 매니저 미감지 — 서비스 중지 건너뜀" | tee -a "$LOG_FILE"
            ;;
    esac
}

restart_services() {
    echo "[INFO] 서비스 재시작 시작" | tee -a "$LOG_FILE"
    case "$PROC_MANAGER" in
        systemd)
            # 유닛 파일 재렌더링 (venv 상태 변경 등 반영)
            install_systemd_units
            sudo systemctl restart "${SYSTEMD_SERVICES[@]}"
            ;;
        pm2:*|legacy-systemd)
            # sudo 가용성 확인: 없으면 PM2 전용 재시작으로 fallback
            if ! has_sudo; then
                echo "[WARN] sudo 사용 불가 — systemd 마이그레이션 건너뜀" | tee -a "$LOG_FILE"
                if [[ "$PROC_MANAGER" == pm2:* ]]; then
                    pm2_restart_services
                else
                    echo "[ERROR] legacy-systemd인데 sudo 없음 — 재시작 불가" | tee -a "$LOG_FILE"
                    return 1
                fi
                return 0
            fi

            echo "[INFO] PM2/legacy→systemd 전환 시작" | tee -a "$LOG_FILE"

            # 1. 구형 단일 서비스 비활성화 (PM2 서비스는 아직 유지)
            for legacy_svc in matterhub.service matterhub-once.service; do
                if systemctl list-unit-files "$legacy_svc" &>/dev/null 2>&1; then
                    sudo systemctl disable "$legacy_svc" 2>/dev/null || true
                    sudo systemctl stop "$legacy_svc" 2>/dev/null || true
                    echo "[INFO] 구형 $legacy_svc 비활성화" | tee -a "$LOG_FILE"
                fi
            done

            # 2. systemd 유닛 설치 (PM2가 아직 살아있는 상태에서)
            install_systemd_units

            # 3. systemd 서비스 활성화 + 시작
            sudo systemctl enable "${SYSTEMD_SERVICES[@]}" 2>/dev/null || true
            sudo systemctl restart "${SYSTEMD_SERVICES[@]}"
            echo "[INFO] systemd 서비스 시작 완료" | tee -a "$LOG_FILE"

            # 4. systemd 서비스 정상 확인 후 PM2 정리
            sleep 3
            local systemd_ok=0
            for svc in "${SYSTEMD_SERVICES[@]}"; do
                systemctl is-active --quiet "$svc" 2>/dev/null && systemd_ok=$((systemd_ok + 1)) || true
            done
            if [ $systemd_ok -ge 2 ]; then
                echo "[INFO] systemd 서비스 정상($systemd_ok개) — PM2 정리 시작" | tee -a "$LOG_FILE"
                if [[ "$PROC_MANAGER" == pm2:* ]]; then
                    local pm2_bin="${PROC_MANAGER#pm2:}"
                    # PM2에서 wm-* 프로세스만 삭제 (고객사 프로세스 유지)
                    "$pm2_bin" list 2>/dev/null | grep -oP '(wm-\S+|matter(?!hub)\S*|slm-server)' | while read -r proc; do
                        "$pm2_bin" delete "$proc" 2>/dev/null || true
                        echo "[INFO] PM2 삭제: $proc" | tee -a "$LOG_FILE"
                    done
                    "$pm2_bin" save 2>/dev/null || true
                    # PM2 startup 서비스 비활성화 (disable만, stop은 하지 않음 — 고객사 프로세스 유지)
                    for svc in $(systemctl list-unit-files 'pm2-*.service' --no-legend 2>/dev/null | awk '{print $1}'); do
                        sudo systemctl disable "$svc" 2>/dev/null || true
                        echo "[INFO] PM2 서비스 disable: $svc" | tee -a "$LOG_FILE"
                    done
                fi
            else
                echo "[WARN] systemd 서비스 불안정($systemd_ok개) — PM2 유지" | tee -a "$LOG_FILE"
            fi
            ;;
        *)
            echo "[ERROR] 프로세스 매니저를 감지할 수 없습니다" | tee -a "$LOG_FILE"
            return 1
            ;;
    esac
}

healthcheck_services() {
    local max_wait=30
    local waited=0

    # PM2 fallback 모드: sudo 없이 PM2로 재시작한 경우
    if [[ "$PROC_MANAGER" == pm2:* ]] && ! has_sudo; then
        local pm2_bin="${PROC_MANAGER#pm2:}"
        while [ $waited -lt $max_wait ]; do
            local online_count=0
            for proc in "${PM2_WM_PROCESSES[@]}"; do
                if "$pm2_bin" describe "$proc" 2>/dev/null | grep -q 'status.*online'; then
                    online_count=$((online_count + 1))
                fi
            done
            if [ $online_count -ge 2 ]; then
                echo "[INFO] healthcheck 통과 (PM2): ${online_count}개 프로세스 online" | tee -a "$LOG_FILE"
                return 0
            fi
            sleep 5
            waited=$((waited + 5))
        done
        echo "[ERROR] healthcheck 실패 (PM2): 프로세스 기동 안됨 (${max_wait}초 대기)" | tee -a "$LOG_FILE"
        return 1
    fi

    # systemd 모드
    while [ $waited -lt $max_wait ]; do
        local active_count=0
        for svc in "${SYSTEMD_SERVICES[@]}"; do
            systemctl is-active --quiet "$svc" 2>/dev/null && active_count=$((active_count + 1)) || true
        done
        if [ $active_count -ge 2 ]; then
            echo "[INFO] healthcheck 통과: ${active_count}개 서비스 정상 기동" | tee -a "$LOG_FILE"
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
    done
    echo "[ERROR] healthcheck 실패: 서비스 기동 안됨 (${max_wait}초 대기)" | tee -a "$LOG_FILE"
    return 1
}

# ── .env 마이그레이션 ──
ensure_env_var() {
    local key="$1" value="$2"
    if ! grep -q "^${key}=" .env 2>/dev/null; then
        echo "${key}=${value}" >> .env
        echo "[INFO] .env에 ${key} 추가" | tee -a "$LOG_FILE"
    fi
}

# ── --restart-only 모드 ──
if [ "$RESTART_ONLY" = "true" ]; then
    echo "[INFO] --restart-only 모드: 서비스 재시작만 수행" | tee -a "$LOG_FILE"
    PRE_UPDATE_COMMIT=$(git rev-parse HEAD)

    restart_services
    if ! healthcheck_services; then
        echo "[WARN] 롤백 시작: $PRE_UPDATE_COMMIT" | tee -a "$LOG_FILE"
        stop_services
        git reset --hard "$PRE_UPDATE_COMMIT"
        restart_services
        cat > "/tmp/update_${UPDATE_ID}.rollback" << ROLLBACKEOF
{"rollback": true, "reverted_to": "$PRE_UPDATE_COMMIT"}
ROLLBACKEOF
        echo "[ERROR] 롤백 완료" | tee -a "$LOG_FILE"
        exit 1
    fi

    echo "[INFO] --restart-only 완료" | tee -a "$LOG_FILE"
    echo "=== MQTT 자동 업데이트 완료 (restart-only) ===" | tee -a "$LOG_FILE"
    exit 0
fi

# ── git pull 전 현재 커밋 저장 (롤백용) ──
PRE_UPDATE_COMMIT=$(git rev-parse HEAD)
echo "[INFO] 현재 커밋: $PRE_UPDATE_COMMIT" | tee -a "$LOG_FILE"

# ── Git 업데이트 ──
echo "[INFO] Git 업데이트 시작" | tee -a "$LOG_FILE"

# 현재 remote 설정 확인
echo "[INFO] 현재 Git remote 설정:" | tee -a "$LOG_FILE"
git remote -v 2>&1 | tee -a "$LOG_FILE"

# 현재 브랜치 확인
CURRENT_BRANCH=$(git branch --show-current)
echo "[INFO] 현재 브랜치: $CURRENT_BRANCH" | tee -a "$LOG_FILE"

# 브랜치가 다르면 체크아웃
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    echo "[INFO] 브랜치 변경: $CURRENT_BRANCH → $BRANCH" | tee -a "$LOG_FILE"
    if ! git checkout "$BRANCH"; then
        echo "[ERROR] 브랜치 체크아웃 실패: $BRANCH" | tee -a "$LOG_FILE"
        exit 1
    fi
fi

# .env 파일 보호
if [ -f .env ]; then
    echo "[INFO] .env 파일 처리 중..." | tee -a "$LOG_FILE"
    cp .env ".env.backup.$(date +%Y%m%d_%H%M%S)"
    git update-index --assume-unchanged .env 2>/dev/null || true
    git checkout -- .env 2>/dev/null || true
    echo "[INFO] .env 파일 처리 완료" | tee -a "$LOG_FILE"
fi

# 변경사항 처리
if git diff --quiet && git diff --cached --quiet; then
    echo "[INFO] 작업 디렉토리 깨끗함" | tee -a "$LOG_FILE"
else
    echo "[INFO] 변경사항 발견. stash 중..." | tee -a "$LOG_FILE"
    git stash push -m "Auto-stash before update $(date)"
    echo "[INFO] 변경사항을 stash에 저장 완료" | tee -a "$LOG_FILE"
fi

# Git pull 또는 강제 업데이트
GIT_SUCCESS=false
if [ "$FORCE_UPDATE" = "true" ]; then
    echo "[INFO] 강제 업데이트 모드 - git reset --hard origin/$BRANCH" | tee -a "$LOG_FILE"
    if git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH"; then
        GIT_SUCCESS=true
        echo "[INFO] 강제 업데이트 성공" | tee -a "$LOG_FILE"
    else
        echo "[ERROR] 강제 업데이트 실패" | tee -a "$LOG_FILE"
    fi
else
    echo "[INFO] Git pull 시작 (브랜치: $BRANCH)..." | tee -a "$LOG_FILE"
    if git pull origin "$BRANCH"; then
        GIT_SUCCESS=true
        echo "[INFO] Git pull 성공" | tee -a "$LOG_FILE"
    else
        echo "[ERROR] Git pull 실패" | tee -a "$LOG_FILE"
    fi
fi

# .env 복구
LATEST_ENV_BACKUP=$(ls -t .env.backup.* 2>/dev/null | head -1)
if [ -n "$LATEST_ENV_BACKUP" ]; then
    cp "$LATEST_ENV_BACKUP" .env
    git update-index --skip-worktree .env 2>/dev/null || true
    rm -f .env.backup.*
    echo "[INFO] .env 파일 복구 완료" | tee -a "$LOG_FILE"
fi

# stash 복원
if git stash list 2>/dev/null | grep -q "Auto-stash before update"; then
    echo "[INFO] stash된 변경사항 복원 시도 중..." | tee -a "$LOG_FILE"
    if git stash pop 2>/dev/null; then
        echo "[INFO] stash된 변경사항 복원 성공" | tee -a "$LOG_FILE"
    else
        echo "[WARN] stash 복원 실패 (충돌). stash에 보존됨" | tee -a "$LOG_FILE"
    fi
fi

if [ "$GIT_SUCCESS" != "true" ]; then
    echo "[ERROR] Git 업데이트 실패 — 종료" | tee -a "$LOG_FILE"
    exit 1
fi

CURRENT_COMMIT=$(git rev-parse HEAD)
LATEST_COMMIT=$(git log -1 --oneline)
echo "[INFO] 최신 커밋: $LATEST_COMMIT" | tee -a "$LOG_FILE"

# ── .env 마이그레이션: 필수 환경변수 보장 ──
ensure_env_var "SUBSCRIBE_MATTERHUB_TOPICS" '"1"'
ensure_env_var "MATTERHUB_VENDOR" '"konai"'

# ── --skip-restart 모드: 상태 파일만 작성하고 종료 ──
if [ "$SKIP_RESTART" = "true" ]; then
    cat > "/tmp/update_${UPDATE_ID}.status" << STATUSEOF
{"exit_code": 0, "commit": "$CURRENT_COMMIT", "pre_commit": "$PRE_UPDATE_COMMIT", "branch": "$BRANCH", "timestamp": $(date +%s)}
STATUSEOF
    echo "[INFO] 상태 파일 작성 완료 (restart 건너뜀)" | tee -a "$LOG_FILE"
    echo "=== MQTT 자동 업데이트 완료 (skip-restart) ===" | tee -a "$LOG_FILE"
    exit 0
fi

# ── 서비스 재시작 + 롤백 ──
echo "[INFO] 서비스 재시작 준비 중..." | tee -a "$LOG_FILE"
stop_services
sleep 3
restart_services

if ! healthcheck_services; then
    echo "[WARN] 롤백 시작: $PRE_UPDATE_COMMIT" | tee -a "$LOG_FILE"
    stop_services
    git reset --hard "$PRE_UPDATE_COMMIT"
    restart_services
    cat > "/tmp/update_${UPDATE_ID}.rollback" << ROLLBACKEOF
{"rollback": true, "reverted_to": "$PRE_UPDATE_COMMIT"}
ROLLBACKEOF
    echo "[ERROR] 롤백 완료" | tee -a "$LOG_FILE"
    exit 1
fi

echo "[INFO] 패치 완료 $(date)" | tee -a "$LOG_FILE"
echo "=== MQTT 자동 업데이트 완료 ===" | tee -a "$LOG_FILE"

exit 0
