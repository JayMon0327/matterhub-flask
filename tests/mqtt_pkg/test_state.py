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
            self.assertIsInstance(payload["devices"], list)
            device = payload["devices"][0]
            self.assertEqual(device["entity_id"], "sensor.temp")
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
            self.assertIsInstance(payload["devices"], list)
            device_ids = [d["entity_id"] for d in payload["devices"]]
            self.assertIn("light.a", device_ids)
            self.assertIn("sensor.c", device_ids)
            self.assertNotIn("light.b", device_ids)

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


def _make_ha_state(entity_id, state_val="on", attributes=None):
    return {
        "entity_id": entity_id,
        "state": state_val,
        "last_changed": "2026-03-21T00:00:00Z",
        "attributes": attributes or {"friendly_name": entity_id},
    }


class TestDeviceAlertPublisher(unittest.TestCase):
    def setUp(self):
        self.state = load_state_module()
        # 매 테스트마다 새 인스턴스
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

    def _patch_battery_threshold(self, threshold=0):
        return patch.object(self.state.settings, "MQTT_ALERT_BATTERY_THRESHOLD", threshold)

    def _patch_interval(self, sec=5):
        return patch.object(self.state.settings, "MQTT_ALERT_CHECK_INTERVAL_SEC", sec)

    def test_init_no_alert_published(self):
        """초기화 시 이미 unavailable인 디바이스에 대해 알림 미발행"""
        states = [
            _make_ha_state("light.a", "unavailable"),
            _make_ha_state("light.b", "on"),
        ]
        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_ha_states(states), self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            self.pub.check_and_publish()
            mock_pub.assert_not_called()
            # unavailable인 것은 _alerted에 seed됨
            self.assertIn("UNAVAILABLE", self.pub._alerted.get("light.a", set()))
            self.assertNotIn("light.b", self.pub._alerted)

    def test_unavailable_transition_publishes_alert(self):
        """unavailable 전환 시 UNAVAILABLE 알림 발행 + 토픽/페이로드 검증"""
        init_states = [_make_ha_state("light.a", "on")]
        changed_states = [_make_ha_state("light.a", "unavailable")]
        with self._patch_connected(), self._patch_matterhub_id("hub-1"), \
                self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            # 1차: 초기화
            with self._patch_ha_states(init_states):
                self.pub.check_and_publish()
            mock_pub.assert_not_called()
            self.pub._last_check = 0  # interval 리셋
            # 2차: 전환
            with self._patch_ha_states(changed_states):
                self.pub.check_and_publish()
            mock_pub.assert_called_once()
            payload = mock_pub.call_args[0][0]
            topic = mock_pub.call_args[1]["response_topic"]
            self.assertEqual(topic, "matterhub/hub-1/event/device_alerts")
            self.assertEqual(payload["alert_type"], "UNAVAILABLE")
            self.assertEqual(payload["entity_id"], "light.a")
            self.assertEqual(payload["prev_state"], "on")
            self.assertEqual(payload["current_state"], "unavailable")

    def test_unavailable_no_repeat(self):
        """unavailable 유지 시 재발행 없음"""
        init_states = [_make_ha_state("light.a", "on")]
        changed_states = [_make_ha_state("light.a", "unavailable")]
        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            with self._patch_ha_states(init_states):
                self.pub.check_and_publish()
            self.pub._last_check = 0
            with self._patch_ha_states(changed_states):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 1)
            self.pub._last_check = 0
            # 3차: 여전히 unavailable
            with self._patch_ha_states(changed_states):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 1)

    def test_recovery_then_re_unavailable(self):
        """복구 후 재전환 시 다시 알림 발행"""
        init = [_make_ha_state("light.a", "on")]
        unavail = [_make_ha_state("light.a", "unavailable")]
        recovered = [_make_ha_state("light.a", "on")]
        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            with self._patch_ha_states(init):
                self.pub.check_and_publish()
            self.pub._last_check = 0
            with self._patch_ha_states(unavail):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 1)
            self.pub._last_check = 0
            # 복구
            with self._patch_ha_states(recovered):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 1)  # 복구 시 알림 아님
            self.assertNotIn("UNAVAILABLE", self.pub._alerted.get("light.a", set()))
            self.pub._last_check = 0
            # 재전환
            with self._patch_ha_states(unavail):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 2)

    def test_battery_empty_alert(self):
        """battery=0 시 BATTERY_EMPTY 알림 발행"""
        init = [_make_ha_state("sensor.door", "on", {"friendly_name": "Door", "battery": 50})]
        low_bat = [_make_ha_state("sensor.door", "on", {"friendly_name": "Door", "battery": 0})]
        with self._patch_connected(), self._patch_matterhub_id("hub"), \
                self._patch_devices_file(None), self._patch_battery_threshold(10), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            with self._patch_ha_states(init):
                self.pub.check_and_publish()
            self.pub._last_check = 0
            with self._patch_ha_states(low_bat):
                self.pub.check_and_publish()
            mock_pub.assert_called_once()
            payload = mock_pub.call_args[0][0]
            self.assertEqual(payload["alert_type"], "BATTERY_EMPTY")
            self.assertEqual(payload["battery"], 0)

    def test_battery_empty_no_repeat(self):
        """battery=0 유지 시 재발행 없음"""
        init = [_make_ha_state("sensor.door", "on", {"friendly_name": "Door", "battery": 50})]
        low_bat = [_make_ha_state("sensor.door", "on", {"friendly_name": "Door", "battery": 0})]
        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_devices_file(None), self._patch_battery_threshold(10), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            with self._patch_ha_states(init):
                self.pub.check_and_publish()
            self.pub._last_check = 0
            with self._patch_ha_states(low_bat):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 1)
            self.pub._last_check = 0
            with self._patch_ha_states(low_bat):
                self.pub.check_and_publish()
            self.assertEqual(mock_pub.call_count, 1)

    def test_interval_respected(self):
        """interval 내 호출 시 스킵"""
        init = [_make_ha_state("light.a", "on")]
        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_devices_file(None), self._patch_interval(9999), \
                self._patch_publish() as mock_pub, \
                self._patch_ha_states(init), patch("builtins.print"):
            self.pub.check_and_publish()  # 초기화
            # _last_check은 방금 설정됨 → interval 내
            self.pub.check_and_publish()
            mock_pub.assert_not_called()

    def test_skip_without_matterhub_id(self):
        """matterhub_id 없으면 스킵"""
        with self._patch_connected(), self._patch_matterhub_id(None), \
                self._patch_publish() as mock_pub:
            self.pub.check_and_publish()
            mock_pub.assert_not_called()

    def test_managed_filter(self):
        """managed 디바이스 필터링"""
        init = [_make_ha_state("light.a", "on"), _make_ha_state("light.b", "on")]
        changed = [_make_ha_state("light.a", "unavailable"), _make_ha_state("light.b", "unavailable")]
        devices_json = json.dumps([{"entity_id": "light.a"}])
        with self._patch_connected(), self._patch_matterhub_id(), \
                self._patch_devices_file("/tmp/devices.json"), \
                patch("os.path.exists", return_value=True), \
                patch("builtins.open", mock_open(read_data=devices_json)), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            with self._patch_ha_states(init):
                self.pub.check_and_publish()
            self.pub._last_check = 0
            with self._patch_ha_states(changed):
                self.pub.check_and_publish()
            # light.a만 알림 (light.b는 managed가 아님)
            self.assertEqual(mock_pub.call_count, 1)
            self.assertEqual(mock_pub.call_args[0][0]["entity_id"], "light.a")

    def test_payload_field_types(self):
        """페이로드 필드/타입 검증 (ts=int, battery=int|None)"""
        init = [_make_ha_state("light.a", "on", {"friendly_name": "Lamp", "device_class": "light"})]
        changed = [_make_ha_state("light.a", "unavailable", {"friendly_name": "Lamp", "device_class": "light"})]
        with self._patch_connected(), self._patch_matterhub_id("hub"), \
                self._patch_devices_file(None), \
                self._patch_publish() as mock_pub, patch("builtins.print"):
            with self._patch_ha_states(init):
                self.pub.check_and_publish()
            self.pub._last_check = 0
            with self._patch_ha_states(changed):
                self.pub.check_and_publish()
            payload = mock_pub.call_args[0][0]
            self.assertIsInstance(payload["ts"], int)
            self.assertIsNone(payload["battery"])
            self.assertEqual(payload["hub_id"], "hub")
            self.assertEqual(payload["attributes"]["friendly_name"], "Lamp")
            self.assertEqual(payload["attributes"]["device_class"], "light")


if __name__ == "__main__":
    unittest.main()
