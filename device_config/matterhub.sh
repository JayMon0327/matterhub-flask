#!/bin/bash

# 프로젝트 루트로 이동 (이 스크립트는 device_config/에 있음)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

python3 sub/ruleEngine.py &
python3 sub/notifier.py &
python3 sub/localIp.py &
python3 app.py &
python3 mqtt.py &

wait
