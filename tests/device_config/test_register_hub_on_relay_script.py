from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "register_hub_on_relay.sh"


class RegisterHubOnRelayScriptTest(unittest.TestCase):
    def test_dry_run_builds_authorized_key_and_ssh_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pubkey_path = Path(temp_dir) / "hub.pub"
            relay_key_path = Path(temp_dir) / "relay.pem"
            pubkey_path.write_text(
                "ssh-ed25519 AAAAC3NzaFakeKeyForTest matterhub-support-tunnel@test\n",
                encoding="utf-8",
            )
            relay_key_path.write_text("dummy", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--dry-run",
                    "--relay-host",
                    "3.38.126.167",
                    "--relay-user",
                    "ec2-user",
                    "--relay-key",
                    str(relay_key_path),
                    "--hub-id",
                    "whatsmatter-nipa_SN-1770784749",
                    "--remote-port",
                    "22608",
                    "--device-user",
                    "whatsmatter",
                    "--hub-pubkey",
                    str(pubkey_path),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            output = result.stdout
            self.assertIn('permitlisten="127.0.0.1:22608"', output)
            self.assertIn("hub_id=whatsmatter-nipa_SN-1770784749", output)
            self.assertIn("ec2-user@3.38.126.167", output)


if __name__ == "__main__":
    unittest.main()
