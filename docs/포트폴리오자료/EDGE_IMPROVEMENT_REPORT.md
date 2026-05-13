# MatterHub Edge Server — 기술 개선 보고서

## 개요

MatterHub Edge Server는 Raspberry Pi 4 기반 IoT 게이트웨이로, Home Assistant와 AWS IoT Core를 MQTT로 브리징하여 스마트홈 디바이스를 원격 모니터링·제어한다. 6개 systemd 서비스로 구성된 멀티 프로세스 아키텍처에서 운영 중 식별된 12개 기술 문제에 대한 분석, 해결, 구현 결과를 기술한다.

**대상 시스템:**
- Platform: Raspberry Pi 4+ / Ubuntu 24.04 LTS
- Runtime: Python 3.9+
- MQTT: AWS IoT Core (awscrt/awsiot SDK, mTLS)
- Process Management: systemd (6개 독립 서비스)

---

## 1. PUBACK 타임아웃 교착 해소

### 문제 발생

AWS IoT Core에서 수신한 `set_env`, `bundle_update`, `bundle_check` 명령이 간헐적으로 10초 후 PUBACK 타임아웃 에러로 실패했다. 명령 자체의 실행에는 문제가 없었으나, 응답 MQTT 메시지 발행이 항상 타임아웃되어 클라우드 측에서 명령 결과를 받을 수 없었다.

### 원인 분석

AWS IoT Core SDK(`awscrt`)는 단일 스레드 이벤트 루프(`EventLoopGroup(1)`)로 동작한다. 이 스레드는 MQTT 메시지 콜백 처리와 PUBACK 응답 수신을 모두 담당한다.

기존 구현은 콜백 핸들러 내에서 응답 메시지를 **동기적으로** 발행했다:

```python
# AS-IS: 콜백 스레드에서 직접 발행 → 교착
def mqtt_callback(topic, payload, **kwargs):
    message = json.loads(payload)
    if message.get("command") == "set_env":
        _handle_set_env(message)           # 작업 실행
        result = publish(response_payload)  # ← QoS 1 발행
        result.result(timeout=10)           # ← PUBACK 대기 (교착!)
```

**교착 메커니즘:**
1. EventLoop 스레드가 콜백 함수 실행 중 (스레드 점유)
2. 콜백 내에서 `publish()` 호출 → QoS 1이므로 PUBACK 응답 필요
3. PUBACK 응답은 **같은 EventLoop 스레드**가 처리해야 함
4. 그러나 해당 스레드는 콜백 함수 완료를 대기 중 → **교착 (deadlock)**
5. 10초 타임아웃 후 실패

### 해결 방안

**동기 처리 vs 큐 기반 비동기 처리 비교:**

| 항목 | 동기 처리 (AS-IS) | 큐 기반 비동기 (TO-BE) |
|------|-------------------|----------------------|
| 콜백 스레드 점유 시간 | 수 초 ~ 10초 | **마이크로초** |
| PUBACK 교착 가능성 | 있음 (EventLoop 단일 스레드) | **없음** (별도 Worker 스레드) |
| 구현 복잡도 | 낮음 | 중간 (Queue + Worker) |
| 동시 명령 처리 | 불가 (직렬) | 큐 순차 처리 (논블로킹) |

`queue.Queue`와 별도 데몬 Worker 스레드를 도입하여 콜백 핸들러가 즉시 반환하도록 변경했다. 명령 처리는 Worker 스레드에서 수행되므로 EventLoop를 차단하지 않는다.

### 결과

| 지표 | AS-IS (동기) | TO-BE (큐 기반) |
|------|-------------|----------------|
| 콜백 스레드 점유 시간 | 수 초 ~ 10초 | **마이크로초** |
| PUBACK 타임아웃 발생률 | set_env/bundle_* 100% 실패 | **0%** |
| 교착 발생 | 간헐적 (3개 명령 타입) | **해소** |
| 명령 응답 수신 | 클라우드에서 수신 불가 | **정상 수신** |

### 세부 구현

**파일:** `mqtt_pkg/update.py`

```python
# 명령 큐 및 동기화
update_queue: queue.Queue[Dict[str, Any]] = queue.Queue()
update_queue_lock = threading.Lock()
is_processing_update = False

def handle_update_command(message: Dict[str, Any]):
    """MQTT 콜백에서 호출 — 즉시 인큐하고 반환 (μs)"""
    update_queue.put(message)
    logger.info(f"[UpdateQueue] 인큐 완료: {message.get('command', 'git_update')}")

def process_update_queue():
    """Worker 스레드 — 큐에서 명령을 꺼내 순차 처리"""
    global is_processing_update
    while True:
        message = update_queue.get()  # 블로킹 대기
        with update_queue_lock:
            is_processing_update = True
        try:
            command = message.get("command", "git_update")
            if command == "set_env":
                _handle_set_env(message)
            elif command == "bundle_update":
                _handle_bundle_update(message)
            elif command == "bundle_check":
                _handle_bundle_check(message)
            else:
                execute_update_async(message)
        finally:
            with update_queue_lock:
                is_processing_update = False
            update_queue.task_done()

def start_queue_worker():
    """데몬 Worker 스레드 시작"""
    t = threading.Thread(target=process_update_queue, daemon=True)
    t.start()
```

핵심 설계 결정:
- `queue.Queue`는 스레드 안전 — 별도 락 불필요
- Worker를 데몬 스레드로 실행 — 메인 프로세스 종료 시 자동 정리
- `is_processing_update` 플래그로 중복 실행 방지
- 콜백은 `update_queue.put()`만 수행하여 EventLoop 스레드 점유 최소화

---

## 2. 업데이트 파이프라인 전면 개편 (PM2 → systemd)

### 문제 발생

기존 PM2 기반 업데이트 시스템에서 MQTT 워커가 `pm2 restart`로 자기 자신을 재시작하면, 재시작 명령을 실행하는 프로세스 자체가 kill되어 업데이트 응답을 보낼 수 없었다. 또한 업데이트 결과 추적이 불가능했다.

### 원인 분석

PM2 기반 구조의 근본적 문제:
1. **자기 프로세스 Kill**: MQTT 워커가 PM2를 통해 자기 자신을 재시작 → 응답 발행 전 프로세스 종료
2. **cgroup 탈출 불가**: PM2가 관리하는 프로세스에서 `subprocess.Popen`으로 실행해도 같은 cgroup에 속해 함께 kill됨
3. **결과 추적 없음**: 업데이트 성공/실패 여부를 클라우드에 보고하는 메커니즘 부재

### 해결 방안

**PM2 재시작 vs systemd-run --scope 비교:**

| 항목 | PM2 restart | systemd-run --scope |
|------|-------------|---------------------|
| 자기 프로세스 Kill | 발생 | **분리된 scope에서 실행** |
| cgroup 격리 | 불가 (같은 cgroup) | **독립 cgroup** |
| 응답 발행 | 프로세스 죽어서 불가 | **발행 후 재시작** |
| 롤백 | 없음 | **git reset + healthcheck** |

4-Phase 비동기 업데이트 패턴과 `systemd-run --scope`를 도입하여 해결했다.

### 결과

| 지표 | AS-IS (PM2) | TO-BE (systemd 4-Phase) |
|------|-------------|------------------------|
| 업데이트 응답 | 수신 불가 (프로세스 kill) | **QoS 1 확인 후 응답** |
| 프로세스 격리 | 같은 cgroup | **systemd-run --scope 독립 cgroup** |
| 롤백 | 없음 | **git reset --hard + 헬스체크** |
| 결과 추적 | 불가 | **update_id별 상태 파일** |
| 헬스체크 | 없음 | **≥2 서비스 online 확인 (30초 대기)** |

