#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="${RUN_USER:-$(id -un)}"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
DEFAULT_PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
if [ -x "$DEFAULT_PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

SUPPORT_TUNNEL_HOST="${SUPPORT_TUNNEL_HOST:-support.whatsmatter.local}"
SUPPORT_TUNNEL_USER="${SUPPORT_TUNNEL_USER:-whatsmatter}"
SUPPORT_TUNNEL_PORT="${SUPPORT_TUNNEL_PORT:-443}"
SUPPORT_TUNNEL_REMOTE_PORT="${SUPPORT_TUNNEL_REMOTE_PORT:-}"
SUPPORT_TUNNEL_LOCAL_PORT="${SUPPORT_TUNNEL_LOCAL_PORT:-22}"
SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS="${SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS:-127.0.0.1}"
SUPPORT_TUNNEL_COMMAND="${SUPPORT_TUNNEL_COMMAND:-autossh}"
SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING="${SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING:-1}"
SUPPORT_TUNNEL_DEVICE_USER="${SUPPORT_TUNNEL_DEVICE_USER:-$RUN_USER}"
SUPPORT_TUNNEL_RELAY_OPERATOR_USER="${SUPPORT_TUNNEL_RELAY_OPERATOR_USER:-ec2-user}"
SUPPORT_TUNNEL_PRIVATE_KEY_PATH="${SUPPORT_TUNNEL_PRIVATE_KEY_PATH:-/home/$RUN_USER/.ssh/matterhub_support_tunnel_ed25519}"
SUPPORT_TUNNEL_KNOWN_HOSTS_PATH="${SUPPORT_TUNNEL_KNOWN_HOSTS_PATH:-/home/$RUN_USER/.ssh/known_hosts}"
SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL="${SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL:-30}"
SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX="${SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX:-3}"
SUPPORT_TUNNEL_AUTOSSH_GATETIME="${SUPPORT_TUNNEL_AUTOSSH_GATETIME:-0}"
SUPPORT_TUNNEL_RELAY_ACCESS_PUBKEY="${SUPPORT_TUNNEL_RELAY_ACCESS_PUBKEY:-}"

ENABLE_NOW=0
DRY_RUN=0

log() {
  printf '[support-tunnel-setup] %s\n' "$*"
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

strip_quotes() {
  local value="$1"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  printf '%s' "$value"
}

get_env_value() {
  local key="$1"
  local file="$2"
  if [ ! -f "$file" ]; then
    return 0
  fi
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
  if [ -z "$line" ]; then
    return 0
  fi
  local value="${line#*=}"
  strip_quotes "$value"
}

set_env_value() {
  local key="$1"
  local value="$2"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "env update: ${key}=${value}"
    return 0
  fi

  local tmp_file
  tmp_file="$(mktemp)"
  if [ -f "$ENV_FILE" ]; then
    awk -v k="$key" -v v="$value" -F= '
      BEGIN { updated=0 }
      {
        if ($1 == k) {
          print k "=" v
          updated=1
        } else {
          print $0
        }
      }
      END {
        if (!updated) {
          print k "=" v
        }
      }
    ' "$ENV_FILE" > "$tmp_file"
  else
    printf '%s=%s\n' "$key" "$value" > "$tmp_file"
  fi
  mv "$tmp_file" "$ENV_FILE"
}

usage() {
  cat <<'EOF'
Usage: ./device_config/setup_support_tunnel.sh [options]

Options:
  --host <hostname>             Support server host (default: support.whatsmatter.local)
  --user <username>             Support server user (default: whatsmatter)
  --remote-port <port>          Remote forwarded port on support server
  --port <port>                 Support server SSH port (default: 443)
  --local-port <port>           Device local SSH port (default: 22)
  --bind-address <address>      Remote bind address (default: 127.0.0.1)
  --key-path <path>             Private key path on device
  --known-hosts-path <path>     known_hosts path on device
  --command <ssh|autossh>       Tunnel command (default: autossh)
  --device-user <username>      SSH user to access device via tunnel (default: current user)
  --relay-operator-user <name>  SSH user for relay login (default: ec2-user)
  --relay-access-pubkey <key>   Relay hub-access public key to append on this device for passwordless j
  --env-file <path>             Target .env path (default: <project>/.env)
  --run-user <username>         systemd run user (default: current shell user)
  --enable-now                  Enable and start matterhub-support-tunnel.service
  --dry-run                     Print planned commands and env updates only
  -h, --help                    Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      SUPPORT_TUNNEL_HOST="$2"
      shift 2
      ;;
    --user)
      SUPPORT_TUNNEL_USER="$2"
      shift 2
      ;;
    --remote-port)
      SUPPORT_TUNNEL_REMOTE_PORT="$2"
      shift 2
      ;;
    --port)
      SUPPORT_TUNNEL_PORT="$2"
      shift 2
      ;;
    --local-port)
      SUPPORT_TUNNEL_LOCAL_PORT="$2"
      shift 2
      ;;
    --bind-address)
      SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS="$2"
      shift 2
      ;;
    --key-path)
      SUPPORT_TUNNEL_PRIVATE_KEY_PATH="$2"
      shift 2
      ;;
    --known-hosts-path)
      SUPPORT_TUNNEL_KNOWN_HOSTS_PATH="$2"
      shift 2
      ;;
    --command)
      SUPPORT_TUNNEL_COMMAND="$2"
      shift 2
      ;;
    --device-user)
      SUPPORT_TUNNEL_DEVICE_USER="$2"
      shift 2
      ;;
    --relay-operator-user)
      SUPPORT_TUNNEL_RELAY_OPERATOR_USER="$2"
      shift 2
      ;;
    --relay-access-pubkey)
      SUPPORT_TUNNEL_RELAY_ACCESS_PUBKEY="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --enable-now)
      ENABLE_NOW=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
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

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

