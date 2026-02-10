#!/bin/bash
# 프로젝트 가상환경 생성 및 의존성 설치 (라즈베리파이 등 externally-managed-environment 대응)
# 사용법: 프로젝트 루트에서 실행 (device_config의 상위 폴더)
#   chmod +x device_config/setup_venv.sh
#   ./device_config/setup_venv.sh
# 필요 시 스크립트가 python3-venv 자동 설치 시도 (Debian/Ubuntu)

set -e
# 이 스크립트가 있는 device_config의 상위 = 프로젝트 루트로 이동
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== 프로젝트 루트: $PROJECT_ROOT ==="

# Debian/Ubuntu(라즈베리파이 OS): venv 생성에 python3-venv 필요 → 없으면 설치
if ! python3 -c "import ensurepip" 2>/dev/null; then
  PYVER=$(python3 -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "python3")
  PKG="${PYVER}-venv"
  echo "=== 가상환경에 필요한 패키지 설치: $PKG ==="
  echo "sudo 권한이 필요할 수 있습니다."
  sudo apt update && sudo apt install -y "$PKG" || {
    echo ""
    echo "자동 설치 실패. 아래를 직접 실행한 뒤 다시 이 스크립트를 실행하세요:"
    echo "  sudo apt update && sudo apt install -y $PKG"
    exit 1
  }
fi

echo "=== Python 가상환경 생성 (venv) ==="
python3 -m venv venv

echo "=== 가상환경에 패키지 설치 ==="
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo ""
echo "=== 완료 ==="
echo "이제 프로젝트 루트에서 PM2로 앱을 실행하세요:"
echo "  pm2 start device_config/startup.json"
echo ""
echo "직접 실행해보려면 (프로젝트 루트에서):"
echo "  ./venv/bin/python app.py"
echo "  ./venv/bin/python mqtt.py"
echo "  ..."
