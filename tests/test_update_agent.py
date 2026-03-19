from __future__ import annotations

import os
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from update_agent import (
    UpdateAgentConfig,
    discover_bundles,
    load_config,
    process_once,
    verify_bundle,
)


def _create_bundle(bundle_path: Path, *, bundle_type: str = "matterhub-runtime") -> None:
    payload_bytes = b"hello"
    manifest_bytes = json.dumps({"bundle_type": bundle_type}).encode("utf-8")
    with tarfile.open(bundle_path, "w:gz") as archive:
        payload_info = tarfile.TarInfo(name="payload/app.py")
        payload_info.size = len(payload_bytes)
        archive.addfile(payload_info, io.BytesIO(payload_bytes))

        manifest_info = tarfile.TarInfo(name="manifest.json")
        manifest_info.size = len(manifest_bytes)
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))


class UpdateAgentTest(unittest.TestCase):
    def test_load_config_parses_defaults_and_flags(self) -> None:
        config = load_config(
            {
                "UPDATE_AGENT_ENABLED": "1",
                "UPDATE_AGENT_PROJECT_ROOT": "/srv/matterhub",
                "UPDATE_AGENT_POLL_SECONDS": "20",
                "UPDATE_AGENT_ONCE": "1",
            }
        )
        self.assertTrue(config.enabled)
        self.assertEqual(Path("/srv/matterhub"), config.project_root)
        self.assertEqual(20, config.poll_seconds)
        self.assertTrue(config.once)

    def test_discover_bundles_returns_sorted_tar_gz_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = Path(temp_dir)
            old = inbox / "old.tar.gz"
            new = inbox / "new.tar.gz"
            other = inbox / "ignored.txt"
            old.write_text("x", encoding="utf-8")
            new.write_text("y", encoding="utf-8")
            other.write_text("z", encoding="utf-8")
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))

            bundles = discover_bundles(inbox)
            self.assertEqual(["old.tar.gz", "new.tar.gz"], [item.name for item in bundles])

    def test_process_once_moves_success_bundle_to_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            applied = root / "applied"
            failed = root / "failed"
            inbox.mkdir(parents=True)
            bundle = inbox / "bundle.tar.gz"
            _create_bundle(bundle)
            apply_script = root / "apply.sh"
            apply_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            calls: list[list[str]] = []

            def fake_runner(command):
                calls.append(list(command))
                return 0

            config = UpdateAgentConfig(
                enabled=True,
                project_root=root,
                inbox_dir=inbox,
                applied_dir=applied,
                failed_dir=failed,
                poll_seconds=10,
                apply_script=apply_script,
                healthcheck_cmd="",
                once=True,
                require_manifest=True,
                allowed_bundle_types=("matterhub-runtime",),
                require_sha256=False,
            )
            rc = process_once(config, runner=fake_runner)
            self.assertEqual(0, rc)
            self.assertEqual(1, len(calls))
            self.assertFalse(bundle.exists())
            self.assertEqual(1, len(list(applied.glob("*.tar.gz"))))
            self.assertEqual(0, len(list(failed.glob("*.tar.gz"))))

    def test_process_once_moves_failed_bundle_to_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            applied = root / "applied"
            failed = root / "failed"
            inbox.mkdir(parents=True)
            bundle = inbox / "bundle.tar.gz"
            _create_bundle(bundle)
            apply_script = root / "apply.sh"
            apply_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            config = UpdateAgentConfig(
                enabled=True,
                project_root=root,
                inbox_dir=inbox,
                applied_dir=applied,
                failed_dir=failed,
                poll_seconds=10,
                apply_script=apply_script,
                healthcheck_cmd="",
                once=True,
                require_manifest=True,
                allowed_bundle_types=("matterhub-runtime",),
                require_sha256=False,
            )
            rc = process_once(config, runner=lambda _command: 3)
            self.assertEqual(3, rc)
            self.assertFalse(bundle.exists())
            self.assertEqual(1, len(list(failed.glob("*.tar.gz"))))
            self.assertEqual(0, len(list(applied.glob("*.tar.gz"))))

    def test_verify_bundle_rejects_disallowed_bundle_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle.tar.gz"
            _create_bundle(bundle, bundle_type="unknown-type")
            config = UpdateAgentConfig(
                enabled=True,
                project_root=root,
                inbox_dir=root,
                applied_dir=root / "applied",
                failed_dir=root / "failed",
                poll_seconds=10,
                apply_script=root / "apply.sh",
                healthcheck_cmd="",
                once=True,
                require_manifest=True,
                allowed_bundle_types=("matterhub-runtime",),
                require_sha256=False,
            )
            verified, reason = verify_bundle(bundle, config)
            self.assertFalse(verified)
            self.assertEqual("bundle_type_not_allowed", reason)

    def test_process_once_moves_invalid_bundle_to_failed_without_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir(parents=True)
            bundle = inbox / "bundle.tar.gz"
            _create_bundle(bundle, bundle_type="invalid")
            apply_script = root / "apply.sh"
            apply_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            called = {"count": 0}

            def fake_runner(_command):
                called["count"] += 1
                return 0

            config = UpdateAgentConfig(
                enabled=True,
                project_root=root,
                inbox_dir=inbox,
                applied_dir=root / "applied",
                failed_dir=root / "failed",
                poll_seconds=10,
                apply_script=apply_script,
                healthcheck_cmd="",
                once=True,
                require_manifest=True,
                allowed_bundle_types=("matterhub-runtime",),
                require_sha256=False,
            )
            rc = process_once(config, runner=fake_runner)
            self.assertEqual(4, rc)
            self.assertEqual(0, called["count"])
            self.assertEqual(1, len(list((root / "failed").glob("*.tar.gz"))))


if __name__ == "__main__":
    unittest.main()
