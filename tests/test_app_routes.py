"""app.py 라우트 검증 테스트.

Phase 1: 데드코드 로그/히스토리 라우트 제거 확인
Phase 4: Postman API 기준 엣지 엔드포인트 검증
"""

import importlib
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _load_app_module():
    """app 모듈을 격리된 환경에서 로드한다.

    app.py는 모듈 수준에서 threading, HA 연결, 스케줄러 등을 시작하므로
    이를 모두 mock한 뒤 import한다.
    """
    env_vars = {
        "res_file_path": "/tmp/test_res",
        "cert_file_path": "/tmp/test_cert",
        "schedules_file_path": "/tmp/test_schedules.json",
        "rules_file_path": "/tmp/test_rules.json",
        "rooms_file_path": "/tmp/test_rooms.json",
        "devices_file_path": "/tmp/test_devices.json",
        "notifications_file_path": "/tmp/test_notifications.json",
        "HA_host": "http://localhost:8123",
        "hass_token": "test_token",
        "ALLOWED_MACS": "",
    }

    patches = []

    # mock device_binding
    mock_binding = types.ModuleType("libs.device_binding")
    mock_binding.enforce_mac_binding = MagicMock(return_value=True)

    # mock wifi modules
    mock_wifi_api = types.ModuleType("wifi_config.api")
    mock_wifi_api.create_wifi_blueprint = MagicMock(
        return_value=MagicMock(name="wifi_bp")
    )
    mock_wifi_bootstrap = types.ModuleType("wifi_config.bootstrap")
    mock_wifi_bootstrap.ensure_bootstrap_ap = MagicMock(
        return_value={"reason": "test", "started": False}
    )
    mock_wifi_bootstrap.watch_disconnection_and_start_ap = MagicMock()

    # mock scheduler
    mock_scheduler = types.ModuleType("sub.scheduler")
    mock_scheduler.one_time_schedule = MagicMock(return_value=MagicMock())
    mock_scheduler.schedule_config = MagicMock()
    mock_scheduler.periodic_scheduler = MagicMock()
    mock_scheduler.one_time_scheduler = MagicMock()

    mock_rule = types.ModuleType("sub.ruleEngine")

    sys.modules["libs.device_binding"] = mock_binding
    sys.modules["wifi_config.api"] = mock_wifi_api
    sys.modules["wifi_config.bootstrap"] = mock_wifi_bootstrap
    sys.modules["sub.scheduler"] = mock_scheduler
    sys.modules["sub.ruleEngine"] = mock_rule

    # mock threading to prevent actual threads
    p_thread = patch("threading.Thread", return_value=MagicMock())
    patches.append(p_thread)
    p_thread.start()

    # mock os.path.exists / os.makedirs to avoid file system side effects
    p_exists = patch("os.path.exists", return_value=True)
    patches.append(p_exists)
    p_exists.start()

    with patch.dict(os.environ, env_vars, clear=False):
        # Remove cached app module if present
        for mod_name in list(sys.modules):
            if mod_name == "app" or mod_name.startswith("app."):
                del sys.modules[mod_name]

        import app as app_module

    for p in patches:
        p.stop()

    return app_module


class AppRouteTest(unittest.TestCase):
    """app.py 라우트 존재/부재 검증"""

    @classmethod
    def setUpClass(cls):
        cls.app_module = _load_app_module()
        cls.app = cls.app_module.app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

        # 등록된 라우트 목록
        cls.registered_rules = [rule.rule for rule in cls.app.url_map.iter_rules()]

    def test_dead_log_routes_removed(self):
        """Phase 1: 삭제된 로그/히스토리 라우트가 존재하지 않아야 한다"""
        dead_routes = [
            "/local/api/logs",
            "/local/api/logs/tail",
            "/local/api/logs/stats",
            "/local/api/logs/files",
            "/local/api/logs/weekly",
            "/local/api/logs/monthly",
            "/local/api/history/period",
            "/local/api/history/period/files",
            "/local/api/history/period/weekly",
            "/local/api/history/period/monthly",
            "/local/api/history/period/daily",
        ]
        for route in dead_routes:
            self.assertNotIn(
                route,
                self.registered_rules,
                f"Dead route {route} should have been removed",
            )

    def test_live_routes_exist(self):
        """Phase 4: Postman API 기준 라우트가 존재해야 한다"""
        expected_routes = [
            "/local/api/states",
            "/local/api/services",
            "/local/api/devices",
            "/local/api/schedules",
            "/local/api/rules",
            "/local/api/rooms",
            "/local/api/notifications",
            "/local/api/matterhub/id",
            "/test",
        ]
        for route in expected_routes:
            self.assertIn(
                route,
                self.registered_rules,
                f"Expected route {route} not found",
            )

    def test_matterhub_id_endpoint(self):
        """GET /local/api/matterhub/id 정상 응답"""
        with patch.dict(os.environ, {"matterhub_id": '"test-hub-123"'}):
            resp = self.client.get("/local/api/matterhub/id")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("matterhub_id", data)


if __name__ == "__main__":
    unittest.main()
