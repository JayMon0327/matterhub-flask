#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PACKAGE_NAME="${PACKAGE_NAME:-matterhub}"
VERSION="${VERSION:-$(date +%Y.%m.%d)-$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo local)}"
ARCH="${ARCH:-arm64}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/dist}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/opt/matterhub}"
CONFIG_DIR="${CONFIG_DIR:-/etc/matterhub}"
RUN_USER="${RUN_USER:-matterhub}"
MODE="${MODE:-source}"
DRY_RUN=0

log() {
  printf '[matterhub-deb-build] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage: ./device_config/build_matterhub_deb.sh [options]

Options:
  --package-name <name>      Package name (default: matterhub)
  --version <version>        Package version (default: YYYY.MM.DD-<gitsha>)
  --arch <arch>              Debian arch (default: arm64)
  --output-dir <dir>         Output directory for .deb (default: ./dist)
  --install-prefix <path>    App install root inside package (default: /opt/matterhub)
  --config-dir <path>        Config directory (default: /etc/matterhub)
  --run-user <user>          Runtime system user created in postinst (default: matterhub)
  --mode <pyc|source>        Payload code mode (default: source)
  --dry-run                  Print build plan only
  -h, --help                 Show help

Notes:
  - mode=source ships .py files and is the safest cross-device default.
  - mode=pyc compiles Python files to .pyc and removes .py from payload.
  - pyc mode requires the build Python runtime to match the target Python runtime.
  - This raises source exposure cost but does not make reverse engineering impossible.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --package-name)
      PACKAGE_NAME="$2"
      shift 2
      ;;
    --version)
      VERSION="$2"
      shift 2
      ;;
    --arch)
      ARCH="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --install-prefix)
      INSTALL_PREFIX="$2"
      shift 2
      ;;
    --config-dir)
      CONFIG_DIR="$2"
      shift 2
      ;;
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --mode)
      MODE="$2"
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

if [ "$MODE" != "pyc" ] && [ "$MODE" != "source" ]; then
  echo "--mode must be one of: pyc, source" >&2
  exit 1
fi

if [ "$DRY_RUN" -ne 1 ]; then
  if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb is required." >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required." >&2
    exit 1
  fi
fi

DEB_FILE="${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
log "package_name=${PACKAGE_NAME}"
log "version=${VERSION}"
log "arch=${ARCH}"
log "mode=${MODE}"
log "install_prefix=${INSTALL_PREFIX}"
log "config_dir=${CONFIG_DIR}"
log "run_user=${RUN_USER}"
log "output_file=${OUTPUT_DIR}/${DEB_FILE}"

if [ "$DRY_RUN" -eq 1 ]; then
  log "plan: copy runtime payload into package root"
  if [ "$MODE" = "pyc" ]; then
    log "plan: compile python payload as pyc and remove .py files (mode=pyc)"
    log "plan: require build Python version to match target Python version"
  else
    log "plan: package python payload as source files (mode=source)"
  fi
  log "plan: include update-agent in package payload and service units"
  log "plan: include claim and Konai certificate assets plus provisioning launcher"
  log "plan: write default env with Wi-Fi automation/AP disabled for packaging phase"
  log "plan: link app .env to config env and bootstrap claim provisioning via systemd"
  log "plan: auto-configure and enable support tunnel after matterhub_id is available, including reboot retries"
  log "plan: add persistent UFW allow rules for 8100/tcp and 8123/tcp when ufw exists"
  log "plan: generate launcher scripts and systemd units"
  log "plan: generate DEBIAN control/postinst/prerm/postrm metadata"
  log "plan: run dpkg-deb --build"
  exit 0
fi

