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
            gdm_password = Path(temp_dir) / "gdm-password"
            gdm_autologin = Path(temp_dir) / "gdm-autologin"
            gdm_custom = Path(temp_dir) / "custom.conf"
            pam_login.write_text(
                "auth requisite pam_securetty.so\n# account required pam_access.so\n",
                encoding="utf-8",
            )
            gdm_password.write_text(
                "@include common-auth\n@include common-account\n",
                encoding="utf-8",
            )
            gdm_autologin.write_text(
                "@include common-auth\n@include common-account\n",
                encoding="utf-8",
            )
            access_conf.write_text(
                "+:root:ALL\n",
                encoding="utf-8",
            )
            gdm_custom.write_text(
                "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=whatsmatter\n",
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
                    "--gdm-password-pam",
                    str(gdm_password),
                    "--gdm-autologin-pam",
                    str(gdm_autologin),
                    "--access-conf",
                    str(access_conf),
                    "--gdm-custom-conf",
                    str(gdm_custom),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            output = result.stdout
            self.assertIn("uncommented pam_access", output)
            self.assertIn("MATTERHUB_LOCAL_CONSOLE_LOCK_BEGIN", output)
            self.assertIn("-:ALL EXCEPT root:LOCAL", output)
            self.assertIn("AutomaticLoginEnable=false", output)
            self.assertIn("systemctl mask getty@tty1.service", output)
            self.assertIn("systemctl set-default multi-user.target", output)

if __name__ == "__main__":
    unittest.main()
