# MatterHub MQTT 연결 안정성 패치 적용 가이드

## 대상

`konai/20260211-v1.1` 브랜치로 설치된 MatterHub 장비

## 패치 내용

| 항목 | 패치 전 | 패치 후 |
|------|---------|---------|
| 부팅 후 연결 | 5회 실패 시 서비스 crash | 네트워크 대기 + 무한 재시도 |
| 연결 끊김 후 | 5회 실패 시 영구 포기 | 무한 재시도 (10~300초 백오프) |
| 끊김 감지 속도 | 최대 5분 | 최대 30초 |
| 발행 실패 로그 | 없음 (silent) | 구조화된 로그 출력 |

## 사용법

### 1. 장비에 SSH 접속

```bash
ssh whatsmatter@<장비IP>
```

### 2. 패치 실행

```bash
cd /opt/matterhub/app
bash device_config/patch_mqtt_stability.sh
```

### 3. (선택) 미리보기 — 실제 변경 없이 계획만 확인

```bash
bash device_config/patch_mqtt_stability.sh --dry-run
```

## 패치 적용 후 예상 동작

### 부팅 직후

```
[MQTT][INIT] network_ready=false retry_after=10s    ← 네트워크 대기
[MQTT][INIT] network_ready=true                      ← 네트워크 준비 완료
[MQTT][CONNECT] attempting connection try=1/5        ← MQTT 연결 시도
[MQTT][CONNECT][FAIL] try=1/5 error=TimeoutError     ← 실패해도
[MQTT][CONNECT] service_retry attempt=1 ...          ← crash 없이 재시도
[MQTT][CONNECT][OK] connected to broker              ← 최종 연결 성공
```

### 연결 끊김 후

```
[MQTT][CONNECT][INTERRUPTED] error=...               ← 끊김 감지 (30초 이내)
[MQTT][ENTITY_CHANGED][SKIP] reason=disconnected     ← 끊긴 동안 로그 출력
[MQTT][RECONNECT] attempt=1                          ← 즉시 재연결 시도
[MQTT][RECONNECT] attempt=6 backoff_delay=10s        ← 5회 초과 시 백오프
[MQTT][CONNECT][OK] connected to broker              ← 재연결 성공
[MQTT][RECONNECT] resubscribe after session loss     ← 토픽 재구독
```

## 검증 방법

```bash
# 실시간 로그 확인
journalctl -u matterhub-mqtt.service -f

# 최근 1분 로그 확인
journalctl -u matterhub-mqtt.service --since "1 minute ago"

# 서비스 상태 확인
systemctl status matterhub-mqtt.service
```

**정상 동작 확인 포인트**:
1. `[MQTT][CONNECT][OK] connected to broker` 로그 존재
2. `entity_changed` 로그가 주기적으로 출력
3. `[FAIL] reason=max_attempts_exceeded` 로그 **없음** (패치 전 증상)

## 롤백 방법

패치 후 문제 발생 시:

```bash
bash device_config/patch_mqtt_stability.sh --rollback
```

자동으로 백업된 파일을 복원하고 서비스를 재시작합니다.

## 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `mqtt.py` | 네트워크 대기, 무한 재시도, 체크 주기 30초 |
| `mqtt_pkg/runtime.py` | 재연결 무한 백오프, keep_alive 30초, resubscribe |
| `mqtt_pkg/publisher.py` | is_connected 체크, 구조화된 로그 |
| `mqtt_pkg/state.py` | 연결 끊김 로그 (30초 빈도 제한) |

## 문의

문제 원인 상세 분석: `docs/konai_mqtt_issue_analysis_2026-04-06.md` 참조
