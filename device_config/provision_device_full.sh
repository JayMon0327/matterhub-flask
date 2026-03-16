#!/usr/bin/env bash
#=====================================================================
# provision_device_full.sh
#
# 운영자 PC에서 실행하는 원스톱 프로비저닝 스크립트
# 1) 라즈베리파이에 SSH 접속 → setup_initial_device.sh 실행
# 2) 장비 공개키 수집
# 3) relay에 허브 등록 (register_hub_on_relay.sh)
# 4) j 접속 검증
#
# 사용법:
#   bash device_config/provision_device_full.sh \
#     --device-ip 192.168.1.96 \
#     --device-ssh-user whatsmatter \
#     --device-ssh-password 'mat458496ad!' \
#     --support-remote-port 22961
#=====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 장비 접속 정보 ──
DEVICE_IP="${DEVICE_IP:-}"
DEVICE_SSH_USER="${DEVICE_SSH_USER:-whatsmatter}"
DEVICE_SSH_PASSWORD="${DEVICE_SSH_PASSWORD:-}"
DEVICE_SSH_PORT="${DEVICE_SSH_PORT:-22}"

# ── relay 정보 ──
RELAY_HOST="${RELAY_HOST:-3.38.126.167}"
RELAY_PORT="${RELAY_PORT:-443}"
RELAY_USER="${RELAY_USER:-ec2-user}"
RELAY_KEY_PATH="${RELAY_KEY_PATH:-$HOME/.ssh/matterhub-relay-operator-key.pem}"

# ── 장비 설정 옵션 ──
SUPPORT_REMOTE_PORT="${SUPPORT_REMOTE_PORT:-}"
SUPPORT_HOST="${SUPPORT_HOST:-$RELAY_HOST}"
SUPPORT_USER="${SUPPORT_USER:-whatsmatter}"
SUPPORT_DEVICE_USER="${SUPPORT_DEVICE_USER:-whatsmatter}"
DEVICE_RUN_USER="${DEVICE_RUN_USER:-whatsmatter}"

# ── setup_initial_device.sh 에 전달할 추가 옵션 ──
SKIP_OS_PACKAGES=0
HARDEN_REVERSE_TUNNEL_ONLY=0
HARDEN_LOCAL_CONSOLE_PAM=0
HARDEN_ALLOW_INBOUND_PORTS=()
EXTRA_SETUP_ARGS=()

# ── 동작 제어 ──
SKIP_DEVICE_SETUP=0
SKIP_RELAY_REGISTER=0
DRY_RUN=0

# ── Git 브랜치 (장비에 클론할 때 사용) ──
GIT_BRANCH="${GIT_BRANCH:-konai/20260211-v1.1}"
DEVICE_PROJECT_DIR="${DEVICE_PROJECT_DIR:-/home/$DEVICE_SSH_USER/Desktop/matterhub}"

log() {
  printf '\033[1;34m[provision]\033[0m %s\n' "$*"
}

err() {
  printf '\033[1;31m[provision] ERROR:\033[0m %s\n' "$*" >&2
}

