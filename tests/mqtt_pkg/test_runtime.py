from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_runtime_module():
    awscrt_module = types.ModuleType("awscrt")
    awscrt_module.io = types.SimpleNamespace()
    awscrt_module.mqtt = types.SimpleNamespace(
        QoS=types.SimpleNamespace(AT_LEAST_ONCE=1),
        Connection=object,
    )

    awsiot_module = types.ModuleType("awsiot")
    awsiot_module.mqtt_connection_builder = types.SimpleNamespace()
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None

    with patch.dict(
        sys.modules,
        {
            "awscrt": awscrt_module,
            "awsiot": awsiot_module,
            "dotenv": dotenv_module,
        },
    ):
        sys.modules.pop("mqtt_pkg.runtime", None)
        sys.modules.pop("mqtt_pkg.settings", None)
        return importlib.import_module("mqtt_pkg.runtime")


class RuntimeDescribeConnectionTest(unittest.TestCase):
    def test_describe_connection_reports_certificate_paths(self) -> None:
        runtime = load_runtime_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            cert_dir = Path(temp_dir)
            (cert_dir / "cert.pem").write_text("cert", encoding="utf-8")
            (cert_dir / "key.pem").write_text("key", encoding="utf-8")
            (cert_dir / "ca_cert.pem").write_text("ca", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "KONAI_CERT_PATH": str(cert_dir),
                    "KONAI_ENDPOINT": "example.iot.ap-northeast-2.amazonaws.com",
                    "KONAI_CLIENT_ID": "matterhub-probe",
                },
                clear=False,
            ):
                client = runtime.AWSIoTClient()
                description = client.describe_connection()

        self.assertEqual("example.iot.ap-northeast-2.amazonaws.com", description["endpoint"])
        self.assertEqual("matterhub-probe", description["client_id"])
        self.assertEqual(str(cert_dir), description["cert_path"])
        self.assertEqual(str(cert_dir / "cert.pem"), description["cert_file"])
        self.assertEqual(str(cert_dir / "key.pem"), description["key_file"])
        self.assertTrue(description["cert_exists"])
        self.assertTrue(description["key_exists"])
        self.assertTrue(description["ca_exists"])

    def test_describe_connection_reports_missing_certificate_files(self) -> None:
        runtime = load_runtime_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"KONAI_CERT_PATH": temp_dir}, clear=False):
                client = runtime.AWSIoTClient()
                description = client.describe_connection()

        self.assertFalse(description["cert_exists"])
        self.assertFalse(description["key_exists"])
        self.assertFalse(description["ca_exists"])


if __name__ == "__main__":
    unittest.main()
