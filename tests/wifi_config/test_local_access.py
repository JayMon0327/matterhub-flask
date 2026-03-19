from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from wifi_config.local_access import (
    build_local_access_summary,
    get_local_http_port,
    get_local_setup_path,
    normalize_local_hostname,
)


class LocalAccessTest(unittest.TestCase):
    def test_normalize_local_hostname_rewrites_invalid_characters(self) -> None:
        self.assertEqual(
            "matterhub-setup-whatsmatter",
            normalize_local_hostname("MatterHub Setup_WhatsMatter"),
        )

    def test_normalize_local_hostname_falls_back_when_empty(self) -> None:
        self.assertEqual(
            "matterhub-setup-whatsmatter",
            normalize_local_hostname(""),
        )

    def test_get_local_http_port_clamps_invalid_values(self) -> None:
        with patch.dict(os.environ, {"MATTERHUB_LOCAL_HTTP_PORT": "99999"}, clear=False):
            self.assertEqual(65535, get_local_http_port())

    def test_get_local_setup_path_adds_leading_slash(self) -> None:
        with patch.dict(os.environ, {"MATTERHUB_LOCAL_SETUP_PATH": "wifi/setup"}, clear=False):
            self.assertEqual("/wifi/setup", get_local_setup_path())

    def test_build_local_access_summary_uses_normalized_hostname(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MATTERHUB_LOCAL_HOSTNAME": "MatterHub Setup_WhatsMatter",
                "MATTERHUB_LOCAL_SERVICE_NAME": "MatterHub Local Setup",
                "MATTERHUB_LOCAL_HTTP_PORT": "8100",
                "MATTERHUB_LOCAL_SETUP_PATH": "/local/admin/network",
            },
            clear=False,
        ):
            summary = build_local_access_summary()

        self.assertEqual("matterhub-setup-whatsmatter", summary["hostname"])
        self.assertEqual("matterhub-setup-whatsmatter.local", summary["fqdn"])
        self.assertEqual("http://matterhub-setup-whatsmatter.local:8100/local/admin/network", summary["setup_url"])
        self.assertEqual("MatterHub Local Setup", summary["service_name"])


if __name__ == "__main__":
    unittest.main()
