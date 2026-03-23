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
    run_user_override: str | None = None
    unit_directives: tuple[str, ...] = ()
    hardening_directives: tuple[str, ...] = ()


DEFAULT_HARDENING_DIRECTIVES: tuple[str, ...] = (
    "NoNewPrivileges=true",
    "PrivateTmp=true",
    "ProtectSystem=false",
    "ProtectControlGroups=true",
    "ProtectKernelTunables=true",
    "ProtectKernelModules=true",
    "RestrictSUIDSGID=true",
    "LockPersonality=true",
    "RestrictRealtime=true",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
    "UMask=0077",
)
API_HARDENING_DIRECTIVES: tuple[str, ...] = tuple(
    directive
    for directive in DEFAULT_HARDENING_DIRECTIVES
    if directive
    not in {
        "NoNewPrivileges=true",
        "RestrictSUIDSGID=true",
        "ProtectSystem=false",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
    }
) + ("ProtectSystem=false", "ReadWritePaths=/etc/matterhub",)



# update-agent는 root로 실행 → CapabilityBoundingSet= (빈값)은
# root의 모든 capability를 제거하여 파일 접근 실패를 유발함.
# 필요한 capability만 명시적으로 허용.
UPDATE_AGENT_HARDENING_DIRECTIVES: tuple[str, ...] = (
    "PrivateTmp=true",
    "ProtectSystem=false",
    "ProtectControlGroups=true",
    "ProtectKernelTunables=true",
    "ProtectKernelModules=true",
    "LockPersonality=true",
    "RestrictRealtime=true",
    "UMask=0077",
)


SERVICE_DEFINITIONS: tuple[ServiceDefinition, ...] = (
    ServiceDefinition(
        service_name="matterhub-api",
        description="MatterHub Flask API",
        script_path=Path("app.py"),
        hardening_directives=(),
    ),
    ServiceDefinition(
        service_name="matterhub-mqtt",
        description="MatterHub MQTT Worker",
        script_path=Path("mqtt.py"),
        hardening_directives=DEFAULT_HARDENING_DIRECTIVES,
    ),
    ServiceDefinition(
        service_name="matterhub-rule-engine",
        description="MatterHub Rule Engine",
        script_path=Path("sub/ruleEngine.py"),
        hardening_directives=DEFAULT_HARDENING_DIRECTIVES,
    ),
    ServiceDefinition(
        service_name="matterhub-notifier",
        description="MatterHub Notifier",
        script_path=Path("sub/notifier.py"),
        hardening_directives=DEFAULT_HARDENING_DIRECTIVES,
    ),
    ServiceDefinition(
        service_name="matterhub-support-tunnel",
        description="MatterHub Support Tunnel",
        script_path=Path("support_tunnel.py"),
        enabled_by_default=False,
        unit_directives=(
            "StartLimitIntervalSec=0",
            "StartLimitBurst=0",
        ),
        hardening_directives=DEFAULT_HARDENING_DIRECTIVES,
    ),
    ServiceDefinition(
        service_name="matterhub-update-agent",
        description="MatterHub Update Agent",
        script_path=Path("update_agent.py"),
        run_user_override="root",
        hardening_directives=UPDATE_AGENT_HARDENING_DIRECTIVES,
    ),
)


def get_service_definitions() -> tuple[ServiceDefinition, ...]:
    return SERVICE_DEFINITIONS


def get_enabled_service_definitions() -> tuple[ServiceDefinition, ...]:
    return tuple(service for service in SERVICE_DEFINITIONS if service.enabled_by_default)


def get_unit_name(service: ServiceDefinition) -> str:
    return f"{service.service_name}.service"


def build_exec_start(
    project_root: Path | str,
    script_path: Path | str,
    *,
    service_name: str,
    runtime_mode: str = "python",
) -> str:
    project_root = Path(project_root)
    if runtime_mode == "python":
        script_path = Path(script_path)
        venv_python = project_root / DEFAULT_VENV_PYTHON
        # venv가 없으면 시스템 python3 사용
        python_path = venv_python if venv_python.exists() else Path("/usr/bin/python3")
        target_script = project_root / script_path
        return f"{python_path} {target_script}"
    if runtime_mode == "binary":
        executable = project_root / "bin" / service_name / service_name
        return str(executable)
    raise ValueError(f"unsupported runtime_mode: {runtime_mode}")


def build_service_context(
    service: ServiceDefinition,
    project_root: Path | str,
    run_user: str,
    *,
    runtime_mode: str = "python",
) -> dict[str, str]:
    project_root = Path(project_root)
    resolved_user = service.run_user_override or run_user
    return {
        "@DESCRIPTION@": service.description,
        "@UNIT_DIRECTIVES@": "\n".join(service.unit_directives),
        "@RUN_USER@": resolved_user,
        "@WORKING_DIRECTORY@": str(project_root),
        "@EXEC_START@": build_exec_start(
            project_root,
            service.script_path,
            service_name=service.service_name,
            runtime_mode=runtime_mode,
        ),
        "@HARDENING_DIRECTIVES@": "\n".join(service.hardening_directives),
    }


def render_systemd_unit(template_text: str, context: dict[str, str]) -> str:
    rendered = template_text
    for placeholder, value in context.items():
        rendered = rendered.replace(placeholder, value)
    return rendered
