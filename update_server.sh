#!/bin/bash

# ========================================
# WhatsMatter Hub 자동 업데이트 스크립트
# ========================================
# 
# 사용법:
# ./update_server.sh [branch] [force_update] [update_id] [hub_id]
# 
# 매개변수:
#   branch: Git 브랜치 (기본값: master)
#   force_update: 강제 업데이트 여부 (기본값: false)
#   update_id: 업데이트 ID (기본값: unknown)
#   hub_id: Hub ID (기본값: unknown)
#
# 예시:
#   ./update_server.sh master false update_20241201_143022 whatsmatter-nipa_SN-1752303557
#   ./update_server.sh develop true update_20241201_143022 whatsmatter-nipa_SN-1752303558
# ========================================

# 로그 파일 설정
LOG_FILE="/home/hyodol/patch_matterhub.log"
echo "=== MQTT 자동 업데이트 시작 $(date) ===" | tee -a "$LOG_FILE"

# 매개변수 처리
BRANCH=${1:-"master"}
FORCE_UPDATE=${2:-"false"}
UPDATE_ID=${3:-"unknown"}
HUB_ID=${4:-"unknown"}

echo "[INFO] 업데이트 매개변수:" | tee -a "$LOG_FILE"
echo "[INFO]   - 브랜치: $BRANCH" | tee -a "$LOG_FILE"
echo "[INFO]   - 강제 업데이트: $FORCE_UPDATE" | tee -a "$LOG_FILE"
echo "[INFO]   - 업데이트 ID: $UPDATE_ID" | tee -a "$LOG_FILE"
echo "[INFO]   - Hub ID: $HUB_ID" | tee -a "$LOG_FILE"

# 📌 대상 파일 경로
cd /home/hyodol/whatsmatter-hub-flask-server/

echo "[INFO] Git 업데이트 시작" | tee -a "$LOG_FILE"

# 현재 remote 설정 확인
echo "[INFO] 현재 Git remote 설정:" | tee -a "$LOG_FILE"
git remote -v | tee -a "$LOG_FILE"

echo "[INFO] Git pull 시작 (브랜치: $BRANCH)" | tee -a "$LOG_FILE"

# 현재 브랜치 확인
CURRENT_BRANCH=$(git branch --show-current)
echo "[INFO] 현재 브랜치: $CURRENT_BRANCH" | tee -a "$LOG_FILE"

# 브랜치가 다르면 체크아웃
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    echo "[INFO] 브랜치 변경: $CURRENT_BRANCH → $BRANCH" | tee -a "$LOG_FILE"
    git checkout $BRANCH
    if [ $? -ne 0 ]; then
        echo "[ERROR] 브랜치 체크아웃 실패: $BRANCH" | tee -a "$LOG_FILE"
        exit 1
    fi
fi

# Git pull 실행
git pull origin $BRANCH

if [ $? -eq 0 ]; then
    echo "[INFO] Git pull 성공" | tee -a "$LOG_FILE"
    
    # 최신 커밋 정보 출력
    LATEST_COMMIT=$(git log -1 --oneline)
    echo "[INFO] 최신 커밋: $LATEST_COMMIT" | tee -a "$LOG_FILE"
else
    echo "[ERROR] Git pull 실패" | tee -a "$LOG_FILE"
    exit 1
fi

# 강제 업데이트가 필요한 경우
if [ "$FORCE_UPDATE" = "true" ]; then
    echo "[INFO] 강제 업데이트 모드 - 하드 리셋 실행" | tee -a "$LOG_FILE"
    git reset --hard origin/$BRANCH
    if [ $? -eq 0 ]; then
        echo "[INFO] 강제 업데이트 완료" | tee -a "$LOG_FILE"
    else
        echo "[ERROR] 강제 업데이트 실패" | tee -a "$LOG_FILE"
        exit 1
    fi
fi

# 🚨 중요: Git 업데이트 완료 후 MQTT 응답 전송
echo "[INFO] Git 업데이트 완료. MQTT 응답 전송 중..." | tee -a "$LOG_FILE"

# MQTT 응답 전송 (Python 스크립트 사용)
python3 << EOF
import json
import time
import subprocess

# 응답 데이터
response_data = {
    'update_id': '$UPDATE_ID',
    'hub_id': '$HUB_ID',
    'timestamp': int(time.time()),
    'command': 'git_update',
    'status': 'success',
    'message': 'Git update completed successfully. Latest commit: $LATEST_COMMIT',
    'branch': '$BRANCH',
    'force_update': '$FORCE_UPDATE',
    'latest_commit': '$LATEST_COMMIT'
}

