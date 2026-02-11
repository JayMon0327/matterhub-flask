import json
import os
import threading
import time
import uuid
import sys
from datetime import datetime, timezone
from awscrt import io, mqtt
from awsiot import mqtt_connection_builder
from dotenv import load_dotenv
import requests

def format_duration(seconds):
    """초를 시간/분/초 형태로 포맷팅"""
    if seconds < 60:
        return f"{int(seconds)}초"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}분 {secs}초"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}시간 {minutes}분 {secs}초"

from sub.scheduler import one_time_schedule, one_time_scheduler, periodic_scheduler, schedule_config
from libs.edit import deleteItem, file_changed_request, putItem  # type: ignore

print("DynamoDB GSI(StatusKey Index) 추가")
print("mqtt.py 실행 전 대기 중 ...")
time.sleep(10) 

load_dotenv()
_mqtt_dir = os.path.dirname(os.path.abspath(__file__))
res_file_path = os.environ.get('res_file_path') or os.path.join(_mqtt_dir, 'resources')
schedules_file_path = os.environ.get('schedules_file_path') or os.path.join(_mqtt_dir, 'resources', 'schedule.json')
rules_file_path = os.environ.get('rules_file_path') or os.path.join(_mqtt_dir, 'resources', 'rules.json')
rooms_file_path = os.environ.get('rooms_file_path') or os.path.join(_mqtt_dir, 'resources', 'rooms.json')
devices_file_path = os.environ.get('devices_file_path') or os.path.join(_mqtt_dir, 'resources', 'devices.json')
notifications_file_path = os.environ.get('notifications_file_path') or os.path.join(_mqtt_dir, 'resources', 'notifications.json')

HA_host = os.environ.get('HA_host')
hass_token = os.environ.get('hass_token')
matterhub_id = (os.environ.get('matterhub_id') or '').strip().strip('"') or None  # None/빈문자열 정리

# matterhub_id 상태 로그 (Claim 프로비저닝 발급 여부 확인용)
if matterhub_id:
    print(f"matterhub_id 로드됨: {matterhub_id}")
else:
    print("matterhub_id 없음 (Claim 프로비저닝 후 .env 등록, 가이드: MATTERHUB_ID_GUIDE.md)")

# 디버깅용: 현재 구독된 토픽들을 추적
SUBSCRIBED_TOPICS = set()

# 알림(온습도 센서 등) 이벤트 발행 로직은 코나이 버전에서는 아직 사용 안 함.
# 기존 코드와의 호환을 위해 더미 함수로 남겨서 에러만 막아 둔다.
def detect_and_publish_alerts(filtered_states, managed_devices):
    # TODO: 코나이용 Alert 로직이 필요하면 이 함수 안에서 구현
    return

# 코나이 토픽: 코나이가 준 Topic prefix 1개만 사용 (구독·발행 동일)
# 예: update/reported/dev/.../matter/k3O6TL
LOCAL_API_BASE = os.environ.get("LOCAL_API_BASE", "http://localhost:8100")
_KONAI_TOPIC_DEFAULT = "update/reported/dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL"
KONAI_TOPIC = os.environ.get("KONAI_TOPIC", os.environ.get("KONAI_TOPIC_RESPONSE", _KONAI_TOPIC_DEFAULT)).strip('"')
KONAI_TOPIC_REQUEST = os.environ.get("KONAI_TOPIC_REQUEST", KONAI_TOPIC).strip('"')   # 구독: 같은 토픽
KONAI_TOPIC_RESPONSE = os.environ.get("KONAI_TOPIC_RESPONSE", KONAI_TOPIC).strip('"')  # 발행: 같은 토픽
# 테스트용 코나이 형식 토픽 (AWS IoT Core에 별도 테스트 토픽을 만들어 실제 코나이 구조를 그대로 검증)
# 예: KONAI_TEST_TOPIC=update/reported/dev/.../matter/test
KONAI_TEST_TOPIC = os.environ.get("KONAI_TEST_TOPIC", "").strip().strip('"') or None
KONAI_TEST_TOPIC_REQUEST = os.environ.get("KONAI_TEST_TOPIC_REQUEST", KONAI_TEST_TOPIC or "").strip().strip('"') or None
KONAI_TEST_TOPIC_RESPONSE = os.environ.get("KONAI_TEST_TOPIC_RESPONSE", KONAI_TEST_TOPIC or "").strip().strip('"') or None

# 변경 시마다 코나이 토픽으로 entity_changed 발행할 entity_id 목록 (쉼표 구분)
KONAI_REPORT_ENTITY_IDS_RAW = os.environ.get("KONAI_REPORT_ENTITY_IDS", "sensor.smart_ht_sensor_ondo,sensor.smart_ht_sensor_seubdo")
KONAI_REPORT_ENTITY_IDS = [eid.strip() for eid in KONAI_REPORT_ENTITY_IDS_RAW.split(",") if eid.strip()]
# 이벤트 발행 제한: 동일 entity_id 최소 발행 간격(초), 짧은 시간 내 동일 값 연속 발행 방지(초)
KONAI_EVENT_THROTTLE_SEC = max(0, float(os.environ.get("KONAI_EVENT_THROTTLE_SEC", "2")))
KONAI_EVENT_DEDUP_WINDOW_SEC = max(0, float(os.environ.get("KONAI_EVENT_DEDUP_WINDOW_SEC", "3")))
# bootstrap 전체 상태 1회 발행 여부 (프로세스당 1회)
konai_bootstrap_done = False
# entity_changed throttle/dedup용: entity_id -> (last_publish_ts, last_state_str)
konai_last_entity_publish = {}
# 전역 변수로 선언
global_mqtt_connection = None
is_connected_flag = False   # 연결 상태 플래그

# 업데이트 큐 시스템
import queue
update_queue = queue.Queue()
update_queue_lock = threading.Lock()
is_processing_update = False

# 섀도우 업데이트 관련 전역 변수
# last_state_update = 0  # 변경사항 감지 기반으로 변경되어 사용하지 않음
# STATE_UPDATE_INTERVAL = 180  # 3분마다 상태 업데이트 - 변경사항 감지 기반으로 변경되어 사용하지 않음

# 변경사항 감지 기반 상태 발행
class StateChangeDetector:
    def __init__(self):
        self.last_states = {}
        self.is_initialized = False  # 초기화 여부 플래그
        self.change_threshold = 5  # 5초 내 변경사항이 있으면 업데이트
        
        # 상태 발행 시 변경 감지에서 제외할 엔티티 목록
        self.excluded_sensors = {
            'sensor.smart_ht_sensor_ondo_1', 
            'sensor.smart_ht_sensor_ondo_2',
            'sensor.smart_ht_sensor_ondo_3',
            'sensor.smart_ht_sensor_seubdo_1',
            'sensor.smart_ht_sensor_seubdo_2', 
            'sensor.smart_ht_sensor_seubdo_3',
            'sensor.smart_presence_sensor_jodo',
            'sensor.smart_presence_sensor_jodo_1',
            'sensor.smart_presence_sensor_jodo_2',
            'sensor.smart_presence_sensor_jodo_3'
        }
        
        # 알림 감지용 배터리 키 목록
        self.battery_keys = ["battery", "battery_level", "battery_percentage"]
        
    def detect_changes(self, current_states):
        """상태 변경사항 감지. excluded_sensors에 있는 항목만 제외하고, 나머지(센서 포함)는 모두 감지."""
        changes = []
        current_time = time.time()
        
        # 첫 번째 실행 시에는 초기 상태만 저장하고 변경사항 없음으로 처리
        if not self.is_initialized:
            for state in current_states:
                entity_id = state.get('entity_id')
                current_state = state.get('state')
                if entity_id:
                    self.last_states[entity_id] = current_state
            self.is_initialized = True
            print(f"디바이스 상태 초기화 완료: {len(self.last_states)}개")
            return False, []  # 초기화 시에는 변경사항 없음
        
        # 실제 변경사항 감지 (excluded_sensors만 제외, 센서 포함 나머지 전부 감지)
        for state in current_states:
            entity_id = state.get('entity_id')
            current_state = state.get('state')
            
            if not entity_id:
                continue
            # 코나이 단일 센서 발행 대상은 제외 목록에 있어도 변경 감지함
            if entity_id in self.excluded_sensors and entity_id not in KONAI_REPORT_ENTITY_IDS:
                continue
                
            if entity_id not in self.last_states:
                # 새로운 디바이스
                changes.append({
                    'type': 'new_device',
                    'entity_id': entity_id,
                    'state': current_state
                })
                self.last_states[entity_id] = current_state
            elif self.last_states[entity_id] != current_state:
                # 상태 변경
                changes.append({
                    'type': 'state_change',
                    'entity_id': entity_id,
                    'previous': self.last_states[entity_id],
                    'current': current_state
                })
                self.last_states[entity_id] = current_state
        
        return len(changes) > 0, changes


