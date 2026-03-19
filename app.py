### BEGIN INIT INFO
# Provides:          scriptname
# Required-Start:    $remote_fs $syslog
# Required-Stop:     $remote_fs $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Start daemon at boot time
# Description:       Enable service provided by daemon.
### END INIT INFO

from flask import Flask, request, jsonify
import requests
import json
import threading
from sub.scheduler import *
from sub.ruleEngine import *
from dotenv import load_dotenv
import os, sys

from libs.device_binding import enforce_mac_binding
from libs.edit import deleteItem, file_changed_request, putItem, update_env_file  # type: ignore
from wifi_config.api import create_wifi_blueprint
from wifi_config.bootstrap import ensure_bootstrap_ap, watch_disconnection_and_start_ap

load_dotenv(dotenv_path='.env')

if not enforce_mac_binding():
    raise SystemExit(1)

res_file_path= os.environ.get('res_file_path')
cert_file_path= os.environ.get('cert_file_path')
schedules_file_path = os.environ.get('schedules_file_path')
rules_file_path = os.environ.get('rules_file_path')
rooms_file_path = os.environ.get('rooms_file_path')
devices_file_path = os.environ.get('devices_file_path')
notifications_file_path = os.environ.get('notifications_file_path')

HA_host = os.environ.get('HA_host')
hass_token = os.environ.get('hass_token')


def config():

    if not os.path.exists(res_file_path):
        os.makedirs(res_file_path)
        print(f"폴더 생성: {res_file_path}")

    if not os.path.exists(cert_file_path):
        os.makedirs(cert_file_path)
        print(f"폴더 생성: {cert_file_path}")

    file_list = [schedules_file_path, rules_file_path, rooms_file_path, devices_file_path, notifications_file_path]
    
    for f in file_list:
        if not os.path.exists(f):
            with open(f, 'w') as f:
                json.dump([], f)

            print(f"{f} 파일이 생성되었습니다.")



app = Flask(__name__)
app.register_blueprint(create_wifi_blueprint())


def _start_wifi_bootstrap_thread() -> None:
    def _run() -> None:
        result = ensure_bootstrap_ap()
        print(
            "[WIFI][BOOTSTRAP] result "
            f"reason={result.get('reason')} started={result.get('started')}"
        )

    threading.Thread(target=_run, daemon=True, name="wifi-bootstrap").start()


_start_wifi_bootstrap_thread()


def _start_wifi_watchdog_thread() -> None:
    threading.Thread(
        target=watch_disconnection_and_start_ap,
        daemon=True,
        name="wifi-ap-watchdog",
    ).start()


_start_wifi_watchdog_thread()

@app.route('/test', methods=['POST'])
def test():
    return '@@@', 200

@app.route('/local/api/config/ha/cert', methods=["POST","DELETE", "PUT"])
def configHACert():
    hass_token = request.json["hass_token"]

    env_file_path = '.env'
    update_env_file(env_file_path, 'hass_token', hass_token)

    return 'Success', 200

@app.route('/local/api/config/aws/cert', methods=["POST","DELETE", "PUT"])
def configAwsCert():
    root_ca = request.json["root_ca"]
    certificate = request.json["certificate"]
    private_key = request.json["private_key"]

    # 업데이트된 데이터를 JSON 파일에 다시 저장5
    with open('./cert/root-CA.crt', 'w', encoding='utf-8') as file:
        file.write(root_ca)
    with open('./cert/matterHub.cert.pem', 'w', encoding='utf-8') as file:
        file.write(certificate)
    with open('./cert/matterHub.private.key', 'w', encoding='utf-8') as file:
        file.write(private_key)

    return 'Success', 200
    
@app.route('/local/api/config/aws/id', methods=["POST","DELETE", "PUT"])
def configAwsId():
    matterhub_id = request.json["matterhub_id"]
    certificate = request.json["certificate"]
    private_key = request.json["private_key"]

    env_file_path = '.env'
    update_env_file(env_file_path, 'matterhub_id', matterhub_id)

    return 'Success', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    return jsonify({
        "status": "disabled",
        "message": "webhook update is disabled; use reverse tunnel maintenance workflow",
    }), 410

