# MatterHub 테스트 전략

## 1. 목적

본 문서는 기능 구현 시 테스트 디렉토리 구조와 검증 기준을 정의한다.

## 2. 핵심 원칙

- 테스트 구조는 실제 코드 구조와 1:1 대응을 유지한다.
- 기능 구현 시 테스트 코드를 함께 작성한다.
- 테스트를 실행해 정상 동작이 확인되어야 완료로 판단한다.
- 문서 기준에서 벗어나는 기능은 테스트가 있더라도 완료로 보지 않는다.

## 3. 디렉토리 매핑 규칙

### 3.1 디렉토리 매핑

실제 코드 디렉토리가 있으면 동일한 상대 경로를 `tests/` 아래에 만든다.

예시:

```text
src/domains/wifi_config/         -> tests/domains/wifi_config/
src/domains/support_tunnel/      -> tests/domains/support_tunnel/
mqtt_pkg/                        -> tests/mqtt_pkg/
sub/                             -> tests/sub/
```

### 3.2 파일 매핑

모듈 파일은 테스트 파일로 직접 대응시킨다.

예시:

```text
app.py                           -> tests/test_app.py
mqtt_pkg/update.py               -> tests/mqtt_pkg/test_update.py
sub/notifier.py                  -> tests/sub/test_notifier.py
```

## 4. 테스트 계층

### 단위 테스트

- 순수 함수
- 메시지 파싱
- 경로/설정 생성
- 명령 조합 로직
- 예외 처리

### 계약 테스트

- MQTT payload 형식
- 내부 API 요청/응답 형식
- `nmcli` 래퍼 입력/출력 규격
- tunnel 명령 파라미터 규격

### 통합 테스트

- Flask endpoint와 내부 서비스 연결
- 설정 저장과 로딩 흐름
- systemd 또는 외부 명령 호출 래퍼의 동작 검증

### 스모크 테스트

- 핵심 프로세스 기동
- Wi-Fi 설정 기본 흐름
- 지원 모드 활성화 흐름
- 패키지 설치 후 기본 시작 확인

## 5. 권장 도구

구현이 시작되면 테스트 기본 도구는 아래를 기준으로 한다.

- `pytest`
- `pytest-mock` 또는 `unittest.mock`
- `monkeypatch`
- 임시 디렉토리 fixture

외부 의존성은 직접 호출하지 않고 mock 또는 wrapper를 통해 테스트한다.

테스트 대상 예시:

- `subprocess.run`
- `nmcli`
- `ssh` 또는 `autossh`
- 파일 시스템 접근
- MQTT publish/subscribe

## 6. 완료 기준

기능 단위 완료 기준은 다음과 같다.

- 대응 테스트 파일이 존재할 것
- 정상 경로 테스트가 있을 것
- 실패 경로 테스트가 있을 것
- 외부 명령 실패 시 처리 테스트가 있을 것
- 실제 테스트 실행 결과를 확인했을 것

## 7. 리팩터링 중 적용 규칙

- 레거시 파일을 수정하면 현재 경로에 맞는 테스트를 먼저 또는 함께 추가한다.
- 이후 도메인 분리가 이뤄지면 테스트 경로도 같은 구조로 이동한다.
- 코드 구조가 바뀌어도 1:1 매핑 원칙은 유지한다.

## 8. 우선 작성 대상

리팩터링 초기에 우선 테스트해야 할 영역은 아래와 같다.

- 업데이트 명령 파싱 및 실행 제어
- 설정 파일 로딩/저장
- Wi-Fi 설정 명령 래퍼
- support tunnel 활성화/종료 로직
- Flask 내부 관리 API
