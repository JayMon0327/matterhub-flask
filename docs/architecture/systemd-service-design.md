# MatterHub systemd 서비스 설계

## 1. 목적

본 문서는 납품 장비에서 MatterHub 런타임을 `systemd` 중심으로 운영하기 위한 기준을 정의한다.

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [리팩터링 로드맵](../refactoring-roadmap.md)

## 2. 설계 목표

- `PM2` 제거
- 서비스 단위 분리
- 자동 재시작과 부팅 시 자동 기동 지원
- 로그 조회 단순화
- 권한 최소화

## 3. 권장 서비스 구성

초기 분리 후보는 아래와 같다.

- `matterhub-api.service`
- `matterhub-mqtt.service`
- `matterhub-rule-engine.service`
- `matterhub-notifier.service`
- `matterhub-support-tunnel.service`

상황에 따라 일부 서비스는 통합할 수 있으나, 그 경우에도 API와 support tunnel은 분리 유지하는 것을 원칙으로 한다.

## 4. 권장 디렉토리 레이아웃

```text
/opt/matterhub/bin
/etc/matterhub
/var/lib/matterhub
/var/log/matterhub
```

원칙:

- 실행 파일은 `/opt/matterhub/bin`
- 설정 파일은 `/etc/matterhub`
- 상태 데이터는 `/var/lib/matterhub`
- 운영 로그는 journald 또는 `/var/log/matterhub`

## 5. 실행 계정

- 전용 시스템 사용자 예: `matterhub`
- 로그인 셸 없는 계정 사용
- `sudo` 권한 미부여
- 필요한 디렉토리에만 쓰기 권한 부여

## 6. 보안 옵션

가능한 한 아래 옵션을 적용한다.

- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateTmp=true`
- `ReadWritePaths=/etc/matterhub /var/lib/matterhub /var/log/matterhub`
- `Restart=always`
- `RestartSec=3`

실제 적용 전에는 서비스별 쓰기 경로를 명확히 정의해야 한다.

## 7. 단위 파일 예시 방향

```ini
[Unit]
Description=MatterHub API
After=network-online.target
Wants=network-online.target

[Service]
User=matterhub
Group=matterhub
WorkingDirectory=/opt/matterhub
EnvironmentFile=/etc/matterhub/matterhub.env
ExecStart=/opt/matterhub/bin/matterhub-api
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/etc/matterhub /var/lib/matterhub /var/log/matterhub

[Install]
WantedBy=multi-user.target
```

## 8. 로그 전략

- 기본은 `journalctl` 조회
- 장기 보존이 필요하면 별도 로그 파일 또는 원격 수집 추가
- 서비스별 식별 가능한 로그 prefix 유지

## 9. 구현 시 검토 항목

- 각 현재 Python 프로세스가 독립 서비스여야 하는지 여부
- 설정 파일 경로 하드코딩 제거
- PID 파일 의존 제거
- 셸 래퍼 없이 직접 실행 가능한 엔트리포인트 확보

## 10. 테스트 포인트

- 부팅 후 자동 시작
- 프로세스 비정상 종료 시 자동 재시작
- 설정 파일 누락 시 실패 로그 명확성
- 읽기 전용 파일 시스템 환경에서의 보호 동작
- 서비스별 로그 분리 가능성

## 11. 현재 구현 단계의 적용 방식

현재 리팩터링 1단계에서는 `.deb` 패키지 대신 Git 배포 상태의 프로젝트 경로에서 직접 `systemd`를 설정한다.

적용 스크립트:

- `device_config/install_ubuntu24.sh`

동작 개요:

- Ubuntu 24.04 필수 패키지 설치
- 프로젝트 루트 기준 `venv` 생성 및 `requirements.txt` 설치
- `device_config/systemd/matterhub-service.service.template` 기반 유닛 렌더링
- `/etc/systemd/system` 아래에 유닛 설치
- `systemctl daemon-reload`, `enable`, `restart` 수행

이 단계는 개발/검증용 Git 배포 흐름이며, 최종 납품 단계에서는 별도 `.deb` 패키징 구조로 전환한다.
