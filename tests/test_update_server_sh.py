"""update_server.sh dry-run 검증 테스트."""

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


if __name__ == "__main__":
    unittest.main()
