#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
if [ -x "$DEFAULT_PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

BUNDLE_PATH=""
RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/matterhub}"
RUN_USER="${RUN_USER:-$(id -un)}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
DRY_RUN=0

log() {
  printf '[matterhub-runtime-install] %s\n' "$*"
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
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

usage() {
  cat <<'EOF'
Usage: ./device_config/install_runtime_bundle.sh --bundle <path> [options]

Options:
  --bundle <path>       Runtime bundle tar.gz path (required)
  --runtime-root <path> Runtime installation root (default: /opt/matterhub)
  --run-user <user>     Service runtime user for non-root units
  --python-bin <path>   Python executable used to render units
  --systemd-dir <path>  systemd unit directory (default: /etc/systemd/system)
  --dry-run             Print planned commands only
  -h, --help            Show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle)
      BUNDLE_PATH="$2"
      shift 2
      ;;
    --runtime-root)
      RUNTIME_ROOT="$2"
      shift 2
      ;;
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --systemd-dir)
      SYSTEMD_DIR="$2"
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

if [ -z "$BUNDLE_PATH" ]; then
  echo "--bundle is required" >&2
  exit 1
fi
if [ ! -f "$BUNDLE_PATH" ]; then
  echo "bundle not found: $BUNDLE_PATH" >&2
  exit 1
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

log "bundle=$BUNDLE_PATH"
log "runtime_root=$RUNTIME_ROOT"
log "run_user=$RUN_USER"

sudo_cmd mkdir -p "$RUNTIME_ROOT"
sudo_cmd chown -R "$RUN_USER":"$RUN_USER" "$RUNTIME_ROOT"

APPLY_SCRIPT="$SCRIPT_DIR/apply_update_bundle.sh"
if [ ! -f "$APPLY_SCRIPT" ]; then
  echo "apply_update_bundle.sh not found: $APPLY_SCRIPT" >&2
  exit 1
fi

run_cmd bash "$APPLY_SCRIPT" \
  --bundle "$BUNDLE_PATH" \
  --project-root "$RUNTIME_ROOT" \
  --skip-restart

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

RENDER_SCRIPT="$SCRIPT_DIR/render_systemd_units.py"
if [ ! -f "$RENDER_SCRIPT" ]; then
  echo "render_systemd_units.py not found: $RENDER_SCRIPT" >&2
  exit 1
fi

run_cmd "$PYTHON_BIN" "$RENDER_SCRIPT" \
  --project-root "$RUNTIME_ROOT" \
  --run-user "$RUN_USER" \
  --runtime-mode binary \
  --output-dir "$TMP_DIR"

ENABLED_UNITS_RAW="$("$PYTHON_BIN" "$RENDER_SCRIPT" --list-enabled-unit-names)"
ENABLED_UNITS=()
while IFS= read -r unit_name; do
  if [ -n "$unit_name" ]; then
    ENABLED_UNITS+=("$unit_name")
  fi
done <<EOF
$ENABLED_UNITS_RAW
EOF

for unit_file in "$TMP_DIR"/*.service; do
  [ -f "$unit_file" ] || continue
  sudo_cmd install -m 0644 "$unit_file" "$SYSTEMD_DIR/$(basename "$unit_file")"
done

sudo_cmd systemctl daemon-reload
if [ "${#ENABLED_UNITS[@]}" -gt 0 ]; then
  sudo_cmd systemctl enable "${ENABLED_UNITS[@]}"
  sudo_cmd systemctl restart "${ENABLED_UNITS[@]}"
fi

log "runtime bundle installation completed"

