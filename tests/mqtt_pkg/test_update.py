"""mqtt_pkg/update.py handle_update_command 재활성화 검증 테스트."""

import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _ensure_real_mqtt_pkg():
    """Ensure the real mqtt_pkg (not tests/mqtt_pkg) is importable."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # If mqtt_pkg in sys.modules points to tests/mqtt_pkg, clear it
    pkg = sys.modules.get("mqtt_pkg")
    if pkg and hasattr(pkg, "__file__") and pkg.__file__ and "tests" in pkg.__file__:
        for mod_name in list(sys.modules):
            if mod_name == "mqtt_pkg" or mod_name.startswith("mqtt_pkg."):
                del sys.modules[mod_name]


class HandleUpdateCommandTest(unittest.TestCase):
    """handle_update_command가 큐에 enqueue하는지 검증"""

    @classmethod
    def setUpClass(cls):
        _ensure_real_mqtt_pkg()

    @patch("mqtt_pkg.update.runtime")
    @patch("mqtt_pkg.update.settings")
    def test_enqueues_message(self, mock_settings, mock_runtime):
        mock_settings.MATTERHUB_ID = "test-hub"
        from mqtt_pkg.update import handle_update_command, update_queue

        # 큐 비우기
        while not update_queue.empty():
            update_queue.get_nowait()

        message = {
            "command": "git_update",
            "update_id": "test-update-001",
            "branch": "master",
        }

        handle_update_command(message)

        # 큐에 메시지가 들어가야 함
        self.assertFalse(update_queue.empty())
        queued = update_queue.get_nowait()
        self.assertEqual(queued["update_id"], "test-update-001")

    @patch("mqtt_pkg.update.runtime")
    @patch("mqtt_pkg.update.settings")
    def test_enqueues_all_commands(self, mock_settings, mock_runtime):
        """set_env, bundle_update, bundle_check도 큐에 들어가야 함"""
        mock_settings.MATTERHUB_ID = "test-hub"
        from mqtt_pkg.update import handle_update_command, update_queue

        # 큐 비우기
        while not update_queue.empty():
            update_queue.get_nowait()

        for cmd in ["set_env", "bundle_update", "bundle_check", "git_update"]:
            handle_update_command({"command": cmd, "update_id": f"{cmd}-001"})

        self.assertEqual(update_queue.qsize(), 4)


class SetEnvCommandTest(unittest.TestCase):
    """set_env 명령 처리 검증"""

    @classmethod
    def setUpClass(cls):
        _ensure_real_mqtt_pkg()

    @patch("mqtt_pkg.update._launch_restart")
    @patch("mqtt_pkg.update.send_final_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_updates_allowed_key(self, mock_runtime, mock_settings, mock_send_final, mock_restart):
        mock_settings.MATTERHUB_ID = "test-hub"
        mock_settings.MATTERHUB_REGION = None
        mock_settings._persist_env_value = MagicMock()

        from mqtt_pkg.update import _handle_set_env

        message = {
            "command": "set_env",
            "update_id": "region-001",
            "key": "MATTERHUB_REGION",
            "value": "gangnam",
        }

        _handle_set_env(message)

        mock_settings._persist_env_value.assert_called_once_with("MATTERHUB_REGION", '"gangnam"')
        mock_send_final.assert_called_once()
        result = mock_send_final.call_args[0][1]
        self.assertTrue(result["success"])
        self.assertEqual(mock_settings.MATTERHUB_REGION, "gangnam")

    @patch("mqtt_pkg.update._launch_restart")
    @patch("mqtt_pkg.update.send_final_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_with_restart(self, mock_runtime, mock_settings, mock_send_final, mock_restart):
        mock_settings.MATTERHUB_ID = "test-hub"
        mock_settings.MATTERHUB_REGION = None
        mock_settings._persist_env_value = MagicMock()

        from mqtt_pkg.update import _handle_set_env

        message = {
            "command": "set_env",
            "update_id": "region-002",
            "key": "MATTERHUB_REGION",
            "value": "gangnam",
            "restart": True,
        }

        _handle_set_env(message)

        result = mock_send_final.call_args[0][1]
        self.assertTrue(result["restart"])
        mock_restart.assert_called_once_with("region-002")

    @patch("mqtt_pkg.update.send_error_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_rejects_disallowed_key(self, mock_runtime, mock_settings, mock_send_err):
        mock_settings.MATTERHUB_ID = "test-hub"

        from mqtt_pkg.update import _handle_set_env

        message = {
            "command": "set_env",
            "update_id": "bad-001",
            "key": "hass_token",
            "value": "stolen",
        }

        _handle_set_env(message)

        mock_send_err.assert_called_once()
        self.assertIn("not allowed", mock_send_err.call_args[0][1])

    @patch("mqtt_pkg.update.send_error_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_rejects_empty_key(self, mock_runtime, mock_settings, mock_send_err):
        mock_settings.MATTERHUB_ID = "test-hub"

        from mqtt_pkg.update import _handle_set_env

        message = {
            "command": "set_env",
            "update_id": "empty-001",
            "key": "",
            "value": "gangnam",
        }

        _handle_set_env(message)

        mock_send_err.assert_called_once()
        self.assertIn("required", mock_send_err.call_args[0][1])


class BundleUpdateCommandTest(unittest.TestCase):
    """bundle_update 명령 처리 검증"""

    @classmethod
    def setUpClass(cls):
        _ensure_real_mqtt_pkg()

    @patch("mqtt_pkg.update.send_final_response")
    @patch("mqtt_pkg.update.send_immediate_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_bundle_update_downloads_and_responds(
        self, mock_runtime, mock_settings, mock_send_imm, mock_send_final
    ):
        mock_settings.MATTERHUB_ID = "test-hub"

        mock_ua = MagicMock()
        mock_config = MagicMock()
        mock_config.inbox_dir = Path("/tmp/fake-inbox")
        mock_dest = MagicMock()
        mock_dest.name = "bundle-v1.2.tar.gz"
        mock_dest.stat.return_value.st_size = 12345

        mock_ua.load_config.return_value = mock_config
        mock_ua.download_bundle.return_value = mock_dest
        mock_ua.list_inbox.return_value = [{"name": "bundle-v1.2.tar.gz", "size": 12345, "mtime": 1000}]

        with patch.dict("sys.modules", {"update_agent": mock_ua}):
            message = {
                "command": "bundle_update",
                "update_id": "bundle-001",
                "url": "https://s3.example.com/bundle-v1.2.tar.gz",
                "sha256": "abc123",
            }

            from mqtt_pkg.update import _handle_bundle_update
            _handle_bundle_update(message)

            mock_send_imm.assert_called_once_with(message, status="downloading")
            mock_send_final.assert_called_once()
            result = mock_send_final.call_args[0][1]
            self.assertTrue(result["success"])
            self.assertEqual("bundle-v1.2.tar.gz", result["bundle_name"])
            self.assertEqual(1, result["inbox_pending"])

    @patch("mqtt_pkg.update.send_error_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_bundle_update_rejects_missing_url(
        self, mock_runtime, mock_settings, mock_send_err
    ):
        mock_settings.MATTERHUB_ID = "test-hub"

        from mqtt_pkg.update import _handle_bundle_update

        message = {
            "command": "bundle_update",
            "update_id": "bundle-002",
        }

        _handle_bundle_update(message)

        mock_send_err.assert_called_once()
        self.assertIn("url is required", mock_send_err.call_args[0][1])


class BundleCheckCommandTest(unittest.TestCase):
    """bundle_check 명령 처리 검증"""

    @classmethod
    def setUpClass(cls):
        _ensure_real_mqtt_pkg()

    @patch("mqtt_pkg.update.send_final_response")
    @patch("mqtt_pkg.update.send_immediate_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_bundle_check_returns_inbox_status(
        self, mock_runtime, mock_settings, mock_send_imm, mock_send_final
    ):
        mock_settings.MATTERHUB_ID = "test-hub"

        mock_ua = MagicMock()
        mock_config = MagicMock()
        mock_config.inbox_dir = Path("/tmp/fake-inbox")
        fake_bundles = [
            {"name": "a.tar.gz", "size": 100, "mtime": 1000},
            {"name": "b.tar.gz", "size": 200, "mtime": 2000},
        ]

        mock_ua.load_config.return_value = mock_config
        mock_ua.list_inbox.return_value = fake_bundles

        with patch.dict("sys.modules", {"update_agent": mock_ua}):
            message = {
                "command": "bundle_check",
                "update_id": "check-001",
            }

            from mqtt_pkg.update import _handle_bundle_check
            _handle_bundle_check(message)

            mock_send_imm.assert_called_once_with(message, status="processing")
            mock_send_final.assert_called_once()
            result = mock_send_final.call_args[0][1]
            self.assertTrue(result["success"])
            self.assertEqual(2, result["inbox_pending"])
            self.assertEqual(2, len(result["bundles"]))


class SetEnvRegionAutoRestartTest(unittest.TestCase):
    """MATTERHUB_REGION 변경 시 자동 재시작 검증"""

    @classmethod
    def setUpClass(cls):
        _ensure_real_mqtt_pkg()

    @patch("mqtt_pkg.update._launch_restart")
    @patch("mqtt_pkg.update.send_final_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_region_forces_restart(self, mock_runtime, mock_settings, mock_send_final, mock_restart):
        """MATTERHUB_REGION 변경 시 restart=False여도 _launch_restart 호출"""
        mock_settings.MATTERHUB_ID = "test-hub"
        mock_settings.MATTERHUB_REGION = None
        mock_settings._persist_env_value = MagicMock()

        from mqtt_pkg.update import _handle_set_env

        message = {
            "command": "set_env",
            "update_id": "region-auto-001",
            "key": "MATTERHUB_REGION",
            "value": "gangnam",
            # restart 없음 (기본값 False)
        }

        _handle_set_env(message)

        mock_restart.assert_called_once_with("region-auto-001")

    @patch("mqtt_pkg.update._launch_restart")
    @patch("mqtt_pkg.update.send_final_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_non_region_key_no_forced_restart(self, mock_runtime, mock_settings, mock_send_final, mock_restart):
        """MATTERHUB_REGION 외 키 변경 시 restart 강제 안됨"""
        mock_settings.MATTERHUB_ID = "test-hub"
        mock_settings._persist_env_value = MagicMock()

        from mqtt_pkg.update import _handle_set_env

        message = {
            "command": "set_env",
            "update_id": "throttle-001",
            "key": "MQTT_EVENT_THROTTLE_SEC",
            "value": "10",
            # restart 없음
        }

        _handle_set_env(message)

        mock_restart.assert_not_called()


class ResponsePhaseFieldTest(unittest.TestCase):
    """응답 메시지에 phase 필드 포함 검증"""

    @classmethod
    def setUpClass(cls):
        _ensure_real_mqtt_pkg()

    @patch("mqtt_pkg.update._publish_response")
    @patch("mqtt_pkg.update.settings")
    def test_immediate_response_has_ack_phase(self, mock_settings, mock_publish):
        mock_settings.MATTERHUB_ID = "test-hub"
        from mqtt_pkg.update import send_immediate_response

        send_immediate_response({"update_id": "test-001", "command": "git_update"}, status="processing")

        payload = mock_publish.call_args[0][0]
        self.assertEqual(payload["phase"], "ack")
        self.assertEqual(payload["status"], "processing")

    @patch("mqtt_pkg.update._publish_response")
    @patch("mqtt_pkg.update.settings")
    def test_final_response_has_result_phase(self, mock_settings, mock_publish):
        mock_settings.MATTERHUB_ID = "test-hub"
        from mqtt_pkg.update import send_final_response

        send_final_response(
            {"update_id": "test-002", "command": "git_update"},
            {"success": True},
        )

        payload = mock_publish.call_args[0][0]
        self.assertEqual(payload["phase"], "result")
        self.assertEqual(payload["status"], "success")

    @patch("mqtt_pkg.update._publish_response")
    @patch("mqtt_pkg.update.settings")
    def test_error_response_has_result_phase(self, mock_settings, mock_publish):
        mock_settings.MATTERHUB_ID = "test-hub"
        from mqtt_pkg.update import send_error_response

        send_error_response({"update_id": "test-003", "command": "git_update"}, "something broke")

        payload = mock_publish.call_args[0][0]
        self.assertEqual(payload["phase"], "result")
        self.assertEqual(payload["status"], "failed")


if __name__ == "__main__":
    unittest.main()
