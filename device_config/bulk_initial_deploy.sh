#!/bin/bash
# ========================================
# MatterHub 일괄 초기 배포 스크립트
# ========================================
#
# 릴레이 서버를 경유하여 모든 디바이스에:
# 1. git fetch + reset (최신 master)
# 2. NOPASSWD sudoers 설정
# 3. update_server.sh 실행 (bootstrap + systemd 마이그레이션)
# 4. 결과 검증 및 CSV 기록
#
# 사용법:
#   bash device_config/bulk_initial_deploy.sh [--dry-run]
#
# 환경변수:
#   RELAY_HOST: 릴레이 서버 (기본: 4.230.8.65)
#   RELAY_USER: 릴레이 사용자 (기본: kh-kim)
#   RELAY_KEY:  릴레이 SSH 키 (기본: /tmp/hyodol-slm-server-key.pem)
#   DEVICE_USER: 디바이스 사용자 (기본: hyodol)
#   DEVICE_KEY_ON_RELAY: 릴레이 내 디바이스 키 경로 (기본: /home/kh-kim/.ssh/id_s2edge)
#   SUDO_PASS: 디바이스 sudo 비밀번호 (기본: tech8123)
#   DEPLOY_BRANCH: 배포 브랜치 (기본: master)
#   BATCH_SIZE: 동시 배포 수 (기본: 8)
#   MAX_RETRIES: 접속 재시도 횟수 (기본: 3)
# ========================================

set -uo pipefail

# ── 설정 ──
RELAY_HOST="${RELAY_HOST:-4.230.8.65}"
RELAY_USER="${RELAY_USER:-kh-kim}"
RELAY_KEY="${RELAY_KEY:-/tmp/hyodol-slm-server-key.pem}"
DEVICE_USER="${DEVICE_USER:-hyodol}"
DEVICE_KEY_ON_RELAY="${DEVICE_KEY_ON_RELAY:-/home/kh-kim/.ssh/id_s2edge}"
SUDO_PASS="${SUDO_PASS:-tech8123}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-master}"
DRY_RUN=false
PROJECT_DIR="whatsmatter-hub-flask-server"
BATCH_SIZE="${BATCH_SIZE:-8}"
MAX_RETRIES="${MAX_RETRIES:-3}"
RETRY_DELAY=3

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
    esac
done

# ── 결과 디렉토리 ──
RESULT_DIR="/tmp/bulk_deploy_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULT_DIR"

# ── 디바이스 포트 목록 ──
DEVICE_PORTS=()