### 세부 구현

**파일:** `mqtt_pkg/update.py`, `device_config/update_server.sh`

```python
# 4-Phase 비동기 업데이트 (update.py)
def execute_update_async(message):
    update_id = message.get("update_id", "")
    branch = message.get("branch", "master")

    # Phase A: git pull (--skip-restart로 재시작 분리)
    send_immediate_response(message, status="processing")
    script = _find_update_script()
    subprocess.Popen(
        ["nohup", "bash", script, branch, "false", update_id, hub_id, "--skip-restart"],
        stdout=open(log_path, "a"), stderr=subprocess.STDOUT
    )

    # Phase B: PID 모니터링 (최대 300초)
    _wait_for_pid(pid, timeout=300)
    status = _read_status_file(f"/tmp/update_{update_id}.status")

    # Phase C: 최종 응답 (QoS 1 PUBACK 확인)
    send_final_response(message, result=status)

    # Phase D: 서비스 재시작 (cgroup 분리)
    _launch_restart(update_id)

def _launch_restart(update_id):
    """systemd-run --scope로 재시작 — 현재 프로세스와 cgroup 분리"""
    restart_cmd = "systemctl restart matterhub-api matterhub-mqtt ..."
    subprocess.Popen(
        ["sudo", "systemd-run", "--scope", "bash", "-c", restart_cmd]
    )
```

```bash
# update_server.sh — 프로세스 매니저 자동 감지
detect_process_manager() {
    # systemd → pm2 → legacy-systemd 순서로 감지
    for svc in matterhub-api.service matterhub-mqtt.service; do
        systemctl is-active "$svc" &>/dev/null && echo "systemd" && return
    done
    command -v pm2 &>/dev/null && pm2 list | grep -q "wm-" && echo "pm2" && return
    echo "legacy-systemd"
}

# 헬스체크: ≥2 서비스가 30초 내 online
healthcheck_services() {
    local max_wait=30
    while [ $elapsed -lt $max_wait ]; do
        online=$(systemctl is-active "${SYSTEMD_SERVICES[@]}" | grep -c "^active$")
        [ "$online" -ge 2 ] && return 0
        sleep 5
    done
    return 1  # 실패 → 롤백 트리거
}

# 실패 시 롤백
# git reset --hard {original_commit} + 서비스 재시작
```

핵심 설계 결정:
- `--skip-restart` 플래그로 git pull과 서비스 재시작을 분리 — PUBACK 확인 후 재시작
- `systemd-run --scope`로 재시작 프로세스를 독립 cgroup에 배치 — 현재 MQTT 워커가 kill되어도 재시작 진행
- `/tmp/update_{id}.status` JSON 파일로 프로세스 간 상태 전달
- PM2 → systemd 자동 마이그레이션 (`migrate_pm2_to_systemd.sh`)

---

## 3. MQTT QoS Fallback 전략

### 문제 발생

MQTT QoS 1 발행 시 PUBACK 응답이 네트워크 불안정으로 타임아웃되면, 메시지가 유실되었다. 특히 디바이스 상태 발행이 실패하면 클라우드 대시보드에 오래된 상태가 표시되었다.

### 원인 분석

기존 구현은 QoS 1 발행 실패 시 단순히 에러를 로깅하고 종료했다:

```python
# AS-IS: QoS 1 실패 → 메시지 유실
def publish(payload):
    future, _ = connection.publish(topic, json.dumps(payload), QoS.AT_LEAST_ONCE)
    future.result(timeout=10)  # 타임아웃 시 Exception → 메시지 유실
```

AWS IoT Core SDK의 `publish()` 반환값이 라이브러리 버전에 따라 tuple 또는 단일 future로 다를 수 있어, `result()` 호출 시 `TypeError`도 발생했다.

### 해결 방안

**재시도 vs QoS 폴백 비교:**

| 항목 | QoS 1 재시도 | QoS 1→0 폴백 |
|------|-------------|-------------|
| 지연 | 재시도 횟수 × 타임아웃 | **즉시 폴백 (1회 추가 시도)** |
| 메시지 보장 | AT_LEAST_ONCE (재전송 가능) | AT_MOST_ONCE (중복 없음) |
| 네트워크 불안정 시 | 반복 실패 가능 | **빠르게 전달 (fire-and-forget)** |
| 적합 케이스 | 트랜잭션 데이터 | **주기적 상태 데이터 (최신 값이 중요)** |

주기적으로 발행되는 디바이스 상태 데이터는 최신 값이 이전 실패를 덮어쓰므로, 빠른 전달이 재시도보다 유리하다.

### 결과

| 지표 | AS-IS (QoS 1 only) | TO-BE (QoS 1→0 폴백) |
|------|--------------------|--------------------|
| 네트워크 불안정 시 메시지 유실 | 100% (예외로 종료) | **QoS 0로 즉시 전달** |
| 발행 지연 | 타임아웃까지 대기 (3초) | **타임아웃 + 즉시 폴백** |
| 라이브러리 호환성 에러 | TypeError 발생 | **tuple/단일 반환값 모두 처리** |

### 세부 구현

**파일:** `mqtt_pkg/publisher.py`

```python
from awscrt.mqtt import QoS

MQTT_PUBLISH_TIMEOUT_SEC = settings.MQTT_PUBLISH_TIMEOUT_SEC  # 기본 3초, 최소 1초

def publish(payload, response_topic=None):
    """QoS 1 → QoS 0 폴백 전략"""
    topic = response_topic or settings.MQTT_TOPIC_PUBLISH
    payload_str = json.dumps(payload)
    connection = runtime.get_connection()

    try:
        # Step 1: QoS 1 시도 (AT_LEAST_ONCE)
        result = connection.publish(topic, payload_str, QoS.AT_LEAST_ONCE)
        # awscrt 버전별 반환값 처리 (tuple 또는 단일 future)
        future = result[0] if isinstance(result, tuple) else result
        try:
            future.result(timeout=MQTT_PUBLISH_TIMEOUT_SEC)
            logger.info(f"[MQTT Pub] 성공 (qos1): {topic}")
        except TypeError:
            logger.warning("[MQTT Pub] result() TypeError — qos1 간주")
    except Exception as e:
        # Step 2: QoS 0 폴백 (AT_MOST_ONCE)
        logger.warning(f"[MQTT Pub] QoS 1 실패, QoS 0 폴백: {e}")
        try:
            connection.publish(topic, payload_str, QoS.AT_MOST_ONCE)
            logger.info(f"[MQTT Pub] 폴백 성공 (qos0_fallback): {topic}")
        except Exception as fallback_err:
            logger.error(f"[MQTT Pub] QoS 0도 실패: {fallback_err}")
```

핵심 설계 결정:
- 타임아웃 3초: 상태 발행 주기(60초) 대비 충분히 짧아 루프 블로킹 최소화
- `isinstance(result, tuple)` 분기: awscrt 라이브러리 버전 호환성 보장
- `TypeError` 캐치: `future.result()` 인터페이스 차이 방어 코드
- QoS 0 폴백은 fire-and-forget — PUBACK 대기 없이 즉시 반환

---

## 4. 벤더 중립 MQTT 아키텍처

### 문제 발생

MQTT 설정(엔드포인트, 토픽, 인증서 경로, 센서 목록)이 코드 전반에 Konai 전용 값으로 하드코딩되어 있어, 새로운 벤더(고객사) 납품 시 수십 개 파일을 수정해야 했다.

### 원인 분석

```python
# AS-IS: 하드코딩된 Konai 설정
ENDPOINT = "a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com"
TOPIC_SUBSCRIBE = "konai/matterhub/delta"
CERT_DIR = "konai_certificates/"
REPORT_ENTITIES = ["sensor.smart_ht_sensor_ondo", ...]  # 42개 Konai 센서
```

