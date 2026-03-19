"""providers/konai/settings.py 검증 테스트."""

import unittest

from providers.konai.settings import (
    CERT_DIR,
    CLIENT_ID,
    ENDPOINT,
    TOPIC_DELTA,
    TOPIC_REPORTED,
    build_default_report_entity_ids,
)


class KonaiSettingsTest(unittest.TestCase):

    def test_endpoint_is_aws_iot(self):
        self.assertIn("iot.ap-northeast-2.amazonaws.com", ENDPOINT)

    def test_client_id_not_empty(self):
        self.assertTrue(CLIENT_ID)

    def test_cert_dir_points_to_konai(self):
        self.assertIn("konai", CERT_DIR)

    def test_topics_contain_thing_name(self):
        thing_fragment = "c3c6d27d5f2f353991afac4e3af69029303795a2"
        self.assertIn(thing_fragment, TOPIC_DELTA)
        self.assertIn(thing_fragment, TOPIC_REPORTED)

    def test_delta_is_subscribe_topic(self):
        self.assertTrue(TOPIC_DELTA.startswith("update/delta/"))

    def test_reported_is_publish_topic(self):
        self.assertTrue(TOPIC_REPORTED.startswith("update/reported/"))

    def test_build_default_report_entity_ids(self):
        ids = build_default_report_entity_ids()
        self.assertIn("sensor.smart_ht_sensor_ondo", ids)
        self.assertIn("sensor.smart_ht_sensor_seubdo", ids)
        # 1~20 범위의 인덱스 엔티티 포함
        self.assertIn("sensor.smart_ht_sensor_ondo_1", ids)
        self.assertIn("sensor.smart_ht_sensor_seubdo_20", ids)
        # 기본 2개 + ondo 20개 + seubdo 20개 = 42개
        self.assertEqual(len(ids), 42)


if __name__ == "__main__":
    unittest.main()
