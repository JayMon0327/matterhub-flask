from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = PROJECT_ROOT / "device_config" / "install_ubuntu24.sh"


class InstallUbuntu24ScriptTest(unittest.TestCase):
    def test_dry_run_prints_expected_install_plan(self) -> None:
        env = os.environ.copy()
        env["RUN_USER"] = "whatsmatter"

        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), "--dry-run"],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        output = result.stdout
        self.assertIn(
            "python3-venv python3-pip network-manager autossh openssh-server avahi-daemon avahi-utils libnss-mdns",
            output,
        )
        self.assertIn("systemctl enable --now ssh", output)
        self.assertIn("matterhub-api.service matterhub-mqtt.service", output)
        self.assertIn("matterhub-support-tunnel.service", output)
        self.assertIn("systemctl daemon-reload", output)
        self.assertIn("systemctl enable", output)
        self.assertIn("render_systemd_units.py", output)
        self.assertIn("로컬 mDNS/HTTP 서비스 광고 설정 실행", output)
        self.assertIn("setup_local_hostname_mdns.sh", output)
        self.assertIn("--hostname matterhub-setup-whatsmatter", output)
        enable_lines = [line for line in output.splitlines() if "systemctl enable" in line]
        self.assertTrue(enable_lines)
        self.assertTrue(all("matterhub-support-tunnel.service" not in line for line in enable_lines))

    def test_dry_run_can_skip_os_packages(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), "--dry-run", "--skip-os-packages"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("OS 패키지 설치 단계 생략", result.stdout)

    def test_dry_run_prefers_sudo_user_when_run_user_is_missing(self) -> None:
        env = os.environ.copy()
        env.pop("RUN_USER", None)
        env["SUDO_USER"] = "whatsmatter"

        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), "--dry-run", "--skip-os-packages"],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("서비스 실행 사용자: whatsmatter", result.stdout)

    def test_dry_run_can_disable_local_mdns(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(INSTALL_SCRIPT),
                "--dry-run",
                "--disable-local-mdns",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("로컬 mDNS 접속: 비활성화", output)
        self.assertNotIn("setup_local_hostname_mdns.sh", output)

    def test_dry_run_can_chain_support_tunnel_setup(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(INSTALL_SCRIPT),
                "--dry-run",
                "--setup-support-tunnel",
                "--support-host",
                "support.whatsmatter.local",
                "--support-user",
                "whatsmatter",
                "--support-remote-port",
                "22608",
                "--support-device-user",
                "whatsmatter",
                "--enable-support-tunnel-now",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("reverse tunnel 초기 설정 실행", output)
        self.assertIn("setup_support_tunnel.sh", output)
        self.assertIn("--host support.whatsmatter.local", output)
        self.assertIn("--user whatsmatter", output)
        self.assertIn("--remote-port 22608", output)
        self.assertIn("--device-user whatsmatter", output)
        self.assertIn("--relay-operator-user ec2-user", output)
        self.assertIn("--enable-now", output)
        self.assertIn("--dry-run", output)

    def test_dry_run_can_chain_reverse_tunnel_only_hardening(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(INSTALL_SCRIPT),
                "--dry-run",
                "--harden-reverse-tunnel-only",
                "--harden-allow-inbound-port",
                "80",
                "--harden-allow-inbound-port",
                "443",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("reverse tunnel only 하드닝 실행", output)
        self.assertIn("harden_reverse_tunnel_only.sh", output)
        self.assertIn("--allow-inbound-port 80", output)
        self.assertIn("--allow-inbound-port 443", output)
        self.assertIn("--dry-run", output)

    def test_dry_run_can_chain_local_console_pam_hardening(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(INSTALL_SCRIPT),
                "--dry-run",
                "--harden-local-console-pam",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("로컬 콘솔 로그인 제한(PAM) 실행", output)
        self.assertIn("harden_local_console_pam.sh", output)
        self.assertIn("--run-user", output)
        self.assertNotIn("--lock-scope", output)
        self.assertNotIn("--enable-gdm-autologin", output)
        self.assertIn("--dry-run", output)


if __name__ == "__main__":
    unittest.main()