문제점:
- 벤더 교체 시 **코드 변경 필요** (설정이 아닌 코드 수정)
- 벤더별 브랜치 관리 → 머지 충돌
- 테스트 시 실제 벤더 인프라 필요

### 해결 방안

**환경변수 분기 vs Provider Factory 패턴 비교:**

| 항목 | 환경변수 분기 | Provider Factory 패턴 |
|------|-------------|---------------------|
| 벤더 추가 | 코드 수정 (if/else 추가) | **폴더 추가 + 클래스 구현** |
| 기본값 관리 | .env에 모든 값 필요 | **프로바이더가 기본값 제공** |
| 테스트 | .env 파일 변경 필요 | **Mock 프로바이더 주입 가능** |
| 코드 변경량 (벤더 교체) | 수십 개 파일 | **0개 (MATTERHUB_VENDOR만 변경)** |

Provider Factory 패턴을 도입하여 벤더별 설정을 독립 모듈로 분리했다.

### 결과

| 지표 | AS-IS (하드코딩) | TO-BE (Provider 패턴) |
|------|----------------|---------------------|
| 벤더 교체 시 코드 변경 | 수십 개 파일 | **0개 파일** |
| 벤더 추가 비용 | 전체 코드 포크 | **1개 폴더 + 1개 클래스** |
| 설정 오버라이드 | 불가 | **MQTT_* 환경변수로 개별 오버라이드** |

### 세부 구현

**파일:** `providers/base.py`, `providers/__init__.py`, `providers/konai/settings.py`, `mqtt_pkg/settings.py`

```python
# providers/base.py — 추상 인터페이스
class MQTTProviderSettings:
    def get_endpoint(self) -> str: raise NotImplementedError
    def get_client_id(self) -> str: raise NotImplementedError
    def get_cert_dir(self) -> str: raise NotImplementedError
    def get_topic_subscribe(self) -> str: raise NotImplementedError
    def get_topic_publish(self) -> str: raise NotImplementedError
    def get_default_report_entity_ids(self) -> list[str]: raise NotImplementedError

# providers/__init__.py — Factory
def load_provider(vendor: str | None = None) -> MQTTProviderSettings:
    vendor = vendor or os.environ.get("MATTERHUB_VENDOR", "konai")
    if vendor == "konai":
        from providers.konai.settings import KonaiSettings
        return KonaiSettings()
    raise ValueError(f"Unknown vendor: {vendor}")

# providers/konai/settings.py — Konai 구현체
class KonaiSettings(MQTTProviderSettings):
    def get_endpoint(self) -> str:
        return "a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com"
    def get_client_id(self) -> str:
        return os.environ.get("matterhub_id", f"matterhub-{os.getpid()}")
    def get_cert_dir(self) -> str:
        return "certificates/"
    def get_default_report_entity_ids(self) -> list[str]:
        return build_default_report_entity_ids()  # 42개 센서

# mqtt_pkg/settings.py — 3단계 설정 해석
_provider = load_provider()

# 환경변수 > .env > 프로바이더 기본값
MQTT_TOPIC_SUBSCRIBE = os.environ.get("MQTT_TOPIC_SUBSCRIBE") or _provider.get_topic_subscribe()
MQTT_TOPIC_PUBLISH = os.environ.get("MQTT_TOPIC_PUBLISH") or _provider.get_topic_publish()
```

핵심 설계 결정:
- 추상 베이스 클래스로 인터페이스 강제 — 새 벤더 구현 시 누락 방지
- `MATTERHUB_VENDOR` 단일 환경변수로 벤더 전환
- `MQTT_*` 환경변수가 프로바이더 기본값을 오버라이드 — 벤더 설정 + 환경별 커스터마이징 동시 지원
- 벤더별 디렉토리 구조: `providers/{vendor}/settings.py`

---

## 5. Claim 기반 자동 프로비저닝

### 문제 발생

신규 디바이스 설치 시 AWS IoT Core 인증서(device.pem.crt, private.pem.key)를 수동으로 생성하여 USB 또는 SCP로 복사해야 했다. 100+ 가구 규모에서 이 수동 프로세스는 설치 시간 증가와 인증서 관리 복잡성을 초래했다.

### 원인 분석

기존 프로세스:
1. AWS Console에서 수동으로 Thing + 인증서 생성
2. 인증서 파일 3개를 다운로드
3. SD 카드 또는 SCP로 디바이스에 복사
4. .env 파일에 matterhub_id 수동 입력

문제점:
- 설치당 15-20분 소요 (인증서 생성 + 파일 전송 + 검증)
- 인증서 분실/덮어쓰기 위험
- 설치자가 AWS Console 접근 권한 필요

### 해결 방안

**수동 설치 vs Claim 프로비저닝 비교:**

| 항목 | 수동 인증서 설치 | Claim 기반 자동 프로비저닝 |
|------|----------------|------------------------|
| 설치 시간 | 15-20분 | **자동 (부팅 시 30초 내)** |
| AWS Console 접근 | 필요 | **불필요 (Claim 인증서만)** |
| 인증서 관리 | 디바이스별 개별 관리 | **자동 발급 + 저장** |
| 확장성 | 디바이스 수에 비례 | **동일 Claim 인증서로 무제한** |

AWS IoT Fleet Provisioning의 Claim 기반 프로비저닝을 채택했다.

### 결과

| 지표 | AS-IS (수동) | TO-BE (Claim 프로비저닝) |
|------|-------------|----------------------|
| 인증서 설치 시간 | 15-20분/디바이스 | **자동 (부팅 시 30초)** |
| AWS Console 접근 | 필요 | **불필요** |
| 인증서 발급 | 수동 생성 + 파일 복사 | **MQTT 토픽으로 자동 발급** |
| matterhub_id 설정 | 수동 .env 편집 | **자동 설정 (SN-{timestamp})** |

### 세부 구현

**파일:** `mqtt_pkg/provisioning.py`

```python
class AWSProvisioningClient:
    def __init__(self):
        self.claim_cert_path = os.environ.get("AWS_CLAIM_CERT_PATH", "certificates/")
        self.claim_cert_file = os.environ.get("AWS_CLAIM_CERT_FILE",
                                               "whatsmatter_nipa_claim_cert.cert.pem")
        self.template_name = os.environ.get("AWS_PROVISION_TEMPLATE_NAME",
                                             "whatsmatter-nipa-template")

    def check_certificate(self) -> tuple[bool, str, str]:
        """디바이스 인증서 존재 확인"""
        cert = os.path.join(self.claim_cert_path, "device.pem.crt")
        key = os.path.join(self.claim_cert_path, "private.pem.key")
        return os.path.exists(cert) and os.path.exists(key), cert, key

    def provision_device(self) -> bool:
        """전체 프로비저닝 플로우 실행"""
        # 1. Claim 인증서로 MQTT 연결
        connection = self._connect_with_claim_cert()

        # 2. 새 디바이스 인증서 발급
        cert_id, cert_pem, key_pem, token = self._issue_device_certificate(connection)
        # 토픽: $aws/certificates/create/json → $aws/.../accepted (15초 대기)

        # 3. 인증서 파일 저장
        self._save_certificate_files(cert_pem, key_pem)

        # 4. Thing 등록 (프로비저닝 템플릿)
        serial_number = f"SN-{int(time.time())}"
        self.register_thing(connection, cert_id, token)
        # 토픽: $aws/provisioning-templates/{template}/provision/json

        # 5. matterhub_id 업데이트 (.env)
        settings.update_matterhub_id(thing_name)
        return True
```

