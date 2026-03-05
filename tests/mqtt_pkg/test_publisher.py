from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_publisher_module():
    awscrt_module = types.ModuleType("awscrt")
    awscrt_module.io = types.SimpleNamespace()
    awscrt_module.mqtt = types.SimpleNamespace(
        QoS=types.SimpleNamespace(AT_MOST_ONCE=0),
        Connection=object,
    )
    awsiot_module = types.ModuleType("awsiot")
    awsiot_module.mqtt_connection_builder = types.SimpleNamespace()
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None

    with patch.dict(
        sys.modules,
        {
            "awscrt": awscrt_module,
            "awsiot": awsiot_module,
            "dotenv": dotenv_module,
        },
    ):
        sys.modules.pop("mqtt_pkg.publisher", None)
        sys.modules.pop("mqtt_pkg.runtime", None)
        sys.modules.pop("mqtt_pkg.settings", None)
        return importlib.import_module("mqtt_pkg.publisher")


class PublisherTest(unittest.TestCase):
    def test_publish_uses_response_topic_and_waits_for_future(self) -> None:
        publisher = load_publisher_module()
        future = Mock()
        future.result = Mock()
        connection = Mock()
        connection.publish.return_value = (future, 1)

        with patch.object(publisher.runtime, "get_connection", return_value=connection):
            with patch.object(publisher.settings, "KONAI_TOPIC_RESPONSE", "update/reported/dev/example"):
                with patch("builtins.print") as print_mock:
                    publisher.publish({"type": "bootstrap_all_states", "data": []})

        connection.publish.assert_called_once()
        future.result.assert_called_once_with(timeout=5)
        print_mock.assert_any_call(
            "[MQTT] publish_result status=success topic=update/reported/dev/example type=bootstrap_all_states"
        )


if __name__ == "__main__":
    unittest.main()
