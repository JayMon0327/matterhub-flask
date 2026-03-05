from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        return None

load_dotenv()


def _strip_quotes(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().strip('"').strip("'")
    return normalized or None


def _env_bool(name: str, default: bool = False, env: Mapping[str, str] | None = None) -> bool:
    source = env or os.environ
    raw = source.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, env: Mapping[str, str] | None = None) -> int:
    source = env or os.environ
    raw = source.get(name)
    if raw is None:
        return default
    return int(raw.strip())


@dataclass(frozen=True)
class TunnelConfig:
    enabled: bool
    command: str
    user: str | None
    host: str | None
    port: int
    remote_port: int | None
    local_port: int
    remote_bind_address: str
    private_key_path: str | None
    known_hosts_path: str | None
    strict_host_key_checking: bool
    server_alive_interval: int
    server_alive_count_max: int
    extra_options: tuple[str, ...]
    autossh_gatetime: str
    relay_operator_user: str
    operator_key_path_hint: str


def load_config(env: Mapping[str, str] | None = None) -> TunnelConfig:
    source = env or os.environ
    command = (_strip_quotes(source.get("SUPPORT_TUNNEL_COMMAND")) or "ssh").lower()
    remote_port_raw = _strip_quotes(source.get("SUPPORT_TUNNEL_REMOTE_PORT"))
    remote_port = int(remote_port_raw) if remote_port_raw else None
    extra_options_raw = _strip_quotes(source.get("SUPPORT_TUNNEL_SSH_EXTRA_OPTS")) or ""

    return TunnelConfig(
        enabled=_env_bool("SUPPORT_TUNNEL_ENABLED", default=False, env=source),
        command=command,
        user=_strip_quotes(source.get("SUPPORT_TUNNEL_USER")),
        host=_strip_quotes(source.get("SUPPORT_TUNNEL_HOST")),
        port=_env_int("SUPPORT_TUNNEL_PORT", default=443, env=source),
        remote_port=remote_port,
        local_port=_env_int("SUPPORT_TUNNEL_LOCAL_PORT", default=22, env=source),
        remote_bind_address=_strip_quotes(source.get("SUPPORT_TUNNEL_REMOTE_BIND_ADDRESS"))
        or "127.0.0.1",
        private_key_path=_strip_quotes(source.get("SUPPORT_TUNNEL_PRIVATE_KEY_PATH")),
        known_hosts_path=_strip_quotes(source.get("SUPPORT_TUNNEL_KNOWN_HOSTS_PATH")),
        strict_host_key_checking=_env_bool(
            "SUPPORT_TUNNEL_STRICT_HOST_KEY_CHECKING",
            default=True,
            env=source,
        ),
        server_alive_interval=_env_int("SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL", default=30, env=source),
        server_alive_count_max=_env_int("SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX", default=3, env=source),
        extra_options=tuple(shlex.split(extra_options_raw)),
        autossh_gatetime=_strip_quotes(source.get("SUPPORT_TUNNEL_AUTOSSH_GATETIME")) or "0",
        relay_operator_user=_strip_quotes(source.get("SUPPORT_TUNNEL_RELAY_OPERATOR_USER"))
        or "ec2-user",
        operator_key_path_hint=_strip_quotes(source.get("SUPPORT_TUNNEL_OPERATOR_KEY_PATH"))
        or "<relay-operator-key.pem>",
    )


def validate_config(config: TunnelConfig) -> list[str]:
    errors: list[str] = []
    if config.command not in {"ssh", "autossh"}:
        errors.append("SUPPORT_TUNNEL_COMMAND must be 'ssh' or 'autossh'.")

    if not config.user:
        errors.append("SUPPORT_TUNNEL_USER is required.")
    if not config.host:
        errors.append("SUPPORT_TUNNEL_HOST is required.")
    if config.remote_port is None:
        errors.append("SUPPORT_TUNNEL_REMOTE_PORT is required.")

    if not (1 <= config.port <= 65535):
        errors.append("SUPPORT_TUNNEL_PORT must be between 1 and 65535.")
    if not (1 <= config.local_port <= 65535):
        errors.append("SUPPORT_TUNNEL_LOCAL_PORT must be between 1 and 65535.")
    if config.remote_port is not None and not (1 <= config.remote_port <= 65535):
        errors.append("SUPPORT_TUNNEL_REMOTE_PORT must be between 1 and 65535.")

    if config.server_alive_interval <= 0:
        errors.append("SUPPORT_TUNNEL_SERVER_ALIVE_INTERVAL must be greater than 0.")
    if config.server_alive_count_max <= 0:
        errors.append("SUPPORT_TUNNEL_SERVER_ALIVE_COUNT_MAX must be greater than 0.")

    return errors


