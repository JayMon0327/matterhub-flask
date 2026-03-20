"""providers.load_provider() 팩토리 테스트."""

import os
import unittest
from unittest.mock import patch


class ProviderFactoryTest(unittest.TestCase):
    def test_load_konai_provider_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MATTERHUB_VENDOR", None)
            from providers import load_provider
            provider = load_provider()
        self.assertEqual("konai_certificates/", provider.get_cert_dir())
        self.assertIn("iot.ap-northeast-2.amazonaws.com", provider.get_endpoint())

    def test_load_konai_provider_explicit(self) -> None:
        from providers import load_provider
        provider = load_provider("konai")
        self.assertTrue(provider.get_topic_subscribe())
        self.assertTrue(provider.get_topic_publish())

    def test_load_provider_from_env(self) -> None:
        with patch.dict(os.environ, {"MATTERHUB_VENDOR": "konai"}, clear=False):
            from providers import load_provider
            provider = load_provider()
        self.assertEqual("konai_certificates/", provider.get_cert_dir())

    def test_unknown_vendor_raises(self) -> None:
        from providers import load_provider
        with self.assertRaises(ValueError):
            load_provider("nonexistent")

    def test_konai_default_report_entity_ids_not_empty(self) -> None:
        from providers import load_provider
        provider = load_provider("konai")
        ids = provider.get_default_report_entity_ids()
        self.assertGreater(len(ids), 0)
        self.assertIn("sensor.smart_ht_sensor_ondo", ids)


if __name__ == "__main__":
    unittest.main()
