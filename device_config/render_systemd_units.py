#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from device_config.service_definitions import (
        build_service_context,
        get_enabled_service_definitions,
        get_service_definitions,
        get_unit_name,
        render_systemd_unit,
    )
except ImportError:
    from service_definitions import (  # type: ignore
        build_service_context,
        get_enabled_service_definitions,
        get_service_definitions,
        get_unit_name,
        render_systemd_unit,
    )


DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "systemd" / "matterhub-service.service.template"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render systemd service units for MatterHub services.",
    )
    parser.add_argument("--project-root", help="Absolute project root path.")
    parser.add_argument("--run-user", help="User that systemd services should run as.")
    parser.add_argument(
        "--output-dir",
        help="Directory where rendered .service files will be written.",
    )
    parser.add_argument(
        "--template",
        default=str(DEFAULT_TEMPLATE_PATH),
        help="Path to the systemd template file.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=["python", "binary"],
        default="python",
        help="ExecStart mode for rendered units.",
    )
    parser.add_argument(
        "--list-unit-names",
        action="store_true",
        help="Print the unit filenames and exit.",
    )
    parser.add_argument(
        "--list-enabled-unit-names",
        action="store_true",
        help="Print unit filenames enabled by default and exit.",
    )
    return parser.parse_args()


def render_units(
    project_root: str,
    run_user: str,
    output_dir: Path,
    template_path: Path,
    runtime_mode: str,
) -> None:
    template_text = template_path.read_text(encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    for service in get_service_definitions():
        context = build_service_context(
            service,
            project_root,
            run_user,
            runtime_mode=runtime_mode,
        )
        rendered = render_systemd_unit(template_text, context)
        unit_path = output_dir / get_unit_name(service)
        unit_path.write_text(rendered, encoding="utf-8")


def main() -> int:
    args = parse_args()

    if args.list_unit_names:
        for service in get_service_definitions():
            print(get_unit_name(service))
        return 0

    if args.list_enabled_unit_names:
        for service in get_enabled_service_definitions():
            print(get_unit_name(service))
        return 0

    if not args.project_root or not args.run_user or not args.output_dir:
        raise SystemExit(
            "--project-root, --run-user, and --output-dir are required unless --list-unit-names is used.",
        )

    render_units(
        project_root=args.project_root,
        run_user=args.run_user,
        output_dir=Path(args.output_dir),
        template_path=Path(args.template),
        runtime_mode=args.runtime_mode,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
