from __future__ import annotations

from .base import LegacyBridgeAdapter, SourceCapabilities, direct_retail_legacy_search


class YandexMarketAdapter(LegacyBridgeAdapter):
    name = "yandex_market"
    platform = "Яндекс Маркет"
    version = "legacy-direct-1"
    capabilities = SourceCapabilities(
        kind="discovery",
        supports_category_filter=True,
        returns_price=True,
        requires_page_verification=True,
    )

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or direct_retail_legacy_search("yandex_market"))

