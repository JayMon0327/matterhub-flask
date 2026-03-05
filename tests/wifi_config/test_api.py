from __future__ import annotations

import unittest
from pathlib import Path

try:
    from flask import Flask
    from wifi_config.api import create_wifi_blueprint
except ModuleNotFoundError:  # pragma: no cover - local env optional dependency
    Flask = None
    create_wifi_blueprint = None


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
        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        app.register_blueprint(create_wifi_blueprint(self.service))
        self.client = app.test_client()

    def test_status_endpoint_returns_ok_payload(self) -> None:
        response = self.client.get("/local/admin/network/status")
        body = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertTrue(body["ok"])
        self.assertEqual("connected", body["data"]["general_state"])

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

    def test_delete_saved_connection_invokes_service(self) -> None:
        response = self.client.delete("/local/admin/network/wifi/saved/OfficeWifi")
        body = response.get_json()

        self.assertEqual(200, response.status_code)
        self.assertTrue(body["ok"])
        self.assertEqual(["OfficeWifi"], self.service.deleted)

    def test_wifi_admin_page_renders_template(self) -> None:
        response = self.client.get("/local/admin/network")
        text = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("MatterHub Wi-Fi 설정 센터", text)
        self.assertIn("WhatsMatter Inc.", text)


if __name__ == "__main__":
    unittest.main()
