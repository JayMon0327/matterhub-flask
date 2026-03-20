# 벤더 교체 가이드

MatterHub는 벤더 중립 아키텍처를 사용합니다.
MQTT 벤더를 교체할 때 `mqtt_pkg/` 코드를 수정할 필요 없이
`providers/` 디렉토리와 `.env` 설정만 변경하면 됩니다.

## 새 벤더 추가 절차

### 1. 프로바이더 설정 파일 생성

```python
# providers/newvendor/__init__.py
# (빈 파일)

# providers/newvendor/settings.py
from providers.base import MQTTProviderSettings

ENDPOINT = "your-iot-endpoint.amazonaws.com"
CLIENT_ID = "your-client-id"
CERT_DIR = "newvendor_certificates/"
TOPIC_SUBSCRIBE = "your/subscribe/topic"
TOPIC_PUBLISH = "your/publish/topic"


class NewVendorSettings(MQTTProviderSettings):
    def get_endpoint(self) -> str:
        return ENDPOINT

    def get_client_id(self) -> str:
        return CLIENT_ID

    def get_cert_dir(self) -> str:
        return CERT_DIR

    def get_topic_subscribe(self) -> str:
        return TOPIC_SUBSCRIBE

    def get_topic_publish(self) -> str:
        return TOPIC_PUBLISH

    def get_default_report_entity_ids(self) -> list[str]:
        return ["sensor.your_sensor_1", "sensor.your_sensor_2"]
```

### 2. 팩토리에 분기 추가

`providers/__init__.py`:
```python
def load_provider(vendor=None):
    vendor = vendor or os.environ.get("MATTERHUB_VENDOR", "konai")
    if vendor == "konai":
        from providers.konai.settings import KonaiSettings
        return KonaiSettings()
    if vendor == "newvendor":
        from providers.newvendor.settings import NewVendorSettings
        return NewVendorSettings()
    raise ValueError(f"Unknown vendor: {vendor}")
```

### 3. .env 설정 변경

```bash
MATTERHUB_VENDOR="newvendor"
# 필요 시 연결 정보 오버라이드
MQTT_ENDPOINT="your-iot-endpoint.amazonaws.com"
MQTT_CLIENT_ID="your-client-id"
MQTT_CERT_PATH="newvendor_certificates/"
```

### 4. 인증서 배치

새 벤더의 인증서를 지정된 디렉토리에 배치합니다:
- `cert.pem` — 디바이스 인증서
- `key.pem` — 프라이빗 키
- `ca_cert.pem` — CA 인증서 (선택)

## 변경 범위

```
변경 O:
  providers/newvendor/settings.py  (신규 1개)
  providers/newvendor/__init__.py  (신규 빈 파일)
  providers/__init__.py            (elif 1줄 추가)
  .env                             (MATTERHUB_VENDOR + 연결정보)

변경 X:
  mqtt_pkg/* 전체
  mqtt.py, app.py, support_tunnel.py, update_agent.py
  모든 테스트 (벤더 특화 테스트 제외)
```

## 환경변수 우선순위

모든 MQTT 설정은 다음 우선순위로 결정됩니다:

1. 벤더 중립 환경변수 (`MQTT_ENDPOINT`, `MQTT_TOPIC_SUBSCRIBE` 등)
2. 레거시 환경변수 (`KONAI_ENDPOINT`, `KONAI_TOPIC_REQUEST` 등)
3. 프로바이더 기본값 (`providers/<vendor>/settings.py`)
