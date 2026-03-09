from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = PROJECT_ROOT / "device_config" / "setup_initial_device.sh"


class SetupInitialDeviceScriptTest(unittest.TestCase):
    def test_dry_run_shows_env_updates_and_install_call(self) -> None:
        env = os.environ.copy()
        env["RUN_USER"] = "whatsmatter"

        result = subprocess.run(
            ["bash", str(SETUP_SCRIPT), "--dry-run"],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        output = result.stdout
        self.assertIn("env update: WIFI_AP_SSID=Matterhub-Setup-WhatsMatter", output)
        self.assertIn("env update: WIFI_AP_IPV4_CIDR=10.42.0.1/24", output)
        self.assertIn("env update: LOCAL_MDNS_ENABLED=1", output)
        self.assertIn("env update: MATTERHUB_LOCAL_HOSTNAME=matterhub-setup-whatsmatter", output)
        self.assertIn("env update: UPDATE_AGENT_ENABLED=1", output)
        self.assertIn("env update: UPDATE_AGENT_REQUIRE_MANIFEST=1", output)
        self.assertIn("env update: UPDATE_AGENT_REQUIRE_SHA256=0", output)
        self.assertIn("install_ubuntu24.sh 실행", output)
        self.assertIn("install_ubuntu24.sh --dry-run", output)
        self.assertIn("--local-hostname matterhub-setup-whatsmatter", output)

    def test_rejects_short_ap_password(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SETUP_SCRIPT),
                "--dry-run",
                "--wifi-ap-password",
                "short",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("at least 8 characters", result.stderr)

    def test_dry_run_passes_support_tunnel_options_to_install_script(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SETUP_SCRIPT),
                "--dry-run",
                "--setup-support-tunnel",
                "--support-host",
                "support.whatsmatter.local",
                "--support-user",
                "whatsmatter",
                "--support-remote-port",
                "22608",
                "--support-relay-operator-user",
                "ec2-user",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        self.assertIn("--setup-support-tunnel", output)
        self.assertIn("--support-host support.whatsmatter.local", output)
        self.assertIn("--support-user whatsmatter", output)
        self.assertIn("--support-remote-port 22608", output)
        self.assertIn("--support-relay-operator-user ec2-user", output)

    def test_dry_run_prefers_sudo_user_when_run_user_is_missing(self) -> None:
        env = os.environ.copy()
        env.pop("RUN_USER", None)
        env["SUDO_USER"] = "whatsmatter"

        result = subprocess.run(
            ["bash", str(SETUP_SCRIPT), "--dry-run"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("--support-device-user whatsmatter", result.stdout)

    def test_dry_run_passes_hardening_options_to_install_script(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SETUP_SCRIPT),
                "--dry-run",
                "--harden-reverse-tunnel-only",
                "--harden-allow-inbound-port",
                "80",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        self.assertIn("--harden-reverse-tunnel-only", output)
        self.assertIn("--harden-allow-inbound-port 80", output)

    def test_dry_run_passes_local_console_pam_option(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SETUP_SCRIPT),
                "--dry-run",
                "--harden-local-console-pam",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        self.assertIn("--harden-local-console-pam", output)

    def test_dry_run_can_disable_local_mdns(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SETUP_SCRIPT),
                "--dry-run",
                "--local-mdns-enabled",
                "0",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        self.assertIn("env update: LOCAL_MDNS_ENABLED=0", output)
        self.assertIn("--disable-local-mdns", output)

    def test_rejects_non_numeric_update_agent_poll_seconds(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SETUP_SCRIPT),
                "--dry-run",
                "--update-agent-poll-seconds",
                "abc",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("update-agent-poll-seconds", result.stderr)


if __name__ == "__main__":
    unittest.main()
