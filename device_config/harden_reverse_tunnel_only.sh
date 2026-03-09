#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
SSHD_DROPIN_PATH="${SSHD_DROPIN_PATH:-/etc/ssh/sshd_config.d/90-matterhub-reverse-tunnel-only.conf}"
DRY_RUN=0
SKIP_UFW=0
SKIP_SSH=0
REQUIRE_TUNNEL_ACTIVE=1
ALLOW_INBOUND_PORTS=()

log() {
  printf '[reverse-tunnel-hardening] %s\n' "$*"
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

usage() {
  cat <<'EOF'
Usage: ./device_config/harden_reverse_tunnel_only.sh [options]

This script applies "reverse tunnel only" access hardening:
  - SSH daemon binds only to localhost (127.0.0.1)
  - Password login disabled, public-key login only
  - UFW reset + default deny incoming / allow outgoing
  - Optional explicit inbound allow-list ports

Options:
  --run-user <user>                Allowed SSH account on device (default: current user)
  --env-file <path>                .env path for support tunnel validation (default: <project>/.env)
  --allow-inbound-port <port>      Keep inbound TCP port open (repeatable)
  --skip-ufw                       Skip UFW policy setup
  --skip-sshd                      Skip SSH daemon hardening setup
  --no-require-tunnel-active       Do not block when support tunnel service is inactive
  --dry-run                        Print planned actions only
  -h, --help                       Show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --allow-inbound-port)
      ALLOW_INBOUND_PORTS+=("$2")
      shift 2
      ;;
    --skip-ufw)
      SKIP_UFW=1
      shift
      ;;
    --skip-sshd)
      SKIP_SSH=1
      shift
      ;;
    --no-require-tunnel-active)
      REQUIRE_TUNNEL_ACTIVE=0
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

if [ "$SKIP_UFW" -eq 1 ] && [ "$SKIP_SSH" -eq 1 ]; then
  echo "Nothing to do: both --skip-ufw and --skip-sshd are set." >&2
  exit 1
fi

if [ "$REQUIRE_TUNNEL_ACTIVE" -eq 1 ]; then
  SUPPORT_TUNNEL_ENABLED="$(get_env_value "SUPPORT_TUNNEL_ENABLED" "$ENV_FILE")"
  SUPPORT_TUNNEL_HOST="$(get_env_value "SUPPORT_TUNNEL_HOST" "$ENV_FILE")"
  SUPPORT_TUNNEL_USER="$(get_env_value "SUPPORT_TUNNEL_USER" "$ENV_FILE")"
  SUPPORT_TUNNEL_REMOTE_PORT="$(get_env_value "SUPPORT_TUNNEL_REMOTE_PORT" "$ENV_FILE")"

  if [ "$SUPPORT_TUNNEL_ENABLED" != "1" ]; then
    echo "SUPPORT_TUNNEL_ENABLED=1 is required before enabling reverse-tunnel-only hardening." >&2
    exit 1
  fi
  if [ -z "$SUPPORT_TUNNEL_HOST" ] || [ -z "$SUPPORT_TUNNEL_USER" ] || [ -z "$SUPPORT_TUNNEL_REMOTE_PORT" ]; then
    echo "SUPPORT_TUNNEL_HOST, SUPPORT_TUNNEL_USER, SUPPORT_TUNNEL_REMOTE_PORT must exist in ${ENV_FILE}." >&2
    exit 1
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    log "support tunnel config validated from ${ENV_FILE} (dry-run)"
  else
    if ! systemctl is-active --quiet matterhub-support-tunnel.service; then
      echo "matterhub-support-tunnel.service must be active before lock-down." >&2
      exit 1
    fi
    log "support tunnel service is active and config is valid"
  fi
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [ "$SKIP_SSH" -ne 1 ]; then
  SSHD_DROPIN_FILE="$TMP_DIR/90-matterhub-reverse-tunnel-only.conf"
  cat > "$SSHD_DROPIN_FILE" <<EOF
# Managed by harden_reverse_tunnel_only.sh
ListenAddress 127.0.0.1
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers ${RUN_USER}
AllowTcpForwarding yes
GatewayPorts no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
EOF

  log "installing sshd hardening drop-in: ${SSHD_DROPIN_PATH}"
  sudo_cmd install -m 0644 "$SSHD_DROPIN_FILE" "$SSHD_DROPIN_PATH"

  if [ "$DRY_RUN" -eq 1 ]; then
    log "sshd drop-in preview:"
    sed 's/^/[dry-run]   /' "$SSHD_DROPIN_FILE"
  else
    if command -v sshd >/dev/null 2>&1; then
      sudo_cmd sshd -t
    elif [ -x /usr/sbin/sshd ]; then
      sudo_cmd /usr/sbin/sshd -t
    else
      echo "sshd binary not found." >&2
      exit 1
    fi

    if sudo_cmd systemctl restart ssh.service 2>/dev/null; then
      log "restarted ssh.service"
    elif sudo_cmd systemctl restart sshd.service 2>/dev/null; then
      log "restarted sshd.service"
    elif sudo_cmd systemctl restart ssh.socket 2>/dev/null; then
      log "restarted ssh.socket"
    else
      echo "Unable to restart ssh service (ssh.service/sshd.service/ssh.socket)." >&2
      exit 1
    fi
  fi
fi

if [ "$SKIP_UFW" -ne 1 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    log "preparing UFW reverse-tunnel-only policy (dry-run)"
  else
    if ! command -v ufw >/dev/null 2>&1; then
      sudo_cmd apt update
      sudo_cmd apt install -y ufw
    fi
  fi

  sudo_cmd ufw --force reset
  sudo_cmd ufw default deny incoming
  sudo_cmd ufw default allow outgoing

  for port in "${ALLOW_INBOUND_PORTS[@]-}"; do
    if [ -z "$port" ]; then
      continue
    fi
    if ! [[ "$port" =~ ^[0-9]+$ ]]; then
      echo "Invalid port in --allow-inbound-port: ${port}" >&2
      exit 1
    fi
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
      echo "Port out of range in --allow-inbound-port: ${port}" >&2
      exit 1
    fi
    sudo_cmd ufw allow "${port}/tcp"
  done

  sudo_cmd ufw --force enable
  sudo_cmd ufw status verbose
fi

log "reverse-tunnel-only hardening complete"
