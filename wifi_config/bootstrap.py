from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

from .service import WifiConfigService
from .state import ProvisionStateStore, get_provision_state_store


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


def _as_int(value: Optional[str], default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _create_wifi_service() -> WifiConfigService:
    ap_conflict_services = [
        item.strip()
        for item in os.environ.get("WIFI_AP_CONFLICT_SERVICES", "named.service").split(",")
        if item.strip()
    ]
    return WifiConfigService(
        interface=os.environ.get("WIFI_INTERFACE", "wlan0"),
        default_health_host=os.environ.get("WIFI_HEALTH_HOST", "8.8.8.8"),
        default_ap_ssid=os.environ.get("WIFI_AP_SSID", "Matterhub-Setup-WhatsMatter"),
        ap_password=os.environ.get("WIFI_AP_PASSWORD", "00000000"),
        ap_ipv4_cidr=os.environ.get("WIFI_AP_IPV4_CIDR", "10.42.0.1/24"),
        ap_band=os.environ.get("WIFI_AP_BAND", "bg"),
        ap_conflict_services=ap_conflict_services,
    )


def _pick_known_network_candidate(
    service: WifiConfigService,
    *,
    configured_ap_ssid: str,
    logger: Logger,
) -> Optional[dict[str, str]]:
    try:
        saved_connections = service.list_saved_connections()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger(f"[WIFI][WATCHDOG] failed to read saved connections: {type(exc).__name__}: {exc}")
        return None
    if not isinstance(saved_connections, list):
        logger("[WIFI][WATCHDOG] invalid saved connections payload")
        return None

    candidates: list[dict[str, str]] = []
    for item in saved_connections:
        profile_name = str(item.get("name") or "").strip()
        profile_ssid = str(item.get("ssid") or profile_name).strip()
        if not profile_name:
            continue
        if _is_ap_profile(profile_name, configured_ap_ssid=configured_ap_ssid):
            continue
        if _is_ap_profile(profile_ssid, configured_ap_ssid=configured_ap_ssid):
            continue
        candidates.append(
            {
                "profile_name": profile_name,
                "ssid": profile_ssid,
                "autoconnect": "1" if bool(item.get("autoconnect")) else "0",
            }
        )

    if not candidates:
        return None

    try:
        scanned_networks = service.scan_wifi(rescan=True)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger(f"[WIFI][WATCHDOG] scan failed during auto reconnect: {type(exc).__name__}: {exc}")
        scanned_networks = []

    if scanned_networks:
        visible_ssids = {str(item.get("ssid") or "").strip() for item in scanned_networks}
        for candidate in candidates:
            if candidate["ssid"] and candidate["ssid"] in visible_ssids:
                return candidate

    for candidate in candidates:
        if candidate["autoconnect"] == "1":
            return candidate
    return candidates[0] if candidates else None


def _is_ap_profile(name: str, *, configured_ap_ssid: str) -> bool:
    normalized = name.strip().lower()
    configured = configured_ap_ssid.strip().lower()
    if not normalized:
        return False
    if configured and normalized == configured:
        return True
    return normalized.startswith("hotspot")


def ensure_bootstrap_ap(
    service: Optional[WifiConfigService] = None,
    state_store: Optional[ProvisionStateStore] = None,
    *,
    logger: Logger = print,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Ensure local setup access by starting AP mode when Wi-Fi is not connected."""
    if not _as_bool(os.environ.get("WIFI_AUTO_AP_ON_BOOT"), True):
        return {"enabled": False, "started": False, "reason": "disabled"}

    wifi_service = service or _create_wifi_service()
    provision_state = state_store or get_provision_state_store()
    provision_state.set_state("BOOTING", reason="bootstrap_begin")

    startup_grace_seconds = _as_int(
        os.environ.get("WIFI_BOOTSTRAP_STARTUP_GRACE_SECONDS"),
        45,
        min_value=0,
        max_value=300,
    )
    startup_check_interval_seconds = _as_int(
        os.environ.get("WIFI_BOOTSTRAP_STARTUP_CHECK_INTERVAL_SECONDS"),
        2,
        min_value=1,
        max_value=30,
    )

    status: dict[str, Any] = {}
    if startup_grace_seconds > 0:
        logger(
            f"[WIFI][BOOTSTRAP] startup grace wait begin seconds={startup_grace_seconds}"
        )
    deadline = monotonic_fn() + startup_grace_seconds
    while True:
        try:
            status = wifi_service.get_status()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger(f"[WIFI][BOOTSTRAP] failed to read status: {type(exc).__name__}: {exc}")
            status = {}

        general_state = str(status.get("general_state") or "")
        current_ssid = str(status.get("current_ssid") or "").strip()
        if general_state.startswith("connected") and current_ssid:
            logger(
                f"[WIFI][BOOTSTRAP] skip AP mode: connected ssid={current_ssid} state={general_state}"
            )
            provision_state.set_state(
                "STA_CONNECTED",
                reason="bootstrap_detected_connected",
                details={"current_ssid": current_ssid},
            )
            return {
                "enabled": True,
                "started": False,
                "reason": "already_connected",
                "status": status,
            }

        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            break
        sleep_fn(min(startup_check_interval_seconds, remaining))

    candidate = _pick_known_network_candidate(
        wifi_service,
        configured_ap_ssid=(os.environ.get("WIFI_BOOTSTRAP_AP_SSID") or "").strip()
        or wifi_service.default_ap_ssid,
        logger=logger,
    )
    if candidate:
        profile_name = candidate["profile_name"]
        try:
            provision_state.set_state(
                "STA_CONNECTING",
                reason="bootstrap_known_network_reconnect",
                details={"profile_name": profile_name},
            )
            reconnect_result = wifi_service.activate_saved_connection(
                profile_name,
                timeout_seconds=20,
            )
            if reconnect_result.get("success"):
                provision_state.set_state(
                    "STA_CONNECTED",
                    reason="bootstrap_known_network_reconnect_success",
                    details={"profile_name": profile_name},
                )
                logger(
                    "[WIFI][BOOTSTRAP] known network reconnect success "
                    f"profile={profile_name} ssid={reconnect_result.get('current_ssid')}"
                )
                return {
                    "enabled": True,
                    "started": False,
                    "reason": "known_network_reconnected",
                    "status": status,
                    "reconnect": reconnect_result,
                }
            logger(
                "[WIFI][BOOTSTRAP] known network reconnect failed "
                f"profile={profile_name}"
            )
            provision_state.set_state(
                "STA_FAILED",
                reason="bootstrap_known_network_reconnect_failed",
                details={"profile_name": profile_name},
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger(
                "[WIFI][BOOTSTRAP] known network reconnect error "
                f"profile={profile_name} {type(exc).__name__}: {exc}"
            )
            provision_state.set_state(
                "STA_FAILED",
                reason="bootstrap_known_network_reconnect_error",
                details={"profile_name": profile_name},
            )

    ssid = (os.environ.get("WIFI_BOOTSTRAP_AP_SSID") or "").strip() or None
    password = (os.environ.get("WIFI_BOOTSTRAP_AP_PASSWORD") or "").strip() or None

    try:
        provision_state.set_state(
            "AP_STARTING",
            reason="bootstrap_start_ap",
            details={"requested_ssid": ssid or ""},
        )
        ap_result = wifi_service.start_ap_mode(ssid=ssid, password=password)
        provision_state.set_state(
            "AP_MODE",
            reason="bootstrap_ap_started",
            details={"ssid": str(ap_result.get("ssid") or "")},
        )
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
        provision_state.set_state(
            "STA_FAILED",
            reason="bootstrap_ap_start_failed",
            details={"error": type(exc).__name__},
        )
        return {
            "enabled": True,
            "started": False,
            "reason": "fallback_ap_failed",
            "status": status,
            "error": str(exc),
        }


def watch_disconnection_and_start_ap(
    service: Optional[WifiConfigService] = None,
    state_store: Optional[ProvisionStateStore] = None,
    *,
    logger: Logger = print,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    max_checks: Optional[int] = None,
) -> None:
    """Watch Wi-Fi state and start AP mode when disconnected for a grace period."""
    if not _as_bool(os.environ.get("WIFI_AUTO_AP_ON_DISCONNECT"), True):
        logger("[WIFI][WATCHDOG] disabled by WIFI_AUTO_AP_ON_DISCONNECT")
        return

    check_interval = _as_int(
        os.environ.get("WIFI_AP_WATCH_INTERVAL_SECONDS"),
        5,
        min_value=2,
        max_value=60,
    )
    disconnect_grace = _as_int(
        os.environ.get("WIFI_AP_DISCONNECT_GRACE_SECONDS"),
        20,
        min_value=5,
        max_value=300,
    )
    auto_reconnect_enabled = _as_bool(
        os.environ.get("WIFI_AP_AUTO_RECONNECT_ENABLED"),
        True,
    )
    auto_reconnect_interval = _as_int(
        os.environ.get("WIFI_AP_AUTO_RECONNECT_INTERVAL_SECONDS"),
        15,
        min_value=5,
        max_value=300,
    )
    auto_reconnect_timeout = _as_int(
        os.environ.get("WIFI_AP_AUTO_RECONNECT_TIMEOUT_SECONDS"),
        20,
        min_value=5,
        max_value=180,
    )
    auto_reconnect_hold_seconds = _as_int(
        os.environ.get("WIFI_AP_AUTO_RECONNECT_HOLD_SECONDS"),
        45,
        min_value=0,
        max_value=600,
    )

    wifi_service = service or _create_wifi_service()
    provision_state = state_store or get_provision_state_store()
    bootstrap_ap_ssid = (os.environ.get("WIFI_BOOTSTRAP_AP_SSID") or "").strip() or None
    bootstrap_ap_password = (os.environ.get("WIFI_BOOTSTRAP_AP_PASSWORD") or "").strip() or None
    configured_ap_ssid = bootstrap_ap_ssid or wifi_service.default_ap_ssid

    disconnected_since: Optional[float] = None
    ap_active_since: Optional[float] = None
    next_auto_reconnect_check_at = 0.0
    checks = 0

    logger(
        "[WIFI][WATCHDOG] start "
        f"interval={check_interval}s grace={disconnect_grace}s ap_ssid={configured_ap_ssid} "
        f"auto_reconnect={'on' if auto_reconnect_enabled else 'off'}"
    )

    while True:
        if max_checks is not None and checks >= max_checks:
            return
        checks += 1

        try:
            status = wifi_service.get_status()
            general_state = str(status.get("general_state") or "")
            current_ssid = str(status.get("current_ssid") or "").strip()
            active = status.get("active_connection") or {}
            active_name = str(active.get("name") or "").strip()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger(f"[WIFI][WATCHDOG] status read failed: {type(exc).__name__}: {exc}")
            sleep_fn(check_interval)
            continue

        is_ap_active = bool(
            (current_ssid and current_ssid == configured_ap_ssid)
            or _is_ap_profile(active_name, configured_ap_ssid=configured_ap_ssid)
        )
        is_connected = general_state.startswith("connected") and bool(current_ssid or active_name)

        if is_connected or is_ap_active:
            disconnected_since = None
            if is_ap_active:
                if ap_active_since is None:
                    ap_active_since = monotonic_fn()
                provision_state.set_state(
                    "AP_MODE",
                    reason="watchdog_ap_active",
                    details={"ssid": current_ssid or active_name},
                )
            else:
                ap_active_since = None
                provision_state.set_state(
                    "STA_CONNECTED",
                    reason="watchdog_sta_connected",
                    details={"ssid": current_ssid or active_name},
                )
            if is_ap_active and auto_reconnect_enabled:
                now = monotonic_fn()
                if ap_active_since is not None and now < ap_active_since + auto_reconnect_hold_seconds:
                    sleep_fn(check_interval)
                    continue
                if now >= next_auto_reconnect_check_at:
                    next_auto_reconnect_check_at = now + auto_reconnect_interval
                    candidate = _pick_known_network_candidate(
                        wifi_service,
                        configured_ap_ssid=configured_ap_ssid,
                        logger=logger,
                    )
                    if candidate:
                        profile_name = candidate["profile_name"]
                        logger(
                            "[WIFI][WATCHDOG] AP active; trying known network "
                            f"profile={profile_name} ssid={candidate.get('ssid')}"
                        )
                        try:
                            provision_state.set_state(
                                "STA_CONNECTING",
                                reason="watchdog_ap_active_known_network_reconnect",
                                details={"profile_name": profile_name},
                            )
                            reconnect_result = wifi_service.activate_saved_connection(
                                profile_name,
                                timeout_seconds=auto_reconnect_timeout,
                            )
                            if reconnect_result.get("success"):
                                provision_state.set_state(
                                    "STA_CONNECTED",
                                    reason="watchdog_ap_active_reconnect_success",
                                    details={"profile_name": profile_name},
                                )
                                logger(
                                    "[WIFI][WATCHDOG] auto reconnect success "
                                    f"profile={profile_name} ssid={reconnect_result.get('current_ssid')}"
                                )
                            else:
                                provision_state.set_state(
                                    "STA_FAILED",
                                    reason="watchdog_ap_active_reconnect_failed",
                                    details={"profile_name": profile_name},
                                )
                                logger(
                                    "[WIFI][WATCHDOG] auto reconnect failed "
                                    f"profile={profile_name}"
                                )
                        except Exception as exc:
                            provision_state.set_state(
                                "STA_FAILED",
                                reason="watchdog_ap_active_reconnect_error",
                                details={"profile_name": profile_name},
                            )
                            logger(
                                "[WIFI][WATCHDOG] auto reconnect error "
                                f"profile={profile_name} {type(exc).__name__}: {exc}"
                            )
            sleep_fn(check_interval)
            continue

        ap_active_since = None
        if auto_reconnect_enabled:
            now = monotonic_fn()
            if now >= next_auto_reconnect_check_at:
                next_auto_reconnect_check_at = now + auto_reconnect_interval
                candidate = _pick_known_network_candidate(
                    wifi_service,
                    configured_ap_ssid=configured_ap_ssid,
                    logger=logger,
                )
                if candidate:
                    profile_name = candidate["profile_name"]
                    logger(
                        "[WIFI][WATCHDOG] disconnected; trying known network "
                        f"profile={profile_name} ssid={candidate.get('ssid')}"
                    )
                    try:
                        provision_state.set_state(
                            "STA_CONNECTING",
                            reason="watchdog_disconnected_known_network_reconnect",
                            details={"profile_name": profile_name},
                        )
                        reconnect_result = wifi_service.activate_saved_connection(
                            profile_name,
                            timeout_seconds=auto_reconnect_timeout,
                        )
                        if reconnect_result.get("success"):
                            provision_state.set_state(
                                "STA_CONNECTED",
                                reason="watchdog_disconnected_reconnect_success",
                                details={"profile_name": profile_name},
                            )
                            logger(
                                "[WIFI][WATCHDOG] reconnect success while disconnected "
                                f"profile={profile_name} ssid={reconnect_result.get('current_ssid')}"
                            )
                            disconnected_since = None
                            sleep_fn(check_interval)
                            continue
                        logger(
                            "[WIFI][WATCHDOG] reconnect failed while disconnected "
                            f"profile={profile_name}"
                        )
                        provision_state.set_state(
                            "STA_FAILED",
                            reason="watchdog_disconnected_reconnect_failed",
                            details={"profile_name": profile_name},
                        )
                    except Exception as exc:
                        provision_state.set_state(
                            "STA_FAILED",
                            reason="watchdog_disconnected_reconnect_error",
                            details={"profile_name": profile_name},
                        )
                        logger(
                            "[WIFI][WATCHDOG] reconnect error while disconnected "
                            f"profile={profile_name} {type(exc).__name__}: {exc}"
                        )

        if disconnected_since is None:
            disconnected_since = monotonic_fn()
            provision_state.set_state(
                "STA_FAILED",
                reason="watchdog_disconnected_detected",
                details={"state": general_state},
            )
            logger(
                "[WIFI][WATCHDOG] disconnected detected "
                f"state={general_state or '(empty)'}; waiting {disconnect_grace}s"
            )
            sleep_fn(check_interval)
            continue

        elapsed = monotonic_fn() - disconnected_since
        if elapsed < disconnect_grace:
            sleep_fn(check_interval)
            continue

        try:
            provision_state.set_state(
                "AP_STARTING",
                reason="watchdog_start_ap_after_grace",
                details={"grace_seconds": disconnect_grace},
            )
            result = wifi_service.start_ap_mode(ssid=bootstrap_ap_ssid, password=bootstrap_ap_password)
            provision_state.set_state(
                "AP_MODE",
                reason="watchdog_ap_started",
                details={"ssid": str(result.get("ssid") or "")},
            )
            logger(
                "[WIFI][WATCHDOG] AP mode started "
                f"ssid={result.get('ssid')} gateway={result.get('gateway_ip')}"
            )
        except Exception as exc:
            provision_state.set_state(
                "STA_FAILED",
                reason="watchdog_ap_start_failed",
                details={"error": type(exc).__name__},
            )
            logger(f"[WIFI][WATCHDOG] failed to start AP mode: {type(exc).__name__}: {exc}")
        finally:
            disconnected_since = monotonic_fn()

        sleep_fn(check_interval)