usage() {
  cat <<'EOF'
Usage: bash device_config/provision_device_full.sh [options]

장비 접속:
  --device-ip <ip>                 장비 IP (필수)
  --device-ssh-user <user>         SSH 사용자 (기본: whatsmatter)
  --device-ssh-password <pw>       SSH 비밀번호 (필수)
  --device-ssh-port <port>         SSH 포트 (기본: 22)
  --device-project-dir <path>      장비 내 프로젝트 경로 (기본: ~/Desktop/matterhub)
  --device-run-user <user>         systemd 실행 사용자 (기본: whatsmatter)

Relay:
  --relay-host <host>              Relay 호스트 (기본: 3.38.126.167)
  --relay-port <port>              Relay SSH 포트 (기본: 443)
  --relay-user <user>              Relay 운영자 (기본: ec2-user)
  --relay-key <path>               Relay 운영자 키 (기본: ~/.ssh/matterhub-relay-operator-key.pem)

Support tunnel:
  --support-remote-port <port>     Reverse tunnel 포트 (필수)
  --support-host <host>            지원 서버 (기본: relay-host)
  --support-user <user>            지원 서버 접속 계정 (기본: whatsmatter)

Hardening:
  --harden-reverse-tunnel-only     Reverse tunnel only 모드
  --harden-allow-inbound-port <p>  Inbound 허용 포트 (반복 가능)
  --harden-local-console-pam       로컬 콘솔 PAM 하드닝

제어:
  --skip-device-setup              장비 설정 건너뜀 (relay 등록만)
  --skip-relay-register            relay 등록 건너뜀
  --skip-os-packages               OS 패키지 설치 건너뜀
  --git-branch <branch>            Git 브랜치 (기본: konai/20260211-v1.1)
  --extra-setup-arg <arg>          추가 setup_initial_device.sh 인수
  --dry-run                        실제 실행 없이 계획만 출력
  -h, --help                       도움말
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --device-ip)               DEVICE_IP="$2"; shift 2 ;;
    --device-ssh-user)         DEVICE_SSH_USER="$2"; shift 2 ;;
    --device-ssh-password)     DEVICE_SSH_PASSWORD="$2"; shift 2 ;;
    --device-ssh-port)         DEVICE_SSH_PORT="$2"; shift 2 ;;
    --device-project-dir)      DEVICE_PROJECT_DIR="$2"; shift 2 ;;
    --device-run-user)         DEVICE_RUN_USER="$2"; shift 2 ;;
    --relay-host)              RELAY_HOST="$2"; SUPPORT_HOST="$2"; shift 2 ;;
    --relay-port)              RELAY_PORT="$2"; shift 2 ;;
    --relay-user)              RELAY_USER="$2"; shift 2 ;;
    --relay-key)               RELAY_KEY_PATH="$2"; shift 2 ;;
    --support-remote-port)     SUPPORT_REMOTE_PORT="$2"; shift 2 ;;
    --support-host)            SUPPORT_HOST="$2"; shift 2 ;;
    --support-user)            SUPPORT_USER="$2"; shift 2 ;;
    --harden-reverse-tunnel-only) HARDEN_REVERSE_TUNNEL_ONLY=1; shift ;;
    --harden-allow-inbound-port)  HARDEN_ALLOW_INBOUND_PORTS+=("$2"); shift 2 ;;
    --harden-local-console-pam)   HARDEN_LOCAL_CONSOLE_PAM=1; shift ;;
    --skip-device-setup)       SKIP_DEVICE_SETUP=1; shift ;;
    --skip-relay-register)     SKIP_RELAY_REGISTER=1; shift ;;
    --skip-os-packages)        SKIP_OS_PACKAGES=1; shift ;;
    --git-branch)              GIT_BRANCH="$2"; shift 2 ;;
    --extra-setup-arg)         EXTRA_SETUP_ARGS+=("$2"); shift 2 ;;
    --dry-run)                 DRY_RUN=1; shift ;;
    -h|--help)                 usage; exit 0 ;;
    *)                         err "Unknown option: $1"; usage; exit 1 ;;
  esac
done

# ── 필수 파라미터 검증 ──
if [ -z "$DEVICE_IP" ]; then
  err "--device-ip 필수"
  exit 1
fi
if [ -z "$DEVICE_SSH_PASSWORD" ]; then
  err "--device-ssh-password 필수"
  exit 1
fi
if [ -z "$SUPPORT_REMOTE_PORT" ]; then
  err "--support-remote-port 필수"
  exit 1
fi
if [ ! -f "$RELAY_KEY_PATH" ]; then
  err "Relay operator key not found: $RELAY_KEY_PATH"
  exit 1
fi

# expect 필요 여부 확인
if ! command -v expect >/dev/null 2>&1; then
  err "expect 명령이 필요합니다. brew install expect (macOS) 또는 apt install expect"
  exit 1
fi

