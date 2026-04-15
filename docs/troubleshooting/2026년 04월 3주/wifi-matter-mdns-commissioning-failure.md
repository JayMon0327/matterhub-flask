# WiFi Matter 기기 커미셔닝 실패 — mDNS Resolution Timeout

- **날짜**: 2026-04-15
- **장비**: 1호기 (192.168.1.94)
- **브랜치**: konai/20260211-v1.1
- **심각도**: Critical (WiFi Matter 기기 신규 등록 불가)
- **상태**: 해결 완료

---

## 증상

HA UI에서 WiFi Matter 기기 추가 시 마지막 단계 "기기를 HomeAssistant에 연결중..."에서 무한 대기 후 "문제가 발생했습니다" 표시.

Thread 기기는 정상 연결됨. WiFi 기기만 실패.

### matter-server 로그

```
Starting Matter commissioning using Node ID 24 and IP fe80::8e87:d0ff:fe09:1022%wlan0.
Established secure session with Device              <- PASE 성공
Timeout waiting for mDNS resolution.                <- Operational Discovery 실패
OperationalSessionSetup[1:0000000000000018]: operational discovery failed
Error on commissioning step 'kFindOperationalForStayActive'
Failed to commission: CHIP Error 0x00000032: Timeout
```

**핵심**: PASE 핸드셰이크는 성공하지만, 커미셔닝 마지막 단계인 Operational Discovery(mDNS로 기기의 operational 주소 탐색)에서 타임아웃.

---

## 근본 원인

### 원인 1: link-local 라우팅 충돌

fe80::/64가 wpan0, wlan0, docker0, veth 4개 인터페이스에 동일 metric으로 존재.
matter-server의 `--primary-interface`가 미설정(None) 상태면 CHIP SDK가 PASE/mDNS 패킷을 wpan0(첫 매칭)으로 라우팅 -> WiFi 기기에 도달 불가.

### 원인 2: mDNS 포트 5353 다중 프로세스 경쟁

UDP 5353에 5개 프로세스가 동시 바인딩:

| 프로세스 | 소켓 수 | 바인딩 범위 |
|---------|--------|-----------|
| avahi-daemon | 2 | 0.0.0.0 + [::] |
| otbr-agent | 2 | wlan0 한정 |
| matter-server | ~15 | 각 인터페이스별 |
| openclaw-gateway | 3 | 0.0.0.0 (와일드카드) |
| python3 (MatterHub) | 3 | 192.168.1.94 + 특정 IPv6 |

avahi-daemon이 wpan0에서도 mDNS 처리하면 otbr-agent와 경쟁하여 해석 지연.

### 원인 3: ip6tables 규칙 재부팅 시 소실

`fix_otbr_mdns_conflict.sh`로 적용한 ip6tables wpan0 5353 DROP 규칙이 재부팅 후 사라짐.

`netfilter-persistent`가 enabled이고 `/etc/iptables/rules.v6`에 저장되어 있었지만, **Docker/OTBR가 부팅 시 ip6tables 체인을 flush & recreate**하면서 커스텀 규칙 소실.

부팅 순서:
1. `netfilter-persistent` -> 저장된 규칙 로드 (DROP 포함)
2. `docker.service` -> ip6tables 체인 재생성 (DOCKER-* 등)
3. `otbr-agent.service` -> OTBR_FORWARD_INGRESS 체인 재생성
4. 결과: INPUT/OUTPUT의 커스텀 DROP 규칙 소실

---

## 해결 방법

### 필수 설정 3가지

| 설정 | 방법 | 영구화 위치 |
|------|------|-----------|
| `--primary-interface wlan0` | docker-compose.yml command에 추가 | `/home/whatsmatter/matterhub-install/docker-compose.yml` |
| `avahi deny-interfaces=wpan0` | avahi-daemon.conf [server] 섹션에 추가 | `/etc/avahi/avahi-daemon.conf` |
| ip6tables wpan0 5353 DROP | systemd 서비스로 부팅 시 자동 적용 | `/etc/systemd/system/matterhub-ip6tables-mdns.service` |