# 포트 목록 파일에서 로드
PORTS_FILE="${PORTS_FILE:-device_config/device_ports.txt}"
if [ -f "$PORTS_FILE" ]; then
    while IFS= read -r line; do
        # 빈 줄, 주석 건너뜀
        [[ -z "$line" || "$line" == \#* ]] && continue
        DEVICE_PORTS+=("$line")
    done < "$PORTS_FILE"
fi

if [ ${#DEVICE_PORTS[@]} -eq 0 ]; then
    echo "[ERROR] 디바이스 포트가 없습니다. $PORTS_FILE 파일을 설정하세요."
    exit 1
fi

echo "=========================================="
echo " MatterHub 일괄 초기 배포"
echo "=========================================="
echo "릴레이: ${RELAY_USER}@${RELAY_HOST}"
echo "디바이스 수: ${#DEVICE_PORTS[@]}"
echo "브랜치: $DEPLOY_BRANCH"
echo "배치 크기: $BATCH_SIZE"
echo "최대 재시도: $MAX_RETRIES"
echo "dry-run: $DRY_RUN"
echo "결과 디렉토리: $RESULT_DIR"
echo "=========================================="

# CSV 헤더
echo "port,status,matterhub_id,commit,mqtt_status,api_status" > "$RESULT_DIR/results.csv"
: > "$RESULT_DIR/offline.txt"
: > "$RESULT_DIR/failed.txt"

# ── 릴레이 SSH 명령 헬퍼 ──
relay_ssh() {
    ssh -i "$RELAY_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        "${RELAY_USER}@${RELAY_HOST}" "$@"
}

device_ssh() {
    local port="$1"
    shift
    # 중첩 인용부호 충돌 방지: heredoc으로 명령 전달
    relay_ssh "ssh -p $port -i $DEVICE_KEY_ON_RELAY -o StrictHostKeyChecking=no -o ConnectTimeout=15 ${DEVICE_USER}@localhost bash -s" <<EOF
$*
EOF
}

# ── 재시도 포함 디바이스 SSH ──
device_ssh_retry() {
    local port="$1"
    shift
    local attempt=0
    while [ $attempt -lt $MAX_RETRIES ]; do
        attempt=$((attempt + 1))
        if device_ssh "$port" "$@" 2>/dev/null; then
            return 0
        fi
        if [ $attempt -lt $MAX_RETRIES ]; then
            sleep $RETRY_DELAY
        fi
    done
    return 1
}

# ── 단일 디바이스 배포 ──
deploy_one() {
    local port="$1"
    local log_file="$RESULT_DIR/${port}.log"
    local status="FAIL"
    local matterhub_id="unknown"
    local commit="unknown"
    local mqtt_status="unknown"
    local api_status="unknown"

    {
        echo "── 디바이스 [$port] 시작 $(date '+%H:%M:%S') ──"

        if [ "$DRY_RUN" = "true" ]; then
            echo "[DRY-RUN] $port: 접속 테스트만 수행"
            if device_ssh_retry "$port" "echo OK"; then
                status="DRY-OK"
            else
                status="UNREACHABLE"
                echo "$port" >> "$RESULT_DIR/offline.txt"
            fi
            echo "port=$port status=$status"
            echo "$port,$status,,,," >> "$RESULT_DIR/results.csv"
            echo "[$status] $port"
            return 0
        fi

        # 1. 접속 테스트 (재시도 포함)
        echo "[$port] 접속 테스트..."
        if ! device_ssh_retry "$port" "echo OK"; then
            echo "[FAIL] $port: 접속 불가 ($MAX_RETRIES회 재시도 후)"
            echo "$port" >> "$RESULT_DIR/offline.txt"
            echo "$port,OFFLINE,,,," >> "$RESULT_DIR/results.csv"
            return 1
        fi

        # 2. resources/ 백업 + stash/untracked 정리 + git fetch + reset (120초 타임아웃)
        echo "[$port] resources/ 백업 + git 정리 + fetch + reset..."
        local res_backup="/tmp/matterhub_resources_backup_${port}"
        device_ssh "$port" "cd ~/$PROJECT_DIR && if [ -d resources ]; then rm -rf $res_backup; cp -a resources $res_backup; echo resources_backed_up; fi" 2>/dev/null
        device_ssh "$port" "cd ~/$PROJECT_DIR && git stash drop 2>/dev/null; git checkout -- . 2>/dev/null; git clean -fd 2>/dev/null" 2>/dev/null
        if ! device_ssh "$port" "cd ~/$PROJECT_DIR && timeout 120 git fetch origin $DEPLOY_BRANCH 2>&1 && git reset --hard origin/$DEPLOY_BRANCH 2>&1" 2>/dev/null; then
            echo "[FAIL] $port: git fetch/reset 실패"
            # resources/ 복원 (실패 시에도)
            device_ssh "$port" "cd ~/$PROJECT_DIR && if [ -d $res_backup ]; then mkdir -p resources; cp -a $res_backup/. resources/; rm -rf $res_backup; echo resources_restored; fi" 2>/dev/null
            echo "$port    git fetch/reset 실패" >> "$RESULT_DIR/failed.txt"
            echo "$port,FAIL_GIT,,,," >> "$RESULT_DIR/results.csv"
            return 1
        fi
        # resources/ 복원 (성공 시)
        device_ssh "$port" "cd ~/$PROJECT_DIR && if [ -d $res_backup ]; then mkdir -p resources; cp -a $res_backup/. resources/; rm -rf $res_backup; echo resources_restored; fi" 2>/dev/null

        # 3. NOPASSWD sudoers 설정
        echo "[$port] sudoers 설정..."
        device_ssh "$port" "
            if [ ! -f /etc/sudoers.d/matterhub-update ]; then
                echo ${SUDO_PASS} | sudo -S bash -c 'echo \"${DEVICE_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /usr/bin/install, /usr/bin/systemd-run\" > /etc/sudoers.d/matterhub-update && chmod 0440 /etc/sudoers.d/matterhub-update'
                echo sudoers OK
            else
                echo sudoers already exists
            fi
        " 2>/dev/null

        # 4. update_server.sh 실행 (300초 타임아웃)
        echo "[$port] update_server.sh 실행..."
        local update_id="bulk-update-$(date +%s)-${port}"
        device_ssh "$port" "cd ~/$PROJECT_DIR && timeout 300 bash device_config/update_server.sh $DEPLOY_BRANCH false $update_id unknown" 2>/dev/null
        local update_rc=$?
        if [ $update_rc -ne 0 ]; then
            echo "[WARN] $port: update_server.sh 비정상 종료 (rc=$update_rc)"
        fi

        # 5. 검증
        echo "[$port] 검증..."
        local verify
        verify=$(device_ssh "$port" "
            cd ~/$PROJECT_DIR
            COMMIT=\$(git log --oneline -1 2>/dev/null | head -c 60)
            MQTT_STATUS=\$(systemctl is-active matterhub-mqtt 2>/dev/null || echo inactive)
            API_STATUS=\$(systemctl is-active matterhub-api 2>/dev/null || echo inactive)
            HUB_ID=\$(grep -oP 'matterhub_id\s*=\s*\"?\K[^\"]+' .env 2>/dev/null || echo unknown)
            echo \"\$COMMIT|\$MQTT_STATUS|\$API_STATUS|\$HUB_ID\"
        " 2>/dev/null)

        # 결과 파싱
        commit=$(echo "$verify" | cut -d'|' -f1)
        mqtt_status=$(echo "$verify" | cut -d'|' -f2)
        api_status=$(echo "$verify" | cut -d'|' -f3)
        matterhub_id=$(echo "$verify" | cut -d'|' -f4)

        echo "[$port] commit=$commit mqtt=$mqtt_status api=$api_status hub_id=$matterhub_id"

        if [ "$mqtt_status" = "active" ]; then
            status="OK"
        elif [ $update_rc -ne 0 ]; then
            status="FAIL_UPDATE"
            echo "$port    update_server.sh 실패 (rc=$update_rc) mqtt=$mqtt_status" >> "$RESULT_DIR/failed.txt"
        else
            status="WARN"
            echo "$port    mqtt=$mqtt_status api=$api_status" >> "$RESULT_DIR/failed.txt"
        fi

        echo "$port,$status,$matterhub_id,$commit,$mqtt_status,$api_status" >> "$RESULT_DIR/results.csv"
        echo "[$status] $port 완료"

    } > "$log_file" 2>&1

    # 콘솔에도 요약 출력
    local last_line
    last_line=$(tail -1 "$log_file")
    echo "$last_line"

    [ "$status" = "OK" ] || [ "$status" = "DRY-OK" ]
}

# ── 배치 병렬 실행 ──
SUCCESS=0
FAIL=0
TOTAL=${#DEVICE_PORTS[@]}
PROCESSED=0

echo ""
echo "배치 실행 시작 (BATCH_SIZE=$BATCH_SIZE)..."
echo ""

for ((i = 0; i < TOTAL; i += BATCH_SIZE)); do
    batch_end=$((i + BATCH_SIZE))
    [ $batch_end -gt $TOTAL ] && batch_end=$TOTAL
    batch_num=$(( (i / BATCH_SIZE) + 1 ))
    total_batches=$(( (TOTAL + BATCH_SIZE - 1) / BATCH_SIZE ))

    echo "── 배치 ${batch_num}/${total_batches} (포트 ${DEVICE_PORTS[$i]}~${DEVICE_PORTS[$((batch_end - 1))]}) ──"

    # 배치 내 병렬 실행
    pids=()
    batch_ports=()
    for ((j = i; j < batch_end; j++)); do
        port="${DEVICE_PORTS[$j]}"
        batch_ports+=("$port")
        deploy_one "$port" &
        pids+=($!)
    done

    # 배치 내 모든 프로세스 대기
    for k in "${!pids[@]}"; do
        pid="${pids[$k]}"
        port="${batch_ports[$k]}"
        if wait "$pid"; then
            SUCCESS=$((SUCCESS + 1))
        else
            FAIL=$((FAIL + 1))
        fi
        PROCESSED=$((PROCESSED + 1))
    done

    echo "── 배치 ${batch_num} 완료 (진행: ${PROCESSED}/${TOTAL}) ──"
    echo ""

    # 배치 간 짧은 대기 (릴레이 부하 방지)
    if [ $batch_end -lt $TOTAL ]; then
        sleep 2
    fi
done

# ── 결과 요약 ──
echo ""
echo "=========================================="
echo " 배포 결과 요약"
echo "=========================================="
echo " 총 디바이스: $TOTAL"
echo " 성공 (OK):   $SUCCESS"
echo " 실패/경고:   $FAIL"
echo "=========================================="
echo ""
echo "결과 파일:"
echo "  CSV:      $RESULT_DIR/results.csv"
echo "  오프라인: $RESULT_DIR/offline.txt ($(wc -l < "$RESULT_DIR/offline.txt" | tr -d ' ')건)"
echo "  실패:     $RESULT_DIR/failed.txt ($(wc -l < "$RESULT_DIR/failed.txt" | tr -d ' ')건)"
echo ""

# 오프라인/실패 목록 출력
if [ -s "$RESULT_DIR/offline.txt" ]; then
    echo "── 오프라인 디바이스 ──"
    cat "$RESULT_DIR/offline.txt"
    echo ""
fi

if [ -s "$RESULT_DIR/failed.txt" ]; then
    echo "── 실패 디바이스 ──"
    cat "$RESULT_DIR/failed.txt"
    echo ""
fi

echo "이제 MQTT 토픽으로 원격 업데이트 가능:"
echo "  토픽: matterhub/update/specific/{mqtt_id}"
echo "  페이로드: {\"command\":\"git_update\",\"update_id\":\"...\",\"branch\":\"master\",\"force_update\":false}"
