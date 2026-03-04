from __future__ import annotations

import types
import unittest
from unittest.mock import patch

from device_config import mqtt_probe


class MqttProbeTest(unittest.TestCase):
    def test_resolve_probe_topic_uses_response_topic_for_reported(self) -> None:
        settings = types.SimpleNamespace(
            KONAI_TOPIC_REQUEST="update/delta/dev/example",
            KONAI_TOPIC_RESPONSE="update/reported/dev/example",
            KONAI_TEST_TOPIC_REQUEST="",
            KONAI_TEST_TOPIC_RESPONSE="",
        )
        with patch.object(mqtt_probe, "_load_settings", return_value=settings):
            self.assertEqual(
                "update/reported/dev/example",
                mqtt_probe.resolve_probe_topic("reported"),
            )

    def test_resolve_probe_topic_requires_custom_topic(self) -> None:
        settings = types.SimpleNamespace(
            KONAI_TOPIC_REQUEST="update/delta/dev/example",
            KONAI_TOPIC_RESPONSE="update/reported/dev/example",
            KONAI_TEST_TOPIC_REQUEST="",
            KONAI_TEST_TOPIC_RESPONSE="",
        )
        with patch.object(mqtt_probe, "_load_settings", return_value=settings):
            with self.assertRaisesRegex(ValueError, "--topic 값이 필요합니다"):
                mqtt_probe.resolve_probe_topic("custom", "")

    def test_build_probe_plan_warns_for_default_client_id(self) -> None:
        lines = mqtt_probe.build_probe_plan(
            connection_info={
                "endpoint": "example.iot",
                "client_id": "matterhub-client",
                "cert_path": "konai_certificates",
                "cert_exists": True,
                "key_exists": True,
                "ca_exists": False,
            },
            topic_mode="reported",
            topic="update/reported/dev/example",
            listen_seconds=3,
            uses_default_client_id=True,
        )

        self.assertIn("[PROBE] topic_mode=reported", lines)
        self.assertIn("[PROBE] client_id=matterhub-client", lines)
        self.assertTrue(any("기본 client_id" in line for line in lines))

    def test_main_resolves_topic_and_calls_run_probe(self) -> None:
        settings = types.SimpleNamespace(
            KONAI_TOPIC_REQUEST="update/delta/dev/example",
            KONAI_TOPIC_RESPONSE="update/reported/dev/example",
            KONAI_TEST_TOPIC_REQUEST="",
            KONAI_TEST_TOPIC_RESPONSE="",
        )
        with patch.object(mqtt_probe, "_load_settings", return_value=settings):
            with patch.object(mqtt_probe, "run_probe", return_value=0) as run_probe_mock:
                exit_code = mqtt_probe.main(
                    [
                        "--topic-mode",
                        "custom",
                        "--topic",
                        "topic/custom",
                        "--listen-seconds",
                        "1.5",
                        "--client-id",
                        "probe-client",
                    ]
                )

        self.assertEqual(0, exit_code)
        run_probe_mock.assert_called_once_with(
            topic_mode="custom",
            topic="topic/custom",
            listen_seconds=1.5,
            client_id="probe-client",
        )


if __name__ == "__main__":
    unittest.main()
