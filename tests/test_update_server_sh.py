"""update_server.sh 구문 및 플래그 파싱 검증 테스트."""

import os
import subprocess
import unittest


SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "device_config",
    "update_server.sh",
)


class UpdateServerShTest(unittest.TestCase):
    """update_server.sh 존재 및 기본 구문 검증"""

    def test_script_exists(self):
        self.assertTrue(
            os.path.isfile(SCRIPT_PATH),
            f"update_server.sh not found at {SCRIPT_PATH}",
        )

    def test_script_is_executable(self):
        self.assertTrue(
            os.access(SCRIPT_PATH, os.X_OK),
            "update_server.sh should be executable",
        )

    def test_bash_syntax_check(self):
        """bash -n 으로 구문 검사"""
        result = subprocess.run(
            ["bash", "-n", SCRIPT_PATH],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Syntax error in update_server.sh: {result.stderr}",
        )

    def test_script_accepts_parameters(self):
        """스크립트 매개변수 문서 확인 (usage 주석 존재)"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("branch", content)
        self.assertIn("force_update", content)
        self.assertIn("update_id", content)
        self.assertIn("hub_id", content)

    def test_skip_restart_flag_documented(self):
        """--skip-restart 플래그 지원 확인"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("--skip-restart", content)

    def test_restart_only_flag_documented(self):
        """--restart-only 플래그 지원 확인"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("--restart-only", content)

    def test_env_migration_present(self):
        """SUBSCRIBE_MATTERHUB_TOPICS .env 마이그레이션 로직 존재"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("SUBSCRIBE_MATTERHUB_TOPICS", content)
        self.assertIn("MATTERHUB_VENDOR", content)

    def test_rollback_logic_present(self):
        """롤백 로직 존재 확인"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("PRE_UPDATE_COMMIT", content)
        self.assertIn("rollback", content.lower())

    def test_process_manager_detection_present(self):
        """프로세스 매니저 감지 함수 존재"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("detect_process_manager", content)
        self.assertIn("systemd", content)
        self.assertIn("pm2", content)

    def test_status_file_output_present(self):
        """상태 파일 출력 로직 존재"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("/tmp/update_", content)
        self.assertIn(".status", content)

    def test_no_hardcoded_hyodol_path(self):
        """하드코딩된 /home/hyodol/ 경로가 없어야 함"""
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("/home/hyodol/", content)


if __name__ == "__main__":
    unittest.main()
