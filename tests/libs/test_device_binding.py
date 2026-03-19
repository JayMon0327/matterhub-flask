from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libs.device_binding import evaluate_mac_binding, load_allowed_macs, normalize_mac


class DeviceBindingTest(unittest.TestCase):
    def test_normalize_mac_supports_common_formats(self) -> None:
        self.assertEqual("aa:bb:cc:dd:ee:ff", normalize_mac("AA-BB-CC-DD-EE-FF"))
        self.assertEqual("aa:bb:cc:dd:ee:ff", normalize_mac("aabbccddeeff"))
        self.assertEqual("", normalize_mac("invalid"))

    def test_load_allowed_macs_from_inline_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed_file = Path(temp_dir) / "allowed.txt"
            allowed_file.write_text("11:22:33:44:55:66\ninvalid\n", encoding="utf-8")
            env = {
                "MAC_BINDING_ALLOWED": "aa:bb:cc:dd:ee:ff,AA-BB-CC-DD-EE-11",
                "MAC_BINDING_ALLOWED_FILE": str(allowed_file),
            }
            allowed = load_allowed_macs(env=env)
            self.assertEqual(
                {"aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:11", "11:22:33:44:55:66"},
                allowed,
            )

    def test_evaluate_returns_allowed_when_binding_disabled(self) -> None:
        allowed, details = evaluate_mac_binding(env={"MAC_BINDING_ENABLED": "0"})
        self.assertTrue(allowed)
        self.assertEqual("disabled", details["reason"])

    def test_evaluate_returns_allowed_when_runtime_mac_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            net_root = Path(temp_dir) / "net"
            iface = net_root / "wlan0"
            iface.mkdir(parents=True)
            (iface / "address").write_text("aa:bb:cc:dd:ee:ff\n", encoding="utf-8")

            env = {
                "MAC_BINDING_ENABLED": "1",
                "MAC_BINDING_ALLOWED": "aa:bb:cc:dd:ee:ff",
                "MAC_BINDING_INTERFACE": "wlan0",
            }
            allowed, details = evaluate_mac_binding(env=env, sys_class_net=net_root)
            self.assertTrue(allowed)
            self.assertEqual("allowed_mac_matched", details["reason"])

    def test_evaluate_returns_block_when_runtime_mac_not_matched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            net_root = Path(temp_dir) / "net"
            iface = net_root / "wlan0"
            iface.mkdir(parents=True)
            (iface / "address").write_text("aa:bb:cc:dd:ee:ff\n", encoding="utf-8")

            env = {
                "MAC_BINDING_ENABLED": "1",
                "MAC_BINDING_ALLOWED": "11:22:33:44:55:66",
                "MAC_BINDING_INTERFACE": "wlan0",
            }
            allowed, details = evaluate_mac_binding(env=env, sys_class_net=net_root)
            self.assertFalse(allowed)
            self.assertEqual("allowed_mac_not_matched", details["reason"])


if __name__ == "__main__":
    unittest.main()

