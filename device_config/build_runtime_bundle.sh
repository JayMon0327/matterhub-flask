#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BINARY_DIST_DIR="${BINARY_DIST_DIR:-$PROJECT_ROOT/dist/bin}"
OUTPUT_BUNDLE="${OUTPUT_BUNDLE:-$PROJECT_ROOT/dist/matterhub-runtime-$(date +%Y%m%d-%H%M%S).tar.gz}"
DRY_RUN=0
INCLUDE_ENV=0

SERVICES=(
  "matterhub-api"
  "matterhub-mqtt"
  "matterhub-rule-engine"
  "matterhub-notifier"
  "matterhub-support-tunnel"
  "matterhub-update-agent"
)

OPTIONAL_DIRS=(
  "templates"
  "resources"
  "certificates"
  "konai_certificates"
)

log() {
  printf '[matterhub-runtime-bundle] %s\n' "$*"
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
Usage: ./device_config/build_runtime_bundle.sh [options]

Options:
  --binary-dist-dir <path>   Source binary dist directory (default: <project>/dist/bin)
  --output-bundle <path>     Output tar.gz path
  --include-env              Include project .env into payload/.env
  --dry-run                  Print planned commands only
  -h, --help                 Show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --binary-dist-dir)
      BINARY_DIST_DIR="$2"
      shift 2
      ;;
    --output-bundle)
      OUTPUT_BUNDLE="$2"
      shift 2
      ;;
    --include-env)
      INCLUDE_ENV=1
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
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ ! -d "$BINARY_DIST_DIR" ]; then
  echo "binary dist dir not found: $BINARY_DIST_DIR" >&2
  exit 1
fi

log "project_root=$PROJECT_ROOT"
log "binary_dist_dir=$BINARY_DIST_DIR"
log "output_bundle=$OUTPUT_BUNDLE"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

PAYLOAD_DIR="$TMP_DIR/payload"
run_cmd mkdir -p "$PAYLOAD_DIR/bin"

for service in "${SERVICES[@]}"; do
  src_dir="$BINARY_DIST_DIR/$service"
  if [ ! -d "$src_dir" ]; then
    echo "missing built service directory: $src_dir" >&2
    exit 1
  fi
  run_cmd cp -a "$src_dir" "$PAYLOAD_DIR/bin/"
done

for directory in "${OPTIONAL_DIRS[@]}"; do
  src_path="$PROJECT_ROOT/$directory"
  if [ -d "$src_path" ]; then
    run_cmd cp -a "$src_path" "$PAYLOAD_DIR/"
  fi
done

if [ "$INCLUDE_ENV" -eq 1 ]; then
  if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "--include-env requested but .env not found in project root" >&2
    exit 1
  fi
  run_cmd cp -a "$PROJECT_ROOT/.env" "$PAYLOAD_DIR/.env"
fi

MANIFEST_PATH="$TMP_DIR/manifest.json"
cat > "$MANIFEST_PATH" <<EOF
{
  "bundle_type": "matterhub-runtime",
  "created_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "services": ["matterhub-api", "matterhub-mqtt", "matterhub-rule-engine", "matterhub-notifier", "matterhub-support-tunnel", "matterhub-update-agent"],
  "binary_dist_dir": "$(printf '%s' "$BINARY_DIST_DIR" | sed 's/"/\\"/g')"
}
EOF

run_cmd mkdir -p "$(dirname "$OUTPUT_BUNDLE")"
run_cmd tar -czf "$OUTPUT_BUNDLE" -C "$TMP_DIR" payload manifest.json

log "runtime bundle created: $OUTPUT_BUNDLE"
