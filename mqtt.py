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

from sub.scheduler import one_time_schedule, one_time_scheduler, periodic_scheduler, schedule_config
from libs.edit import deleteItem, file_changed_request, putItem  # type: ignore

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

# 섀도우 업데이트 관련 전역 변수
# last_state_update = 0  # 변경사항 감지 기반으로 변경되어 사용하지 않음
# STATE_UPDATE_INTERVAL = 180  # 3분마다 상태 업데이트 - 변경사항 감지 기반으로 변경되어 사용하지 않음

# 변경사항 감지 기반 섀도우 업데이트
class StateChangeDetector:
    def __init__(self):
        self.last_states = {}
        self.change_threshold = 5  # 5초 내 변경사항이 있으면 업데이트
        
    def detect_changes(self, current_states):
        """상태 변경사항 감지"""
        changes = []
        current_time = time.time()
        
        for state in current_states:
            entity_id = state.get('entity_id')
            current_state = state.get('state')
            last_changed = state.get('last_changed')
            
            if entity_id not in self.last_states:
                # 새로운 디바이스
                changes.append({
                    'type': 'new_device',
                    'entity_id': entity_id,
                    'state': current_state
                })
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
HEARTBEAT_INTERVAL = 20000  # 약 5.5시간마다 heartbeat (변경사항이 없어도)
reconnect_attempts = 0
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 30  # 30초 후 재연결 시도

def check_mqtt_connection():
    """MQTT 연결 상태 확인 및 재연결"""
    global global_mqtt_connection, reconnect_attempts, is_connected_flag

    try:
        # 간단한 헬스체크: 연결돼 있다고 믿지만 publish가 실패하면 끊긴 것으로 간주
        def _health_check():
            if global_mqtt_connection is None:
                return False
            try:
                # QoS 0 ping 주제에 더미 페이로드
                global_mqtt_connection.publish(
                    topic=f"matterhub/{matterhub_id}/health",
                    payload=b"{}",
                    qos=mqtt.QoS.AT_MOST_ONCE
                )
                return True
            except Exception:
                return False

        still_ok = is_connected_flag and _health_check()
        if still_ok:
            reconnect_attempts = 0
            return True

        print(f"🔌 MQTT 연결 끊김, 재연결 시도... (시도 {reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS})")

        if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            print(f"🚨 최대 재연결 시도 횟수 초과 ({MAX_RECONNECT_ATTEMPTS}회)")
            return False

        reconnect_attempts += 1

        # 기존 연결 정리(예외 무시)
        if global_mqtt_connection:
            try:
                global_mqtt_connection.disconnect()
            except:
                pass

        # 재연결
        aws_client = AWSIoTClient()
        global_mqtt_connection = aws_client.connect_mqtt()

        # 재구독
        for t in (
            f"matterhub/{matterhub_id}/api",
            "matterhub/api",
            "matterhub/group/all/api",
        ):
            subscribe_future, _ = global_mqtt_connection.subscribe(
                topic=t,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=mqtt_callback
            )
            subscribe_future.result()

        print("✅ MQTT 재연결 성공!")
        reconnect_attempts = 0
        return True

    except Exception as e:
        print(f"❌ 연결 상태 확인 실패: {e}")
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
                keep_alive_secs=30
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
        """인증서를 사용하여 MQTT 연결"""
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
            global is_connected_flag
            is_connected_flag = False
            print(f"⚠️ MQTT 연결 끊김: {error}")

        def on_resumed(connection, return_code, session_present, **kwargs):
            global is_connected_flag
            # 0(ACCEPTED)일 때 정상 복구
            is_connected_flag = (return_code == 0)
            print(f"✅ MQTT 연결 재개됨 (return_code={return_code}, session_present={session_present})")

        mqtt_conn = mqtt_connection_builder.mtls_from_path(
            endpoint=self.endpoint,
            cert_filepath=cert_file,
            pri_key_filepath=key_file,
            client_bootstrap=client_bootstrap,
            client_id=self.client_id,
            keep_alive_secs=30,
            on_connection_interrupted=on_interrupted,
            on_connection_resumed=on_resumed,
        )
        
        print("새 인증서로 MQTT 연결 시도 중...")
        connect_future = mqtt_conn.connect()
        connect_future.result()
        print("새 인증서로 MQTT 연결 성공")
        
        # 최초 연결 성공 → 플래그 세팅
        global is_connected_flag
        is_connected_flag = True
        
        return mqtt_conn

