#!/usr/bin/env bash

set -euo pipefail

COUNTRY_CODE="${COUNTRY_CODE:-KR}"
BOOT_CMDLINE_PATH="${BOOT_CMDLINE_PATH:-/boot/firmware/cmdline.txt}"
DRY_RUN=0

log() {
  printf '[matterhub-wifi-regdom] %s\n' "$*"
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
Usage: ./device_config/setup_wifi_regulatory_domain.sh [options]

Options:
  --country-code <code>   Regulatory domain / Wi-Fi country code (default: KR)
  --boot-cmdline <path>   Kernel cmdline path (default: /boot/firmware/cmdline.txt)
  --dry-run               Print actions only
  -h, --help              Show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --country-code)
      COUNTRY_CODE="$2"
      shift 2
      ;;
    --boot-cmdline)
      BOOT_CMDLINE_PATH="$2"
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

COUNTRY_CODE="$(printf '%s' "$COUNTRY_CODE" | tr '[:lower:]' '[:upper:]')"
if ! [[ "$COUNTRY_CODE" =~ ^[A-Z]{2}$ ]]; then
  echo "country code must be a 2-letter ISO code" >&2
  exit 1
fi

log "country_code=$COUNTRY_CODE"
log "boot_cmdline=$BOOT_CMDLINE_PATH"

if [ "$DRY_RUN" -eq 1 ]; then
  log "kernel cmdline에 cfg80211.ieee80211_regdom=$COUNTRY_CODE 반영 예정"
else
  if [ ! -f "$BOOT_CMDLINE_PATH" ]; then
    echo "boot cmdline not found: $BOOT_CMDLINE_PATH" >&2
    exit 1
  fi
  tmp_file="$(mktemp)"
  awk -v code="$COUNTRY_CODE" '
    {
      line=$0
      gsub(/cfg80211\.ieee80211_regdom=[^ ]+/, "", line)
      gsub(/[[:space:]]+/, " ", line)
      sub(/^ /, "", line)
      sub(/ $/, "", line)
      if (line != "") {
        print line " cfg80211.ieee80211_regdom=" code
      } else {
        print "cfg80211.ieee80211_regdom=" code
      }
    }
  ' "$BOOT_CMDLINE_PATH" > "$tmp_file"
  sudo_cmd install -m 0644 "$tmp_file" "$BOOT_CMDLINE_PATH"
  rm -f "$tmp_file"
fi

if command -v iw >/dev/null 2>&1; then
  log "즉시 regdom 적용 시도"
  sudo_cmd iw reg set "$COUNTRY_CODE"
else
  log "iw 미설치: 즉시 regdom 적용 단계 생략"
fi

log "NetworkManager 재시작"
sudo_cmd systemctl restart NetworkManager
log "재부팅 후 iw reg get 으로 country code 적용 여부 확인 필요"
