#!/usr/bin/env bash

set -euo pipefail

RUN_USER="${RUN_USER:-matterhub}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/opt/matterhub}"
CONFIG_DIR="${CONFIG_DIR:-/etc/matterhub}"
ENV_FILE="${ENV_FILE:-$CONFIG_DIR/matterhub.env}"
PROVISION_BIN="${PROVISION_BIN:-$INSTALL_PREFIX/bin/matterhub-provision}"
SUPPORT_TUNNEL_SETUP_SCRIPT="${SUPPORT_TUNNEL_SETUP_SCRIPT:-$INSTALL_PREFIX/device_config/setup_support_tunnel.sh}"
CURRENT_UID="${CURRENT_UID:-$(id -u)}"
RUN_AS_USER_HELPER="${RUN_AS_USER_HELPER:-}"
PROVISION_AS_RUN_USER="${PROVISION_AS_RUN_USER:-0}"

log() {
  printf '[matterhub-provision-bootstrap] %s\n' "$*"
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

normalize_env_permissions() {
  if [ ! -f "$ENV_FILE" ]; then
    return
  fi
  if [ "$CURRENT_UID" -eq 0 ] && [ -n "$RUN_USER" ]; then
    chown "root:${RUN_USER}" "$ENV_FILE" 2>/dev/null || true
  fi
  chmod 660 "$ENV_FILE" 2>/dev/null || true
}

run_provision() {
  if [ "$PROVISION_AS_RUN_USER" = "1" ] && [ "$CURRENT_UID" -eq 0 ] && [ -n "$RUN_USER" ]; then
    log "running provisioning as $RUN_USER"
    if [ -n "$RUN_AS_USER_HELPER" ]; then
      "$RUN_AS_USER_HELPER" "$RUN_USER" "$PROVISION_BIN" --ensure --non-interactive
      return
    fi
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$RUN_USER" -- "$PROVISION_BIN" --ensure --non-interactive
      return
    fi
    if command -v sudo >/dev/null 2>&1; then
      sudo -u "$RUN_USER" "$PROVISION_BIN" --ensure --non-interactive
      return
    fi
  fi

  "$PROVISION_BIN" --ensure --non-interactive
}

if [ ! -x "$PROVISION_BIN" ]; then
  echo "matterhub-provision binary not found: $PROVISION_BIN" >&2
  exit 1
fi

log "running claim provisioning ensure step"
run_provision
normalize_env_permissions

MATTERHUB_ID="$(get_env_value "matterhub_id" "$ENV_FILE")"
if [ -z "$MATTERHUB_ID" ]; then
  log "matterhub_id is still missing after provisioning; support tunnel setup deferred"
  exit 0
fi

if [ ! -x "$SUPPORT_TUNNEL_SETUP_SCRIPT" ]; then
  echo "support tunnel setup script not found: $SUPPORT_TUNNEL_SETUP_SCRIPT" >&2
  exit 1
fi

log "matterhub_id available: $MATTERHUB_ID"
log "ensuring support tunnel is configured and enabled"
bash "$SUPPORT_TUNNEL_SETUP_SCRIPT" \
  --env-file "$ENV_FILE" \
  --run-user "$RUN_USER" \
  --skip-install-unit \
  --enable-now

normalize_env_permissions
