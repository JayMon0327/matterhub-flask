from __future__ import annotations

import importlib
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch, mock_open


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


def _make_ha_states(entity_ids):
    return [
        {
            "entity_id": eid,
            "state": "on",
            "last_changed": "2026-03-21T00:00:00Z",
            "attributes": {"friendly_name": eid},
        }
        for eid in entity_ids
    ]


class TestPublishDeviceStatesBulk(unittest.TestCase):
    def setUp(self):
        self.state = load_state_module()
        self.state._last_device_state_publish = 0.0

    def _patch_connected(self, connected=True):
        return patch.object(self.state.runtime, "is_connected", return_value=connected)

    def _patch_matterhub_id(self, hub_id="test-hub-001"):
        return patch.object(self.state.settings, "MATTERHUB_ID", hub_id)

    def _patch_ha_states(self, states):
        return patch.object(self.state, "_fetch_ha_states", return_value=states)

    def _patch_devices_file(self, path=None):
        return patch.object(self.state.settings, "DEVICES_FILE_PATH", path)

    def _patch_publish(self):
        return patch.object(self.state.publisher, "publish")

    def test_publish_device_states_bulk_correct_topic(self):
        ha_states = _make_ha_states(["light.living_room"])
        with self._patch_connected(), self._patch_matterhub_id("my-hub"), \
                self._patch_ha_states(ha_states), self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            self.state.publish_device_states_bulk()
            mock_pub.assert_called_once()
            call_kwargs = mock_pub.call_args
            self.assertEqual(call_kwargs[1]["response_topic"], "matterhub/my-hub/state/devices")

    def test_payload_format(self):
        ha_states = _make_ha_states(["sensor.temp"])
        with self._patch_connected(), self._patch_matterhub_id("hub-1"), \
                self._patch_ha_states(ha_states), self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            self.state.publish_device_states_bulk()
            payload = mock_pub.call_args[0][0]
            self.assertIn("hub_id", payload)
            self.assertIn("ts", payload)
            self.assertIn("devices", payload)
            device = payload["devices"]["sensor.temp"]
            self.assertEqual(device["state"], "on")
            self.assertIn("last_changed", device)
            self.assertIn("attributes", device)

    def test_filters_by_devices_json(self):
        ha_states = _make_ha_states(["light.a", "light.b", "sensor.c"])
        devices_json = json.dumps([
            {"entity_id": "light.a"},
            {"entity_id": "sensor.c"},
        ])
        with self._patch_connected(), self._patch_matterhub_id("hub"), \
                self._patch_ha_states(ha_states), \
                self._patch_devices_file("/tmp/devices.json"), \
                patch("os.path.exists", return_value=True), \
                patch("builtins.open", mock_open(read_data=devices_json)), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            self.state.publish_device_states_bulk()
            payload = mock_pub.call_args[0][0]
            self.assertIn("light.a", payload["devices"])
            self.assertIn("sensor.c", payload["devices"])
            self.assertNotIn("light.b", payload["devices"])

    def test_publishes_all_without_devices_json(self):
        ha_states = _make_ha_states(["light.a", "light.b", "sensor.c"])
        with self._patch_connected(), self._patch_matterhub_id("hub"), \
                self._patch_ha_states(ha_states), self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            self.state.publish_device_states_bulk()
            payload = mock_pub.call_args[0][0]
            self.assertEqual(len(payload["devices"]), 3)

    def test_respects_interval(self):
        ha_states = _make_ha_states(["light.a"])
        with self._patch_connected(), self._patch_matterhub_id("hub"), \
                self._patch_ha_states(ha_states), self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, \
                patch.object(self.state.settings, "MQTT_DEVICE_STATE_INTERVAL_SEC", 60), \
                patch("builtins.print"):
            self.state.publish_device_states_bulk()
            self.assertEqual(mock_pub.call_count, 1)
            # 두 번째 호출: 간격 내이므로 발행 안 함
            self.state.publish_device_states_bulk()
            self.assertEqual(mock_pub.call_count, 1)

    def test_skips_without_matterhub_id(self):
        with self._patch_connected(), self._patch_matterhub_id(None), \
                self._patch_publish() as mock_pub:
            self.state.publish_device_states_bulk()
            mock_pub.assert_not_called()

    def test_chunking_large_payload(self):
        # 많은 디바이스로 청크 분할 트리거
        entity_ids = [f"sensor.device_{i}" for i in range(200)]
        ha_states = _make_ha_states(entity_ids)
        with self._patch_connected(), self._patch_matterhub_id("hub"), \
                self._patch_ha_states(ha_states), self._patch_devices_file(None), \
                patch.object(self.state.settings, "MQTT_DEVICE_STATE_CHUNK_SIZE_KB", 10), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            self.state.publish_device_states_bulk()
            # 청크가 여러 개 발행되어야 함
            self.assertGreater(mock_pub.call_count, 1)
            # 각 청크에 chunk/total_chunks 필드 확인
            for call in mock_pub.call_args_list:
                payload = call[0][0]
                self.assertIn("chunk", payload)
                self.assertIn("total_chunks", payload)
                self.assertIn("devices", payload)


if __name__ == "__main__":
    unittest.main()
