from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "install_runtime_bundle.sh"


class InstallRuntimeBundleScriptTest(unittest.TestCase):
    def test_dry_run_prints_apply_and_systemd_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle_path = root / "runtime.tar.gz"
            bundle_path.write_text("dummy", encoding="utf-8")
            runtime_root = root / "opt" / "matterhub"

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--dry-run",
                    "--bundle",
                    str(bundle_path),
                    "--runtime-root",
                    str(runtime_root),
                    "--run-user",
                    "whatsmatter",
                    "--python-bin",
                    "python3",
                    "--systemd-dir",
                    str(root / "systemd"),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            output = result.stdout
            self.assertIn("apply_update_bundle.sh", output)
            self.assertIn("--skip-restart", output)
            self.assertIn("render_systemd_units.py", output)
            self.assertIn("--runtime-mode binary", output)
            self.assertIn("systemctl daemon-reload", output)
            self.assertIn("systemctl enable", output)
            self.assertIn("matterhub-update-agent.service", output)
            self.assertNotIn("matterhub-support-tunnel.service", output.split("systemctl enable")[-1])


if __name__ == "__main__":
    unittest.main()

