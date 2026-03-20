from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_run_provision_module(fake_client_class: type[object]):
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None

    mqtt_pkg_module = types.ModuleType("mqtt_pkg")
    provisioning_module = types.ModuleType("mqtt_pkg.provisioning")
    provisioning_module.AWSProvisioningClient = fake_client_class

    with patch.dict(
        sys.modules,
        {
            "dotenv": dotenv_module,
            "mqtt_pkg": mqtt_pkg_module,
            "mqtt_pkg.provisioning": provisioning_module,
        },
    ):
        sys.modules.pop("device_config.run_provision", None)
        return importlib.import_module("device_config.run_provision")


class ExistingIdClient:
    instances: list["ExistingIdClient"] = []

    def __init__(self) -> None:
        self.provision_calls = 0
        ExistingIdClient.instances.append(self)

    def check_certificate(self):
        return False, None, None

    def provision_device(self) -> bool:
        self.provision_calls += 1
        return True


class MissingCertClient:
    instances: list["MissingCertClient"] = []

    def __init__(self) -> None:
        self.provision_calls = 0
        MissingCertClient.instances.append(self)

    def check_certificate(self):
        return False, None, None

    def provision_device(self) -> bool:
        self.provision_calls += 1
        return True


class ExistingCertClient:
    instances: list["ExistingCertClient"] = []

    def __init__(self) -> None:
        self.provision_calls = 0
        ExistingCertClient.instances.append(self)

    def check_certificate(self):
        return True, "certificates/device.pem.crt", "certificates/private.pem.key"

    def provision_device(self) -> bool:
        self.provision_calls += 1
        return True


class RunProvisionTest(unittest.TestCase):
    def test_ensure_skips_when_matterhub_id_exists(self) -> None:
        ExistingIdClient.instances.clear()
        run_provision = load_run_provision_module(ExistingIdClient)

        with patch.dict(os.environ, {"matterhub_id": '"hub-123"'}, clear=False):
            exit_code = run_provision.main(["--ensure", "--non-interactive"])

        self.assertEqual(0, exit_code)
        self.assertEqual([], ExistingIdClient.instances)

    def test_ensure_skips_when_auto_provision_disabled(self) -> None:
        MissingCertClient.instances.clear()
        run_provision = load_run_provision_module(MissingCertClient)

        with patch.dict(os.environ, {"MATTERHUB_AUTO_PROVISION": "0"}, clear=False):
            exit_code = run_provision.main(["--ensure", "--non-interactive"])

        self.assertEqual(0, exit_code)
        self.assertEqual([], MissingCertClient.instances)

    def test_ensure_provisions_when_id_and_device_cert_are_missing(self) -> None:
        MissingCertClient.instances.clear()
        run_provision = load_run_provision_module(MissingCertClient)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("matterhub_id", None)
            os.environ.pop("MATTERHUB_AUTO_PROVISION", None)
            exit_code = run_provision.main(["--ensure", "--non-interactive"])

        self.assertEqual(0, exit_code)
        self.assertEqual(1, MissingCertClient.instances[0].provision_calls)

    def test_ensure_fails_when_device_cert_exists_without_matterhub_id(self) -> None:
        ExistingCertClient.instances.clear()
        run_provision = load_run_provision_module(ExistingCertClient)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("matterhub_id", None)
            exit_code = run_provision.main(["--ensure", "--non-interactive"])

        self.assertEqual(1, exit_code)
        self.assertEqual(0, ExistingCertClient.instances[0].provision_calls)


if __name__ == "__main__":
    unittest.main()
