#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"

RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
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

WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
WIFI_HEALTH_HOST="${WIFI_HEALTH_HOST:-8.8.8.8}"
WIFI_AP_SSID="${WIFI_AP_SSID:-Matterhub-Setup-WhatsMatter}"
WIFI_AP_PASSWORD="${WIFI_AP_PASSWORD:-matterhub1234}"
WIFI_AP_IPV4_CIDR="${WIFI_AP_IPV4_CIDR:-10.42.0.1/24}"
WIFI_AUTO_AP_ON_BOOT="${WIFI_AUTO_AP_ON_BOOT:-1}"
WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS="${WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS:-45}"
WIFI_BOOTSTRAP_AP_SSID="${WIFI_BOOTSTRAP_AP_SSID:-}"
WIFI_BOOTSTRAP_AP_PASSWORD="${WIFI_BOOTSTRAP_AP_PASSWORD:-}"
LOCAL_MDNS_ENABLED="${LOCAL_MDNS_ENABLED:-1}"
MATTERHUB_LOCAL_HOSTNAME="${MATTERHUB_LOCAL_HOSTNAME:-matterhub-setup-whatsmatter}"
MATTERHUB_LOCAL_SERVICE_NAME="${MATTERHUB_LOCAL_SERVICE_NAME:-MatterHub Wi-Fi Setup}"
UPDATE_AGENT_ENABLED="${UPDATE_AGENT_ENABLED:-1}"
UPDATE_AGENT_POLL_SECONDS="${UPDATE_AGENT_POLL_SECONDS:-15}"
UPDATE_AGENT_REQUIRE_MANIFEST="${UPDATE_AGENT_REQUIRE_MANIFEST:-1}"
UPDATE_AGENT_REQUIRE_SHA256="${UPDATE_AGENT_REQUIRE_SHA256:-0}"
UPDATE_AGENT_ALLOWED_BUNDLE_TYPES="${UPDATE_AGENT_ALLOWED_BUNDLE_TYPES:-matterhub-runtime,matterhub-update}"

log() {
  printf '[matterhub-initial-setup] %s\n' "$*"
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
Usage: ./device_config/setup_initial_device.sh [options]

Initial setup wrapper:
  1) Writes Wi-Fi/AP defaults to .env
  2) Executes install_ubuntu24.sh (venv, requirements, NetworkManager, openssh-server, polkit, systemd)
  3) Optionally chains support tunnel setup options

