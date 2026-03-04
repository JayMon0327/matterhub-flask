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
        self.assertIn("python3-venv python3-pip network-manager", output)
        self.assertIn("matterhub-api.service matterhub-mqtt.service", output)
        self.assertIn("systemctl daemon-reload", output)
        self.assertIn("systemctl enable", output)
        self.assertIn("render_systemd_units.py", output)

    def test_dry_run_can_skip_os_packages(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), "--dry-run", "--skip-os-packages"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("OS 패키지 설치 단계 생략", result.stdout)


if __name__ == "__main__":
    unittest.main()