def publish_alert_event(alert_payload):
    """
    AWS IoT Core로 알림 이벤트 발행
    """
    try:
        if not global_mqtt_connection or not is_connected_flag:
            print("❌ MQTT 연결 없음 - 알림 이벤트 발행 스킵")
            return
            
        # 알림 이벤트 토픽으로 발행
        alert_topic = f"matterhub/{matterhub_id}/event/device_alerts"
        
        global_mqtt_connection.publish(
            topic=alert_topic,
            payload=json.dumps(alert_payload),
            qos=mqtt.QoS.AT_MOST_ONCE  # QoS0으로 비용 최소화
        )
        
        print(f"AWS IoT Core 알림 이벤트 발행: {alert_topic}")
        
    except Exception as e:
        print(f"❌ AWS IoT Core 알림 이벤트 발행 실패: {e}")

# 전역 변수
state_detector = StateChangeDetector()
# 알림 중복 방지용 캐시: {(entity_id, alert_type): first_detected_ts}
active_alerts = {}
last_heartbeat = 0
HEARTBEAT_INTERVAL = 3600  # 30분 → 60분으로 변경 (비용 절감)
last_state_publish = 0  # 상태 발행 rate-limit용
MIN_STATE_PUBLISH_INTERVAL = 120  # 상태 발행 최소 간격(초)
last_health_check = 0  # 헬스체크용
HEALTH_CHECK_INTERVAL = 1800  # 10분 → 30분으로 변경 (비용 절감)
reconnect_attempts = 0
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 30  # 30초 후 재연결 시도

def check_mqtt_connection():
    """MQTT 연결 상태 확인 및 재연결 - 동시성 문제 해결"""
    global global_mqtt_connection, reconnect_attempts, is_connected_flag

    try:
        # 헬스체크 publish 제거: 연결 플래그와 연결 객체 존재 여부만 확인
        def _health_check():
            if global_mqtt_connection is None:
                return False
            # publish 없이 연결 상태만 확인 (비용 절감)
            return is_connected_flag

        still_ok = is_connected_flag and _health_check()
        if still_ok:
            reconnect_attempts = 0
            return True

        print(f"MQTT 재연결 시도: {reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS}")

        if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            print(f"MQTT 재연결 실패: 최대 시도 횟수 초과")
            return False

        reconnect_attempts += 1

        # 기존 연결 정리(예외 무시)
        if global_mqtt_connection:
            try:
                global_mqtt_connection.disconnect()
            except:
                pass

        # 🚀 동시성 문제 해결: 재연결 시에도 지수 백오프 적용
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                # 동시 재연결 방지를 위한 랜덤 지연
                if attempt > 0:
                    import random
                    random_delay = random.uniform(0.5, 2.0)  # 0.5-2초 랜덤 지연
                    print(f"재연결 지연: {random_delay:.1f}초")
                    time.sleep(random_delay)
                
                # 재연결
                aws_client = AWSIoTClient()
                global_mqtt_connection = aws_client.connect_mqtt()

                subscribe_topics = [KONAI_TOPIC_REQUEST]
                if KONAI_TEST_TOPIC_REQUEST:
                    subscribe_topics.append(KONAI_TEST_TOPIC_REQUEST)
                if matterhub_id and os.environ.get("SUBSCRIBE_MATTERHUB_TOPICS", "0") == "1":
                    subscribe_topics.extend([
                        f"matterhub/{matterhub_id}/api",
                        "matterhub/api",
                        "matterhub/group/all/api",
                        f"matterhub/update/specific/{matterhub_id}",
                    ])
                
                for t in subscribe_topics:
                    try:
                        print(f"SUBSCRIBE 재요청: {t}")
                        subscribe_future, _ = global_mqtt_connection.subscribe(
                            topic=t,
                            qos=mqtt.QoS.AT_LEAST_ONCE,
                            callback=mqtt_callback
                        )
                        subscribe_future.result()
                        SUBSCRIBED_TOPICS.add(t)
                        print(f"✅ SUBSCRIBE 재성공: {t}")
                    except Exception as e:
                        print(f"❌ 토픽 재구독 실패: {t} - {e!r} ({type(e).__name__})")

                print("MQTT 재연결 성공")
                reconnect_attempts = 0
                return True
                
            except Exception as e:
                print(f"❌ 재연결 실패 (시도 {attempt + 1}/{max_retries}): {e}")
                
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"재연결 재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ 재연결 최종 실패: {max_retries}회 시도 후 포기")
                    return False

    except Exception as e:
        print(f"연결 상태 확인 실패: {e}")
        return False

class AWSIoTClient:
    """코나이(Konai) 인증서 기반 MQTT 클라이언트. konai_certificates/ 사용, 프로비저닝 없음."""
    def __init__(self):
        self.cert_path = "konai_certificates/"
        self.endpoint = "a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com"
        # 코나이 Client ID: {device_id}-matter-{suffix}. env 없으면 기본값 사용
        self.client_id = os.environ.get(
            "KONAI_CLIENT_ID",
            "c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL"
        ).strip('"')

    def check_certificate(self):
        """코나이 인증서(cert.pem, key.pem) 확인"""
        cert_file = os.path.join(self.cert_path, "cert.pem")
        key_file = os.path.join(self.cert_path, "key.pem")
        if os.path.exists(cert_file) and os.path.exists(key_file):
            return True, cert_file, key_file
        return False, None, None

    # (제거됨) provision_device / register_thing
    # 코나이는 사전 발급 인증서(cert.pem, key.pem)만 사용합니다.
    # 기존 whatsmatter 방식: Claim 인증서로 AWS에 인증서 발급 요청 → device.pem.crt/private.pem.key 생성
    # → 프로비저닝 템플릿으로 사물 등록 → thingName을 matterhub_id로 .env에 저장.
    # 코나이 연동에서는 위 플로우를 사용하지 않으므로 matterhub_id는 .env에 직접 설정해야 합니다.

    def connect_mqtt(self):
        """코나이 인증서(cert.pem, key.pem)로 MQTT 연결. 프로비저닝 없음."""
        has_cert, cert_file, key_file = self.check_certificate()
        if not has_cert:
            raise Exception(
                "konai_certificates/cert.pem 또는 key.pem이 없습니다. "
                "코나이 인증서를 konai_certificates/ 디렉토리에 넣어 주세요."
            )

        # 코나이: client_id는 __init__에서 설정한 값 유지 (덮어쓰지 않음)
        event_loop_group = io.EventLoopGroup(1)
        host_resolver = io.DefaultHostResolver(event_loop_group)
        client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

        # 연결 상태 콜백
        def on_interrupted(connection, error, **kwargs):
            global is_connected_flag, reconnect_attempts
            is_connected_flag = False
            print(f"MQTT 연결 끊김: {error}")
            if SUBSCRIBED_TOPICS:
                print(f"구독 중: {', '.join(sorted(SUBSCRIBED_TOPICS))}")
            print(f"재연결 시도 ({reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS})")

        def on_resumed(connection, return_code, session_present, **kwargs):
            global is_connected_flag, reconnect_attempts
            is_connected_flag = (return_code == 0)
            if return_code == 0:
                reconnect_attempts = 0
                print(f"✅ MQTT 연결 재개됨 (return_code={return_code}, session_present={session_present})")
            else:
                print(f"❌ MQTT 재연결 실패 (return_code={return_code})")

        # 루트 CA(선택): ca_cert.pem이 있으면 TLS 검증에 사용
        mtls_kw = dict(
            endpoint=self.endpoint,
            cert_filepath=cert_file,
            pri_key_filepath=key_file,
            client_bootstrap=client_bootstrap,
            client_id=self.client_id,
            keep_alive_secs=120,
            on_connection_interrupted=on_interrupted,
            on_connection_resumed=on_resumed,
        )
        ca_path = os.path.join(self.cert_path, "ca_cert.pem")
        if os.path.exists(ca_path):
            mtls_kw["ca_filepath"] = ca_path
        mqtt_conn = mqtt_connection_builder.mtls_from_path(**mtls_kw)
        
        # 🚀 동시성 문제 해결: 지수 백오프 재시도 로직
        max_retries = 5
        base_delay = 2  # 기본 지연 시간 (초)
        
        for attempt in range(max_retries):
            try:
                print(f"새 인증서로 MQTT 연결 시도 중... (시도 {attempt + 1}/{max_retries})")
                
                # 동시 연결 방지를 위한 랜덤 지연
                if attempt > 0:
                    import random
                    random_delay = random.uniform(1, 3)  # 1-3초 랜덤 지연
                    print(f"연결 재시도 지연: {random_delay:.1f}초")
                    time.sleep(random_delay)
                
                connect_future = mqtt_conn.connect()
                connect_future.result(timeout=15)  # 타임아웃 15초
                
                print("새 인증서로 MQTT 연결 성공")
                
                # 최초 연결 성공 → 플래그 세팅
                global is_connected_flag
                is_connected_flag = True
                
                return mqtt_conn
                
            except Exception as e:
                print(f"❌ MQTT 연결 실패 (시도 {attempt + 1}/{max_retries}): {e}")
                
                if attempt < max_retries - 1:
                    # 지수 백오프: 2, 4, 8, 16초
                    delay = base_delay * (2 ** attempt)
                    print(f"재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ MQTT 연결 최종 실패: {max_retries}회 시도 후 포기")
                    raise Exception(f"MQTT 연결 실패: {max_retries}회 시도 후 포기 - {e}")
        
        # 이 지점에 도달하면 안 되지만 안전장치
        raise Exception("MQTT 연결 실패: 예상치 못한 오류")


