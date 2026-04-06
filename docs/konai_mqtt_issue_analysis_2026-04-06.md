# MatterHub MQTT 연결 안정성 이슈 분석 및 해결

## 1. 보고된 증상

> "재부팅 이후에 update/reported/~ 로 entity_changed 가 안올라오고 있습니다!!
> 재부팅을 몇번 했더니 갑자기 entity_changed가 올라오는데,
> 이마저도 몇분이 지나면 연결이 끊깁니다.
> entity_changed가 올라오는 동안 update/delta/~를 보내도 로그에 변화도 없고, 실제로 응답도 오지 않습니다.
> bootstrap은 허브 부팅시마다 1회 올라오는게 아닌가요? 이것도 안올라옵니다~!"

## 2. 원인 분석 (5가지)

### 원인 1: 부팅 후 MQTT 연결 실패 → 서비스 crash (Critical)

**위치**: `mqtt.py:128`

부팅 직후 네트워크가 완전히 준비되기 전에 MQTT 연결을 시도합니다. AWS IoT Core TLS 핸드셰이크가 타임아웃(10초)되면 5회 재시도 후 서비스가 crash합니다. systemd가 재시작하지만 같은 조건에서 또 crash하여 무한 반복됩니다.

→ **재부팅 후 entity_changed/bootstrap 안올라옴**: 서비스가 crash loop에 빠져 MQTT 연결 자체가 안 됨

### 원인 2: 재연결 5회 실패 후 영구 포기 (Critical)

**위치**: `mqtt_pkg/runtime.py:229-231`

연결이 끊긴 후 재연결을 5회(`MAX_RECONNECT_ATTEMPTS=5`) 시도하고 모두 실패하면 더 이상 재연결을 시도하지 않습니다. 서비스는 running 상태이지만 MQTT가 연결되지 않은 zombie 상태가 됩니다.

→ **몇 분 후 연결 끊기면 복구 불가**: 5회 재연결 실패 후 영구 포기

### 원인 3: 끊김 감지 지연 (High)

**위치**: `mqtt_pkg/runtime.py:98`, `mqtt.py:150`

- MQTT keep_alive가 300초(5분)로 설정되어 서버 측 끊김 감지에 최대 5분 소요
- 연결 상태 체크 주기가 60초로 끊김 후 최대 59초간 무음 실패

→ **연결 끊긴 줄 모르고 계속 발행 시도**: 5분간 감지 못함

### 원인 4: delta 수신 불가 (High)

연결이 끊긴 상태에서는 `update/delta/~` 토픽의 메시지를 수신할 수 없습니다. 또한 재연결 후에도 토픽 재구독이 보장되지 않아 delta 메시지를 받지 못합니다.

→ **delta 보내도 응답 없음**: 연결 끊김 + 재구독 미수행

### 원인 5: 부팅 시 entity_changed 미발행 (Medium)

**위치**: `mqtt_pkg/state.py:175-180`

기존에는 dedup window(3초) 기반으로 중복 제거했으나, 부팅 후 첫 호출에서 모든 entity를 발행한 뒤 동일 상태가 유지되면 3초 후에도 계속 반복 발행하는 구조였습니다. 이를 상태 변화 기반으로 변경하여, 부팅 시 최초 1회는 전체 발행하고 이후에는 값이 변할 때만 발행하도록 수정했습니다.

→ **bootstrap/entity_changed 발행 동작 개선**

## 3. 해결 내용

| 원인 | 해결 | 효과 |
|------|------|------|
| 부팅 후 crash | 네트워크 대기 + 서비스 레벨 무한 재시도 | crash loop 방지 |
| 재연결 영구 포기 | 무한 재시도 + 점진적 백오프 (10~300초) | zombie 서비스 방지 |
| 끊김 감지 지연 | keep_alive 300→30초, check 60→30초 | 30초 이내 감지 |
| delta 수신 불가 | 재연결 후 자동 재구독 보장 | delta 응답 복구 |
| entity_changed 미발행 | 부팅 시 1회 전체 발행, 이후 변화 시만 | 안정적 발행 |

## 4. 패치 적용 방법

장비에 SSH 접속 후:

```bash
bash /opt/matterhub/app/device_config/patch_mqtt_stability.sh
```

롤백:
```bash
bash /opt/matterhub/app/device_config/patch_mqtt_stability.sh --rollback
```

상세 가이드: `konai_mqtt_patch_guide_2026-04-06.md` 참조