Options:
  --env-file <path>                  Target .env path (default: <project>/.env)
  --dry-run                          Show planned updates/commands only
  --skip-os-packages                 Pass through to install_ubuntu24.sh

  --wifi-interface <name>            Default: wlan0
  --wifi-health-host <host>          Default: 8.8.8.8
  --wifi-ap-ssid <ssid>              Default: Matterhub-Setup-WhatsMatter
  --wifi-ap-password <password>      Default: matterhub1234
  --wifi-ap-ipv4-cidr <cidr>         Default: 10.42.0.1/24
  --wifi-auto-ap-on-boot <0|1>       Default: 1
  --wifi-bootstrap-startup-grace-seconds <sec>
                                     Default: 45 (AP 시작 전 대기)
  --wifi-bootstrap-ap-ssid <ssid>    Optional
  --wifi-bootstrap-ap-password <pw>  Optional
  --local-mdns-enabled <0|1>         Default: 1
  --local-hostname <name>            Default: matterhub-setup-whatsmatter
  --local-service-name <name>        Default: MatterHub Wi-Fi Setup
  --update-agent-enabled <0|1>       Default: 1
  --update-agent-poll-seconds <sec>  Default: 15
  --update-agent-require-manifest <0|1>
                                     Default: 1
  --update-agent-require-sha256 <0|1>
                                     Default: 0
  --update-agent-allowed-bundle-types <csv>
                                     Default: matterhub-runtime,matterhub-update

  --setup-support-tunnel             Pass through to install_ubuntu24.sh
  --enable-support-tunnel-now        Pass through to install_ubuntu24.sh
  --support-host <host>              Pass through
  --support-user <user>              Pass through
  --support-port <port>              Pass through
  --support-remote-port <port>       Pass through
  --support-device-user <user>       Pass through
  --support-relay-operator-user <u>  Pass through
  --support-relay-access-pubkey <k>  Pass through
  --harden-reverse-tunnel-only       Pass through
  --harden-allow-inbound-port <p>    Pass through (repeatable)
  --harden-local-console-pam         Pass through
  -h, --help                         Show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --skip-os-packages)
      SKIP_OS_PACKAGES=1
      shift
      ;;
    --wifi-interface)
      WIFI_INTERFACE="$2"
      shift 2
      ;;
    --wifi-health-host)
      WIFI_HEALTH_HOST="$2"
      shift 2
      ;;
    --wifi-ap-ssid)
      WIFI_AP_SSID="$2"
      shift 2
      ;;
    --wifi-ap-password)
      WIFI_AP_PASSWORD="$2"
      shift 2
      ;;
    --wifi-ap-ipv4-cidr)
      WIFI_AP_IPV4_CIDR="$2"
      shift 2
      ;;
    --wifi-auto-ap-on-boot)
      WIFI_AUTO_AP_ON_BOOT="$2"
      shift 2
      ;;
    --wifi-bootstrap-startup-grace-seconds)
      WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS="$2"
      shift 2
      ;;
    --wifi-bootstrap-ap-ssid)
      WIFI_BOOTSTRAP_AP_SSID="$2"
      shift 2
      ;;
    --wifi-bootstrap-ap-password)
      WIFI_BOOTSTRAP_AP_PASSWORD="$2"
      shift 2
      ;;
    --local-mdns-enabled)
      LOCAL_MDNS_ENABLED="$2"
      shift 2
      ;;
    --local-hostname)
      MATTERHUB_LOCAL_HOSTNAME="$2"
      shift 2
      ;;
    --local-service-name)
      MATTERHUB_LOCAL_SERVICE_NAME="$2"
      shift 2
      ;;
    --update-agent-enabled)
      UPDATE_AGENT_ENABLED="$2"
      shift 2
      ;;
    --update-agent-poll-seconds)
      UPDATE_AGENT_POLL_SECONDS="$2"
      shift 2
      ;;
    --update-agent-require-manifest)
      UPDATE_AGENT_REQUIRE_MANIFEST="$2"
      shift 2
      ;;
    --update-agent-require-sha256)
      UPDATE_AGENT_REQUIRE_SHA256="$2"
      shift 2
      ;;
    --update-agent-allowed-bundle-types)
      UPDATE_AGENT_ALLOWED_BUNDLE_TYPES="$2"
      shift 2
      ;;
    --setup-support-tunnel)
      SETUP_SUPPORT_TUNNEL=1
      shift
      ;;
    --enable-support-tunnel-now)
      ENABLE_SUPPORT_TUNNEL_NOW=1
      shift
      ;;
    --support-host)
      SUPPORT_HOST="$2"
      shift 2
      ;;
    --support-user)
      SUPPORT_USER="$2"
      shift 2
      ;;
    --support-port)
      SUPPORT_PORT="$2"
      shift 2
      ;;
    --support-remote-port)
      SUPPORT_REMOTE_PORT="$2"
      shift 2
      ;;
    --support-device-user)
      SUPPORT_DEVICE_USER="$2"
      shift 2
      ;;
    --support-relay-operator-user)
      SUPPORT_RELAY_OPERATOR_USER="$2"
      shift 2
      ;;
    --support-relay-access-pubkey)
      SUPPORT_RELAY_ACCESS_PUBKEY="$2"
      shift 2
      ;;
    --harden-reverse-tunnel-only)
      HARDEN_REVERSE_TUNNEL_ONLY=1
      shift
      ;;
    --harden-allow-inbound-port)
      HARDEN_ALLOW_INBOUND_PORTS+=("$2")
      shift 2
      ;;
    --harden-local-console-pam)
      HARDEN_LOCAL_CONSOLE_PAM=1
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

if [ "${#WIFI_AP_PASSWORD}" -lt 8 ]; then
  echo "WIFI_AP_PASSWORD must be at least 8 characters." >&2
  exit 1
fi

case "$WIFI_AUTO_AP_ON_BOOT" in
  0|1|true|false|yes|no)
    ;;
  *)
    echo "--wifi-auto-ap-on-boot value must be one of: 0,1,true,false,yes,no" >&2
    exit 1
    ;;
esac

case "$LOCAL_MDNS_ENABLED" in
  0|1|true|false|yes|no)
    ;;
  *)
    echo "--local-mdns-enabled value must be one of: 0,1,true,false,yes,no" >&2
    exit 1
    ;;
esac

