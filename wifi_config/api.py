from __future__ import annotations

import os
from typing import Any, Optional

from flask import Blueprint, jsonify, render_template, request

from .service import NmcliCommandError, WifiConfigService
from .state import ProvisionStateStore, get_provision_state_store


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _parse_timeout(value: Any, default: int = 60) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = default
    return max(10, min(timeout, 180))


def create_wifi_blueprint(
    service: Optional[WifiConfigService] = None,
    state_store: Optional[ProvisionStateStore] = None,
) -> Blueprint:
    wifi_service = service or WifiConfigService(
        interface=os.environ.get("WIFI_INTERFACE", "wlan0"),
        default_health_host=os.environ.get("WIFI_HEALTH_HOST", "8.8.8.8"),
        default_ap_ssid=os.environ.get("WIFI_AP_SSID", "Matterhub-Setup-WhatsMatter"),
        ap_password=os.environ.get("WIFI_AP_PASSWORD", "matterhub1234"),
        ap_ipv4_cidr=os.environ.get("WIFI_AP_IPV4_CIDR", "10.42.0.1/24"),
    )
    provision_state = state_store or get_provision_state_store()
    wifi_bp = Blueprint("wifi_admin", __name__)

    @wifi_bp.get("/local/admin/network")
    def wifi_admin_page():
        return render_template("wifi_admin.html", interface=wifi_service.interface)

    @wifi_bp.get("/local/admin/network/status")
    def network_status():
        try:
            data = wifi_service.get_status()
            data["provision_state"] = provision_state.snapshot()
            return jsonify({"ok": True, "data": data})
        except NmcliCommandError as exc:
            return jsonify({"ok": False, "error": exc.to_dict()}), 500
        except Exception as exc:
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 500

    @wifi_bp.get("/local/admin/network/wifi/scan")
    def wifi_scan():
        rescan = _as_bool(request.args.get("rescan"), True)
        try:
            return jsonify({"ok": True, "data": wifi_service.scan_wifi(rescan=rescan)})
        except NmcliCommandError as exc:
            return jsonify({"ok": False, "error": exc.to_dict()}), 500
        except Exception as exc:
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 500

    @wifi_bp.post("/local/admin/network/wifi/connect")
    def wifi_connect():
        payload = request.get_json(silent=True) or {}
        ssid = str(payload.get("ssid", "")).strip()
        if not ssid:
            return jsonify({"ok": False, "error": {"message": "ssid is required"}}), 400

        password = payload.get("password")
        if password is not None:
            password = str(password).strip() or None

        try:
            provision_state.set_state(
                "STA_CONNECTING",
                reason="user_submit_wifi",
                details={"target_ssid": ssid},
            )
            result = wifi_service.connect_wifi(
                ssid=ssid,
                password=password,
                hidden=_as_bool(payload.get("hidden"), False),
                timeout_seconds=_parse_timeout(payload.get("timeout_seconds"), 60),
                health_host=str(payload.get("health_host", "")).strip() or None,
                rollback_on_failure=_as_bool(payload.get("rollback_on_failure"), True),
                ap_mode_on_failure=_as_bool(payload.get("ap_mode_on_failure"), True),
            )
            if result.get("success"):
                provision_state.set_state(
                    "STA_CONNECTED",
                    reason="user_submit_wifi_success",
                    details={"target_ssid": ssid},
                )
            else:
                provision_state.set_state(
                    "STA_FAILED",
                    reason="user_submit_wifi_failed",
                    details={"target_ssid": ssid},
                )
                if result.get("ap_mode_started"):
                    provision_state.set_state(
                        "AP_MODE",
                        reason="ap_fallback_started_after_connect_failure",
                        details={"target_ssid": ssid},
                    )
            status_code = 200 if result.get("success") else 502
            return jsonify({"ok": bool(result.get("success")), "data": result}), status_code
        except ValueError as exc:
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 400
        except NmcliCommandError as exc:
            provision_state.set_state(
                "STA_FAILED",
                reason="nmcli_error_during_connect",
                details={"target_ssid": ssid, "return_code": exc.return_code},
            )
            return jsonify({"ok": False, "error": exc.to_dict()}), 500
        except Exception as exc:
            provision_state.set_state(
                "STA_FAILED",
                reason="unexpected_error_during_connect",
                details={"target_ssid": ssid},
            )
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 500

    @wifi_bp.get("/local/admin/network/wifi/saved")
    def wifi_saved():
        try:
            return jsonify({"ok": True, "data": wifi_service.list_saved_connections()})
        except NmcliCommandError as exc:
            return jsonify({"ok": False, "error": exc.to_dict()}), 500
        except Exception as exc:
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 500

    @wifi_bp.delete("/local/admin/network/wifi/saved/<path:connection_name>")
    def wifi_saved_delete(connection_name: str):
        try:
            return jsonify(
                {"ok": True, "data": wifi_service.delete_saved_connection(connection_name)}
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 400
        except NmcliCommandError as exc:
            return jsonify({"ok": False, "error": exc.to_dict()}), 500
        except Exception as exc:
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 500

    @wifi_bp.post("/local/admin/network/recovery/ap-mode")
    def network_recovery_ap_mode():
        payload = request.get_json(silent=True) or {}
        ssid = str(payload.get("ssid", "")).strip() or None
        password = str(payload.get("password", "")).strip() or None
        try:
            provision_state.set_state(
                "AP_STARTING",
                reason="manual_recovery_request",
                details={"requested_ssid": ssid or ""},
            )
            result = wifi_service.start_ap_mode(ssid=ssid, password=password)
            provision_state.set_state(
                "AP_MODE",
                reason="manual_recovery_started",
                details={"ssid": str(result.get("ssid") or "")},
            )
            return jsonify({"ok": True, "data": result})
        except ValueError as exc:
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 400
        except NmcliCommandError as exc:
            provision_state.set_state(
                "STA_FAILED",
                reason="nmcli_error_during_ap_recovery",
                details={"return_code": exc.return_code},
            )
            return jsonify({"ok": False, "error": exc.to_dict()}), 500
        except Exception as exc:
            provision_state.set_state(
                "STA_FAILED",
                reason="unexpected_error_during_ap_recovery",
            )
            return jsonify({"ok": False, "error": {"message": str(exc)}}), 500

    return wifi_bp
