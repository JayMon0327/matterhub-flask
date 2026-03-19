from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

from . import settings


class AWSProvisioningClient:
    """
    Legacy whatsmatter Claim provisioning flow.
    Used to issue a new matterhub_id (thingName) and persist to .env.
    """

    def __init__(self) -> None:
        self.cert_path = os.environ.get("AWS_CLAIM_CERT_PATH", "certificates/")
        self.claim_cert = os.environ.get("AWS_CLAIM_CERT_FILE", "whatsmatter_nipa_claim_cert.cert.pem")
        self.claim_key = os.environ.get("AWS_CLAIM_KEY_FILE", "whatsmatter_nipa_claim_cert.private.key")
        self.endpoint = os.environ.get(
            "AWS_PROVISION_ENDPOINT",
            "a206qwcndl23az-ats.iot.ap-northeast-2.amazonaws.com",
        )
        self.client_id = os.environ.get("AWS_PROVISION_CLIENT_ID", "whatsmatter-nipa-claim-thing")

    def check_certificate(self) -> Tuple[bool, Optional[str], Optional[str]]:
        cert_file = os.path.join(self.cert_path, "device.pem.crt")
        key_file = os.path.join(self.cert_path, "private.pem.key")
        if os.path.exists(cert_file) and os.path.exists(key_file):
            return True, cert_file, key_file
        return False, None, None

    def provision_device(self) -> bool:
        try:
            event_loop_group = io.EventLoopGroup(1)
            host_resolver = io.DefaultHostResolver(event_loop_group)
            client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

            mqtt_connection = mqtt_connection_builder.mtls_from_path(
                endpoint=self.endpoint,
                cert_filepath=os.path.join(self.cert_path, self.claim_cert),
                pri_key_filepath=os.path.join(self.cert_path, self.claim_key),
                client_bootstrap=client_bootstrap,
                client_id=self.client_id,
                keep_alive_secs=300,
            )

            print("[PROVISION] Claim 인증서로 MQTT 연결 시도 중...")
            connect_future = mqtt_connection.connect()
            connect_future.result(timeout=10)
            print("[PROVISION] MQTT 연결 성공")

            new_cert_data = self._issue_device_certificate(mqtt_connection)
            if not new_cert_data:
                return False

            cert_id = new_cert_data["certificateId"]
            ownership_token = new_cert_data["certificateOwnershipToken"]

            success = self.register_thing(mqtt_connection, cert_id, ownership_token)
            if not success:
                print("[PROVISION] 사물 등록 실패")
                return False

            print("[PROVISION] 프로비저닝 플로우 완료")
            return True

        except Exception as exc:
            print(f"[PROVISION] 프로비저닝 실패: {exc}")
            return False

    def register_thing(
        self,
        mqtt_connection: mqtt.Connection,
        certificate_id: str,
        cert_ownership_token: str,
    ) -> bool:
        try:
            template_name = os.environ.get("AWS_PROVISION_TEMPLATE_NAME", "whatsmatter-nipa-template")
            template_topic = f"$aws/provisioning-templates/{template_name}/provision/json"
            accepted_topic = f"$aws/provisioning-templates/{template_name}/provision/json/accepted"
            rejected_topic = f"$aws/provisioning-templates/{template_name}/provision/json/rejected"

            received_response = False
            registration_data: Optional[dict] = None
            reject_reason: Optional[dict] = None

            def on_accepted(topic, payload, **kwargs):
                nonlocal received_response, registration_data
                registration_data = json.loads(payload.decode())
                received_response = True

            def on_rejected(topic, payload, **kwargs):
                nonlocal received_response, reject_reason
                try:
                    reject_reason = json.loads(payload.decode())
                except Exception:
                    reject_reason = {"raw": payload.decode(errors="ignore")}
                received_response = True

            for sub_topic, callback in [(accepted_topic, on_accepted), (rejected_topic, on_rejected)]:
                sub_future, _ = mqtt_connection.subscribe(
                    topic=sub_topic,
                    qos=mqtt.QoS.AT_LEAST_ONCE,
                    callback=callback,
                )
                sub_future.result(timeout=10)

            print(f"[PROVISION] 템플릿: {template_name}")

            payload = {
                "Parameters": {"SerialNumber": f"SN-{int(time.time())}"},
                "certificateOwnershipToken": cert_ownership_token,
                "certificateId": certificate_id,
            }
            print("[PROVISION] 사물 등록 요청 중...")
            publish_future, _ = mqtt_connection.publish(
                topic=template_topic,
                payload=json.dumps(payload),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )
            publish_future.result(timeout=10)

            timeout = time.time() + 15
            while not received_response and time.time() < timeout:
                time.sleep(0.1)

            if reject_reason:
                print(f"[PROVISION] 사물 등록 거부됨: {reject_reason}")
                return False

            if not registration_data:
                print("[PROVISION] 사물 등록 실패: 응답 없음 (템플릿명·endpoint·Claim 정책 확인)")
                print(f"   - 템플릿: {template_name}, endpoint: {self.endpoint}")
                return False

            thing_name = registration_data.get("thingName")
            if not thing_name:
                print(f"[PROVISION] 사물 등록 실패: thingName 없음, 응답={registration_data}")
                return False

            settings.update_matterhub_id(thing_name)
            print(
                f"✅ [PROVISION] matterhub_id 발급 완료: {thing_name} "
                f"(.env 저장됨, mqtt.py 재시작 필요)"
            )
            return True

        except Exception as exc:
            print(f"[PROVISION] 사물 등록 실패: {exc}")
            return False

    def _issue_device_certificate(self, mqtt_connection: mqtt.Connection) -> Optional[dict]:
        provision_topic = "$aws/certificates/create/json"
        response_topic = "$aws/certificates/create/json/accepted"

        received_response = False
        new_cert_data: Optional[dict] = None

        def on_message_received(topic, payload, **kwargs):
            nonlocal received_response, new_cert_data
            new_cert_data = json.loads(payload.decode())
            received_response = True

        subscribe_future, _ = mqtt_connection.subscribe(
            topic=response_topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_message_received,
        )
        subscribe_future.result(timeout=10)

        print("[PROVISION] 새 인증서 발급 요청 중...")
        publish_future, _ = mqtt_connection.publish(
            topic=provision_topic,
            payload=json.dumps({}),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        publish_future.result(timeout=10)

        timeout = time.time() + 15
        while not received_response and time.time() < timeout:
            time.sleep(0.1)

        if not new_cert_data:
            print("[PROVISION] 인증서 발급 실패: 응답 없음")
            return None

        certificate_pem = new_cert_data.get("certificatePem")
        cert_id = new_cert_data.get("certificateId")
        ownership_token = new_cert_data.get("certificateOwnershipToken")

        if not (certificate_pem and cert_id and ownership_token):
            print(f"[PROVISION] 인증서 발급 실패: 응답 필드 부족: {new_cert_data}")
            return None

        cert_file = os.path.join(self.cert_path, "device.pem.crt")
        key_file = os.path.join(self.cert_path, "private.pem.key")
        with open(cert_file, "w", encoding="utf-8") as cert_fp:
            cert_fp.write(certificate_pem)
        private_key = new_cert_data.get("privateKey")
        if private_key:
            with open(key_file, "w", encoding="utf-8") as key_fp:
                key_fp.write(private_key)
            print(f"[PROVISION] 새 인증서 저장: {cert_file}, {key_file}")
        else:
            print(f"[PROVISION] 경고: privateKey 없음, claim key 재사용. {cert_file} 만 저장")

        return new_cert_data

