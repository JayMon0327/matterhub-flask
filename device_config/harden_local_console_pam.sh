#!/usr/bin/env bash

set -euo pipefail

RUN_USER="${RUN_USER:-$(id -un)}"
PAM_LOGIN_PATH="${PAM_LOGIN_PATH:-/etc/pam.d/login}"
GDM_PASSWORD_PAM_PATH="${GDM_PASSWORD_PAM_PATH:-/etc/pam.d/gdm-password}"
GDM_AUTOLOGIN_PAM_PATH="${GDM_AUTOLOGIN_PAM_PATH:-/etc/pam.d/gdm-autologin}"
ACCESS_CONF_PATH="${ACCESS_CONF_PATH:-/etc/security/access.conf}"
GDM_CUSTOM_CONF_PATH="${GDM_CUSTOM_CONF_PATH:-/etc/gdm3/custom.conf}"
LOCK_SCOPE="tty-only"
GDM_AUTOLOGIN_MODE="enable"
GDM_AUTOLOGIN_USER="${RUN_USER}"
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

Apply local-console access policy for runtime account.

Options:
  --run-user <user>       Runtime user denied on local console (default: current user)
  --pam-login-path <path> PAM login file (default: /etc/pam.d/login)
  --gdm-password-pam <path>
                         GDM password PAM file (default: /etc/pam.d/gdm-password)
  --gdm-autologin-pam <path>
                         GDM autologin PAM file (default: /etc/pam.d/gdm-autologin)
  --access-conf <path>    access.conf path (default: /etc/security/access.conf)
  --gdm-custom-conf <path>
                         GDM custom.conf path (default: /etc/gdm3/custom.conf)
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
    --gdm-password-pam)
      GDM_PASSWORD_PAM_PATH="$2"
      shift 2
      ;;
    --gdm-autologin-pam)
      GDM_AUTOLOGIN_PAM_PATH="$2"
      shift 2
      ;;
    --access-conf)
      ACCESS_CONF_PATH="$2"
      shift 2
      ;;
    --gdm-custom-conf)
      GDM_CUSTOM_CONF_PATH="$2"
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
GDM_AUTOLOGIN_USER="$RUN_USER"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

LOGIN_TMP="$TMP_DIR/login"
ACCESS_TMP="$TMP_DIR/access.conf"
GDM_PASSWORD_TMP="$TMP_DIR/gdm-password"
GDM_AUTOLOGIN_TMP="$TMP_DIR/gdm-autologin"
GDM_CUSTOM_TMP="$TMP_DIR/gdm-custom.conf"

if [ ! -f "$PAM_LOGIN_PATH" ]; then
  echo "PAM login file not found: $PAM_LOGIN_PATH" >&2
  exit 1
fi

