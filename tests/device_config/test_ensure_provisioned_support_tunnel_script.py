from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "device_config" / "ensure_provisioned_support_tunnel.sh"


class EnsureProvisionedSupportTunnelScriptTest(unittest.TestCase):
    def test_bootstrap_runs_support_tunnel_setup_after_provisioning_creates_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_file = root / "matterhub.env"
            env_file.write_text("", encoding="utf-8")
            marker = root / "setup-called.txt"

            provision_bin = root / "matterhub-provision"
            provision_bin.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f"printf '%s\\n' 'matterhub_id=\"hub-77\"' >> {env_file}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            provision_bin.chmod(0o755)

            tunnel_setup = root / "setup_support_tunnel.sh"
            tunnel_setup.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f"printf '%s\\n' \"$*\" > {marker}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            tunnel_setup.chmod(0o755)

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                ],
                cwd=PROJECT_ROOT,
                env={
                    "RUN_USER": "matterhub",
                    "ENV_FILE": str(env_file),
                    "PROVISION_BIN": str(provision_bin),
                    "SUPPORT_TUNNEL_SETUP_SCRIPT": str(tunnel_setup),
                },
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("matterhub_id available: hub-77", result.stdout)
            self.assertTrue(marker.exists())
            called_args = marker.read_text(encoding="utf-8")
            self.assertIn("--env-file", called_args)
            self.assertIn(str(env_file), called_args)
            self.assertIn("--run-user matterhub", called_args)
            self.assertIn("--skip-install-unit", called_args)
            self.assertIn("--enable-now", called_args)

    def test_bootstrap_runs_provision_as_run_user_when_invoked_as_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_file = root / "matterhub.env"
            env_file.write_text("", encoding="utf-8")
            marker = root / "setup-called.txt"
            helper_marker = root / "run-as-user.txt"

            provision_bin = root / "matterhub-provision"
            provision_bin.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f"printf '%s\\n' 'matterhub_id=\"hub-root\"' >> {env_file}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            provision_bin.chmod(0o755)

            run_as_user_helper = root / "run-as-user-helper.sh"
            run_as_user_helper.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f"printf '%s\\n' \"$1\" > {helper_marker}",
                        "shift",
                        "exec \"$@\"",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            run_as_user_helper.chmod(0o755)

            tunnel_setup = root / "setup_support_tunnel.sh"
            tunnel_setup.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f"printf '%s\\n' \"$*\" > {marker}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            tunnel_setup.chmod(0o755)

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                ],
                cwd=PROJECT_ROOT,
                env={
                    "CURRENT_UID": "0",
                    "PROVISION_AS_RUN_USER": "1",
                    "RUN_USER": "matterhub",
                    "ENV_FILE": str(env_file),
                    "PROVISION_BIN": str(provision_bin),
                    "RUN_AS_USER_HELPER": str(run_as_user_helper),
                    "SUPPORT_TUNNEL_SETUP_SCRIPT": str(tunnel_setup),
                },
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("running provisioning as matterhub", result.stdout)
            self.assertEqual("matterhub\n", helper_marker.read_text(encoding="utf-8"))
            self.assertTrue(marker.exists())

    def test_bootstrap_skips_support_tunnel_when_id_is_still_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_file = root / "matterhub.env"
            env_file.write_text("", encoding="utf-8")
            marker = root / "setup-called.txt"

            provision_bin = root / "matterhub-provision"
            provision_bin.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n",
                encoding="utf-8",
            )
            provision_bin.chmod(0o755)

            tunnel_setup = root / "setup_support_tunnel.sh"
            tunnel_setup.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f"printf '%s\\n' called > {marker}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            tunnel_setup.chmod(0o755)

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                ],
                cwd=PROJECT_ROOT,
                env={
                    "RUN_USER": "matterhub",
                    "ENV_FILE": str(env_file),
                    "PROVISION_BIN": str(provision_bin),
                    "SUPPORT_TUNNEL_SETUP_SCRIPT": str(tunnel_setup),
                },
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("support tunnel setup deferred", result.stdout)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
