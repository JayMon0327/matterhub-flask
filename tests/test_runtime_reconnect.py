"""Phase 2: 재연결 로직 강화 테스트."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

# runtime 모듈의 전역 상태를 직접 조작하여 테스트
import mqtt_pkg.runtime as runtime


class ReconnectBackoffTest(unittest.TestCase):
    """check_mqtt_connection()의 무한 재시도 + 백오프 테스트."""

    def setUp(self) -> None:
        runtime.global_mqtt_connection = None
        runtime.is_connected_flag = False
        runtime.reconnect_attempts = 0
        runtime._pending_resubscribe = False
        runtime.SUBSCRIBED_TOPICS.clear()

    def tearDown(self) -> None:
        runtime.global_mqtt_connection = None
        runtime.is_connected_flag = False
        runtime.reconnect_attempts = 0
        runtime._pending_resubscribe = False
        runtime.SUBSCRIBED_TOPICS.clear()

    def test_connected_resets_attempts(self) -> None:
        runtime.reconnect_attempts = 10
        runtime.is_connected_flag = True
        runtime.global_mqtt_connection = Mock()

        result = runtime.check_mqtt_connection([], Mock())
        self.assertTrue(result)
        self.assertEqual(runtime.reconnect_attempts, 0)

    def test_never_gives_up_after_threshold(self) -> None:
        """RECONNECT_BACKOFF_THRESHOLD 초과해도 return False가 아닌 재시도 시도."""
        runtime.reconnect_attempts = 10  # threshold(5) 초과

        mock_factory = Mock()
        mock_factory.return_value.connect_mqtt.side_effect = TimeoutError

        with patch("time.sleep"):
            result = runtime.check_mqtt_connection(
                ["topic1"], Mock(), client_factory=mock_factory
            )
        # 연결 실패이므로 False이지만, "포기"가 아닌 "이번 시도 실패"
        self.assertFalse(result)
        # 다음 호출 시에도 시도해야 함 (attempts 증가만, 포기 안함)
        self.assertEqual(runtime.reconnect_attempts, 11)

    def test_backoff_delay_applied_after_threshold(self) -> None:
        """threshold 초과 시 백오프 대기가 적용되는지 확인."""
        runtime.reconnect_attempts = 5  # 다음 호출에서 6번째 = threshold 초과

        mock_factory = Mock()
        mock_factory.return_value.connect_mqtt.side_effect = TimeoutError

        with patch("time.sleep") as mock_sleep:
            runtime.check_mqtt_connection(
                ["topic1"], Mock(), client_factory=mock_factory
            )
        # 6번째 시도: backoff_exp=1, delay=10*2^0=10초
        mock_sleep.assert_called_once_with(10)

    def test_backoff_delay_caps_at_max(self) -> None:
        """백오프 대기가 RECONNECT_MAX_DELAY를 넘지 않는지 확인."""
        runtime.reconnect_attempts = 20  # 매우 큰 값

        mock_factory = Mock()
        mock_factory.return_value.connect_mqtt.side_effect = TimeoutError

        with patch("time.sleep") as mock_sleep:
            runtime.check_mqtt_connection(
                ["topic1"], Mock(), client_factory=mock_factory
            )
        delay = mock_sleep.call_args[0][0]
        self.assertLessEqual(delay, runtime.RECONNECT_MAX_DELAY)

    def test_no_backoff_within_threshold(self) -> None:
        """threshold 이내에서는 백오프 대기 없이 즉시 재시도."""
        runtime.reconnect_attempts = 2

        mock_factory = Mock()
        mock_factory.return_value.connect_mqtt.side_effect = TimeoutError

        with patch("time.sleep") as mock_sleep:
            runtime.check_mqtt_connection(
                ["topic1"], Mock(), client_factory=mock_factory
            )
        mock_sleep.assert_not_called()


class PendingResubscribeTest(unittest.TestCase):
    """_pending_resubscribe 플래그 동작 테스트."""

    def setUp(self) -> None:
        runtime.global_mqtt_connection = Mock()
        runtime.is_connected_flag = True
        runtime.reconnect_attempts = 0
        runtime._pending_resubscribe = False

    def tearDown(self) -> None:
        runtime.global_mqtt_connection = None
        runtime.is_connected_flag = False
        runtime._pending_resubscribe = False

    def test_resubscribe_triggered_when_flag_set(self) -> None:
        """pending_resubscribe=True + connected → resubscribe 호출."""
        runtime._pending_resubscribe = True

        mock_callback = Mock()
        topics = ["topic1", "topic2"]

        with patch.object(runtime, "subscribe") as mock_sub:
            result = runtime.check_mqtt_connection(topics, mock_callback)

        self.assertTrue(result)
        self.assertFalse(runtime._pending_resubscribe)
        self.assertEqual(mock_sub.call_count, 2)

    def test_no_resubscribe_when_flag_not_set(self) -> None:
        """pending_resubscribe=False + connected → resubscribe 미호출."""
        with patch.object(runtime, "subscribe") as mock_sub:
            result = runtime.check_mqtt_connection(["topic1"], Mock())

        self.assertTrue(result)
        mock_sub.assert_not_called()


if __name__ == "__main__":
    unittest.main()
