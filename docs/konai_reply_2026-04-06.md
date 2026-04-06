# 코나이 MQTT 이슈 답변

안녕하세요, 보고해주신 MQTT 연결 불안정 이슈 분석 및 패치 완료했습니다.

---

## 문제 원인

총 5가지 원인이 복합적으로 작용하고 있었습니다.

### 1. 부팅 후 MQTT 연결 실패 시 서비스 crash

라즈베리파이 재부팅 직후 네트워크가 완전히 준비되기 전에 MQTT 연결을 시도합니다.
AWS IoT Core TLS 핸드셰이크가 타임아웃되면 5회 재시도 후 **서비스가 crash**하고,
systemd가 재시작해도 같은 조건에서 또 crash하여 **무한 반복**됩니다.

→ 재부팅 후 entity_changed, bootstrap 모두 안올라오는 원인

### 2. 연결 끊김 후 재연결 5회 실패 시 영구 포기

MQTT 연결이 끊긴 후 재연결을 5회만 시도하고, 모두 실패하면 **더 이상 재연결을 시도하지 않습니다.**
서비스는 running 상태이지만 MQTT가 연결되지 않은 상태(zombie)가 됩니다.

→ 몇 분 후 연결 끊기면 영구적으로 복구 불가. delta 보내도 응답 없음.

### 3. 연결 끊김 감지 지연 (최대 5분)

MQTT keep_alive가 5분(300초)으로 설정되어 있어, 서버 측 연결 끊김을 감지하는 데 최대 5분이 걸립니다.
그 동안 entity_changed 발행이 무음으로 실패합니다.

### 4. 재연결 후 토픽 재구독 미보장

MQTT가 자동 재연결되더라도 `update/delta/~` 토픽에 대한 **재구독이 보장되지 않아**
delta 메시지를 수신하지 못합니다.

### 5. 부팅 시 entity_changed 발행 로직 이슈

기존에는 시간 기반 dedup window(3초)로 중복 제거를 했는데,
부팅 후 첫 호출에서 발행한 뒤 동일 상태가 유지되면 이후에도 반복 발행되는 구조였습니다.
이를 **상태 변화 기반**으로 변경하여, 부팅 시 최초 1회는 전체 발행하고 이후에는 값이 변할 때만 발행하도록 수정했습니다.

---

## 해결 내용

| 항목 | 패치 전 | 패치 후 |
|------|---------|---------|
| 부팅 후 연결 실패 | 5회 실패 → 서비스 crash | 네트워크 대기 + **무한 재시도** (crash 없음) |
| 연결 끊김 후 | 5회 재연결 실패 → **영구 포기** | 점진적 백오프(10~300초)로 **무한 재시도** |
| 끊김 감지 | 최대 **5분** | 최대 **30초** |
| 재연결 후 구독 | 재구독 미보장 | **자동 재구독** 보장 |
| 부팅 시 entity_changed | 발행 로직 불안정 | 부팅 시 **1회 전체 발행**, 이후 변화 시만 발행 |
| 장애 진단 | 로그 없음 | 구조화된 로그 출력 |

---

## 패치 적용 방법

장비에 SSH 접속 후 아래 명령을 한 줄로 복사-붙여넣기 실행합니다:

> 첨부 파일 `konai_patch_oneliner_2026-04-06.txt`의 내용을 그대로 붙여넣으세요.

- 자동으로 기존 파일 백업 후 패치 적용 + 서비스 재시작
- 백업 위치: `/opt/matterhub/app/.patch_backup_YYYYMMDD_HHMMSS/`
- 롤백 필요 시 백업 디렉토리에서 `.pyc` 파일을 복원하면 됩니다

### 패치 후 정상 로그 예시

```
[MQTT][INIT] network_ready=true                  ← 네트워크 대기 후 연결
[MQTT][CONNECT][OK] connected to broker           ← 1회 시도로 성공
bootstrap_all_states ... 34 entities              ← 부팅 시 전체 상태 발행
entity_changed: sensor.smart_ht_sensor_ondo       ← 온도 센서 변화 발행
entity_changed: sensor.smart_ht_sensor_seubdo     ← 습도 센서 변화 발행
```

### 검증 방법

```bash
# 서비스 상태 확인
systemctl status matterhub-mqtt.service

# 실시간 로그 확인
journalctl -u matterhub-mqtt.service -f

# entity_changed 발행 확인
journalctl -u matterhub-mqtt.service --since "5 minutes ago" | grep entity_changed
```

---

## 참고: 변경된 파일 (4개)

| 파일 | 변경 내용 |
|------|-----------|
| `mqtt.py` | 네트워크 대기, 무한 재시도, 연결 체크 30초 |
| `mqtt_pkg/runtime.py` | 재연결 무한 백오프, keep_alive 30초, 자동 재구독 |
| `mqtt_pkg/publisher.py` | 연결 상태 체크 강화, 진단 로그 |
| `mqtt_pkg/state.py` | 부팅 시 1회 전체 발행, 이후 변화 시만 발행 |
