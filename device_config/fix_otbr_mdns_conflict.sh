#!/usr/bin/env bash
# fix_otbr_mdns_conflict.sh
# OTBR 활성 시 WiFi Matter 기기 커미셔닝 실패 수정
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 장애 현상:
#   Thread dongle(OTBR)이 활성화된 상태에서 WiFi Matter 기기 커미셔닝이
#   마지막 단계(PASE handshake)에서 타임아웃으로 실패.
#   dongle을 뽑으면 정상 커미셔닝됨.
#
# 근본 원인 (2가지 복합):
#
#   [원인 1] link-local 라우팅 충돌 — PASE 패킷이 wpan0으로 빠짐
#     fe80::/64 가 wpan0, wlan0, docker0, veth 4개 인터페이스에 동일 metric으로 존재.
#     matter-server(CHIP SDK)가 primary-interface 미지정(None) 상태에서
#     WiFi 기기의 link-local 주소(fe80::...)로 PASE 패킷 전송 시,
#     커널이 wpan0(첫 번째 매칭)으로 라우팅 → WiFi 기기에 도달 불가 → 타임아웃.
#     로그: "PASESession timed out while waiting for a response from the peer.
#            Expected message type was 33"
#            "Using 'None' as primary interface (for link-local addresses)"
#
#   [원인 2] mDNS 포트 5353 3중 경쟁
#     avahi-daemon, otbr-agent, matter-server 3개가 모두 UDP 5353에 바인딩.
#     matter-server가 network_mode=host로 wpan0 Thread 주소에도 mDNS 소켓 바인딩.
#     OTBR mDNS 프록시와 avahi-daemon이 경쟁하여 mDNS 해석 지연/충돌.
#
# 수정 내용:
#   1) docker-compose: matter-server에 --primary-interface wlan0 추가
#      → CHIP SDK가 link-local 통신에 wlan0 사용 (핵심 수정)
#   2) avahi-daemon: wpan0 인터페이스 제외 (deny-interfaces)
#   3) ip6tables: wpan0에서 mDNS(UDP 5353) 차단
#   4) 규칙 영구화 (netfilter-persistent)
#
# 사용법:
#   # 원격 실행
#   sshpass -p 'whatsmatter' ssh whatsmatter@<IP> 'bash -s' < fix_otbr_mdns_conflict.sh
#   # Pi에서 직접 실행
#   bash fix_otbr_mdns_conflict.sh
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[FIX]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*" >&2; }

COMPOSE_DIR="${COMPOSE_DIR:-/home/whatsmatter/matterhub-install}"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"
PRIMARY_IFACE="wlan0"

# ── Pre-check ──────────────────────────────────────────────
log "=== OTBR mDNS 충돌 + link-local 라우팅 수정 스크립트 ==="

if ! ip link show wpan0 &>/dev/null; then
    warn "wpan0 인터페이스 없음 – OTBR/Thread dongle 미연결. 수정 불필요할 수 있음."
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    err "docker-compose.yml을 찾을 수 없음: $COMPOSE_FILE"
    err "COMPOSE_DIR 환경변수로 경로를 지정하세요."
    exit 1
fi

# ── 1. matter-server --primary-interface wlan0 (핵심) ──────
log "1/4  matter-server: --primary-interface $PRIMARY_IFACE 설정"

if grep -q "primary-interface" "$COMPOSE_FILE" 2>/dev/null; then
    log "  → 이미 --primary-interface 설정됨. 스킵."
else
    # command: 줄 끝에 --primary-interface wlan0 추가
    sudo sed -i "s|--log-level info|--log-level info --primary-interface ${PRIMARY_IFACE}|" "$COMPOSE_FILE"
    if grep -q "primary-interface" "$COMPOSE_FILE"; then
        log "  → --primary-interface $PRIMARY_IFACE 추가 완료"
    else
        err "  → docker-compose.yml 수정 실패. 수동으로 추가하세요:"
        err "    command: ... --primary-interface $PRIMARY_IFACE"
        exit 1
    fi
fi

# ── 2. avahi-daemon 인터페이스 제한 ──────────────────────────
log "2/4  avahi-daemon 설정: wpan0 인터페이스 제외"

if grep -q "^deny-interfaces=" "$AVAHI_CONF" 2>/dev/null; then
    if grep -q "wpan0" "$AVAHI_CONF"; then
        log "  → 이미 wpan0 deny 설정됨. 스킵."
    else
        sudo sed -i 's/^deny-interfaces=.*/&,wpan0/' "$AVAHI_CONF"
        log "  → 기존 deny-interfaces에 wpan0 추가"
    fi
