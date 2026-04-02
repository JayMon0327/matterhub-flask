# 플랫폼 설치 트러블슈팅 (2026-04-01)

192.168.1.94, 192.168.1.101, 192.168.1.96 3대 장비에 `/platform-base-image` + `/platform-activate` 스킬을 적용하며 발생한 이슈 기록.

---

## 1. OTBR Thread 초기화 시 detached 상태 (2026-04-01)

**증상**: `dataset init new` → `dataset channel 15` → `commit active` → `thread start` 후 15초 대기했으나 `state`가 `detached`, channel이 `11`로 표시.

**원인**: OTBR 빌드 직후 `otbr-agent`가 자동 시작되면서 기본 dataset(channel 11)으로 attach 시도. 이후 `dataset init new`가 적용되지 않고 기존 dataset이 유지됨.

**해결**:
```bash
sudo ot-ctl thread stop
sudo ot-ctl ifconfig down
sudo ot-ctl dataset init new
sudo ot-ctl dataset channel 15
sudo ot-ctl dataset commit active
sudo ot-ctl ifconfig up
sudo ot-ctl thread start
sleep 15
sudo ot-ctl state   # leader
```

**교훈**: Thread 초기화 전에 반드시 `thread stop` + `ifconfig down`으로 기존 상태를 정리해야 한다.

---

## 2. ip6tables 규칙 재부팅 후 소실 (2026-04-01)

**증상**: `/platform-base-image`에서 ip6tables wpan0 5353 DROP 규칙을 설정했으나, SD카드를 다른 장비에 옮긴 후 규칙이 없음.

**원인**: `iptables-persistent` 설치 시 네트워크 불안정으로 설치 실패하거나, `netfilter-persistent save` 실행 전에 세션이 종료됨. 또는 재부팅 후 `netfilter-persistent.service`가 규칙을 복원하지 못함.

**해결**: `/platform-activate` 2-0 사전 확인에서 ip6tables 규칙 존재 여부를 체크하고, 없으면 재적용.
```bash
sudo ip6tables -L INPUT -n | grep -q 5353 || {
    sudo ip6tables -I INPUT -i wpan0 -p udp --dport 5353 -j DROP
    sudo ip6tables -I OUTPUT -o wpan0 -p udp --dport 5353 -j DROP
    sudo netfilter-persistent save 2>/dev/null || sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 > /dev/null
}
```

**교훈**: 베이스 이미지의 ip6tables 영구화를 신뢰하지 말고, activate 단계에서 항상 확인/복구해야 한다.

---

## 3. SSH known_hosts 호스트 키 충돌 (2026-04-01)

**증상**: 같은 IP에 새 SD카드를 넣으면 SSH 접속 시 `REMOTE HOST IDENTIFICATION HAS CHANGED` 에러로 접속 거부.

**원인**: 새 SD카드(새 OS)의 SSH 호스트 키가 이전과 다름.

**해결**: 접속 전 known_hosts 갱신.
```bash
ssh-keygen -R <IP>
ssh-keyscan -H <IP> >> ~/.ssh/known_hosts
```

**교훈**: 스킬 실행 시 SSH 접속 전에 항상 known_hosts를 갱신해야 한다.

---

## 4. expect에서 sudo 비밀번호 캡처 실패 (2026-04-01)

**증상**: `sudo command1 && command2 && command3` 형태로 실행 시 expect가 중간 sudo 비밀번호 프롬프트를 캡처하지 못해 `3 incorrect password attempts` 에러.

**원인**: `&&`로 연결된 명령에서 sudo가 비밀번호를 요구할 때 expect의 `expect "password"` 패턴이 매칭되지 않음.

**해결**: sudo 명령은 `&&`로 연결하지 말고 개별 `send` + `expect`로 분리.
```tcl
# BAD
send "sudo cmd1 && sudo cmd2\r"

# GOOD
send "sudo cmd1\r"
expect { "password" { send "pw\r"; exp_continue } "$ " {} }
send "sudo cmd2\r"
expect "$ "
```

