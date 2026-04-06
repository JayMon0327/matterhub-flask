from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from awscrt import mqtt

from . import runtime, settings


def konai_timestamp() -> str:
    """Return an ISO8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def publish(payload: Dict[str, Any], response_topic: Optional[str] = None) -> None:
    connection = runtime.get_connection()
    target_topic = response_topic or settings.KONAI_TOPIC_RESPONSE
    payload_type = payload.get("type", "(미설정)")

    if connection is None or not runtime.is_connected():
        reason = "no_connection" if connection is None else "disconnected"
        print(
            f"[MQTT][PUBLISH][SKIP] topic={target_topic or '(없음)'} "
            f"type={payload_type} reason={reason}"
        )
        return

    if not target_topic:
        print(f"[MQTT][PUBLISH][SKIP] type={payload_type} reason=no_topic")
        return
    try:
        publish_result = connection.publish(
            topic=target_topic,
            payload=json.dumps(payload, ensure_ascii=False),
            qos=mqtt.QoS.AT_MOST_ONCE,
        )
        publish_future = publish_result[0] if isinstance(publish_result, tuple) else publish_result
        if hasattr(publish_future, "result"):
            try:
                publish_future.result(timeout=5)
            except TypeError:
                publish_future.result()
        print(f"[MQTT] publish_result topic={target_topic} status=success type={payload_type}")
    except Exception as exc:
        print(
            f"[MQTT] publish_result topic={target_topic} "
            f"status=failed type={payload_type} error={type(exc).__name__}"
        )
        return


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
        "ts": konai_timestamp(),
        "error": {"code": code, "message": message},
    }
    if detail is not None:
        body["error"]["detail"] = detail

    publish(body, response_topic=response_topic)
    print(f"❌ 코나이 오류 응답: {code} - {message}")
