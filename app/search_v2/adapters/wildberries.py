from __future__ import annotations

from .base import LegacyBridgeAdapter, SourceCapabilities, wildberries_legacy_search


class WildberriesAdapterV2(LegacyBridgeAdapter):
    name = "wildberries"
    platform = "Wildberries"
    version = "legacy-public-1"
    capabilities = SourceCapabilities(
        kind="discovery",
        structured_endpoint=True,
        supports_category_filter=True,
        returns_price=True,
        returns_availability=True,
        requires_page_verification=True,
        optional=True,
    )

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or wildberries_legacy_search)


WildberriesAdapter = WildberriesAdapterV2
