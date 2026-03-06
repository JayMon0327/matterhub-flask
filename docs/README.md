# MatterHub 납품 문서 인덱스

## 1. 목적

이 디렉토리는 라즈베리파이 + Ubuntu 24.04 LTS 납품 구조를 문서 기준으로 통제하기 위한 참조 문서 모음이다.

문서는 아래 순서대로 읽고 참조한다.

## 2. 권장 참조 순서

1. [라즈베리파이 납품용 패키징 및 운영 기획서](./raspberry-pi-delivery-plan.md)
2. [리팩터링 로드맵](./refactoring-roadmap.md)
3. [개발 워크플로우](./development-workflow.md)
4. [테스트 전략](./testing-strategy.md)
5. [systemd 서비스 설계](./architecture/systemd-service-design.md)
6. [Reverse SSH Tunnel 설계](./remote-maintenance/reverse-ssh-tunnel-design.md)
7. [Reverse SSH Tunnel 빠른 적용 가이드](./remote-maintenance/reverse-ssh-tunnel-quickstart.md)
8. [EC2 Relay Server Setup](./remote-maintenance/ec2-relay-setup.md)
9. [리버스 터널 접속방법](./remote-maintenance/reverse-tunnel-access-method.md)
10. [Wi-Fi 설정 Web UI 설계](./network/wifi-config-webui-design.md)
11. [Wi-Fi 설정 방법 (운영 가이드)](./network/wifi-setup-guide.md)
12. [.deb 패키징 설계](./packaging/deb-packaging-design.md)
13. [납품 및 운영 런북](./operations/delivery-runbook.md)
14. [Reverse Tunnel Only 하드닝 가이드](./operations/reverse-tunnel-only-hardening.md)
15. [MQTT 토픽 진단 로그 (2026-03-04)](./operations/mqtt-topic-diagnosis-2026-03-04.md)
16. [라즈베리파이 반복 설치 가이드](./operations/repeatable-raspi-setup.md)

## 3. 문서 사용 규칙

- 모든 구현은 먼저 [라즈베리파이 납품용 패키징 및 운영 기획서](./raspberry-pi-delivery-plan.md)를 기준 문서로 삼는다.
- 실제 작업 순서는 [리팩터링 로드맵](./refactoring-roadmap.md)을 따른다.
- 코드 수정 전 승인 절차와 완료 기준은 [개발 워크플로우](./development-workflow.md)를 따른다.
- 테스트 디렉토리 구성과 검증 기준은 [테스트 전략](./testing-strategy.md)를 따른다.
- 상세 설계가 필요한 경우에는 해당 도메인 문서를 우선 참조한다.

## 4. 설계 변경 원칙

- 구현 중 문서와 다른 방향이 더 적합하다고 판단되면 바로 코드로 가지 않는다.
- 먼저 변경 사유, 장단점, 영향 범위를 사용자에게 제안한다.
- 승인 후 문서를 선행 수정하고, 그 다음 구현을 진행한다.

## 5. 현재 바로 실행할 스크립트

Git 기반 개발 배포 단계에서 라즈베리파이에 적용할 통합 스크립트:

- `device_config/setup_initial_device.sh`
- `device_config/install_ubuntu24.sh`
