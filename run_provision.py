#!/usr/bin/env python3
"""
matterhub_id 발급 전용 스크립트 (Claim 프로비저닝)

Claim 인증서로 AWS IoT Core에 사물을 등록하고,
발급된 thingName을 matterhub_id로 .env에 저장합니다.

사용법:
  cd matterhub-flask
  python run_provision.py

필요 조건:
  - certificates/ 디렉토리에 Claim 인증서 존재
    - whatsmatter_nipa_claim_cert.cert.pem
    - whatsmatter_nipa_claim_cert.private.key
  - AWS IoT 프로비저닝 템플릿 설정 (whatsmatter-nipa-template)

발급 완료 후:
  - .env에 matterhub_id="발급된값" 자동 저장
  - mqtt.py 또는 PM2 재시작 필요
"""
import os
import sys

# 프로젝트 루트에서 실행되도록
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# mqtt 모듈에서 AWSProvisioningClient import
from mqtt import AWSProvisioningClient


def main():
    print("")
    print("═══════════════════════════════════════════════════════════════")
    print("  matterhub_id Claim 프로비저닝 실행")
    print("═══════════════════════════════════════════════════════════════")
    print("")

    client = AWSProvisioningClient()
    has_cert, cert_file, key_file = client.check_certificate()

    if has_cert:
        print(f"ℹ️  device 인증서 존재: {cert_file}")
        print("   (이미 발급된 경우 .env의 matterhub_id를 확인하세요)")
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
        print("   다음 단계: mqtt.py(또는 PM2) 재시작 후 테스트하세요.")
        print("")
        return 0
    else:
        print("")
        print("❌ 프로비저닝 실패. 위 로그를 확인하세요.")
        print("")
        return 1


if __name__ == "__main__":
    sys.exit(main())
