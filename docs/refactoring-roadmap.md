# MatterHub 리팩터링 로드맵

## 1. 목적

본 문서는 납품용 구조 전환을 어떤 순서로 진행할지 정의한다.

모든 단계는 아래 문서를 함께 참조한다.

- 상위 기준: [라즈베리파이 납품용 패키징 및 운영 기획서](./raspberry-pi-delivery-plan.md)
- 승인 절차: [개발 워크플로우](./development-workflow.md)
- 검증 기준: [테스트 전략](./testing-strategy.md)

## 2. 단계별 진행 순서

### 단계 1. 런타임 구조 정리

목표:

- 현재 `레거시 프로세스 매니저 + 셸 스크립트 + 다중 백그라운드 프로세스` 구조를 `systemd` 중심 구조로 전환할 준비를 한다.
- 실행 코드, 설정, 데이터, 로그 경계를 명확히 나눈다.

참조 문서:

- [systemd 서비스 설계](./architecture/systemd-service-design.md)

산출물:

- 목표 디렉토리 구조 정의
- 서비스 단위 분리안
- 환경 변수 및 파일 경로 정리안

### 단계 2. 원격 유지보수 구조 전환

목표:

- `MQTT -> git pull` 구조를 제거하고 `MQTT -> support tunnel open` 구조로 전환한다.

참조 문서:

- [Reverse SSH Tunnel 설계](./remote-maintenance/reverse-ssh-tunnel-design.md)

산출물:

- 지원 모드 활성화 API 또는 메시지 규격
- reverse tunnel 실행/종료 절차
- 유지보수 계정 및 키 관리 정책

### 단계 3. Wi-Fi 설정 기능 구축

목표:

- 고객사 Linux 로그인 없이 Wi-Fi를 변경할 수 있도록 로컬 설정 페이지와 내부 API를 만든다.

참조 문서:

- [Wi-Fi 설정 Web UI 설계](./network/wifi-config-webui-design.md)

산출물:

- SSID 스캔 API
- 연결 변경 API
- AP 복구 모드
- 연결 실패 롤백 절차

### 단계 4. 패키징 체계 구축

목표:

- Python 소스 직접 배포 대신 컴파일 산출물과 `.deb` 패키지 기반 배포로 전환한다.

참조 문서:

- [.deb 패키징 설계](./packaging/deb-packaging-design.md)

산출물:

- 빌드 스크립트
- 패키지 디렉토리 레이아웃
- `postinst`/`prerm`/`postrm` 정책
- 업그레이드 및 롤백 절차

### 단계 5. 운영 검증 및 납품 기준 확정

목표:

- 설치, 재부팅, 네트워크 변경, 유지보수 접속, 업그레이드, 복구까지 전체 흐름을 검증한다.

참조 문서:

- [납품 및 운영 런북](./operations/delivery-runbook.md)

산출물:

- 운영 체크리스트
- 장애 대응 시나리오
- 납품 전 점검표

## 3. 구현 게이트

각 단계는 아래 조건을 만족해야 다음 단계로 이동한다.

- 관련 문서가 최신 상태일 것
- 코드 수정 전에 작업 범위를 승인받을 것
- 실제 도메인 디렉토리와 대응되는 테스트 디렉토리에 테스트가 작성될 것
- 테스트 실행 결과가 기록될 것
- 문서와 실제 구현이 어긋나지 않을 것

## 4. 도메인 기준 분리 원칙

리팩터링이 본격화되면 기능 단위는 아래 도메인 중심으로 정리한다.

- `system_runtime`
- `support_tunnel`
- `wifi_config`
- `packaging`
- `operations`

각 도메인은 테스트 디렉토리와 1:1로 매칭한다.

예시:

```text
src/domains/system_runtime   <-> tests/domains/system_runtime
src/domains/support_tunnel   <-> tests/domains/support_tunnel
src/domains/wifi_config      <-> tests/domains/wifi_config
```

현행 레거시 구조를 수정하는 동안에는 현재 패키지 경로도 동일 규칙으로 대응한다.

예시:

```text
mqtt_pkg/                    <-> tests/mqtt_pkg/
sub/                         <-> tests/sub/
app.py                       <-> tests/test_app.py
```
