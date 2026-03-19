from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "build_runtime_binaries.sh"


class BuildRuntimeBinariesScriptTest(unittest.TestCase):
    def test_dry_run_prints_pyinstaller_commands_for_all_services(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--dry-run",
                "--python-bin",
                "python3",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        output = result.stdout
        self.assertIn(
            "targets=matterhub-api matterhub-mqtt matterhub-rule-engine matterhub-notifier matterhub-support-tunnel matterhub-update-agent",
            output,
        )
        self.assertIn("PyInstaller", output)
        self.assertIn("--name matterhub-api", output)
        self.assertIn("--name matterhub-mqtt", output)
        self.assertIn("--name matterhub-support-tunnel", output)
        self.assertIn("--name matterhub-update-agent", output)

    def test_dry_run_can_build_single_service(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--dry-run",
                "--python-bin",
                "python3",
                "--service",
                "matterhub-mqtt",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        output = result.stdout
        self.assertIn("targets=matterhub-mqtt", output)
        self.assertIn("--name matterhub-mqtt", output)
        self.assertNotIn("--name matterhub-api", output)

    def test_unknown_service_returns_error(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--dry-run",
                "--python-bin",
                "python3",
                "--service",
                "unknown-service",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("unknown service", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
