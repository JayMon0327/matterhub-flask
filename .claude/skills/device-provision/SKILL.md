---
name: device-provision
description: MatterHub AWS IoT 프로비저닝. Claim 인증서로 Thing 등록 후 matterhub_id를 발급받고 .env에 저장한다. "/device-provision" 또는 "프로비저닝", "Thing 등록" 시 사용.
---

# MatterHub AWS IoT 프로비저닝

Claim 인증서를 사용하여 AWS IoT Core에 Thing을 등록하고 matterhub_id를 발급받는 스킬.

## 사전 조건

- `/device-setup`이 완료된 상태
- `certificates/` 디렉토리에 Claim 인증서 3개 존재:
  - `AmazonRootCA1.pem`
  - `whatsmatter_nipa_claim_cert.cert.pem`
  - `whatsmatter_nipa_claim_cert.private.key`

사용자에게 다음 정보를 확인한다:

| 항목 | 예시 | 필수 |
|------|------|------|
| 디바이스 IP | 192.168.219.191 | Y |
| SSH User | matterhub | Y |
| SSH Password | whatsmatter1234 | Y |

## 프로비저닝 절차

### Step 1: 기존 프로비저닝 확인

이미 발급된 인증서가 있는지 확인한다:

```bash
ls ~/Desktop/matterhub/certificates/device.pem.crt 2>/dev/null && echo "ALREADY_PROVISIONED" || echo "NEED_PROVISION"
```

이미 있고 `.env`에 `matterhub_id`도 설정되어 있으면 사용자에게 "이미 프로비저닝됨"을 알린다.

### Step 2: 프로비저닝 실행

```bash
cd ~/Desktop/matterhub && python3 -u -c "
from mqtt_pkg.provisioning import AWSProvisioningClient
client = AWSProvisioningClient()
has_cert, cert_file, key_file = client.check_certificate()
print(f'기존 인증서: {has_cert}, cert={cert_file}, key={key_file}')
if not has_cert:
    print('프로비저닝 시작...')
    result = client.provision_device()
    print(f'결과: {result}')
else:
    print('이미 프로비저닝된 인증서 존재')
"
```

### 예상 출력 (성공 시)

```
기존 인증서: False, cert=None, key=None
프로비저닝 시작...
[PROVISION] Claim 인증서로 MQTT 연결 시도 중...
[PROVISION] MQTT 연결 성공
[PROVISION] 새 인증서 발급 요청 중...
[PROVISION] 새 인증서 저장: certificates/device.pem.crt, certificates/private.pem.key
[PROVISION] 템플릿: whatsmatter-nipa-template
[PROVISION] 사물 등록 요청 중...
✅ [PROVISION] matterhub_id 발급 완료: whatsmatter-nipa_SN-XXXXXXXXXX (.env 저장됨, mqtt.py 재시작 필요)
[PROVISION] 프로비저닝 플로우 완료
결과: True
```

### Step 3: 인증서 심링크 생성

프로비저닝으로 `device.pem.crt`, `private.pem.key`가 생성되었으므로 심링크를 만든다:

```bash
cd ~/Desktop/matterhub/certificates/
ln -sf device.pem.crt cert.pem
ln -sf private.pem.key key.pem
ln -sf AmazonRootCA1.pem ca_cert.pem
```

### Step 4: .env 업데이트

프로비저닝이 자동으로 `matterhub_id`를 `.env`에 저장하지만, `MQTT_CLIENT_ID`도 동일하게 설정해야 한다:

```bash
# .env에서 발급된 matterhub_id 확인
grep matterhub_id ~/Desktop/matterhub/.env
```

출력된 `matterhub_id` 값을 `MQTT_CLIENT_ID`에도 설정:

```bash
# 예: matterhub_id="whatsmatter-nipa_SN-1774090901" 인 경우
# MQTT_CLIENT_ID도 동일하게 설정
```

`.env`에 `MQTT_CLIENT_ID`가 빈 값이면 발급된 matterhub_id로 채운다.

### Step 5: 검증

```bash
cat ~/Desktop/matterhub/.env | grep -E 'matterhub_id|MQTT_CLIENT_ID|MQTT_CERT_PATH|MQTT_ENDPOINT'
ls -la ~/Desktop/matterhub/certificates/{cert,key,ca_cert}.pem
```

확인 사항:
- `matterhub_id`가 `whatsmatter-nipa_SN-XXXXXXXXXX` 형태로 설정됨
- `MQTT_CLIENT_ID`가 matterhub_id와 동일
- `MQTT_CERT_PATH`가 `certificates/`
- `MQTT_ENDPOINT`가 `a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com`
- 심링크 3개가 정상 존재

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `[PROVISION] 인증서 발급 실패: 응답 없음` | Claim 인증서 만료 또는 IoT 정책 문제 | AWS 콘솔에서 Claim 인증서 상태 확인 |
| `[PROVISION] 사물 등록 거부됨` | 템플릿 이름 불일치 | `AWS_PROVISION_TEMPLATE_NAME` 환경변수 확인 |
| `MQTT UNEXPECTED_HANGUP` 반복 | konai_certificates 사용 중 | `MQTT_CERT_PATH=certificates/`로 변경, 심링크 확인 |
| `MQTT_CLIENT_ID`와 Thing 불일치 | 프로비저닝된 ID와 다른 client_id 사용 | `MQTT_CLIENT_ID`를 matterhub_id와 동일하게 설정 |

## 완료 후 안내

프로비저닝 완료 후 `/device-verify`로 서비스 실행 및 검증을 진행한다.
