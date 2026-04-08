# HA WebSocket 실기기 통합 테스트

## 이슈 요약

WebSocket(실시간 감지) + PERIODIC(30초 주기 발행)이 실기기에서 안정적으로 동시 동작하는지 검증.

## 테스트 환경

- 장비: 192.168.1.101, Ubuntu 24.04, Python 3.12
- WebSocket: `websockets` 16.0 (asyncio)
- 센서: Smart HT Sensor (온도/습도, Matter/Thread)

## 테스트 결과

### 1. 5분간 안정성 — 정상
| 항목 | 결과 |
|------|------|
| POLL | 60회/5분 (5초 간격) ✅ |
| PERIODIC | 4회/5분 (30초 간격) ✅ |
| WS 연결 | 유지됨 (`entity_filter=42`) ✅ |
| NRestarts | 0 ✅ |

### 2. HA 재시작 → WebSocket 자동 재연결 — 정상
- HA Docker 재시작 → WS 끊김(`ConnectionRefusedError`)
- **5초 후 자동 재연결** 성공
- 재연결 직후 entity_changed 이벤트 수신 확인 ✅

### 3. MQTT 재시작 → 전체 동작 — 정상
- Bootstrap 34 entities 발행 ✅
- WS 연결 + POLL + PERIODIC 동시 기동 ✅
- 에러 없음 ✅

## 배포 여부

- 테스트 장비 배포 완료, 정상 동작 확인

## 다음 단계

Phase 4: 커밋 + 코나이 v4 패치 생성
