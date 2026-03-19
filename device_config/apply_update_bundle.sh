#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROJECT_ROOT="${PROJECT_ROOT:-$DEFAULT_PROJECT_ROOT}"
BUNDLE_PATH=""
BACKUP_ROOT="${BACKUP_ROOT:-}"
HEALTHCHECK_CMD="${HEALTHCHECK_CMD:-}"
DRY_RUN=0
SKIP_RESTART=0

DEFAULT_SERVICES=(
  "matterhub-api.service"
  "matterhub-mqtt.service"
  "matterhub-rule-engine.service"
  "matterhub-notifier.service"
)
SERVICES=("${DEFAULT_SERVICES[@]}")

log() {
  printf '[matterhub-update] %s\n' "$*"
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
Usage: ./device_config/apply_update_bundle.sh --bundle <path> [options]

Options:
  --bundle <path>         Update bundle tar.gz path (required)
  --project-root <path>   Target project root (default: repo root)
  --backup-root <path>    Backup root directory (default: <project>/update/backup)
  --service <name>        Service to restart (repeatable)
  --skip-restart          Do not restart services
  --healthcheck-cmd <cmd> Command executed after apply/restart; non-zero triggers rollback
  --dry-run               Print planned commands only
  -h, --help              Show help

Bundle layout:
  - <bundle>/payload/... (recommended)
  - or top-level files/directories to overlay directly
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle)
      BUNDLE_PATH="$2"
      shift 2
      ;;
    --project-root)
      PROJECT_ROOT="$2"
      shift 2
      ;;
    --backup-root)
      BACKUP_ROOT="$2"
      shift 2
      ;;
    --service)
      SERVICES+=("$2")
      shift 2
      ;;
    --skip-restart)
      SKIP_RESTART=1
      shift
      ;;
    --healthcheck-cmd)
      HEALTHCHECK_CMD="$2"
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
if [ ! -d "$PROJECT_ROOT" ]; then
  echo "project root not found: $PROJECT_ROOT" >&2
  exit 1
fi
if [ -z "$BACKUP_ROOT" ]; then
  BACKUP_ROOT="$PROJECT_ROOT/update/backup"
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

EXTRACT_DIR="$TMP_DIR/extract"
mkdir -p "$EXTRACT_DIR"
if [ "$DRY_RUN" -eq 1 ]; then
  print_command "[dry-run]" tar -xzf "$BUNDLE_PATH" -C "$EXTRACT_DIR"
fi
tar -xzf "$BUNDLE_PATH" -C "$EXTRACT_DIR"

PAYLOAD_DIR=""
if [ -d "$EXTRACT_DIR/payload" ]; then
  PAYLOAD_DIR="$EXTRACT_DIR/payload"
else
  first_entry="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 | head -n 1 || true)"
  if [ -n "$first_entry" ] && [ -d "$first_entry/payload" ]; then
    PAYLOAD_DIR="$first_entry/payload"
  elif [ -n "$first_entry" ] && [ -d "$first_entry" ]; then
    PAYLOAD_DIR="$first_entry"
  fi
fi

if [ -z "$PAYLOAD_DIR" ] || [ ! -d "$PAYLOAD_DIR" ]; then
  echo "invalid bundle: payload directory not found" >&2
  exit 1
fi

FILES_LIST="$TMP_DIR/files.list"
(
  cd "$PAYLOAD_DIR"
  find . -type f -print | sed 's#^\./##'
) > "$FILES_LIST"

if [ ! -s "$FILES_LIST" ]; then
  echo "invalid bundle: payload has no files" >&2
  exit 1
fi

log "bundle=$BUNDLE_PATH"
log "project_root=$PROJECT_ROOT"
log "payload_dir=$PAYLOAD_DIR"
log "backup_dir=$BACKUP_DIR"

run_cmd mkdir -p "$BACKUP_DIR"

APPLIED_FILES=()
NEW_FILES=()
while IFS= read -r rel_path; do
  [ -z "$rel_path" ] && continue
  src="$PAYLOAD_DIR/$rel_path"
  dst="$PROJECT_ROOT/$rel_path"
  backup_target="$BACKUP_DIR/$rel_path"
  if [ -e "$dst" ]; then
    run_cmd mkdir -p "$(dirname "$backup_target")"
    run_cmd cp -a "$dst" "$backup_target"
  else
    NEW_FILES+=("$dst")
  fi
  run_cmd mkdir -p "$(dirname "$dst")"
  run_cmd cp -a "$src" "$dst"
  APPLIED_FILES+=("$dst")
done < "$FILES_LIST"

rollback() {
  log "rollback started"
  while IFS= read -r rel_path; do
    [ -z "$rel_path" ] && continue
    backup_target="$BACKUP_DIR/$rel_path"
    dst="$PROJECT_ROOT/$rel_path"
    if [ -e "$backup_target" ]; then
      run_cmd mkdir -p "$(dirname "$dst")"
      run_cmd cp -a "$backup_target" "$dst"
    fi
  done < "$FILES_LIST"
  for new_file in "${NEW_FILES[@]-}"; do
    if [ -n "$new_file" ]; then
      run_cmd rm -f "$new_file"
    fi
  done
  if [ "$SKIP_RESTART" -eq 0 ] && [ "${#SERVICES[@]}" -gt 0 ]; then
    sudo_cmd systemctl restart "${SERVICES[@]}"
  fi
  log "rollback completed"
}

if [ "$SKIP_RESTART" -eq 0 ] && [ "${#SERVICES[@]}" -gt 0 ]; then
  sudo_cmd systemctl daemon-reload
  sudo_cmd systemctl restart "${SERVICES[@]}"
fi

if [ -n "$HEALTHCHECK_CMD" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command "[dry-run] bash -lc" "$HEALTHCHECK_CMD"
  else
    if ! bash -lc "$HEALTHCHECK_CMD"; then
      log "healthcheck failed; triggering rollback"
      rollback
      exit 1
    fi
  fi
fi

log "update applied successfully"
exit 0
