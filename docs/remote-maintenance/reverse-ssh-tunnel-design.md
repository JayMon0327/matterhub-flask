# MatterHub Reverse SSH Tunnel 설계

## 1. 목적

본 문서는 납품 장비의 원격 유지보수를 `reverse SSH tunnel` 중심으로 구성하기 위한 설계를 정의한다.

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [리팩터링 로드맵](../refactoring-roadmap.md)

## 2. 설계 목표

- 고객사 네트워크에 inbound 포트 개방 없이 유지보수 가능
- `git pull` 없이 장비 접속 및 상태 점검 가능
- 유지보수 요청이 있을 때만 제한적으로 접속 허용
- 접속 이력과 작업 이력 추적 가능

## 3. 권장 구조

구성 요소:

- 장비 내부 tunnel launcher
- MQTT 또는 내부 관리 API를 통한 support mode 활성화
- 중앙 support server
- 유지보수 전용 SSH 계정 및 공개키

## 4. 기본 흐름

1. 운영자가 특정 장비의 지원 모드 활성화를 요청한다.
2. 장비가 요청을 검증하고 reverse tunnel을 시작한다.
3. 장비는 support server에 outbound SSH 연결을 맺는다.
4. 운영자는 support server를 통해 대상 장비로 접속한다.
5. 점검, 로그 확인, 패키지 설치, 서비스 재시작을 수행한다.
6. 작업 종료 후 장비가 tunnel을 종료한다.

## 5. 실행 방식

초기 구현은 아래 둘 중 하나를 기준으로 검토한다.

- `ssh -N -R ...`
- `autossh -N -R ...`

권장 방향:

- 연결 안정성이 필요하면 `autossh`
- 초기 구현 단순성이 중요하면 `ssh`

## 6. MQTT 역할 제한

MQTT는 아래 역할만 수행한다.

- 지원 모드 시작 요청
- 지원 모드 종료 요청
- 상태 회신

MQTT가 직접 수행하면 안 되는 역할:

- 임의 셸 명령 실행
- Git 브랜치 지정
- 원격 코드 pull
- 운영자 임의 명령 전달

## 7. 보안 정책

- 비밀번호 로그인 금지
- 공개키 인증만 허용
- 유지보수 전용 계정 분리
- `PermitOpen` 또는 서버측 제한 검토
- tunnel 활성화 시간 제한
- 사용자 승인된 운영 절차에만 사용

## 8. 내부 API 또는 도메인 설계 방향

향후 코드 구조는 아래 도메인으로 분리하는 것을 기본으로 한다.

```text
support_tunnel/
  service.py
  launcher.py
  policy.py
  status.py
```

테스트는 동일 구조로 대응한다.

```text
tests/domains/support_tunnel/
```

## 9. 장애 및 복구 고려사항

- support server 미도달 시 재시도 정책
- 일정 시간 후 자동 종료
- 중복 터널 요청 시 단일 세션 유지
- 비정상 종료 시 상태 정리
- 네트워크 변경 후 재연결 정책

## 10. 테스트 포인트

- 시작 요청 파싱
- 중복 시작 방지
- 종료 요청 처리
- 명령 구성 검증
- SSH 실행 실패 처리
- 타임아웃 후 자동 종료
- 상태 회신 payload 검증

## 11. 현재 구현(1차)

현재 저장소에는 reverse tunnel 실행기를 아래 경로에 추가한다.

- 실행 모듈: `mqtt_pkg/support_tunnel.py`
- 엔트리포인트: `support_tunnel.py`
- systemd 유닛명: `matterhub-support-tunnel.service`

`device_config/install_ubuntu24.sh` 실행 시 위 서비스 유닛이 설치된다.
단, support tunnel은 운영자가 필요 시 수동 시작하는 구조이므로 기본 자동 enable/restart 대상에서는 제외한다.

## 12. 환경변수 규격

reverse tunnel 실행기는 `.env` 또는 systemd `EnvironmentFile` 기준으로 아래 값을 사용한다.

필수:

- `SUPPORT_TUNNEL_ENABLED=1`
- `SUPPORT_TUNNEL_USER=<유지보수 계정>`
- `SUPPORT_TUNNEL_HOST=<지원 서버 호스트>`
- `SUPPORT_TUNNEL_REMOTE_PORT=<지원 서버에 열 포트>`

선택:

- `SUPPORT_TUNNEL_COMMAND=ssh|autossh` (기본 `ssh`)
- `SUPPORT_TUNNEL_PORT=443` (지원 서버 SSH 포트)
- `SUPPORT_TUNNEL_LOCAL_PORT=22` (장비 내부 SSH 포트)
- `SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS=127.0.0.1`
- `SUPPORT_TUNNEL_PRIVATE_KEY_PATH=/etc/matterhub/support_tunnel_ed25519`
- `SUPPORT_TUNNEL_KNOWN_HOSTS_PATH=/etc/matterhub/support_known_hosts`
- `SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING=1` (기본 1)
- `SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL=30`
- `SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX=3`
- `SUPPORT_TUNNEL_SSH_EXTRA_OPTS="<추가 SSH 옵션>"`
- `SUPPORT_TUNNEL_AUTOSSH_GATETIME=0`

## 13. 운영 명령

설정 검증(dry-run):

```bash
cd /home/whatsmatter/Desktop/matterhub
venv/bin/python support_tunnel.py --dry-run
```

운영자 접속 명령 출력:

```bash
cd /home/whatsmatter/Desktop/matterhub
venv/bin/python support_tunnel.py --print-connect-command --device-user whatsmatter
```

서비스 제어:

```bash
sudo systemctl restart matterhub-support-tunnel.service
sudo systemctl status matterhub-support-tunnel.service
journalctl -u matterhub-support-tunnel.service -n 200 --no-pager
```

## 14. 자동 설치 스크립트

반복 배포용 스크립트:

- `device_config/setup_support_tunnel.sh`

빠른 적용 순서는 아래 문서를 따른다.

- [Reverse SSH Tunnel 빠른 적용 가이드](./reverse-ssh-tunnel-quickstart.md)
