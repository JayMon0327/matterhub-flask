#!/usr/bin/env python3
"""
matterhub_id 발급 전용 스크립트 (Claim 프로비저닝)

Claim 인증서로 AWS IoT Core에 사물을 등록하고,
발급된 thingName을 matterhub_id로 .env에 저장합니다.

사용법 (venv Python으로 실행):
  cd matterhub-flask
  venv/bin/python3 run_provision.py
  venv/bin/python3 run_provision.py --ensure --non-interactive

필요 조건:
  - certificates/ 디렉토리에 Claim 인증서 존재
    - whatsmatter_nipa_claim_cert.cert.pem
    - whatsmatter_nipa_claim_cert.private.key
  - AWS IoT 프로비저닝 템플릿 설정 (whatsmatter-nipa-template)

발급 완료 후:
  - .env에 matterhub_id="발급된값" 자동 저장
  - matterhub-mqtt.service 재시작 필요
"""
import argparse
import os
import sys

# 프로젝트 루트에서 실행되도록
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
os.chdir(_script_dir)

# dotenv 등 의존성: venv 사용 필수
try:
    from dotenv import load_dotenv
except ImportError:
    print("")
    print("❌ dotenv 모듈을 찾을 수 없습니다.")
    print("   가상환경 Python으로 실행하세요:")
    print("")
    print("   venv/bin/python3 run_provision.py")
    print("")
    sys.exit(1)

load_dotenv(dotenv_path='.env')

# 리팩터링 후 프로비저닝 클라이언트는 mqtt_pkg 아래로 이동했다.
from mqtt_pkg.provisioning import AWSProvisioningClient


def _normalize(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


def _is_truthy(value: str | None) -> bool:
    return _normalize(value).lower() in {"1", "true", "yes", "y", "on"}


def _is_falsey(value: str | None) -> bool:
    return _normalize(value).lower() in {"0", "false", "no", "n", "off"}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MatterHub claim provisioning helper")
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="Provision only when matterhub_id and device certificate are both missing.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt for confirmation. Intended for install/post-install hooks.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = _parse_args(argv)

    print("")
    print("═══════════════════════════════════════════════════════════════")
    print("  matterhub_id Claim 프로비저닝 실행")
    print("═══════════════════════════════════════════════════════════════")
    print("")

    if args.ensure and _is_falsey(os.environ.get("MATTERHUB_AUTO_PROVISION")):
        print("MATTERHUB_AUTO_PROVISION=0 이므로 자동 프로비저닝을 건너뜁니다.")
        print("")
        return 0

    current_matterhub_id = _normalize(os.environ.get("matterhub_id"))
    if current_matterhub_id:
        print(f"✅ matterhub_id 이미 설정됨: {current_matterhub_id}")
        print("")
        return 0

    client = AWSProvisioningClient()
    has_cert, cert_file, key_file = client.check_certificate()

    if has_cert:
        print(f"device 인증서 존재: {cert_file}")
        print("   (이미 발급된 경우 .env의 matterhub_id를 확인하세요)")
        if args.ensure or args.non_interactive:
            print("❌ matterhub_id 없이 device 인증서만 존재합니다.")
            print("   자동 재프로비저닝은 중복 사물 생성 위험이 있어 중단합니다.")
            print("   수동 확인 후 필요 시 재프로비저닝하세요.")
            print("")
            return 1
        print("")
        ans = input("   프로비저닝을 다시 실행할까요? (기존 thingName 유지) [y/N]: ").strip().lower()
        if ans != "y":
            print("   취소됨.")
            return 0
        print("")

    success = client.provision_device()
    if success:
        print("")
        print("✅ 프로비저닝 완료. .env에 matterhub_id가 저장되었습니다.")
        print("   다음 단계: matterhub-mqtt.service 재시작 후 테스트하세요.")
        print("")
        return 0
    else:
        print("")
        print("❌ 프로비저닝 실패. 위 로그를 확인하세요.")
        print("")
        return 1


if __name__ == "__main__":
    sys.exit(main())
