from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RENDER_SCRIPT = PROJECT_ROOT / "device_config" / "render_systemd_units.py"


class RenderSystemdUnitsTest(unittest.TestCase):
    def test_list_unit_names(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RENDER_SCRIPT), "--list-unit-names"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            [
                "matterhub-api.service",
                "matterhub-mqtt.service",
                "matterhub-rule-engine.service",
                "matterhub-notifier.service",
                "matterhub-support-tunnel.service",
            ],
            result.stdout.strip().splitlines(),
        )

    def test_render_creates_units_with_expected_execstart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(
                [
                    sys.executable,
                    str(RENDER_SCRIPT),
                    "--project-root",
                    "/srv/matterhub",
                    "--run-user",
                    "whatsmatter",
                    "--output-dir",
                    temp_dir,
                ],
                cwd=PROJECT_ROOT,
                check=True,
            )

            api_unit = Path(temp_dir) / "matterhub-api.service"
            mqtt_unit = Path(temp_dir) / "matterhub-mqtt.service"
            support_tunnel_unit = Path(temp_dir) / "matterhub-support-tunnel.service"

            self.assertTrue(api_unit.exists())
            self.assertTrue(mqtt_unit.exists())
            self.assertTrue(support_tunnel_unit.exists())

            api_text = api_unit.read_text(encoding="utf-8")
            mqtt_text = mqtt_unit.read_text(encoding="utf-8")
            support_tunnel_text = support_tunnel_unit.read_text(encoding="utf-8")

            self.assertIn("User=whatsmatter", api_text)
            self.assertIn("WorkingDirectory=/srv/matterhub", api_text)
            self.assertIn(
                "ExecStart=/srv/matterhub/venv/bin/python /srv/matterhub/app.py",
                api_text,
            )
            self.assertIn("NoNewPrivileges=true", api_text)
            self.assertIn("ProtectSystem=full", api_text)
            self.assertIn("CapabilityBoundingSet=", api_text)
            self.assertIn(
                "ExecStart=/srv/matterhub/venv/bin/python /srv/matterhub/mqtt.py",
                mqtt_text,
            )
            self.assertIn(
                "ExecStart=/srv/matterhub/venv/bin/python /srv/matterhub/support_tunnel.py",
                support_tunnel_text,
            )

    def test_list_enabled_unit_names_excludes_support_tunnel(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RENDER_SCRIPT), "--list-enabled-unit-names"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        enabled_units = result.stdout.strip().splitlines()
        self.assertIn("matterhub-api.service", enabled_units)
        self.assertIn("matterhub-mqtt.service", enabled_units)
        self.assertNotIn("matterhub-support-tunnel.service", enabled_units)


if __name__ == "__main__":
    unittest.main()