@app.route('/local/api', methods=["POST","DELETE", "PUT", "GET"])
def home():
    headers = {"Authorization": f"Bearer {hass_token}"}
    response = requests.get(f"{HA_host}/api/", headers=headers)
    
    return str(response.json())

@app.route('/local/api/services')
def services():
    headers = {"Authorization": f"Bearer {hass_token}"}
    response = requests.get(f"{HA_host}/api/services", headers=headers)
    return jsonify(response.json())

@app.route('/local/api/states')
def states():
    """
    HA /api/states 프록시.
    - HA 쪽에서 JSON 이 아닌 응답(HTML, 에러페이지 등)을 주면 json()에서 예외가 나므로
      이를 잡아서 에러 내용을 그대로 반환하고, HTTP 상태코드를 함께 노출한다.
    """
    headers = {"Authorization": f"Bearer {hass_token}"}
    resp = requests.get(f"{HA_host}/api/states", headers=headers)
    try:
        data = resp.json()
    except Exception as e:  # JSONDecodeError, ValueError 등
        # 디버깅을 위해 앞부분 텍스트를 로그/응답에 남긴다.
        text_snippet = resp.text[:200] if resp.text else ""
        return jsonify({
            "error": "ha_states_invalid_json",
            "message": str(e),
            "status_code": resp.status_code,
            "body_snippet": text_snippet,
        }), 502
    return jsonify(data)

@app.route('/local/api/states/<entity_id>')
def statesEntityId(entity_id):
    headers = {"Authorization": f"Bearer {hass_token}"}
    response = requests.get(f"{HA_host}/api/states/{entity_id}", headers=headers)
    return jsonify(response.json())

@app.route('/local/api/devices/<entity_id>/command', methods=["POST"])
def device_command(entity_id):
    headers = {"Authorization": f"Bearer {hass_token}"}
    body = {
        "entity_id": entity_id
        }
    _r = {**request.json}
    _r.pop('domain')
    _r.pop('service')
    merged_dict = {**body, **_r}
    print(merged_dict)
    response = requests.post(f"{HA_host}/api/services/{request.json['domain']}/{request.json['service']}", data=json.dumps(merged_dict), headers=headers)
    return jsonify(response.json()) 

@app.route('/local/api/devices/<entity_id>/status', methods=["GET"])
def device_status(entity_id):
    headers = {"Authorization": f"Bearer {hass_token}"}
    response = requests.get(f"{HA_host}/api/states/{entity_id}", headers=headers)
    return jsonify(response.json()) 

@app.route('/local/api/devices/<entity_id>/services', methods=["GET"])
def device_services(entity_id):
    target_entity = entity_id
    target_domain = target_entity.split('.')[0]

    url = f"{HA_host}/api/services"
    headers = {"Authorization": f"Bearer {hass_token}"}
    response = requests.get(url, headers=headers)
    all_domain = json.loads(response.content)

    for d in all_domain:
        if(d['domain'] == target_domain):
            switch_services = d['services']
            return jsonify(switch_services) 
    return jsonify({}) 


