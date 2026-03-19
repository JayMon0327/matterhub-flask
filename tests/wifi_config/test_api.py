from __future__ import annotations

import unittest
from pathlib import Path

try:
    from flask import Flask
    from wifi_config.api import create_wifi_blueprint
except ModuleNotFoundError:  # pragma: no cover - local env optional dependency
    Flask = None
    create_wifi_blueprint = None

from wifi_config.state import ProvisionStateStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class StubWifiService:
    interface = "wlan0"

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.connect_result = {
            "success": True,
            "target_ssid": "OfficeWifi",
            "rollback_attempted": False,
            "rollback_success": False,
            "ap_mode_started": False,
            "health_check_passed": True,
            "previous_connection": {"name": "OfficeWifi", "uuid": "u1", "device": "wlan0"},
            "active_connection": {"name": "OfficeWifi", "uuid": "u1", "device": "wlan0"},
        }

    def get_status(self):
        return {
            "interface": self.interface,
            "general_state": "connected",
            "wifi_device": {"DEVICE": "wlan0", "STATE": "connected"},
            "active_connection": {"name": "OfficeWifi", "uuid": "u1", "device": "wlan0"},
            "current_ssid": "OfficeWifi",
        }

    def scan_wifi(self, *, rescan: bool = True):
        del rescan
        return [{"ssid": "OfficeWifi", "signal": 82, "security": "WPA2", "in_use": True}]

    def connect_wifi(self, **kwargs):
        return {**self.connect_result, "target_ssid": kwargs.get("ssid", "unknown")}

    def list_saved_connections(self):
        return [{"name": "OfficeWifi", "uuid": "u1", "active": True, "autoconnect": True}]

    def delete_saved_connection(self, connection_name: str):
        self.deleted.append(connection_name)
        return {"deleted": connection_name}

    def start_ap_mode(self, *, ssid=None, password=None):
        del password
        return {"ssid": ssid or "MatterHub-Setup-abc123", "interface": "wlan0", "security": "wpa2-psk"}


@unittest.skipIf(Flask is None or create_wifi_blueprint is None, "flask is not installed")
class WifiApiBlueprintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StubWifiService()
        self.state_store = ProvisionStateStore()
        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        app.register_blueprint(create_wifi_blueprint(self.service, state_store=self.state_store))
        self.client = app.test_client()

    def test_status_endpoint_returns_ok_payload(self) -> None:
        response = self.client.get("/local/admin/network/status")
        body = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertTrue(body["ok"])
        self.assertEqual("connected", body["data"]["general_state"])
        self.assertEqual("BOOTING", body["data"]["provision_state"]["state"])
        self.assertEqual(
            "matterhub-setup-whatsmatter.local",
            body["data"]["local_access"]["fqdn"],
        )

    def test_connect_endpoint_requires_ssid(self) -> None:
        response = self.client.post("/local/admin/network/wifi/connect", json={})
        body = response.get_json()

        self.assertEqual(400, response.status_code)
        self.assertFalse(body["ok"])
        self.assertIn("ssid", body["error"]["message"])

    def test_connect_endpoint_returns_502_when_service_reports_failure(self) -> None:
        self.service.connect_result["success"] = False
        response = self.client.post(
            "/local/admin/network/wifi/connect",
            json={"ssid": "BadWifi", "password": "badpass"},
        )
        body = response.get_json()

        self.assertEqual(502, response.status_code)
        self.assertFalse(body["ok"])
        self.assertEqual("BadWifi", body["data"]["target_ssid"])
        snapshot = self.state_store.snapshot()
        self.assertEqual("STA_FAILED", snapshot["state"])
        self.assertEqual("user_submit_wifi_failed", snapshot["reason"])

    def test_connect_endpoint_sets_sta_connected_on_success(self) -> None:
        response = self.client.post(
            "/local/admin/network/wifi/connect",
            json={"ssid": "OfficeWifi", "password": "goodpass"},
        )
        body = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertTrue(body["ok"])
        snapshot = self.state_store.snapshot()
        self.assertEqual("STA_CONNECTED", snapshot["state"])
        self.assertEqual("user_submit_wifi_success", snapshot["reason"])

    def test_connect_endpoint_sets_ap_mode_when_fallback_started(self) -> None:
        self.service.connect_result["success"] = False
        self.service.connect_result["ap_mode_started"] = True
        response = self.client.post(
            "/local/admin/network/wifi/connect",
            json={"ssid": "NoSignalWifi", "password": "badpass"},
        )
        body = response.get_json()

        self.assertEqual(502, response.status_code)
        self.assertFalse(body["ok"])
        snapshot = self.state_store.snapshot()
        self.assertEqual("AP_MODE", snapshot["state"])
        self.assertEqual("ap_fallback_started_after_connect_failure", snapshot["reason"])

    def test_delete_saved_connection_invokes_service(self) -> None:
        response = self.client.delete("/local/admin/network/wifi/saved/OfficeWifi")
        body = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertTrue(body["ok"])
        self.assertEqual(["OfficeWifi"], self.service.deleted)

    def test_manual_recovery_endpoint_sets_hold_window(self) -> None:
        response = self.client.post("/local/admin/network/recovery/ap-mode", json={})
        body = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertTrue(body["ok"])
        self.assertEqual(600, body["data"]["manual_hold_seconds"])
        self.assertIn("manual_hold_until", body["data"])
        snapshot = self.state_store.snapshot()
        self.assertEqual("AP_MODE", snapshot["state"])
        self.assertEqual("manual_recovery_started", snapshot["reason"])
        self.assertIn("manual_hold_until", snapshot["details"])

    def test_wifi_admin_page_renders_template(self) -> None:
        response = self.client.get("/local/admin/network")
        text = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("MatterHub Wi-Fi 설정", text)
        self.assertIn("WhatsMatter Inc.", text)
        self.assertIn("알고 있는 네트워크", text)
        self.assertNotIn("fonts.googleapis.com", text)
        self.assertIn("matterhub-setup-whatsmatter.local", text)


if __name__ == "__main__":
    unittest.main()
