# MatterHub 납품 및 운영 런북

## 1. 목적

본 문서는 납품 전후 운영자가 따라야 할 기본 절차를 정의한다.

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [리팩터링 로드맵](../refactoring-roadmap.md)

## 2. 납품 전 체크리스트

- 대상 장비 아키텍처 확인
- Ubuntu 24.04 LTS 버전 확인
- 필수 패키지 설치 여부 확인
- 기본 네트워크 연결 확인
- MatterHub 패키지 설치 확인
- systemd 서비스 enable 상태 확인
- 초기 프로비저닝 완료 확인

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
- `venv` 생성 및 Python 의존성 설치
- `NetworkManager` 제어 권한(polkit) 설치
- systemd 유닛 렌더링 및 설치
- 서비스 enable/restart

reverse tunnel 설정까지 동시에 진행하려면 아래 옵션을 사용한다.

```bash
cd /home/whatsmatter/Desktop/matterhub
RELAY_HUB_ACCESS_PUBKEY="$(ssh -i ~/.ssh/matterhub-relay-operator-key.pem -p 443 ec2-user@3.38.126.167 'cat /home/ec2-user/.ssh/hub_access_ed25519.pub')"
bash device_config/install_ubuntu24.sh \
  --setup-support-tunnel \
  --support-host 3.38.126.167 \
  --support-user whatsmatter \
  --support-relay-operator-user ec2-user \
  --support-relay-access-pubkey "$RELAY_HUB_ACCESS_PUBKEY"
```

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

### Wi-Fi 변경 지원

1. 로컬 설정 페이지 접속
2. SSID 스캔
3. 새 네트워크 연결 시도
4. 연결 상태 확인
5. 실패 시 롤백 또는 AP 복구 모드 사용

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

예시:

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/build_matterhub_deb.sh --version 2026.03.05 --mode pyc
```