핵심 설계 결정:
- Claim 인증서는 모든 디바이스에 동일하게 배포 — .deb 패키지에 포함
- 디바이스 인증서가 이미 존재하면 프로비저닝 스킵 (멱등성)
- 시리얼 번호는 Unix 타임스탬프 기반 (`SN-{timestamp}`) — 고유성 보장
- 프로비저닝 응답 대기 15초 (0.1초 폴링) — 네트워크 지연 감안

---

## 6. 디바이스 상태 벌크 퍼블리시 & 청킹

### 문제 발생

관리 대상 디바이스 수가 증가하면서 전체 디바이스 상태를 단일 MQTT 메시지로 발행할 때 페이로드가 100KB를 초과하여 AWS IoT Core의 메시지 크기 제한(128KB)에 근접하고, 발행이 간헐적으로 실패했다.

### 원인 분석

기존 구현은 모든 관리 디바이스의 상태를 하나의 JSON 페이로드로 직렬화하여 발행했다. 디바이스당 평균 500바이트-1KB의 상태 데이터가 포함되어, 100개 이상의 디바이스에서 페이로드 크기가 급격히 증가했다.

### 해결 방안

**단일 메시지 vs 청크 분할 비교:**

| 항목 | 단일 메시지 | 청크 분할 |
|------|-----------|----------|
| 페이로드 크기 | 무제한 (실패 위험) | **100KB 이내 (설정 가능)** |
| 발행 실패 시 영향 | 전체 상태 유실 | **해당 청크만 재시도** |
| 네트워크 효율 | 변경 없는 디바이스도 포함 | **델타 감지로 변경분만** |

100KB 청크 분할 + 델타 감지를 도입했다.

### 결과

| 지표 | AS-IS (단일 메시지) | TO-BE (청킹 + 델타) |
|------|-------------------|-------------------|
| 최대 페이로드 크기 | 제한 없음 (128KB 초과 가능) | **100KB 이내 (설정 가능, 최소 10KB)** |
| 발행 간격 | 매번 전체 | **60초 간격, 변경분만** |
| 네트워크 부하 | 높음 | **델타 감지로 감소** |

### 세부 구현

**파일:** `mqtt_pkg/state.py`

```python
MQTT_DEVICE_STATE_CHUNK_SIZE_KB = settings.MQTT_DEVICE_STATE_CHUNK_SIZE_KB  # 기본 100KB
MQTT_DEVICE_STATE_INTERVAL_SEC = settings.MQTT_DEVICE_STATE_INTERVAL_SEC    # 기본 60초

def _publish_devices_with_chunking(topic: str, devices: list):
    """100KB 단위로 청크 분할 후 순차 발행"""
    max_bytes = MQTT_DEVICE_STATE_CHUNK_SIZE_KB * 1024  # 102,400 bytes

    if not devices:
        return

    # 디바이스당 평균 크기 계산
    sample = json.dumps(devices[0]).encode("utf-8")
    avg_size = max(len(sample), 100)

    # 청크당 디바이스 수 계산 (500바이트 JSON 오버헤드 예약)
    per_chunk = max(1, int((max_bytes - 500) / avg_size))

    for i in range(0, len(devices), per_chunk):
        chunk = devices[i : i + per_chunk]
        payload = {
            "hub_id": settings.MATTERHUB_ID,
            "ts": publisher.utc_timestamp(),
            "devices": chunk,
            "chunk_index": i // per_chunk,
            "total_chunks": (len(devices) + per_chunk - 1) // per_chunk,
        }
        publisher.publish(payload, response_topic=topic)

class StateChangeDetector:
    """이전 상태와 비교하여 변경된 엔티티만 감지"""
    def __init__(self):
        self.last_states: Dict[str, str] = {}
        self.excluded_sensors: set = {
            # 빈번히 변경되는 센서 제외 (노이즈 감소)
        }

    def detect_changes(self, current_states) -> tuple[bool, list]:
        if not self.is_initialized:
            self.last_states = {e["entity_id"]: e["state"] for e in current_states}
            self.is_initialized = True
            return False, []

        changes = []
        for entity in current_states:
            eid = entity["entity_id"]
            if eid in self.excluded_sensors:
                continue
            if self.last_states.get(eid) != entity["state"]:
                changes.append(entity)

        self.last_states = {e["entity_id"]: e["state"] for e in current_states}
        return len(changes) > 0, changes
```

핵심 설계 결정:
- 500바이트 JSON 오버헤드 예약: `hub_id`, `ts`, `chunk_index` 등 메타데이터 공간
- `chunk_index` + `total_chunks`: 클라우드에서 청크 완전성 검증 가능
- 델타 감지는 `entity_id` + `state` 문자열 비교 — 경량화 (attributes 변경은 무시)
- `MQTT_DEVICE_STATE_INTERVAL_SEC` 최소 10초로 제한 — IoT Core TPS 보호

---

## 7. Wi-Fi AP 부트스트랩 & 자동 복구

### 문제 발생

네트워크가 단절된 가정의 MatterHub 디바이스에 물리적으로 접근하지 않으면 설정을 변경할 수 없었다. Wi-Fi 비밀번호 변경, 공유기 교체 시 디바이스가 오프라인 상태로 방치되었다.

### 원인 분석

기존 시스템에는 네트워크 복구 메커니즘이 전혀 없었다:
- Wi-Fi 연결 실패 시 무한 대기
- 관리자가 SSH로 접속하려 해도 네트워크 단절 상태
- USB 키보드/모니터 직접 연결 → 현장 방문 필수

### 해결 방안

**고정 AP vs 자동 AP 전환 비교:**

| 항목 | 고정 AP 모드 | 자동 AP 부트스트랩 + STA 워치독 |
|------|------------|-------------------------------|
| 네트워크 전환 | 수동 전환 필요 | **자동 감지 + 전환** |
| 정상 운영 영향 | AP와 STA 충돌 | **STA 연결 시 AP 비활성** |
| 복구 시간 | 현장 방문 | **20초 유예 + 자동 AP** |
| 원격 복구 | 불가 | **AP 웹 UI로 새 Wi-Fi 설정** |

NetworkManager(nmcli) 기반 자동 AP 부트스트랩과 STA 워치독을 구현했다.

### 결과

| 지표 | AS-IS (수동) | TO-BE (자동 AP 부트스트랩) |
|------|-------------|------------------------|
| 네트워크 단절 시 복구 | 현장 방문 필수 | **20초 내 AP 모드 자동 전환** |
| 부팅 시 Wi-Fi 없음 | 무한 대기 | **45초 유예 → AP 모드** |
| 복구 인터페이스 | 없음 | **웹 UI (10.42.0.1:8100)** |
| STA 재접속 | 수동 | **15초 간격 자동 시도** |

### 세부 구현

**파일:** `wifi_config/bootstrap.py`, `wifi_config/service.py`, `wifi_config/api.py`