if ! [[ "$WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "--wifi-bootstrap-startup-grace-seconds must be a non-negative integer" >&2
  exit 1
fi

if ! [[ "$UPDATE_AGENT_POLL_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "--update-agent-poll-seconds must be a non-negative integer" >&2
  exit 1
fi

for flag in "$UPDATE_AGENT_ENABLED" "$UPDATE_AGENT_REQUIRE_MANIFEST" "$UPDATE_AGENT_REQUIRE_SHA256"; do
  case "$flag" in
    0|1|true|false|yes|no)
      ;;
    *)
      echo "update-agent boolean options must be one of: 0,1,true,false,yes,no" >&2
      exit 1
      ;;
  esac
done

INSTALL_SCRIPT="$SCRIPT_DIR/install_ubuntu24.sh"
if [ ! -f "$INSTALL_SCRIPT" ]; then
  echo "install_ubuntu24.sh not found: $INSTALL_SCRIPT" >&2
  exit 1
fi

log "env_file=$ENV_FILE"
log "Wi-Fi/AP defaults will be managed in .env"

set_env_value "WIFI_INTERFACE" "$WIFI_INTERFACE"
set_env_value "WIFI_HEALTH_HOST" "$WIFI_HEALTH_HOST"
set_env_value "WIFI_AP_SSID" "$WIFI_AP_SSID"
set_env_value "WIFI_AP_PASSWORD" "$WIFI_AP_PASSWORD"
set_env_value "WIFI_AP_IPV4_CIDR" "$WIFI_AP_IPV4_CIDR"
set_env_value "WIFI_AUTO_AP_ON_BOOT" "$WIFI_AUTO_AP_ON_BOOT"
set_env_value "WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS" "$WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS"
set_env_value "LOCAL_MDNS_ENABLED" "$LOCAL_MDNS_ENABLED"
set_env_value "MATTERHUB_LOCAL_HOSTNAME" "$MATTERHUB_LOCAL_HOSTNAME"
set_env_value "MATTERHUB_LOCAL_SERVICE_NAME" "$MATTERHUB_LOCAL_SERVICE_NAME"
set_env_value "UPDATE_AGENT_ENABLED" "$UPDATE_AGENT_ENABLED"
set_env_value "UPDATE_AGENT_POLL_SECONDS" "$UPDATE_AGENT_POLL_SECONDS"
set_env_value "UPDATE_AGENT_REQUIRE_MANIFEST" "$UPDATE_AGENT_REQUIRE_MANIFEST"
set_env_value "UPDATE_AGENT_REQUIRE_SHA256" "$UPDATE_AGENT_REQUIRE_SHA256"
set_env_value "UPDATE_AGENT_ALLOWED_BUNDLE_TYPES" "$UPDATE_AGENT_ALLOWED_BUNDLE_TYPES"

if [ -n "$WIFI_BOOTSTRAP_AP_SSID" ]; then
  set_env_value "WIFI_BOOTSTRAP_AP_SSID" "$WIFI_BOOTSTRAP_AP_SSID"
fi
if [ -n "$WIFI_BOOTSTRAP_AP_PASSWORD" ]; then
  set_env_value "WIFI_BOOTSTRAP_AP_PASSWORD" "$WIFI_BOOTSTRAP_AP_PASSWORD"
fi

install_cmd=(bash "$INSTALL_SCRIPT")
if [ "$DRY_RUN" -eq 1 ]; then
  install_cmd+=(--dry-run)
fi
if [ "$SKIP_OS_PACKAGES" -eq 1 ]; then
  install_cmd+=(--skip-os-packages)
fi
case "$LOCAL_MDNS_ENABLED" in
  0|false|no)
    install_cmd+=(--disable-local-mdns)
    ;;
esac
install_cmd+=(--local-hostname "$MATTERHUB_LOCAL_HOSTNAME")
install_cmd+=(--local-service-name "$MATTERHUB_LOCAL_SERVICE_NAME")
if [ "$SETUP_SUPPORT_TUNNEL" -eq 1 ]; then
  install_cmd+=(--setup-support-tunnel)
fi
if [ "$ENABLE_SUPPORT_TUNNEL_NOW" -eq 1 ]; then
  install_cmd+=(--enable-support-tunnel-now)
fi
if [ -n "$SUPPORT_HOST" ]; then
  install_cmd+=(--support-host "$SUPPORT_HOST")
fi
if [ -n "$SUPPORT_USER" ]; then
  install_cmd+=(--support-user "$SUPPORT_USER")
fi
if [ -n "$SUPPORT_PORT" ]; then
  install_cmd+=(--support-port "$SUPPORT_PORT")
fi
if [ -n "$SUPPORT_REMOTE_PORT" ]; then
  install_cmd+=(--support-remote-port "$SUPPORT_REMOTE_PORT")
fi
if [ -n "$SUPPORT_DEVICE_USER" ]; then
  install_cmd+=(--support-device-user "$SUPPORT_DEVICE_USER")
fi
if [ -n "$SUPPORT_RELAY_OPERATOR_USER" ]; then
  install_cmd+=(--support-relay-operator-user "$SUPPORT_RELAY_OPERATOR_USER")
fi
if [ -n "$SUPPORT_RELAY_ACCESS_PUBKEY" ]; then
  install_cmd+=(--support-relay-access-pubkey "$SUPPORT_RELAY_ACCESS_PUBKEY")
fi
if [ "$HARDEN_REVERSE_TUNNEL_ONLY" -eq 1 ]; then
  install_cmd+=(--harden-reverse-tunnel-only)
fi
for port in "${HARDEN_ALLOW_INBOUND_PORTS[@]-}"; do
  if [ -z "$port" ]; then
    continue
  fi
  install_cmd+=(--harden-allow-inbound-port "$port")
done
if [ "$HARDEN_LOCAL_CONSOLE_PAM" -eq 1 ]; then
  install_cmd+=(--harden-local-console-pam)
fi

log "install_ubuntu24.sh 실행"
run_cmd "${install_cmd[@]}"

log "초기 통합 설정 완료"
