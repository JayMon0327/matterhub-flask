from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import patch


def _setup_fake_modules():
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
    return {
        "awscrt": awscrt_module,
        "awsiot": awsiot_module,
        "dotenv": dotenv_module,
    }


def load_state_module():
    fake = _setup_fake_modules()
    with patch.dict(sys.modules, fake):
        for mod_name in [
            "mqtt_pkg.state", "mqtt_pkg.publisher",
            "mqtt_pkg.runtime", "mqtt_pkg.settings",
        ]:
            sys.modules.pop(mod_name, None)
        return importlib.import_module("mqtt_pkg.state")


class TestUnavailableResolved(unittest.TestCase):
    def setUp(self):
        self.state = load_state_module()
        self.pub = self.state.DeviceAlertPublisher()

    def _patch_connected(self, connected=True):
        return patch.object(self.state.runtime, "is_connected", return_value=connected)

    def _patch_matterhub_id(self, hub_id="test-hub"):
        return patch.object(self.state.settings, "MATTERHUB_ID", hub_id)

    def _patch_ha_states(self, states):
        return patch.object(self.state, "_fetch_ha_states", return_value=states)

    def _patch_devices_file(self, path=None):
        return patch.object(self.state.settings, "DEVICES_FILE_PATH", path)

    def _patch_publish(self):
        return patch.object(self.state.publisher, "publish")

    def test_publishes_unavailable_resolved_on_recovery(self):
        """디바이스 복구 시 UNAVAILABLE_RESOLVED publish"""
        init_states = [
            {"entity_id": "binary.living", "state": "on",
             "attributes": {"friendly_name": "거실"}}
        ]
        unavail_states = [
            {"entity_id": "binary.living", "state": "unavailable",
             "attributes": {"friendly_name": "거실"}}
        ]
        recovered_states = [
            {"entity_id": "binary.living", "state": "on",
             "attributes": {"friendly_name": "거실"}}
        ]

        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            # 1차: 초기화 (on 상태)
            with self._patch_ha_states(init_states):
                self.pub.check_and_publish()
            mock_pub.assert_not_called()

            # 2차: unavailable 전환 → UNAVAILABLE alert
            self.pub._last_check = 0
            with self._patch_ha_states(unavail_states):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 1)
            self.assertIn("UNAVAILABLE", self.pub._alerted.get("binary.living", set()))

            # 3차: 복구 → UNAVAILABLE_RESOLVED publish
            mock_pub.reset_mock()
            self.pub._last_check = 0
            with self._patch_ha_states(recovered_states):
                self.pub.check_and_publish()

            mock_pub.assert_called_once()
            payload = mock_pub.call_args[0][0]
            self.assertEqual(payload["alert_type"], "UNAVAILABLE_RESOLVED")
            self.assertEqual(payload["entity_id"], "binary.living")
            self.assertNotIn("UNAVAILABLE", self.pub._alerted.get("binary.living", set()))

    def test_publishes_seed_on_init_for_unavailable_devices(self):
        """초기화 시 이미 unavailable인 디바이스에 source='seed' publish"""
        states = [
            {"entity_id": "binary.dead", "state": "unavailable",
             "attributes": {"friendly_name": "dead"}},
            {"entity_id": "binary.alive", "state": "on",
             "attributes": {"friendly_name": "alive"}},
        ]

        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            with self._patch_ha_states(states):
                self.pub.check_and_publish()  # _initialized False → seed loop

            # SEED publish는 unavailable인 binary.dead에 대해서만
            seed_calls = [
                c for c in mock_pub.call_args_list
                if c[0][0].get("source") == "seed"
            ]
            self.assertEqual(len(seed_calls), 1)
            self.assertEqual(seed_calls[0][0][0]["entity_id"], "binary.dead")
            self.assertEqual(seed_calls[0][0][0]["alert_type"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
