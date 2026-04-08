# HA WebSocket 연동 구현

## 이슈 요약

기존 폴링(5초마다 HA REST API) 방식은 93%가 무의미한 호출이고, 센서값 변화 감지가 최대 5초 지연.
HA WebSocket `state_changed` 이벤트를 구독하여 실시간 감지로 전환.

## 변경 파일 목록

| 파일 | 변경 유형 |
|------|-----------|
| `mqtt_pkg/state.py` | 수정 — WebSocket 리스너 추가, 폴링 entity_changed 제거 |
| `mqtt.py` | 수정 — `start_ha_websocket_listener()` 호출 추가 |

## 수정 내용

### 1. WebSocket 리스너 (`start_ha_websocket_listener`)
- `websockets` (asyncio) 기반, 별도 daemon 스레드에서 실행
- HA WebSocket(`ws://HA_HOST/api/websocket`) 연결 → 인증 → `state_changed` 이벤트 구독
- `KONAI_REPORT_ENTITY_IDS`에 포함된 entity만 필터링
- 변화 감지 시 즉시 `entity_changed` 발행
- 연결 끊기면 5초 후 자동 재연결

### 2. 발행 구조 분리
- **WebSocket**: `entity_changed` (실시간 변화 감지)
- **폴링**: `periodic_state` (30초마다 무조건 발행)
- 중복 발행 제거 (폴링에서 entity_changed 감지 로직 제거)

### 3. 로그 체계
- `[MQTT][WS] connected entity_filter=N` — WebSocket 연결
- `[MQTT][WS][ENTITY_CHANGED] entity 값→값` — 실시간 감지
- `[MQTT][POLL] status=200 entities=N` — 5초 폴링
- `[MQTT][PERIODIC] N entities 발행 완료` — 30초 주기적 발행

## 배포 여부

- 테스트 장비(192.168.1.101) 배포 완료, 정상 동작 확인

## 테스트 결과

- WebSocket 연결 성공: `connected entity_filter=42` ✅
- 즉시 감지: `sensor.smart_ht_sensor_ondo 26.41→25.91` (폴링 대비 5초 빠름) ✅
- 주기적 발행: 30초마다 `PERIODIC 2 entities` ✅
- 중복 발행 제거 확인 ✅
- 단위 테스트 16건 통과 ✅