# ── 1) Relay hub-access 공개키 가져오기 ──
log "1/5 Relay hub-access 공개키 조회"
RELAY_HUB_ACCESS_PUBKEY="$(ssh -i "$RELAY_KEY_PATH" -p "$RELAY_PORT" \
  -o StrictHostKeyChecking=no -o BatchMode=yes \
  "${RELAY_USER}@${RELAY_HOST}" \
  'cat /home/ec2-user/.ssh/hub_access_ed25519.pub' 2>/dev/null)"

if [ -z "$RELAY_HUB_ACCESS_PUBKEY" ]; then
  err "Relay hub-access 공개키 조회 실패"
  exit 1
fi
log "  pubkey: ${RELAY_HUB_ACCESS_PUBKEY:0:50}..."

# ── 2) 장비에서 setup_initial_device.sh 실행 ──
if [ "$SKIP_DEVICE_SETUP" -eq 0 ]; then
  log "2/5 장비 설정 시작 (${DEVICE_SSH_USER}@${DEVICE_IP})"

  # 장비에서 실행할 원격 스크립트 생성
  REMOTE_SCRIPT=$(cat <<REMOTE_EOF
#!/bin/bash
set -euo pipefail

echo "▶ 소스 확인/업데이트"
if [ -d "$DEVICE_PROJECT_DIR" ]; then
  cd "$DEVICE_PROJECT_DIR"
  git pull --ff-only origin "$GIT_BRANCH" 2>/dev/null || true
else
  sudo apt update && sudo apt install -y git
  git clone -b "$GIT_BRANCH" https://github.com/JayMon0327/matterhub-flask.git "$DEVICE_PROJECT_DIR"
fi

cd "$DEVICE_PROJECT_DIR"

echo "▶ setup_initial_device.sh 실행"
SETUP_CMD=(bash device_config/setup_initial_device.sh)
SETUP_CMD+=(--setup-support-tunnel)
SETUP_CMD+=(--enable-support-tunnel-now)
SETUP_CMD+=(--support-host "$SUPPORT_HOST")
SETUP_CMD+=(--support-user "$SUPPORT_USER")
SETUP_CMD+=(--support-remote-port "$SUPPORT_REMOTE_PORT")
SETUP_CMD+=(--support-relay-operator-user "$RELAY_USER")
SETUP_CMD+=(--support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY")
REMOTE_EOF
)

  # skip-os-packages
  if [ "$SKIP_OS_PACKAGES" -eq 1 ]; then
    REMOTE_SCRIPT+=$'\nSETUP_CMD+=(--skip-os-packages)'
  fi

  # hardening
  if [ "$HARDEN_REVERSE_TUNNEL_ONLY" -eq 1 ]; then
    REMOTE_SCRIPT+=$'\nSETUP_CMD+=(--harden-reverse-tunnel-only)'
  fi
  for port in "${HARDEN_ALLOW_INBOUND_PORTS[@]-}"; do
    [ -z "$port" ] && continue
    REMOTE_SCRIPT+=$'\nSETUP_CMD+=(--harden-allow-inbound-port '"$port"')'
  done
  if [ "$HARDEN_LOCAL_CONSOLE_PAM" -eq 1 ]; then
    REMOTE_SCRIPT+=$'\nSETUP_CMD+=(--harden-local-console-pam)'
  fi

  # extra args
  for arg in "${EXTRA_SETUP_ARGS[@]-}"; do
    [ -z "$arg" ] && continue
    REMOTE_SCRIPT+=$'\nSETUP_CMD+=('"$arg"')'
  done

  REMOTE_SCRIPT+=$'\n"${SETUP_CMD[@]}"'
  REMOTE_SCRIPT+=$'\necho "===DEVICE_SETUP_DONE==="'

  if [ "$DRY_RUN" -eq 1 ]; then
    log "[dry-run] 장비에서 실행될 스크립트:"
    echo "$REMOTE_SCRIPT"
  else
    # expect를 통해 비밀번호 기반 SSH로 원격 실행
    expect -c "
set timeout 600
spawn ssh -o StrictHostKeyChecking=no -p $DEVICE_SSH_PORT ${DEVICE_SSH_USER}@${DEVICE_IP}
expect \"password:\"
send \"${DEVICE_SSH_PASSWORD}\r\"
expect \"\\\\$\"
send \"cat > /tmp/_matterhub_provision.sh << 'SCRIPT_END'\r\"
expect \"SCRIPT_END\"
send \"${DEVICE_SSH_PASSWORD}\r\"
expect \"\\\\$\"
" 2>&1 || true

    # 스크립트를 SCP로 전송
    TMP_SCRIPT="$(mktemp)"
    printf '%s\n' "$REMOTE_SCRIPT" > "$TMP_SCRIPT"

    expect -c "
set timeout 30
spawn scp -o StrictHostKeyChecking=no -P $DEVICE_SSH_PORT $TMP_SCRIPT ${DEVICE_SSH_USER}@${DEVICE_IP}:/tmp/_matterhub_provision.sh
expect \"password:\"
send \"${DEVICE_SSH_PASSWORD}\r\"
expect eof
" 2>&1

    rm -f "$TMP_SCRIPT"

    # 원격 실행
    log "  장비에서 스크립트 실행 중... (시간이 걸릴 수 있습니다)"
    expect -c "
set timeout 600
spawn ssh -o StrictHostKeyChecking=no -p $DEVICE_SSH_PORT ${DEVICE_SSH_USER}@${DEVICE_IP}
expect \"password:\"
send \"${DEVICE_SSH_PASSWORD}\r\"
expect \"\\\\$\"
send \"echo ${DEVICE_SSH_PASSWORD} | sudo -S bash /tmp/_matterhub_provision.sh 2>&1; echo ===PROVISION_EXIT_CODE=\\\$?===\r\"
expect {
  \"===DEVICE_SETUP_DONE===\" {
    puts \"\\n장비 설정 완료\"
  }
  timeout {
    puts \"\\n장비 설정 타임아웃\"
  }
}
expect \"\\\\$\"
send \"exit\r\"
expect eof
" 2>&1
  fi
