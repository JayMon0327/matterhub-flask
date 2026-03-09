# MatterHub 납품형 허브 표준 기획서 (현재 프로젝트 기준)

## 1. 문서 목적

본 문서는 MatterHub를 고객에게 "전용 어플라이언스" 형태로 납품하기 위한 표준 운영/배포/보안/유지보수 기준을 정의한다.

핵심 목표:

- 고객은 전용 Wi-Fi 설정 UI만 사용한다.
- 고객은 Ubuntu 계정/SSH/콘솔로 접근할 수 없다.
- 서비스는 `systemd`로 자동기동/자동복구된다.
- 유지보수는 reverse tunnel 기반으로 수행한다.
- 배포는 소스(`.py`)가 아닌 실행파일 중심으로 전환한다.
- 업데이트는 번들 교체 + 롤백 구조로 운영한다.

## 2. 전제 조건

- OS: Ubuntu 24.04 LTS (Raspberry Pi)
- Docker 미사용
- PM2 미사용, `systemd` 사용
- 고객사 SSID는 사전 고정 불가
- 고객은 부팅 후 로컬 Wi-Fi 설정 페이지를 통해 SSID/비밀번호 입력

고객에 비공개여야 하는 영역:

- SSH 접속
- OS 계정/패스워드
- 쉘 명령
- 내부 실행 구조/로그 전체
- Python 원본 코드

## 3. 운영 모델

고객 허용 행위:

- 전원 인가
- 로컬 설정 UI 접속
- Wi-Fi 설정/변경
- 상태 확인
- 네트워크 초기화(허용 시)

제조사 허용 행위:

- reverse tunnel 기반 원격 점검
- 업데이트 파일 배포/적용
- 서비스 재기동/로그 점검
- 장애 복구

## 4. 표준 아키텍처

- OS 레벨 접근 차단
- 로컬 Wi-Fi provisioning UI
- systemd 서비스 분리 운영
- reverse tunnel 원격 유지보수
- 업데이트 번들 교체 + 롤백
- 실행파일 기반 배포(소스 비배포)

## 5. 구성요소 정의 (현재 용어 기준)

애플리케이션 서비스:

1. `matterhub-api.service`
2. `matterhub-rule-engine.service`
3. `matterhub-notifier.service`
4. `matterhub-mqtt.service`
5. `matterhub-support-tunnel.service`
6. `matterhub-update-agent.service`

Provisioning 서비스 정의:

- 논리 서비스명: `matterhub-provision`
- 현재 구현 위치: `app.py` + `wifi_config/*` + `templates/wifi_admin.html`
- 즉, 현재는 별도 `matterhub-provision.service`가 아니라 `matterhub-api.service` 내부 기능으로 동작

## 6. 디렉토리/경로 원칙 (중요)

이 문서는 "디렉토리 구조 개편"을 목표로 하지 않는다.

현재 운영 경로(개발/검증 기준):

- `/home/whatsmatter/Desktop/matterhub` 프로젝트 루트
- `.env`, `device_config/*`, `wifi_config/*`, `mqtt_pkg/*`, `templates/*` 기준 운영

패키징 단계 참고 경로:

- `device_config/build_matterhub_deb.sh`의 패키지 내부 경로(`/opt/matterhub`)는 패키지 산출물 관점에서만 유지
- 본 단계에서 코드/서비스의 대규모 경로 전환은 범위에서 제외

## 7. 실행파일 배포정책 (핵심)

### 7.1 기본 정책

- 고객 장비에 Python 원본(`.py`) 배포 금지
- Git 저장소 비배포
- 실행파일 중심 배포
- 업데이트도 실행파일 단위 교체

### 7.2 이미지/설치 방식

- Raspberry Pi Imager/installer 기반 납품 이미지 사용
- 납품 이미지는 고객에게 OS 운영권한을 주지 않는 appliance 모드 구성

### 7.3 빌드 방향

