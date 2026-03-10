from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]
_GLOBAL_PAUSED_CONFLICT_SERVICES: set[str] = set()
_GLOBAL_PAUSED_CONFLICT_SERVICES_LOCK = threading.Lock()


def _default_runner(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


@dataclass
class NmcliCommandError(RuntimeError):
    command: list[str]
    return_code: int
    stdout: str
    stderr: str

    def __str__(self) -> str:
        cmd = " ".join(self.command)
        return f"Command failed ({self.return_code}): {cmd} :: {self.stderr or self.stdout}"

    def to_dict(self) -> dict[str, object]:
        return {
            "command": " ".join(self.command),
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def _split_terse_line(line: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    escaped = False

    for char in line:
        if escaped:
            buffer.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ":":
            parts.append("".join(buffer))
            buffer = []
            continue
        buffer.append(char)

    if escaped:
        buffer.append("\\")
    parts.append("".join(buffer))
    return parts


def _parse_terse_rows(output: str, columns: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        values = _split_terse_line(line)
        if len(values) < len(columns):
            values.extend([""] * (len(columns) - len(values)))
        if len(values) > len(columns):
            values = values[: len(columns) - 1] + [":".join(values[len(columns) - 1 :])]
        rows.append({column: values[index] for index, column in enumerate(columns)})
    return rows


def _is_hotspot_profile_name(name: str, *, ap_ssid: str = "") -> bool:
    normalized = name.strip().lower()
    normalized_ap_ssid = ap_ssid.strip().lower()
    if not normalized:
        return False
    if normalized_ap_ssid and normalized == normalized_ap_ssid:
        return True
    return normalized.startswith("hotspot")


class WifiConfigService:
    def __init__(
        self,
        *,
        interface: str = "wlan0",
        default_health_host: str = "8.8.8.8",
        default_ap_ssid: str = "Matterhub-Setup-WhatsMatter",
        ap_password: str = "00000000",
        ap_ipv4_cidr: str = "10.42.0.1/24",
        ap_band: str = "bg",
        ap_conflict_services: Optional[list[str]] = None,
        runner: Optional[Runner] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interface = interface
        self.default_health_host = default_health_host
        self.default_ap_ssid = default_ap_ssid
        self.ap_password = ap_password
        self.ap_ipv4_cidr = ap_ipv4_cidr
        self.ap_band = ap_band.strip()
        self.ap_conflict_services = self._normalize_service_names(ap_conflict_services or [])
        self._paused_conflict_services: set[str] = set()
        self._runner: Runner = runner or _default_runner
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn

    def get_status(self) -> dict[str, object]:
        rows = _parse_terse_rows(
            self._run_nmcli(
                ["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], timeout=15
            ),
            ["DEVICE", "TYPE", "STATE", "CONNECTION"],
        )

        wifi_device = next((row for row in rows if row.get("DEVICE") == self.interface), None)
        if wifi_device is None:
            wifi_device = next((row for row in rows if row.get("TYPE") == "wifi"), {})

        active = self.get_active_wifi_connection()
        return {
            "interface": self.interface,
            "general_state": self._get_general_state(),
            "wifi_device": wifi_device or {},
            "active_connection": active,
            "current_ssid": self._get_current_ssid(),
        }

    def scan_wifi(self, *, rescan: bool = True) -> list[dict[str, object]]:
        args = [
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY,BARS,CHAN",
            "device",
            "wifi",
            "list",
            "ifname",
            self.interface,
        ]
        args.extend(["--rescan", "yes" if rescan else "no"])
        rows = _parse_terse_rows(
            self._run_nmcli(args, timeout=20),
            ["IN-USE", "SSID", "SIGNAL", "SECURITY", "BARS", "CHAN"],
        )

        merged: dict[str, dict[str, object]] = {}
        for row in rows:
            ssid = row.get("SSID", "").strip()
            if not ssid:
                continue
            try:
                signal = int((row.get("SIGNAL") or "0").strip())
            except ValueError:
                signal = 0
            candidate = {
                "ssid": ssid,
                "signal": signal,
                "security": (row.get("SECURITY") or "").strip(),
                "bars": (row.get("BARS") or "").strip(),
                "channel": (row.get("CHAN") or "").strip(),
                "in_use": (row.get("IN-USE") or "").strip() == "*",
            }
            previous = merged.get(ssid)
            if previous is None or int(previous["signal"]) < signal:
                merged[ssid] = candidate
            elif candidate["in_use"]:
                previous["in_use"] = True

        return sorted(
            merged.values(),
            key=lambda item: (bool(item.get("in_use")), int(item.get("signal", 0))),
            reverse=True,
        )

    def list_saved_connections(self) -> list[dict[str, object]]:
        active_rows = _parse_terse_rows(
            self._run_nmcli(
                ["-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                timeout=15,
            ),
            ["NAME", "UUID", "TYPE", "DEVICE"],
        )
        active_uuids = {row.get("UUID") for row in active_rows if row.get("UUID")}

        rows = _parse_terse_rows(
            self._run_nmcli(
                [
                    "-t",
                    "-f",
                    "NAME,UUID,TYPE,AUTOCONNECT,DEVICE",
                    "connection",
                    "show",
                ],
                timeout=15,
            ),
            ["NAME", "UUID", "TYPE", "AUTOCONNECT", "DEVICE"],
        )

        saved: list[dict[str, object]] = []
        for row in rows:
            if row.get("TYPE") != "802-11-wireless":
                continue
            profile_name = row.get("NAME", "")
            profile_ssid = self._get_connection_ssid(profile_name) or profile_name
            saved.append(
                {
                    "name": profile_name,
                    "ssid": profile_ssid,
                    "uuid": row.get("UUID", ""),
                    "autoconnect": (row.get("AUTOCONNECT", "").lower() == "yes"),
                    "device": row.get("DEVICE", ""),
                    "active": row.get("UUID", "") in active_uuids,
                }
            )
        return saved

    def delete_saved_connection(self, connection_name: str) -> dict[str, object]:
        if not connection_name.strip():
            raise ValueError("connection_name is required")
        self._run_nmcli(["connection", "delete", "id", connection_name], timeout=20)
        return {"deleted": connection_name}

    def activate_saved_connection(self, connection_name: str, *, timeout_seconds: int = 30) -> dict[str, object]:
        name = connection_name.strip()
        if not name:
            raise ValueError("connection_name is required")

        activated = self._activate_connection({"name": name, "uuid": "", "device": self.interface})
        if not activated:
            return {
                "success": False,
                "connection_name": name,
                "active_connection": self.get_active_wifi_connection(),
            }

        deadline = self._monotonic() + max(5, int(timeout_seconds))
        while self._monotonic() <= deadline:
            active = self.get_active_wifi_connection()
            general_state = self._get_general_state()
            if active and active.get("name") == name and general_state.startswith("connected"):
                self._resume_paused_conflicting_services()
                return {
                    "success": True,
                    "connection_name": name,
                    "active_connection": active,
                    "current_ssid": self._get_current_ssid(),
                }
            self._sleep(1)

        return {
            "success": False,
            "connection_name": name,
            "active_connection": self.get_active_wifi_connection(),
            "timeout": True,
        }

    def start_ap_mode(self, *, ssid: Optional[str] = None, password: Optional[str] = None) -> dict[str, object]:
        ap_ssid = (ssid or "").strip() or self._default_ap_ssid()
        ap_password = (password or "").strip() or self.ap_password
        if len(ap_password) < 8:
            raise ValueError("AP password must be at least 8 characters")

        self._pause_conflicting_services_for_ap()
        self._disconnect_active_wifi_before_ap(ap_ssid)

        hotspot_command = [
            "device",
            "wifi",
            "hotspot",
            "ifname",
            self.interface,
        ]
        if self.ap_band:
            hotspot_command.extend(["band", self.ap_band])
        hotspot_command.extend([
            "ssid",
            ap_ssid,
            "password",
            ap_password,
        ])

        start_error: Optional[NmcliCommandError] = None
        for attempt in range(1, 4):
            try:
                self._run_nmcli(hotspot_command, timeout=40)
                start_error = None
                break
            except NmcliCommandError as exc:
                start_error = exc
                if attempt == 1 and self._is_device_unavailable_error(exc):
                    self._prepare_device_for_ap_retry()
                    continue
                if self._is_ip_config_reservation_error(exc):
                    self._recover_hotspot_connection()
                    continue
                break
        if start_error is not None:
            self._resume_paused_conflicting_services()
            raise start_error

        connection_name = self._resolve_ap_connection_name(ap_ssid)
        try:
            self._configure_ap_ipv4_for_ap(connection_name, ap_ssid)
        except Exception:
            self._resume_paused_conflicting_services()
            raise
        gateway_ip = self._ap_gateway_ip()
        return {
            "ssid": ap_ssid,
            "interface": self.interface,
            "security": "wpa2-psk",
            "connection_name": connection_name,
            "gateway_ip": gateway_ip,
            "setup_url": f"http://{gateway_ip}:8100/local/admin/network",
        }

    def connect_wifi(
        self,
        *,
        ssid: str,
        password: Optional[str] = None,
        hidden: bool = False,
        timeout_seconds: int = 60,
        health_host: Optional[str] = None,
        rollback_on_failure: bool = True,
        ap_mode_on_failure: bool = True,
    ) -> dict[str, object]:
        target_ssid = ssid.strip()
        if not target_ssid:
            raise ValueError("ssid is required")

        previous = self.get_active_wifi_connection()
        command = ["device", "wifi", "connect", target_ssid, "ifname", self.interface]
        if password:
            command.extend(["password", password])
        if hidden:
            command.extend(["hidden", "yes"])

        connect_error: Optional[NmcliCommandError] = None
        profile_persistence_error: Optional[dict[str, object]] = None
        try:
            self._run_nmcli(command, timeout=30)
            connected_profile = self.get_active_wifi_connection() or {
                "name": target_ssid,
                "uuid": "",
                "device": self.interface,
            }
            try:
                self._ensure_system_autoconnect_profile(
                    profile=connected_profile,
                    password=password,
                )
            except NmcliCommandError as exc:
                profile_persistence_error = exc.to_dict()
        except NmcliCommandError as exc:
            connect_error = exc

        health_ok = False
        if connect_error is None:
            health_ok = self._wait_for_health(
                target_ssid=target_ssid,
                timeout_seconds=max(10, int(timeout_seconds)),
                health_host=(health_host or "").strip() or self.default_health_host,
            )

        rollback_attempted = False
        rollback_success = False
        if (connect_error or not health_ok) and rollback_on_failure and previous:
            rollback_attempted = True
            rollback_success = self._activate_connection(previous)

        ap_mode_started = False
        ap_mode_result: Optional[dict[str, object]] = None
        if (connect_error or not health_ok) and ap_mode_on_failure and not rollback_success:
            ap_mode_result = self.start_ap_mode()
            ap_mode_started = True

        if connect_error is None and health_ok:
            self._resume_paused_conflicting_services()
        elif rollback_success:
            self._resume_paused_conflicting_services()

        success = connect_error is None and health_ok
        result: dict[str, object] = {
            "success": success,
            "target_ssid": target_ssid,
            "hidden": hidden,
            "health_check_passed": health_ok,
            "rollback_attempted": rollback_attempted,
            "rollback_success": rollback_success,
            "ap_mode_started": ap_mode_started,
            "previous_connection": previous,
            "active_connection": self.get_active_wifi_connection(),
        }
        if connect_error is not None:
            result["error"] = connect_error.to_dict()
        if profile_persistence_error is not None:
            result["profile_persistence_error"] = profile_persistence_error
        if ap_mode_result is not None:
            result["ap_mode"] = ap_mode_result
        return result

    def get_active_wifi_connection(self) -> Optional[dict[str, str]]:
        rows = _parse_terse_rows(
            self._run_nmcli(
                ["-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
                timeout=10,
            ),
            ["NAME", "UUID", "TYPE", "DEVICE"],
        )
        for row in rows:
            if row.get("TYPE") == "802-11-wireless":
                return {
                    "name": row.get("NAME", ""),
                    "uuid": row.get("UUID", ""),
                    "device": row.get("DEVICE", ""),
                }
        return None

    def _activate_connection(self, previous: dict[str, str]) -> bool:
        uuid = (previous.get("uuid") or "").strip()
        name = (previous.get("name") or "").strip()
        if not uuid and not name:
            return False

        command = ["connection", "up"]
        if uuid:
            command.extend(["uuid", uuid])
        else:
            command.extend(["id", name])
        command.extend(["ifname", self.interface])

        try:
            self._run_nmcli(command, timeout=30)
            try:
                self._ensure_system_autoconnect_profile(
                    profile=previous,
                    password=None,
                )
            except NmcliCommandError:
                # Activation success is more important than persistence tuning.
                # Keep best-effort behavior here.
                pass
            return True
        except NmcliCommandError:
            return False

    def _wait_for_health(self, *, target_ssid: str, timeout_seconds: int, health_host: str) -> bool:
        deadline = self._monotonic() + timeout_seconds
        while self._monotonic() <= deadline:
            active = self.get_active_wifi_connection()
            current_ssid = self._get_current_ssid()
            general_state = self._get_general_state()

            on_target = bool(current_ssid and current_ssid == target_ssid)
            if not on_target and active:
                on_target = active.get("name") == target_ssid

            if on_target and general_state.startswith("connected"):
                if not health_host or self._ping_host(health_host):
                    return True
            self._sleep(2)
        return False

    def _get_current_ssid(self) -> Optional[str]:
        rows = _parse_terse_rows(
            self._run_nmcli(
                ["-t", "-f", "IN-USE,SSID", "device", "wifi", "list", "ifname", self.interface],
                timeout=10,
            ),
            ["IN-USE", "SSID"],
        )
        active = next((row for row in rows if row.get("IN-USE", "").strip() == "*"), None)
        if active and active.get("SSID"):
            return active["SSID"].strip()
        return None

    def _get_general_state(self) -> str:
        output = self._run_nmcli(["-t", "-f", "STATE", "general", "status"], timeout=10)
        return output.splitlines()[0].strip() if output else ""

    def _ping_host(self, host: str) -> bool:
        result = self._runner(["ping", "-c", "1", "-W", "1", host], 5)
        return result.returncode == 0

    def _run_nmcli(self, args: list[str], *, timeout: int) -> str:
        command = ["nmcli", *args]
        result = self._runner(command, timeout)
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            raise NmcliCommandError(
                command=command,
                return_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return stdout

    def _configure_ap_ipv4(self, connection_name: str) -> None:
        self._run_nmcli(
            [
                "connection",
                "modify",
                "id",
                connection_name,
                "ipv4.method",
                "shared",
                "ipv4.addresses",
                self.ap_ipv4_cidr,
                "ipv6.method",
                "ignore",
            ],
            timeout=20,
        )
        self._run_nmcli(
            ["connection", "up", "id", connection_name, "ifname", self.interface],
            timeout=30,
        )

    def _configure_ap_ipv4_for_ap(self, connection_name: str, ap_ssid: str) -> None:
        candidates: list[str] = []
        for candidate in [connection_name, *self._list_hotspot_connection_names(ap_ssid), "Hotspot", ap_ssid]:
            normalized = candidate.strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        last_error: Optional[NmcliCommandError] = None
        for candidate in candidates:
            try:
                self._configure_ap_ipv4(candidate)
                return
            except NmcliCommandError as exc:
                last_error = exc
                if self._is_unknown_connection_error(exc):
                    continue
                raise

        if last_error is not None:
            raise last_error

    def _resolve_ap_connection_name(self, ap_ssid: str) -> str:
        active = self.get_active_wifi_connection()
        active_name = str((active or {}).get("name") or "").strip()
        if _is_hotspot_profile_name(active_name, ap_ssid=ap_ssid):
            return active_name
        hotspot_names = self._list_hotspot_connection_names(ap_ssid)
        if hotspot_names:
            return hotspot_names[0]
        return "Hotspot"

    def _ap_gateway_ip(self) -> str:
        cidr = self.ap_ipv4_cidr.strip()
        if "/" in cidr:
            return cidr.split("/", 1)[0]
        return cidr

    def _default_ap_ssid(self) -> str:
        return self.default_ap_ssid

    def _get_connection_ssid(self, connection_name: str) -> Optional[str]:
        normalized_name = connection_name.strip()
        if not normalized_name:
            return None
        try:
            output = self._run_nmcli(
                ["-g", "802-11-wireless.ssid", "connection", "show", "id", normalized_name],
                timeout=10,
            )
        except NmcliCommandError:
            return None

        for line in output.splitlines():
            ssid = line.strip()
            if ssid:
                return ssid
        return None

    def _list_hotspot_connection_names(self, ap_ssid: str) -> list[str]:
        try:
            rows = _parse_terse_rows(
                self._run_nmcli(
                    ["-t", "-f", "NAME,TYPE,TIMESTAMP", "connection", "show"],
                    timeout=10,
                ),
                ["NAME", "TYPE", "TIMESTAMP"],
            )
        except NmcliCommandError:
            return []

        candidates: list[tuple[int, str]] = []
        for row in rows:
            if row.get("TYPE") != "802-11-wireless":
                continue
            name = str(row.get("NAME") or "").strip()
            if not _is_hotspot_profile_name(name, ap_ssid=ap_ssid):
                continue
            try:
                timestamp = int(str(row.get("TIMESTAMP") or "0").strip() or "0")
            except ValueError:
                timestamp = 0
            candidates.append((timestamp, name))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return [name for _, name in candidates]

    def _prepare_device_for_ap_retry(self) -> None:
        self._run_nmcli_best_effort(["radio", "wifi", "on"], timeout=10)
        self._run_nmcli_best_effort(
            ["device", "set", self.interface, "managed", "yes"],
            timeout=10,
        )
        self._run_nmcli_best_effort(["device", "disconnect", self.interface], timeout=15)

    def _recover_hotspot_connection(self) -> None:
        self._run_nmcli_best_effort(["connection", "down", "id", "Hotspot"], timeout=15)
        self._run_nmcli_best_effort(["connection", "delete", "id", "Hotspot"], timeout=15)

    def _disconnect_active_wifi_before_ap(self, ap_ssid: str) -> None:
        active = self.get_active_wifi_connection()
        active_name = str((active or {}).get("name") or "").strip()
        if not active_name or _is_hotspot_profile_name(active_name, ap_ssid=ap_ssid):
            return
        self._run_nmcli_best_effort(["connection", "down", "id", active_name], timeout=20)
        self._run_nmcli_best_effort(["device", "disconnect", self.interface], timeout=15)

    def _is_device_unavailable_error(self, error: NmcliCommandError) -> bool:
        text = f"{error.stderr}\n{error.stdout}".lower()
        return "device is not available" in text or "device is unavailable" in text

    def _is_ip_config_reservation_error(self, error: NmcliCommandError) -> bool:
        text = f"{error.stderr}\n{error.stdout}".lower()
        return "ip configuration could not be reserved" in text

    def _is_unknown_connection_error(self, error: NmcliCommandError) -> bool:
        text = f"{error.stderr}\n{error.stdout}".lower()
        return "unknown connection" in text

    def _run_nmcli_best_effort(self, args: list[str], *, timeout: int) -> bool:
        try:
            self._run_nmcli(args, timeout=timeout)
            return True
        except NmcliCommandError:
            return False

    def _pause_conflicting_services_for_ap(self) -> None:
        for service_name in self.ap_conflict_services:
            if service_name in self._paused_conflict_services:
                continue
            state = self._run_command_best_effort(
                ["sudo", "-n", "systemctl", "is-active", service_name],
                timeout=10,
            )
            if state not in {"active", "activating"}:
                continue
            if self._run_command_success(
                ["sudo", "-n", "systemctl", "stop", service_name],
                timeout=20,
            ):
                self._paused_conflict_services.add(service_name)
                with _GLOBAL_PAUSED_CONFLICT_SERVICES_LOCK:
                    _GLOBAL_PAUSED_CONFLICT_SERVICES.add(service_name)

    def _resume_paused_conflicting_services(self) -> None:
        paused_services = set(self._paused_conflict_services)
        with _GLOBAL_PAUSED_CONFLICT_SERVICES_LOCK:
            paused_services.update(_GLOBAL_PAUSED_CONFLICT_SERVICES)
            for service_name in paused_services:
                _GLOBAL_PAUSED_CONFLICT_SERVICES.discard(service_name)
        self._paused_conflict_services.clear()
        if not paused_services:
            return
        for service_name in sorted(paused_services):
            self._run_command_success(
                ["sudo", "-n", "systemctl", "start", service_name],
                timeout=20,
            )

    def _run_command_success(self, command: list[str], *, timeout: int) -> bool:
        result = self._runner(command, timeout)
        if result.returncode != 0:
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            print(
                "[WIFI][CMD] failed "
                f"cmd={' '.join(command)} rc={result.returncode} "
                f"stdout={stdout} stderr={stderr}"
            )
        return result.returncode == 0

    def _run_command_best_effort(self, command: list[str], *, timeout: int) -> str:
        result = self._runner(command, timeout)
        if result.returncode != 0:
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            print(
                "[WIFI][CMD] best-effort failed "
                f"cmd={' '.join(command)} rc={result.returncode} "
                f"stdout={stdout} stderr={stderr}"
            )
            return ""
        return (result.stdout or "").strip()

    def _normalize_service_names(self, service_names: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in service_names:
            service_name = item.strip()
            if not service_name:
                continue
            if "." not in service_name:
                service_name = f"{service_name}.service"
            if service_name not in normalized:
                normalized.append(service_name)
        return normalized

    def _ensure_system_autoconnect_profile(
        self,
        *,
        profile: dict[str, str],
        password: Optional[str],
    ) -> None:
        uuid = (profile.get("uuid") or "").strip()
        name = (profile.get("name") or "").strip()
        if not uuid and not name:
            return

        selector = ["uuid", uuid] if uuid else ["id", name]
        self._run_nmcli(
            [
                "connection",
                "modify",
                *selector,
                "connection.permissions",
                "",
                "connection.autoconnect",
                "yes",
            ],
            timeout=20,
        )

        normalized_password = (password or "").strip()
        if normalized_password:
            self._run_nmcli(
                [
                    "connection",
                    "modify",
                    *selector,
                    "802-11-wireless-security.psk-flags",
                    "0",
                    "802-11-wireless-security.psk",
                    normalized_password,
                ],
                timeout=20,
            )
