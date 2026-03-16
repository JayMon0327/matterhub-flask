# MatterHub .deb 패키징 설계

## 1. 목적

본 문서는 납품 장비에 Git 저장소 대신 설치 가능한 `.deb` 패키지를 배포하기 위한 기준을 정의한다.

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [리팩터링 로드맵](../refactoring-roadmap.md)

## 2. 설계 목표

- 평문 Python 소스 직접 배포 최소화
- 설치, 업그레이드, 제거 절차 표준화
- systemd 서비스 파일 포함
- 설정 파일과 런타임 데이터는 패키지 본체와 분리

## 3. 권장 빌드 방향

- Python 코드는 `Nuitka` 기반 컴파일 산출물 검토
- 산출물을 `.deb` 패키지 payload로 구성
- 설치 스크립트에서 사용자, 디렉토리, systemd 활성화 처리

## 4. 패키지 포함 항목

- 실행 파일
- 기본 설정 템플릿
- systemd unit 파일
- support tunnel 관련 실행 파일 또는 스크립트
- claim/Konai 인증서 자산 (`certificates/`, `konai_certificates/`)
- Wi-Fi 설정 UI 관련 정적 자산

## 5. 패키지에 포함하지 않을 항목

- Git metadata
- 개발용 테스트 아티팩트
- 운영 중 생성되는 로그 및 상태 데이터

## 6. 파일 배치 기준

```text
/opt/matterhub/bin
/etc/matterhub
/usr/lib/systemd/system
```

런타임 중 생성되는 파일은 패키지 설치 대상이 아니라 운영 디렉토리에서 생성한다.

## 7. 설치 스크립트 고려사항

`postinst`에서 검토할 항목:

- 전용 사용자 생성
- 디렉토리 생성 및 권한 설정
- 기본 env 파일 배치
- systemd daemon reload
- 서비스 enable

`prerm` 또는 `postrm`에서 검토할 항목:

- 서비스 중지
- 필요 시 사용자 데이터 보존 정책
- 완전 삭제 여부 분리

## 8. 업그레이드 정책

- 설정 파일은 덮어쓰지 않도록 설계
- 데이터 디렉토리는 유지
- 새 버전 설치 후 systemd 재시작
- 실패 시 이전 패키지 재설치 가능하도록 버전 관리

## 9. 서명 및 배포

자동 업데이트까지 고려하면 아래를 함께 검토한다.

- 패키지 서명
- 사설 APT 저장소 또는 버전별 아티팩트 저장소
- SHA256 무결성 검증

## 10. 테스트 포인트

- 신규 설치
- 동일 버전 재설치
- 상위 버전 업그레이드
- 설정 파일 보존
- 서비스 자동 활성화
- 제거 후 데이터 보존 정책 검증

## 11. 현재 구현 스크립트

패키지 빌드 자동화 스크립트:

- `device_config/build_matterhub_deb.sh`
- `device_config/build_runtime_binaries.sh` (`PyInstaller --onedir` 기반 서비스별 실행파일 빌드)
- `device_config/build_runtime_bundle.sh` (실행파일 전용 runtime bundle 생성)
- `device_config/install_runtime_bundle.sh` (라즈베리파이에 runtime bundle 설치)

## 12. 현재 저장소 기준 상태

현재 저장소에는 `.deb`/runtime bundle 빌드 자동화 스크립트와 대응 테스트가 존재한다.

다만 아래 상태는 아직 별도로 구분해야 한다.

- 패키징 "자동화 준비 완료"
- 최종 납품용 "실제 산출물 생성 및 실기기 설치 검증 완료"

현재 기준으로는 첫 번째만 충족된 상태다.

판단 근거:

- `device_config/build_matterhub_deb.sh`
- `device_config/build_runtime_binaries.sh`
- `device_config/build_runtime_bundle.sh`
- `device_config/install_runtime_bundle.sh`
- `tests/device_config/test_build_matterhub_deb_script.py`
- `tests/device_config/test_build_runtime_binaries_script.py`
- `tests/device_config/test_build_runtime_bundle_script.py`
- `tests/device_config/test_install_runtime_bundle_script.py`

주의:

- 저장소 루트에 최종 납품 산출물(`dist/*.deb`, `dist/*.tar.gz`)이 생성되어 있어야 "실제 패키징 완료"로 볼 수 있다.
- 현재 패키징 스크립트는 `wifi_config`, `templates`까지 payload에 포함한다.
- 다만 현재 패키징 단계에서는 Wi-Fi 자동 설정/AP hotspot 동작을 기본 비활성화(`WIFI_AUTO_AP_ON_BOOT=0`, `WIFI_AUTO_AP_ON_DISCONNECT=0`, `WIFI_AP_AUTO_RECONNECT_ENABLED=0`)한 상태로 산출물을 생성한다.
- 즉, Wi-Fi 코드는 payload에 남아 있지만 기본 동작에서는 dormant 상태이며, 이후 원격 핫픽스로 재활성화하는 전제를 둔다.
- 이번 패키징 범위는 Wi-Fi 자동화 제외 나머지 소프트웨어(`api`, `mqtt`, `rule-engine`, `notifier`, `support-tunnel`, `update-agent`)를 우선 납품 가능한 형태로 묶는 것이다.
- 현재 납품 패키징은 `certificates/`와 `konai_certificates/`를 payload에 포함한다.
- `.deb` 설치 시 `matterhub-provision.service`가 자동 Claim 프로비저닝과 support tunnel bootstrap을 수행한다.
- `matterhub_id`가 확보되면 bootstrap 단계가 `setup_support_tunnel.sh`를 호출해 remote port를 계산하고 `matterhub-support-tunnel.service`를 자동 enable/start 한다.
- 설치 시점에 `matterhub_id`가 없어 support tunnel 설정이 보류되더라도, `matterhub-provision.service`가 부팅 시 다시 실행되므로 이후 재부팅에서 self-heal 된다.
- `.deb` 또는 runtime bundle 설치 스크립트는 `ufw`가 존재할 경우 `8100/tcp`, `8123/tcp` 허용 규칙을 자동으로 추가한다.

기본 예시:

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/build_matterhub_deb.sh \
  --version 2026.03.05 \
  --output-dir /home/whatsmatter/Desktop/matterhub/dist
```

참고:

- 기본값은 `source` 이고, 이 모드가 라즈베리파이 반복 설치용 기본 경로다.
- `--mode pyc` 는 `.py`를 `.pyc`로 변환해 payload의 원본 코드 노출을 줄인다.
- 다만 `pyc` 모드는 빌드 Python 버전과 타깃 Python 버전이 맞아야 하므로, 다른 장비에 반복 배포하는 기본값으로는 권장하지 않는다.
- 완전한 역공학 방지는 아니며, 목표는 노출 난이도 상승이다.
