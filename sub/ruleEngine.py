import asyncio
import websockets
import json
import requests
import os, time
from dotenv import load_dotenv

# wm-app가 먼저 리소스/파일을 생성할 시간을 주기 위해 약간 지연
time.sleep(5)
load_dotenv()
hass_token = os.environ.get('hass_token')
HA_host = os.environ.get('HA_host')

auth_body = {
    "type": "auth",
    "access_token": hass_token
}

subscribe_state_changed_body = {
    "id": 18,
    "type": "subscribe_events",
    "event_type": "state_changed"
}

subscribe_file_changed_body = {
    "id": 19,
    "type": "subscribe_events",
    "event_type": "rules_file_changed"
}

def get_rules():
    with open('resources/rules.json', 'r', encoding='utf-8') as file:
        data = json.load(file)

        return data
    return [] 



class rule_engine() : 
    def __init__(self):
        self.rules_list = get_rules()

    def file_reload(self):
        self.rules_list = get_rules()

    def add_rule(self, r):
        self.rules_list.append(r)

    def run_pending(self, event):
        for rule in self.rules_list:
            if(not rule['activate']):
                continue

            if(event['event']['data']['new_state']['entity_id'] == rule['trigger']['entity_id']):

                try:
                    _option = rule['trigger']['option']
                    
                except KeyError:
                    _option = "equal"
                
                
                
                if(_option == "equal"):
                    if(event['event']['data']['new_state']['state'] == rule['trigger']['state']):
                        executeActions(rule)
                    return 
                current_state = float(event['event']['data']['new_state']['state'])
                target_state = float(rule['trigger']['state'])
                if(_option == "greaterThan"):
                    if(current_state > target_state):
                        executeActions(rule)
                    return
                if(_option == "greaterThanOrEquals"):
                    if(current_state >= target_state):
                        executeActions(rule)
                    return
                if(_option == "lessThan"):
                    if(current_state < target_state):
                        executeActions(rule)
                    return
                if(_option == "lessThanOrEquals"):
                    if(current_state <= target_state):
                        executeActions(rule)
                    return
        return

def executeActions(rule):
    if isinstance(rule['action'], dict):
        service(rule['condition'], rule['action']['domain'], rule['action']['service'], rule['action']['entity_id'])
    if isinstance(rule['action'], list):
        for r in rule['action']:
            service(rule['condition'], r['domain'], r['service'], r['entity_id'])


def service(condition, domain, service, entity):
    if (checkCondition(condition)):
        headers = {"Authorization": f"Bearer {hass_token}"}
        body = {"entity_id": entity}

        response = requests.post(f"{HA_host}/api/services/{domain}/{service}", data=json.dumps(body), headers=headers)
        print(response)
        print(response.content)

def checkCondition(condition):
    if(condition ==[]) : 
        return True
    for c in condition:
        headers = {"Authorization": f"Bearer {hass_token}"}

        response = requests.get(f"{HA_host}/api/states/{c['entity_id']}", headers=headers)
        response = json.loads(response.content)

        current_state = response['state']
        target_state = c['state']
        if(c['option']==""):
            if (current_state == target_state):
                return True
            else : 
                return False
        if(c['option']=="equal"):
            if (current_state == target_state):
                return True
            else : 
                return False
            
        current_state = float(response['state'])
        target_state = float(c['state'])
        if(c['option']=="greaterThan"):
            if (current_state > target_state):
                return True
            else : 
                return False
        if(c['option']=="greaterThanOrEquals"):
            if (current_state >= target_state):
                return True
            else : 
                return False
        if(c['option']=="lessThan"):
            if (current_state < target_state):
                return True
            else : 
                return False
        if(c['option']=="lessThanOrEquals"):
            if (current_state <= target_state):
                return True
            else : 
                return False


    return False





async def subscribe(r):
    uri = f"ws://{HA_host.replace('http://','')}/api/websocket"
    while(1):
        try:
            async with websockets.connect(uri) as websocket:
                response = await websocket.recv()
                print(f"Received from server: {response}")

                await websocket.send(json.dumps(auth_body))
                response = await websocket.recv()
                print(f"Received from server: {response}")

                await websocket.send(json.dumps(subscribe_state_changed_body))
                response = await websocket.recv()
                print(f"Received from server: {response}")

                await websocket.send(json.dumps(subscribe_file_changed_body))
                response = await websocket.recv()
                print(f"Received from server: {response}")

                while(1):
                    response = await websocket.recv()
                    print(f"Received from server: {response}")
                    event = json.loads(response)
                    try : 
                        if(event['event']['event_type']=="state_changed"):
                            r.run_pending(event)
                        if(event['event']['event_type']=="rules_file_changed"):
                            r.file_reload()
                    except Exception as e:
                        # 예외가 발생했을 때 실행되는 코드
                        print(f"An error occurred: {type(e).__name__}")
                        print(f"Error details: {e}")
        except Exception as e:
            # 예외가 발생했을 때 실행되는 코드
            print(f"An error occurred: {type(e).__name__}")
            print(f"Error details: {e}")
            time.sleep(5)
if __name__ == "__main__":
    r = rule_engine()

    asyncio.run(subscribe(r))
