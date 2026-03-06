# MatterHub Wi-Fi 설정 방법 (운영 가이드)

## 1. 목적

이 문서는 고객사 또는 현장 운영자가 MatterHub 장비의 Wi-Fi를 변경할 때 사용하는 실제 절차를 정의한다.

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [MatterHub Wi-Fi 설정 Web UI 설계](./wifi-config-webui-design.md)

## 2. 핵심 개념

- Wi-Fi 설정 페이지는 별도 프론트 서버가 아니라 MatterHub Flask 서비스가 직접 제공한다.
- 장비 부팅 시 `matterhub-api.service`가 자동 실행되며 Wi-Fi 설정 페이지도 함께 열린다.
- 인터넷이 없는 초기 상태에서는 장비가 AP(핫스팟) 모드로 진입할 수 있다.

## 3. 접속 주소 규칙

### 3.1 일반 모드(장비가 기존 Wi-Fi에 연결된 상태)

- 접속 URL: `http://<라즈베리파이_IP>:8100/local/admin/network`
- 예시: `http://192.168.1.94:8100/local/admin/network`

### 3.2 AP 모드(초기 설치/네트워크 장애 복구 상태)

- AP SSID 기본값: `Matterhub-Setup-WhatsMatter`
- AP 게이트웨이 기본값: `10.42.0.1`
- 접속 URL: `http://10.42.0.1:8100/local/admin/network`

현재 구현은 AP 주소를 `10.42.0.1/24`로 고정하도록 설정되어 있다.

## 3.3 신규 장비 초기 통합 설치 권장 명령

신규 라즈베리파이에는 아래 통합 스크립트를 우선 사용한다.

```bash
cd /home/whatsmatter/Desktop/matterhub
bash device_config/setup_initial_device.sh
```

이 스크립트는 다음을 한 번에 수행한다.

- Wi-Fi/AP 기본값을 `.env`에 반영
- `install_ubuntu24.sh` 호출
- `network-manager`, `nmcli`, AP/polkit 권한 설정 포함
- `openssh-server` 설치 및 `ssh` 서비스 `enable --now`
- venv/requirements/systemd 서비스 설치 및 재시작

## 4. 고객사 사용 절차

### 4.1 장비가 이미 Wi-Fi에 연결된 경우

1. 같은 네트워크에 PC/모바일을 연결한다.
2. 브라우저에서 `http://<라즈베리파이_IP>:8100/local/admin/network` 접속한다.
3. 페이지에서 주변 Wi-Fi를 스캔하거나 SSID/Password를 입력해 연결을 변경한다.

### 4.2 장비가 Wi-Fi에 연결되지 않은 초기/장애 상태

1. PC/모바일에서 `Matterhub-Setup-WhatsMatter` SSID에 연결한다.
2. 브라우저에서 `http://10.42.0.1:8100/local/admin/network` 접속한다.
3. 대상 SSID/Password를 입력해 연결한다.
4. 연결 성공 후 장비가 대상 Wi-Fi로 붙으면 기존 AP 연결은 끊길 수 있다.

## 5. 화면에서 제공되는 기능

- 네트워크 상태 조회
- 주변 Wi-Fi 스캔
- Wi-Fi 연결 시도(연결 확인 포함)
- 저장된 연결 목록 조회/삭제
- AP 복구 모드 수동 시작

### 5.1 복구 모드(AP) 설명

복구 모드는 "현재 Wi-Fi가 안 잡히거나 설정을 처음부터 다시 해야 할 때" 사용하는 기능이다.

- 복구 모드 시작 시 장비가 임시 Wi-Fi(AP)를 연다.
- 사용자는 해당 AP에 접속해서 설정 페이지로 다시 들어올 수 있다.
- 기본 접속 주소는 `http://10.42.0.1:8100/local/admin/network` 이다.
- 정상 Wi-Fi 연결이 완료되면 복구 모드는 더 이상 필요하지 않다.

## 6. 실패 시 동작(롤백/복구)

Wi-Fi 연결 시 내부 동작은 아래 순서다.

