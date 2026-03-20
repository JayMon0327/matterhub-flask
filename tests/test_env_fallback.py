"""환경변수 fallback 우선순위 테스트."""

import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch


def reload_settings(**env_overrides):
    """환경변수를 오버라이드한 후 mqtt_pkg.settings를 리임포트."""
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None

    with patch.dict(os.environ, env_overrides, clear=False):
        with patch.dict(sys.modules, {"dotenv": dotenv_module}):
            for mod_name in list(sys.modules):
                if mod_name == "mqtt_pkg" or mod_name.startswith("mqtt_pkg."):
                    del sys.modules[mod_name]
                if mod_name.startswith("providers"):
                    del sys.modules[mod_name]
            return importlib.import_module("mqtt_pkg.settings")


class EnvFallbackTest(unittest.TestCase):
    def test_mqtt_prefix_takes_priority_over_konai(self) -> None:
        settings = reload_settings(
            MQTT_TOPIC_SUBSCRIBE="mqtt/sub",
            KONAI_TOPIC_REQUEST="konai/req",
        )
        self.assertEqual("mqtt/sub", settings.MQTT_TOPIC_SUBSCRIBE)

    def test_konai_fallback_when_mqtt_not_set(self) -> None:
        env = {"KONAI_TOPIC_REQUEST": "konai/req"}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MQTT_TOPIC_SUBSCRIBE", None)
            os.environ.pop("KONAI_TOPIC", None)
            settings = reload_settings(**env)
        self.assertEqual("konai/req", settings.MQTT_TOPIC_SUBSCRIBE)

    def test_provider_default_when_no_env(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in ["MQTT_TOPIC_SUBSCRIBE", "KONAI_TOPIC_REQUEST", "KONAI_TOPIC"]:
                os.environ.pop(key, None)
            settings = reload_settings()
        # provider 기본값 (konai delta topic)
        self.assertIn("update/delta/dev/", settings.MQTT_TOPIC_SUBSCRIBE)

    def test_throttle_sec_fallback(self) -> None:
        settings = reload_settings(KONAI_EVENT_THROTTLE_SEC="5")
        self.assertEqual(5.0, settings.MQTT_EVENT_THROTTLE_SEC)

    def test_throttle_sec_mqtt_priority(self) -> None:
        settings = reload_settings(
            MQTT_EVENT_THROTTLE_SEC="10",
            KONAI_EVENT_THROTTLE_SEC="5",
        )
        self.assertEqual(10.0, settings.MQTT_EVENT_THROTTLE_SEC)

    def test_report_entity_ids_from_env(self) -> None:
        settings = reload_settings(MQTT_REPORT_ENTITY_IDS="sensor.a,sensor.b")
        self.assertEqual(["sensor.a", "sensor.b"], settings.MQTT_REPORT_ENTITY_IDS)


if __name__ == "__main__":
    unittest.main()
