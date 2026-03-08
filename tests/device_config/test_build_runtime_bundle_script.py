from __future__ import annotations

import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "build_runtime_bundle.sh"

SERVICES = [
    "matterhub-api",
    "matterhub-mqtt",
    "matterhub-rule-engine",
    "matterhub-notifier",
    "matterhub-support-tunnel",
    "matterhub-update-agent",
]


class BuildRuntimeBundleScriptTest(unittest.TestCase):
    def test_dry_run_prints_bundle_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist_dir = root / "dist" / "bin"
            for service in SERVICES:
                target = dist_dir / service
                target.mkdir(parents=True, exist_ok=True)
                (target / service).write_text("binary", encoding="utf-8")

            output_bundle = root / "runtime.tar.gz"
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--dry-run",
                    "--binary-dist-dir",
                    str(dist_dir),
                    "--output-bundle",
                    str(output_bundle),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            output = result.stdout
            self.assertIn("[matterhub-runtime-bundle] binary_dist_dir=", output)
            self.assertIn("[dry-run] cp -a", output)
            self.assertIn("[dry-run] tar -czf", output)

    def test_build_creates_runtime_bundle_with_payload_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist_dir = root / "dist" / "bin"
            for service in SERVICES:
                target = dist_dir / service
                target.mkdir(parents=True, exist_ok=True)
                (target / service).write_text("binary", encoding="utf-8")

            output_bundle = root / "runtime.tar.gz"
            subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--binary-dist-dir",
                    str(dist_dir),
                    "--output-bundle",
                    str(output_bundle),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue(output_bundle.exists())
            with tarfile.open(output_bundle, "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("payload/bin/matterhub-api/matterhub-api", names)
            self.assertIn("payload/bin/matterhub-update-agent/matterhub-update-agent", names)
            self.assertIn("manifest.json", names)


if __name__ == "__main__":
    unittest.main()

