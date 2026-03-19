from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "provision_relay_ec2.sh"


class ProvisionRelayEc2ScriptTest(unittest.TestCase):
    def test_dry_run_prints_expected_plan(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--dry-run"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("profile=matterhub-relay", output)
        self.assertIn("instance_type=t4g.nano", output)
        self.assertIn("plan: create or reuse security group and open tcp/443", output)
        self.assertIn("plan: allocate/associate Elastic IP", output)
        self.assertIn("hub_port_range=22000-23999", output)


if __name__ == "__main__":
    unittest.main()