### systemd 서비스 (ip6tables 영구화)

`netfilter-persistent`만으로는 Docker/OTBR flush에 의해 무효화됨. Docker/OTBR 이후에 실행되는 전용 서비스 필요:

**파일**: `/etc/systemd/system/matterhub-ip6tables-mdns.service`

```ini
[Unit]
Description=MatterHub mDNS ip6tables rules (wpan0 UDP 5353 DROP)
After=docker.service otbr-agent.service netfilter-persistent.service
Wants=docker.service otbr-agent.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sleep 5
ExecStart=/bin/bash -c '\
  ip6tables -C INPUT -i wpan0 -p udp --dport 5353 -j DROP 2>/dev/null || ip6tables -I INPUT -i wpan0 -p udp --dport 5353 -j DROP; \
  ip6tables -C OUTPUT -o wpan0 -p udp --dport 5353 -j DROP 2>/dev/null || ip6tables -I OUTPUT -o wpan0 -p udp --dport 5353 -j DROP'

[Install]
WantedBy=multi-user.target
```

**설계 포인트**:
- `After=docker.service otbr-agent.service` -> Docker/OTBR 체인 재생성 이후 실행
- `ExecStartPre=/bin/sleep 5` -> 체인 안정화 대기
- `-C` (check) 후 `-I` (insert) -> 멱등성 보장
- `RemainAfterExit=yes` -> 서비스 상태를 active로 유지

활성화:
```bash
sudo systemctl daemon-reload
sudo systemctl enable matterhub-ip6tables-mdns.service
sudo systemctl start matterhub-ip6tables-mdns.service
```

---

## 오진 기록

### ip6tables DROP이 Thread를 깨뜨린다는 초기 의심 (오진)

수정 과정에서 ip6tables DROP 적용 직후 Thread 기기가 연결 안 되는 현상 발생.
DROP 제거 후 Thread 복구 -> "DROP이 Thread mDNS를 차단한다"고 판단.

**실제 원인**: 재부팅 직후 OTBR 안정화 타이밍 문제. OTBR가 아직 leader 상태에 도달하기 전이었음.

**검증**: OTBR 안정화 후 DROP 재적용 -> Thread + WiFi 모두 정상 동작 확인.

**교훈**: 재부팅 직후에는 OTBR 안정화(leader 도달)를 먼저 확인한 후 테스트해야 함.

---

## 검증 체크리스트

```bash
# 1. matter-server primary-interface
docker logs matter-server --tail 30 2>&1 | grep "primary interface"
# -> Using 'wlan0' as primary interface

# 2. avahi wpan0 제외
grep "^deny-interfaces" /etc/avahi/avahi-daemon.conf
# -> deny-interfaces=wpan0

# 3. ip6tables wpan0 mDNS 차단
sudo ip6tables -L INPUT -n | grep 5353
sudo ip6tables -L OUTPUT -n | grep 5353
# -> DROP 규칙 존재

# 4. systemd 영구화 서비스
systemctl is-enabled matterhub-ip6tables-mdns.service
# -> enabled
```

---

## 관련 파일

| 파일 | 위치 | 용도 |
|------|------|------|
| fix_otbr_mdns_conflict.sh | `device_config/fix_otbr_mdns_conflict.sh` (konai) | 일회성 수정 스크립트 |
| docker-compose.yml | `/home/whatsmatter/matterhub-install/docker-compose.yml` | --primary-interface wlan0 |
| avahi-daemon.conf | `/etc/avahi/avahi-daemon.conf` | deny-interfaces=wpan0 |
| systemd unit | `/etc/systemd/system/matterhub-ip6tables-mdns.service` | 재부팅 시 ip6tables 자동 복원 |

---

## 미해결 사항

- **openclaw-gateway**: `0.0.0.0:5353`에 와일드카드 3개 소켓 바인딩으로 mDNS 경쟁에 참여 중. 현재 수정하지 않음 (별도 관리 대상).
