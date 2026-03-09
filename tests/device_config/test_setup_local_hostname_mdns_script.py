from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "setup_local_hostname_mdns.sh"


class SetupLocalHostnameMdnsScriptTest(unittest.TestCase):
    def test_dry_run_prints_hostname_and_avahi_plan(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--dry-run",
                "--hostname",
                "MatterHub Setup_WhatsMatter",
                "--service-name",
                "MatterHub Wi-Fi Setup",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        self.assertIn("normalized_hostname=matterhub-setup-whatsmatter", output)
        self.assertIn("preferred_url=http://matterhub-setup-whatsmatter.local:8100/local/admin/network", output)
        self.assertIn("[dry-run] sudo hostnamectl set-hostname matterhub-setup-whatsmatter", output)
        self.assertIn("[dry-run] sudo systemctl enable --now avahi-daemon", output)
        self.assertIn("fallback_url=http://10.42.0.1:8100/local/admin/network", output)

    def test_dry_run_allows_custom_port_and_path(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--dry-run",
                "--hostname",
                "wm-hub",
                "--http-port",
                "8123",
                "--setup-path",
                "custom/setup",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        self.assertIn("preferred_url=http://wm-hub.local:8123/custom/setup", output)

    def test_rejects_non_numeric_port(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--dry-run",
                "--http-port",
                "abc",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("--http-port must be numeric", result.stderr)


if __name__ == "__main__":
    unittest.main()
