from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

from . import publisher, settings, update


def handle_konai_states_request(
    payload_bytes: Optional[bytes] = None, response_topic: Optional[str] = None
) -> None:
    """Process Konai request: correlation_id is mandatory."""
    try:
        correlation_id: Optional[str] = None
        entity_id: Optional[str] = None
        if payload_bytes:
            try:
                message = json.loads(payload_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                publisher.publish_error(
                    None,
                    "INVALID_JSON",
                    "Request payload is not valid JSON",
                    response_topic=response_topic,
                )
                return
            if not isinstance(message, dict):
                publisher.publish_error(
                    None,
                    "INVALID_JSON",
                    "Request payload must be a JSON object",
                    response_topic=response_topic,
                )
                return

            correlation_id = _extract_correlation_id(message)
            if not correlation_id:
                publisher.publish_error(
                    None,
                    "MISSING_CORRELATION_ID",
                    "correlation_id is required",
                    response_topic=response_topic,
                )
                return

            entity = message.get("entity_id")
            if entity is not None and str(entity).strip():
                entity_id = str(entity).strip()

        headers: Dict[str, str] = {}
        if settings.HASS_TOKEN:
            headers["Authorization"] = f"Bearer {settings.HASS_TOKEN}"

        timestamp = publisher.konai_timestamp()

        if entity_id:
            url = f"{settings.LOCAL_API_BASE}/local/api/states/{entity_id}"
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    payload = {
                        "type": "query_response_single",
                        "correlation_id": correlation_id,
                        "ts": timestamp,
                        "data": data,
                    }
                    if settings.MATTERHUB_ID:
                        payload["hub_id"] = settings.MATTERHUB_ID
                    publisher.publish(payload, response_topic=response_topic)
                    print(f"코나이 단일 조회 응답: {entity_id}")
                    return

                publisher.publish_error(
                    correlation_id,
                    "INVALID_ENTITY_ID",
                    f"Entity '{entity_id}' not found (HTTP {response.status_code})",
                    response_topic=response_topic,
                )
            except requests.Timeout:
                publisher.publish_error(
                    correlation_id,
                    "TIMEOUT",
                    "Local API request timed out",
                    response_topic=response_topic,
                )
            except Exception as exc:
                publisher.publish_error(
                    correlation_id,
                    "LOCAL_API_ERROR",
                    str(exc),
                    detail={"exception": type(exc).__name__},
                    response_topic=response_topic,
                )
            return

        try:
            response = requests.get(
                f"{settings.LOCAL_API_BASE}/local/api/states",
                headers=headers,
                timeout=10,
            )
            if response.status_code != 200:
                publisher.publish_error(
                    correlation_id,
                    "LOCAL_API_ERROR",
                    f"Failed to fetch all states (HTTP {response.status_code})",
                    response_topic=response_topic,
                )
                return

            payload = {
                "type": "query_response_all",
                "correlation_id": correlation_id,
                "ts": timestamp,
                "data": response.json(),
            }
            if settings.MATTERHUB_ID:
                payload["hub_id"] = settings.MATTERHUB_ID
            publisher.publish(payload, response_topic=response_topic)
            print("코나이 전체 조회 응답 발행")

        except requests.Timeout:
            publisher.publish_error(
                correlation_id,
                "TIMEOUT",
                "Local API request timed out",
                response_topic=response_topic,
            )
        except Exception as exc:
            publisher.publish_error(
                correlation_id,
                "LOCAL_API_ERROR",
                str(exc),
                detail={"exception": type(exc).__name__},
                response_topic=response_topic,
            )

    except Exception as exc:
        print(f"❌ 코나이 요청 처리 실패: {exc}")
        try:
            publisher.publish_error(None, "LOCAL_API_ERROR", str(exc), response_topic=response_topic)
        except Exception:
            pass


def mqtt_callback(topic: str, payload: bytes, **kwargs: Any) -> None:
    payload_bytes = payload if isinstance(payload, (bytes, bytearray)) else bytes(str(payload), "utf-8")
    try:
        parsed = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        parsed = None

    if topic == settings.KONAI_TOPIC_REQUEST:
        if isinstance(parsed, dict) and parsed.get("type") in {
            "query_response_all",
            "query_response_single",
            "error",
            "entity_changed",
            "bootstrap_all_states",
        }:
            return
        print(f"코나이 요청 수신: {topic}")
        handle_konai_states_request(payload_bytes, response_topic=settings.KONAI_TOPIC_RESPONSE)
        return

    if topic == settings.KONAI_TOPIC_RESPONSE and settings.KONAI_TOPIC_RESPONSE != settings.KONAI_TOPIC_REQUEST:
        return

    if settings.KONAI_TEST_TOPIC_REQUEST and topic == settings.KONAI_TEST_TOPIC_REQUEST:
        test_response_topic = (
            settings.KONAI_TEST_TOPIC_RESPONSE or settings.KONAI_TEST_TOPIC_REQUEST
        )
        print(
            f"코나이 테스트 요청: {topic} -> {test_response_topic}, "
            f"matterhub_id={settings.MATTERHUB_ID or '(미설정)'}"
        )
        handle_konai_states_request(payload_bytes, response_topic=test_response_topic)
        return

    if (
        settings.KONAI_TEST_TOPIC_RESPONSE
        and topic == settings.KONAI_TEST_TOPIC_RESPONSE
        and settings.KONAI_TEST_TOPIC_RESPONSE != settings.KONAI_TEST_TOPIC_REQUEST
    ):
        return

    matterhub_id = settings.MATTERHUB_ID
    update_topics = []
    if matterhub_id:
        update_topics.append(f"matterhub/{matterhub_id}/git/update")
        update_topics.append(f"matterhub/update/specific/{matterhub_id}")

    if topic in update_topics or (matterhub_id and topic.startswith("matterhub/update/specific/")):
        if isinstance(parsed, dict):
            print(f"🚀 Git 업데이트 명령 수신: {topic}")
            update.handle_update_command(parsed)
        else:
            print(f"❌ 업데이트 명령 파싱 실패: {payload_bytes!r}")
        return

    print(f"알 수 없는 토픽 수신: {topic}")


def _extract_correlation_id(message: Dict[str, Any]) -> Optional[str]:
    correlation_id = message.get("correlation_id")
    if correlation_id is not None and str(correlation_id).strip():
        return str(correlation_id).strip()
    request_id = message.get("request_id")
    if request_id is not None and str(request_id).strip():
        return str(request_id).strip()
    return None