if [ -z "$SUPPORT_TUNNEL_REMOTE_PORT" ]; then
  MATTERHUB_ID="$(get_env_value "matterhub_id" "$ENV_FILE")"
  if [ -n "$MATTERHUB_ID" ]; then
    CHECKSUM="$(printf '%s' "$MATTERHUB_ID" | cksum | awk '{print $1}')"
    SUPPORT_TUNNEL_REMOTE_PORT="$((22000 + CHECKSUM % 1000))"
    log "remote port not specified; derived from matterhub_id=${MATTERHUB_ID} -> ${SUPPORT_TUNNEL_REMOTE_PORT}"
  else
    SUPPORT_TUNNEL_REMOTE_PORT="2222"
    log "remote port not specified; matterhub_id missing -> fallback ${SUPPORT_TUNNEL_REMOTE_PORT}"
  fi
fi

MATTERHUB_ID="${MATTERHUB_ID:-$(get_env_value "matterhub_id" "$ENV_FILE")}"

KEY_DIR="$(dirname "$SUPPORT_TUNNEL_PRIVATE_KEY_PATH")"
PUB_KEY_PATH="${SUPPORT_TUNNEL_PRIVATE_KEY_PATH}.pub"

log "project_root=$PROJECT_ROOT"
log "env_file=$ENV_FILE"
log "support_host=$SUPPORT_TUNNEL_HOST support_user=$SUPPORT_TUNNEL_USER remote_port=$SUPPORT_TUNNEL_REMOTE_PORT"
log "key_path=$SUPPORT_TUNNEL_PRIVATE_KEY_PATH"

run_cmd mkdir -p "$KEY_DIR"
if [ ! -f "$SUPPORT_TUNNEL_PRIVATE_KEY_PATH" ]; then
  run_cmd ssh-keygen -t ed25519 -N "" -C "matterhub-support-tunnel@$(hostname)" -f "$SUPPORT_TUNNEL_PRIVATE_KEY_PATH"
else
  log "existing key reused: $SUPPORT_TUNNEL_PRIVATE_KEY_PATH"
fi

if [ "$DRY_RUN" -eq 0 ]; then
  chmod 700 "$KEY_DIR"
  chmod 600 "$SUPPORT_TUNNEL_PRIVATE_KEY_PATH"
  if [ -f "$PUB_KEY_PATH" ]; then
    chmod 644 "$PUB_KEY_PATH"
  fi
fi

