"""벤더별 MQTT 설정 기본값 인터페이스."""


class MQTTProviderSettings:
    """벤더별 MQTT 설정 기본값을 제공하는 인터페이스."""

    def get_endpoint(self) -> str:
        raise NotImplementedError

    def get_client_id(self) -> str:
        raise NotImplementedError

    def get_cert_dir(self) -> str:
        raise NotImplementedError

    def get_topic_subscribe(self) -> str:
        raise NotImplementedError

    def get_topic_publish(self) -> str:
        raise NotImplementedError

    def get_default_report_entity_ids(self) -> list[str]:
        raise NotImplementedError
