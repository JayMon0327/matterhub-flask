#!/bin/bash
# PM2 + venv 원클릭 설치 (패키지 설치 ~ PM2 기동까지 한 스크립트)
# 사용법: 프로젝트 루트에서 실행 (sudo 없이 실행 권장)
#   chmod +x device_config/server_install_pm2.sh
#   ./device_config/server_install_pm2.sh
#
# ⚠️ sudo로 실행하면 PM2가 root로 떠서, 로그/재시작은 반드시 sudo pm2 logs, sudo pm2 list 로 확인.
# 필요: pm2 설치됨 (npm install -g pm2). Debian/Ubuntu에서는 시스템 pip 대신 venv 사용.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# sudo로 실행 중이면 PM2가 root 소유로 떠서, 이후 pm2 list/logs 도 반드시 sudo 로 봐야 함
if [ "$(id -u)" = "0" ]; then
  echo "⚠️  root로 실행 중입니다. 가능하면 sudo 없이 실행하세요 (pm2 list / pm2 logs 를 sudo 없이 쓰려면)."
fi
echo "=== 프로젝트 루트: $PROJECT_ROOT ==="

# --- 1) Python 가상환경 가능 여부 (python3-venv) ---
if ! python3 -c "import ensurepip" 2>/dev/null; then
  PYVER=$(python3 -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "python3")
  PKG="${PYVER}-venv"
  echo "[1/3] 가상환경용 패키지 설치: $PKG (sudo 필요)"
  sudo apt update && sudo apt install -y "$PKG" || {
    echo "실패. 직접 실행 후 다시 이 스크립트 실행: sudo apt install -y $PKG"
    exit 1
  }
fi

# --- 2) venv 생성 + 패키지 설치 ---
echo "[2/3] 가상환경 생성 및 패키지 설치..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# --- 3) PM2로 기동 ---
if ! command -v pm2 &>/dev/null; then
  echo "PM2 없음. 설치 후 다시 실행: npm install -g pm2"
  exit 1
fi
echo "[3/3] PM2로 앱 시작 (cwd는 프로젝트 루트)..."
# 확장자 .json 이 없으면 PM2가 ecosystem이 아니라 스크립트로 인식해 'tmp' 한 개만 뜨고 실패함
STARTUP_TMP=$(mktemp).json
sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" device_config/startup.json > "$STARTUP_TMP"
pm2 start "$STARTUP_TMP"
rm -f "$STARTUP_TMP"
pm2 save
pm2 list

echo ""
echo "=== 완료. 재시작: pm2 restart all / 로그: pm2 logs ==="