else
  log "2/5 장비 설정 건너뜀 (--skip-device-setup)"
fi

# ── 3) 장비 공개키 수집 ──
log "3/5 장비 SSH 공개키 수집"

DEVICE_PUBKEY_REMOTE_PATH="/home/${DEVICE_RUN_USER}/.ssh/matterhub_support_tunnel_ed25519.pub"
TMP_PUBKEY="$(mktemp)"

if [ "$DRY_RUN" -eq 1 ]; then
  log "[dry-run] scp ${DEVICE_SSH_USER}@${DEVICE_IP}:${DEVICE_PUBKEY_REMOTE_PATH} -> $TMP_PUBKEY"
  echo "dry-run-placeholder-key" > "$TMP_PUBKEY"
else
  expect -c "
set timeout 15
spawn scp -o StrictHostKeyChecking=no -P $DEVICE_SSH_PORT ${DEVICE_SSH_USER}@${DEVICE_IP}:${DEVICE_PUBKEY_REMOTE_PATH} $TMP_PUBKEY
expect \"password:\"
send \"${DEVICE_SSH_PASSWORD}\r\"
expect eof
" 2>&1

  if [ ! -s "$TMP_PUBKEY" ]; then
    err "장비 공개키 수집 실패. 경로: $DEVICE_PUBKEY_REMOTE_PATH"
    rm -f "$TMP_PUBKEY"
    exit 1
  fi
  log "  pubkey: $(head -c 50 "$TMP_PUBKEY")..."
fi

# ── 4) 장비 matterhub_id 수집 ──
log "4/5 장비 matterhub_id 수집"

if [ "$DRY_RUN" -eq 1 ]; then
  HUB_ID="dry-run-hub-id"
  log "[dry-run] hub_id=$HUB_ID"
else
  HUB_ID="$(expect -c "
