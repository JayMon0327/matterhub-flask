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

OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/dist/bin}"
BUILD_ROOT="${BUILD_ROOT:-$PROJECT_ROOT/dist/build}"
DRY_RUN=0

ALL_SERVICES=(
  "matterhub-api:app.py"
  "matterhub-mqtt:mqtt.py"
  "matterhub-rule-engine:sub/ruleEngine.py"
  "matterhub-notifier:sub/notifier.py"
  "matterhub-support-tunnel:support_tunnel.py"
  "matterhub-update-agent:update_agent.py"
)
SELECTED_SERVICES=()

log() {
  printf '[matterhub-binary-build] %s\n' "$*"
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

usage() {
  cat <<'EOF'
Usage: ./device_config/build_runtime_binaries.sh [options]

Options:
  --service <name>      Build specific service only (repeatable)
  --output-dir <path>   PyInstaller distpath (default: <project>/dist/bin)
  --build-root <path>   Work/spec root (default: <project>/dist/build)
  --python-bin <path>   Python executable used for PyInstaller
  --dry-run             Print planned commands only
  -h, --help            Show help

Service names:
  matterhub-api
  matterhub-mqtt
  matterhub-rule-engine
  matterhub-notifier
  matterhub-support-tunnel
  matterhub-update-agent
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --service)
      SELECTED_SERVICES+=("$2")
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --build-root)
      BUILD_ROOT="$2"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="$2"
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

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

service_entrypoint() {
  local service_name="$1"
  for item in "${ALL_SERVICES[@]}"; do
    local name="${item%%:*}"
    local entry="${item#*:}"
    if [ "$name" = "$service_name" ]; then
      printf '%s' "$entry"
      return 0
    fi
  done
  return 1
}

TARGET_SERVICES=()
if [ "${#SELECTED_SERVICES[@]}" -eq 0 ]; then
  for item in "${ALL_SERVICES[@]}"; do
    TARGET_SERVICES+=("${item%%:*}")
  done
else
  for name in "${SELECTED_SERVICES[@]}"; do
    if ! service_entrypoint "$name" >/dev/null; then
      echo "unknown service: $name" >&2
      exit 1
    fi
    TARGET_SERVICES+=("$name")
  done
fi

log "project_root=$PROJECT_ROOT"
log "python_bin=$PYTHON_BIN"
log "output_dir=$OUTPUT_DIR"
log "build_root=$BUILD_ROOT"
log "targets=${TARGET_SERVICES[*]}"

run_cmd mkdir -p "$OUTPUT_DIR" "$BUILD_ROOT/work" "$BUILD_ROOT/spec"

for service_name in "${TARGET_SERVICES[@]}"; do
  entry="$(service_entrypoint "$service_name")"
  entry_path="$PROJECT_ROOT/$entry"
  if [ ! -f "$entry_path" ]; then
    echo "entrypoint not found for ${service_name}: ${entry_path}" >&2
    exit 1
  fi

  build_cmd=(
    "$PYTHON_BIN" -m PyInstaller
    --noconfirm
    --clean
    --onedir
    --name "$service_name"
    --distpath "$OUTPUT_DIR"
    --workpath "$BUILD_ROOT/work"
    --specpath "$BUILD_ROOT/spec"
    "$entry_path"
  )
  run_cmd "${build_cmd[@]}"
done

log "binary build completed"
