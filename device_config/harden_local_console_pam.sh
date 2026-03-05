#!/usr/bin/env bash

set -euo pipefail

RUN_USER="${RUN_USER:-$(id -un)}"
PAM_LOGIN_PATH="${PAM_LOGIN_PATH:-/etc/pam.d/login}"
ACCESS_CONF_PATH="${ACCESS_CONF_PATH:-/etc/security/access.conf}"
DRY_RUN=0

MARKER_BEGIN="# MATTERHUB_LOCAL_CONSOLE_LOCK_BEGIN"
MARKER_END="# MATTERHUB_LOCAL_CONSOLE_LOCK_END"

log() {
  printf '[local-console-pam-hardening] %s\n' "$*"
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

sudo_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command "[dry-run] sudo" "$@"
    return 0
  fi
  sudo "$@"
}

usage() {
  cat <<'EOF'
Usage: ./device_config/harden_local_console_pam.sh [options]

Apply PAM rule to deny local-console login for the runtime account while keeping SSH relay workflow.

Options:
  --run-user <user>       Runtime user denied on local console (default: current user)
  --pam-login-path <path> PAM login file (default: /etc/pam.d/login)
  --access-conf <path>    access.conf path (default: /etc/security/access.conf)
  --dry-run               Show planned actions only
  -h, --help              Show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --pam-login-path)
      PAM_LOGIN_PATH="$2"
      shift 2
      ;;
    --access-conf)
      ACCESS_CONF_PATH="$2"
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

if [ -z "$RUN_USER" ]; then
  echo "RUN_USER cannot be empty." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

LOGIN_TMP="$TMP_DIR/login"
ACCESS_TMP="$TMP_DIR/access.conf"

if [ ! -f "$PAM_LOGIN_PATH" ]; then
  echo "PAM login file not found: $PAM_LOGIN_PATH" >&2
  exit 1
fi

cp "$PAM_LOGIN_PATH" "$LOGIN_TMP"
if grep -Eq '^[[:space:]]*account[[:space:]]+required[[:space:]]+pam_access\.so([[:space:]]|$)' "$LOGIN_TMP"; then
  log "pam_access is already enabled in ${PAM_LOGIN_PATH}"
elif grep -Eq '^[[:space:]]*#[[:space:]]*account[[:space:]]+required[[:space:]]+pam_access\.so([[:space:]]|$)' "$LOGIN_TMP"; then
  awk '
    {
      if ($0 ~ /^[[:space:]]*#[[:space:]]*account[[:space:]]+required[[:space:]]+pam_access\.so([[:space:]]|$)/) {
        sub(/^[[:space:]]*#[[:space:]]*/, "", $0)
      }
      print
    }
  ' "$LOGIN_TMP" > "$LOGIN_TMP.new"
  mv "$LOGIN_TMP.new" "$LOGIN_TMP"
  log "uncommented pam_access in ${PAM_LOGIN_PATH}"
else
  printf '\naccount required pam_access.so\n' >> "$LOGIN_TMP"
  log "appended pam_access entry to ${PAM_LOGIN_PATH}"
fi

if [ -f "$ACCESS_CONF_PATH" ]; then
  awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
    $0 == begin { skip=1; next }
    $0 == end { skip=0; next }
    !skip { print }
  ' "$ACCESS_CONF_PATH" > "$ACCESS_TMP"
else
  : > "$ACCESS_TMP"
fi

{
  echo ""
  echo "$MARKER_BEGIN"
  echo "+:root:LOCAL"
  echo "-:${RUN_USER}:LOCAL"
  echo "$MARKER_END"
} >> "$ACCESS_TMP"

log "installing PAM login policy: ${PAM_LOGIN_PATH}"
sudo_cmd install -m 0644 "$LOGIN_TMP" "$PAM_LOGIN_PATH"
log "installing access policy: ${ACCESS_CONF_PATH}"
sudo_cmd install -m 0644 "$ACCESS_TMP" "$ACCESS_CONF_PATH"

if [ "$DRY_RUN" -eq 1 ]; then
  log "pam login preview:"
  tail -n 12 "$LOGIN_TMP" | sed 's/^/[dry-run]   /'
  log "access.conf preview:"
  tail -n 12 "$ACCESS_TMP" | sed 's/^/[dry-run]   /'
fi

log "local console PAM hardening complete"
