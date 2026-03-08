from __future__ import annotations

import unittest

from wifi_config.state import ProvisionStateStore, get_provision_state_store


class ProvisionStateStoreTest(unittest.TestCase):
    def test_snapshot_returns_initial_booting_state(self) -> None:
        store = ProvisionStateStore()
        snapshot = store.snapshot()

        self.assertEqual("BOOTING", snapshot["state"])
        self.assertEqual("init", snapshot["reason"])
        self.assertIsInstance(snapshot["updated_at"], float)

    def test_set_state_updates_reason_and_details(self) -> None:
        ticks = iter([100.0, 101.0])
        store = ProvisionStateStore(time_fn=lambda: next(ticks))

        store.set_state(
            "STA_CONNECTING",
            reason="user_submit_wifi",
            details={"target_ssid": "OfficeWifi"},
        )
        snapshot = store.snapshot()

        self.assertEqual("STA_CONNECTING", snapshot["state"])
        self.assertEqual("user_submit_wifi", snapshot["reason"])
        self.assertEqual({"target_ssid": "OfficeWifi"}, snapshot["details"])
        self.assertEqual(101.0, snapshot["updated_at"])

    def test_invalid_state_raises_value_error(self) -> None:
        store = ProvisionStateStore()
        with self.assertRaises(ValueError):
            store.set_state("INVALID")

    def test_global_store_returns_singleton(self) -> None:
        self.assertIs(get_provision_state_store(), get_provision_state_store())


if __name__ == "__main__":
    unittest.main()