또는 bash 스크립트를 Pi에 scp 후 `sudo bash /tmp/script.sh`로 실행 (sudo 1회만 요구).

**교훈**: expect에서 복잡한 sudo 명령은 스크립트 파일로 만들어서 실행하는 것이 안정적.

---

## 5. expect에서 `tail -N`으로 출력 필터링 시 sudo 프롬프트 누락 (2026-04-01)

**증상**: `sudo bash script.sh 2>&1 | tail -5` 형태로 실행 시 sudo 비밀번호 프롬프트가 tail에 의해 버퍼링되어 expect가 "password" 패턴을 감지하지 못함. 세션이 무한 대기.

**원인**: `tail`이 파이프 입력을 버퍼링하므로 sudo의 비밀번호 프롬프트가 stdout으로 즉시 전달되지 않음.

**해결**: sudo 명령 실행 시 `| tail`을 사용하지 않는다. 출력이 길면 스크립트 내부에서 `echo` 마커를 사용.
```tcl
# BAD
send "sudo bash /tmp/script.sh 2>&1 | tail -5\r"

# GOOD
send "sudo bash /tmp/script.sh 2>&1\r"
expect { "password" { send "pw\r"; exp_continue } "COMPLETE_MARKER" {} }
```

**교훈**: expect + sudo + tail 조합은 사용하지 않는다.

---

## 6. Matter 통합 등록 시 abort 응답 (2026-04-01)

**증상**: OTBR 통합 등록 후 Matter 통합을 별도로 등록하면 `"type":"abort"` 응답.

**원인**: HA가 OTBR 통합 등록 시 Thread + Matter를 자동으로 함께 등록. 이미 등록된 상태에서 중복 등록 시도하면 abort.

**해결**: abort는 에러가 아닌 "이미 등록됨" 상태. 통합 확인으로 검증:
```bash
curl -s http://127.0.0.1:8123/api/config/config_entries/entry \
  -H "Authorization: Bearer $HA_TOKEN" | python3 -c \
  "import sys,json; [print(e['domain'], e['state']) for e in json.load(sys.stdin) if e['domain'] in ('otbr','thread','matter')]"
# thread loaded
# otbr loaded
# matter loaded
```

**교훈**: Matter 통합은 OTBR 등록 시 자동 포함될 수 있으므로, 별도 등록 전에 이미 존재하는지 확인한다.

---

## 7. Matter Server 시작 직후 mDNS advertise 실패 (2026-04-01)

**증상**: matter-server 로그에 시작 직후 2~3건의 mDNS advertise 에러:
```
CHIP_ERROR: Failed to advertise records: Network is unreachable (OS Error 0x02000065)
```

**원인**: 컨테이너 시작 직후 wpan0 IPv6 link-local 주소가 아직 stable되지 않은 타이밍에 CHIP UDPv6 소켓 bind 시도.

**해결**: 일시적 타이밍 이슈로 이후 재발 없음. 운영 영향 없음. Matter 기기 커미셔닝 중 동일 현상 재발 시 `docker restart matter-server`.

**교훈**: 시작 직후 mDNS 에러는 무시 가능. 지속 발생 시에만 대응.

---

## 요약: 스킬 반영 사항

| 이슈 | 반영 대상 |
|------|-----------|
| Thread 초기화 전 stop/ifconfig down 필수 | `/platform-activate` 2-1a |
| ip6tables 규칙 activate 시 확인/복구 | `/platform-activate` 2-0 |
| SSH known_hosts 갱신 | 모든 스킬 SSH 접속 전 |
| expect + sudo: 명령 분리 또는 스크립트 파일 | 스킬 실행 시 주의사항 |
| expect + sudo + tail 금지 | 스킬 실행 시 주의사항 |
| Matter abort는 정상 | `/platform-activate` 2-2 |
