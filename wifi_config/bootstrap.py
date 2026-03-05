from __future__ import annotations

import os
from typing import Any, Callable, Optional

from .service import WifiConfigService


Logger = Callable[[str], None]


def _as_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def ensure_bootstrap_ap(
    service: Optional[WifiConfigService] = None,
    *,
    logger: Logger = print,
) -> dict[str, Any]:
    """Ensure local setup access by starting AP mode when Wi-Fi is not connected."""
    if not _as_bool(os.environ.get("WIFI_AUTO_AP_ON_BOOT"), True):
        return {"enabled": False, "started": False, "reason": "disabled"}

    wifi_service = service or WifiConfigService(
        interface=os.environ.get("WIFI_INTERFACE", "wlan0"),
        default_health_host=os.environ.get("WIFI_HEALTH_HOST", "8.8.8.8"),
        default_ap_ssid=os.environ.get("WIFI_AP_SSID", "Matterhub-Setup-WhatsMatter"),
        ap_password=os.environ.get("WIFI_AP_PASSWORD", "matterhub1234"),
        ap_ipv4_cidr=os.environ.get("WIFI_AP_IPV4_CIDR", "10.42.0.1/24"),
    )

    status: dict[str, Any] = {}
    try:
        status = wifi_service.get_status()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger(f"[WIFI][BOOTSTRAP] failed to read status: {type(exc).__name__}: {exc}")

    general_state = str(status.get("general_state") or "")
    current_ssid = str(status.get("current_ssid") or "").strip()
    if general_state.startswith("connected") and current_ssid:
        logger(
            f"[WIFI][BOOTSTRAP] skip AP mode: connected ssid={current_ssid} state={general_state}"
        )
        return {
            "enabled": True,
            "started": False,
            "reason": "already_connected",
            "status": status,
        }

    ssid = (os.environ.get("WIFI_BOOTSTRAP_AP_SSID") or "").strip() or None
    password = (os.environ.get("WIFI_BOOTSTRAP_AP_PASSWORD") or "").strip() or None

    try:
        ap_result = wifi_service.start_ap_mode(ssid=ssid, password=password)
        logger(
            f"[WIFI][BOOTSTRAP] AP mode started ssid={ap_result.get('ssid')} interface={ap_result.get('interface')}"
        )
        return {
            "enabled": True,
            "started": True,
            "reason": "fallback_ap_started",
            "status": status,
            "ap_mode": ap_result,
        }
    except Exception as exc:
        logger(f"[WIFI][BOOTSTRAP] failed to start AP mode: {type(exc).__name__}: {exc}")
        return {
            "enabled": True,
            "started": False,
            "reason": "fallback_ap_failed",
            "status": status,
            "error": str(exc),
        }
