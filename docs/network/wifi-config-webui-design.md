# MatterHub Wi-Fi 설정 Web UI 설계

## 1. 목적

본 문서는 고객사가 Linux 로그인 없이 Wi-Fi 정보를 변경할 수 있도록 로컬 Web UI와 내부 API를 설계하기 위한 기준을 정의한다.

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [리팩터링 로드맵](../refactoring-roadmap.md)

## 2. 설계 목표

- 고객사는 브라우저만으로 Wi-Fi를 변경할 수 있어야 한다.
- Ubuntu 24.04 LTS 환경에 맞게 `NetworkManager` 기반으로 동작해야 한다.
- 잘못된 설정 시 복구 경로가 있어야 한다.
- 로컬 관리 기능이 외부에 과도하게 노출되면 안 된다.

## 3. 기술 방향

- 네트워크 제어는 `wpa_supplicant.conf` 직접 편집 대신 `nmcli` 사용
- Web UI는 로컬 전용 페이지로 운영
- 백엔드는 `nmcli` 호출을 직접 노출하지 않고 제한된 서비스 계층을 둠

## 4. 주요 기능

- 현재 연결 상태 조회
- 주변 SSID 스캔
- 새로운 SSID 연결
- 저장된 연결 조회
- 저장된 연결 삭제
- 복구 모드 진입

## 5. 권장 API 초안

- `GET /local/admin/network/status`
- `GET /local/admin/network/wifi/scan`
- `POST /local/admin/network/wifi/connect`
- `GET /local/admin/network/wifi/saved`
- `DELETE /local/admin/network/wifi/saved/<connection_name>`
- `POST /local/admin/network/recovery/ap-mode`

실제 path는 구현 시 기존 API 네이밍과 함께 재검토할 수 있다. 단, 변경 시에는 먼저 사용자에게 제안한다.

`GET /local/admin/network/status`는 Wi-Fi 상태와 함께 provisioning 상태머신 정보를 포함한다.

- `provision_state.state`
  - `BOOTING`
  - `STA_CONNECTING`
  - `STA_CONNECTED`
  - `STA_FAILED`
  - `AP_STARTING`
  - `AP_MODE`
- `provision_state.reason`
- `provision_state.details`
- `provision_state.updated_at` (epoch seconds)

## 6. 내부 서비스 분리 방향

향후 도메인 구조 예시:

```text
wifi_config/
  service.py
  scanner.py
  connector.py
  recovery.py
  dto.py
```

테스트는 아래와 같이 대응한다.

```text
tests/domains/wifi_config/
```

## 7. 보안 및 접근 제한

- 외부 인터넷에 직접 노출하지 않는다.
- 기본적으로 로컬망 또는 장비 AP 모드에서만 접근 허용한다.
- 관리 UI는 최소한의 인증 또는 물리적 접근 전제를 검토한다.
- 입력 검증 없이 셸 명령으로 연결 문자열을 조합하지 않는다.

## 8. 장애 복구 시나리오

필수 복구 시나리오:

- 새 Wi-Fi 연결 실패 시 이전 프로파일 복원
- 네트워크 단절 시 AP 모드 전환
- 설정 페이지 재접속 가능 보장

권장 흐름:

1. 새 SSID 연결 시도
2. health check 또는 서버 reachability 확인
3. 실패 시 기존 연결 복구
4. 복구도 실패하면 AP 모드 시작

## 9. AP 모드 기준

예시 방향:

- SSID: `MatterHub-Setup-<device_id>`
- 로컬 접속 주소 고정
- 설정 완료 후 AP 모드 종료

## 10. 테스트 포인트

- Wi-Fi 스캔 결과 파싱
- SSID 연결 명령 구성
- 잘못된 비밀번호 처리
- 이전 연결 롤백
- AP 모드 진입
- 상태 조회 API 응답 형식
