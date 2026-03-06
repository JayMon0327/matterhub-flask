#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="${RUN_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/venv}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
DRY_RUN=0
SKIP_OS_PACKAGES=0
SETUP_SUPPORT_TUNNEL=0
ENABLE_SUPPORT_TUNNEL_NOW=0
HARDEN_REVERSE_TUNNEL_ONLY=0
HARDEN_LOCAL_CONSOLE_PAM=0
SUPPORT_HOST="${SUPPORT_HOST:-${SUPPORT_TUNNEL_HOST:-}}"
SUPPORT_USER="${SUPPORT_USER:-${SUPPORT_TUNNEL_USER:-}}"
SUPPORT_PORT="${SUPPORT_PORT:-${SUPPORT_TUNNEL_PORT:-}}"
SUPPORT_REMOTE_PORT="${SUPPORT_REMOTE_PORT:-${SUPPORT_TUNNEL_REMOTE_PORT:-}}"
SUPPORT_DEVICE_USER="${SUPPORT_DEVICE_USER:-${SUPPORT_TUNNEL_DEVICE_USER:-$RUN_USER}}"
SUPPORT_RELAY_OPERATOR_USER="${SUPPORT_RELAY_OPERATOR_USER:-${SUPPORT_TUNNEL_RELAY_OPERATOR_USER:-ec2-user}}"
SUPPORT_RELAY_ACCESS_PUBKEY="${SUPPORT_RELAY_ACCESS_PUBKEY:-${SUPPORT_TUNNEL_RELAY_ACCESS_PUBKEY:-}}"
HARDEN_ALLOW_INBOUND_PORTS=()
POLKIT_RULE_PATH="${POLKIT_RULE_PATH:-/etc/polkit-1/rules.d/49-matterhub-networkmanager.rules}"

log() {
  printf '[matterhub-install] %s\n' "$*"
}

print_command() {
  local prefix="$1"
  shift
  printf '%s' "$prefix"
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
  printf '\n'
}

run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command "[dry-run]" "$@"
    return 0
  fi
  "$@"
}

sudo_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command "[dry-run] sudo" "$@"
    return 0
  fi
  sudo "$@"
}

usage() {
  cat <<'EOF'
Usage: ./device_config/install_ubuntu24.sh [--dry-run] [--skip-os-packages]

Options:
  --dry-run           Print the actions without executing sudo/systemctl/pip commands.
  --skip-os-packages  Skip apt update/install steps.
  --setup-support-tunnel
                      Run reverse tunnel setup script after base install.
  --enable-support-tunnel-now
                      Enable/start matterhub-support-tunnel.service when tunnel setup runs.
  --support-host      Support server host passed to setup_support_tunnel.sh.
  --support-user      Support server user passed to setup_support_tunnel.sh.
  --support-port      Support server SSH port passed to setup_support_tunnel.sh.
  --support-remote-port
                      Reverse SSH remote port passed to setup_support_tunnel.sh.
  --support-device-user
                      Device SSH user for operator connect command output.
  --support-relay-operator-user
                      Relay login SSH user for operator command output.
  --support-relay-access-pubkey
                      Relay hub-access public key to append on device authorized_keys.
  --harden-reverse-tunnel-only
                      Apply reverse-tunnel-only access hardening (no direct inbound SSH).
  --harden-allow-inbound-port
                      Keep inbound TCP port open under UFW policy (repeatable).
  --harden-local-console-pam
                      Apply PAM policy to block local-console login for runtime account.

Environment variables:
  RUN_USER     systemd service user (default: current shell user)
  PYTHON_BIN   python executable used to create the venv (default: python3)
  VENV_DIR     virtualenv path (default: <project>/venv)
  SYSTEMD_DIR  target systemd unit directory (default: /etc/systemd/system)
  SUPPORT_HOST / SUPPORT_USER / SUPPORT_PORT / SUPPORT_REMOTE_PORT / SUPPORT_DEVICE_USER
  SUPPORT_RELAY_OPERATOR_USER / SUPPORT_RELAY_ACCESS_PUBKEY
               reverse tunnel setup defaults (also accepts SUPPORT_TUNNEL_* names).
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --skip-os-packages)
      SKIP_OS_PACKAGES=1
      ;;
    --setup-support-tunnel)
      SETUP_SUPPORT_TUNNEL=1
      ;;
    --enable-support-tunnel-now)
      ENABLE_SUPPORT_TUNNEL_NOW=1
      ;;
    --support-host)
      SUPPORT_HOST="$2"
      shift
      ;;
    --support-user)
      SUPPORT_USER="$2"
      shift
      ;;
    --support-port)
      SUPPORT_PORT="$2"
      shift
      ;;
    --support-remote-port)
      SUPPORT_REMOTE_PORT="$2"
      shift
      ;;
    --support-device-user)
      SUPPORT_DEVICE_USER="$2"
      shift
      ;;
    --support-relay-operator-user)
      SUPPORT_RELAY_OPERATOR_USER="$2"
      shift
      ;;
    --support-relay-access-pubkey)
      SUPPORT_RELAY_ACCESS_PUBKEY="$2"
      shift
      ;;
    --harden-reverse-tunnel-only)
      HARDEN_REVERSE_TUNNEL_ONLY=1
      ;;
    --harden-allow-inbound-port)
      HARDEN_ALLOW_INBOUND_PORTS+=("$2")
      shift
      ;;
    --harden-local-console-pam)
      HARDEN_LOCAL_CONSOLE_PAM=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

