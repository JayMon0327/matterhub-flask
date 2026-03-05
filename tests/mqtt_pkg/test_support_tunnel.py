from __future__ import annotations

import unittest
from typing import Mapping, Sequence
from unittest.mock import Mock

from mqtt_pkg.support_tunnel import (
    TunnelConfig,
    build_operator_connect_command,
    build_ssh_command,
    execute,
    load_config,
    validate_config,
)


def _valid_config(**overrides: object) -> TunnelConfig:
    data = {
        "enabled": True,
        "command": "ssh",
        "user": "maint",
        "host": "support.example.com",
        "port": 443,
        "remote_port": 2222,
        "local_port": 22,
        "remote_bind_address": "127.0.0.1",
        "private_key_path": "/etc/matterhub/support_tunnel_ed25519",
        "known_hosts_path": "/etc/matterhub/support_known_hosts",
        "strict_host_key_checking": True,
        "server_alive_interval": 30,
        "server_alive_count_max": 3,
        "extra_options": tuple(),
        "autossh_gatetime": "0",
        "relay_operator_user": "ec2-user",
        "operator_key_path_hint": "<relay-operator-key.pem>",
    }
    data.update(overrides)
    return TunnelConfig(**data)


class SupportTunnelConfigTest(unittest.TestCase):
    def test_load_config_parses_env_values(self) -> None:
        env = {
            "SUPPORT_TUNNEL_ENABLED": "1",
            "SUPPORT_TUNNEL_COMMAND": "autossh",
            "SUPPORT_TUNNEL_USER": "maint",
            "SUPPORT_TUNNEL_HOST": "support.example.com",
            "SUPPORT_TUNNEL_PORT": "443",
            "SUPPORT_TUNNEL_REMOTE_PORT": "2222",
            "SUPPORT_TUNNEL_LOCAL_PORT": "22",
            "SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS": "0.0.0.0",
            "SUPPORT_TUNNEL_PRIVATE_KEY_PATH": "/keys/id_ed25519",
            "SUPPORT_TUNNEL_KNOWN_HOSTS_PATH": "/keys/known_hosts",
            "SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING": "0",
            "SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL": "20",
            "SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX": "2",
            "SUPPORT_TUNNEL_SSH_EXTRA_OPTS": "-v -o LogLevel=ERROR",
            "SUPPORT_TUNNEL_AUTOSSH_GATETIME": "5",
            "SUPPORT_TUNNEL_RELAY_OPERATOR_USER": "relayops",
            "SUPPORT_TUNNEL_OPERATOR_KEY_PATH": "/keys/relay.pem",
        }

        config = load_config(env=env)

        self.assertTrue(config.enabled)
        self.assertEqual("autossh", config.command)
        self.assertEqual("maint", config.user)
        self.assertEqual("support.example.com", config.host)
        self.assertEqual(2222, config.remote_port)
        self.assertEqual("0.0.0.0", config.remote_bind_address)
        self.assertFalse(config.strict_host_key_checking)
        self.assertEqual(20, config.server_alive_interval)
        self.assertEqual(2, config.server_alive_count_max)
        self.assertEqual(("-v", "-o", "LogLevel=ERROR"), config.extra_options)
        self.assertEqual("5", config.autossh_gatetime)
        self.assertEqual("relayops", config.relay_operator_user)
        self.assertEqual("/keys/relay.pem", config.operator_key_path_hint)

    def test_validate_config_reports_missing_required_fields(self) -> None:
        config = _valid_config(user=None, host=None, remote_port=None)

        errors = validate_config(config)

        self.assertIn("SUPPORT_TUNNEL_USER is required.", errors)
        self.assertIn("SUPPORT_TUNNEL_HOST is required.", errors)
        self.assertIn("SUPPORT_TUNNEL_REMOTE_PORT is required.", errors)

    def test_build_ssh_command_includes_expected_reverse_forward(self) -> None:
        config = _valid_config()

        command = build_ssh_command(config)

        self.assertEqual("ssh", command[0])
        self.assertIn("-R", command)
        self.assertIn("127.0.0.1:2222:localhost:22", command)
        self.assertIn("maint@support.example.com", command)

    def test_build_operator_connect_command_uses_proxycommand(self) -> None:
        config = _valid_config()

        command = build_operator_connect_command(config, device_user="whatsmatter")

        self.assertEqual(
            [
                "ssh",
                "-o",
                "ProxyCommand=ssh -i <relay-operator-key.pem> -p 443 ec2-user@support.example.com -W %h:%p",
                "-p",
                "2222",
                "whatsmatter@127.0.0.1",
            ],
            command,
        )


class SupportTunnelExecuteTest(unittest.TestCase):
    def test_execute_returns_zero_when_disabled(self) -> None:
        config = _valid_config(enabled=False)
        runner = Mock()

        return_code = execute(config, runner=runner)

        self.assertEqual(0, return_code)
        runner.assert_not_called()

    def test_execute_dry_run_does_not_invoke_runner(self) -> None:
        config = _valid_config()
        runner = Mock()

        return_code = execute(config, dry_run=True, runner=runner)

        self.assertEqual(0, return_code)
        runner.assert_not_called()

    def test_execute_injects_autossh_env(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_runner(command: Sequence[str], env: Mapping[str, str] | None) -> int:
            self.assertEqual("autossh", command[0])
            self.assertIsNotNone(env)
            captured_env.update(env or {})
            return 0

        config = _valid_config(command="autossh", autossh_gatetime="10")

        return_code = execute(config, runner=fake_runner)

        self.assertEqual(0, return_code)
        self.assertEqual("10", captured_env.get("AUTOSSH_GATETIME"))


if __name__ == "__main__":
    unittest.main()
