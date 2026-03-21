#!/bin/bash
# ========================================
# MatterHub 일괄 초기 배포 스크립트
# ========================================
#
# 릴레이 서버를 경유하여 모든 디바이스에:
# 1. git pull (최신 master)
# 2. auto_bootstrap (cert 심링크, .env 마이그레이션, sudoers)
# 3. NOPASSWD sudoers 설정 (sudo 비밀번호 사용)
# 4. PM2 서비스 재시작
#
# 이후에는 MQTT 토픽으로 원격 업데이트 가능
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

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
    esac
done

# ── 디바이스 포트 목록 ──
# 릴레이 서버의 reverse tunnel 포트 = 디바이스 식별자
# 포트 목록을 여기에 추가하거나 파일에서 읽기
DEVICE_PORTS=(
    # 예시: 릴레이 포트 번호들
    15093
    # 아래에 다른 디바이스 포트를 추가
    # 15094
    # 15095
)

# 포트 목록 파일이 있으면 로드
PORTS_FILE="${PORTS_FILE:-device_config/device_ports.txt}"
if [ -f "$PORTS_FILE" ]; then
    while IFS= read -r line; do
        # 빈 줄, 주석 건너뜀
        [[ -z "$line" || "$line" == \#* ]] && continue
        DEVICE_PORTS+=("$line")
    done < "$PORTS_FILE"
fi

if [ ${#DEVICE_PORTS[@]} -eq 0 ]; then
    echo "[ERROR] 디바이스 포트가 없습니다. DEVICE_PORTS 배열이나 $PORTS_FILE 파일을 설정하세요."
    exit 1
fi

echo "=========================================="
echo " MatterHub 일괄 초기 배포"
echo "=========================================="
echo "릴레이: ${RELAY_USER}@${RELAY_HOST}"
echo "디바이스 수: ${#DEVICE_PORTS[@]}"
echo "브랜치: $DEPLOY_BRANCH"
echo "dry-run: $DRY_RUN"
echo "=========================================="

# ── 릴레이 SSH 명령 헬퍼 ──
relay_ssh() {
    ssh -i "$RELAY_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        "${RELAY_USER}@${RELAY_HOST}" "$@"
}

device_ssh() {
    local port="$1"
    shift
    relay_ssh "ssh -p $port -i $DEVICE_KEY_ON_RELAY -o StrictHostKeyChecking=no -o ConnectTimeout=15 ${DEVICE_USER}@localhost '$*'"
}

# ── 단일 디바이스 배포 ──
deploy_one() {
    local port="$1"
    local status="FAIL"

    echo ""
    echo "── 디바이스 [$port] 시작 ──"

    if [ "$DRY_RUN" = "true" ]; then
        echo "[DRY-RUN] $port: 접속 테스트만 수행"
        device_ssh "$port" "echo OK" 2>/dev/null && status="DRY-OK" || status="UNREACHABLE"
        echo "[$status] $port"
        return
    fi

    # 1. 접속 테스트
    if ! device_ssh "$port" "echo OK" 2>/dev/null; then
        echo "[FAIL] $port: 접속 불가"
        return 1
    fi

    # 2. git pull
    echo "[$port] git pull..."
    device_ssh "$port" "cd ~/$PROJECT_DIR && git fetch origin $DEPLOY_BRANCH 2>&1 && git reset --hard origin/$DEPLOY_BRANCH 2>&1" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "[FAIL] $port: git pull 실패"
        return 1
    fi

    # 3. NOPASSWD sudoers 설정 (sudo 비밀번호 사용)
    echo "[$port] sudoers 설정..."
    device_ssh "$port" "
        if [ ! -f /etc/sudoers.d/matterhub-update ]; then
            echo '$SUDO_PASS' | sudo -S bash -c 'cat > /etc/sudoers.d/matterhub-update << SUDOEOF
# MatterHub 업데이트 스크립트용 NOPASSWD 설정
$DEVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /usr/bin/install, /usr/bin/systemd-run
SUDOEOF
chmod 0440 /etc/sudoers.d/matterhub-update'
            echo 'sudoers OK'
        else
            echo 'sudoers already exists'
        fi
    " 2>/dev/null

    # 4. update_server.sh 실행 (--skip-restart: git pull + bootstrap만)
    echo "[$port] bootstrap 실행..."
    device_ssh "$port" "cd ~/$PROJECT_DIR && bash device_config/update_server.sh $DEPLOY_BRANCH false initial-deploy-\$(date +%s) unknown --skip-restart" 2>/dev/null

    # 5. PM2 재시작 (NVM PATH 포함)
    echo "[$port] PM2 재시작..."
    device_ssh "$port" "
        export PATH=\$(find /home/$DEVICE_USER/.nvm/versions/node -maxdepth 2 -name bin -type d 2>/dev/null | head -1):\$PATH
        cd ~/$PROJECT_DIR
        pm2 restart wm-mqtt --update-env 2>/dev/null || pm2 start mqtt.py --name wm-mqtt --interpreter python3 --cwd ~/$PROJECT_DIR 2>/dev/null
        pm2 restart wm-app --update-env 2>/dev/null || pm2 start app.py --name wm-app --interpreter python3 --cwd ~/$PROJECT_DIR 2>/dev/null
        pm2 restart wm-ruleEngine --update-env 2>/dev/null || pm2 start sub/ruleEngine.py --name wm-ruleEngine --interpreter python3 --cwd ~/$PROJECT_DIR 2>/dev/null
        pm2 restart wm-notifier --update-env 2>/dev/null || pm2 start sub/notifier.py --name wm-notifier --interpreter python3 --cwd ~/$PROJECT_DIR 2>/dev/null
        pm2 save 2>/dev/null
    " 2>/dev/null

    # 6. 검증
    echo "[$port] 검증..."
    local verify
    verify=$(device_ssh "$port" "
        export PATH=\$(find /home/$DEVICE_USER/.nvm/versions/node -maxdepth 2 -name bin -type d 2>/dev/null | head -1):\$PATH
        cd ~/$PROJECT_DIR
        COMMIT=\$(git log --oneline -1)
        MQTT_STATUS=\$(pm2 describe wm-mqtt 2>/dev/null | grep -o 'online' | head -1)
        HUB_ID=\$(grep -oP 'matterhub_id\s*=\s*\"?\K[^\"]+' .env 2>/dev/null)
        SUBSCRIBE=\$(grep SUBSCRIBE_MATTERHUB_TOPICS .env 2>/dev/null | grep -o '1')
        echo \"commit=\$COMMIT mqtt=\$MQTT_STATUS hub_id=\$HUB_ID subscribe=\$SUBSCRIBE\"
    " 2>/dev/null)

    echo "[$port] $verify"

    if echo "$verify" | grep -q "mqtt=online"; then
        status="OK"
    else
        status="WARN"
    fi

    echo "[$status] $port 완료"
}

# ── 일괄 실행 ──
SUCCESS=0
FAIL=0
TOTAL=${#DEVICE_PORTS[@]}

for port in "${DEVICE_PORTS[@]}"; do
    if deploy_one "$port"; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=========================================="
echo " 배포 결과: 총 $TOTAL / 성공 $SUCCESS / 실패 $FAIL"
echo "=========================================="
echo ""
echo "이제 MQTT 토픽으로 원격 업데이트 가능:"
echo "  토픽: matterhub/update/specific/{mqtt_id}"
echo "  페이로드: {\"command\":\"git_update\",\"update_id\":\"...\",\"branch\":\"master\",\"force_update\":false}"