class AWSProvisioningClient:
    """
    예전 whatsmatter 방식의 Claim 프로비저닝 플로우를 복원한 클라이언트.
    - certificates/ 디렉토리의 Claim 인증서(whatsmatter_nipa_claim_cert.*)를 사용
    - AWS IoT Core에서 새 인증서 발급 + 사물 등록
    - 등록된 thingName을 matterhub_id로 보고 .env에 저장
    코나이 브로커용 연결(AWSIoTClient)와는 별도로, 'matterhub_id 한 번 발급받을 때만' 사용합니다.
    """

    def __init__(self):
        # 예전 AWS IoT 환경 기준 기본값 (필요 시 env로 오버라이드 가능)
        self.cert_path = os.environ.get("AWS_CLAIM_CERT_PATH", "certificates/")
        self.claim_cert = os.environ.get("AWS_CLAIM_CERT_FILE", "whatsmatter_nipa_claim_cert.cert.pem")
        self.claim_key = os.environ.get("AWS_CLAIM_KEY_FILE", "whatsmatter_nipa_claim_cert.private.key")
        self.endpoint = os.environ.get(
            "AWS_PROVISION_ENDPOINT",
            "a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com",
        )
        self.client_id = os.environ.get("AWS_PROVISION_CLIENT_ID", "whatsmatter-nipa-claim-thing")

    def check_certificate(self):
        """발급된 device 인증서가 있는지 확인 (예전 device.pem.crt / private.pem.key)"""
        cert_file = os.path.join(self.cert_path, "device.pem.crt")
        key_file = os.path.join(self.cert_path, "private.pem.key")
        if os.path.exists(cert_file) and os.path.exists(key_file):
            return True, cert_file, key_file
        return False, None, None

    def register_thing(self, mqtt_connection, certificate_id, cert_ownership_token):
        """
        AWS IoT 프로비저닝 템플릿을 사용해 사물 등록.
        registrationData['thingName'] 를 matterhub_id로 사용하고 .env에 저장합니다.
        """
        try:
            template_name = os.environ.get("AWS_PROVISION_TEMPLATE_NAME", "whatsmatter-nipa-template")
            template_topic = f"$aws/provisioning-templates/{template_name}/provision/json"
            accepted_topic = f"$aws/provisioning-templates/{template_name}/provision/json/accepted"
            rejected_topic = f"$aws/provisioning-templates/{template_name}/provision/json/rejected"

            received_response = False
            registration_data = None
            reject_reason = None

            def on_accepted(topic, payload, **kwargs):
                nonlocal received_response, registration_data
                registration_data = json.loads(payload.decode())
                received_response = True

            def on_rejected(topic, payload, **kwargs):
                nonlocal received_response, reject_reason
                try:
                    reject_reason = json.loads(payload.decode())
                except Exception:
                    reject_reason = {"raw": payload.decode(errors="ignore")}
                received_response = True

            # accepted / rejected 둘 다 구독
            for sub_topic, callback in [(accepted_topic, on_accepted), (rejected_topic, on_rejected)]:
                sub_fut, _ = mqtt_connection.subscribe(
                    topic=sub_topic,
                    qos=mqtt.QoS.AT_LEAST_ONCE,
                    callback=callback,
                )
                sub_fut.result(timeout=10)

            print(f"[PROVISION] 템플릿: {template_name}")

            # 템플릿으로 사물 등록 요청 (원본: Parameters.SerialNumber 필수)
            payload = {
                "Parameters": {
                    "SerialNumber": f"SN-{int(time.time())}",
                },
                "certificateOwnershipToken": cert_ownership_token,
                "certificateId": certificate_id,
            }
            print("[PROVISION] 사물 등록 요청 중...")
            publish_future, _ = mqtt_connection.publish(
                topic=template_topic,
                payload=json.dumps(payload),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )
            publish_future.result(timeout=10)

            # 응답 대기 (15초)
            timeout = time.time() + 15
            while not received_response and time.time() < timeout:
                time.sleep(0.1)

            if reject_reason:
                print(f"[PROVISION] 사물 등록 거부됨: {reject_reason}")
                return False

            if registration_data:
                thing_name = registration_data.get("thingName")
                if not thing_name:
                    print(f"[PROVISION] 사물 등록 실패: thingName 없음, 응답={registration_data}")
                    return False

                # 전역 matterhub_id 업데이트
                global matterhub_id
                matterhub_id = thing_name

                # .env 파일 읽기 및 업데이트
                env_data: dict[str, str] = {}
                if os.path.exists(".env"):
                    with open(".env", "r", encoding="utf-8") as f:
                        for line in f:
                            if "=" in line:
                                key, value = line.strip().split("=", 1)
                                env_data[key] = value

                # matterhub_id 업데이트 또는 추가 (예전 스타일: 따옴표 포함)
                env_data["matterhub_id"] = f"\"{matterhub_id}\""

                # .env 저장
                with open(".env", "w", encoding="utf-8") as f:
                    for key, value in env_data.items():
                        f.write(f"{key}={value}\n")

                print(f"✅ [PROVISION] matterhub_id 발급 완료: {matterhub_id} (.env 저장됨, mqtt.py 재시작 필요)")
                return True

            print("[PROVISION] 사물 등록 실패: 응답 없음 (템플릿명·endpoint·Claim 정책 확인)")
            print(f"   - 템플릿: {template_name}, endpoint: {self.endpoint}")
            return False

        except Exception as e:
            print(f"[PROVISION] 사물 등록 실패: {e}")
            return False

    def provision_device(self):
        """
        Claim 인증서를 사용하여:
        1) 새 device.pem.crt / private.pem.key 발급
        2) 프로비저닝 템플릿으로 사물 등록
        3) 등록된 thingName을 matterhub_id로 보고 .env 에 저장
        """
        try:
            event_loop_group = io.EventLoopGroup(1)
            host_resolver = io.DefaultHostResolver(event_loop_group)
            client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

            mqtt_connection = mqtt_connection_builder.mtls_from_path(
                endpoint=self.endpoint,
                cert_filepath=os.path.join(self.cert_path, self.claim_cert),
                pri_key_filepath=os.path.join(self.cert_path, self.claim_key),
                client_bootstrap=client_bootstrap,
                client_id=self.client_id,
                keep_alive_secs=120,
            )

            print("[PROVISION] Claim 인증서로 MQTT 연결 시도 중...")
            connect_future = mqtt_connection.connect()
            connect_future.result(timeout=10)
            print("[PROVISION] MQTT 연결 성공")

            # 인증서 발급 요청
            provision_topic = "$aws/certificates/create/json"
            response_topic = "$aws/certificates/create/json/accepted"

            received_response = False
            new_cert_data = None

            def on_message_received(topic, payload, **kwargs):
                nonlocal received_response, new_cert_data
                new_cert_data = json.loads(payload.decode())
                received_response = True

            subscribe_future, _ = mqtt_connection.subscribe(
                topic=response_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=on_message_received,
            )
            subscribe_future.result(timeout=10)

            print("[PROVISION] 새 인증서 발급 요청 중...")
            publish_future, _ = mqtt_connection.publish(
                topic=provision_topic,
                payload=json.dumps({}),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )
            publish_future.result(timeout=10)

            # 응답 대기
            timeout = time.time() + 15
            while not received_response and time.time() < timeout:
                time.sleep(0.1)

            if not new_cert_data:
                print("[PROVISION] 인증서 발급 실패: 응답 없음")
                return False

            certificate_pem = new_cert_data.get("certificatePem")
            cert_id = new_cert_data.get("certificateId")
            ownership_token = new_cert_data.get("certificateOwnershipToken")

            if not (certificate_pem and cert_id and ownership_token):
                print(f"[PROVISION] 인증서 발급 실패: 응답 필드 부족: {new_cert_data}")
                return False

            # 새 인증서 저장 (원본과 동일: certificatePem + privateKey)
            cert_file = os.path.join(self.cert_path, "device.pem.crt")
            key_file = os.path.join(self.cert_path, "private.pem.key")
            with open(cert_file, "w", encoding="utf-8") as f:
                f.write(certificate_pem)
            private_key = new_cert_data.get("privateKey")
            if private_key:
                with open(key_file, "w", encoding="utf-8") as f:
                    f.write(private_key)
                print(f"[PROVISION] 새 인증서 저장: {cert_file}, {key_file}")
            else:
                print(f"[PROVISION] 경고: privateKey 없음, claim key 재사용. {cert_file} 만 저장")

            # 사물 등록 (thingName → matterhub_id)
            success = self.register_thing(mqtt_connection, cert_id, ownership_token)
            if not success:
                print("[PROVISION] 사물 등록 실패")
                return False

            print("[PROVISION] 프로비저닝 플로우 완료")
            return True

        except Exception as e:
            print(f"[PROVISION] 프로비저닝 실패: {e}")
            return False

