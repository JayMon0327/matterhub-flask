#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="${RUN_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/venv}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
DRY_RUN=0
SKIP_OS_PACKAGES=0

log() {
  printf '[matterhub-install] %s\n' "$*"
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
Usage: ./device_config/install_ubuntu24.sh [--dry-run] [--skip-os-packages]

Options:
  --dry-run           Print the actions without executing sudo/systemctl/pip commands.
  --skip-os-packages  Skip apt update/install steps.

Environment variables:
  RUN_USER     systemd service user (default: current shell user)
  PYTHON_BIN   python executable used to create the venv (default: python3)
  VENV_DIR     virtualenv path (default: <project>/venv)
  SYSTEMD_DIR  target systemd unit directory (default: /etc/systemd/system)
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --skip-os-packages)
      SKIP_OS_PACKAGES=1
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
  shift
done

if [ "$DRY_RUN" -ne 1 ] && [ "$(uname -s)" != "Linux" ]; then
  echo "This installer must be executed on Ubuntu/Linux. Use --dry-run for planning on macOS." >&2
  exit 1
fi

SERVICE_UNITS=()
while IFS= read -r unit_name; do
  if [ -n "$unit_name" ]; then
    SERVICE_UNITS+=("$unit_name")
  fi
done <<EOF
$("$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" --list-unit-names)
EOF

if [ "${#SERVICE_UNITS[@]}" -eq 0 ]; then
  echo "No systemd service units were discovered." >&2
  exit 1
fi

log "프로젝트 루트: $PROJECT_ROOT"
log "서비스 실행 사용자: $RUN_USER"
log "설치 대상 systemd 디렉토리: $SYSTEMD_DIR"
log "대상 서비스: ${SERVICE_UNITS[*]}"

if [ "$SKIP_OS_PACKAGES" -eq 0 ]; then
  log "Ubuntu 필수 패키지 설치"
  sudo_cmd apt update
  sudo_cmd apt install -y python3-venv python3-pip network-manager
else
  log "OS 패키지 설치 단계 생략"
fi

if [ ! -d "$VENV_DIR" ]; then
  log "가상환경 생성: $VENV_DIR"
  run_cmd "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  log "가상환경 재사용: $VENV_DIR"
fi

log "Python 패키지 설치/업데이트"
run_cmd "$VENV_DIR/bin/pip" install --upgrade pip
run_cmd "$VENV_DIR/bin/pip" install -r "$PROJECT_ROOT/requirements.txt"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

log "systemd 유닛 렌더링"
if [ "$DRY_RUN" -eq 1 ]; then
  print_command "[dry-run]" "$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" \
    --project-root "$PROJECT_ROOT" \
    --run-user "$RUN_USER" \
    --output-dir "$TMP_DIR"
else
  "$PYTHON_BIN" "$SCRIPT_DIR/render_systemd_units.py" \
    --project-root "$PROJECT_ROOT" \
    --run-user "$RUN_USER" \
    --output-dir "$TMP_DIR"
fi

for unit_name in "${SERVICE_UNITS[@]}"; do
  sudo_cmd install -m 0644 "$TMP_DIR/$unit_name" "$SYSTEMD_DIR/$unit_name"
done

log "systemd reload/enable/restart"
sudo_cmd systemctl daemon-reload
sudo_cmd systemctl enable "${SERVICE_UNITS[@]}"
sudo_cmd systemctl restart "${SERVICE_UNITS[@]}"

if [ "$DRY_RUN" -eq 0 ]; then
  sudo systemctl --no-pager --full status "${SERVICE_UNITS[@]}" || true
fi

log "설치 완료"
