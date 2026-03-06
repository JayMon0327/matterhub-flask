# MQTT Topic Diagnosis Log (2026-03-04)

## 1. 목적

Konai MQTT split-topic(`delta` 구독, `reported` 발행) 구조에서 실제 라즈베리파이 장비 기준으로 어떤 토픽이 정상 동작하는지 기록한다.

## 2. 검증 환경

- 장비: Raspberry Pi / Ubuntu 24.04 LTS
- 프로젝트 경로: `/home/whatsmatter/Desktop/matterhub`
- 서비스: `matterhub-mqtt.service`
- 브랜치: `konai/20260211-v1.1`

## 3. 핵심 결론

- `update/reported/dev/.../matter/k3O6TL`
  - 최소 재현 probe 기준 `CONNECT OK`, `SUBSCRIBE OK`
- `update/delta/dev/.../matter/k3O6TL`
  - 최소 재현 probe 기준 `CONNECT OK` 이후 `AWS_ERROR_MQTT_UNEXPECTED_HANGUP`
  - `SUBSCRIBE` 완료되지 않음

즉, 현재 코드 연동 문제보다 broker/policy/topic permission 조건이 더 유력하다.

재검증(2026-03-05) 결과도 동일:

- `venv/bin/python device_config/mqtt_probe.py --topic-mode both --listen-seconds 0`
  - request(delta): failed
  - response(reported): success

## 4. 확인한 비원인

- `mqtt.py` 와 `mqtt_pkg` 연동 불량 아님
- 레거시 `wm-mqtt` 잔존 프로세스 아님
- `.env` 누락 아님
- `certificates/` 와 `konai_certificates/` 공존 자체가 직접 원인 아님

## 5. 실제 검증 명령

### 5.1 서비스 로그 확인

```bash
journalctl -u matterhub-mqtt.service -n 120 --no-pager
journalctl -u matterhub-mqtt.service -f
```

### 5.2 서비스 중지 후 토픽 분리 검증

```bash
sudo systemctl stop matterhub-mqtt.service
cd /home/whatsmatter/Desktop/matterhub
venv/bin/python - <<'PY'
import os
from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

def test_topic(topic, client_id):
    print("\\n===", topic, "===")
    cert_path='konai_certificates'
    cert_file=os.path.join(cert_path,'cert.pem')
    key_file=os.path.join(cert_path,'key.pem')
    ca_file=os.path.join(cert_path,'ca_cert.pem')
    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)
    kw=dict(
        endpoint='a34vuzhubahjfj-ats.iot.ap-northeast-2.amazonaws.com',
        cert_filepath=cert_file,
        pri_key_filepath=key_file,
        client_bootstrap=client_bootstrap,
        client_id=client_id,
        keep_alive_secs=300,
    )
    if os.path.exists(ca_file):
        kw['ca_filepath']=ca_file
    conn = mqtt_connection_builder.mtls_from_path(**kw)
    try:
        conn.connect().result(timeout=10)
        print('CONNECT OK')
        future, _ = conn.subscribe(topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE, callback=lambda *a, **k: None)
        future.result(timeout=10)
        print('SUBSCRIBE OK')
    except Exception as exc:
        print('ERROR', repr(exc), type(exc).__name__)
    finally:
        try:
            conn.disconnect().result(timeout=5)
        except Exception as exc:
            print('DISCONNECT', repr(exc), type(exc).__name__)

base='dev/c3c6d27d5f2f353991afac4e3af69029303795a2/matter/k3O6TL'
test_topic(f'update/reported/{base}', 'c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL')
test_topic(f'update/delta/{base}', 'c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL')
PY
sudo systemctl start matterhub-mqtt.service
```

## 6. 최근 코드 개선 사항

- `mqtt.py`
  - 시작 시 endpoint / client_id / request_topic / response_topic / cert 상태 출력
  - 초기 구독 결과를 토픽별 success/failed 로 요약 출력
- `mqtt_pkg/runtime.py`
  - 재구독 결과를 토픽별 success/failed 로 요약 출력
- `mqtt_pkg/publisher.py`
  - 실제 발행 토픽과 payload type 로그 출력
- `device_config/mqtt_probe.py`
  - repo 어디서 실행해도 `mqtt_pkg` import 가능
  - `--topic-mode both` 로 request/response 토픽 연속 검증 가능
  - `result label=<request|response> status=<success|failed>` 형식으로 검증 결과 고정 출력

## 7. Konai/AWS 측 확인 요청 포인트

- 현재 인증서가 `update/delta/dev/.../matter/k3O6TL` subscribe 권한을 갖는지
- policy 상 `client_id = c3c6d27d5f2f353991afac4e3af69029303795a2-matter-k3O6TL` 로 제한되어 있는지
- `reported` 는 subscribe 가능하지만 `delta` 는 거부되도록 설정된 상태인지
- 연결 직후 broker가 세션을 끊는 이유가 topic authorization failure 인지

## 8. 다음 권장 순서

1. Konai/AWS 측에서 `delta` subscribe 권한 확인
2. 수정 후 라즈베리파이에서 `device_config/mqtt_probe.py --topic-mode both` 재실행
3. `matterhub-mqtt.service` 재기동 후 `journalctl` 로 startup / subscribe / publish 로그 재확인

## 9. 참고 커밋

- `25af1cf` Improve MQTT diagnostics and add probe
- `307f831` Improve MQTT topic verification logging
- `92cd187` Fix MQTT probe callback signature
