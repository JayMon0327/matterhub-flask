import schedule
import time
import requests
import json
import threading
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path='.env')
HA_host = os.environ.get('HA_host')
hass_token = os.environ.get('hass_token')

class one_time_schedule():

    def __init__(self):
        self.one_time_schedule_list = []

    def add_schedule(self, s):
        self.one_time_schedule_list.append(s)

    def run_pending(self):
        for s in self.one_time_schedule_list:
            target_time = datetime.strptime(s['schedule']['datetime'], "%Y-%m-%d %H:%M")
            current_time = datetime.now()
            current_time = current_time.replace(second=0, microsecond=0)

            if current_time < target_time:
                pass
            elif current_time > target_time:
                pass
            else:
                service(s['condition'], s['action']['domain'], s['action']['service'], s['action']['entity_id'])
            



def checkCondition(condition):
    for c in condition:
        headers = {"Authorization": f"Bearer {hass_token}"}

        response = requests.get(f"{HA_host}/api/states/{c['entity_id']}", headers=headers)
        response = json.loads(response.content)

        if(c['option']==""):
            if (response['state'] == c['state']):
                pass
            else : 
                return False
        if(c['option']=="equal"):
            if (response['state'] == c['state']):
                pass
            else : 
                return False
    
    return True


def executeActions(schedule):
    if isinstance(schedule['action'], dict):
        service(schedule['condition'], schedule['action']['domain'], schedule['action']['service'], schedule['action']['entity_id'])
    if isinstance(schedule['action'], list):
        for r in schedule['action']:
            service(schedule['condition'], r['domain'], r['service'], r['entity_id'])


def service(condition, domain, service, entity):
    try : 
        if (checkCondition(condition)):
            headers = {"Authorization": f"Bearer {hass_token}"}
            body = {"entity_id": entity}

            response = requests.post(f"{HA_host}/api/services/{domain}/{service}", data=json.dumps(body), headers=headers)
            print(response)
            print(response.content)
    except Exception as e:
        # 예외가 발생했을 때 실행되는 코드
        print(f"An error occurred: {type(e).__name__}")
        print(f"Error details: {e}")


def schedule_config(one_time):
    schedule.clear()

    schedules_path = os.environ.get('schedules_file_path', 'resources/schedule.json')
    with open(schedules_path, 'r', encoding='utf-8') as file:
        data = json.load(file)  # JSON 파일을 파싱하여 Python 객체로 변환
        for _schedule_data in data:
            if(not _schedule_data['activate']):
                continue
            if(_schedule_data['schedule']['type'] == "periodic"):
                if(_schedule_data['schedule']['period']['rate'] == "seconds"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).seconds.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).seconds.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "minutes"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).minutes.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).minutes.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "hours"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).hours.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).hours.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "days"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).days.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).days.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "weeks"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).weeks.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).weeks.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "monday"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).monday.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).monday.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "tuesday"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).tuesday.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).tuesday.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "wednesday"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).wednesday.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).wednesday.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "thursday"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).thursday.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).thursday.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "friday"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).friday.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).friday.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "saturday"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).saturday.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).saturday.do(executeActions,_schedule_data)
                if(_schedule_data['schedule']['period']['rate'] == "sunday"):
                    if(_schedule_data['schedule']['period']['at'] != ""):
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).sunday.at(_schedule_data['schedule']['period']['at']).do(executeActions,_schedule_data)
                    else:
                        schedule.every(int(_schedule_data['schedule']['period']['value'])).sunday.do(executeActions,_schedule_data)


            if(_schedule_data['schedule']['type'] == "one-time"):
                one_time.add_schedule(_schedule_data)

def periodic_scheduler():
    while 1:
        schedule.run_pending()
        time.sleep(1)

def one_time_scheduler(one_time):
    prev_datetime = ''

    while 1:
        cur_datetime = datetime.now().minute
        if(prev_datetime!=cur_datetime):
            one_time.run_pending()
            prev_datetime = cur_datetime
        time.sleep(10)



################# start ##################

if (__name__ == "__main__"):
    one_time = one_time_schedule()

    schedule_config(one_time)
    p = threading.Thread(target=periodic_scheduler)
    p.start()
    o = threading.Thread(target=one_time_scheduler, args=(one_time,))
    o.start()

    