1. 새 SSID 연결 시도
2. health check 확인
3. 실패 시 이전 연결 프로필 롤백 시도
4. 롤백 실패 시 AP 모드 시작

즉, 잘못된 설정으로 장비가 완전히 고립되는 상황을 줄이도록 설계되어 있다.

## 7. 운영자 점검 API

- `GET /local/admin/network/status`
- `GET /local/admin/network/wifi/scan`
- `POST /local/admin/network/wifi/connect`
- `GET /local/admin/network/wifi/saved`
- `DELETE /local/admin/network/wifi/saved/<connection_name>`
- `POST /local/admin/network/recovery/ap-mode`

## 8. 주요 환경 변수

- `WIFI_INTERFACE` (기본: `wlan0`)
- `WIFI_HEALTH_HOST` (기본: `8.8.8.8`)
- `WIFI_AP_SSID` (기본: `Matterhub-Setup-WhatsMatter`)
- `WIFI_AP_PASSWORD` (기본: `matterhub1234`)
- `WIFI_AP_IPV4_CIDR` (기본: `10.42.0.1/24`)
- `WIFI_AUTO_AP_ON_BOOT` (기본: `true`)
- `WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS` (기본: `45`, 부팅 직후 AP 전환 전 대기시간)
- `WIFI_AUTO_AP_ON_DISCONNECT` (기본: `true`)
- `WIFI_AP_DISCONNECT_GRACE_SECONDS` (기본: `20`)
- `WIFI_AP_AUTO_RECONNECT_ENABLED` (기본: `true`)
- `WIFI_AP_AUTO_RECONNECT_INTERVAL_SECONDS` (기본: `15`)
- `WIFI_AP_AUTO_RECONNECT_TIMEOUT_SECONDS` (기본: `20`)
- `WIFI_BOOTSTRAP_AP_SSID` (선택)
- `WIFI_BOOTSTRAP_AP_PASSWORD` (선택)

## 8.1 재부팅 후 로그인해야만 Wi-Fi/터널이 붙는 경우

원인 후보:
- 저장된 Wi-Fi 프로필이 사용자 세션 의존(`connection.permissions` 설정)인 경우
- 비밀번호가 사용자 keyring에만 저장되어 부팅 단계에서 자동연결이 실패하는 경우

즉시 조치:

```bash
PROFILE_NAME="현재 연결 프로필명"
WIFI_PASSWORD="현재 Wi-Fi 비밀번호"
sudo nmcli connection modify "$PROFILE_NAME" connection.permissions "" connection.autoconnect yes
sudo nmcli connection modify "$PROFILE_NAME" 802-11-wireless-security.psk-flags 0 802-11-wireless-security.psk "$WIFI_PASSWORD"
sudo nmcli connection up "$PROFILE_NAME"
```

확인:

```bash
nmcli -f NAME,UUID,TYPE,AUTOCONNECT connection show
```

## 9. 권장 운영 정책

- 출고 전 `WIFI_AP_PASSWORD`를 고객사 정책에 맞는 값으로 변경한다.
- Wi-Fi 설정 페이지는 외부 인터넷으로 직접 노출하지 않는다.
- 장비 인수 시 AP 모드 접속 테스트를 1회 수행한다.

## 10. CS 검증 체크리스트

현장 CS에서 반드시 확인할 항목:

1. 중간에 Wi-Fi가 끊겼을 때
- `WIFI_AP_DISCONNECT_GRACE_SECONDS` 동안 자동 복구 시도 후,
- 복구 실패 시 AP 모드(`Matterhub-Setup-...`) 진입 여부 확인

2. 끊겼다가 기존에 알고 있는 네트워크가 다시 살아났을 때
- watchdog가 저장된 Wi-Fi 프로필(`autoconnect=yes`)로 자동 재연결 시도
- AP가 이미 떠 있는 상태에서도 주기적으로 known network 재연결 시도

3. 재부팅 후 로그인하지 않아도 동작하는지
- Wi-Fi profile이 system-level(`connection.permissions=""`) + `autoconnect=yes`인지 확인
- reverse tunnel이 자동으로 다시 올라오는지 확인
