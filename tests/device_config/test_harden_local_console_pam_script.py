from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "harden_local_console_pam.sh"


class HardenLocalConsolePamScriptTest(unittest.TestCase):
    def test_dry_run_prints_login_and_access_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pam_login = Path(temp_dir) / "login"
            access_conf = Path(temp_dir) / "access.conf"
            pam_login.write_text(
                "auth requisite pam_securetty.so\n# account required pam_access.so\n",
                encoding="utf-8",
            )
            access_conf.write_text(
                "+:root:ALL\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--dry-run",
                    "--run-user",
                    "whatsmatter",
                    "--pam-login-path",
                    str(pam_login),
                    "--access-conf",
                    str(access_conf),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            output = result.stdout
            self.assertIn("uncommented pam_access", output)
            self.assertIn("MATTERHUB_LOCAL_CONSOLE_LOCK_BEGIN", output)
            self.assertIn("-:whatsmatter:LOCAL", output)


if __name__ == "__main__":
    unittest.main()
