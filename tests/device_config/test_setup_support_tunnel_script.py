from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = PROJECT_ROOT / "device_config" / "setup_support_tunnel.sh"


class SetupSupportTunnelScriptTest(unittest.TestCase):
    def test_dry_run_derives_remote_port_and_prints_connect_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text('matterhub_id="whatsmatter-nipa_SN-1770784749"\n', encoding="utf-8")

            env = os.environ.copy()
            for key in list(env.keys()):
                if key.startswith("SUPPORT_TUNNEL_"):
                    env.pop(key, None)
            env["PYTHON_BIN"] = sys.executable
            env["RUN_USER"] = "whatsmatter"

            result = subprocess.run(
                [
                    "bash",
                    str(SETUP_SCRIPT),
                    "--dry-run",
                    "--env-file",
                    str(env_file),
                    "--host",
                    "support.whatsmatter.local",
                    "--user",
                    "whatsmatter",
                    "--device-user",
                    "whatsmatter",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            output = result.stdout
            self.assertIn("remote port not specified; derived from matterhub_id", output)
            self.assertIn("env update: SUPPORT_TUNNEL_HOST=support.whatsmatter.local", output)
            self.assertIn("env update: SUPPORT_TUNNEL_RELAY_OPERATOR_USER=ec2-user", output)
            self.assertIn("permitlisten=\"127.0.0.1:", output)
            self.assertIn(
                "ssh -i <relay-operator-key.pem> -p 443 ec2-user@support.whatsmatter.local",
                output,
            )
            self.assertIn(
                "j whatsmatter-nipa_SN-1770784749",
                output,
            )
            self.assertIn("ProxyCommand='ssh -i <relay-operator-key.pem>", output)
            self.assertIn("whatsmatter@127.0.0.1", output)

    def test_dry_run_with_relay_access_pubkey_reports_append_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text('matterhub_id="hub-1"\n', encoding="utf-8")

            env = os.environ.copy()
            for key in list(env.keys()):
                if key.startswith("SUPPORT_TUNNEL_"):
                    env.pop(key, None)
            env["PYTHON_BIN"] = sys.executable
            env["RUN_USER"] = "whatsmatter"

            result = subprocess.run(
                [
                    "bash",
                    str(SETUP_SCRIPT),
                    "--dry-run",
                    "--env-file",
                    str(env_file),
                    "--host",
                    "3.38.126.167",
                    "--user",
                    "whatsmatter",
                    "--remote-port",
                    "22608",
                    "--relay-access-pubkey",
                    "ssh-ed25519 AAAATEST relay-hub-access@test",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            output = result.stdout
            self.assertIn("relay access pubkey will be appended", output)

    def test_dry_run_enable_now_prints_enable_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("", encoding="utf-8")

            env = os.environ.copy()
            for key in list(env.keys()):
                if key.startswith("SUPPORT_TUNNEL_"):
                    env.pop(key, None)
            env["PYTHON_BIN"] = sys.executable

            result = subprocess.run(
                [
                    "bash",
                    str(SETUP_SCRIPT),
                    "--dry-run",
                    "--env-file",
                    str(env_file),
                    "--host",
                    "support.whatsmatter.local",
                    "--user",
                    "whatsmatter",
                    "--remote-port",
                    "22321",
                    "--enable-now",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            output = result.stdout
            self.assertIn(
                "[dry-run] sudo systemctl enable --now matterhub-support-tunnel.service",
                output,
            )
            self.assertIn("SUPPORT_TUNNEL_REMOTE_PORT=22321", output)


if __name__ == "__main__":
    unittest.main()
