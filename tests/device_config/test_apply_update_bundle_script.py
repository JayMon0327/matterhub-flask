from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLY_SCRIPT = PROJECT_ROOT / "device_config" / "apply_update_bundle.sh"


def _create_bundle(bundle_path: Path, *, relative_path: str, content: str) -> None:
    data = content.encode("utf-8")
    with tarfile.open(bundle_path, "w:gz") as tar:
        info = tarfile.TarInfo(name=f"payload/{relative_path}")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


class ApplyUpdateBundleScriptTest(unittest.TestCase):
    def test_dry_run_prints_plan_and_does_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            target_file = project_root / "app.py"
            target_file.write_text("print('old')\n", encoding="utf-8")

            bundle_path = root / "update.tar.gz"
            _create_bundle(bundle_path, relative_path="app.py", content="print('new')\n")

            result = subprocess.run(
                [
                    "bash",
                    str(APPLY_SCRIPT),
                    "--dry-run",
                    "--bundle",
                    str(bundle_path),
                    "--project-root",
                    str(project_root),
                    "--skip-restart",
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("[matterhub-update] bundle=", result.stdout)
            self.assertIn("[dry-run] tar -xzf", result.stdout)
            self.assertIn("[dry-run] cp -a", result.stdout)
            self.assertEqual("print('old')\n", target_file.read_text(encoding="utf-8"))

    def test_apply_updates_files_when_skip_restart_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            target_file = project_root / "app.py"
            target_file.write_text("print('old')\n", encoding="utf-8")

            bundle_path = root / "update.tar.gz"
            _create_bundle(bundle_path, relative_path="app.py", content="print('new')\n")

            subprocess.run(
                [
                    "bash",
                    str(APPLY_SCRIPT),
                    "--bundle",
                    str(bundle_path),
                    "--project-root",
                    str(project_root),
                    "--skip-restart",
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual("print('new')\n", target_file.read_text(encoding="utf-8"))

    def test_healthcheck_failure_triggers_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            target_file = project_root / "app.py"
            target_file.write_text("print('old')\n", encoding="utf-8")

            bundle_path = root / "update.tar.gz"
            _create_bundle(bundle_path, relative_path="app.py", content="print('new')\n")

            result = subprocess.run(
                [
                    "bash",
                    str(APPLY_SCRIPT),
                    "--bundle",
                    str(bundle_path),
                    "--project-root",
                    str(project_root),
                    "--skip-restart",
                    "--healthcheck-cmd",
                    "exit 1",
                ],
                cwd=PROJECT_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("rollback started", result.stdout)
            self.assertEqual("print('old')\n", target_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

