# MatterHub 납품 및 운영 런북

## 1. 목적

본 문서는 납품 전후 운영자가 따라야 할 기본 절차를 정의한다.

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [리팩터링 로드맵](../refactoring-roadmap.md)
- [납품 준비 점검표](./delivery-readiness-checklist.md)

## 2. 납품 전 체크리스트

- 대상 장비 아키텍처 확인
- Ubuntu 24.04 LTS 버전 확인
- 필수 패키지 설치 여부 확인
- 기본 네트워크 연결 확인
- MatterHub 패키지 설치 확인
- systemd 서비스 enable 상태 확인
- 초기 프로비저닝 완료 확인

## 2.1 현재 패키징 상태 해석

현재 저장소 기준으로는 "패키징 스크립트와 테스트는 준비됨" 상태이며, "최종 납품용 산출물까지 생성 완료" 상태는 아니다.

즉, 아래 두 상태를 분리해서 판단한다.

- 준비 완료: `.deb`/runtime bundle 빌드 스크립트와 테스트가 존재하고 dry-run 및 단위 테스트가 통과함
- 납품 완료: 실제 `dist/*.deb` 또는 `dist/*.tar.gz` 산출물을 생성하고, 라즈베리파이 실기기에 설치 검증까지 마침

추가 주의:

- 현재 패키징 스크립트는 Wi-Fi 관련 코드/템플릿도 함께 포함한다.
- 다만 현재 납품 단계에서는 Wi-Fi 자동 설정/AP hotspot 동작을 기본 비활성화한 상태로 패키징한다.
- 따라서 최종 패키지 빌드 자체는 진행할 수 있고, Wi-Fi 자동화 재활성화는 별도 핫픽스 범위로 분리한다.

## 3. 설치 절차 초안

1. 장비에 기본 OS 이미지 준비
2. 필수 OS 패키지 설치
3. MatterHub `.deb` 설치
4. `/etc/matterhub` 설정 반영
5. systemd 서비스 enable 및 start
6. 기본 동작 확인

## 3.1 현재 개발 단계의 Git 배포 절차

현 시점에서는 최종 `.deb` 배포 전이므로, 아래 절차로 라즈베리파이에 적용한다.

1. 라즈베리파이에서 최신 코드를 pull 한다.
2. 프로젝트 루트로 이동한다.
3. 신규 장비는 아래 통합 스크립트를 우선 실행한다.

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/setup_initial_device.sh
```

`setup_initial_device.sh` 는 Wi-Fi/AP 기본값을 `.env`에 반영한 뒤 `install_ubuntu24.sh`를 호출한다.

`install_ubuntu24.sh` 는 다음 작업을 일괄 수행한다.

- Ubuntu 필수 패키지 설치
- `openssh-server` 설치 및 `ssh` 서비스 활성화
- `avahi-daemon` 기반 로컬 mDNS 호스트명 설정
- `iw` 설치 및 Wi-Fi 국가코드(`WIFI_COUNTRY_CODE`) 고정
- `venv` 생성 및 Python 의존성 설치
- `NetworkManager` 제어 권한(polkit) 설치
- AP 진입 시 충돌 서비스(`WIFI_AP_CONFLICT_SERVICES`, 기본 `named.service`) 제어 권한 설치
- systemd 유닛 렌더링 및 설치
- 서비스 enable/restart
- `matterhub-update-agent.service` 자동기동 포함

reverse tunnel 설정까지 동시에 진행하려면 아래 옵션을 사용한다.

```bash
cd /home/whatsmatter/Desktop/matterhub
RELAY_HUB_ACCESS_PUBKEY="$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 'cat /home/ec2-user/.ssh/hub_access_ed25519.pub')"
bash device_config/install_ubuntu24.sh \
  --setup-support-tunnel \
  --support-host 3.38.126.167 \
  --support-user whatsmatter \
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY" \
  --harden-allow-inbound-port 8100 \
  --harden-allow-inbound-port 8123
```

운영 기준:

- `8100/tcp`, `8123/tcp`는 영구 허용 포트다.
- `22/tcp`, `8110/tcp`는 유지보수 작업 중에만 임시 허용하고 끝나면 다시 닫는다.

## 4. 운영 절차 초안

### 일반 점검

- 서비스 상태 확인
- 로그 확인
- MQTT 연결 상태 확인
- 로컬 API 상태 확인

### 원격 유지보수

1. support mode 활성화
2. reverse tunnel 접속 확인
3. 로그/설정/서비스 점검
4. 패키지 업그레이드 또는 복구
5. support mode 종료

실행 문서는 아래를 우선 사용한다.

- [Reverse SSH Tunnel 빠른 적용 가이드](../remote-maintenance/reverse-ssh-tunnel-quickstart.md)
- [리버스 터널 접속방법](../remote-maintenance/reverse-tunnel-access-method.md)
- [Reverse Tunnel Only 하드닝 가이드](./reverse-tunnel-only-hardening.md)

포트 정책:

- 고객사 납품 상태에서는 direct SSH `22/tcp` inbound를 차단한다.
- 단, `8100/tcp`와 `8123/tcp`는 항상 열어 둔다.
- `8100/tcp`: MatterHub Wi-Fi 설정 Web UI
- `8123/tcp`: Home Assistant

### Wi-Fi 변경 지원

1. `http://matterhub-setup-whatsmatter.local:8100/local/admin/network` 접속 시도
2. `.local` 접속이 안 되면 라즈베리파이 IP 또는 AP 주소(`10.42.0.1`) 사용
3. SSID 스캔
4. 새 네트워크 연결 시도
5. 연결 상태 확인
6. 실패 시 롤백 또는 AP 복구 모드 사용

