from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_VENV_PYTHON = Path("venv/bin/python")


@dataclass(frozen=True)
class ServiceDefinition:
    service_name: str
    description: str
    script_path: Path
    enabled_by_default: bool = True


SERVICE_DEFINITIONS: tuple[ServiceDefinition, ...] = (
    ServiceDefinition(
        service_name="matterhub-api",
        description="MatterHub Flask API",
        script_path=Path("app.py"),
    ),
    ServiceDefinition(
        service_name="matterhub-mqtt",
        description="MatterHub MQTT Worker",
        script_path=Path("mqtt.py"),
    ),
    ServiceDefinition(
        service_name="matterhub-rule-engine",
        description="MatterHub Rule Engine",
        script_path=Path("sub/ruleEngine.py"),
    ),
    ServiceDefinition(
        service_name="matterhub-notifier",
        description="MatterHub Notifier",
        script_path=Path("sub/notifier.py"),
    ),
    ServiceDefinition(
        service_name="matterhub-support-tunnel",
        description="MatterHub Support Tunnel",
        script_path=Path("support_tunnel.py"),
        enabled_by_default=False,
    ),
)


def get_service_definitions() -> tuple[ServiceDefinition, ...]:
    return SERVICE_DEFINITIONS


def get_enabled_service_definitions() -> tuple[ServiceDefinition, ...]:
    return tuple(service for service in SERVICE_DEFINITIONS if service.enabled_by_default)


def get_unit_name(service: ServiceDefinition) -> str:
    return f"{service.service_name}.service"


def build_exec_start(project_root: Path | str, script_path: Path | str) -> str:
    project_root = Path(project_root)
    script_path = Path(script_path)
    python_path = project_root / DEFAULT_VENV_PYTHON
    target_script = project_root / script_path
    return f"{python_path} {target_script}"


def build_service_context(
    service: ServiceDefinition,
    project_root: Path | str,
    run_user: str,
) -> dict[str, str]:
    project_root = Path(project_root)
    return {
        "@DESCRIPTION@": service.description,
        "@RUN_USER@": run_user,
        "@WORKING_DIRECTORY@": str(project_root),
        "@EXEC_START@": build_exec_start(project_root, service.script_path),
    }


def render_systemd_unit(template_text: str, context: dict[str, str]) -> str:
    rendered = template_text
    for placeholder, value in context.items():
        rendered = rendered.replace(placeholder, value)
    return rendered
