import json
import os
import threading
import time
import uuid
import sys
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
res_file_path= os.environ.get('res_file_path')
schedules_file_path = os.environ.get('schedules_file_path')
rules_file_path = os.environ.get('rules_file_path')
rooms_file_path = os.environ.get('rooms_file_path')
devices_file_path = os.environ.get('devices_file_path')
notifications_file_path = os.environ.get('notifications_file_path')

HA_host = os.environ.get('HA_host')
hass_token = os.environ.get('hass_token')
matterhub_id = os.environ.get('matterhub_id')

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

# 변경사항 감지 기반 섀도우 업데이트
class StateChangeDetector:
    def __init__(self):
        self.last_states = {}
        self.is_initialized = False  # 초기화 여부 플래그
        self.change_threshold = 5  # 5초 내 변경사항이 있으면 업데이트
        
        # 섀도우 업데이트에서 제외할 센서 목록 (state 변화 감지만 제외)
        self.excluded_sensors = {
            'sensor.smart_ht_sensor_ondo',
            'sensor.smart_ht_sensor_ondo_1', 
            'sensor.smart_ht_sensor_ondo_2',
            'sensor.smart_ht_sensor_ondo_3',
            'sensor.smart_ht_sensor_seubdo',
            'sensor.smart_ht_sensor_seubdo_1',
            'sensor.smart_ht_sensor_seubdo_2', 
            'sensor.smart_ht_sensor_seubdo_3',
            'sensor.smart_presence_sensor_jodo',
            'sensor.smart_presence_sensor_jodo_1',
            'sensor.smart_presence_sensor_jodo_2',
            'sensor.smart_presence_sensor_jodo_3'
        }
        
    def detect_changes(self, current_states):
        """상태 변경사항 감지 (sensor.로 시작하는 디바이스는 state 변화 무시)"""
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
        
        # 실제 변경사항 감지 (sensor.로 시작하는 디바이스는 state 변화 무시)
        for state in current_states:
            entity_id = state.get('entity_id')
            current_state = state.get('state')
            
            if not entity_id:
                continue
                
            # sensor.로 시작하는 디바이스는 변경사항 감지에서 제외 (state 변화 무시)
            if entity_id.startswith('sensor.'):
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

# 전역 변수
state_detector = StateChangeDetector()
last_heartbeat = 0
HEARTBEAT_INTERVAL = 3600  # 30분 → 60분으로 변경 (비용 절감)
last_shadow_update = 0  # Shadow 업데이트 rate-limit용
MIN_SHADOW_INTERVAL = 120  # 30초 → 120초로 변경 (비용 절감)
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
                    print(f"🔄 재연결 지연: {random_delay:.1f}초")
                    time.sleep(random_delay)
                
                # 재연결
                aws_client = AWSIoTClient()
                global_mqtt_connection = aws_client.connect_mqtt()

                # 재구독 (필요한 토픽만)
                subscribe_topics = [
                    f"matterhub/{matterhub_id}/api",
                    "matterhub/api",
                    "matterhub/group/all/api",
                    f"matterhub/update/specific/{matterhub_id}",  # 실제 사용되는 업데이트 토픽만
                ]
                
                for t in subscribe_topics:
                    try:
                        subscribe_future, _ = global_mqtt_connection.subscribe(
                            topic=t,
                            qos=mqtt.QoS.AT_LEAST_ONCE,
                            callback=mqtt_callback
                        )
                        subscribe_future.result()
                        print(f"✅ 토픽 재구독 성공: {t}")
                    except Exception as e:
                        print(f"❌ 토픽 재구독 실패: {t} - {e}")

                print("MQTT 재연결 성공")
                reconnect_attempts = 0
                return True
                
            except Exception as e:
                print(f"❌ 재연결 실패 (시도 {attempt + 1}/{max_retries}): {e}")
                
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"⏳ 재연결 재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ 재연결 최종 실패: {max_retries}회 시도 후 포기")
                    return False

    except Exception as e:
        print(f"연결 상태 확인 실패: {e}")
        return False