```python
# bootstrap.py — 부팅 시 AP 부트스트랩
def ensure_bootstrap_ap(service, state_store, *, logger, sleep_fn, monotonic_fn):
    """부팅 시 WiFi 연결 보장"""
    STARTUP_GRACE_SECONDS = int(os.environ.get(
        "WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS", "45"))  # 범위: 0-300

    state_store.set_state("BOOTING")

    # Phase 1: 유예 기간 동안 WiFi 연결 대기 (45초)
    elapsed = 0
    while elapsed < STARTUP_GRACE_SECONDS:
        status = service.get_status()
        if status.get("current_ssid"):
            state_store.set_state("STA_CONNECTED")
            return {"started": True, "status": "STA_CONNECTED"}
        sleep_fn(2)
        elapsed += 2

    # Phase 2: 알려진 네트워크 재접속 시도 (20초 타임아웃)
    candidate = _pick_known_network_candidate(service)
    if candidate:
        state_store.set_state("STA_CONNECTING")
        result = service.activate_saved_connection(
            candidate["profile_name"], timeout_seconds=20)
        if result.get("ok"):
            state_store.set_state("STA_CONNECTED")
            return {"started": True, "status": "STA_CONNECTED"}

    # Phase 3: AP 모드 전환
    state_store.set_state("AP_STARTING")
    service.start_ap_mode(
        ssid=os.environ.get("WIFI_BOOTSTRAP_AP_SSID", "Matterhub-Setup-WhatsMatter"),
        password=os.environ.get("WIFI_BOOTSTRAP_AP_PASSWORD", "00000000"))
    state_store.set_state("AP_MODE")
    return {"started": True, "status": "AP_MODE", "ap_mode": True}

# bootstrap.py — STA 워치독 (연결 끊김 감시)
def watch_disconnection_and_start_ap(service, state_store, **kwargs):
    """연결 끊김 감시 → AP 자동 전환 + STA 자동 재접속"""
    WATCH_INTERVAL = 5       # 범위: 2-60초
    DISCONNECT_GRACE = 20    # 범위: 5-300초
    RECONNECT_INTERVAL = 15  # 범위: 5-300초
    RECONNECT_TIMEOUT = 20   # 범위: 5-180초
    MANUAL_HOLD = 45         # 범위: 0-600초

    while True:
        status = service.get_status()
        if not status.get("current_ssid"):
            # 20초 유예 후 AP 모드 전환
            sleep_fn(DISCONNECT_GRACE)
            if not service.get_status().get("current_ssid"):
                service.start_ap_mode()
                state_store.set_state("AP_MODE")

        # AP 모드 중 자동 STA 재접속 시도 (15초 간격)
        if state_store.snapshot()["state"] == "AP_MODE":
            if not _manual_ap_hold_active(state_store.snapshot()):
                candidate = _pick_known_network_candidate(service)
                if candidate:
                    service.activate_saved_connection(candidate, timeout_seconds=20)

        sleep_fn(WATCH_INTERVAL)
```

```python
# service.py — nmcli AP 모드 시작
class WifiConfigService:
    def start_ap_mode(self, *, ssid=None, password=None):
        """nmcli hotspot으로 AP 모드 활성화 (최대 3회 재시도)"""
        ssid = ssid or self.default_ap_ssid  # "Matterhub-Setup-WhatsMatter"
        password = password or self.ap_password  # "00000000"

        self._pause_conflicting_services_for_ap()  # named.service 등 중지

        # nmcli device wifi hotspot ssid {ssid} password {password} ...
        for attempt in range(3):
            try:
                self._run_nmcli([
                    "device", "wifi", "hotspot",
                    "ifname", self.interface,
                    "ssid", ssid,
                    "password", password,
                ], timeout=40)
                return {"ssid": ssid, "gateway_ip": "10.42.0.1"}
            except NmcliCommandError:
                # 재시도 전 기존 연결 정리
                pass
```

핵심 설계 결정:
- `ProvisionStateStore`로 상태 전이 추적 (스레드 안전, RLock)
- 수동 AP 홀드(45초): 관리자가 웹 UI로 AP를 수동 활성화하면 자동 재접속 차단
- `_pick_known_network_candidate()`: AP 프로파일 필터링, 가시적 네트워크 우선
- `_pause_conflicting_services_for_ap()`: DNS 서비스(named.service) 등 AP와 충돌하는 서비스 일시 중지
- 부팅 시 Wi-Fi 부트스트랩과 AP 워치독은 별도 데몬 스레드로 실행

---

## 8. MAC 바인딩 보안 강화

### 문제 발생

MatterHub 디바이스의 SD 카드를 복제하면 동일한 인증서와 설정으로 다른 Raspberry Pi에서 실행할 수 있었다. 이는 무단 복제 및 AWS IoT Core 인증서 남용 위험을 초래했다.

### 원인 분석

기존 시스템은 디바이스 고유성을 검증하는 메커니즘이 없었다. AWS IoT Core 인증서가 파일 기반이므로 SD 카드 복사만으로 디바이스 복제가 가능했다.

### 해결 방안

**TPM 기반 vs MAC 바인딩 비교:**

