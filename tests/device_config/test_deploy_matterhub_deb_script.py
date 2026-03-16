from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "deploy_matterhub_deb.sh"


class DeployMatterhubDebScriptTest(unittest.TestCase):
    def test_dry_run_uses_explicit_artifact_and_remote_install_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "matterhub_test_arm64.deb"
            artifact.write_text("deb", encoding="utf-8")

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--dry-run",
                    "--host",
                    "192.168.1.96",
                    "--user",
                    "whatsmatter",
                    "--artifact",
                    str(artifact),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            output = result.stdout
            self.assertIn("host=192.168.1.96", output)
            self.assertIn(f"artifact={artifact}", output)
            self.assertIn("[dry-run] ssh -o StrictHostKeyChecking=no -p 22 whatsmatter@192.168.1.96 mkdir\\ -p", output)
            self.assertIn("[dry-run] scp -o StrictHostKeyChecking=no -P 22", output)
            self.assertIn("[dry-run] ssh -o StrictHostKeyChecking=no -p 22 whatsmatter@192.168.1.96 -tt", output)
            self.assertIn("sudo\\ apt-get\\ install\\ -y", output)


if __name__ == "__main__":
    unittest.main()