set_env_value "SUPPORT_TUNNEL_ENABLED" "1"
set_env_value "SUPPORT_TUNNEL_COMMAND" "$SUPPORT_TUNNEL_COMMAND"
set_env_value "SUPPORT_TUNNEL_USER" "$SUPPORT_TUNNEL_USER"
set_env_value "SUPPORT_TUNNEL_HOST" "$SUPPORT_TUNNEL_HOST"
set_env_value "SUPPORT_TUNNEL_PORT" "$SUPPORT_TUNNEL_PORT"
set_env_value "SUPPORT_TUNNEL_REMOTE_PORT" "$SUPPORT_TUNNEL_REMOTE_PORT"
set_env_value "SUPPORT_TUNNEL_LOCAL_PORT" "$SUPPORT_TUNNEL_LOCAL_PORT"
set_env_value "SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS" "$SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS"
set_env_value "SUPPORT_TUNNEL_PRIVATE_KEY_PATH" "$SUPPORT_TUNNEL_PRIVATE_KEY_PATH"
set_env_value "SUPPORT_TUNNEL_KNOWN_HOSTS_PATH" "$SUPPORT_TUNNEL_KNOWN_HOSTS_PATH"
set_env_value "SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING" "$SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING"
set_env_value "SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL" "$SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL"
set_env_value "SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX" "$SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX"
set_env_value "SUPPORT_TUNNEL_AUTOSSH_GATETIME" "$SUPPORT_TUNNEL_AUTOSSH_GATETIME"
set_env_value "SUPPORT_TUNNEL_DEVICE_USER" "$SUPPORT_TUNNEL_DEVICE_USER"
set_env_value "SUPPORT_TUNNEL_RELAY_OPERATOR_USER" "$SUPPORT_TUNNEL_RELAY_OPERATOR_USER"

if [ -n "$SUPPORT_TUNNEL_RELAY_ACCESS_PUBKEY" ]; then
  AUTH_DIR="/home/$RUN_USER/.ssh"
  AUTH_FILE="$AUTH_DIR/authorized_keys"
  run_cmd mkdir -p "$AUTH_DIR"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "relay access pubkey will be appended to ${AUTH_FILE} if missing"
  else
    touch "$AUTH_FILE"
    chmod 700 "$AUTH_DIR"
    chmod 600 "$AUTH_FILE"
    if ! grep -Fqx "$SUPPORT_TUNNEL_RELAY_ACCESS_PUBKEY" "$AUTH_FILE"; then
      printf '%s\n' "$SUPPORT_TUNNEL_RELAY_ACCESS_PUBKEY" >> "$AUTH_FILE"
      log "relay access pubkey appended to ${AUTH_FILE}"
    else
      log "relay access pubkey already present in ${AUTH_FILE}"
    fi
  fi
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

run_cmd "$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" \
  --project-root "$PROJECT_ROOT" \
  --run-user "$RUN_USER" \
  --output-dir "$TMP_DIR"
sudo_cmd install -m 0644 "$TMP_DIR/matterhub-support-tunnel.service" "$SYSTEMD_DIR/matterhub-support-tunnel.service"
sudo_cmd systemctl daemon-reload

if [ "$ENABLE_NOW" -eq 1 ]; then
  sudo_cmd systemctl enable --now matterhub-support-tunnel.service
  sudo_cmd systemctl --no-pager --full status matterhub-support-tunnel.service
else
  log "service installed only (not enabled). use: sudo systemctl enable --now matterhub-support-tunnel.service"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  PUB_KEY_PLACEHOLDER="<dry-run:public-key-unavailable>"
else
  PUB_KEY_PLACEHOLDER="$(cat "$PUB_KEY_PATH")"
fi

echo
echo "=== Support server authorized_keys entry (recommended) ==="
echo "restrict,port-forwarding,permitlisten=\"127.0.0.1:${SUPPORT_TUNNEL_REMOTE_PORT}\" ${PUB_KEY_PLACEHOLDER}"
echo
echo "=== Operator connect (recommended: two-step) ==="
echo "1) ssh -i <relay-operator-key.pem> -p ${SUPPORT_TUNNEL_PORT} ${SUPPORT_TUNNEL_RELAY_OPERATOR_USER}@${SUPPORT_TUNNEL_HOST}"
if [ -n "$MATTERHUB_ID" ]; then
  echo "2) j ${MATTERHUB_ID}"
else
  echo "2) j <hub_id>"
fi
echo
echo "=== Operator connect (one-liner) ==="
echo "ssh -o ProxyCommand='ssh -i <relay-operator-key.pem> -p ${SUPPORT_TUNNEL_PORT} ${SUPPORT_TUNNEL_RELAY_OPERATOR_USER}@${SUPPORT_TUNNEL_HOST} -W %h:%p' -p ${SUPPORT_TUNNEL_REMOTE_PORT} ${SUPPORT_TUNNEL_DEVICE_USER}@127.0.0.1"