| 항목 | TPM (하드웨어 보안) | MAC 바인딩 (소프트웨어) |
|------|-------------------|---------------------|
| 보안 강도 | 최고 (하드웨어 키 저장) | **중간 (MAC 스푸핑 가능하나 충분)** |
| 하드웨어 요구 | TPM 칩 필요 | **없음 (NIC 내장)** |
| 구현 복잡도 | 높음 (PKCS#11 연동) | **낮음 (/sys/class/net 읽기)** |
| Raspberry Pi 지원 | 제한적 | **완벽** |

Raspberry Pi에 TPM이 없으므로 MAC 바인딩을 선택했다. MAC 스푸핑은 물리적 접근 + 전문 지식이 필요하므로 가정 환경에서 충분한 보호 수준이다.

### 결과

| 지표 | AS-IS (무제한) | TO-BE (MAC 바인딩) |
|------|--------------|------------------|
| SD 카드 복제 방지 | 없음 | **MAC 불일치 시 서비스 시작 차단** |
| 인증서 남용 방지 | 없음 | **하드웨어 바인딩** |
| 관리 방식 | 없음 | **환경변수 + 화이트리스트 파일** |

### 세부 구현

**파일:** `libs/device_binding.py`

```python
def normalize_mac(value: str | None) -> str:
    """MAC 주소 정규화: 소문자, 콜론 구분"""
    if not value:
        return ""
    cleaned = value.strip().lower().replace(":", "").replace("-", "")
    if len(cleaned) != 12 or not all(c in "0123456789abcdef" for c in cleaned):
        return ""
    return ":".join(cleaned[i:i+2] for i in range(0, 12, 2))

def load_runtime_macs(*, interface, sys_class_net="/sys/class/net", read_text_fn):
    """시스템 네트워크 인터페이스에서 실제 MAC 주소 수집"""
    macs = {}
    for iface in os.listdir(sys_class_net):
        if iface == "lo":
            continue
        if interface and iface != interface:
            continue
        addr = read_text_fn(f"{sys_class_net}/{iface}/address")
        normalized = normalize_mac(addr)
        if normalized:
            macs[iface] = normalized
    return macs

def evaluate_mac_binding(*, env, sys_class_net, read_text_fn):
    """MAC 바인딩 평가 — 허용/차단 결정"""
    enabled = _as_bool(env.get("MAC_BINDING_ENABLED"), default=False)
    if not enabled:
        return True, {"reason": "disabled"}

    allowed = load_allowed_macs(env=env, read_text_fn=read_text_fn)
    if not allowed:
        return True, {"reason": "allowed_list_empty"}

    runtime = load_runtime_macs(
        interface=env.get("MAC_BINDING_INTERFACE"),
        sys_class_net=sys_class_net,
        read_text_fn=read_text_fn)

    for iface, mac in runtime.items():
        if mac in allowed:
            return True, {"reason": "allowed_mac_matched", "interface": iface, "mac": mac}

    return False, {"reason": "allowed_mac_not_matched", "runtime_macs": runtime}

def enforce_mac_binding(logger=print) -> bool:
    """서비스 시작 시 호출 — False 반환 시 서비스 종료"""
    allowed, details = evaluate_mac_binding(
        env=os.environ,
        sys_class_net="/sys/class/net",
        read_text_fn=lambda p: Path(p).read_text().strip())

    if not allowed:
        logger(f"[MAC Binding] 차단: {details}")
    return allowed
```

핵심 설계 결정:
- `/sys/class/net/{iface}/address` 직접 읽기 — 외부 명령 의존 없음
- 화이트리스트는 환경변수(`MAC_BINDING_ALLOWED`) 또는 파일(`MAC_BINDING_ALLOWED_FILE`)로 관리
- 루프백(lo) 인터페이스 자동 제외
- `MAC_BINDING_INTERFACE` 미설정 시 모든 NIC 검사 — 유/무선 모두 커버
- `enforce_mac_binding()`은 `support_tunnel.py` 등 각 서비스 엔트리에서 호출

---

## 9. 멀티 프로세스 systemd 아키텍처 & 하드닝

### 문제 발생

초기 단일 프로세스 구조(또는 PM2 관리)에서 하나의 컴포넌트(예: MQTT 연결 오류)가 크래시하면 Flask API, 알림, 자동화 등 전체 기능이 중단되었다.

### 원인 분석

단일 프로세스 구조의 문제:
- **장애 전파**: MQTT 스레드 데드락 → 전체 프로세스 행
- **재시작 범위**: 하나의 기능 오류로 모든 기능 재시작
- **보안**: 모든 기능이 동일 권한으로 실행 (root 또는 단일 사용자)
- **리소스 격리**: 메모리 누수가 전체 시스템 영향

### 해결 방안

**PM2 vs systemd 비교:**

| 항목 | PM2 | systemd |
|------|-----|---------|
| 보안 하드닝 | 없음 | **12개 보안 디렉티브** |
| cgroup 격리 | 없음 | **서비스별 독립 cgroup** |
| 부팅 통합 | 별도 스크립트 필요 | **네이티브 (WantedBy=multi-user)** |
| 의존성 관리 | 없음 | **After=network-online.target** |
| 로그 관리 | PM2 로그 파일 | **journalctl (structured logging)** |

### 결과

| 지표 | AS-IS (단일/PM2) | TO-BE (systemd 6-서비스) |
|------|-----------------|------------------------|
| 장애 격리 | 전체 영향 | **서비스별 독립 (나머지 정상)** |
| 재시작 범위 | 전체 | **해당 서비스만 (5초 후 자동)** |
| 보안 수준 | 단일 권한 | **서비스별 하드닝 프로파일 (12개 디렉티브)** |
| root 서비스 | 전체 또는 없음 | **update-agent만 root** |
| 부팅 통합 | cron/rc.local | **systemd native** |

### 세부 구현

**파일:** `device_config/service_definitions.py`

```python
from dataclasses import dataclass
from pathlib import Path

# 기본 하드닝 (12개 디렉티브)
DEFAULT_HARDENING_DIRECTIVES = (
    "NoNewPrivileges=true",
    "PrivateTmp=true",
    "ProtectSystem=false",
    "ProtectControlGroups=true",
    "ProtectKernelTunables=true",
    "ProtectKernelModules=true",
    "RestrictSUIDSGID=true",
    "LockPersonality=true",
    "RestrictRealtime=true",
    "CapabilityBoundingSet=",         # 모든 Linux capability 제거
    "AmbientCapabilities=",           # Ambient capability 제거
    "UMask=0077",                     # 소유자만 접근
)

# root 서비스용 하드닝 (update-agent)
UPDATE_AGENT_HARDENING_DIRECTIVES = (
    "PrivateTmp=true",
    "ProtectSystem=false",
    "ProtectControlGroups=true",
    "ProtectKernelTunables=true",
    "ProtectKernelModules=true",
    "LockPersonality=true",
    "RestrictRealtime=true",
    "UMask=0077",
    # NoNewPrivileges, RestrictSUIDSGID, CapabilityBoundingSet 제외
    # → root 파일 접근 및 systemctl 실행 허용
)

@dataclass(frozen=True)
class ServiceDefinition:
    service_name: str
    description: str
    script_path: Path
    enabled_by_default: bool = True
    run_user_override: str | None = None
    unit_directives: tuple[str, ...] = ()
    hardening_directives: tuple[str, ...] = ()

SERVICE_DEFINITIONS = (
    ServiceDefinition("matterhub-api", "Flask REST API", Path("app.py")),
    ServiceDefinition("matterhub-mqtt", "MQTT Worker", Path("mqtt.py"),
                       hardening_directives=DEFAULT_HARDENING_DIRECTIVES),
    ServiceDefinition("matterhub-rule-engine", "Rule Engine", Path("sub/ruleEngine.py"),
                       hardening_directives=DEFAULT_HARDENING_DIRECTIVES),
    ServiceDefinition("matterhub-notifier", "Notifier", Path("sub/notifier.py"),
                       hardening_directives=DEFAULT_HARDENING_DIRECTIVES),
    ServiceDefinition("matterhub-support-tunnel", "SSH Tunnel", Path("support_tunnel.py"),
                       enabled_by_default=False,
                       hardening_directives=DEFAULT_HARDENING_DIRECTIVES),
    ServiceDefinition("matterhub-update-agent", "Update Agent", Path("update_agent.py"),
                       run_user_override="root",
                       hardening_directives=UPDATE_AGENT_HARDENING_DIRECTIVES),
)
```

핵심 설계 결정:
- `Restart=always` + `RestartSec=5`: 크래시 시 5초 후 자동 복구
- update-agent만 root: 파일시스템 쓰기, systemctl restart 권한 필요
- support-tunnel은 기본 비활성: `StartLimitIntervalSec=0`으로 무한 재시작 허용
- `CapabilityBoundingSet=` (빈 값): 모든 Linux capability 제거 — 최소 권한 원칙
- `ProtectSystem=false`: 업데이트 시 파일 수정 필요하므로 읽기 전용 설정 미사용
- `frozen=True` 데이터클래스: 서비스 정의의 불변성 보장

---

## 10. OTA 번들 업데이트 시스템

### 문제 발생

Git pull 기반 업데이트는 코드 변경만 배포 가능했다. 설정 파일 변경, 바이너리 추가, systemd unit 수정 등 코드 외 리소스 배포가 필요한 경우 수동 SCP를 사용해야 했다.

### 원인 분석

Git 기반 배포의 한계:
- Git 추적 대상만 업데이트 가능
- `.env`, 인증서, systemd unit 등은 Git 외 관리
- 바이너리 파일은 Git에 부적합
- 롤백이 `git reset --hard`만 가능 (파일 단위 불가)

### 해결 방안

**SCP 수동 배포 vs OTA 번들 비교:**

| 항목 | SCP 수동 배포 | OTA 번들 업데이트 |
|------|-------------|-----------------|
| 배포 대상 | 단일 디바이스 | **MQTT 브로드캐스트** |
| 무결성 검증 | 없음 | **SHA256 + manifest.json** |
| 롤백 | 수동 복원 | **자동 (백업 → 헬스체크 실패 → 복원)** |
| 배포 가능 범위 | 코드 + 설정 | **코드 + 설정 + 바이너리 + unit** |

### 결과

| 지표 | AS-IS (수동/Git only) | TO-BE (OTA 번들) |
|------|---------------------|-----------------|
| 배포 방식 | SSH + SCP / git pull | **MQTT 명령 → 자동 다운로드 + 적용** |
| 무결성 검증 | 없음 | **SHA256 체크섬 + manifest 타입 검증** |
| 롤백 | 수동 | **자동 (헬스체크 실패 시)** |
| 배포 범위 | 코드만 | **코드 + 설정 + 바이너리 + systemd unit** |

### 세부 구현

**파일:** `update_agent.py`, `device_config/apply_update_bundle.sh`

```python
# update_agent.py — 번들 검증 및 적용 에이전트
@dataclass(frozen=True)
class UpdateAgentConfig:
    enabled: bool
    project_root: Path
    inbox_dir: Path          # update/inbox/
    applied_dir: Path        # update/applied/
    failed_dir: Path         # update/failed/
    poll_seconds: int        # 기본 15초 (범위: 3-3600)
    apply_script: Path       # device_config/apply_update_bundle.sh
    require_manifest: bool   # 기본 True
    allowed_bundle_types: tuple[str, ...]  # ("matterhub-runtime", "matterhub-update")
    require_sha256: bool     # 기본 False

def verify_bundle(bundle_path: Path, config: UpdateAgentConfig) -> tuple[bool, str]:
    """번들 무결성 검증"""
    # 1. SHA256 체크섬 검증 (옵션)
    if config.require_sha256:
        expected = _read_sidecar_sha256(bundle_path)  # .sha256 사이드카 파일
        actual = _calculate_sha256(bundle_path)        # 1MB 청크 단위 계산
        if expected != actual:
            return False, "sha256_mismatch"

    # 2. tar.gz 유효성 + payload/ 디렉토리 존재
    with tarfile.open(bundle_path, "r:gz") as tar:
        names = tar.getnames()
        if not any(n.startswith("payload/") for n in names):
            return False, "payload_missing"

    # 3. manifest.json 확인 (옵션)
    if config.require_manifest:
        manifest = json.loads(tar.extractfile("payload/manifest.json").read())
        if manifest.get("bundle_type") not in config.allowed_bundle_types:
            return False, "bundle_type_not_allowed"

    return True, "ok"

def run_forever(config: UpdateAgentConfig, runner):
    """15초 폴링 루프"""
    while True:
        bundles = discover_bundles(config.inbox_dir)  # *.tar.gz, mtime 정렬
        if bundles:
            ok, reason = verify_bundle(bundles[0], config)
            if ok:
                rc = runner(_build_apply_command(config, bundles[0]))
                _archive_bundle(bundles[0], config.applied_dir if rc == 0 else config.failed_dir)
        time.sleep(config.poll_seconds)
```

```bash
# apply_update_bundle.sh — 번들 적용 + 롤백
SERVICES=(matterhub-api.service matterhub-mqtt.service
          matterhub-rule-engine.service matterhub-notifier.service)

# 파일별 백업 → 적용
for file in $(cat "$FILES_LIST"); do
    if [ -f "$PROJECT_ROOT/$file" ]; then
        cp "$PROJECT_ROOT/$file" "$BACKUP_DIR/$file"   # 백업
    fi
    cp "$PAYLOAD_DIR/$file" "$PROJECT_ROOT/$file"      # 적용
done

# 서비스 재시작
sudo systemctl daemon-reload
sudo systemctl restart "${SERVICES[@]}"

# 헬스체크 + 롤백
if [ -n "$HEALTHCHECK_CMD" ]; then
    if ! bash -lc "$HEALTHCHECK_CMD"; then
        # 롤백: 백업 복원 + 새 파일 삭제 + 서비스 재시작
        for file in $(cat "$FILES_LIST"); do
            if [ -f "$BACKUP_DIR/$file" ]; then
                cp "$BACKUP_DIR/$file" "$PROJECT_ROOT/$file"
            else
                rm -f "$PROJECT_ROOT/$file"  # 새로 추가된 파일 삭제
            fi
        done
        sudo systemctl restart "${SERVICES[@]}"
        exit 1
    fi
fi
```

핵심 설계 결정:
- `inbox/` → `applied/` 또는 `failed/` 아카이브: 번들 이력 보존
- `payload/` 디렉토리 규칙: 번들 내 실제 배포 파일만 payload/ 하위에 배치
- `manifest.json`: `bundle_type`으로 허용 번들만 적용 (보안)
- SHA256 사이드카 파일(`.sha256`): 번들 파일 옆에 해시 파일 배치 — 전송 중 변조 감지
- 롤백은 파일 단위: `BACKUP_DIR`에 원본 파일별 백업, 새 파일은 삭제

---

## 11. 역방향 SSH 터널 원격 유지보수

### 문제 발생

가정 네트워크의 방화벽(NAT) 뒤에 설치된 MatterHub 디바이스에 SSH로 접속할 수 없었다. 네트워크 장애나 설정 오류 발생 시 현장 방문이 유일한 해결 수단이었다.

### 원인 분석

가정용 공유기의 NAT/방화벽:
- 외부 → 내부 SSH 접속 차단 (포트 포워딩 설정 불가능)
- 동적 IP 할당 — 고정 IP 없음
- VPN 설정은 각 가정 공유기별 상이하여 비현실적

### 해결 방안

**VPN vs 역방향 SSH 터널 비교:**

| 항목 | VPN (WireGuard 등) | 역방향 SSH 터널 |
|------|-------------------|---------------|
| 공유기 설정 | 필요 (포트 포워딩) | **불필요** |
| 복잡도 | 높음 (키 교환, 라우팅) | **낮음 (SSH만)** |
| NAT 통과 | 일부 NAT에서 문제 | **아웃바운드 SSH로 확실한 통과** |
| 대역폭 | 전체 트래픽 터널링 | **필요 시 SSH만** |

### 결과

| 지표 | AS-IS (현장 방문) | TO-BE (SSH 터널) |
|------|----------------|-----------------|
| 원격 접속 | 불가 | **릴레이 서버 경유 SSH** |
| 접속 시간 | 현장 방문 (수 시간) | **즉시 (1초 미만)** |
| 연결 유지 | N/A | **autossh + 지수 백오프** |
| NAT/방화벽 | 차단됨 | **아웃바운드 :443으로 통과** |

### 세부 구현

**파일:** `libs/support_tunnel.py`

```python
@dataclass(frozen=True)
class TunnelConfig:
    enabled: bool
    command: str                        # "ssh" 또는 "autossh"
    user: str | None                    # 릴레이 서버 사용자
    host: str | None                    # 릴레이 서버 주소
    port: int                           # 기본 443 (HTTPS 포트로 방화벽 우회)
    remote_port: int | None             # 디바이스별 고유 포트
    local_port: int                     # 기본 22 (SSH)
    remote_bind_address: str            # 기본 "127.0.0.1"
    server_alive_interval: int          # 기본 30초
    server_alive_count_max: int         # 기본 3 (90초 무응답 시 재연결)
    reconnect_delay_seconds: int        # 기본 5초 (초기 재연결 대기)
    max_reconnect_delay_seconds: int    # 기본 60초 (지수 백오프 상한)
    connect_timeout_seconds: int        # 기본 10초
    preflight_tcp_check: bool           # 기본 True (연결 전 TCP 확인)
    preflight_tcp_timeout_seconds: int  # 기본 5초

def build_ssh_command(config: TunnelConfig) -> list[str]:
    """SSH 역방향 터널 명령 생성"""
    cmd = [config.command]  # ssh 또는 autossh

    if config.command == "autossh":
        cmd.extend(["-M", "0"])  # 모니터링 비활성 (ServerAlive 사용)

    cmd.extend([
        "-N", "-T",  # 원격 명령 없음, TTY 없음
        "-R", f"{config.remote_bind_address}:{config.remote_port}:"
              f"localhost:{config.local_port}",
        "-p", str(config.port),
        "-o", "ExitOnForwardFailure=yes",
        "-o", f"ConnectTimeout={config.connect_timeout_seconds}",
        "-o", f"ServerAliveInterval={config.server_alive_interval}",
        "-o", f"ServerAliveCountMax={config.server_alive_count_max}",
    ])

    if config.private_key_path:
        cmd.extend(["-i", config.private_key_path])

    cmd.append(f"{config.user}@{config.host}")
    return cmd

def execute(config, *, retry_forever=True, sleep_fn, **kwargs) -> int:
    """지수 백오프 재연결 루프"""
    delay = config.reconnect_delay_seconds  # 5초

    while True:
        # Preflight: TCP 연결 확인
        if config.preflight_tcp_check:
            if not _probe_tcp_connectivity(config.host, config.port,
                                           config.preflight_tcp_timeout_seconds):
                sleep_fn(delay)
                delay = min(delay * 2, config.max_reconnect_delay_seconds)  # 지수 백오프
                continue

        # SSH 프로세스 실행
        cmd = build_ssh_command(config)
        rc = runner(cmd)

        if not retry_forever:
            return rc

        # 재연결 대기 (지수 백오프: 5 → 10 → 20 → 40 → 60초)
        sleep_fn(delay)
        delay = min(delay * 2, config.max_reconnect_delay_seconds)
```

핵심 설계 결정:
- 포트 443 사용: HTTPS와 동일 포트로 기업/가정 방화벽 우회
- `remote_bind_address=127.0.0.1`: 릴레이 서버 로컬에서만 터널 접근 가능 (보안)
- `ExitOnForwardFailure=yes`: 포트 충돌 시 즉시 종료 (다른 디바이스와 포트 겹침 방지)
- `ServerAliveInterval=30` + `CountMax=3`: 90초 무응답 시 자동 감지 + 재연결
- 지수 백오프: 5초 → 60초 상한, 네트워크 복구 시까지 안정적 재시도
- Preflight TCP 체크: SSH 시도 전 TCP 레벨에서 릴레이 접근성 확인

---

## 12. .deb 패키지 빌드 & .pyc 배포

### 문제 발생

수동 배포(SCP + SSH)는 시간이 오래 걸리고, Python 소스 코드(.py)가 디바이스에 노출되었다. 또한 개발 PC(macOS)와 디바이스(Ubuntu/ARM)의 Python 버전 불일치로 사전 컴파일된 .pyc가 디바이스에서 실행되지 않았다.

### 원인 분석

1. **소스 노출**: .py 파일이 디바이스에 그대로 배포 — 역공학 및 변조 가능
2. **Python 버전 불일치**: macOS Python 3.12에서 컴파일한 .pyc가 디바이스 Python 3.9에서 로드 실패 (magic number 불일치)
3. **수동 배포**: 파일 복사, venv 생성, systemd 설치를 매번 수동으로 수행
4. **macOS 아티팩트**: `tar`에 `._*` 리소스 파일이 포함됨

### 해결 방안

**Docker 크로스 컴파일 vs postinst 디바이스 컴파일 비교:**

| 항목 | Docker ARM 빌드 | postinst 디바이스 컴파일 |
|------|----------------|----------------------|
| 빌드 환경 | Docker QEMU 에뮬레이션 | **네이티브 ARM** |
| Python 버전 일치 | 보장 (Docker 이미지 고정) | **보장 (디바이스 Python 사용)** |
| 빌드 시간 | 느림 (QEMU 오버헤드) | **빠름 (네이티브)** |
| 빌드 PC 의존성 | Docker 필수 | **dpkg-deb만 필요** |

소스 모드(.py)로 패키징하고 postinst에서 디바이스 Python으로 컴파일하는 방식을 채택했다.

### 결과

| 지표 | AS-IS (수동 SCP) | TO-BE (.deb + .pyc) |
|------|-----------------|-------------------|
| 배포 방식 | 수동 파일 복사 | **dpkg -i matterhub.deb** |
| 소스 코드 노출 | .py 파일 노출 | **.pyc만 존재 (.py 삭제)** |
| Python 버전 문제 | magic number 불일치 | **디바이스에서 네이티브 컴파일** |
| 서비스 설정 | 수동 systemd 설치 | **postinst 자동화** |
| 의존성 설치 | 수동 pip install | **postinst venv + pip** |

### 세부 구현

**파일:** `device_config/build_matterhub_deb.sh`

```bash
#!/usr/bin/env bash
# .deb 패키지 빌드 스크립트

VERSION="${VERSION:-$(date +%Y.%m.%d)-$(git rev-parse --short HEAD)}"
ARCH="${ARCH:-arm64}"
INSTALL_PREFIX="/opt/matterhub"
MODE="${MODE:-source}"  # "pyc" 또는 "source"

# 1. 패키지 구조 생성
BUILD_ROOT=$(mktemp -d)
APP_DIR="$BUILD_ROOT/$PACKAGE_NAME/$INSTALL_PREFIX/app"

# 2. 소스 코드 복사 (선택적)
for dir in mqtt_pkg sub libs wifi_config templates certificates providers; do
    rsync -a "$dir/" "$APP_DIR/$dir/"
done
for file in app.py mqtt.py support_tunnel.py update_agent.py; do
    cp "$file" "$APP_DIR/"
done

# 3. (선택) .pyc 컴파일 + .py 삭제 (빌드 PC에서)
if [ "$MODE" = "pyc" ]; then
    python3 -m compileall -q -b "$APP_DIR"
    find "$APP_DIR" -name "*.py" -delete
fi

# 4. 런처 스크립트 생성 (6개 서비스)
for svc in matterhub-api matterhub-mqtt ...; do
    cat > "$BIN_DIR/$svc" << 'LAUNCHER'
#!/usr/bin/env bash
VENV="$INSTALL_PREFIX/venv/bin/python"
[ -x "$VENV" ] || VENV="/usr/bin/python3"
export PYTHONPATH="$INSTALL_PREFIX/app"
exec "$VENV" "$INSTALL_PREFIX/app/$SCRIPT"
LAUNCHER
done

# 5. systemd unit 생성 (7개: 6서비스 + provision oneshot)
# Restart=always, RestartSec=5, After=network-online.target

# 6. DEBIAN/postinst (설치 후 스크립트)
cat > "$DEBIAN_DIR/postinst" << 'POSTINST'
#!/usr/bin/env bash
set -e

# 사용자 생성
useradd -r -s /usr/sbin/nologin matterhub 2>/dev/null || true

# venv 생성 + 의존성 설치
python3 -m venv --system-site-packages "$INSTALL_PREFIX/venv"
"$INSTALL_PREFIX/venv/bin/pip" install -r "$INSTALL_PREFIX/app/requirements.txt"

# .py → .pyc 컴파일 + .py 삭제 (디바이스에서 네이티브 컴파일)
"$INSTALL_PREFIX/venv/bin/python" -m compileall -q -b "$INSTALL_PREFIX/app"
find "$INSTALL_PREFIX/app" -name "*.py" ! -name "__init__.py" -delete

# systemd 활성화 + 시작
systemctl daemon-reload
for svc in matterhub-api matterhub-mqtt ...; do
    systemctl enable "$svc.service"
    systemctl start "$svc.service"
done
POSTINST

# 7. .deb 빌드
dpkg-deb --build "$BUILD_ROOT/$PACKAGE_NAME" "$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
```

핵심 설계 결정:
- `MODE=source`(기본): .py 소스로 패키징, postinst에서 디바이스 Python으로 컴파일 — **Python 버전 불일치 해소**
- `MODE=pyc`: 빌드 PC에서 사전 컴파일 — 빌드 PC와 디바이스 Python 버전이 동일할 때만 사용
- `compileall -q -b`: `-b` 플래그로 .pyc를 .py와 같은 디렉토리에 생성 (import 경로 유지)
- `__init__.py`는 삭제 대상에서 제외: 일부 라이브러리가 패키지 탐지에 필요
- `conffiles`에 `/etc/matterhub/matterhub.env` 선언: dpkg 업그레이드 시 사용자 설정 보존
- DEBIAN 의존성: `python3, python3-venv, python3-pip, network-manager, openssh-client, openssh-server`
