from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from wifi_config.bootstrap import ensure_bootstrap_ap


class WifiBootstrapTest(unittest.TestCase):
    def test_disabled_by_env(self) -> None:
        service = Mock()
        with patch.dict(os.environ, {"WIFI_AUTO_AP_ON_BOOT": "0"}, clear=False):
            result = ensure_bootstrap_ap(service)
        self.assertFalse(result["enabled"])
        self.assertFalse(result["started"])
        service.get_status.assert_not_called()

    def test_skip_when_already_connected(self) -> None:
        service = Mock()
        service.get_status.return_value = {
            "general_state": "connected (global)",
            "current_ssid": "OfficeWifi",
        }
        result = ensure_bootstrap_ap(service, logger=lambda _: None)
        self.assertEqual("already_connected", result["reason"])
        service.start_ap_mode.assert_not_called()

    def test_start_ap_mode_when_not_connected(self) -> None:
        service = Mock()
        service.get_status.return_value = {
            "general_state": "disconnected",
            "current_ssid": "",
        }
        service.start_ap_mode.return_value = {
            "ssid": "MatterHub-Setup-ab12cd",
            "interface": "wlan0",
        }
        result = ensure_bootstrap_ap(service, logger=lambda _: None)
        self.assertTrue(result["started"])
        self.assertEqual("fallback_ap_started", result["reason"])
        service.start_ap_mode.assert_called_once()


if __name__ == "__main__":
    unittest.main()