# MQTT 응답 전송 (mosquitto_pub 사용)
response_topic = f"matterhub/{'$HUB_ID'}/update/response"
try:
    subprocess.run([
        'mosquitto_pub',
        '-h', 'localhost',  # MQTT 브로커 주소
        '-t', response_topic,
        '-m', json.dumps(response_data)
    ], check=True)
    print(f"✅ MQTT 응답 전송 완료: success")
except Exception as e:
    print(f"❌ MQTT 응답 전송 실패: {e}")
EOF

# 🚨 중요: MQTT 응답 전송 후 20초 대기
echo "[INFO] MQTT 응답 전송 완료. 20초 대기 중..." | tee -a "$LOG_FILE"
sleep 20

# PM2 경로 설정
PM2="/home/hyodol/.nvm/versions/node/v22.17.0/bin/pm2"

echo "[INFO] PM2 프로세스 재시작 시작" | tee -a "$LOG_FILE"

# 1. wm-mqtt 프로세스 중지 (자기 자신)
echo "[INFO] wm-mqtt 중지 중..." | tee -a "$LOG_FILE"
$PM2 stop wm-mqtt
sleep 5

# 2. wm-mqtt 프로세스 삭제
echo "[INFO] wm-mqtt 삭제 중..." | tee -a "$LOG_FILE"
$PM2 delete wm-mqtt
sleep 5

# 3. 다른 프로세스들도 중지 및 삭제
echo "[INFO] 다른 프로세스들 중지 및 삭제 중..." | tee -a "$LOG_FILE"
$PM2 delete wm-localIp
$PM2 delete wm-notifier
$PM2 delete wm-ruleEngine
$PM2 delete wm-app
sleep 10

# 4. 새로운 코드로 프로세스 시작
echo "[INFO] 새로운 코드로 프로세스 시작 중..." | tee -a "$LOG_FILE"
cd /home/hyodol/whatsmatter-hub-flask-server

# startup.json이 있는지 확인
if [ -f "startup.json" ]; then
    echo "[INFO] startup.json 사용하여 프로세스 시작" | tee -a "$LOG_FILE"
    
    $PM2 start startup.json
    
else
    echo "[INFO] startup.json 없음 - 개별 프로세스 시작" | tee -a "$LOG_FILE"
    
    # 개별 프로세스 시작 (startup.json이 없는 경우)
    cd /home/hyodol/whatsmatter-hub-flask-server
    
    # wm-mqtt 시작
    echo "[INFO] wm-mqtt 시작 중..." | tee -a "$LOG_FILE"
    $PM2 start mqtt.py --name wm-mqtt --interpreter python3
    sleep 3
    
    # 다른 프로세스들 시작 (필요시)
    # $PM2 start app.py --name wm-app --interpreter python3
    # sleep 2
fi

echo "[INFO] PM2 설정 저장 중..." | tee -a "$LOG_FILE"
$PM2 save

echo "[INFO] PM2 상태 확인" | tee -a "$LOG_FILE"
$PM2 list

# 업데이트 완료 확인
echo "[INFO] 업데이트 완료 확인 중..." | tee -a "$LOG_FILE"

# 프로세스 상태 확인
RUNNING_PROCESSES=$($PM2 list | grep "online" | wc -l)
TOTAL_PROCESSES=$($PM2 list | grep -E "(wm-|online|stopped|error)" | wc -l)

echo "[INFO] 프로세스 상태:" | tee -a "$LOG_FILE"
echo "[INFO]   - 실행 중: $RUNNING_PROCESSES" | tee -a "$LOG_FILE"
echo "[INFO]   - 전체: $TOTAL_PROCESSES" | tee -a "$LOG_FILE"

if [ $RUNNING_PROCESSES -gt 0 ]; then
    echo "[INFO] ✅ 업데이트 성공: $RUNNING_PROCESSES개 프로세스 실행 중" | tee -a "$LOG_FILE"
else
    echo "[WARN] ⚠️ 업데이트 완료되었지만 실행 중인 프로세스가 없음" | tee -a "$LOG_FILE"
fi

echo "[INFO] 패치 완료 $(date)" | tee -a "$LOG_FILE"
echo "=== MQTT 자동 업데이트 완료 ===" | tee -a "$LOG_FILE"

# 성공적으로 완료
exit 0
