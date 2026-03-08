from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


ALLOWED_PROVISION_STATES = {
    "BOOTING",
    "STA_CONNECTING",
    "STA_CONNECTED",
    "STA_FAILED",
    "AP_STARTING",
    "AP_MODE",
}


class ProvisionStateStore:
    """Thread-safe provision state tracker shared across API and watchdog threads."""

    def __init__(
        self,
        *,
        initial_state: str = "BOOTING",
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        if initial_state not in ALLOWED_PROVISION_STATES:
            raise ValueError(f"invalid initial_state: {initial_state}")
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._state = initial_state
        self._reason = "init"
        self._details: dict[str, Any] = {}
        self._updated_at = self._time_fn()

    def set_state(
        self,
        state: str,
        *,
        reason: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        if state not in ALLOWED_PROVISION_STATES:
            raise ValueError(f"invalid state: {state}")
        with self._lock:
            self._state = state
            self._reason = (reason or "").strip() or "unspecified"
            self._details = dict(details or {})
            self._updated_at = self._time_fn()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "reason": self._reason,
                "details": dict(self._details),
                "updated_at": self._updated_at,
            }


_GLOBAL_PROVISION_STATE_STORE = ProvisionStateStore()


def get_provision_state_store() -> ProvisionStateStore:
    return _GLOBAL_PROVISION_STATE_STORE