def build_ssh_command(config: TunnelConfig) -> list[str]:
    if config.remote_port is None:
        raise ValueError("SUPPORT_TUNNEL_REMOTE_PORT is required.")
    if not config.user or not config.host:
        raise ValueError("SUPPORT_TUNNEL_USER and SUPPORT_TUNNEL_HOST are required.")

    reverse_spec = (
        f"{config.remote_bind_address}:{config.remote_port}:localhost:{config.local_port}"
    )

    command = [
        config.command,
        "-N",
        "-T",
        "-R",
        reverse_spec,
        "-p",
        str(config.port),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ServerAliveInterval={config.server_alive_interval}",
        "-o",
        f"ServerAliveCountMax={config.server_alive_count_max}",
        "-o",
        (
            "StrictHostKeyChecking=yes"
            if config.strict_host_key_checking
            else "StrictHostKeyChecking=no"
        ),
    ]

    if config.known_hosts_path:
        command.extend(["-o", f"UserKnownHostsFile={config.known_hosts_path}"])
    if config.private_key_path:
        command.extend(["-i", config.private_key_path])
    if config.extra_options:
        command.extend(config.extra_options)

    command.append(f"{config.user}@{config.host}")
    return command


def build_operator_connect_command(
    config: TunnelConfig,
    *,
    device_user: str = "whatsmatter",
    device_host: str = "127.0.0.1",
) -> list[str]:
    if config.remote_port is None:
        raise ValueError("SUPPORT_TUNNEL_REMOTE_PORT is required.")
    if not config.user or not config.host:
        raise ValueError("SUPPORT_TUNNEL_USER and SUPPORT_TUNNEL_HOST are required.")

    proxy_command = (
        "ssh "
        f"-i {config.operator_key_path_hint} "
        f"-p {config.port} "
        f"{config.relay_operator_user}@{config.host} "
        "-W %h:%p"
    )

    return [
        "ssh",
        "-o",
        f"ProxyCommand={proxy_command}",
        "-p",
        str(config.remote_port),
        f"{device_user}@{device_host}",
    ]


def _quote_command(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _default_runner(command: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    completed = subprocess.run(list(command), env=dict(env) if env else None, check=False)
    return int(completed.returncode)


def execute(
    config: TunnelConfig,
    *,
    dry_run: bool = False,
    runner: Callable[[Sequence[str], Mapping[str, str] | None], int] = _default_runner,
) -> int:
    if not config.enabled:
        print("[SUPPORT_TUNNEL] disabled (SUPPORT_TUNNEL_ENABLED=0)")
        return 0

    errors = validate_config(config)
    if errors:
        for error in errors:
            print(f"[SUPPORT_TUNNEL][CONFIG][FAIL] {error}")
        return 2

    command = build_ssh_command(config)
    print(f"[SUPPORT_TUNNEL] command={_quote_command(command)}")
    if dry_run:
        print("[SUPPORT_TUNNEL] dry-run mode, command not executed")
        return 0

    run_env = os.environ.copy()
    if config.command == "autossh":
        run_env.setdefault("AUTOSSH_GATETIME", config.autossh_gatetime)

    print("[SUPPORT_TUNNEL] starting reverse SSH tunnel")
    return_code = runner(command, run_env)
    if return_code == 0:
        print("[SUPPORT_TUNNEL] process exited normally")
    else:
        print(f"[SUPPORT_TUNNEL][FAIL] process exited with code={return_code}")
    return return_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start MatterHub reverse SSH tunnel process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved command without executing it.",
    )
    parser.add_argument(
        "--print-connect-command",
        action="store_true",
        help="Print operator-side SSH command for accessing this device via reverse tunnel.",
    )
    parser.add_argument(
        "--device-user",
        default=os.environ.get("SUPPORT_TUNNEL_DEVICE_USER", "whatsmatter"),
        help="Device SSH user for operator connect command (default: whatsmatter).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = load_config()
    if args.print_connect_command:
        try:
            step1_command = [
                "ssh",
                "-i",
                config.operator_key_path_hint,
                "-p",
                str(config.port),
                f"{config.relay_operator_user}@{config.host}",
            ]
            step2_command = [
                "j",
                os.environ.get("matterhub_id", "<hub_id>"),
            ]
            operator_command = build_operator_connect_command(
                config,
                device_user=args.device_user,
            )
            print(f"[SUPPORT_TUNNEL] operator_connect_step1={_quote_command(step1_command)}")
            print(f"[SUPPORT_TUNNEL] operator_connect_step2={_quote_command(step2_command)}")
            print(f"[SUPPORT_TUNNEL] operator_connect_oneliner={_quote_command(operator_command)}")
            return 0
        except Exception as exc:
            print(f"[SUPPORT_TUNNEL][CONFIG][FAIL] {exc}")
            return 2
    return execute(config, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
