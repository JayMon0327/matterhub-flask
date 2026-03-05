from __future__ import annotations

import subprocess
import unittest
from collections import deque
from typing import Deque, Iterable
from unittest.mock import patch

from wifi_config.service import WifiConfigService, _split_terse_line


def completed(
    *,
    return_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=return_code,
        stdout=stdout,
        stderr=stderr,
    )


class OrderedRunner:
    def __init__(self, steps: Iterable[tuple[list[str], subprocess.CompletedProcess[str]]]) -> None:
        self.steps: Deque[tuple[list[str], subprocess.CompletedProcess[str]]] = deque(steps)
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append(command)
        if not self.steps:
            raise AssertionError(f"Unexpected command: {command}")
        expected_prefix, result = self.steps.popleft()
        if command[: len(expected_prefix)] != expected_prefix:
            raise AssertionError(
                f"Expected prefix {expected_prefix} but got {command}"
            )
        return result


class WifiConfigServiceTest(unittest.TestCase):
    def test_split_terse_line_handles_escaped_colons(self) -> None:
        self.assertEqual(
            ["*", "Office:Lab", "75", "WPA2"],
            _split_terse_line("*:Office\\:Lab:75:WPA2"),
        )

    def test_scan_wifi_returns_unique_networks_sorted_by_signal(self) -> None:
        runner = OrderedRunner(
            [
                (
                    [
                        "nmcli",
                        "-t",
                        "-f",
                        "IN-USE,SSID,SIGNAL,SECURITY,BARS,CHAN",
                        "device",
                        "wifi",
                        "list",
                        "ifname",
                        "wlan0",
                        "--rescan",
                        "yes",
                    ],
                    completed(
                        stdout=(
                            "*:Office\\:Lab:78:WPA2:strong:6\n"
                            ":Guest:31::weak:11\n"
                            ":Office\\:Lab:60:WPA2:mid:6\n"
                        )
                    ),
                )
            ]
        )
        service = WifiConfigService(runner=runner)

        result = service.scan_wifi()

        self.assertEqual(2, len(result))
        self.assertEqual("Office:Lab", result[0]["ssid"])
        self.assertEqual(78, result[0]["signal"])
        self.assertTrue(result[0]["in_use"])
        self.assertEqual("Guest", result[1]["ssid"])

    def test_connect_failure_rolls_back_and_starts_ap_mode(self) -> None:
        runner = OrderedRunner(
            [
                (
                    ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                    completed(stdout="HomeNet:home-uuid:802-11-wireless:wlan0\n"),
                ),
                (
                    ["nmcli", "device", "wifi", "connect", "NewWifi", "ifname", "wlan0", "password", "badpass"],
                    completed(stdout=""),
                ),
                (
                    ["nmcli", "connection", "up", "uuid", "home-uuid", "ifname", "wlan0"],
                    completed(return_code=10, stderr="activation failed"),
                ),
                (
                    [
                        "nmcli",
                        "device",
                        "wifi",
                        "hotspot",
                        "ifname",
                        "wlan0",
                        "ssid",
                        "Matterhub-Setup-test01",
                        "password",
                        "matterhub1234",
                    ],
                    completed(stdout="Hotspot created"),
                ),
                (
                    ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                    completed(stdout="Matterhub-Setup-test01:ap-uuid:802-11-wireless:wlan0\n"),
                ),
                (
                    [
                        "nmcli",
                        "connection",
                        "modify",
                        "id",
                        "Matterhub-Setup-test01",
                        "ipv4.method",
                        "shared",
                        "ipv4.addresses",
                        "10.42.0.1/24",
                        "ipv6.method",
                        "ignore",
                    ],
                    completed(stdout=""),
                ),
                (
                    ["nmcli", "connection", "up", "id", "Matterhub-Setup-test01", "ifname", "wlan0"],
                    completed(stdout="Connection up"),
                ),
                (
                    ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                    completed(stdout="Matterhub-Setup-test01:ap-uuid:802-11-wireless:wlan0\n"),
                ),
            ]
        )
        service = WifiConfigService(
            runner=runner,
            ap_password="matterhub1234",
            default_ap_ssid="Matterhub-Setup-test01",
            ap_ipv4_cidr="10.42.0.1/24",
        )

        with patch.object(service, "_wait_for_health", return_value=False):
            with patch.object(service, "_default_ap_ssid", return_value="Matterhub-Setup-test01"):
                result = service.connect_wifi(
                    ssid="NewWifi",
                    password="badpass",
                    rollback_on_failure=True,
                    ap_mode_on_failure=True,
                )

        self.assertFalse(result["success"])
        self.assertTrue(result["rollback_attempted"])
        self.assertFalse(result["rollback_success"])
        self.assertTrue(result["ap_mode_started"])
        self.assertEqual("HomeNet", result["previous_connection"]["name"])
        self.assertEqual("10.42.0.1", result["ap_mode"]["gateway_ip"])
        self.assertEqual(
            "http://10.42.0.1:8100/local/admin/network",
            result["ap_mode"]["setup_url"],
        )

    def test_start_ap_mode_requires_min_password_length(self) -> None:
        service = WifiConfigService(runner=OrderedRunner([]))
        with self.assertRaisesRegex(ValueError, "at least 8"):
            service.start_ap_mode(ssid="MatterHub-Setup-abc123", password="12345")

    def test_default_ap_ssid_uses_service_default(self) -> None:
        service = WifiConfigService(
            runner=OrderedRunner([]),
            default_ap_ssid="Matterhub-Setup-WhatsMatter",
        )
        self.assertEqual("Matterhub-Setup-WhatsMatter", service._default_ap_ssid())

    def test_activate_saved_connection_reports_success(self) -> None:
        runner = OrderedRunner(
            [
                (
                    ["nmcli", "connection", "up", "id", "HomeNet", "ifname", "wlan0"],
                    completed(stdout="Connection activated"),
                ),
                (
                    ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                    completed(stdout="HomeNet:u1:802-11-wireless:wlan0\n"),
                ),
                (
                    ["nmcli", "-t", "-f", "STATE", "general", "status"],
                    completed(stdout="connected\n"),
                ),
                (
                    ["nmcli", "-t", "-f", "IN-USE,SSID", "device", "wifi", "list", "ifname", "wlan0"],
                    completed(stdout="*:HomeNet\n"),
                ),
            ]
        )
        service = WifiConfigService(
            runner=runner,
            sleep_fn=lambda _: None,
            monotonic_fn=lambda: 0.0,
        )

        result = service.activate_saved_connection("HomeNet", timeout_seconds=10)

        self.assertTrue(result["success"])
        self.assertEqual("HomeNet", result["connection_name"])
        self.assertEqual("HomeNet", result["current_ssid"])

    def test_activate_saved_connection_reports_failure_when_up_fails(self) -> None:
        runner = OrderedRunner(
            [
                (
                    ["nmcli", "connection", "up", "id", "BadNet", "ifname", "wlan0"],
                    completed(return_code=10, stderr="activation failed"),
                ),
                (
                    ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                    completed(stdout="Matterhub-Setup-WhatsMatter:ap-uuid:802-11-wireless:wlan0\n"),
                ),
            ]
        )
        service = WifiConfigService(runner=runner)

        result = service.activate_saved_connection("BadNet", timeout_seconds=10)

        self.assertFalse(result["success"])
        self.assertEqual("BadNet", result["connection_name"])


if __name__ == "__main__":
    unittest.main()
