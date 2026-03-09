#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
AVAHI_SERVICE_DIR="${AVAHI_SERVICE_DIR:-/etc/avahi/services}"
AVAHI_SERVICE_FILE="${AVAHI_SERVICE_FILE:-matterhub-wifi-admin.service}"
HOSTS_PATH="${HOSTS_PATH:-/etc/hosts}"

DRY_RUN=0
HOSTNAME_VALUE="${MATTERHUB_LOCAL_HOSTNAME:-matterhub-setup-whatsmatter}"
SERVICE_NAME="${MATTERHUB_LOCAL_SERVICE_NAME:-MatterHub Wi-Fi Setup}"
HTTP_PORT="${MATTERHUB_LOCAL_HTTP_PORT:-8100}"
SETUP_PATH="${MATTERHUB_LOCAL_SETUP_PATH:-/local/admin/network}"

log() {
  printf '[matterhub-local-mdns] %s\n' "$*"
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
Usage: ./device_config/setup_local_hostname_mdns.sh [options]

Configure a stable local mDNS hostname and advertise MatterHub Wi-Fi setup page via Avahi.

Options:
  --env-file <path>         Optional .env path reference for logs
  --hostname <name>         Local hostname label (default: matterhub-setup-whatsmatter)
  --service-name <name>     DNS-SD HTTP service name (default: MatterHub Wi-Fi Setup)
  --http-port <port>        HTTP port to advertise (default: 8100)
  --setup-path <path>       Setup path TXT record (default: /local/admin/network)
  --dry-run                 Print actions only
  -h, --help                Show help
EOF
}

normalize_hostname() {
  local raw="$1"
  local normalized
  normalized="$(printf '%s' "$raw" | tr '[:upper:]_' '[:lower:]-' | sed -E 's/[^a-z0-9-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')"
  if [ -z "$normalized" ]; then
    normalized="matterhub-setup-whatsmatter"
  fi
  normalized="${normalized:0:63}"
  normalized="${normalized%-}"
  normalized="${normalized#-}"
  if [ -z "$normalized" ]; then
    normalized="matterhub-setup-whatsmatter"
  fi
  printf '%s' "$normalized"
}

xml_escape() {
  local raw="$1"
  raw="${raw//&/&amp;}"
  raw="${raw//</&lt;}"
  raw="${raw//>/&gt;}"
  printf '%s' "$raw"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --hostname)
      HOSTNAME_VALUE="$2"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --http-port)
      HTTP_PORT="$2"
      shift 2
      ;;
    --setup-path)
      SETUP_PATH="$2"
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
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! [[ "$HTTP_PORT" =~ ^[0-9]+$ ]]; then
  echo "--http-port must be numeric" >&2
  exit 1
fi

if [ "$HTTP_PORT" -lt 1 ] || [ "$HTTP_PORT" -gt 65535 ]; then
  echo "--http-port must be between 1 and 65535" >&2
  exit 1
fi

if [ -z "$SETUP_PATH" ]; then
  echo "--setup-path must not be empty" >&2
  exit 1
fi

if [[ "$SETUP_PATH" != /* ]]; then
  SETUP_PATH="/$SETUP_PATH"
fi

HOSTNAME_VALUE="$(normalize_hostname "$HOSTNAME_VALUE")"
SERVICE_NAME_ESCAPED="$(xml_escape "$SERVICE_NAME")"
SETUP_PATH_ESCAPED="$(xml_escape "$SETUP_PATH")"
TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

SERVICE_FILE_PATH="$TMP_DIR/$AVAHI_SERVICE_FILE"
HOSTS_TMP="$TMP_DIR/hosts"
cat > "$SERVICE_FILE_PATH" <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">${SERVICE_NAME_ESCAPED}</name>
  <service>
    <type>_http._tcp</type>
    <port>${HTTP_PORT}</port>
    <txt-record>path=${SETUP_PATH_ESCAPED}</txt-record>
  </service>
</service-group>
EOF

render_hosts_file() {
  local source_path="$1"
  local output_path="$2"
  local hostname_value="$3"

  if [ -f "$source_path" ]; then
    cp "$source_path" "$output_path"
  else
    : > "$output_path"
  fi

  awk -v hostname_value="$hostname_value" '
    BEGIN { updated=0 }
    /^[[:space:]]*127\.0\.1\.1([[:space:]]|$)/ {
      if (!updated) {
        print "127.0.1.1 " hostname_value
        updated=1
      }
      next
    }
    { print }
    END {
      if (!updated) {
        print "127.0.1.1 " hostname_value
      }
    }
  ' "$output_path" > "$output_path.new"
  mv "$output_path.new" "$output_path"
}

render_hosts_file "$HOSTS_PATH" "$HOSTS_TMP" "$HOSTNAME_VALUE"

log "env_file=$ENV_FILE"
log "normalized_hostname=$HOSTNAME_VALUE"
log "service_name=$SERVICE_NAME"
log "preferred_url=http://${HOSTNAME_VALUE}.local:${HTTP_PORT}${SETUP_PATH}"
log "hosts_entry=127.0.1.1 ${HOSTNAME_VALUE}"

sudo_cmd hostnamectl set-hostname "$HOSTNAME_VALUE"
sudo_cmd install -m 0644 "$HOSTS_TMP" "$HOSTS_PATH"
sudo_cmd install -d -m 0755 "$AVAHI_SERVICE_DIR"
sudo_cmd install -m 0644 "$SERVICE_FILE_PATH" "$AVAHI_SERVICE_DIR/$AVAHI_SERVICE_FILE"
sudo_cmd systemctl enable --now avahi-daemon
sudo_cmd systemctl restart avahi-daemon

log "fallback_url=http://10.42.0.1:${HTTP_PORT}${SETUP_PATH}"
