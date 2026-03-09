# MatterHub 납품 준비 점검표

## 1. 목적

본 문서는 현재 저장소 기준으로 납품 준비 상태를 점검하기 위한 체크리스트다.

중요 원칙:

- 코드/테스트로 닫힌 항목과
- 실제 라즈베리파이에서만 닫을 수 있는 항목을 분리한다.

## 2. 코드/테스트 기준으로 확인 완료

### 2.1 Wi-Fi provisioning

- `GET /local/admin/network/status`에 provisioning 상태 포함
- AP 부팅 fallback 로직 존재
- 저장된 known network 자동 재연결 로직 존재
- 연결 실패 시 롤백 후 AP fallback 로직 존재

자동화 확인:

- `tests/wifi_config/test_api.py`
- `tests/wifi_config/test_bootstrap.py`
- `tests/wifi_config/test_service.py`
- `tests/wifi_config/test_state.py`

### 2.2 Wi-Fi 설정 UI

- 연결은 modal 기반으로 진행
- 저장된 네트워크는 modal로 분리
- 외부 Google Fonts 제거
- Toss 기준 색상/카드/pill/button 구조 반영
- 스캔한 SSID와 저장된 연결 이름 렌더링 시 escape 처리
- 로컬 호스트명(`matterhub-setup-whatsmatter.local`) 접속 경로 표시

자동화 확인:

- `tests/wifi_config/test_api.py`

### 2.3 Reverse tunnel 보호/복구

- relay TCP preflight 검사
- SSH `ConnectTimeout` 적용
- private key / known_hosts 존재 검사
- known_hosts 사전 등록을 위한 설치 스크립트 존재

자동화 확인:

- `tests/mqtt_pkg/test_support_tunnel.py`
- `tests/device_config/test_setup_support_tunnel_script.py`

### 2.4 업데이트 번들 / 롤백

- 번들 검증
- `payload/` 오버레이
- 헬스체크 실패 시 롤백
- update-agent 자동 적용 경로 존재

자동화 확인:

- `tests/test_update_agent.py`
- `tests/device_config/test_apply_update_bundle_script.py`
- `tests/device_config/test_build_runtime_bundle_script.py`
- `tests/device_config/test_install_runtime_bundle_script.py`

### 2.5 서비스/패키징

- systemd unit 렌더링
- binary runtime 경로 지원
- update-agent unit 포함
- 설치 스크립트에서 Wi-Fi/AP/update-agent/env 초기값 반영
- Avahi 기반 로컬 mDNS hostname/HTTP 서비스 광고 설치 경로 존재

자동화 확인:

- `tests/device_config/test_service_definitions.py`
- `tests/device_config/test_render_systemd_units.py`
- `tests/device_config/test_setup_initial_device_script.py`
- `tests/device_config/test_setup_local_hostname_mdns_script.py`

### 2.6 MAC 바인딩

- 허용 MAC 검증 모듈 존재
- 주요 엔트리포인트 preflight 적용

자동화 확인:

- `tests/libs/test_device_binding.py`

## 3. 실제 장비에서 최종 확인 필요

아래 항목은 현재 세션에서 라즈베리파이에 접속하지 않은 상태이므로 아직 “문서/코드 준비 완료”까지만 확인된 상태다.

### 3.1 로그인 전 reverse tunnel

확인 목표:

- 장비 전원 인가
- Ubuntu 로그인 전
- 인터넷 연결 완료 상태에서
- `matterhub-support-tunnel.service`가 실제로 relay에 연결되는지 확인

확인 이유:

- 이 항목은 Desktop/keyring/실제 프로파일 저장 상태 영향을 받으므로 실기기 재부팅 검증이 필요하다.

### 3.2 실기기 AP 자동 전환

확인 목표:

- 현재 연결된 Wi-Fi를 끊었을 때
- 저장된 known network 자동 재연결을 먼저 시도하고
- 실패 시 AP 모드가 실제로 뜨는지 확인

### 3.3 UI 최종 확인

확인 목표:

- AP 모드와 일반 모드에서 `/local/admin/network` 렌더링 확인
- `.local` 호스트명 접속 확인
- 연결 modal 동작 확인
- 연결 10초 안내 문구와 상태 배너 확인
- 모바일 폭에서 카드 재배치 확인

## 4. 구현 완료 후 유의사항

### 4.1 로컬 호스트명 접속

사용자가 원했던 “위치와 네트워크가 바뀌어도 같은 호스트명으로 접속” 요구는 현재 저장소 기준으로 `mDNS(Avahi)` 방식으로 반영되었다.

현재 공식 접속 방식:

- 우선: `http://matterhub-setup-whatsmatter.local:8100/local/admin/network`
- 일반 모드: `http://<라즈베리파이_IP>:8100/local/admin/network`
- AP 모드: `http://10.42.0.1:8100/local/admin/network`

주의:

- `.local` 해석은 단말/망 환경에 따라 차이가 있을 수 있다.
- 따라서 납품 문서에는 항상 `.local`과 IP/AP fallback 경로를 함께 안내한다.

## 5. 현재 권장 마감 절차

1. 라즈베리파이 실기기에서 재부팅 검증
2. Wi-Fi 단절/AP fallback 검증
3. reverse tunnel cold boot 검증
4. 최종 디자인 확인 후 캡처 보관
5. 결과를 `codex_context.md`와 운영 문서에 반영
