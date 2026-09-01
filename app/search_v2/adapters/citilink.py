from __future__ import annotations

from .base import LegacyBridgeAdapter, SourceCapabilities, direct_retail_legacy_search


class CitilinkAdapter(LegacyBridgeAdapter):
    name = "citilink"
    platform = "Ситилинк"
    version = "legacy-direct-1"
    capabilities = SourceCapabilities(
        kind="reliable_anchor",
        supports_category_filter=True,
        returns_price=True,
        requires_page_verification=True,
        optional=True,
    )

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or direct_retail_legacy_search("citilink"))

