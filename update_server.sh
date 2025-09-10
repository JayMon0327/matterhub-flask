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

# 🛡️ Git 상태 정리 및 안전한 업데이트
echo "[INFO] Git 상태 정리 및 안전한 업데이트 시작..." | tee -a "$LOG_FILE"

# 1. .env 파일 특별 처리
if [ -f .env ]; then
    echo "[INFO] .env 파일 처리 중..." | tee -a "$LOG_FILE"
    
    # .env 백업
    cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
    
    # .env를 Git에서 제외 (임시)
    git update-index --assume-unchanged .env
    
    # .env 변경사항 되돌리기
    git checkout -- .env
    
    echo "[INFO] ✅ .env 파일 처리 완료" | tee -a "$LOG_FILE"
fi

# 2. 다른 변경사항들 처리
if git diff --quiet && git diff --cached --quiet; then
    echo "[INFO] 작업 디렉토리 깨끗함. Git pull 진행..." | tee -a "$LOG_FILE"
else
    echo "[INFO] 변경사항 발견. 정리 중..." | tee -a "$LOG_FILE"
    
    # 변경사항 stash
    git stash push -m "Auto-stash before update $(date)"
    echo "[INFO] 변경사항을 stash에 저장 완료" | tee -a "$LOG_FILE"
fi

# 3. Git pull 실행
echo "[INFO] Git pull 시작 (브랜치: $BRANCH)..." | tee -a "$LOG_FILE"
if git pull origin $BRANCH; then
    echo "[INFO] ✅ Git pull 성공!" | tee -a "$LOG_FILE"
    
    # 최신 커밋 정보 출력
    LATEST_COMMIT=$(git log -1 --oneline)
    echo "[INFO] 최신 커밋: $LATEST_COMMIT" | tee -a "$LOG_FILE"
    
    # 4. .env 보호 상태 복원
    if [ -f .env.backup.* ]; then
        echo "[INFO] .env 파일 보호 상태 복원 중..." | tee -a "$LOG_FILE"
        git update-index --skip-worktree .env
        
        # 백업 파일 정리
        rm -f .env.backup.*
        echo "[INFO] ✅ .env 파일 보호 상태 복원 완료" | tee -a "$LOG_FILE"
    fi
    
    # 5. stash된 변경사항 복원 시도
    if git stash list | grep -q "Auto-stash before update"; then
        echo "[INFO] stash된 변경사항 복원 시도 중..." | tee -a "$LOG_FILE"
        
        if git stash pop; then
            echo "[INFO] ✅ stash된 변경사항 복원 성공" | tee -a "$LOG_FILE"
        else
            echo "[WARN] ⚠️ stash 복원 실패 (충돌 발생). 변경사항은 stash에 보존됨" | tee -a "$LOG_FILE"
            git stash list | tee -a "$LOG_FILE"
        fi
    fi
    
else
    echo "[ERROR] ❌ Git pull 실패" | tee -a "$LOG_FILE"
    
    # 실패 시 .env 복구
    if [ -f .env.backup.* ]; then
        echo "[INFO] Git pull 실패. .env 파일 복구 중..." | tee -a "$LOG_FILE"
        cp .env.backup.* .env
        git update-index --skip-worktree .env
        echo "[INFO] ✅ .env 파일 복구 완료" | tee -a "$LOG_FILE"
    fi
    
    exit 1
fi

# 강제 업데이트가 필요한 경우
if [ "$FORCE_UPDATE" = "true" ]; then
    echo "[INFO] 강제 업데이트 모드 - .env 파일 완전 보호" | tee -a "$LOG_FILE"
    
    # 1. .env 파일 백업
    if [ -f .env ]; then
        echo "[INFO] 강제 업데이트 전 .env 파일 백업..." | tee -a "$LOG_FILE"
        cp .env .env.force_update.backup.$(date +%Y%m%d_%H%M%S)
    fi
    
    # 2. .env 파일을 Git에서 완전히 제외
    echo "[INFO] .env 파일을 Git에서 제외 중..." | tee -a "$LOG_FILE"
    git update-index --assume-unchanged .env
    
    # 3. 하드 리셋 실행
    echo "[INFO] 하드 리셋 실행 중..." | tee -a "$LOG_FILE"
    git reset --hard origin/$BRANCH
    
    if [ $? -eq 0 ]; then
        echo "[INFO] 강제 업데이트 완료" | tee -a "$LOG_FILE"
        
        # 4. .env 파일 복구 및 보호 설정
        if [ -f .env.force_update.backup.* ]; then
            echo "[INFO] .env 파일 복구 및 보호 설정 중..." | tee -a "$LOG_FILE"
            
            # 가장 최근 백업 파일 찾기
            LATEST_ENV_BACKUP=$(ls -t .env.force_update.backup.* | head -1)
            if [ -n "$LATEST_ENV_BACKUP" ]; then
                # 백업에서 .env 복구
                cp "$LATEST_ENV_BACKUP" .env
                
                # .env 파일을 Git에서 보호
                git update-index --skip-worktree .env
                
                echo "[INFO] ✅ .env 파일 복구 및 보호 완료" | tee -a "$LOG_FILE"
                
                # 백업 파일 정리
                rm -f .env.force_update.backup.*
            fi
        fi
    else
        echo "[ERROR] 강제 업데이트 실패" | tee -a "$LOG_FILE"
        
        # 실패 시 .env 복구
        if [ -f .env.force_update.backup.* ]; then
            echo "[INFO] 강제 업데이트 실패. .env 파일 복구 중..." | tee -a "$LOG_FILE"
            LATEST_ENV_BACKUP=$(ls -t .env.force_update.backup.* | head -1)
            if [ -n "$LATEST_ENV_BACKUP" ]; then
                cp "$LATEST_ENV_BACKUP" .env
                git update-index --skip-worktree .env
                echo "[INFO] ✅ .env 파일 복구 완료" | tee -a "$LOG_FILE"
            fi
        fi
        
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

sleep 10

$PM2 restart wm-mqtt

sleep 10
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
