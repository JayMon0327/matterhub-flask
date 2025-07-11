import socket
import time

import requests

def get_local_ip():
    try:
        # 가상의 외부 서버에 연결을 시도해 로컬 IP를 가져옵니다.
        # 여기서 1.1.1.1:53은 클라우드플레어 DNS 서버를 이용합니다.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 53))
            local_ip = s.getsockname()[0]
        return local_ip
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    # 아래 내용을 1시간 마다 반복
    while True:
        try:
            ip = get_local_ip()
            print(f"[localIp.py] Local IP Address: {ip}")
            
            # 로컬 서버에 현재 ip 전송
            requests.get(f'http://localhost:8000/matter?ip={ip}')
    
            time.sleep(3600)
        except Exception as e:
            print(f"[localIp.py] Error: {e}")
            pass
        
