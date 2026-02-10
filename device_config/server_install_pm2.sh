#!/bin/bash
# venv + 패키지만 설치 (PM2 기동은 따로 실행)
# 사용법: 프로젝트 루트에서 실행
#   chmod +x device_config/server_install_pm2.sh
#   ./device_config/server_install_pm2.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

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

