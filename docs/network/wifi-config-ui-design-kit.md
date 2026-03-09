# MatterHub Wi-Fi 설정 UI 디자인 키트

## 1. 목적

본 문서는 MatterHub Wi-Fi 설정 페이지의 시각 기준과 컴포넌트 규칙을 고정한다.

목표는 아래 두 가지를 동시에 만족하는 것이다.

- 고객이 납품 장비에서 처음 보는 화면이어도 바로 이해할 수 있는 UX
- Toss 계열 서비스처럼 단정하고 신뢰감 있는 시각 언어

상위 문서:

- [라즈베리파이 납품용 패키징 및 운영 기획서](../raspberry-pi-delivery-plan.md)
- [MatterHub Wi-Fi 설정 Web UI 설계](./wifi-config-webui-design.md)

## 2. 분석 기준

디자인 기준은 아래 공식 페이지를 비교해 추출했다.

1. `https://toss.im/`
2. `https://toss.im/en`
3. `https://business.toss.im/`
4. `https://pay.toss.im/`
5. `https://support.toss.im/`
6. `https://privacy.toss.im/`
7. `https://bugbounty.toss.im/`

## 3. Toss 공통 패턴 요약

### 3.1 색상

공통 축은 밝은 회색 배경 + 흰색 카드 + 강한 블루 포인트다.

주요 토큰:

- 배경: `#f2f4f6`, `#f9fafb`
- 카드/표면: `#ffffff`
- 본문 텍스트: `#191f28`, `#333d4b`
- 보조 텍스트: `#4e5968`, `#6b7684`, `#8b95a1`
- 경계선: `#e5e8eb`, `#d1d6db`
- 주요 액션: `#3182f6`, pressed `#1b64da`

보조 상태색도 명확하다.

- 성공: 녹색 계열
- 경고: 주황 계열
- 오류: 빨강 계열

### 3.2 위계

- 제목은 크고 짧다.
- 설명은 1~2문장으로 제한한다.
- 한 섹션 안에는 하나의 주행동만 강조한다.
- 숫자, 상태, 현재 값은 별도 카드나 pill로 분리한다.

### 3.3 레이아웃

- 화면 전체는 넓은 여백을 사용한다.
- 카드 반경이 크다.
- 그림자보다 배경 명도 대비로 레이어를 나눈다.
- 섹션은 hero -> 상세 카드 -> 보조 정보 순서로 흐른다.

### 3.4 컴포넌트 표현

- 버튼은 채움/고스트/약한 보조형으로 구분한다.
- 상태는 pill/chip로 짧게 표시한다.
- 리스트 아이템은 카드처럼 분리한다.
- 아이콘은 단색이고 연한 배경 배지 위에 올린다.

### 3.5 카피 톤

- 용어는 짧고 직접적이다.
- 사용자가 해야 할 행동을 먼저 말한다.
- 기술 구현 설명보다 결과와 다음 행동을 우선 보여준다.

## 4. MatterHub 전용 해석 규칙

Toss 패턴을 그대로 복제하지 않고, 로컬 provisioning 환경에 맞게 아래 규칙으로 변환한다.

### 4.1 오프라인 우선

- AP 모드에서는 인터넷이 없을 수 있으므로 외부 폰트/CDN 의존 금지
- 이미지 없이도 읽히는 구성 유지
- CSS/JS는 페이지 내부 또는 로컬 정적 자산만 사용

### 4.2 한국어 우선

- 고객용 문구는 한국어 우선
- 예외 용어는 `SSID`, `Password` 정도로 제한

### 4.3 단일 화면 이해

- 데스크톱에서는 스크롤 없이 핵심 상태, 주변 Wi-Fi, 복구 모드가 보이도록 구성
- 모바일에서는 카드가 세로로 자연스럽게 재배열되어야 한다

### 4.4 상태 가시성

