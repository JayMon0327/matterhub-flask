#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST=""
USER_NAME="${USER_NAME:-whatsmatter}"
SSH_PORT="${SSH_PORT:-22}"
ARTIFACT_PATH=""
REMOTE_DIR="${REMOTE_DIR:-/tmp/matterhub-deploy}"
SKIP_APT_UPDATE=0
DRY_RUN=0
SSH_PASSWORD_ENV="${SSH_PASSWORD_ENV:-SSH_PASSWORD}"
SUDO_PASSWORD_ENV="${SUDO_PASSWORD_ENV:-SUDO_PASSWORD}"

log() {
  printf '[matterhub-deploy] %s\n' "$*"
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

usage() {
  cat <<'EOF'
Usage: ./device_config/deploy_matterhub_deb.sh --host <ip-or-hostname> [options]

Options:
  --host <host>              Target Raspberry Pi host or IP (required)
  --user <user>              SSH user (default: whatsmatter)
  --port <port>              SSH port (default: 22)
  --artifact <path>          Local .deb artifact path (default: latest dist/matterhub_*_arm64.deb)
  --remote-dir <path>        Remote upload directory (default: /tmp/matterhub-deploy)
  --skip-apt-update          Skip remote apt-get update before install
  --ssh-password-env <name>  Env var name containing SSH login password
  --sudo-password-env <name> Env var name containing sudo password
  --dry-run                  Print planned commands only
  -h, --help                 Show help

Behavior:
  - git pull is not required on the Raspberry Pi.
  - The local .deb artifact is copied to the device and installed remotely.
  - If password env vars are set, scp/ssh/sudo can be automated via expect.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --user)
      USER_NAME="$2"
      shift 2
      ;;
    --port)
      SSH_PORT="$2"
      shift 2
      ;;
    --artifact)
      ARTIFACT_PATH="$2"
      shift 2
      ;;
    --remote-dir)
      REMOTE_DIR="$2"
      shift 2
      ;;
    --skip-apt-update)
      SKIP_APT_UPDATE=1
      shift
      ;;
    --ssh-password-env)
      SSH_PASSWORD_ENV="$2"
      shift 2
      ;;
    --sudo-password-env)
      SUDO_PASSWORD_ENV="$2"
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

if [ -z "$HOST" ]; then
  echo "--host is required" >&2
  exit 1
fi

if [ -z "$ARTIFACT_PATH" ]; then
  ARTIFACT_PATH="$(ls -1t "$PROJECT_ROOT"/dist/matterhub_*_arm64.deb 2>/dev/null | head -n 1 || true)"
fi

if [ -z "$ARTIFACT_PATH" ]; then
  echo "No .deb artifact found. Pass --artifact explicitly." >&2
  exit 1
fi

if [ ! -f "$ARTIFACT_PATH" ]; then
  echo "Artifact not found: $ARTIFACT_PATH" >&2
  exit 1
fi

SSH_PASSWORD="${!SSH_PASSWORD_ENV:-}"
SUDO_PASSWORD="${!SUDO_PASSWORD_ENV:-$SSH_PASSWORD}"

REMOTE_ARTIFACT="${REMOTE_DIR}/$(basename "$ARTIFACT_PATH")"

SCP_BASE=(scp -o StrictHostKeyChecking=no -P "$SSH_PORT")
SSH_BASE=(ssh -o StrictHostKeyChecking=no -p "$SSH_PORT" "${USER_NAME}@${HOST}")

REMOTE_PREPARE_CMD="mkdir -p '$REMOTE_DIR'"
REMOTE_INSTALL_CMD="sudo mkdir -p '$REMOTE_DIR' && sudo chown '${USER_NAME}:${USER_NAME}' '$REMOTE_DIR'"
if [ "$SKIP_APT_UPDATE" -eq 0 ]; then
  REMOTE_INSTALL_CMD="$REMOTE_INSTALL_CMD && sudo apt-get update"
fi
REMOTE_INSTALL_CMD="$REMOTE_INSTALL_CMD && sudo apt-get install -o Dpkg::Options::=\"--force-confnew\" -y '$REMOTE_ARTIFACT'"

run_expect() {
  local ssh_password="$1"
  local sudo_password="$2"
  shift 2
  EXPECT_SSH_PASSWORD="$ssh_password" EXPECT_SUDO_PASSWORD="$sudo_password" expect -f - "$@" <<'EOF'
set timeout -1
set ssh_password $env(EXPECT_SSH_PASSWORD)
set sudo_password $env(EXPECT_SUDO_PASSWORD)
set password_count 0
spawn {*}$argv
expect {
  -re "(?i)are you sure you want to continue connecting.*" {
    send "yes\r"
    exp_continue
  }
  -re "(?i)(?:password|passphrase).*:" {
    if {$password_count == 0} {
      send "$ssh_password\r"
    } else {
      send "$sudo_password\r"
    }
    incr password_count
    exp_continue
  }
  eof {
    catch wait result
    set exit_code [lindex $result 3]
    exit $exit_code
  }
}
EOF
}

run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command "[dry-run]" "$@"
    return 0
  fi
  if [ -n "$SSH_PASSWORD" ]; then
    run_expect "$SSH_PASSWORD" "$SUDO_PASSWORD" "$@"
  else
    "$@"
  fi
}

log "host=$HOST"
log "user=$USER_NAME"
log "artifact=$ARTIFACT_PATH"
log "remote_artifact=$REMOTE_ARTIFACT"
if [ "$SKIP_APT_UPDATE" -eq 1 ]; then
  log "remote apt-get update skipped"
fi
if [ -n "$SSH_PASSWORD" ]; then
  log "password-assisted deploy enabled via env vars"
else
  log "interactive/key-based deploy mode"
fi

run_cmd "${SSH_BASE[@]}" "$REMOTE_PREPARE_CMD"
run_cmd "${SCP_BASE[@]}" "$ARTIFACT_PATH" "${USER_NAME}@${HOST}:${REMOTE_ARTIFACT}"
run_cmd "${SSH_BASE[@]}" -tt "$REMOTE_INSTALL_CMD"

log "remote install completed"
