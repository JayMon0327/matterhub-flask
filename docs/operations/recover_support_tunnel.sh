#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
SYSTEMD_UNIT="/etc/systemd/system/matterhub-support-tunnel.service"
SYSTEMD_DROPIN_DIR="/etc/systemd/system/matterhub-support-tunnel.service.d"
SYSTEMD_DROPIN_FILE="$SYSTEMD_DROPIN_DIR/override.conf"

RUN_USER="${RUN_USER:-whatsmatter}"
SUPPORT_TUNNEL_HOST="${SUPPORT_TUNNEL_HOST:-3.38.126.167}"
SUPPORT_TUNNEL_USER="${SUPPORT_TUNNEL_USER:-whatsmatter}"
SUPPORT_TUNNEL_PORT="${SUPPORT_TUNNEL_PORT:-443}"
SUPPORT_TUNNEL_REMOTE_PORT="${SUPPORT_TUNNEL_REMOTE_PORT:-}"
SUPPORT_TUNNEL_LOCAL_PORT="${SUPPORT_TUNNEL_LOCAL_PORT:-22}"
SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS="${SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS:-127.0.0.1}"
SUPPORT_TUNNEL_COMMAND="${SUPPORT_TUNNEL_COMMAND:-ssh}"
SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING="${SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING:-0}"
SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL="${SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL:-30}"
SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX="${SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX:-3}"
SUPPORT_TUNNEL_RECONNECT_DELAY_SECONDS="${SUPPORT_TUNNEL_RECONNECT_DELAY_SECONDS:-5}"
SUPPORT_TUNNEL_MAX_RECONNECT_DELAY_SECONDS="${SUPPORT_TUNNEL_MAX_RECONNECT_DELAY_SECONDS:-60}"

usage() {
  cat <<'EOF'
Usage: bash docs/operations/recover_support_tunnel.sh [options]

Options:
  --run-user <user>           Device Linux user (default: whatsmatter)
  --host <host>               Relay host (default: 3.38.126.167)
  --user <user>               Relay SSH user (default: whatsmatter)
  --port <port>               Relay SSH port (default: 443)
  --remote-port <port>        Reverse SSH remote port (default: derived from matterhub_id, fallback 22608)
  --local-port <port>         Device local SSH port (default: 22)
  --command <ssh|autossh>     Tunnel command (default: ssh)
  -h, --help                  Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --host)
      SUPPORT_TUNNEL_HOST="$2"
      shift 2
      ;;
    --user)
      SUPPORT_TUNNEL_USER="$2"
      shift 2
      ;;
    --port)
      SUPPORT_TUNNEL_PORT="$2"
      shift 2
      ;;
    --remote-port)
      SUPPORT_TUNNEL_REMOTE_PORT="$2"
      shift 2
      ;;
    --local-port)
      SUPPORT_TUNNEL_LOCAL_PORT="$2"
      shift 2
      ;;
    --command)
      SUPPORT_TUNNEL_COMMAND="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

log() {
  printf '[recover-support-tunnel] %s\n' "$*"
}