ensure_pam_access_line() {
  local source_path="$1"
  local output_path="$2"

  cp "$source_path" "$output_path"
  if grep -Eq '^[[:space:]]*account[[:space:]]+required[[:space:]]+pam_access\.so([[:space:]]|$)' "$output_path"; then
    log "pam_access is already enabled in ${source_path}"
  elif grep -Eq '^[[:space:]]*#[[:space:]]*account[[:space:]]+required[[:space:]]+pam_access\.so([[:space:]]|$)' "$output_path"; then
    awk '
      {
        if ($0 ~ /^[[:space:]]*#[[:space:]]*account[[:space:]]+required[[:space:]]+pam_access\.so([[:space:]]|$)/) {
          sub(/^[[:space:]]*#[[:space:]]*/, "", $0)
        }
        print
      }
    ' "$output_path" > "$output_path.new"
    mv "$output_path.new" "$output_path"
    log "uncommented pam_access in ${source_path}"
  else
    printf '\naccount required pam_access.so\n' >> "$output_path"
    log "appended pam_access entry to ${source_path}"
  fi
}

render_gdm_custom_conf() {
  local source_path="$1"
  local output_path="$2"
  local mode="$3"
  local user="$4"

  if [ -f "$source_path" ]; then
    cp "$source_path" "$output_path"
  else
    : > "$output_path"
  fi

  awk -v mode="$mode" -v user="$user" '
    function emit_gdm_auth_lines() {
      if (!seen_autologin_enable) {
        if (mode == "enable") {
          print "AutomaticLoginEnable=true"
        } else {
          print "AutomaticLoginEnable=false"
        }
      }
      if (mode == "enable") {
        if (!seen_autologin_user) {
          print "AutomaticLogin=" user
        }
      } else {
        if (!seen_autologin_user) {
          print "#AutomaticLogin=disabled"
        }
      }
    }
    BEGIN {
      in_daemon = 0
      saw_daemon = 0
      seen_autologin_enable = 0
      seen_autologin_user = 0
    }
    /^\[.*\][[:space:]]*$/ {
      if (in_daemon) {
        emit_gdm_auth_lines()
      }
      in_daemon = 0
      section = $0
      if (section == "[daemon]") {
        saw_daemon = 1
        in_daemon = 1
        seen_autologin_enable = 0
        seen_autologin_user = 0
        print "[daemon]"
        next
      }
      print $0
      next
    }
    {
      if (!in_daemon) {
        print $0
        next
      }
      if ($0 ~ /^[[:space:]]*AutomaticLoginEnable=/) {
        if (mode == "enable") {
          print "AutomaticLoginEnable=true"
        } else {
          print "AutomaticLoginEnable=false"
        }
        seen_autologin_enable = 1
        next
      }
      if ($0 ~ /^[[:space:]]*AutomaticLogin=/ || $0 ~ /^[[:space:]]*#AutomaticLogin=disabled/) {
        if (mode == "enable") {
          print "AutomaticLogin=" user
        } else {
          print "#AutomaticLogin=disabled"
        }
        seen_autologin_user = 1
        next
      }
      print $0
    }
    END {
      if (in_daemon) {
        emit_gdm_auth_lines()
      }
      if (!saw_daemon) {
        print ""
        print "[daemon]"
        if (mode == "enable") {
          print "AutomaticLoginEnable=true"
          print "AutomaticLogin=" user
        } else {
          print "AutomaticLoginEnable=false"
          print "#AutomaticLogin=disabled"
        }
      }
    }
  ' "$output_path" > "$output_path.new"
  mv "$output_path.new" "$output_path"
}

ensure_pam_access_line "$PAM_LOGIN_PATH" "$LOGIN_TMP"
if [ "$LOCK_SCOPE" = "all" ]; then
  if [ -f "$GDM_PASSWORD_PAM_PATH" ]; then
    ensure_pam_access_line "$GDM_PASSWORD_PAM_PATH" "$GDM_PASSWORD_TMP"
  fi
  if [ -f "$GDM_AUTOLOGIN_PAM_PATH" ]; then
    ensure_pam_access_line "$GDM_AUTOLOGIN_PAM_PATH" "$GDM_AUTOLOGIN_TMP"
  fi
else
  log "lock-scope=tty-only, skipping GDM PAM lock files"
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

if [ "$GDM_AUTOLOGIN_MODE" != "keep" ]; then
  render_gdm_custom_conf "$GDM_CUSTOM_CONF_PATH" "$GDM_CUSTOM_TMP" "$GDM_AUTOLOGIN_MODE" "$GDM_AUTOLOGIN_USER"
fi

log "installing PAM login policy: ${PAM_LOGIN_PATH}"
sudo_cmd install -m 0644 "$LOGIN_TMP" "$PAM_LOGIN_PATH"
if [ "$LOCK_SCOPE" = "all" ] && [ -f "$GDM_PASSWORD_PAM_PATH" ]; then
  log "installing GDM password PAM policy: ${GDM_PASSWORD_PAM_PATH}"
  sudo_cmd install -m 0644 "$GDM_PASSWORD_TMP" "$GDM_PASSWORD_PAM_PATH"
fi
if [ "$LOCK_SCOPE" = "all" ] && [ -f "$GDM_AUTOLOGIN_PAM_PATH" ]; then
  log "installing GDM autologin PAM policy: ${GDM_AUTOLOGIN_PAM_PATH}"
  sudo_cmd install -m 0644 "$GDM_AUTOLOGIN_TMP" "$GDM_AUTOLOGIN_PAM_PATH"
fi
log "installing access policy: ${ACCESS_CONF_PATH}"
sudo_cmd install -m 0644 "$ACCESS_TMP" "$ACCESS_CONF_PATH"

if [ "$GDM_AUTOLOGIN_MODE" != "keep" ]; then
  log "installing GDM custom policy: ${GDM_CUSTOM_CONF_PATH}"
  sudo_cmd install -m 0644 "$GDM_CUSTOM_TMP" "$GDM_CUSTOM_CONF_PATH"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "pam login preview:"
  tail -n 12 "$LOGIN_TMP" | sed 's/^/[dry-run]   /'
  log "access.conf preview:"
  tail -n 12 "$ACCESS_TMP" | sed 's/^/[dry-run]   /'
  if [ "$GDM_AUTOLOGIN_MODE" != "keep" ]; then
    log "gdm custom.conf preview:"
    tail -n 12 "$GDM_CUSTOM_TMP" | sed 's/^/[dry-run]   /'
  fi
fi

log "local console PAM hardening complete"
