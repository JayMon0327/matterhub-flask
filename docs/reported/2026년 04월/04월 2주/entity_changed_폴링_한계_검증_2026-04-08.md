# entity_changed 폴링 감지 한계 검증

## 이슈 요약

현재 entity_changed 발행은 5초마다 HA REST API를 폴링하여 state 문자열 비교 방식.
센서값이 안 변하면 발행이 안 되고, 변해도 최대 5초 지연. 93%의 폴링이 무의미.

## 검증 결과

| 항목 | 결과 |
|------|------|
| POLL 동작 | 5초마다 정상 (`status=200 entities=2`) |
| PERIODIC 동작 | 30초마다 정상 (`2 entities 발행 완료`) |
| ENTITY_CHANGED 감지율 | 244회 POLL 중 17회 감지 (**7%**) |
| 감지 지연 | 최대 5~25초 (폴링 주기 + Matter 리포트 주기) |
| HA WebSocket | 연결/인증/구독 모두 성공, push 방식 전환 가능 |

## 결론

폴링 방식의 한계 확인. HA WebSocket `state_changed` 이벤트 기반으로 전환하여 실시간 감지 + 리소스 절감 필요.

## 다음 단계

Phase 2: HA WebSocket 연동 구현