elif grep -q "^#deny-interfaces=" "$AVAHI_CONF" 2>/dev/null; then
    sudo sed -i 's/^#deny-interfaces=.*/deny-interfaces=wpan0/' "$AVAHI_CONF"
    log "  → deny-interfaces=wpan0 활성화"
else
    sudo sed -i '/^\[server\]/a deny-interfaces=wpan0' "$AVAHI_CONF"
    log "  → deny-interfaces=wpan0 신규 추가"
fi

sudo systemctl restart avahi-daemon
log "  → avahi-daemon 재시작 완료"

# ── 3. ip6tables: wpan0에서 mDNS 차단 ──────────────────────
log "3/4  ip6tables: wpan0 mDNS(UDP 5353) 차단"

if sudo ip6tables -C INPUT -i wpan0 -p udp --dport 5353 -j DROP 2>/dev/null; then
    log "  → INPUT DROP 룰 이미 존재. 스킵."
else
    sudo ip6tables -I INPUT -i wpan0 -p udp --dport 5353 -j DROP
    log "  → INPUT -i wpan0 -p udp --dport 5353 -j DROP 추가"
fi

if sudo ip6tables -C OUTPUT -o wpan0 -p udp --dport 5353 -j DROP 2>/dev/null; then
    log "  → OUTPUT DROP 룰 이미 존재. 스킵."
else
    sudo ip6tables -I OUTPUT -o wpan0 -p udp --dport 5353 -j DROP
    log "  → OUTPUT -o wpan0 -p udp --dport 5353 -j DROP 추가"
fi

# ── 4. ip6tables 영구화 ──────────────────────────────────────
log "4/4  ip6tables 규칙 영구화"

if ! command -v netfilter-persistent &>/dev/null; then
    log "  → iptables-persistent 설치 중..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent 2>&1 | tail -1
fi

if command -v netfilter-persistent &>/dev/null; then
    sudo netfilter-persistent save
    log "  → netfilter-persistent save 완료"
elif [ -d /etc/iptables ]; then
    sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 > /dev/null
    log "  → /etc/iptables/rules.v6 저장 완료"
else
    warn "  → 영구화 실패. 재부팅 시 ip6tables 룰이 초기화될 수 있음."
fi

# ── 5. matter-server 재시작 (docker compose) ─────────────────
log "matter-server 재시작 (docker compose recreate)"

if docker ps --format '{{.Names}}' | grep -q matter-server; then
    cd "$COMPOSE_DIR"
    docker compose up -d matter-server 2>&1 | grep -v "^$"
    sleep 8
    log "  → matter-server 컨테이너 재생성 완료"
else
    warn "  → matter-server 컨테이너 미실행. 수동으로 시작하세요:"
    warn "    cd $COMPOSE_DIR && docker compose up -d matter-server"
fi

# ── 검증 ──────────────────────────────────────────────────
log ""
log "=== 검증 ==="

echo ""
log "[핵심] matter-server primary interface:"
if docker logs matter-server --tail 30 2>&1 | grep -q "Using 'wlan0'"; then
    docker logs matter-server --tail 30 2>&1 | grep "primary interface"
    log "  → wlan0 설정 확인됨"
else
    warn "  → 'wlan0' primary interface 로그를 찾을 수 없음. 수동 확인:"
    warn "    docker logs matter-server 2>&1 | grep 'primary interface'"
fi

echo ""
log "avahi-daemon deny-interfaces:"
grep -E "^deny-interfaces" "$AVAHI_CONF" || warn "  설정 없음"

echo ""
log "ip6tables wpan0 mDNS 차단 룰:"
sudo ip6tables -L INPUT -n -v 2>&1 | grep -E "5353.*wpan0|wpan0.*5353" || warn "  INPUT 룰 없음"
sudo ip6tables -L OUTPUT -n -v 2>&1 | grep -E "5353.*wpan0|wpan0.*5353" || warn "  OUTPUT 룰 없음"

echo ""
log "기존 Matter 노드 연결 상태:"
NODES_OK=$(docker logs matter-server --tail 30 2>&1 | grep -c "Subscription succeeded" || true)
log "  → ${NODES_OK}개 노드 subscription 성공"

echo ""
log "=== 수정 완료 ==="
log ""
log "WiFi Matter 기기 커미셔닝을 시도하세요."
log "문제 발생 시 로그 확인:"
log "  docker logs matter-server --tail 50"
log "  sudo tcpdump -i wlan0 port 5353 -n -c 50"