- PyInstaller 또는 Nuitka 기반 실행파일 생성
- 초기에는 서비스별 `--onedir` 우선
- 안정화 후 일부 서비스 `--onefile` 검토

### 7.4 코드 노출 저감

- 디버그 심볼/문자열 최소화
- 민감정보 로그 금지
- 인증서/키 권한 최소화

## 8. systemd 서비스 구조

필수 서비스:

- `matterhub-api.service`
- `matterhub-mqtt.service`
- `matterhub-rule-engine.service`
- `matterhub-notifier.service`
- `matterhub-support-tunnel.service`
- `matterhub-update-agent.service`

원칙:

- `Restart=always` 또는 `on-failure`
- 부팅 자동기동
- `EnvironmentFile`/`.env` 연동
- 서비스별 책임 분리

## 9. 네트워크 모드 표준

- `STA 모드`: 고객 Wi-Fi 연결 상태
- `AP 모드`: 설정/복구용 핫스팟

AP 진입 조건:

- 저장된 Wi-Fi 없음
- 저장된 Wi-Fi 연결 실패 누적
- 네트워크 초기화 요청
- 강제 AP 전환 정책

## 10. Wi-Fi Provisioning 표준 동작

초기 부팅:

1. 저장된 Wi-Fi 확인
2. 있으면 STA 연결 시도
3. 성공 시 정상 운영
4. 실패 시 AP 모드 진입

AP 모드 UX:

- SSID: `Matterhub-Setup-WhatsMatter` (기본값)
- 우선 접속: `http://matterhub-setup-whatsmatter.local:8100/local/admin/network`
- fallback 접속: `http://10.42.0.1:8100/local/admin/network`
- 연결 성공 시 STA 전환

## 11. `matterhub-provision` 상세 정의 (현재 구현 반영)

### 11.1 역할

- Wi-Fi 상태 조회
- 주변 SSID 스캔
- Wi-Fi 연결/롤백
- AP 시작/복구
- 저장된 연결 관리

### 11.2 사용 기술

- Flask 라우트 (`app.py` + `wifi_config/api.py`)
- `NetworkManager` + `nmcli`
- 프론트엔드: `templates/wifi_admin.html`

### 11.3 비기능 요구사항

- 고객 OS 로그인 없이 사용 가능
- 실패 시 AP 복구 보장
- 비밀번호는 앱 내부 평문 중복 저장 금지

## 12. Provisioning API 기준 (현재 경로)

- `GET /local/admin/network/status`
- `GET /local/admin/network/wifi/scan`
- `POST /local/admin/network/wifi/connect`
- `GET /local/admin/network/wifi/saved`
- `DELETE /local/admin/network/wifi/saved/<connection_name>`
- `POST /local/admin/network/recovery/ap-mode`

## 13. Provisioning 상태머신

상태:

- `BOOTING`
- `STA_CONNECTING`
- `STA_CONNECTED`
- `STA_FAILED`
- `AP_STARTING`
- `AP_MODE`

주요 전이:

- `BOOTING` + saved wifi -> `STA_CONNECTING`
- `STA_CONNECTING` + success -> `STA_CONNECTED`
- `STA_CONNECTING` + fail -> `STA_FAILED`
- `STA_FAILED` + retry_exceeded -> `AP_STARTING` -> `AP_MODE`
- `AP_MODE` + valid wifi submit -> `STA_CONNECTING`

## 14. OS 접근 보안정책

- root 로그인 금지
- 패스워드 SSH 금지
- 고객용 쉘 계정 미제공
- 물리 콘솔 로그인 차단
- inbound 포트 최소화

## 15. 업데이트 구조

표준:

- `tar.gz` 업데이트 번들 수신
- staging 전개
- 바이너리 교체
- 서비스 재기동
- 실패 시 자동 롤백

예시 파일명:

- `matterhub-update-1.2.3.tar.gz`

현재 1차 구현 스크립트:

