# MQTT Split Topic 장애 정리 (2026-03-05)

## 1. 결론 요약
- 현재 증상은 코드 단순 버그보다 `update/delta/...` 구독 권한/정책 또는 broker 측 동작 이슈 가능성이 높다.
- 동일 인증서/동일 endpoint/동일 client_id 조건에서:
  - `update/delta/...` 구독은 반복 실패(Timeout + unexpected hangup)
  - `update/reported/...` 구독/발행은 성공 케이스 확인됨
- 따라서 split topic 구조 자체는 코드에서 유지 가능하지만, request 경로(delta)가 broker 단에서 막히는 상태로 보인다.

## 2. 현재 구조(의도)
- request 수신: `KONAI_TOPIC_REQUEST` (`update/delta/...`) 구독
- response/event 발행: `KONAI_TOPIC_RESPONSE` (`update/reported/...`) 발행
- entity_changed / bootstrap_all_states 는 `reported` 토픽으로 발행

## 3. 관측된 현상
- `matterhub-mqtt.service` startup에서 delta 구독 실패 로그가 반복됨.
- probe 테스트에서 response(reported) 구독은 성공.
- reported 발행 로그(`publish_result ... status=success`) 및 entity_changed 발행 로그 확인.

## 4. 검증 결과(라즈베리파이 실측)

### 4.1 토픽 probe
명령:
```bash
venv/bin/python device_config/mqtt_probe.py --topic-mode both --listen-seconds 0
```
결과:
- request(delta): failed
- response(reported): success

### 4.2 이벤트 발행 경로
검증 스크립트에서 `state.publish_bootstrap_all_states()` 및 `state.publish_device_state()` 호출 시:
- `publish_result topic=update/reported/... status=success type=bootstrap_all_states`
- `publish_result topic=update/reported/... status=success type=entity_changed`
- `코나이 entity_changed ... -> update/reported/...` 로그 확인

## 5. 코드 측 비원인 정리
- 인증서 경로 오설정 아님: `konai_certificates`에서 cert/key/ca 존재 확인.
- reported 발행 코드 경로 자체는 동작 확인됨.
- split-topic 매핑(request=delta, response=reported) 설정값 로드 정상.

## 6. 코나아이/AWS 측 점검 요청 항목
- 인증서 정책에서 `update/delta/dev/.../matter/k3O6TL`에 대해 `iot:Subscribe`, `iot:Receive`가 허용되는지
- 해당 client_id(`c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL`)에 delta 구독 제한이 걸려있는지
- reported는 허용, delta는 차단되는 비대칭 정책이 존재하는지
- broker가 delta subscribe 시 연결을 끊는 사유(권한 거부/정책 불일치) 확인 가능 여부

## 7. 현재 코드 로그 포맷 변경 사항
- INIT/CONNECT/SUBSCRIBE 태그 기반으로 통일해 가독성 개선
- 구독 결과를 토픽별로 명시:
  - `[MQTT][SUBSCRIBE][OK] topic[n]=...`
  - `[MQTT][SUBSCRIBE][FAIL] topic[n]=...`

---

## 코나아이 전달용 요약 메시지
안녕하세요. 동일 인증서/동일 client_id 환경에서 `update/reported/...`는 subscribe/publish가 성공하지만 `update/delta/...` subscribe 시 `AWS_ERROR_MQTT_UNEXPECTED_HANGUP` 및 timeout으로 실패하고 있습니다. 장비 코드 측 split-topic 매핑 및 reported 이벤트 발행은 정상 확인되어, delta 토픽에 대한 정책(`iot:Subscribe`/`iot:Receive`) 또는 broker 측 차단 여부 확인 부탁드립니다.
