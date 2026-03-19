from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "device_config" / "setup_wifi_regulatory_domain.sh"


class SetupWifiRegulatoryDomainScriptTest(unittest.TestCase):
    def test_dry_run_shows_country_code_actions(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), "--dry-run", "--country-code", "KR"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("country_code=KR", result.stdout)
        self.assertIn("cfg80211.ieee80211_regdom=KR", result.stdout)
        self.assertIn("NetworkManager", result.stdout)

    def test_rejects_invalid_country_code(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), "--dry-run", "--country-code", "KOR"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("country code", result.stderr)


if __name__ == "__main__":
    unittest.main()
