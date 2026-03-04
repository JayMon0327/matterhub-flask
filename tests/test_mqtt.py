from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MQTT_ENTRYPOINT = PROJECT_ROOT / "mqtt.py"


def load_mqtt_module(
    *,
    request_topic: str = "update/delta/dev/example",
    response_topic: str = "update/reported/dev/example",
    test_request_topic: str = "",
    test_response_topic: str = "",
    matterhub_id: str | None = None,
    subscribe_matterhub_topics: bool = False,
):
    mqtt_pkg_module = types.ModuleType("mqtt_pkg")
    mqtt_pkg_module.__path__ = []

    callbacks_module = types.ModuleType("mqtt_pkg.callbacks")
    callbacks_module.mqtt_callback = Mock(name="mqtt_callback")

    runtime_module = types.ModuleType("mqtt_pkg.runtime")
    runtime_module.subscribe = Mock(name="subscribe")
    runtime_module.set_connection = Mock(name="set_connection")
    runtime_module.get_connection = Mock(name="get_connection", return_value=None)
    runtime_module.check_mqtt_connection = Mock(name="check_mqtt_connection", return_value=True)

    class DummyAWSIoTClient:
        def describe_connection(self):
            return {
                "endpoint": "example.iot",
                "client_id": "matterhub-client",
                "cert_path": "konai_certificates",
                "cert_exists": True,
                "key_exists": True,
                "ca_exists": False,
            }

        def connect_mqtt(self):
            return object()

    runtime_module.AWSIoTClient = DummyAWSIoTClient

    settings_module = types.ModuleType("mqtt_pkg.settings")
    settings_module.KONAI_TOPIC_REQUEST = request_topic
    settings_module.KONAI_TOPIC_RESPONSE = response_topic
    settings_module.KONAI_TEST_TOPIC_REQUEST = test_request_topic
    settings_module.KONAI_TEST_TOPIC_RESPONSE = test_response_topic
    settings_module.SUBSCRIBE_MATTERHUB_TOPICS = subscribe_matterhub_topics
    settings_module.MATTERHUB_ID = matterhub_id

    state_module = types.ModuleType("mqtt_pkg.state")
    state_module.publish_bootstrap_all_states = Mock(name="publish_bootstrap_all_states")
    state_module.publish_device_state = Mock(name="publish_device_state")

    test_subscriber_module = types.ModuleType("mqtt_pkg.test_subscriber")
    test_subscriber_module.start_konai_test_subscriber_if_enabled = Mock(
        name="start_konai_test_subscriber_if_enabled"
    )

    update_module = types.ModuleType("mqtt_pkg.update")
    update_module.start_queue_worker = Mock(name="start_queue_worker")

    injected_modules = {
        "mqtt_pkg": mqtt_pkg_module,
        "mqtt_pkg.callbacks": callbacks_module,
        "mqtt_pkg.runtime": runtime_module,
        "mqtt_pkg.settings": settings_module,
        "mqtt_pkg.state": state_module,
        "mqtt_pkg.test_subscriber": test_subscriber_module,
        "mqtt_pkg.update": update_module,
    }

    original_modules = {name: sys.modules.get(name) for name in injected_modules}
    module_name = "test_mqtt_module"
    original_entrypoint = sys.modules.get(module_name)

    try:
        sys.modules.update(injected_modules)
        spec = importlib.util.spec_from_file_location(module_name, MQTT_ENTRYPOINT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        if original_entrypoint is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original_entrypoint


class MqttEntrypointTest(unittest.TestCase):
    def test_build_subscribe_topics_deduplicates_request_and_test_topics(self) -> None:
        module = load_mqtt_module(
            request_topic="update/delta/dev/example",
            test_request_topic="update/delta/dev/example",
            matterhub_id="hub-1",
            subscribe_matterhub_topics=True,
        )

        topics = module.build_subscribe_topics()

        self.assertEqual(
            [
                "update/delta/dev/example",
                "matterhub/hub-1/git/update",
                "matterhub/update/specific/hub-1",
            ],
            topics,
        )

    def test_build_startup_report_contains_connection_and_topic_summary(self) -> None:
        module = load_mqtt_module(
            request_topic="update/delta/dev/example",
            response_topic="update/reported/dev/example",
            matterhub_id="hub-1",
            subscribe_matterhub_topics=True,
        )

        topics = module.build_subscribe_topics()
        report = module.build_startup_report(module.AWSIoTClient(), topics)

        self.assertIn("[MQTT] endpoint=example.iot", report)
        self.assertIn("[MQTT] client_id=matterhub-client", report)
        self.assertIn("[MQTT] request_topic=update/delta/dev/example", report)
        self.assertIn("[MQTT] response_topic=update/reported/dev/example", report)
        self.assertIn("[MQTT] subscribe_count=3", report)
        self.assertIn("[MQTT] subscribe[1]=update/delta/dev/example", report)


if __name__ == "__main__":
    unittest.main()