- `device_config/apply_update_bundle.sh`
  - `payload/` 기준 파일 오버레이
  - 실패 시 롤백(백업 복원) 지원
  - `--healthcheck-cmd` 실패 시 자동 롤백

## 16. reverse tunnel 유지보수 표준

### 16.1 원칙

- 장비 -> relay 서버 outbound 연결
- 고객망 inbound 포트 개방 없음
- 운영자는 relay 경유로만 접근

### 16.2 로그인 전 tunnel 실패 대응

필수 점검 조건:

- Wi-Fi profile: `connection.permissions=""`, `autoconnect=yes`
- Wi-Fi secret: `psk-flags=0` (로그인 세션 의존 제거)
- relay host key: `known_hosts` 사전 등록
- `matterhub-support-tunnel.service` enable 상태 확인

### 16.3 대체 전략

1. 자동로그인 활성화 + 물리콘솔 비노출
- 세션 의존 문제를 우회하나 보안/운영 복잡도 상승

2. Ubuntu Server 재설치
- GUI/keyring 의존 제거에 유리, headless 운영 안정성 높음

정책:

- 로그인 전 tunnel 실패가 반복되면 2번(서버 전환) 우선 검토

## 17. MAC 바인딩 정책 (추가)

목표:

- 허용된 MAC이 아닌 장비에서 실행파일이 동작하지 않도록 제한

구현 원칙:

1. 허용 MAC 목록을 `config`에 저장(서명 권장)
2. 실행 시작 preflight에서 MAC 검증
3. 불일치 시 서비스 즉시 종료 + 보안 로그 기록

현재 1차 구현 환경변수:

- `MAC_BINDING_ENABLED=1|0`
- `MAC_BINDING_ALLOWED=aa:bb:cc:dd:ee:ff,11:22:33:44:55:66`
- `MAC_BINDING_ALLOWED_FILE=/path/to/allowed_macs.txt` (선택)
- `MAC_BINDING_INTERFACE=wlan0` (선택)

주의:

- MAC 바인딩은 복제 억제책이며 단독 대책으로는 불충분
- SD 탈착 후 코드 열람 자체를 완전 차단하지 못함
- 실행파일 배포 + 권한 하드닝 + 업데이트 검증과 함께 적용

## 18. 데이터/비밀값 관리 원칙

- 설정/상태/로그 경로 분리
- Wi-Fi 비밀번호는 OS 네트워크 매니저 위임 우선
- 앱 내부 중복 평문 저장 금지

## 19. 로그 정책

- 운영 필수 로그만 유지
- 민감정보 마스킹
- 서비스별 식별 가능한 prefix 유지

## 20. 고객용 UI 요구사항

필수:

- 상태 표시
- Wi-Fi 스캔
- Wi-Fi 설정/변경
- 네트워크 초기화
- 장비 식별자/버전 표시
- 오류 메시지 가이드

비노출:

- shell 접근
- 디버그 메뉴
- 내부 운영 엔드포인트

## 21. 운영 절차 요약

초기 설치:

1. 전원 인가
2. AP 또는 기존 STA 확인
3. Wi-Fi 설정 페이지 접속
4. SSID/비밀번호 입력
5. 정상 운영 확인

장애 대응:

- STA 실패 누적 시 AP 자동 전환
- 서비스 장애 시 systemd 자동 재시작
- 업데이트 실패 시 롤백

## 22. 구현 우선순위

1. 실행파일 배포 전환
2. `matterhub-provision` 안정화
3. reverse tunnel 로그인 전 실패 케이스 제거
4. 업데이트 번들/롤백 구현
5. MAC 바인딩 적용

## 23. 승인 후 구현 항목

1. `matterhub-provision` 상세 API/화면 명세 확정
2. reverse tunnel 사전검증/자동복구 로직 반영
3. 실행파일 빌드/배포 파이프라인 구성
4. 업데이트 번들 적용 스크립트 작성
5. MAC 바인딩 검증 모듈 구현
