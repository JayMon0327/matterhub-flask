from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from wifi_config.bootstrap import ensure_bootstrap_ap, watch_disconnection_and_start_ap
from wifi_config.state import ProvisionStateStore


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
        state_store = ProvisionStateStore()
        service.get_status.return_value = {
            "general_state": "connected (global)",
            "current_ssid": "OfficeWifi",
        }
        result = ensure_bootstrap_ap(service, state_store=state_store, logger=lambda _: None)
        self.assertEqual("already_connected", result["reason"])
        service.start_ap_mode.assert_not_called()
        self.assertEqual("STA_CONNECTED", state_store.snapshot()["state"])

    def test_start_ap_mode_when_not_connected(self) -> None:
        service = Mock()
        state_store = ProvisionStateStore()
        service.get_status.return_value = {
            "general_state": "disconnected",
            "current_ssid": "",
        }
        service.start_ap_mode.return_value = {
            "ssid": "MatterHub-Setup-ab12cd",
            "interface": "wlan0",
        }
        with patch.dict(
            os.environ,
            {"WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS": "0"},
            clear=False,
        ):
            result = ensure_bootstrap_ap(service, state_store=state_store, logger=lambda _: None)
        self.assertTrue(result["started"])
        self.assertEqual("fallback_ap_started", result["reason"])
        service.start_ap_mode.assert_called_once()
        self.assertEqual("AP_MODE", state_store.snapshot()["state"])

    def test_bootstrap_reconnects_known_network_before_ap(self) -> None:
        service = Mock()
        state_store = ProvisionStateStore()
        service.default_ap_ssid = "Matterhub-Setup-WhatsMatter"
        service.get_status.return_value = {
            "general_state": "disconnected",
            "current_ssid": "",
        }
        service.list_saved_connections.return_value = [
            {
                "name": "OfficeWifi",
                "ssid": "OfficeWifi",
                "autoconnect": True,
            }
        ]
        service.scan_wifi.return_value = [{"ssid": "OfficeWifi", "signal": 85}]
        service.activate_saved_connection.return_value = {
            "success": True,
            "connection_name": "OfficeWifi",
            "current_ssid": "OfficeWifi",
        }

        with patch.dict(
            os.environ,
            {"WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS": "0"},
            clear=False,
        ):
            result = ensure_bootstrap_ap(service, state_store=state_store, logger=lambda _: None)

        self.assertFalse(result["started"])
        self.assertEqual("known_network_reconnected", result["reason"])
        service.start_ap_mode.assert_not_called()
        self.assertEqual("STA_CONNECTED", state_store.snapshot()["state"])

    def test_watchdog_disabled_by_env(self) -> None:
        service = Mock()
        with patch.dict(os.environ, {"WIFI_AUTO_AP_ON_DISCONNECT": "0"}, clear=False):
            watch_disconnection_and_start_ap(
                service,
                logger=lambda _: None,
                max_checks=1,
                sleep_fn=lambda _: None,
            )
        service.get_status.assert_not_called()

    def test_watchdog_starts_ap_after_grace_period(self) -> None:
        service = Mock()
        state_store = ProvisionStateStore()
        service.default_ap_ssid = "Matterhub-Setup-WhatsMatter"
        service.get_status.return_value = {
            "general_state": "disconnected",
            "current_ssid": "",
            "active_connection": {},
        }
        service.start_ap_mode.return_value = {
            "ssid": "Matterhub-Setup-WhatsMatter",
            "gateway_ip": "10.42.0.1",
        }

        ticks = iter([0.0, 11.0, 11.0])
        with patch.dict(
            os.environ,
            {
                "WIFI_AUTO_AP_ON_DISCONNECT": "1",
                "WIFI_AP_WATCH_INTERVAL_SECONDS": "2",
                "WIFI_AP_DISCONNECT_GRACE_SECONDS": "10",
                "WIFI_AP_AUTO_RECONNECT_ENABLED": "0",
            },
            clear=False,
        ):
            watch_disconnection_and_start_ap(
                service,
                state_store=state_store,
                logger=lambda _: None,
                max_checks=2,
                sleep_fn=lambda _: None,
                monotonic_fn=lambda: next(ticks),
            )

        service.start_ap_mode.assert_called_once()
        self.assertEqual("AP_MODE", state_store.snapshot()["state"])

    def test_watchdog_auto_reconnects_known_network_when_ap_active(self) -> None:
        service = Mock()
        state_store = ProvisionStateStore()
        service.default_ap_ssid = "Matterhub-Setup-WhatsMatter"
        service.get_status.return_value = {
            "general_state": "connected (site only)",
            "current_ssid": "Matterhub-Setup-WhatsMatter",
            "active_connection": {"name": "Matterhub-Setup-WhatsMatter"},
        }
        service.list_saved_connections.return_value = [
            {
                "name": "HomeNet",
                "ssid": "HomeNet",
                "autoconnect": True,
            }
        ]
        service.scan_wifi.return_value = [{"ssid": "HomeNet", "signal": 80}]
        service.activate_saved_connection.return_value = {
            "success": True,
            "connection_name": "HomeNet",
            "current_ssid": "HomeNet",
        }

        with patch.dict(
            os.environ,
            {
                "WIFI_AUTO_AP_ON_DISCONNECT": "1",
                "WIFI_AP_AUTO_RECONNECT_ENABLED": "1",
                "WIFI_AP_AUTO_RECONNECT_INTERVAL_SECONDS": "5",
                "WIFI_AP_AUTO_RECONNECT_TIMEOUT_SECONDS": "20",
                "WIFI_AP_AUTO_RECONNECT_HOLD_SECONDS": "0",
            },
            clear=False,
        ):
            watch_disconnection_and_start_ap(
                service,
                state_store=state_store,
                logger=lambda _: None,
                max_checks=1,
                sleep_fn=lambda _: None,
                monotonic_fn=lambda: 0.0,
            )

        service.activate_saved_connection.assert_called_once_with(
            "HomeNet",
            timeout_seconds=20,
        )
        self.assertEqual("STA_CONNECTED", state_store.snapshot()["state"])

    def test_watchdog_holds_manual_recovery_ap_before_auto_reconnect(self) -> None:
        service = Mock()
        state_store = ProvisionStateStore()
        state_store.set_state(
            "AP_MODE",
            reason="manual_recovery_started",
            details={
                "ssid": "Matterhub-Setup-WhatsMatter",
                "manual_hold_until": 9999999999,
            },
        )
        service.default_ap_ssid = "Matterhub-Setup-WhatsMatter"
        service.get_status.return_value = {
            "general_state": "connected (site only)",
            "current_ssid": "Matterhub-Setup-WhatsMatter",
            "active_connection": {"name": "Matterhub-Setup-WhatsMatter"},
        }

        with patch.dict(
            os.environ,
            {
                "WIFI_AUTO_AP_ON_DISCONNECT": "1",
                "WIFI_AP_AUTO_RECONNECT_ENABLED": "1",
                "WIFI_AP_AUTO_RECONNECT_INTERVAL_SECONDS": "5",
                "WIFI_AP_AUTO_RECONNECT_TIMEOUT_SECONDS": "20",
                "WIFI_AP_AUTO_RECONNECT_HOLD_SECONDS": "0",
            },
            clear=False,
        ):
            watch_disconnection_and_start_ap(
                service,
                state_store=state_store,
                logger=lambda _: None,
                max_checks=1,
                sleep_fn=lambda _: None,
                monotonic_fn=lambda: 0.0,
            )

        service.activate_saved_connection.assert_not_called()
        snapshot = state_store.snapshot()
        self.assertEqual("AP_MODE", snapshot["state"])
        self.assertEqual("manual_recovery_started", snapshot["reason"])

    def test_watchdog_treats_hotspot_prefixed_profile_as_ap_active(self) -> None:
        service = Mock()
        state_store = ProvisionStateStore()
        service.default_ap_ssid = "Matterhub-Setup-WhatsMatter"
        service.get_status.return_value = {
            "general_state": "connecting",
            "current_ssid": "",
            "active_connection": {"name": "Hotspot-1"},
        }

        with patch.dict(
            os.environ,
            {
                "WIFI_AUTO_AP_ON_DISCONNECT": "1",
                "WIFI_AP_AUTO_RECONNECT_ENABLED": "1",
                "WIFI_AP_AUTO_RECONNECT_HOLD_SECONDS": "45",
            },
            clear=False,
        ):
            watch_disconnection_and_start_ap(
                service,
                state_store=state_store,
                logger=lambda _: None,
                max_checks=1,
                sleep_fn=lambda _: None,
                monotonic_fn=lambda: 0.0,
            )

        service.activate_saved_connection.assert_not_called()
        service.start_ap_mode.assert_not_called()
        self.assertEqual("AP_MODE", state_store.snapshot()["state"])

    def test_watchdog_reconnects_known_network_before_ap_when_disconnected(self) -> None:
        service = Mock()
        state_store = ProvisionStateStore()
        service.default_ap_ssid = "Matterhub-Setup-WhatsMatter"
        service.get_status.return_value = {
            "general_state": "disconnected",
            "current_ssid": "",
            "active_connection": {},
        }
        service.list_saved_connections.return_value = [
            {
                "name": "HomeNet",
                "ssid": "HomeNet",
                "autoconnect": True,
            }
        ]
        service.scan_wifi.return_value = [{"ssid": "HomeNet", "signal": 70}]
        service.activate_saved_connection.return_value = {
            "success": True,
            "connection_name": "HomeNet",
            "current_ssid": "HomeNet",
        }

        with patch.dict(
            os.environ,
            {
                "WIFI_AUTO_AP_ON_DISCONNECT": "1",
                "WIFI_AP_AUTO_RECONNECT_ENABLED": "1",
                "WIFI_AP_AUTO_RECONNECT_INTERVAL_SECONDS": "5",
                "WIFI_AP_AUTO_RECONNECT_TIMEOUT_SECONDS": "20",
            },
            clear=False,
        ):
            watch_disconnection_and_start_ap(
                service,
                state_store=state_store,
                logger=lambda _: None,
                max_checks=1,
                sleep_fn=lambda _: None,
                monotonic_fn=lambda: 0.0,
            )

        service.activate_saved_connection.assert_called_once_with(
            "HomeNet",
            timeout_seconds=20,
        )
        service.start_ap_mode.assert_not_called()
        self.assertEqual("STA_CONNECTED", state_store.snapshot()["state"])


if __name__ == "__main__":
    unittest.main()
