from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from awscrt import mqtt

from . import runtime, settings


def utc_timestamp() -> str:
    """Return an ISO8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def publish(payload: Dict[str, Any], response_topic: Optional[str] = None) -> None:
    connection = runtime.get_connection()
    if connection is None:
        print("[MQTT] publish 실패: MQTT 연결이 설정되지 않았습니다.")
        return

    target_topic = response_topic or settings.MQTT_TOPIC_PUBLISH
    if not target_topic:
        print("[MQTT] publish 실패: 대상 토픽을 확인할 수 없습니다.")
        return

    payload_type = payload.get("type", "(미설정)")
    payload_bytes = json.dumps(payload, ensure_ascii=False)

    # QoS 1 시도 → PUBACK 타임아웃 시 QoS 0 폴백
    for qos_level in (mqtt.QoS.AT_LEAST_ONCE, mqtt.QoS.AT_MOST_ONCE):
        try:
            publish_result = connection.publish(
                topic=target_topic,
                payload=payload_bytes,
                qos=qos_level,
            )
            publish_future = publish_result[0] if isinstance(publish_result, tuple) else publish_result
            if hasattr(publish_future, "result"):
                try:
                    publish_future.result(timeout=settings.MQTT_PUBLISH_TIMEOUT_SEC)
                except TypeError:
                    publish_future.result()
            qos_label = "qos1" if qos_level == mqtt.QoS.AT_LEAST_ONCE else "qos0_fallback"
            print(f"[MQTT] publish_result topic={target_topic} status=success type={payload_type} {qos_label}")
            return
        except Exception as exc:
            if qos_level == mqtt.QoS.AT_LEAST_ONCE:
                print(f"[MQTT] publish QoS1 실패, QoS0 폴백 시도: {type(exc).__name__}")
                continue
            print(
                f"[MQTT] publish_result topic={target_topic} "
                f"status=failed type={payload_type} error={type(exc).__name__}"
            )


def publish_error(
    correlation_id: Optional[str],
    code: str,
    message: str,
    detail: Optional[Dict[str, Any]] = None,
    response_topic: Optional[str] = None,
) -> None:
    body: Dict[str, Any] = {
        "type": "error",
        "correlation_id": correlation_id,
        "ts": utc_timestamp(),
        "error": {"code": code, "message": message},
    }
    if detail is not None:
        body["error"]["detail"] = detail

    publish(body, response_topic=response_topic)
    print(f"[MQTT][ERROR] 오류 응답: {code} - {message}")
