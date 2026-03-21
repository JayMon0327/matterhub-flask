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

    @patch("mqtt_pkg.update.send_immediate_response")
    @patch("mqtt_pkg.update.runtime")
    @patch("mqtt_pkg.update.settings")
    def test_enqueues_message(self, mock_settings, mock_runtime, mock_send_imm):
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

        # immediate response가 호출되어야 함
        mock_send_imm.assert_called_once_with(message, status="processing")

        # 큐에 메시지가 들어가야 함
        self.assertFalse(update_queue.empty())
        queued = update_queue.get_nowait()
        self.assertEqual(queued["update_id"], "test-update-001")

    @patch("mqtt_pkg.update.send_error_response")
    @patch("mqtt_pkg.update.send_immediate_response", side_effect=Exception("boom"))
    @patch("mqtt_pkg.update.runtime")
    @patch("mqtt_pkg.update.settings")
    def test_sends_error_on_exception(
        self, mock_settings, mock_runtime, mock_send_imm, mock_send_err
    ):
        mock_settings.MATTERHUB_ID = "test-hub"
        from mqtt_pkg.update import handle_update_command

        message = {"command": "git_update", "update_id": "fail-001"}

        handle_update_command(message)

        mock_send_err.assert_called_once()
        args = mock_send_err.call_args
        self.assertEqual(args[0][0], message)
        self.assertIn("boom", args[0][1])


class SetEnvCommandTest(unittest.TestCase):
    """set_env 명령 처리 검증"""

    @classmethod
    def setUpClass(cls):
        _ensure_real_mqtt_pkg()

    @patch("mqtt_pkg.update.send_final_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_updates_allowed_key(self, mock_runtime, mock_settings, mock_send_final):
        mock_settings.MATTERHUB_ID = "test-hub"
        mock_settings.MATTERHUB_REGION = None
        mock_settings._persist_env_value = MagicMock()

        from mqtt_pkg.update import handle_update_command

        message = {
            "command": "set_env",
            "update_id": "region-001",
            "key": "MATTERHUB_REGION",
            "value": "gangnam",
        }

        handle_update_command(message)

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

        from mqtt_pkg.update import handle_update_command

        message = {
            "command": "set_env",
            "update_id": "region-002",
            "key": "MATTERHUB_REGION",
            "value": "gangnam",
            "restart": True,
        }

        handle_update_command(message)

        result = mock_send_final.call_args[0][1]
        self.assertTrue(result["restart"])
        mock_restart.assert_called_once_with("region-002")

    @patch("mqtt_pkg.update.send_error_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_rejects_disallowed_key(self, mock_runtime, mock_settings, mock_send_err):
        mock_settings.MATTERHUB_ID = "test-hub"

        from mqtt_pkg.update import handle_update_command

        message = {
            "command": "set_env",
            "update_id": "bad-001",
            "key": "hass_token",
            "value": "stolen",
        }

        handle_update_command(message)

        mock_send_err.assert_called_once()
        self.assertIn("not allowed", mock_send_err.call_args[0][1])

    @patch("mqtt_pkg.update.send_error_response")
    @patch("mqtt_pkg.update.settings")
    @patch("mqtt_pkg.update.runtime")
    def test_set_env_rejects_empty_key(self, mock_runtime, mock_settings, mock_send_err):
        mock_settings.MATTERHUB_ID = "test-hub"

        from mqtt_pkg.update import handle_update_command

        message = {
            "command": "set_env",
            "update_id": "empty-001",
            "key": "",
            "value": "gangnam",
        }

        handle_update_command(message)

        mock_send_err.assert_called_once()
        self.assertIn("required", mock_send_err.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
