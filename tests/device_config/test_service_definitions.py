from __future__ import annotations

import unittest
from pathlib import Path

from device_config.service_definitions import (
    build_exec_start,
    build_service_context,
    DEFAULT_HARDENING_DIRECTIVES,
    get_enabled_service_definitions,
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
                "matterhub-support-tunnel",
            ],
            [service.service_name for service in get_service_definitions()],
        )

    def test_unit_name_suffix(self) -> None:
        service = get_service_definitions()[0]
        self.assertEqual("matterhub-api.service", get_unit_name(service))

    def test_enabled_by_default_excludes_support_tunnel(self) -> None:
        enabled = [service.service_name for service in get_enabled_service_definitions()]
        self.assertIn("matterhub-api", enabled)
        self.assertIn("matterhub-mqtt", enabled)
        self.assertNotIn("matterhub-support-tunnel", enabled)

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
        self.assertIn("NoNewPrivileges=true", context["@HARDENING_DIRECTIVES@"])

    def test_default_hardening_directives_attached(self) -> None:
        for service in get_service_definitions():
            self.assertEqual(DEFAULT_HARDENING_DIRECTIVES, service.hardening_directives)


if __name__ == "__main__":
    unittest.main()
