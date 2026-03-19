from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def normalize_mac(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    stripped = raw.replace(":", "").replace("-", "")
    if len(stripped) != 12 or any(ch not in "0123456789abcdef" for ch in stripped):
        return ""
    return ":".join(stripped[index : index + 2] for index in range(0, 12, 2))


def _parse_allowed_from_text(text: str) -> set[str]:
    normalized = text.replace(",", "\n")
    allowed: set[str] = set()
    for line in normalized.splitlines():
        mac = normalize_mac(line)
        if mac:
            allowed.add(mac)
    return allowed


def load_allowed_macs(
    *,
    env: Mapping[str, str] | None = None,
    read_text_fn: Callable[[Path], str] | None = None,
) -> set[str]:
    source = env or os.environ
    allowed: set[str] = set()

    inline = source.get("MAC_BINDING_ALLOWED")
    if inline:
        allowed |= _parse_allowed_from_text(inline)

    allowed_file = source.get("MAC_BINDING_ALLOWED_FILE")
    if allowed_file:
        path = Path(allowed_file)
        if path.is_file():
            reader = read_text_fn or (lambda p: p.read_text(encoding="utf-8"))
            allowed |= _parse_allowed_from_text(reader(path))
    return allowed


def load_runtime_macs(
    *,
    interface: str | None = None,
    sys_class_net: Path = Path("/sys/class/net"),
    read_text_fn: Callable[[Path], str] | None = None,
) -> dict[str, str]:
    reader = read_text_fn or (lambda p: p.read_text(encoding="utf-8"))
    interfaces: list[str] = []
    if interface:
        interfaces = [interface]
    elif sys_class_net.is_dir():
        interfaces = sorted(entry.name for entry in sys_class_net.iterdir() if entry.is_dir())

    runtime: dict[str, str] = {}
    for name in interfaces:
        if name == "lo":
            continue
        address_path = sys_class_net / name / "address"
        if not address_path.is_file():
            continue
        try:
            mac = normalize_mac(reader(address_path))
        except Exception:
            continue
        if mac:
            runtime[name] = mac
    return runtime


def evaluate_mac_binding(
    *,
    env: Mapping[str, str] | None = None,
    sys_class_net: Path = Path("/sys/class/net"),
    read_text_fn: Callable[[Path], str] | None = None,
) -> tuple[bool, dict[str, object]]:
    source = env or os.environ
    if not _as_bool(source.get("MAC_BINDING_ENABLED"), default=False):
        return True, {"enabled": False, "reason": "disabled"}

    allowed = load_allowed_macs(env=source, read_text_fn=read_text_fn)
    if not allowed:
        return False, {"enabled": True, "reason": "allowed_list_empty"}

    target_interface = (source.get("MAC_BINDING_INTERFACE") or "").strip() or None
    runtime = load_runtime_macs(
        interface=target_interface,
        sys_class_net=sys_class_net,
        read_text_fn=read_text_fn,
    )
    if not runtime:
        return False, {"enabled": True, "reason": "runtime_mac_not_found"}

    runtime_set = set(runtime.values())
    matched = sorted(runtime_set & allowed)
    if matched:
        return True, {
            "enabled": True,
            "reason": "allowed_mac_matched",
            "matched_macs": matched,
            "runtime_macs": runtime,
        }
    return False, {
        "enabled": True,
        "reason": "allowed_mac_not_matched",
        "runtime_macs": runtime,
    }


def enforce_mac_binding(logger: Callable[[str], None] = print) -> bool:
    allowed, details = evaluate_mac_binding()
    if allowed:
        if details.get("enabled"):
            logger(
                "[MAC_BINDING][OK] "
                f"reason={details.get('reason')} matched={details.get('matched_macs', [])}"
            )
        else:
            logger("[MAC_BINDING] disabled")
        return True

    logger(
        "[MAC_BINDING][FAIL] "
        f"reason={details.get('reason')} runtime_macs={details.get('runtime_macs', {})}"
    )
    return False