if [ "$DRY_RUN" -ne 1 ] && [ "$(uname -s)" != "Linux" ]; then
  echo "This installer must be executed on Ubuntu/Linux. Use --dry-run for planning on macOS." >&2
  exit 1
fi

SERVICE_UNITS=()
while IFS= read -r unit_name; do
  if [ -n "$unit_name" ]; then
    SERVICE_UNITS+=("$unit_name")
  fi
done <<EOF
$("$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" --list-unit-names)
EOF

if [ "${#SERVICE_UNITS[@]}" -eq 0 ]; then
  echo "No systemd service units were discovered." >&2
  exit 1
fi

ENABLED_SERVICE_UNITS=()
while IFS= read -r unit_name; do
  if [ -n "$unit_name" ]; then
    ENABLED_SERVICE_UNITS+=("$unit_name")
  fi
done <<EOF
$("$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" --list-enabled-unit-names)
EOF

log "프로젝트 루트: $PROJECT_ROOT"
log "서비스 실행 사용자: $RUN_USER"
log "설치 대상 systemd 디렉토리: $SYSTEMD_DIR"
log "대상 서비스: ${SERVICE_UNITS[*]}"
if [ "${#ENABLED_SERVICE_UNITS[@]}" -gt 0 ]; then
  log "자동 enable/restart 대상 서비스: ${ENABLED_SERVICE_UNITS[*]}"
else
  log "자동 enable/restart 대상 서비스 없음"
fi

if [ "$SETUP_SUPPORT_TUNNEL" -eq 1 ]; then
  if [ "$ENABLE_SUPPORT_TUNNEL_NOW" -eq 1 ]; then
    log "reverse tunnel 설정: 실행 후 서비스 즉시 시작"
  else
    log "reverse tunnel 설정: 유닛 설치/환경 구성만 수행"
  fi
  if [ -n "$SUPPORT_HOST" ]; then
    log "reverse tunnel host: $SUPPORT_HOST"
  fi
  if [ -n "$SUPPORT_USER" ]; then
    log "reverse tunnel user: $SUPPORT_USER"
  fi
  if [ -n "$SUPPORT_REMOTE_PORT" ]; then
    log "reverse tunnel remote port: $SUPPORT_REMOTE_PORT"
  fi
  if [ -n "$SUPPORT_RELAY_OPERATOR_USER" ]; then
    log "reverse tunnel relay operator user: $SUPPORT_RELAY_OPERATOR_USER"
  fi
fi

if [ "$HARDEN_REVERSE_TUNNEL_ONLY" -eq 1 ]; then
  log "reverse tunnel only 하드닝: 적용 예정"
  if [ "${#HARDEN_ALLOW_INBOUND_PORTS[@]}" -gt 0 ]; then
    log "하드닝 inbound 예외 포트: ${HARDEN_ALLOW_INBOUND_PORTS[*]}"
  else
    log "하드닝 inbound 예외 포트 없음 (모든 inbound 차단)"
  fi
fi

if [ "$HARDEN_LOCAL_CONSOLE_PAM" -eq 1 ]; then
  log "로컬 콘솔 로그인 제한(PAM): 적용 예정"
fi

if [ "$SKIP_OS_PACKAGES" -eq 0 ]; then
  log "Ubuntu 필수 패키지 설치"
  sudo_cmd apt update
  sudo_cmd apt install -y python3-venv python3-pip network-manager autossh openssh-server
else
  log "OS 패키지 설치 단계 생략"
fi

log "SSH 서버 서비스 활성화"
sudo_cmd systemctl enable --now ssh

if [ ! -d "$VENV_DIR" ]; then
  log "가상환경 생성: $VENV_DIR"
  run_cmd "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  log "가상환경 재사용: $VENV_DIR"
fi

log "Python 패키지 설치/업데이트"
run_cmd "$VENV_DIR/bin/pip" install --upgrade pip
run_cmd "$VENV_DIR/bin/pip" install -r "$PROJECT_ROOT/requirements.txt"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

RUN_USER_ESCAPED="${RUN_USER//\'/}"
POLKIT_RULE_FILE="$TMP_DIR/49-matterhub-networkmanager.rules"
cat > "$POLKIT_RULE_FILE" <<EOF
polkit.addRule(function(action, subject) {
    if (subject.user == "${RUN_USER_ESCAPED}" &&
        action.id.indexOf("org.freedesktop.NetworkManager.") == 0) {
        return polkit.Result.YES;
    }
});
EOF

log "NetworkManager 제어 권한(polkit) 설치: $POLKIT_RULE_PATH"
sudo_cmd install -m 0644 "$POLKIT_RULE_FILE" "$POLKIT_RULE_PATH"

