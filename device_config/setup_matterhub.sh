#!/bin/bash

# 1. 환경 변수 설정 (사용자 환경에 맞게 수정 가능)
RCP_DEVICE="/dev/serial/by-id/usb-Nordic_Semiconductor_nRF528xx_OpenThread_Device_FBB261EC858A-if01"
INFRA_IF="wlan0"
INSTALL_DIR="$HOME/matterhub-platform"

echo "==========================================="
echo " MatterHub Platform 통합 설치를 시작합니다 "
echo "==========================================="

# 2. 기존 Supervisor 및 컨테이너 정리 (충돌 방지)
echo "▶ 1단계: 기존 Supervisor 및 Docker 컨테이너 정리"
sudo systemctl stop hassio-supervisor.service 2>/dev/null
sudo systemctl disable hassio-supervisor.service 2>/dev/null
if [ "$(sudo docker ps -q)" ]; then
    sudo docker stop $(sudo docker ps -q)
fi

# 3. 저장소 클론 및 설치
echo "▶ 2단계: MatterHub 플랫폼 설치 (소스 빌드 포함)"
if [ -d "$INSTALL_DIR" ]; then
    echo "기존 디렉토리가 존재하여 업데이트를 진행합니다."
    cd "$INSTALL_DIR" && git pull
else
    git clone https://github.com/JayMon0327/matterhub-platform.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
chmod +x ./scripts/install.sh
# 가이드 주신 대로 설치 스크립트 실행
RCP_DEVICE=$RCP_DEVICE INFRA_IF=$INFRA_IF sudo --preserve-env=HOME,RCP_DEVICE,INFRA_IF ./scripts/install.sh

# 4. 포트 및 외부 접속 설정 (0.0.0.0:8080 및 8081 최적화)
echo "▶ 3단계: 포트 바인딩 및 외부 접속 설정 (8080, 8081)"

# OTBR Web UI 설정 (0.0.0.0:8080 허용)
echo "OTBR_WEB_OPTS=\"-I wpan0 -p 8080 -a 0.0.0.0\"" | sudo tee /etc/default/otbr-web

# OTBR Agent 설정 확인 및 디버그 모드 추가
sudo sed -i "s|OTBR_AGENT_OPTS=\"|OTBR_AGENT_OPTS=\"-d7 |" /etc/default/otbr-agent

# 5. 서비스 재시작 및 확인
echo "▶ 4단계: 서비스 재시작 및 상태 확인"
sudo systemctl daemon-reload
sudo systemctl restart otbr-agent
sudo systemctl restart otbr-web

echo "-------------------------------------------"
echo "설치가 완료되었습니다!"
echo "1. HomeAssistant: http://$(hostname -I | awk '{print $1}'):8123"
echo "2. OTBR Web UI: http://$(hostname -I | awk '{print $1}'):8080"
echo "3. OTBR REST API: http://127.0.0.1:8081 (Docker 내 HA에서 접근 가능)"
echo "-------------------------------------------"

# 최종 포트 리스닝 상태 출력
sudo ss -tulpn | grep -E '8080|8081|8123|5580'