## 5. 장애 대응 초안

### 서비스 기동 실패

- `journalctl` 확인
- 설정 파일 유효성 확인
- 실행 파일 권한 및 경로 확인

### 네트워크 연결 실패

- 유선 연결 가능 여부 확인
- AP 복구 모드 사용
- 저장된 연결 정보 확인

### 유지보수 접속 실패

- support server 접근성 확인
- 키 파일 및 계정 권한 확인
- tunnel 프로세스 상태 확인

## 6. 복구 정책 초안

- 최신 안정 패키지 버전 유지
- 업그레이드 실패 시 직전 버전으로 롤백
- 설정 파일과 운영 데이터는 복구 전 백업
- 네트워크 변경 전 이전 프로파일 정보 보존

## 6.1 업데이트 번들 적용 표준

수동 적용:

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/apply_update_bundle.sh \
  --bundle /tmp/matterhub-update-1.2.3.tar.gz \
  --project-root /home/whatsmatter/Desktop/matterhub \
  --healthcheck-cmd "systemctl is-active matterhub-api.service matterhub-mqtt.service matterhub-rule-engine.service matterhub-notifier.service"
```

자동 적용(update-agent):

```bash
cd /home/whatsmatter/Desktop/matterhub
mkdir -p update/inbox
cp /tmp/matterhub-update-1.2.3.tar.gz update/inbox/
systemctl status matterhub-update-agent.service --no-pager
journalctl -u matterhub-update-agent.service -n 50 --no-pager
```

검증/정책 환경변수(`.env`):

- `UPDATE_AGENT_REQUIRE_MANIFEST=1`
- `UPDATE_AGENT_ALLOWED_BUNDLE_TYPES=matterhub-runtime,matterhub-update`
- `UPDATE_AGENT_REQUIRE_SHA256=0|1`

`UPDATE_AGENT_REQUIRE_SHA256=1`일 때는 `bundle.tar.gz.sha256` 파일이 필수다.

## 7. 최종 인수 기준

- 부팅 후 자동 기동
- MQTT 정상 연결
- 로컬 API 정상 응답
- Wi-Fi 변경 가능
- support tunnel 기반 유지보수 가능
- 로그 확인 가능
- 재부팅 후 상태 유지

## 8. 패키징 전환 실행 메모

현 단계 `.deb` 빌드 스크립트:

- `device_config/build_matterhub_deb.sh`
- `device_config/build_runtime_binaries.sh`
- `device_config/build_runtime_bundle.sh`
- `device_config/install_runtime_bundle.sh`

예시:

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/build_matterhub_deb.sh --version 2026.03.05
```

현재 패키징 기준:

- Wi-Fi 자동 설정/AP hotspot 동작은 기본 비활성화 상태로 패키징한다.
- 패키지 기본 env는 `WIFI_AUTO_AP_ON_BOOT=0`, `WIFI_AUTO_AP_ON_DISCONNECT=0`, `WIFI_AP_AUTO_RECONNECT_ENABLED=0`를 포함한다.
- 이번 납품 범위는 `api`, `mqtt`, `rule-engine`, `notifier`, `support-tunnel`, `update-agent` 패키징 완료를 우선 기준으로 삼는다.

최종 납품 완료로 판단하려면 아래가 추가로 필요하다.

- 실제 `dist/` 산출물 생성
- 생성된 패키지 또는 runtime bundle을 실기기에 설치
- 서비스 기동 및 reverse tunnel 정책 검증
- `22/tcp` 차단, `8100/tcp`/`8123/tcp` 유지 개방 확인
- Wi-Fi 자동화 재활성화 여부는 별도 핫픽스 범위로 관리

## 9. 물리 콘솔 접근 제한

물리 모니터/키보드 로그인까지 막으려면 아래 옵션을 설치 단계에 포함한다.

```bash
bash device_config/setup_initial_device.sh \
  --setup-support-tunnel \
  --harden-reverse-tunnel-only \
  --harden-allow-inbound-port 8100 \
  --harden-allow-inbound-port 8123 \
  --harden-local-console-pam
```

적용 후 기대 상태:

- 로컬 GUI 로그인 화면 미노출
- 로컬 TTY 로그인 프롬프트 미노출
- 원격 유지보수는 reverse tunnel 경로만 사용