class AWSIoTClient:
    def __init__(self):
        self.cert_path = "certificates/"
        self.claim_cert = "whatsmatter_nipa_claim_cert.cert.pem"
        self.claim_key = "whatsmatter_nipa_claim_cert.private.key"
        self.endpoint = "a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com"
        self.client_id = "whatsmatter-nipa-claim-thing"
        
    def check_certificate(self):
        """발급된 인증서 확인"""
        cert_file = os.path.join(self.cert_path, "device.pem.crt")
        key_file = os.path.join(self.cert_path, "private.pem.key")
        
        if os.path.exists(cert_file) and os.path.exists(key_file):
            return True, cert_file, key_file
        return False, None, None

    def provision_device(self):
        """Claim 인증서를 사용하여 새 인증서 발급 및 사물 등록"""
        try:
            # Claim 인증서로 MQTT 클라이언트 생성
            event_loop_group = io.EventLoopGroup(1)
            host_resolver = io.DefaultHostResolver(event_loop_group)
            client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

            mqtt_connection = mqtt_connection_builder.mtls_from_path(
                endpoint=self.endpoint,
                cert_filepath=os.path.join(self.cert_path, self.claim_cert),
                pri_key_filepath=os.path.join(self.cert_path, self.claim_key),
                client_bootstrap=client_bootstrap,
                client_id=self.client_id,
                keep_alive_secs=120  # 300초 → 120초로 변경 (비용 최적화)
            )

            print("MQTT 연결 시도 중...")
            connect_future = mqtt_connection.connect()
            connect_future.result(timeout=10)
            print("MQTT 연결 성공")
            
            # 인증서 발급 요청
            provision_topic = "$aws/certificates/create/json"
            response_topic = "$aws/certificates/create/json/accepted"
            
            # 응답 대기를 위한 플래그
            received_response = False
            new_cert_data = None
            
            def on_message_received(topic, payload, **kwargs):
                nonlocal received_response, new_cert_data
                new_cert_data = json.loads(payload.decode())
                received_response = True
            
            subscribe_future, _ = mqtt_connection.subscribe(
                topic=response_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=on_message_received
            )
            subscribe_future.result(timeout=10)
            
            print("인증서 발급 요청 중...")
            publish_future, _ = mqtt_connection.publish(
                topic=provision_topic,
                payload=json.dumps({}),
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            publish_future.result(timeout=10)
            
            # 응답 대기
            timeout = time.time() + 10
            while not received_response and time.time() < timeout:
                time.sleep(0.1)
            
            if new_cert_data:
                # 새 인증서 저장
                with open(os.path.join(self.cert_path, "device.pem.crt"), "w") as f:
                    f.write(new_cert_data["certificatePem"])
                with open(os.path.join(self.cert_path, "private.pem.key"), "w") as f:
                    f.write(new_cert_data["privateKey"])
                
                # 인증서 발급 후 사물 등록 진행
                success = self.register_thing(
                    mqtt_connection, 
                    new_cert_data["certificateId"],
                    new_cert_data["certificateOwnershipToken"]
                )
                mqtt_connection.disconnect()
                return success
                
            mqtt_connection.disconnect()
            return False
        except Exception as e:
            print(f"인증서 발급 실패: {e}")
            return False

    def register_thing(self, mqtt_connection, certificate_id, cert_ownership_token):
        """템플릿을 사용하여 사물 등록"""
        try:
            template_topic = "$aws/provisioning-templates/whatsmatter-nipa-template/provision/json"
            response_topic = "$aws/provisioning-templates/whatsmatter-nipa-template/provision/json/accepted"
            
            received_response = False
            registration_data = None
            
            def on_registration_response(topic, payload, **kwargs):
                nonlocal received_response, registration_data
                registration_data = json.loads(payload.decode())
                received_response = True
            
            # 등록 응답 구독
            subscribe_future, _ = mqtt_connection.subscribe(
                topic=response_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=on_registration_response
            )
            subscribe_future.result(timeout=30)
            
            # 등록 요청 전송
            registration_request = {
                "Parameters": {
                    "SerialNumber": f"SN-{int(time.time())}"  # 실제 디바이스 이름으로 변경 필요
                },
                "certificateOwnershipToken": cert_ownership_token,
                "certificateId": certificate_id
            }
            
            print("사물 등록 요청 중...")
            publish_future, _ = mqtt_connection.publish(
                topic=template_topic,
                payload=json.dumps(registration_request),
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            publish_future.result(timeout=10)
            
            # 응답 대기
            timeout = time.time() + 10
            while not received_response and time.time() < timeout:
                time.sleep(0.1)
            
            if registration_data:
                print("사물 등록 성공:", registration_data)
                
                global matterhub_id
                matterhub_id = registration_data['thingName']
                # .env 파일 읽기 및 업데이트
                env_data = {}
                if os.path.exists('.env'):
                    with open('.env', 'r') as f:
                        for line in f:
                            if '=' in line:
                                key, value = line.strip().split('=', 1)
                                env_data[key] = value
                
                # matterhub_id 업데이트 또는 추가
                env_data['matterhub_id'] = f"\"{matterhub_id}\""
                
                # .env 파일에 저장
                with open('.env', 'w') as f:
                    for key, value in env_data.items():
                        f.write(f'{key}={value}\n')
                print(f"matterhub_id를 .env 파일에 저장했습니다: {matterhub_id}")
                return True
            
            print("사물 등록 실패: 응답 없음")
            return False
            
        except Exception as e:
            print(f"사물 등록 실패: {e}")
            return False

    def connect_mqtt(self):
        """인증서를 사용하여 MQTT 연결 - 동시성 문제 해결"""
        has_cert, cert_file, key_file = self.check_certificate()
        
        if not has_cert:
            success = self.provision_device()
            if not success:
                raise Exception("인증서 발급 실패")
            has_cert, cert_file, key_file = self.check_certificate()
            
        # 새로운 인증서로 연결할 때는 client_id를 다르게 설정
        self.client_id = f"device_{int(time.time())}"  # 고유한 client_id 생성
        
        event_loop_group = io.EventLoopGroup(1)
        host_resolver = io.DefaultHostResolver(event_loop_group)
        client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)
        
        # 연결 상태 콜백
        def on_interrupted(connection, error, **kwargs):
            global is_connected_flag, reconnect_attempts
            is_connected_flag = False
            print(f"⚠️ MQTT 연결 끊김 감지: {error}")
            print(f"🔄 자동 재연결 시도 준비 중... (현재 시도: {reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS})")

        def on_resumed(connection, return_code, session_present, **kwargs):
            global is_connected_flag, reconnect_attempts
            # 0(ACCEPTED)일 때 정상 복구
            is_connected_flag = (return_code == 0)
            if return_code == 0:
                reconnect_attempts = 0  # 재연결 성공 시 카운터 리셋
                print(f"✅ MQTT 연결 재개됨 (return_code={return_code}, session_present={session_present})")
            else:
                print(f"❌ MQTT 재연결 실패 (return_code={return_code})")

        mqtt_conn = mqtt_connection_builder.mtls_from_path(
            endpoint=self.endpoint,
            cert_filepath=cert_file,
            pri_key_filepath=key_file,
            client_bootstrap=client_bootstrap,
            client_id=self.client_id,
            keep_alive_secs=120,  # 300초 → 120초로 변경 (비용 최적화)
            on_connection_interrupted=on_interrupted,
            on_connection_resumed=on_resumed,
        )
        
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
                    print(f"🔄 동시 연결 방지를 위한 지연: {random_delay:.1f}초")
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
                    print(f"⏳ 재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ MQTT 연결 최종 실패: {max_retries}회 시도 후 포기")
                    raise Exception(f"MQTT 연결 실패: {max_retries}회 시도 후 포기 - {e}")
        
        # 이 지점에 도달하면 안 되지만 안전장치
        raise Exception("MQTT 연결 실패: 예상치 못한 오류")

def update_device_shadow():
    """변경사항 감지 기반 섀도우 업데이트 - Home Assistant 상태를 AWS IoT Core에 보고"""
    global last_heartbeat, last_shadow_update
    
    try:
        # MQTT 연결 상태 확인
        if not check_mqtt_connection():
            print("❌ MQTT 연결 실패로 섀도우 업데이트 스킵")
            return
            
        current_time = time.time()
        
        # Home Assistant에서 현재 상태 가져오기
        headers = {"Authorization": f"Bearer {hass_token}"}
        response = requests.get(f"{HA_host}/api/states", headers=headers)
        
        if response.status_code == 200:
            states = response.json()
            
            # devices.json에서 관리하는 entity_id 목록 가져오기
            managed_devices = set()
            try:
                if devices_file_path and os.path.exists(devices_file_path):
                    with open(devices_file_path, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:  # 파일이 비어있지 않은 경우만
                            devices_data = json.loads(content)
                            for device in devices_data:
                                if 'entity_id' in device:
                                    managed_devices.add(device['entity_id'])
                        else:
                            print(f"devices.json 파일이 비어있음: {devices_file_path}")
                elif devices_file_path:
                    print(f"devices.json 파일이 존재하지 않음: {devices_file_path}")
                else:
                    print("devices_file_path 환경변수가 설정되지 않음 - 모든 디바이스 관리")
            except json.JSONDecodeError as e:
                print(f"devices.json JSON 형식 오류: {e}")
                print(f"파일 경로: {devices_file_path}")
            except Exception as e:
                print(f"devices.json 읽기 실패: {e}")
                print(f"파일 경로: {devices_file_path}")
            finally:
                # 실패 시에도 빈 set으로 처리하여 프로그램 중단 방지
                if not managed_devices:
                    managed_devices = set()
            
            # 관리되는 디바이스만 필터링
            filtered_states = []
            for state in states:
                entity_id = state.get('entity_id', '')
                # managed_devices가 None이면 모든 디바이스 포함, 아니면 필터링
                if managed_devices is None or entity_id in managed_devices:
                    filtered_states.append(state)
            
            print(f"디바이스 상태: 전체 {len(states)}개, 관리 {len(filtered_states)}개")
            
            # 변경사항 감지 (관리되는 디바이스만)
            has_changes, changes = state_detector.detect_changes(filtered_states)
            
            # 변경사항이 있거나 heartbeat 시간이 되었으면 업데이트
            should_update = has_changes or (current_time - last_heartbeat >= HEARTBEAT_INTERVAL)
            
            # Rate-limit 체크: 최소 간격 보장 (비용 절감)
            if should_update and (current_time - last_shadow_update < MIN_SHADOW_INTERVAL):
                remaining = MIN_SHADOW_INTERVAL - (current_time - last_shadow_update)
                print(f"Shadow 업데이트 대기: {format_duration(remaining)} 남음")
                return
            
            # 디버깅 로그
            if has_changes:
                print(f"변경사항 감지: {len(changes)}개")
                for change in changes[:3]:  # 처음 3개만 출력
                    print(f"  - {change.get('type', 'unknown')}: {change.get('entity_id', 'unknown')}")
                if len(changes) > 3:
                    print(f"  ... 외 {len(changes) - 3}개")
            elif current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
                elapsed = current_time - last_heartbeat
                print(f"Heartbeat 시간 도달: {format_duration(elapsed)} 경과")
            else:
                remaining = HEARTBEAT_INTERVAL - (current_time - last_heartbeat)
                # 로그 출력 빈도 감소 (비용 절감을 위해 주석 처리)
                # print(f"변경사항 없음, Heartbeat 대기: {format_duration(remaining)} 남음")
            
            if should_update:
                # 상태 데이터 정리
                shadow_state = {
                    "state": {
                        "reported": {
                            "hub_id": matterhub_id,
                            "timestamp": int(current_time),
                            "status_key": f"{matterhub_id}#LATEST",  # 최신 상태 조회용 키
                            "device_count": len(filtered_states),  # 현재 연결된 관리 대상 디바이스 수
                            "total_devices": len(states),  # Home Assistant 전체 디바이스 수
                            "managed_devices": len(managed_devices),  # devices.json에 등록된 디바이스 수
                            "online": True,
                            "ha_reachable": True,
                            "devices": {},
                            "has_changes": has_changes,
                            "change_count": len(changes) if has_changes else 0,
                            "device_stats": {
                                "connected": len(filtered_states),  # 현재 연결된 관리 대상
                                "total_ha": len(states),  # Home Assistant 전체
                                "configured": len(managed_devices)  # 설정 파일에 등록된
                            }
                        }
                    }
                }
                
                # 관리되는 디바이스 상태만 포함
                for state in filtered_states:
                    entity_id = state.get('entity_id', '')
                    if entity_id:
                        shadow_state["state"]["reported"]["devices"][entity_id] = {
                            "state": state.get('state'),
                            "last_changed": state.get('last_changed'),
                            "attributes": state.get('attributes', {})
                        }
                
                # 섀도우 업데이트 토픽으로 발행 (QoS0으로 비용 절감)
                shadow_topic = f"$aws/things/{matterhub_id}/shadow/update"
                global_mqtt_connection.publish(
                    topic=shadow_topic,
                    payload=json.dumps(shadow_state),
                    qos=mqtt.QoS.AT_MOST_ONCE  # QoS1 → QoS0으로 변경하여 비용 절감
                )
                
                # Shadow 업데이트 성공 시 시간 기록 (rate-limit용)
                last_shadow_update = current_time
                
                if has_changes:
                    print(f"Shadow 업데이트: {len(changes)}개 변경사항")
                else:
                    last_heartbeat = current_time
                    print(f"Heartbeat Shadow 업데이트")
                    
    except Exception as e:
        print(f"Shadow 업데이트 실패: {e}")

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
        print(f"📊 결과: {'성공' if result['success'] else '실패'}")
        
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
        
        print(f"🔧 백그라운드 업데이트 실행 시작: {update_id}")
        print(f"📋 업데이트 상세 정보:")
        print(f"   - Branch: {branch}")
        print(f"   - Force Update: {force_update}")
        print(f"   - Hub ID: {matterhub_id}")
        
        # 외부 스크립트 실행
        result = execute_external_update_script(branch, force_update, update_id)
        
        print(f"📊 스크립트 실행 결과: {result}")
        
        # 스크립트가 백그라운드에서 실행된 경우 완료 대기
        if result.get('success') and result.get('pid'):
            print(f"⏳ 업데이트 스크립트 완료 대기 중... (PID: {result['pid']})")
            
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
                    print(f"⚠️ 프로세스 체크 실패: {e}")
                
                time.sleep(wait_interval)
                waited_time += wait_interval
                print(f"⏳ 업데이트 대기 중... ({waited_time}/{max_wait_time}초)")
            
            if waited_time >= max_wait_time:
                print(f"⚠️ 업데이트 타임아웃 ({max_wait_time}초)")
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
            
            print(f"🔄 큐에서 업데이트 명령 처리 시작: {message.get('update_id')}")
            
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
            print(f"📊 현재 큐 크기: {update_queue.qsize()}")
            
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
            print(f"⚠️ 스크립트 권한 설정 실패: {e}")
        
        print(f"🚀 외부 업데이트 스크립트 실행: {script_path}")
        print(f"📋 매개변수: branch={branch}, force_update={force_update}, update_id={update_id}, hub_id={matterhub_id}")
        
        # 스크립트 내용 확인 (디버깅용)
        try:
            with open(script_path, 'r') as f:
                script_content = f.read()
                print(f"📄 스크립트 내용 (처음 200자): {script_content[:200]}...")
        except Exception as e:
            print(f"⚠️ 스크립트 내용 읽기 실패: {e}")
        
        # 백그라운드에서 스크립트 실행 (nohup 사용)
        force_flag = "true" if force_update else "false"
        
        # 로그 파일 경로 설정
        log_file = f"/tmp/update_{update_id}.log"
        
        # 명령어 구성: 로그 파일에 출력 저장
        cmd = f"nohup bash {script_path} {branch} {force_flag} {update_id} {matterhub_id} > {log_file} 2>&1 & echo $!"
        
        print(f"🔧 실행 명령어: {cmd}")
        
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
                            print(f"📋 스크립트 로그: {log_content}")
                    except Exception as e:
                        print(f"⚠️ 로그 파일 읽기 실패: {e}")
                
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
                print(f"⚠️ PID 추출 실패: {result.stdout}")
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

def mqtt_callback(topic, payload, **kwargs):
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

# 사용 예시
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
        
        # 초기 Shadow 업데이트 실행
        print("초기 Shadow 업데이트 실행...")
        update_device_shadow()
        print("초기 Shadow 업데이트 완료")
        
    except Exception as e:
        print(f"MQTT 연결 실패: {e}")
        # 🚀 동시성 문제 해결: 연결 실패 시에도 재시도 로직 적용
        print("🔄 연결 실패로 인한 재시도 로직 시작...")
        
        max_retries = 3
        base_delay = 5
        
        for attempt in range(max_retries):
            try:
                # 동시 연결 방지를 위한 랜덤 지연
                import random
                random_delay = random.uniform(2, 8)  # 2-8초 랜덤 지연
                print(f"🔄 연결 재시도 전 지연: {random_delay:.1f}초")
                time.sleep(random_delay)
                
                print(f"🔄 MQTT 연결 재시도: {attempt + 1}/{max_retries}")
                aws_client = AWSIoTClient()
                global_mqtt_connection = aws_client.connect_mqtt()
                print("MQTT 연결 성공")
                
                # 초기 Shadow 업데이트 실행
                print("초기 Shadow 업데이트 실행...")
                update_device_shadow()
                print("초기 Shadow 업데이트 완료")
                break
                
            except Exception as retry_e:
                print(f"❌ 연결 재시도 실패 (시도 {attempt + 1}/{max_retries}): {retry_e}")
                
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"⏳ 재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ MQTT 연결 최종 실패: {max_retries}회 시도 후 포기")
                    sys.exit(1)  # ← 이걸로 PM2가 재시작하게 됨
    
    # 🚀 동시성 문제 해결: 토픽 구독도 재시도 로직 적용
    subscribe_topics = [
        f"matterhub/{matterhub_id}/api",
        "matterhub/api",
        "matterhub/group/all/api",
        f"matterhub/update/specific/{matterhub_id}",  # 실제 사용되는 토픽만 구독
    ]
    
    print("📡 토픽 구독 시작...")
    for topic in subscribe_topics:
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                # 동시 구독 방지를 위한 랜덤 지연
                if attempt > 0:
                    import random
                    random_delay = random.uniform(0.5, 1.5)  # 0.5-1.5초 랜덤 지연
                    print(f"🔄 구독 재시도 전 지연: {random_delay:.1f}초")
                    time.sleep(random_delay)
                
                subscribe_future, packet_id = global_mqtt_connection.subscribe(
                    topic=topic,
                    qos=mqtt.QoS.AT_LEAST_ONCE,
                    callback=mqtt_callback
                )
                
                subscribe_result = subscribe_future.result(timeout=10)
                print(f"✅ {topic} 토픽 구독 완료")
                break
                
            except Exception as e:
                print(f"❌ 토픽 구독 실패 (시도 {attempt + 1}/{max_retries}): {topic} - {e}")
                
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"⏳ 구독 재시도 전 대기: {delay}초")
                    time.sleep(delay)
                else:
                    print(f"❌ 토픽 구독 최종 실패: {topic}")
                    # 구독 실패해도 프로그램 계속 실행 (일부 토픽만 실패할 수 있음)
    
    print("📡 모든 토픽 구독 완료")


    # 테스트용 데이터 publish 제거 (비용 절감)
    # test_data = {
    #     "message": "테스트 메시지",
    #     "timestamp": time.time()
    # }
    

    
    try:
        # 최적화된 메인 루프
        connection_check_counter = 0
        
        while True:
            # Shadow 업데이트 실행 (변경사항 감지 기반)
            update_device_shadow()
            
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
        