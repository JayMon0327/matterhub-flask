from __future__ import annotations

import unittest
from pathlib import Path

from device_config.service_definitions import (
    build_exec_start,
    build_service_context,
    get_service_definitions,
    get_unit_name,
)


class ServiceDefinitionsTest(unittest.TestCase):
    def test_expected_service_names(self) -> None:
        self.assertEqual(
            [
                "matterhub-api",
                "matterhub-mqtt",
                "matterhub-rule-engine",
                "matterhub-notifier",
            ],
            [service.service_name for service in get_service_definitions()],
        )

    def test_unit_name_suffix(self) -> None:
        service = get_service_definitions()[0]
        self.assertEqual("matterhub-api.service", get_unit_name(service))

    def test_exec_start_uses_repo_venv_and_script(self) -> None:
        command = build_exec_start("/srv/matterhub", "mqtt.py")
        self.assertEqual(
            "/srv/matterhub/venv/bin/python /srv/matterhub/mqtt.py",
            command,
        )

    def test_service_context_contains_render_placeholders(self) -> None:
        service = get_service_definitions()[0]
        context = build_service_context(service, Path("/srv/matterhub"), "whatsmatter")
        self.assertEqual("MatterHub Flask API", context["@DESCRIPTION@"])
        self.assertEqual("whatsmatter", context["@RUN_USER@"])
        self.assertEqual("/srv/matterhub", context["@WORKING_DIRECTORY@"])


if __name__ == "__main__":
    unittest.main()