set_env_value() {
  local key="$1"
  local value="$2"
  if [ ! -f "$ENV_FILE" ]; then
    touch "$ENV_FILE"
  fi
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

get_env_value() {
  local key="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return 0
  fi
  grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | sed "s/^${key}=//"
}

if [ -z "$SUPPORT_TUNNEL_REMOTE_PORT" ]; then
  MATTERHUB_ID="$(get_env_value "matterhub_id" | tr -d '"' | tr -d "'")"
  if [ -n "$MATTERHUB_ID" ]; then
    CHECKSUM="$(printf '%s' "$MATTERHUB_ID" | cksum | awk '{print $1}')"
    SUPPORT_TUNNEL_REMOTE_PORT="$((22000 + CHECKSUM % 1000))"
    log "derived remote port from matterhub_id=${MATTERHUB_ID}: ${SUPPORT_TUNNEL_REMOTE_PORT}"
  else
    SUPPORT_TUNNEL_REMOTE_PORT="22608"
    log "matterhub_id not found, fallback remote port=${SUPPORT_TUNNEL_REMOTE_PORT}"
  fi
fi

KEY_PATH="/home/${RUN_USER}/.ssh/matterhub_support_tunnel_ed25519"
KNOWN_HOSTS_PATH="/home/${RUN_USER}/.ssh/known_hosts"

if [ -x "$PROJECT_ROOT/venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ssh command not found" >&2
  exit 1
fi

log "project_root=$PROJECT_ROOT"
log "env_file=$ENV_FILE"
log "run_user=$RUN_USER"

sudo systemctl stop matterhub-support-tunnel.service >/dev/null 2>&1 || true
sudo systemctl reset-failed matterhub-support-tunnel.service >/dev/null 2>&1 || true

sudo -u "$RUN_USER" mkdir -p "/home/${RUN_USER}/.ssh"
if [ ! -f "$KEY_PATH" ]; then
  log "generating tunnel key: $KEY_PATH"
  sudo -u "$RUN_USER" ssh-keygen -t ed25519 -N "" -C "matterhub-support-tunnel@$(hostname)" -f "$KEY_PATH"
fi
sudo chown -R "$RUN_USER:$RUN_USER" "/home/${RUN_USER}/.ssh"
sudo chmod 700 "/home/${RUN_USER}/.ssh"
sudo chmod 600 "$KEY_PATH"
[ -f "${KEY_PATH}.pub" ] && sudo chmod 644 "${KEY_PATH}.pub" || true

set_env_value "SUPPORT_TUNNEL_ENABLED" "1"
set_env_value "SUPPORT_TUNNEL_COMMAND" "$SUPPORT_TUNNEL_COMMAND"
set_env_value "SUPPORT_TUNNEL_USER" "$SUPPORT_TUNNEL_USER"
set_env_value "SUPPORT_TUNNEL_HOST" "$SUPPORT_TUNNEL_HOST"
set_env_value "SUPPORT_TUNNEL_PORT" "$SUPPORT_TUNNEL_PORT"
set_env_value "SUPPORT_TUNNEL_REMOTE_PORT" "$SUPPORT_TUNNEL_REMOTE_PORT"
set_env_value "SUPPORT_TUNNEL_LOCAL_PORT" "$SUPPORT_TUNNEL_LOCAL_PORT"
set_env_value "SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS" "$SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS"
set_env_value "SUPPORT_TUNNEL_PRIVATE_KEY_PATH" "$KEY_PATH"
set_env_value "SUPPORT_TUNNEL_KNOWN_HOSTS_PATH" "$KNOWN_HOSTS_PATH"
set_env_value "SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING" "$SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING"
set_env_value "SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL" "$SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL"
set_env_value "SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX" "$SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX"
set_env_value "SUPPORT_TUNNEL_RECONNECT_DELAY_SECONDS" "$SUPPORT_TUNNEL_RECONNECT_DELAY_SECONDS"
set_env_value "SUPPORT_TUNNEL_MAX_RECONNECT_DELAY_SECONDS" "$SUPPORT_TUNNEL_MAX_RECONNECT_DELAY_SECONDS"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

"$PYTHON_BIN" "$PROJECT_ROOT/device_config/render_systemd_units.py" \
  --project-root "$PROJECT_ROOT" \
  --run-user "$RUN_USER" \
  --output-dir "$TMP_DIR"

sudo install -m 0644 "$TMP_DIR/matterhub-support-tunnel.service" "$SYSTEMD_UNIT"
sudo mkdir -p "$SYSTEMD_DROPIN_DIR"
cat > "$TMP_DIR/override.conf" <<'EOF'
[Unit]
StartLimitIntervalSec=0
StartLimitBurst=0
EOF
sudo install -m 0644 "$TMP_DIR/override.conf" "$SYSTEMD_DROPIN_FILE"

sudo systemctl daemon-reload
sudo systemctl enable --now matterhub-support-tunnel.service

log "service status:"
sudo systemctl --no-pager --full status matterhub-support-tunnel.service || true

log "journal tail:"
sudo journalctl -u matterhub-support-tunnel.service -n 50 --no-pager || true

log "quick auth check:"
if sudo -u "$RUN_USER" ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no \
  -i "$KEY_PATH" -p "$SUPPORT_TUNNEL_PORT" "${SUPPORT_TUNNEL_USER}@${SUPPORT_TUNNEL_HOST}" 'echo relay_auth_ok' >/dev/null 2>&1; then
  log "relay ssh auth OK"
else
  log "relay ssh auth failed. Add this public key on relay authorized_keys:"
  [ -f "${KEY_PATH}.pub" ] && cat "${KEY_PATH}.pub" || true
fi

log "done"
log "operator test: ssh -i <relay-operator-key.pem> -p 443 ec2-user@3.38.126.167 '/usr/local/bin/j 1770784749 \"echo tunnel_ok\"'"
