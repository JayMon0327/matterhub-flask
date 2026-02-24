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
    if connection is None:
        print("❌ Konai publish 실패: MQTT 연결이 설정되지 않았습니다.")
        return

    target_topic = response_topic or settings.KONAI_TOPIC_RESPONSE
    if not target_topic:
        print("❌ Konai publish 실패: 대상 토픽을 확인할 수 없습니다.")
        return

    connection.publish(
        topic=target_topic,
        payload=json.dumps(payload, ensure_ascii=False),
        qos=mqtt.QoS.AT_MOST_ONCE,
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
        "ts": konai_timestamp(),
        "error": {"code": code, "message": message},
    }
    if detail is not None:
        body["error"]["detail"] = detail

    publish(body, response_topic=response_topic)
    print(f"❌ 코나이 오류 응답: {code} - {message}")

