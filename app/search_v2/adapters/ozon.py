from __future__ import annotations

from .base import SiteExactSearchBridge, SourceCapabilities, generic_web_legacy_search


class OzonAdapter(SiteExactSearchBridge):
    name = "ozon"
    platform = "Ozon"
    domain = "ozon.ru/product"
    version = "legacy-exact-1"
    capabilities = SourceCapabilities(kind="discovery", supports_city=False)

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or generic_web_legacy_search)