@app.route('/local/api/devices', methods=["POST","DELETE", "PUT", "GET"])
def devices():
    try:
        with open(devices_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        data = []  # 파일이 없으면 빈 리스트로 초기화
    
    if request.method == "POST":
        new_data = request.json
        data.append(new_data)
    if request.method == "DELETE":
        target_value =  request.json['entity_id']
        data = deleteItem(data, "entity_id", target_value)
    if request.method == "PUT":
        target_value =  request.json['entity_id']
        data = putItem(data, "entity_id", target_value, request.json)
    if request.method == "GET":
        pass

    # 업데이트된 데이터를 JSON 파일에 다시 저장5
    with open(devices_file_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    schedule_config(one_time)
    return jsonify(data)



@app.route('/local/api/schedules', methods=["POST","DELETE", "PUT", "GET"])
def schdules():
    # 파일에서 기존 데이터 읽기
    try:
        with open(schedules_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        data = []  # 파일이 없으면 빈 리스트로 초기화
    
    if request.method == "POST":
        new_data = request.json
        new_data.setdefault('activate', True)
        data.append(new_data)
    if request.method == "DELETE":
        target_value =  request.json['id']
        data = deleteItem(data, "id", target_value)
    if request.method == "PUT":
        target_value =  request.json['id']
        data = putItem(data, "id", target_value, request.json)
    if request.method == "GET":
        pass

    # 업데이트된 데이터를 JSON 파일에 다시 저장5
    with open(schedules_file_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    if(request.method!="GET"):
        schedule_config(one_time)
    return jsonify(data)

@app.route('/local/api/schedules/<schedule_id>', methods=["POST","DELETE", "PUT", "GET"])
def schdules_id(schedule_id):
    # 파일에서 기존 데이터 읽기
    try:
        with open(schedules_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        data = []  # 파일이 없으면 빈 리스트로 초기화

    if request.method == "POST":
        new_data = request.json
        new_data.setdefault('activate', True)
        data.append(new_data)
    if request.method == "DELETE":
        data = deleteItem(data, "id", schedule_id)
    if request.method == "PUT":
        data = putItem(data, "id", schedule_id, request.json)
    if request.method == "GET":
        pass

    # 업데이트된 데이터를 JSON 파일에 다시 저장5
    with open(schedules_file_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    if(request.method!="GET"):
        schedule_config(one_time)

    return jsonify(data)

@app.route('/local/api/rules', methods=["POST","DELETE", "PUT", "GET"])
def rules():
    
    try:
        with open(rules_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        data = []  # 파일이 없으면 빈 리스트로 초기화

    if request.method == "POST":
        new_data = request.json
        new_data.setdefault('activate', True)
        data.append(new_data)
    if request.method == "DELETE":
        target_value =  request.json['id']
        data = deleteItem(data, "id", target_value)
    if request.method == "PUT":
        target_value =  request.json['id']
        data = putItem(data, "id", target_value, request.json)
    if request.method == "GET":
        pass

    # 업데이트된 데이터를 JSON 파일에 다시 저장5
    with open(rules_file_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    if(request.method!="GET"):
        file_changed_request("rules_file_changed")
    return jsonify(data)

@app.route('/local/api/rooms', methods=["POST","DELETE", "PUT", "GET"])
def rooms():
    try:
        with open(rooms_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        data = []  # 파일이 없으면 빈 리스트로 초기화

    if request.method == "POST":
        new_data = request.json
        new_data.setdefault('activate', True)
        data.append(new_data)
    if request.method == "DELETE":
        target_value =  request.json['id']
        data = deleteItem(data, "id", target_value)
    if request.method == "PUT":
        target_value =  request.json['id']
        data = putItem(data, "id", target_value, request.json)
    if request.method == "GET":
        pass

    # 업데이트된 데이터를 JSON 파일에 다시 저장5
    with open(rooms_file_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    schedule_config(one_time)
    return jsonify(data)
    
@app.route('/local/api/notifications', methods=["POST","DELETE", "PUT", "GET"])
def notifications():
    try:
        with open(notifications_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        data = []  # 파일이 없으면 빈 리스트로 초기화

    if request.method == "POST":
        new_data = request.json
        data.append(new_data)
    if request.method == "DELETE":
        target_value =  request.json['id']
        data = deleteItem(data, "id", target_value)
    if request.method == "PUT":
        target_value =  request.json['id']
        data = putItem(data, "id", target_value, request.json)
    if request.method == "GET":
        pass

    # 업데이트된 데이터를 JSON 파일에 다시 저장5
    with open(notifications_file_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

    if(request.method!="GET"):
        res= file_changed_request("notifications_file_changed")
        print(res.content)
    return jsonify(data)

@app.route('/local/api/matterhub/id', methods=["GET"])
def matterhub_id():
    matterhub_id = os.environ.get('matterhub_id', '').strip('"')
    return jsonify({"matterhub_id": matterhub_id})


config()

one_time = one_time_schedule()
schedule_config(one_time)
p = threading.Thread(target=periodic_scheduler)
p.start()
o = threading.Thread(target=one_time_scheduler, args=[one_time])
o.start()

if __name__ == '__main__':
    # 운영 환경(systemd)에서는 debug/reloader 비활성화가 기본이다.
    # 필요할 때만 WM_DEBUG=1 로 켜서 사용.
    _debug = os.environ.get("WM_DEBUG", "0").strip().lower() in ("1", "true", "yes", "y")
    app.run('0.0.0.0', debug=_debug, use_reloader=_debug, port=8100)