set timeout 15
spawn ssh -o StrictHostKeyChecking=no -p $DEVICE_SSH_PORT ${DEVICE_SSH_USER}@${DEVICE_IP}
expect \"password:\"
send \"${DEVICE_SSH_PASSWORD}\r\"
expect \"\\\\$\"
send \"grep '^matterhub_id=' ${DEVICE_PROJECT_DIR}/.env 2>/dev/null || echo mat458496ad! | sudo -S grep '^matterhub_id=' /opt/matterhub/app/.env 2>/dev/null || echo mat458496ad! | sudo -S grep '^matterhub_id=' /etc/matterhub/matterhub.env 2>/dev/null || echo 'NOT_FOUND'\r\"
expect \"\\\\$\"
send \"exit\r\"
expect eof
" 2>&1 | grep '^matterhub_id=' | tail -1 | sed 's/^matterhub_id=//' | tr -d '\"' | tr -d "'" | tr -d '\r')"

  if [ -z "$HUB_ID" ] || [ "$HUB_ID" = "NOT_FOUND" ]; then
    err "matterhub_id를 찾을 수 없습니다. run_provision.py를 먼저 실행하세요."
    rm -f "$TMP_PUBKEY"
    exit 1
  fi
  log "  hub_id=$HUB_ID"
fi

# ── 5) Relay에 허브 등록 ──
if [ "$SKIP_RELAY_REGISTER" -eq 0 ]; then
  log "5/5 Relay에 허브 등록"

  if [ "$DRY_RUN" -eq 1 ]; then
    log "[dry-run] bash $SCRIPT_DIR/register_hub_on_relay.sh \\"
    log "  --relay-host $RELAY_HOST --relay-port $RELAY_PORT \\"
    log "  --relay-user $RELAY_USER --relay-key $RELAY_KEY_PATH \\"
    log "  --hub-id $HUB_ID --remote-port $SUPPORT_REMOTE_PORT \\"
    log "  --hub-pubkey $TMP_PUBKEY --device-user $SUPPORT_DEVICE_USER"
  else
    bash "$SCRIPT_DIR/register_hub_on_relay.sh" \
      --relay-host "$RELAY_HOST" \
      --relay-port "$RELAY_PORT" \
      --relay-user "$RELAY_USER" \
      --relay-key "$RELAY_KEY_PATH" \
      --hub-id "$HUB_ID" \
      --remote-port "$SUPPORT_REMOTE_PORT" \
      --hub-pubkey "$TMP_PUBKEY" \
      --device-user "$SUPPORT_DEVICE_USER"
  fi
else
  log "5/5 Relay 등록 건너뜀 (--skip-relay-register)"
fi

rm -f "$TMP_PUBKEY"

# ── 6) 검증 ──
if [ "$DRY_RUN" -eq 0 ] && [ "$SKIP_RELAY_REGISTER" -eq 0 ]; then
  log "검증: j $HUB_ID 접속 테스트"
  VERIFY_RESULT="$(ssh -i "$RELAY_KEY_PATH" -p "$RELAY_PORT" \
    -o StrictHostKeyChecking=no -o BatchMode=yes \
    "${RELAY_USER}@${RELAY_HOST}" \
    "j $HUB_ID echo TUNNEL_VERIFY_OK" 2>&1 || true)"

  if echo "$VERIFY_RESULT" | grep -q "TUNNEL_VERIFY_OK"; then
    log "✅ j $HUB_ID 접속 성공!"
  else
    log "⚠️  j $HUB_ID 접속 실패 (터널이 아직 연결 중일 수 있습니다)"
    log "  수동 검증: ssh relay → j $HUB_ID"
  fi
fi

echo ""
echo "=============================================="
echo "  프로비저닝 완료"
echo "=============================================="
echo "  장비 IP:       $DEVICE_IP"
echo "  Hub ID:        ${HUB_ID:-unknown}"
echo "  Remote Port:   $SUPPORT_REMOTE_PORT"
echo "  Relay:         $RELAY_HOST:$RELAY_PORT"
echo ""
echo "  접속 명령:"
echo "    ssh -i $RELAY_KEY_PATH -p $RELAY_PORT ${RELAY_USER}@${RELAY_HOST}"
echo "    j ${HUB_ID:-<hub_id>}"
echo "=============================================="
