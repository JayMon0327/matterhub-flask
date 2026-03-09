from __future__ import annotations

import unittest
from pathlib import Path

from device_config.service_definitions import (
    API_HARDENING_DIRECTIVES,
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
                "matterhub-update-agent",
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
        self.assertIn("matterhub-update-agent", enabled)
        self.assertNotIn("matterhub-support-tunnel", enabled)

    def test_exec_start_uses_repo_venv_and_script(self) -> None:
        command = build_exec_start(
            "/srv/matterhub",
            "mqtt.py",
            service_name="matterhub-mqtt",
            runtime_mode="python",
        )
        self.assertEqual(
            "/srv/matterhub/venv/bin/python /srv/matterhub/mqtt.py",
            command,
        )

    def test_exec_start_binary_mode_points_to_runtime_executable(self) -> None:
        command = build_exec_start(
            "/opt/matterhub",
            "mqtt.py",
            service_name="matterhub-mqtt",
            runtime_mode="binary",
        )
        self.assertEqual("/opt/matterhub/bin/matterhub-mqtt/matterhub-mqtt", command)

    def test_service_context_contains_render_placeholders(self) -> None:
        service = get_service_definitions()[0]
        context = build_service_context(service, Path("/srv/matterhub"), "whatsmatter")
        self.assertEqual("MatterHub Flask API", context["@DESCRIPTION@"])
        self.assertEqual("whatsmatter", context["@RUN_USER@"])
        self.assertEqual("/srv/matterhub", context["@WORKING_DIRECTORY@"])
        self.assertNotIn("NoNewPrivileges=true", context["@HARDENING_DIRECTIVES@"])
        self.assertNotIn("RestrictSUIDSGID=true", context["@HARDENING_DIRECTIVES@"])

    def test_api_hardening_drops_privilege_blocking_directives(self) -> None:
        self.assertNotIn("NoNewPrivileges=true", API_HARDENING_DIRECTIVES)
        self.assertNotIn("RestrictSUIDSGID=true", API_HARDENING_DIRECTIVES)
        self.assertIn("ProtectSystem=full", API_HARDENING_DIRECTIVES)

    def test_default_hardening_directives_attached(self) -> None:
        api_service = [s for s in get_service_definitions() if s.service_name == "matterhub-api"][0]
        mqtt_service = [s for s in get_service_definitions() if s.service_name == "matterhub-mqtt"][0]
        self.assertEqual(API_HARDENING_DIRECTIVES, api_service.hardening_directives)
        self.assertEqual(DEFAULT_HARDENING_DIRECTIVES, mqtt_service.hardening_directives)

    def test_support_tunnel_has_unit_start_limit_override(self) -> None:
        support_tunnel = [s for s in get_service_definitions() if s.service_name == "matterhub-support-tunnel"][0]
        self.assertIn("StartLimitIntervalSec=0", support_tunnel.unit_directives)
        self.assertIn("StartLimitBurst=0", support_tunnel.unit_directives)

    def test_update_agent_runs_as_root(self) -> None:
        update_agent = [s for s in get_service_definitions() if s.service_name == "matterhub-update-agent"][0]
        self.assertEqual("root", update_agent.run_user_override)


if __name__ == "__main__":
    unittest.main()
