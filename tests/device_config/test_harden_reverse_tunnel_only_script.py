from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "harden_reverse_tunnel_only.sh"


class HardenReverseTunnelOnlyScriptTest(unittest.TestCase):
    def test_dry_run_prints_sshd_and_ufw_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "SUPPORT_TUNNEL_ENABLED=1",
                        "SUPPORT_TUNNEL_HOST=3.38.126.167",
                        "SUPPORT_TUNNEL_USER=whatsmatter",
                        "SUPPORT_TUNNEL_REMOTE_PORT=22608",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--dry-run",
                    "--env-file",
                    str(env_file),
                    "--run-user",
                    "whatsmatter",
                    "--allow-inbound-port",
                    "80",
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            output = result.stdout
            self.assertIn("support tunnel config validated", output)
            self.assertIn("ListenAddress 127.0.0.1", output)
            self.assertIn("AllowUsers whatsmatter", output)
            self.assertIn("ufw --force reset", output)
            self.assertIn("ufw allow 80/tcp", output)

    def test_requires_action_when_both_areas_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "SUPPORT_TUNNEL_ENABLED=1\nSUPPORT_TUNNEL_HOST=x\nSUPPORT_TUNNEL_USER=y\nSUPPORT_TUNNEL_REMOTE_PORT=1\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--dry-run",
                    "--env-file",
                    str(env_file),
                    "--skip-ufw",
                    "--skip-sshd",
                ],
                cwd=PROJECT_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("Nothing to do", result.stderr)


if __name__ == "__main__":
    unittest.main()
