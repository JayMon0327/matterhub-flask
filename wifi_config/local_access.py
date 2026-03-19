from __future__ import annotations

import os
import re


DEFAULT_LOCAL_HOSTNAME = "matterhub-setup-whatsmatter"
DEFAULT_LOCAL_SERVICE_NAME = "MatterHub Wi-Fi Setup"
DEFAULT_LOCAL_HTTP_PORT = 8100
DEFAULT_LOCAL_SETUP_PATH = "/local/admin/network"


def normalize_local_hostname(value: str | None, fallback: str = DEFAULT_LOCAL_HOSTNAME) -> str:
    candidate = (value or "").strip().lower()
    candidate = candidate.replace("_", "-")
    candidate = re.sub(r"[^a-z0-9-]+", "-", candidate)
    candidate = re.sub(r"-{2,}", "-", candidate).strip("-")
    if not candidate:
        candidate = fallback
    if len(candidate) > 63:
        candidate = candidate[:63].rstrip("-")
    if not candidate or not candidate[0].isalnum() or not candidate[-1].isalnum():
        return fallback
    return candidate


def get_local_hostname() -> str:
    return normalize_local_hostname(os.environ.get("MATTERHUB_LOCAL_HOSTNAME"))


def get_local_service_name() -> str:
    value = (os.environ.get("MATTERHUB_LOCAL_SERVICE_NAME") or "").strip()
    return value or DEFAULT_LOCAL_SERVICE_NAME


def get_local_http_port() -> int:
    try:
        port = int(os.environ.get("MATTERHUB_LOCAL_HTTP_PORT", str(DEFAULT_LOCAL_HTTP_PORT)))
    except (TypeError, ValueError):
        port = DEFAULT_LOCAL_HTTP_PORT
    return max(1, min(port, 65535))


def get_local_setup_path() -> str:
    value = (os.environ.get("MATTERHUB_LOCAL_SETUP_PATH") or "").strip()
    if not value:
        return DEFAULT_LOCAL_SETUP_PATH
    if not value.startswith("/"):
        return f"/{value}"
    return value


def build_local_access_summary() -> dict[str, object]:
    hostname = get_local_hostname()
    fqdn = f"{hostname}.local"
    port = get_local_http_port()
    path = get_local_setup_path()
    base_url = f"http://{fqdn}:{port}"
    return {
        "hostname": hostname,
        "fqdn": fqdn,
        "port": port,
        "path": path,
        "setup_url": f"{base_url}{path}",
        "service_name": get_local_service_name(),
    }