log "systemd 유닛 렌더링"
if [ "$DRY_RUN" -eq 1 ]; then
  print_command "[dry-run]" "$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" \
    --project-root "$PROJECT_ROOT" \
    --run-user "$RUN_USER" \
    --output-dir "$TMP_DIR"
else
  "$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" \
    --project-root "$PROJECT_ROOT" \
    --run-user "$RUN_USER" \
    --output-dir "$TMP_DIR"
fi

for unit_name in "${SERVICE_UNITS[@]}"; do
  sudo_cmd install -m 0644 "$TMP_DIR/$unit_name" "$SYSTEMD_DIR/$unit_name"
done

log "systemd reload/enable/restart"
sudo_cmd systemctl daemon-reload
if [ "${#ENABLED_SERVICE_UNITS[@]}" -gt 0 ]; then
  sudo_cmd systemctl enable "${ENABLED_SERVICE_UNITS[@]}"
  sudo_cmd systemctl restart "${ENABLED_SERVICE_UNITS[@]}"
fi

if [ "$DRY_RUN" -eq 0 ]; then
  if [ "${#ENABLED_SERVICE_UNITS[@]}" -gt 0 ]; then
    sudo systemctl --no-pager --full status "${ENABLED_SERVICE_UNITS[@]}" || true
  fi
fi

if [ "$SETUP_SUPPORT_TUNNEL" -eq 1 ]; then
  SETUP_SCRIPT="$SCRIPT_DIR/setup_support_tunnel.sh"
  if [ ! -f "$SETUP_SCRIPT" ]; then
    echo "setup_support_tunnel.sh not found: $SETUP_SCRIPT" >&2
    exit 1
  fi

  setup_cmd=(
    bash "$SETUP_SCRIPT"
    --run-user "$RUN_USER"
    --env-file "$PROJECT_ROOT/.env"
  )
  if [ -n "$SUPPORT_HOST" ]; then
    setup_cmd+=(--host "$SUPPORT_HOST")
  fi
  if [ -n "$SUPPORT_USER" ]; then
    setup_cmd+=(--user "$SUPPORT_USER")
  fi
  if [ -n "$SUPPORT_PORT" ]; then
    setup_cmd+=(--port "$SUPPORT_PORT")
  fi
  if [ -n "$SUPPORT_REMOTE_PORT" ]; then
    setup_cmd+=(--remote-port "$SUPPORT_REMOTE_PORT")
  fi
  if [ -n "$SUPPORT_DEVICE_USER" ]; then
    setup_cmd+=(--device-user "$SUPPORT_DEVICE_USER")
  fi
  if [ -n "$SUPPORT_RELAY_OPERATOR_USER" ]; then
    setup_cmd+=(--relay-operator-user "$SUPPORT_RELAY_OPERATOR_USER")
  fi
  if [ -n "$SUPPORT_RELAY_ACCESS_PUBKEY" ]; then
    setup_cmd+=(--relay-access-pubkey "$SUPPORT_RELAY_ACCESS_PUBKEY")
  fi
  if [ "$ENABLE_SUPPORT_TUNNEL_NOW" -eq 1 ]; then
    setup_cmd+=(--enable-now)
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    setup_cmd+=(--dry-run)
  fi

  log "reverse tunnel 초기 설정 실행"
  run_cmd "${setup_cmd[@]}"
fi

if [ "$HARDEN_REVERSE_TUNNEL_ONLY" -eq 1 ]; then
  HARDEN_SCRIPT="$SCRIPT_DIR/harden_reverse_tunnel_only.sh"
  if [ ! -f "$HARDEN_SCRIPT" ]; then
    echo "harden_reverse_tunnel_only.sh not found: $HARDEN_SCRIPT" >&2
    exit 1
  fi

  harden_cmd=(
    bash "$HARDEN_SCRIPT"
    --run-user "$RUN_USER"
    --env-file "$PROJECT_ROOT/.env"
  )
  for port in "${HARDEN_ALLOW_INBOUND_PORTS[@]-}"; do
    if [ -z "$port" ]; then
      continue
    fi
    harden_cmd+=(--allow-inbound-port "$port")
  done
  if [ "$DRY_RUN" -eq 1 ]; then
    harden_cmd+=(--dry-run)
  fi

  log "reverse tunnel only 하드닝 실행"
  run_cmd "${harden_cmd[@]}"
fi

if [ "$HARDEN_LOCAL_CONSOLE_PAM" -eq 1 ]; then
  PAM_HARDEN_SCRIPT="$SCRIPT_DIR/harden_local_console_pam.sh"
  if [ ! -f "$PAM_HARDEN_SCRIPT" ]; then
    echo "harden_local_console_pam.sh not found: $PAM_HARDEN_SCRIPT" >&2
    exit 1
  fi

  pam_harden_cmd=(
    bash "$PAM_HARDEN_SCRIPT"
    --run-user "$RUN_USER"
  )
  if [ "$DRY_RUN" -eq 1 ]; then
    pam_harden_cmd+=(--dry-run)
  fi

  log "로컬 콘솔 로그인 제한(PAM) 실행"
  run_cmd "${pam_harden_cmd[@]}"
fi

log "설치 완료"