- 현재 모드(`AP`, `Wi-Fi 연결`, `연결 시도 중`)를 hero 상단 chip으로 노출
- provisioning 상태머신은 별도 chip과 상태 카드로 중복 노출
- 연결 성공/실패/롤백/AP 전환은 배너로 즉시 알려준다
- 사용자가 다시 접속할 수 있도록 고정 로컬 호스트명(`.local`)을 hero 영역에 노출한다

### 4.5 보안 표현

- SSID는 사용자 입력값이 아니므로 반드시 escape 후 렌더링
- 주변 Wi-Fi 이름과 저장된 연결 이름은 `innerHTML` 직삽입 금지

## 5. 토큰 정의

### 5.1 Color

```text
--bg: #f2f4f6
--bg-accent: #e8f3ff
--surface: #ffffff
--surface-muted: #f9fafb
--line: #e5e8eb
--line-strong: #d1d6db
--text: #191f28
--text-soft: #4e5968
--text-muted: #8b95a1
--primary: #3182f6
--primary-pressed: #1b64da
--positive: #03b26c
--warning: #fe9800
--danger: #f04452
```

### 5.2 Radius

```text
shell/card: 28px ~ 36px
control: 16px ~ 18px
pill: 999px
```

### 5.3 Shadow

```text
soft: 0 18px 48px rgba(2, 32, 71, 0.08)
card: 0 10px 24px rgba(0, 27, 55, 0.06)
```

### 5.4 Motion

- 등장 애니메이션: 180ms ~ 500ms
- 버튼 hover: translateY(-1px) 이하
- 연결 대기 상태: spinner + progress sweep 조합
- 과한 bounce/scale 금지

## 6. 컴포넌트 규칙

### 6.1 Hero Card

- 제품명, 한 줄 설명, 주요 행동 버튼 2개만 둔다
- 오른쪽에는 상태 요약 카드 배치

### 6.2 Status Chip

- 짧은 상태만 표시
- 색상으로 의미를 보조하되 텍스트 없이 의미 전달하지 않는다

### 6.3 Wi-Fi List Item

- 아이콘 배지 + SSID + 상태 pill + 선택 버튼 구조
- 현재 연결된 항목은 선택 버튼 대신 `현재 연결됨` pill 표시

### 6.4 Modal

- 선택한 Wi-Fi 정보를 카드처럼 먼저 보여준다
- Password 입력과 연결 버튼을 가까이 배치한다
- 연결 중에는 진행 상태와 안내 문구를 유지한다

### 6.5 Recovery Card

- 복구 모드가 필요한 상황을 먼저 설명한다
- AP SSID / AP Password 입력은 보조 설정으로 배치한다
- CTA는 하나만 강조한다

### 6.6 Notice Banner

- 사용자가 방금 한 작업의 결과를 페이지 상단에 즉시 표시한다
- 성공/경고/실패를 색상과 문구로 동시에 구분한다

## 7. 현재 페이지 적용 원칙

현재 `templates/wifi_admin.html`은 아래 원칙을 따라야 한다.

- Toss 계열 neutral + blue palette 적용
- 외부 폰트 제거
- 주변 Wi-Fi / 상태 / 복구 모드를 3개 핵심 블록으로 정리
- 저장된 네트워크는 modal로만 노출
- 연결은 modal 기반으로 진행
- 연결 진행 중 안내 배너와 modal 상태 텍스트를 동시에 유지
- 스캔 결과와 저장된 네트워크 렌더링 시 escape 처리

## 8. 검증 기준

UI 변경 후 아래를 확인한다.

1. Flask 템플릿이 정상 렌더링되는가
2. 외부 폰트 URL이 템플릿에 남아 있지 않은가
3. 주변 Wi-Fi 이름에 HTML 특수문자가 들어가도 안전한가
4. 데스크톱/모바일에서 카드 재배치가 무너지지 않는가
5. 연결 modal에서 10초 타임아웃 안내가 유지되는가
