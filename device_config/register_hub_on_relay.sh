#!/usr/bin/env bash

set -euo pipefail

RELAY_HOST="${RELAY_HOST:-}"
RELAY_PORT="${RELAY_PORT:-443}"
RELAY_USER="${RELAY_USER:-ec2-user}"
RELAY_KEY_PATH="${RELAY_KEY_PATH:-$HOME/.ssh/matterhub-relay-operator-key.pem}"
HUB_ID="${HUB_ID:-}"
REMOTE_PORT="${REMOTE_PORT:-}"
DEVICE_USER="${DEVICE_USER:-whatsmatter}"
HUB_PUBLIC_KEY_PATH="${HUB_PUBLIC_KEY_PATH:-}"
DRY_RUN=0

log() {
  printf '[relay-register] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage: ./device_config/register_hub_on_relay.sh [options]

Required:
  --relay-host <ip-or-domain>       Relay host
  --hub-id <hub_id>                 Hub identifier
  --remote-port <port>              Hub's reverse tunnel remote port
  --hub-pubkey <path>               Hub public key path (.pub)

Optional:
  --relay-user <username>           Relay operator SSH user (default: ec2-user)
  --relay-port <port>               Relay SSH port (default: 443)
  --relay-key <path>                Relay operator key pem path
  --device-user <username>          Device SSH user through tunnel (default: whatsmatter)
  --dry-run                         Show commands only
  -h, --help                        Show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --relay-host)
      RELAY_HOST="$2"
      shift 2
      ;;
    --relay-user)
      RELAY_USER="$2"
      shift 2
      ;;
    --relay-port)
      RELAY_PORT="$2"
      shift 2
      ;;
    --relay-key)
      RELAY_KEY_PATH="$2"
      shift 2
      ;;
    --hub-id)
      HUB_ID="$2"
      shift 2
      ;;
    --remote-port)
      REMOTE_PORT="$2"
      shift 2
      ;;
    --hub-pubkey)
      HUB_PUBLIC_KEY_PATH="$2"
      shift 2
      ;;
    --device-user)
      DEVICE_USER="$2"
      shift 2
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

if [ -z "$RELAY_HOST" ] || [ -z "$HUB_ID" ] || [ -z "$REMOTE_PORT" ] || [ -z "$HUB_PUBLIC_KEY_PATH" ]; then
  usage >&2
  exit 1
fi

if [ ! -f "$HUB_PUBLIC_KEY_PATH" ]; then
  echo "Hub public key file not found: $HUB_PUBLIC_KEY_PATH" >&2
  exit 1
fi

if [ ! -f "$RELAY_KEY_PATH" ]; then
  echo "Relay operator key not found: $RELAY_KEY_PATH" >&2
  exit 1
fi

HUB_PUBLIC_KEY="$(cat "$HUB_PUBLIC_KEY_PATH")"
AUTHORIZED_LINE="restrict,port-forwarding,permitlisten=\"127.0.0.1:${REMOTE_PORT}\" ${HUB_PUBLIC_KEY}"

SSH_BASE=(
  ssh
  -o StrictHostKeyChecking=no
  -i "$RELAY_KEY_PATH"
  -p "$RELAY_PORT"
  "${RELAY_USER}@${RELAY_HOST}"
)

REMOTE_CMD=$(cat <<EOF
set -euo pipefail
if ! grep -Fqx '$AUTHORIZED_LINE' /home/whatsmatter/.ssh/authorized_keys; then
  echo '$AUTHORIZED_LINE' | sudo tee -a /home/whatsmatter/.ssh/authorized_keys >/dev/null
fi
sudo chown whatsmatter:whatsmatter /home/whatsmatter/.ssh/authorized_keys
sudo chmod 600 /home/whatsmatter/.ssh/authorized_keys
sudo register-hub '$HUB_ID' '$REMOTE_PORT' '$DEVICE_USER'
echo '--- hubs.map ---'
sudo tail -n 20 /opt/matterhub-relay/hubs.map
EOF
)

if [ "$DRY_RUN" -eq 1 ]; then
  log "relay_host=$RELAY_HOST relay_user=$RELAY_USER relay_port=$RELAY_PORT"
  log "hub_id=$HUB_ID remote_port=$REMOTE_PORT device_user=$DEVICE_USER"
  log "authorized_key_line=$AUTHORIZED_LINE"
  printf '[dry-run] %q ' "${SSH_BASE[@]}"
  printf '%q' "$REMOTE_CMD"
  printf '\n'
  exit 0
fi

log "registering hub on relay host=${RELAY_HOST} hub_id=${HUB_ID} remote_port=${REMOTE_PORT}"
"${SSH_BASE[@]}" "$REMOTE_CMD"
log "complete"
