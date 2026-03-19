"""mqtt_pkg/update.py handle_update_command 재활성화 검증 테스트."""

import unittest
from unittest.mock import patch, MagicMock


class HandleUpdateCommandTest(unittest.TestCase):
    """handle_update_command가 큐에 enqueue하는지 검증"""

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


if __name__ == "__main__":
    unittest.main()