def publish_bootstrap_all_states():
    """MQTT 연결 성공 후 1회만: 전체 상태를 type=bootstrap_all_states 로 발행"""
    global konai_bootstrap_done
    if konai_bootstrap_done:
        return
    try:
        if not check_mqtt_connection():
            return
        headers = {}
        if hass_token:
            headers["Authorization"] = f"Bearer {hass_token}"
        resp = requests.get(f"{LOCAL_API_BASE}/local/api/states", headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"❌ 코나이 bootstrap: 로컬 API 실패 HTTP {resp.status_code}")
            return
        data = resp.json()
        bootstrap_payload = {
            "type": "bootstrap_all_states",
            "correlation_id": None,
            "ts": _konai_ts(),
            "data": data,
        }
        if matterhub_id:
            bootstrap_payload["hub_id"] = matterhub_id
        _konai_publish(bootstrap_payload)
        konai_bootstrap_done = True
        print(f"✅ 코나이 bootstrap 발행: 전체 {len(data) if isinstance(data, list) else 0} entities")
    except Exception as e:
        print(f"❌ 코나이 bootstrap 실패: {e}")


def publish_device_state():
    """변경사항 감지 후 KONAI_REPORT_ENTITY_IDS 대상만 entity_changed 이벤트 발행. 전체 상태는 발행하지 않음(bootstrap 1회만)."""
    global konai_last_entity_publish

    try:
        if not check_mqtt_connection():
            return
        current_time = time.time()
        headers = {"Authorization": f"Bearer {hass_token}"}
        response = requests.get(f"{HA_host}/api/states", headers=headers)
        if response.status_code != 200:
            return

        states = response.json()
        managed_devices = set()
        try:
            if devices_file_path and os.path.exists(devices_file_path):
                with open(devices_file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        devices_data = json.loads(content)
                        for device in devices_data:
                            if 'entity_id' in device:
                                managed_devices.add(device['entity_id'])
        except Exception:
            pass
        if not managed_devices:
            managed_devices = set()

        filtered_states = [s for s in states if not managed_devices or s.get('entity_id', '') in managed_devices]
        has_changes, changes = state_detector.detect_changes(filtered_states)

        detect_and_publish_alerts(filtered_states, managed_devices)

        try:
            to_remove = []
            for (eid, atype), _ in list(active_alerts.items()):
                found = next((s for s in filtered_states if s.get('entity_id') == eid), None)
                if not found:
                    continue
                st = (found.get('state') or '').lower()
                attrs = found.get('attributes', {}) or {}
                if atype == 'UNAVAILABLE' and st != 'unavailable':
                    to_remove.append((eid, atype))
                elif atype == 'BATTERY_EMPTY':
                    ok = False
                    for k in state_detector.battery_keys:
                        if k in attrs:
                            try:
                                if int(attrs[k]) > 0:
                                    ok = True
                                    break
                            except (ValueError, TypeError):
                                pass
                    if ok:
                        to_remove.append((eid, atype))
            for key in to_remove:
                active_alerts.pop(key, None)
        except Exception:
            pass

        if not has_changes:
            return

        # KONAI_REPORT_ENTITY_IDS 대상만 entity_changed 발행 (throttle + dedup)
        for ch in changes:
            eid = ch.get("entity_id")
            if not eid or eid not in KONAI_REPORT_ENTITY_IDS:
                continue
            one = next((s for s in filtered_states if s.get("entity_id") == eid), None)
            if not one:
                continue

            state_str = json.dumps(one, sort_keys=True, ensure_ascii=False)
            last_info = konai_last_entity_publish.get(eid)
            now = time.time()
            # throttle: 최소 간격 미만이면 스킵
            if last_info:
                last_ts, last_val = last_info
                if now - last_ts < KONAI_EVENT_THROTTLE_SEC:
                    continue
                if KONAI_EVENT_DEDUP_WINDOW_SEC > 0 and (now - last_ts) < KONAI_EVENT_DEDUP_WINDOW_SEC and last_val == state_str:
                    continue
            konai_last_entity_publish[eid] = (now, state_str)

            event_id = f"evt-{int(now * 1000)}-{eid.replace('.', '_')}"
            evt_payload = {
                "type": "entity_changed",
                "correlation_id": None,
                "event_id": event_id,
                "ts": _konai_ts(),
                "entity_id": eid,
                "state": one,
            }
            if matterhub_id:
                evt_payload["hub_id"] = matterhub_id
            _konai_publish(evt_payload)
            print(f"코나이 entity_changed: {eid} → {KONAI_TOPIC_RESPONSE}")

    except Exception as e:
        print(f"상태 발행(이벤트) 실패: {e}")

def send_health_check():
    """간단한 헬스체크 전송 (비용 최소화)"""
    global last_health_check
    
    try:
        current_time = time.time()
        
        # 10분마다만 헬스체크 전송
        if current_time - last_health_check >= HEALTH_CHECK_INTERVAL:
            if check_mqtt_connection():
                # 최소한의 헬스체크 메시지 (QoS0으로 비용 절감)
                health_data = {
                    "status": "alive",
                    "timestamp": int(current_time),
                    "hub_id": matterhub_id
                }
                
                global_mqtt_connection.publish(
                    topic=f"matterhub/{matterhub_id}/health",
                    payload=json.dumps(health_data),
                    qos=mqtt.QoS.AT_MOST_ONCE  # QoS0으로 비용 최소화
                )
                
                last_health_check = current_time
                print(f"헬스체크 전송")
                
    except Exception as e:
        print(f"헬스체크 전송 실패: {e}")

def check_dynamic_endpoint(target_endpoint, endpoint, target_method, method): 
    url_var_list = []
    if(target_method!=method):
        return False
    
    target_endpoint_list = target_endpoint.split('/')
    endpoint_list = endpoint.split('/')

    if(len(target_endpoint_list) != len(endpoint_list)):
        return False
    
    for index in range(len(target_endpoint_list)):
        if(target_endpoint_list[index]=='_'):
            url_var_list.append(endpoint_list[index])
        else:
            if(target_endpoint_list[index]!=endpoint_list[index]):
                return False
    
    return url_var_list

def handle_ha_request(endpoint, method, request_func, response_id=None):
    """Home Assistant API 요청을 처리하고 응답을 반환하는 공통 함수"""
    try:
        response = request_func()
        res = {
            "endpoint": endpoint,
            "method": method,
            "status": "success",
            "data": response.json()
        }
    except Exception as e:
        print(f"Error: {e}")
        res = {
            "endpoint": endpoint,
            "method": method,
            "status": "error",
            "data": []
        }
    
    # response_id가 있으면 응답에 추가
    if response_id is not None:
        res["response_id"] = f"matterhub/{matterhub_id}/api/response"
    
    print(f"Response: {res}")
    
    global_mqtt_connection.publish(
        topic=f"matterhub/{matterhub_id}/api/response",
        payload=json.dumps(res),
        qos=mqtt.QoS.AT_MOST_ONCE  # QoS1 → QoS0으로 변경하여 ACK 패킷 감소
    )
    return

def send_immediate_response(message, status="processing"):
    """즉시 응답 전송 (처리 중 상태)"""
    try:
        update_id = message.get('update_id')
        response_topic = f"matterhub/{matterhub_id}/update/response"
        
        response_data = {
            'update_id': update_id,
            'hub_id': matterhub_id,
            'timestamp': int(time.time()),
            'command': 'git_update',
            'status': status,
            'message': f'Update command received and {status}'
        }
        
        global_mqtt_connection.publish(
            topic=response_topic,
            payload=json.dumps(response_data),
            qos=mqtt.QoS.AT_MOST_ONCE
        )
        
        print(f"📤 즉시 응답 전송: {status} - {update_id}")
        
    except Exception as e:
        print(f"❌ 즉시 응답 전송 실패: {e}")

def send_final_response(message, result):
    """최종 응답 전송 (완료 상태)"""
    try:
        update_id = message.get('update_id')
        response_topic = f"matterhub/{matterhub_id}/update/response"
        
        response_data = {
            'update_id': update_id,
            'hub_id': matterhub_id,
            'timestamp': int(time.time()),
            'command': 'git_update',
            'status': 'success' if result['success'] else 'failed',
            'result': result
        }
        
        global_mqtt_connection.publish(
            topic=response_topic,
            payload=json.dumps(response_data),
            qos=mqtt.QoS.AT_MOST_ONCE
        )
        
        print(f"✅ 최종 응답 전송 완료: {update_id}")
        print(f"결과: {'성공' if result['success'] else '실패'}")
        
    except Exception as e:
        print(f"❌ 최종 응답 전송 실패: {e}")

def send_error_response(message, error_msg):
    """에러 응답 전송"""
    try:
        update_id = message.get('update_id')
        response_topic = f"matterhub/{matterhub_id}/update/response"
        
        error_response = {
            'update_id': update_id,
            'hub_id': matterhub_id,
            'timestamp': int(time.time()),
            'command': 'git_update',
            'status': 'failed',
            'error': error_msg
        }
        
        global_mqtt_connection.publish(
            topic=response_topic,
            payload=json.dumps(error_response),
            qos=mqtt.QoS.AT_MOST_ONCE
        )
        
        print(f"❌ 에러 응답 전송: {update_id} - {error_msg}")
        
    except Exception as e:
        print(f"❌ 에러 응답 전송 실패: {e}")

def execute_update_async(message):
    """비동기 업데이트 실행"""
    try:
        command = message.get('command')
        update_id = message.get('update_id')
        branch = message.get('branch', 'master')
        force_update = message.get('force_update', False)
        
        print(f"백그라운드 업데이트 시작: {update_id} (branch={branch}, force={force_update}, hub_id={matterhub_id})")
        
        # 외부 스크립트 실행
        result = execute_external_update_script(branch, force_update, update_id)
        
        print(f"스크립트 실행 결과: {result}")
        
        # 스크립트가 백그라운드에서 실행된 경우 완료 대기
        if result.get('success') and result.get('pid'):
            print(f"업데이트 스크립트 대기 (PID: {result['pid']})")
            
            # 업데이트 완료 대기 (최대 5분)
            max_wait_time = 300  # 5분
            wait_interval = 10   # 10초마다 체크
            waited_time = 0
            
            while waited_time < max_wait_time:
                # 프로세스가 실행 중인지 확인
                try:
                    import subprocess
                    check_result = subprocess.run(
                        ['ps', '-p', str(result['pid'])],
                        capture_output=True,
                        text=True
                    )
                    
                    if check_result.returncode != 0:
                        # 프로세스가 종료됨
                        print(f"✅ 업데이트 스크립트 완료 감지 (PID: {result['pid']})")
                        break
                        
                except Exception as e:
                    print(f"프로세스 체크 실패: {e}")
                
                time.sleep(wait_interval)
                waited_time += wait_interval
                print(f"업데이트 대기 ({waited_time}/{max_wait_time}초)")
            
            if waited_time >= max_wait_time:
                print(f"업데이트 타임아웃 ({max_wait_time}초)")
                result['timeout'] = True
        
        # 최종 응답 전송
        send_final_response(message, result)
        
    except Exception as e:
        print(f"❌ 비동기 업데이트 실행 실패: {e}")
        send_error_response(message, str(e))

def process_update_queue():
    """업데이트 큐 처리 (순차적 처리)"""
    global is_processing_update
    
    while True:
        try:
            # 큐에서 업데이트 명령 가져오기 (블로킹)
            message = update_queue.get()
            
            with update_queue_lock:
                is_processing_update = True
            
            print(f"업데이트 큐 처리: {message.get('update_id')}")
            
            # 업데이트 실행
            execute_update_async(message)
            
            with update_queue_lock:
                is_processing_update = False
            
            # 작업 완료 표시
            update_queue.task_done()
            
            print(f"✅ 큐 업데이트 완료: {message.get('update_id')}")
            
        except Exception as e:
            print(f"❌ 큐 처리 중 오류: {e}")
            with update_queue_lock:
                is_processing_update = False
            update_queue.task_done()

def handle_update_command(message):
    """업데이트 명령 처리 - 큐 시스템 사용"""
    try:
        command = message.get('command')
        update_id = message.get('update_id')
        
        if command == 'git_update':
            print(f"🚀 Git 업데이트 명령 수신: {update_id}")
            
            # 즉시 "큐에 추가됨" 응답 전송
            send_immediate_response(message, "queued")
            
            # 큐에 업데이트 명령 추가
            update_queue.put(message)
            
            print(f"📥 업데이트 명령이 큐에 추가됨: {update_id}")
            print(f"큐 크기: {update_queue.qsize()}")
            
    except Exception as e:
        print(f"❌ Git 업데이트 실패: {e}")
        send_error_response(message, str(e))

def execute_external_update_script(branch='master', force_update=False, update_id='unknown'):
    """외부 업데이트 스크립트 실행 - mosquitto_pub 제거"""
    try:
        import subprocess
        import os
        
        # 업데이트 스크립트 경로를 동적으로 찾기
        possible_paths = [
            "/home/hyodol/whatsmatter-hub-flask-server/update_server.sh",
            "./update_server.sh",
            "../update_server.sh",
            os.path.join(os.path.dirname(__file__), "update_server.sh"),
            os.path.join(os.path.dirname(__file__), "../update_server.sh")
        ]
        
        script_path = None
        for path in possible_paths:
            if os.path.exists(path):
                script_path = path
                break
        
        if not script_path:
            return {
                'success': False,
                'error': f'Update script not found in any of the expected paths: {possible_paths}',
                'timestamp': int(time.time())
            }
        
        # 스크립트 실행 권한 확인 및 부여
        try:
            os.chmod(script_path, 0o755)
            print(f"✅ 스크립트 권한 설정 완료: {script_path}")
        except Exception as e:
            print(f"스크립트 권한 설정 실패: {e}")
        
        print(f"🚀 외부 업데이트 스크립트 실행: {script_path}")
        print(f"매개변수: branch={branch}, force_update={force_update}, update_id={update_id}, hub_id={matterhub_id}")
        
        # 스크립트 내용 확인 (디버깅용)
        try:
            with open(script_path, 'r') as f:
                script_content = f.read()
                print(f"📄 스크립트 내용 (처음 200자): {script_content[:200]}...")
        except Exception as e:
            print(f"스크립트 내용 읽기 실패: {e}")
        
        # 백그라운드에서 스크립트 실행 (nohup 사용)
        force_flag = "true" if force_update else "false"
        
        # 로그 파일 경로 설정
        log_file = f"/tmp/update_{update_id}.log"
        
        # 명령어 구성: 로그 파일에 출력 저장
        cmd = f"nohup bash {script_path} {branch} {force_flag} {update_id} {matterhub_id} > {log_file} 2>&1 & echo $!"
        
        print(f"실행 명령어: {cmd}")
        
        # 스크립트 실행
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            # 프로세스 ID 추출
            try:
                pid = int(result.stdout.strip())
                print(f"✅ 업데이트 스크립트 시작됨 (PID: {pid})")
                
                # 잠시 대기 후 로그 확인
                time.sleep(2)
                if os.path.exists(log_file):
                    try:
                        with open(log_file, 'r') as f:
                            log_content = f.read()
                            print(f"스크립트 로그: {log_content}")
                    except Exception as e:
                        print(f"로그 파일 읽기 실패: {e}")
                
                return {
                    'success': True,
                    'message': f'Update script started successfully (PID: {pid})',
                    'script_path': script_path,
                    'branch': branch,
                    'force_update': force_update,
                    'update_id': update_id,
                    'hub_id': matterhub_id,
                    'pid': pid,
                    'log_file': log_file,
                    'timestamp': int(time.time())
                }
            except ValueError:
                print(f"PID 추출 실패: {result.stdout}")
                return {
                    'success': True,
                    'message': 'Update script started but PID extraction failed',
                    'script_path': script_path,
                    'branch': branch,
                    'force_update': force_update,
                    'update_id': update_id,
                    'hub_id': matterhub_id,
                    'timestamp': int(time.time())
                }
        else:
            print(f"❌ 스크립트 실행 실패: {result.stderr}")
            return {
                'success': False,
                'error': f'Script execution failed: {result.stderr}',
                'timestamp': int(time.time())
            }
        
    except Exception as e:
        print(f"❌ 업데이트 스크립트 실행 중 예외 발생: {e}")
        return {
            'success': False,
            'error': str(e),
            'timestamp': int(time.time())
        }

def _konai_ts():
    """ISO8601 타임스탬프 (UTC)"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _konai_publish(payload_dict, response_topic=None):
    """코나이/테스트 토픽으로 dict 발행. response_topic이 없으면 기본 KONAI_TOPIC_RESPONSE 사용."""
    target_topic = response_topic or KONAI_TOPIC_RESPONSE
    global_mqtt_connection.publish(
        topic=target_topic,
        payload=json.dumps(payload_dict, ensure_ascii=False),
        qos=mqtt.QoS.AT_MOST_ONCE,
    )


def _konai_publish_error(correlation_id, code, message, detail=None, response_topic=None):
    """오류 응답 발행 (type: error). response_topic이 없으면 기본 KONAI_TOPIC_RESPONSE 사용."""
    body = {
        "type": "error",
        "correlation_id": correlation_id,
        "ts": _konai_ts(),
        "error": {"code": code, "message": message},
    }
    if detail is not None:
        body["error"]["detail"] = detail
    _konai_publish(body, response_topic=response_topic)
    print(f"❌ 코나이 오류 응답: {code} - {message}")


def handle_konai_states_request(payload_bytes=None, response_topic=None):
    """코나이 요청 처리: correlation_id 필수, entity_id 있으면 단일 조회 없으면 전체 조회.
    응답 규격: type, correlation_id, ts, data 또는 error."""
    try:
        correlation_id = None
        entity_id = None
        if payload_bytes:
            try:
                msg = json.loads(payload_bytes.decode("utf-8"))
            except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
                _konai_publish_error(None, "INVALID_JSON", "Request payload is not valid JSON", response_topic=response_topic)
                return
            if not isinstance(msg, dict):
                _konai_publish_error(None, "INVALID_JSON", "Request payload must be a JSON object", response_topic=response_topic)
                return
            correlation_id = msg.get("correlation_id")
            if not correlation_id:
                cid = msg.get("request_id")  # 대체 필드
                if cid is not None and str(cid).strip():
                    correlation_id = str(cid).strip()
            if not correlation_id:
                _konai_publish_error(None, "MISSING_CORRELATION_ID", "correlation_id is required", response_topic=response_topic)
                return
            eid = msg.get("entity_id")
            if eid is not None and str(eid).strip():
                entity_id = str(eid).strip()

        headers = {}
        if hass_token:
            headers["Authorization"] = f"Bearer {hass_token}"
        ts = _konai_ts()

        if entity_id:
            url = f"{LOCAL_API_BASE}/local/api/states/{entity_id}"
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    payload = {
                        "type": "query_response_single",
                        "correlation_id": correlation_id,
                        "ts": ts,
                        "data": data,
                    }
                    if matterhub_id:
                        payload["hub_id"] = matterhub_id
                    _konai_publish(payload, response_topic=response_topic)
                    print(f"✅ 코나이 단일 조회 응답: entity_id={entity_id}" + (f", hub_id={matterhub_id}" if matterhub_id else " (hub_id 없음)"))
                else:
                    _konai_publish_error(
                        correlation_id,
                        "LOCAL_API_ERROR" if resp.status_code >= 500 else "INVALID_ENTITY_ID",
                        resp.text or f"HTTP {resp.status_code}",
                        detail={"status_code": resp.status_code},
                        response_topic=response_topic,
                    )
            except requests.Timeout:
                _konai_publish_error(correlation_id, "TIMEOUT", "Local API request timed out", response_topic=response_topic)
            except Exception as e:
                _konai_publish_error(correlation_id, "LOCAL_API_ERROR", str(e), detail={"exception": type(e).__name__}, response_topic=response_topic)
        else:
            url = f"{LOCAL_API_BASE}/local/api/states"
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    payload = {
                        "type": "query_response_all",
                        "correlation_id": correlation_id,
                        "ts": ts,
                        "data": data,
                    }
                    if matterhub_id:
                        payload["hub_id"] = matterhub_id
                    _konai_publish(payload, response_topic=response_topic)
                    print(f"✅ 코나이 전체 조회 응답: {len(data) if isinstance(data, list) else 'n/a'} entities" + (f", hub_id={matterhub_id}" if matterhub_id else " (hub_id 없음)"))
                else:
                    _konai_publish_error(
                        correlation_id,
                        "LOCAL_API_ERROR",
                        resp.text or f"HTTP {resp.status_code}",
                        detail={"status_code": resp.status_code},
                        response_topic=response_topic,
                    )
            except requests.Timeout:
                _konai_publish_error(correlation_id, "TIMEOUT", "Local API request timed out", response_topic=response_topic)
            except Exception as e:
                _konai_publish_error(correlation_id, "LOCAL_API_ERROR", str(e), detail={"exception": type(e).__name__}, response_topic=response_topic)
    except Exception as e:
        print(f"❌ 코나이 요청 처리 실패: {e}")
        try:
            _konai_publish_error(None, "LOCAL_API_ERROR", str(e), response_topic=response_topic)
        except Exception:
            pass


def mqtt_callback(topic, payload, **kwargs):
    # 코나이 & 테스트용 코나이 형식 토픽:
    # 요청 토픽 수신 시 로컬 API 호출 후 해당 토픽용 응답 토픽으로 발행 (payload에 entity_id 있으면 해당 센서만 조회)
    if topic == KONAI_TOPIC_REQUEST:
        print(f"📩 코나이 요청 수신: {topic}")
        handle_konai_states_request(payload, response_topic=KONAI_TOPIC_RESPONSE)
        return
    if KONAI_TEST_TOPIC_REQUEST and topic == KONAI_TEST_TOPIC_REQUEST:
        # 테스트 토픽은 코나이와 동일한 JSON 스펙으로 동작하되, 응답은 테스트용 토픽으로 송출
        test_response_topic = KONAI_TEST_TOPIC_RESPONSE or KONAI_TEST_TOPIC_REQUEST
        print(f"코나이 테스트 요청: {topic} -> {test_response_topic}, matterhub_id={matterhub_id or '(미설정)'}")
        handle_konai_states_request(payload, response_topic=test_response_topic)
        return

    _message = json.loads(payload.decode('utf-8'))

    # 기본값 설정
    endpoint = None
    method = None
    response_id = None

    try:
        endpoint = _message['endpoint']
        method = _message['method']
        response_id = _message.get('response_id')  # response_id 추출 (없을 수 있음)
        # response_id가 없으면 임의의 UUID 생성
        if response_id is None:
            response_id = str(uuid.uuid4())
    except:
        # endpoint, method가 없는 경우 예외처리
        response_id = str(uuid.uuid4())  # 예외 발생 시에도 UUID 생성
        pass

    headers = {"Authorization": f"Bearer {hass_token}"}

    if endpoint == "/services":
        print(f"Received message: {payload} from topic: {topic} endpoint: {endpoint} method: {method}")
        handle_ha_request(
            endpoint,
            method,
            lambda: requests.get(f"{HA_host}/api/services", headers=headers),
            response_id
        )
        return

    # ✅ [1] 기존 개별 전체 상태 조회
    if endpoint == "/states" and method == "get":
        print(f"Received message: {payload} from topic: {topic} endpoint: {endpoint} method: {method}")
        handle_ha_request(
            endpoint,
            method,
            lambda: requests.get(f"{HA_host}/api/states", headers=headers),
            response_id
        )
        return

    check_res = check_dynamic_endpoint("/states/_",endpoint,"get",method)
    if(check_res):
        print(f"Received message: {payload} from topic: {topic} endpoint: {endpoint} method: {method}")
        handle_ha_request(
            endpoint,
            method,
            lambda: requests.get(f"{HA_host}/api/states/{check_res[0]}", headers=headers),
            response_id
        )
        return

    check_res = check_dynamic_endpoint("/devices/_/command",endpoint,"post",method)
    if(check_res):
        domain = _message['payload']['domain']
        service = _message['payload']['service']
        res = {
            "entity_id": check_res[0]
        }
        handle_ha_request(
            endpoint,
            method,
            lambda: requests.post(f"{HA_host}/api/services/{domain}/{service}", 
                                data=json.dumps(res), 
                                headers=headers),
            response_id
        )
        return

    # ✅ [3] 그룹 제어 처리
    if endpoint.startswith("/devices/") and endpoint.endswith("/command") and method == "post" and topic == "matterhub/group/all/api":
        print(f"[Group] Received group command from topic: {topic}")
        check_res = check_dynamic_endpoint("/devices/_/command", endpoint, "post", method)
        if check_res:
            domain = _message['payload']['domain']
            service = _message['payload']['service']
            res = {
                "entity_id": check_res[0]
            }
            handle_ha_request(
                endpoint,
                method,
                lambda: requests.post(
                    f"{HA_host}/api/services/{domain}/{service}",
                    data=json.dumps(res),
                    headers=headers
                ),
                response_id
            )
        return

    check_res = check_dynamic_endpoint("/devices/_/status",endpoint,"get",method)
    if(check_res):
        handle_ha_request(
            endpoint,
            method,
            lambda: requests.get(f"{HA_host}/api/states/{check_res[0]}", headers=headers),
            response_id
        )
        return

    check_res = check_dynamic_endpoint("/devices/_/services",endpoint,"get",method)
    if(check_res):
        target_entity = check_res[0]
        target_domain = target_entity.split('.')[0]
        
        def get_domain_services():
            response = requests.get(f"{HA_host}/api/services", headers=headers)
            all_domain = response.json()
            for d in all_domain:
                if(d['domain'] == target_domain):
                    return {"json": lambda: d['services']}
            return {"json": lambda: {}}
            
        handle_ha_request(
            endpoint,
            method,
            get_domain_services,
            response_id
        )
        return

    if(endpoint=="/devices" and method in ["get","post","delete","put"]):
        try:
            with open(devices_file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except FileNotFoundError:
            data = []
        
        if method == "post":
            new_data = _message['payload']
            data.append(new_data)
        if method == "delete":
            target_value = _message['payload']['entity_id']
            data = deleteItem(data, "entity_id", target_value)
        if method == "put":
            target_value = _message['payload']['entity_id']
            data = putItem(data, "entity_id", target_value, _message['payload'])

        with open(devices_file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4, ensure_ascii=False)

        def mock_request():
            class MockResponse:
                def json(self):
                    return data
            return MockResponse()
            # return type('Response', (), {'json': lambda: data})()

        handle_ha_request(endpoint, method, mock_request, response_id)
        return

    if(endpoint=="/schedules" and method in ["get","post","delete","put"]):
        try:
            with open(schedules_file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except FileNotFoundError:
            data = []
        
        if method == "post":
            new_data = _message['payload']
            data.append(new_data)
        if method == "delete":
            target_value = _message['payload']['id']
            data = deleteItem(data, "id", target_value)
        if method == "put":
            target_value = _message['payload']['id']
            data = putItem(data, "id", target_value, _message['payload'])

        with open(schedules_file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4, ensure_ascii=False)

        if(method != "get"):
            schedule_config(one_time)

        def mock_request():
            class MockResponse:
                def json(self):
                    return data
            return MockResponse()
            # return type('Response', (), {'json': lambda: data})()

        handle_ha_request(endpoint, method, mock_request, response_id)
        return

    if(endpoint=="/rules" and method in ["get","post","delete","put"]):
        try:
            with open(rules_file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except FileNotFoundError:
            data = []
        
        if method == "post":
            new_data = _message['payload']
            data.append(new_data)
        if method == "delete":
            target_value = _message['payload']['id']
            data = deleteItem(data, "id", target_value)
        if method == "put":
            target_value = _message['payload']['id']
            data = putItem(data, "id", target_value, _message['payload'])

        with open(rules_file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4, ensure_ascii=False)

        def mock_request():
            class MockResponse:
                def json(self):
                    return data
            return MockResponse()
            # return type('Response', (), {'json': lambda: data})()

        handle_ha_request(endpoint, method, mock_request, response_id)
        return
    
    if (endpoint == "/notifications" and method in ["get","post","delete","put"]):
        try:
            with open(notifications_file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except FileNotFoundError:
            data = []

        if method == "post":
            new_data = _message['payload']
            data.append(new_data)

        if method == "delete":
            target_value = _message['payload']['id']
            data = deleteItem(data, "id", target_value)

        if method == "put":
            target_value = _message['payload']['id']
            data = putItem(data, "id", target_value, _message['payload'])

        with open(notifications_file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4, ensure_ascii=False)

        # app.py와 동일하게 변경 알림 훅 호출(옵션)
        try:
            file_changed_request("notifications_file_changed")
        except Exception as e:
            print(f"[warn] notifications_file_changed 호출 실패: {e}")

        def mock_request():
            class MockResponse:
                def json(self):
                    return data
            return MockResponse()

        handle_ha_request(endpoint, method, mock_request, response_id)
        return

    if endpoint == "/" and method == "get":
        def mock_request():
            class MockResponse:
                def json(self):
                    return {"status": "ok"}
            return MockResponse()

        handle_ha_request(endpoint, method, mock_request, response_id)
        return

    # Git 업데이트 명령 처리 (specific 토픽만 처리)
    if topic == f"matterhub/{matterhub_id}/git/update" or topic.startswith("matterhub/update/specific/"):
        print(f"🚀 Git 업데이트 명령 수신: {topic}")
        handle_update_command(_message)
        return

    print(_message)

def config():
    # resource 디렉토리 생성
    if not os.path.exists(res_file_path):
        os.makedirs(res_file_path)
        print(f"폴더 생성: {res_file_path}")

    file_list = [schedules_file_path, rules_file_path, rooms_file_path, devices_file_path, notifications_file_path]
    
    for file_path in file_list:
        if not os.path.exists(file_path):
            try:
                # 디렉토리가 없으면 생성
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, ensure_ascii=False)
                print(f"파일 생성: {file_path}")
            except Exception as e:
                print(f"파일 생성 실패 {file_path}: {e}")

    # 메인 실행 진입점은 파일 맨 아래에 정의합니다.


# =====================================================================
# [TEST ONLY] KONAI Claim 프로비저닝 + AWS IoT Core 테스트 토픽 구독 코드
#   - 이 코드는 코나이 연동 구조를 AWS IoT Core 테스트 토픽에서
#     그대로 검증하기 위한 테스트 전용 코드입니다.
#   - 운영 배포 시에는 이 섹션 전체를 제거하거나 비활성화하세요.
#   - 실행 제어: 환경변수 ENABLE_KONAI_TEST_SUBSCRIBER="1" 일 때만 동작
# =====================================================================

def _build_konai_test_subscriber_connection():
    """
    Claim 기반 프로비저닝으로 발급된 device 인증서를 사용해
    AWS IoT Core(AWSProvisioningClient.endpoint)에 MQTT 연결을 생성.
    필요 시 provision_device() 를 호출해 인증서를 자동 발급합니다.
    """
    provisioning_client = AWSProvisioningClient()

    has_cert, cert_file, key_file = provisioning_client.check_certificate()
    if not has_cert:
        print("[TEST] device 인증서 없음, Claim 프로비저닝 실행")
        success = provisioning_client.provision_device()
        if not success:
            print("❌ [TEST] Claim 프로비저닝 실패 - 테스트 구독을 시작하지 않습니다.")
            return None
        has_cert, cert_file, key_file = provisioning_client.check_certificate()
        if not has_cert:
            print("❌ [TEST] 프로비저닝 후에도 device 인증서를 찾을 수 없습니다.")
            return None

    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

    # 테스트용 클라이언트 ID (환경변수로 오버라이드 가능)
    test_client_id = os.environ.get("AWS_TEST_CLIENT_ID", "whatsmatter-nipa-test-subscriber")

    print(f"[TEST] AWS IoT Core 테스트 구독 MQTT 연결 생성 "
          f"(endpoint={provisioning_client.endpoint}, client_id={test_client_id})")

    mqtt_conn = mqtt_connection_builder.mtls_from_path(
        endpoint=provisioning_client.endpoint,
        cert_filepath=cert_file,
        pri_key_filepath=key_file,
        client_bootstrap=client_bootstrap,
        client_id=test_client_id,
        keep_alive_secs=120,
    )
    return mqtt_conn


def _run_konai_test_subscriber_loop():
    """
    TEST ONLY:
    - KONAI_TEST_TOPIC / KONAI_TEST_TOPIC_REQUEST 를 구독해서
      mqtt.py 가 발행하는 테스트 응답을 동일 프로세스에서 확인하는 용도.
    - 별도 클라이언트 없이 로그만으로 테스트할 때 사용.
    """
    # 테스트 토픽 결정 (요청 토픽 기준)
    test_topic = KONAI_TEST_TOPIC_REQUEST or KONAI_TEST_TOPIC
    if not test_topic:
        print("[TEST] KONAI_TEST_TOPIC 미설정, 테스트 구독 스킵")
        return

    try:
        mqtt_conn = _build_konai_test_subscriber_connection()
        if mqtt_conn is None:
            return

        print("[TEST] AWS IoT Core 테스트 구독 MQTT 연결 시도")
        connect_future = mqtt_conn.connect()
        connect_future.result()
        print("✅ [TEST] 테스트 구독용 MQTT 연결 성공")

        def on_message(topic, payload, **kwargs):
            try:
                body = json.loads(payload.decode("utf-8"))
            except Exception:
                body = payload.decode("utf-8", errors="ignore")
            print("\n📩 [TEST 수신] ===============================")
            print(f"topic = {topic}")
            print(json.dumps(body, ensure_ascii=False, indent=2))
            print("===========================================\n")

        print(f"[TEST] 테스트 토픽 구독: {test_topic}")
        subscribe_future, _ = mqtt_conn.subscribe(
            topic=test_topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_message,
        )
        subscribe_future.result()
        print(f"✅ [TEST] 테스트 토픽 구독 완료: {test_topic}")
        print("[TEST] 테스트 구독 루프 진입")

        # 메인 프로세스와 함께 살아있도록 간단한 루프 유지
        while True:
            time.sleep(5)

    except Exception as e:
        print(f"❌ [TEST] 테스트 구독 루프 오류: {e}")


def start_konai_test_subscriber_if_enabled():
    """
    ENABLE_KONAI_TEST_SUBSCRIBER 환경변수가 "1" 일 때
    테스트 구독 스레드를 백그라운드에서 시작합니다.
    """
    if os.environ.get("ENABLE_KONAI_TEST_SUBSCRIBER", "0") != "1":
        return

    print("[TEST] ENABLE_KONAI_TEST_SUBSCRIBER=1, 테스트 구독 스레드 시작")
    t = threading.Thread(target=_run_konai_test_subscriber_loop, name="konai-test-subscriber")
    t.daemon = True
    t.start()


# ======================== 메인 실행 진입점 ==========================
if __name__ == "__main__":

    config()

    one_time = one_time_schedule()
    schedule_config(one_time)
    p = threading.Thread(target=periodic_scheduler)
    p.start()
    o = threading.Thread(target=one_time_scheduler, args=[one_time])
    o.start()

    # 업데이트 큐 처리 스레드 시작
    q = threading.Thread(target=process_update_queue)
    q.daemon = True
    q.start()
    print("✅ 업데이트 큐 처리 스레드 시작됨")

    try:
        aws_client = AWSIoTClient()
        global_mqtt_connection = aws_client.connect_mqtt()
        print("MQTT 연결 성공")

        # 코나이 bootstrap은 구독 완료 후 1회 호출
    except Exception as e:
        print(f"MQTT 연결 실패: {e}")
        # 🚀 동시성 문제 해결: 연결 실패 시에도 재시도 로직 적용
        print("연결 재시도 로직 시작")

        max_retries = 3
        base_delay = 5

        for attempt in range(max_retries):
            try:
                # 동시 연결 방지를 위한 랜덤 지연
                import random
                random_delay = random.uniform(2, 8)  # 2-8초 랜덤 지연
                print(f"연결 재시도 지연: {random_delay:.1f}초")
                time.sleep(random_delay)

                print(f"MQTT 연결 재시도: {attempt + 1}/{max_retries}")
                aws_client = AWSIoTClient()
                global_mqtt_connection = aws_client.connect_mqtt()
                print("MQTT 연결 성공")
                # bootstrap은 구독 완료 후 1회만 호출됨
                break

            except Exception as retry_e:
                print(f"❌ 연결 재시도 실패 (시도 {attempt + 1}/{max_retries}): {retry_e}")

                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ MQTT 연결 최종 실패: {max_retries}회 시도 후 포기")
                    sys.exit(1)  # ← 이걸로 PM2가 재시작하게 됨

    # 토픽 구독
    # - 코나이: KONAI_TOPIC_REQUEST
    # - 테스트용: KONAI_TEST_TOPIC_REQUEST (옵션)
    # - matterhub/*: AWS IoT 브로커용. KONAI 브로커(a34vuzhubahjfj)와 별개이므로 기본 비활성화
    subscribe_topics = [KONAI_TOPIC_REQUEST]
    if KONAI_TEST_TOPIC_REQUEST:
        subscribe_topics.append(KONAI_TEST_TOPIC_REQUEST)
    if matterhub_id and os.environ.get("SUBSCRIBE_MATTERHUB_TOPICS", "0") == "1":
        subscribe_topics.extend([
            f"matterhub/{matterhub_id}/api",
            "matterhub/api",
            "matterhub/group/all/api",
            f"matterhub/update/specific/{matterhub_id}",
        ])

    print(f"matterhub_id: {matterhub_id or '(미설정)'}")
    print(f"토픽 구독 시작 (총 {len(subscribe_topics)}개)")
    for topic in subscribe_topics:
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                # 동시 구독 방지를 위한 랜덤 지연
                if attempt > 0:
                    import random
                    random_delay = random.uniform(0.5, 1.5)  # 0.5-1.5초 랜덤 지연
                    print(f"구독 재시도 지연: {random_delay:.1f}초")
                    time.sleep(random_delay)

                print(f"SUBSCRIBE: {topic}")
                subscribe_future, packet_id = global_mqtt_connection.subscribe(
                    topic=topic,
                    qos=mqtt.QoS.AT_LEAST_ONCE,
                    callback=mqtt_callback
                )

                subscribe_result = subscribe_future.result(timeout=10)
                SUBSCRIBED_TOPICS.add(topic)
                print(f"✅ SUBSCRIBE 성공: {topic}")
                break

            except Exception as e:
                print(f"❌ 토픽 구독 실패 (시도 {attempt + 1}/{max_retries}): {topic} - {e!r} ({type(e).__name__})")

                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"구독 재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ 토픽 구독 최종 실패: {topic}")
                    # 구독 실패해도 프로그램 계속 실행 (일부 토픽만 실패할 수 있음)

    print("모든 토픽 구독 완료")

    # 코나이: bootstrap 전체 상태 1회 발행 (연결·구독 후 1회)
    publish_bootstrap_all_states()

    # 🧪 TEST ONLY: 환경변수 기반으로 테스트 구독 스레드 시작
    start_konai_test_subscriber_if_enabled()

    try:
        # 최적화된 메인 루프
        connection_check_counter = 0

        while True:
            # 상태 발행 (변경사항 감지 기반)
            publish_device_state()

            # 간단한 헬스체크 전송 (10분 간격)
            send_health_check()

            # 60초마다 MQTT 연결 상태 확인 (비용 절감을 위해 빈도 감소)
            connection_check_counter += 1
            if connection_check_counter >= 12:  # 5초 * 12 = 60초마다
                check_mqtt_connection()
                connection_check_counter = 0

            # CPU 사용량 감소를 위한 대기
            time.sleep(5)

    except KeyboardInterrupt:
        print("프로그램 종료")
        global_mqtt_connection.disconnect()