BUILD_ROOT="$(mktemp -d)"
PKG_ROOT="${BUILD_ROOT}/${PACKAGE_NAME}"
APP_DIR="${PKG_ROOT}${INSTALL_PREFIX}/app"
BIN_DIR="${PKG_ROOT}${INSTALL_PREFIX}/bin"
TOOL_DIR="${PKG_ROOT}${INSTALL_PREFIX}/device_config"
SYSTEMD_DIR="${PKG_ROOT}/usr/lib/systemd/system"
DEBIAN_DIR="${PKG_ROOT}/DEBIAN"
ENV_DIR="${PKG_ROOT}${CONFIG_DIR}"
RUNTIME_DIRS=(mqtt_pkg sub libs wifi_config templates certificates konai_certificates)
RUNTIME_FILES=(app.py mqtt.py support_tunnel.py update_agent.py run_provision.py requirements.txt)
PACKAGE_TOOL_FILES=(setup_support_tunnel.sh ensure_provisioned_support_tunnel.sh apply_update_bundle.sh)

cleanup() {
  rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT

mkdir -p "$APP_DIR" "$BIN_DIR" "$TOOL_DIR" "$SYSTEMD_DIR" "$DEBIAN_DIR" "$ENV_DIR"

for path in "${RUNTIME_DIRS[@]}"; do
  if [ -e "${PROJECT_ROOT}/${path}" ]; then
    cp -R "${PROJECT_ROOT}/${path}" "$APP_DIR/"
  fi
done
for path in "${RUNTIME_FILES[@]}"; do
  if [ -e "${PROJECT_ROOT}/${path}" ]; then
    cp "${PROJECT_ROOT}/${path}" "$APP_DIR/"
  fi
done
for path in "${PACKAGE_TOOL_FILES[@]}"; do
  if [ -e "${PROJECT_ROOT}/device_config/${path}" ]; then
    cp "${PROJECT_ROOT}/device_config/${path}" "$TOOL_DIR/"
    chmod 755 "${TOOL_DIR}/${path}"
  fi
done

find "$APP_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$APP_DIR" -type f -name '*.pyc' -delete
find "$APP_DIR" -type f -name '*.pyo' -delete

ENTRY_EXT="py"
if [ "$MODE" = "pyc" ]; then
  python3 -m compileall -q -b "$APP_DIR"
  find "$APP_DIR" -type f -name '*.py' -delete
  ENTRY_EXT="pyc"
fi

create_launcher() {
  local name="$1"
  local target="$2"
  cat > "${BIN_DIR}/${name}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${INSTALL_PREFIX}/venv/bin/python"
if [ ! -x "\$PYTHON_BIN" ]; then
  PYTHON_BIN="/usr/bin/python3"
fi
export PYTHONPATH="${INSTALL_PREFIX}/app:\${PYTHONPATH:-}"
exec "\$PYTHON_BIN" "${INSTALL_PREFIX}/app/${target}.${ENTRY_EXT}" "\$@"
EOF
  chmod 755 "${BIN_DIR}/${name}"
}

create_launcher "matterhub-api" "app"
create_launcher "matterhub-mqtt" "mqtt"
create_launcher "matterhub-rule-engine" "sub/ruleEngine"
create_launcher "matterhub-notifier" "sub/notifier"
create_launcher "matterhub-support-tunnel" "support_tunnel"
create_launcher "matterhub-update-agent" "update_agent"
create_launcher "matterhub-provision" "run_provision"

create_unit() {
  local unit_name="$1"
  local description="$2"
  local exec_path="$3"
  local service_user="${4:-$RUN_USER}"
  local no_new_privileges="NoNewPrivileges=true"
  local restrict_suidsgid="RestrictSUIDSGID=true"
  local capability_bounding_set="CapabilityBoundingSet="
  local ambient_capabilities="AmbientCapabilities="
  local protect_system="ProtectSystem=full"
  local read_write_paths=""
  if [ "$unit_name" = "matterhub-api" ]; then
    no_new_privileges=""
    restrict_suidsgid=""
    capability_bounding_set=""
    ambient_capabilities=""
    protect_system="ProtectSystem=false"
    read_write_paths="ReadWritePaths=/etc/matterhub"
  fi
  cat > "${SYSTEMD_DIR}/${unit_name}.service" <<EOF
[Unit]
Description=${description}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${service_user}
Group=${service_user}
WorkingDirectory=${INSTALL_PREFIX}/app
EnvironmentFile=-${CONFIG_DIR}/matterhub.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${exec_path}
Restart=always
RestartSec=5
${no_new_privileges}
PrivateTmp=true
${protect_system}
ProtectControlGroups=true
ProtectKernelTunables=true
ProtectKernelModules=true
${restrict_suidsgid}
LockPersonality=true
RestrictRealtime=true
${capability_bounding_set}
${ambient_capabilities}
${read_write_paths}
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
}

create_unit "matterhub-api" "MatterHub Flask API" "${INSTALL_PREFIX}/bin/matterhub-api"
create_unit "matterhub-mqtt" "MatterHub MQTT Worker" "${INSTALL_PREFIX}/bin/matterhub-mqtt"
create_unit "matterhub-rule-engine" "MatterHub Rule Engine" "${INSTALL_PREFIX}/bin/matterhub-rule-engine"
create_unit "matterhub-notifier" "MatterHub Notifier" "${INSTALL_PREFIX}/bin/matterhub-notifier"
create_unit "matterhub-support-tunnel" "MatterHub Support Tunnel" "${INSTALL_PREFIX}/bin/matterhub-support-tunnel"
create_unit "matterhub-update-agent" "MatterHub Update Agent" "${INSTALL_PREFIX}/bin/matterhub-update-agent" "root"

cat > "${SYSTEMD_DIR}/matterhub-provision.service" <<EOF
[Unit]
Description=MatterHub Claim Provisioning Bootstrap
After=network-online.target
Wants=network-online.target
Before=matterhub-mqtt.service matterhub-update-agent.service

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=${INSTALL_PREFIX}/app
EnvironmentFile=-${CONFIG_DIR}/matterhub.env
Environment=PYTHONUNBUFFERED=1
Environment=RUN_USER=${RUN_USER}
Environment=INSTALL_PREFIX=${INSTALL_PREFIX}
Environment=CONFIG_DIR=${CONFIG_DIR}
Environment=ENV_FILE=${CONFIG_DIR}/matterhub.env
ExecStart=${INSTALL_PREFIX}/device_config/ensure_provisioned_support_tunnel.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectControlGroups=true
ProtectKernelTunables=true
ProtectKernelModules=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

cat > "${ENV_DIR}/matterhub.env" <<'EOF'
# MatterHub runtime configuration
# copy values from deployment .env as needed
SUPPORT_TUNNEL_ENABLED=0
SUPPORT_TUNNEL_COMMAND=ssh
SUPPORT_TUNNEL_PORT=443
SUPPORT_TUNNEL_LOCAL_PORT=22
SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS=127.0.0.1
SUPPORT_TUNNEL_HOST=3.38.126.167
SUPPORT_TUNNEL_USER=whatsmatter
MATTERHUB_AUTO_PROVISION=1
WIFI_AUTO_AP_ON_BOOT=0
WIFI_AUTO_AP_ON_DISCONNECT=0
WIFI_AP_AUTO_RECONNECT_ENABLED=0

# Application Resource Paths
res_file_path=resources
cert_file_path=cert
schedules_file_path=resources/schedule.json
rules_file_path=resources/rules.json
rooms_file_path=resources/rooms.json
devices_file_path=resources/devices.json
notifications_file_path=resources/notifications.json
UPDATE_AGENT_PROJECT_ROOT=/opt/matterhub

# Home Assistant Connection
HA_host=http://localhost:8123
hass_token=REPLACE_ME
EOF

cat > "${DEBIAN_DIR}/conffiles" <<EOF
${CONFIG_DIR}/matterhub.env
EOF

cat > "${DEBIAN_DIR}/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: MatterHub Ops <ops@whatsmatter.local>
Depends: python3, python3-venv, python3-pip, network-manager, openssh-client, openssh-server
Description: MatterHub runtime package
 MatterHub services packaged for Raspberry Pi Ubuntu 24.04 deployment.
EOF

cat > "${DEBIAN_DIR}/postinst" <<EOF
#!/usr/bin/env bash
set -euo pipefail

RUN_USER="${RUN_USER}"
INSTALL_PREFIX="${INSTALL_PREFIX}"
CONFIG_DIR="${CONFIG_DIR}"

if ! id -u "\${RUN_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "\${RUN_USER}"
fi

mkdir -p "\${INSTALL_PREFIX}" "\${CONFIG_DIR}" /var/lib/matterhub /var/log/matterhub
chown -R "\${RUN_USER}:\${RUN_USER}" "\${INSTALL_PREFIX}" /var/lib/matterhub /var/log/matterhub
if [ -f "\${CONFIG_DIR}/matterhub.env" ]; then
  chown root:"\${RUN_USER}" "\${CONFIG_DIR}/matterhub.env"
  chmod 660 "\${CONFIG_DIR}/matterhub.env"
fi

if [ ! -d "\${INSTALL_PREFIX}/venv" ]; then
  python3 -m venv "\${INSTALL_PREFIX}/venv"
fi
"\${INSTALL_PREFIX}/venv/bin/python" - <<'PY'
from pathlib import Path
install_prefix = Path("${INSTALL_PREFIX}")
config_env = Path("${CONFIG_DIR}") / "matterhub.env"
app_env = install_prefix / "app" / ".env"
app_env.unlink(missing_ok=True)
app_env.symlink_to(config_env)
PY
"\${INSTALL_PREFIX}/venv/bin/pip" install --upgrade pip
"\${INSTALL_PREFIX}/venv/bin/pip" install -r "\${INSTALL_PREFIX}/app/requirements.txt"

# Code security: compile .py → .pyc on device, then remove .py source files
echo "[matterhub-postinst] compiling Python bytecode and removing source files"
"\${INSTALL_PREFIX}/venv/bin/python" -m compileall -q -b "\${INSTALL_PREFIX}/app"
find "\${INSTALL_PREFIX}/app" -type f -name '*.py' ! -name '__init__.py' -delete
find "\${INSTALL_PREFIX}/app" -type d -name '__pycache__' -prune -exec rm -rf {} +
# Update launcher scripts to use .pyc extension
for launcher in "\${INSTALL_PREFIX}"/bin/matterhub-*; do
  [ -f "\$launcher" ] && sed -i 's/\.py"/\.pyc"/g' "\$launcher"
done

systemctl daemon-reload
systemctl enable matterhub-provision.service matterhub-api.service matterhub-mqtt.service matterhub-rule-engine.service matterhub-notifier.service matterhub-update-agent.service
if ! systemctl start matterhub-provision.service; then
  echo "[matterhub-postinst] automatic claim/support-tunnel bootstrap failed or was deferred; continuing install"
fi
if command -v ufw >/dev/null 2>&1; then
  ufw allow 8100/tcp || true
  ufw allow 8123/tcp || true
else
  echo "[matterhub-postinst] ufw not installed; skipping 8100/8123 allow rules"
fi
systemctl restart matterhub-api.service matterhub-mqtt.service matterhub-rule-engine.service matterhub-notifier.service matterhub-update-agent.service
EOF

cat > "${DEBIAN_DIR}/prerm" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
systemctl stop matterhub-provision.service matterhub-api.service matterhub-mqtt.service matterhub-rule-engine.service matterhub-notifier.service matterhub-support-tunnel.service matterhub-update-agent.service || true
EOF

cat > "${DEBIAN_DIR}/postrm" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
systemctl daemon-reload || true
EOF

chmod 755 "${DEBIAN_DIR}/postinst" "${DEBIAN_DIR}/prerm" "${DEBIAN_DIR}/postrm"

mkdir -p "$OUTPUT_DIR"
dpkg-deb --build "$PKG_ROOT" "$OUTPUT_DIR/$DEB_FILE" >/dev/null
log "deb built: ${OUTPUT_DIR}/${DEB_FILE}"