def update_device_shadow():
    """변경사항 감지 기반 섀도우 업데이트 - Home Assistant 상태를 AWS IoT Core에 보고"""
    global last_heartbeat
    
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
                with open(devices_file_path, 'r', encoding='utf-8') as f:
                    devices_data = json.load(f)
                    for device in devices_data:
                        if 'entity_id' in device:
                            managed_devices.add(device['entity_id'])
            except Exception as e:
                print(f"⚠️ devices.json 읽기 실패: {e}")
                managed_devices = set()  # 실패 시 빈 set으로 처리
            
            # 관리되는 디바이스만 필터링
            filtered_states = []
            for state in states:
                entity_id = state.get('entity_id', '')
                if entity_id in managed_devices:
                    filtered_states.append(state)
            
            print(f"📊 전체 디바이스: {len(states)}개, 관리 대상: {len(filtered_states)}개")
            
            # 변경사항 감지 (관리되는 디바이스만)
            has_changes, changes = state_detector.detect_changes(filtered_states)
            
            # 변경사항이 있거나 heartbeat 시간이 되었으면 업데이트
            should_update = has_changes or (current_time - last_heartbeat >= HEARTBEAT_INTERVAL)
            
            if should_update:
                # 상태 데이터 정리
                shadow_state = {
                    "state": {
                        "reported": {
                            "hub_id": matterhub_id,
                            "timestamp": int(current_time),
                            "device_count": len(filtered_states),  # 관리되는 디바이스 수만
                            "total_devices": len(states),  # 전체 디바이스 수
                            "managed_devices": len(managed_devices),  # 관리 대상 디바이스 수
                            "online": True,
                            "ha_reachable": True,
                            "devices": {},
                            "has_changes": has_changes,
                            "change_count": len(changes) if has_changes else 0
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
                
                # 섀도우 업데이트 토픽으로 발행
                shadow_topic = f"$aws/things/{matterhub_id}/shadow/update"
                global_mqtt_connection.publish(
                    topic=shadow_topic,
                    payload=json.dumps(shadow_state),
                    qos=mqtt.QoS.AT_LEAST_ONCE
                )
                
                if has_changes:
                    print(f"🔔 변경사항 감지로 섀도우 업데이트: {len(changes)}개 변경")
                else:
                    last_heartbeat = current_time
                    print(f"💓 Heartbeat 섀도우 업데이트 (5.5시간 간격)")
                    
    except Exception as e:
        print(f"섀도우 업데이트 실패: {e}")

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
        qos=mqtt.QoS.AT_LEAST_ONCE
    )
    return

def handle_update_command(message):
    """업데이트 명령 처리"""
    try:
        command = message.get('command')
        update_id = message.get('update_id')
        branch = message.get('branch', 'master')
        force_update = message.get('force_update', False)
        
        if command == 'git_update':
            print(f"🚀 Git 업데이트 명령 수신: {update_id}")
            
            # 외부 스크립트 실행 (업데이트 ID와 Hub ID 전달)
            result = execute_external_update_script(branch, force_update, update_id)
            
            # 응답 전송
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
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            
            print(f"✅ Git 업데이트 응답 전송: {result}")
            
    except Exception as e:
        print(f"❌ Git 업데이트 실패: {e}")
        # 에러 응답 전송
        error_response = {
            'update_id': message.get('update_id'),
            'hub_id': matterhub_id,
            'timestamp': int(time.time()),
            'command': 'git_update',
            'status': 'failed',
            'error': str(e)
        }
        
        response_topic = f"matterhub/{matterhub_id}/update/response"
        global_mqtt_connection.publish(
            topic=response_topic,
            payload=json.dumps(error_response),
            qos=mqtt.QoS.AT_LEAST_ONCE
        )

def execute_external_update_script(branch='master', force_update=False, update_id='unknown'):
    """외부 업데이트 스크립트 실행"""
    try:
        import subprocess
        import os
        
        # 업데이트 스크립트 경로 (Git에서 가져온 최신 스크립트)
        script_path = "/home/hyodol/whatsmatter-hub-flask-server/update_server.sh"
        
        # 스크립트가 존재하는지 확인
        if not os.path.exists(script_path):
            return {
                'success': False,
                'error': 'Update script not found',
                'timestamp': int(time.time())
            }
        
        # 스크립트 실행 권한 확인 및 부여
        os.chmod(script_path, 0o755)
        
        print(f"🚀 외부 업데이트 스크립트 실행: {script_path}")
        print(f"📋 매개변수: branch={branch}, force_update={force_update}, update_id={update_id}, hub_id={matterhub_id}")
        
        # 백그라운드에서 스크립트 실행 (nohup 사용)
        # 매개변수: branch, force_update, update_id, hub_id
        force_flag = "true" if force_update else "false"
        cmd = f"nohup bash {script_path} {branch} {force_flag} {update_id} {matterhub_id} > /dev/null 2>&1 &"
        
        result = subprocess.run(cmd, shell=True, check=True)
        
        return {
            'success': True,
            'message': f'Update script started in background',
            'script_path': script_path,
            'branch': branch,
            'force_update': force_update,
            'update_id': update_id,
            'hub_id': matterhub_id,
            'timestamp': int(time.time())
        }
        
    except Exception as e:
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

    # Git 업데이트 명령 처리
    if topic == f"matterhub/{matterhub_id}/git/update" or topic == "matterhub/update/all" or topic.startswith("matterhub/update/region/") or topic.startswith("matterhub/update/specific/"):
        print(f"🚀 Git 업데이트 명령 수신: {topic}")
        handle_update_command(_message)
        return

    print(_message)

def config():
    if not os.path.exists(res_file_path):
        os.makedirs(res_file_path)
        print(f"폴더 생성: {res_file_path}")


    file_list = [schedules_file_path, rules_file_path, rooms_file_path, devices_file_path, notifications_file_path]
    
    for f in file_list:
        if not os.path.exists(f):
            with open(f, 'w') as f:
                json.dump([], f)
            print(f"{f} 파일이 생성되었습니다.")

# 사용 예시
if __name__ == "__main__":
    
    config()

    one_time = one_time_schedule()
    schedule_config(one_time)
    p = threading.Thread(target=periodic_scheduler)
    p.start()
    o = threading.Thread(target=one_time_scheduler, args=[one_time])
    o.start()

    try:
        aws_client = AWSIoTClient()
        global_mqtt_connection = aws_client.connect_mqtt()
        print("MQTT 연결 성공")
    except Exception as e:
        print(f"[에러] MQTT 연결 실패: {e}")
        sys.exit(1)  # ← 이걸로 PM2가 재시작하게 됨
    
    # hello 토픽 구독
    subscribe_future, packet_id = global_mqtt_connection.subscribe(
        topic=f"matterhub/{matterhub_id}/api",
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=mqtt_callback
    )
    
    subscribe_result = subscribe_future.result()
    print(f"matterhub/{matterhub_id}/api 토픽 구독 완료")

    subscribe_future, packet_id = global_mqtt_connection.subscribe(
        topic=f"matterhub/api",
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=mqtt_callback
    )
    subscribe_result = subscribe_future.result()
    print(f"matterhub/api 토픽 구독 완료")

 # 전체 그룹 토픽 구독
    GROUP_TOPIC = "matterhub/group/all/api"
    subscribe_future, packet_id = global_mqtt_connection.subscribe(
        topic=GROUP_TOPIC,
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=mqtt_callback
    )
    subscribe_result = subscribe_future.result()
    print(f"{GROUP_TOPIC} 토픽 구독 완료")

    # 원격 업데이트 명령 토픽 구독 (브로드캐스트/지역/개별)
    update_topics = [
        "matterhub/update/all",
        f"matterhub/update/region/+",
        f"matterhub/update/specific/{matterhub_id}",
    ]
    for ut in update_topics:
        subscribe_future, packet_id = global_mqtt_connection.subscribe(
            topic=ut,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=mqtt_callback
        )
        subscribe_future.result()
        print(f"{ut} 토픽 구독 완료")


    # 테스트용 데이터 publish
    test_data = {
        "message": "테스트 메시지",
        "timestamp": time.time()
    }
    

    
    try:
        # 최적화된 메인 루프
        connection_check_counter = 0
        
        while True:
            # 섀도우 업데이트 실행 (변경사항 감지 기반)
            update_device_shadow()
            
            # 60초마다 MQTT 연결 상태 확인 (5초 × 12 = 60초)
            connection_check_counter += 1
            if connection_check_counter >= 12:
                check_mqtt_connection()
                connection_check_counter = 0
            
            # 더 긴 대기 시간으로 CPU 사용량 감소
            time.sleep(5)  # 1초 → 5초로 변경
            
    except KeyboardInterrupt:
        print("프로그램 종료")
        global_mqtt_connection.disconnect()
        
