from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "build_matterhub_deb.sh"


class BuildMatterhubDebScriptTest(unittest.TestCase):
    def test_dry_run_prints_expected_plan(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--dry-run",
                "--version",
                "1.2.3",
                "--output-dir",
                "/tmp/matterhub-dist",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("package_name=matterhub", output)
        self.assertIn("version=1.2.3", output)
        self.assertIn("mode=source", output)
        self.assertIn("output_file=/tmp/matterhub-dist/matterhub_1.2.3_arm64.deb", output)
        self.assertIn("package python payload as source files (mode=source)", output)
        self.assertIn("include update-agent in package payload", output)
        self.assertIn("include claim and Konai certificate assets plus provisioning launcher", output)
        self.assertIn("default env with Wi-Fi automation/AP disabled", output)
        self.assertIn("bootstrap claim provisioning via systemd", output)
        self.assertIn("auto-configure and enable support tunnel after matterhub_id is available, including reboot retries", output)
        self.assertIn("persistent UFW allow rules for 8100/tcp and 8123/tcp", output)
        self.assertIn("plan: run dpkg-deb --build", output)


if __name__ == "__main__":
    unittest.main()
