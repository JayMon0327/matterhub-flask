"""벤더 프로바이더 팩토리."""

import os

from .base import MQTTProviderSettings


def load_provider(vendor: str | None = None) -> MQTTProviderSettings:
    """벤더 이름에 따라 적절한 프로바이더 설정 인스턴스를 반환한다."""
    vendor = vendor or os.environ.get("MATTERHUB_VENDOR", "konai")
    if vendor == "konai":
        from providers.konai.settings import KonaiSettings
        return KonaiSettings()
    raise ValueError(f"Unknown vendor: {vendor}")
