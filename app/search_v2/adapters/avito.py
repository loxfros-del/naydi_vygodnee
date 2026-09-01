from __future__ import annotations

from .base import SiteExactSearchBridge, SourceCapabilities, generic_web_legacy_search


class AvitoAdapter(SiteExactSearchBridge):
    name = "avito"
    platform = "Avito"
    domain = "avito.ru"
    version = "legacy-exact-1"
    capabilities = SourceCapabilities(
        kind="discovery",
        supports_city=True,
        supports_condition=True,
        supports_category_filter=True,
        requires_page_verification=True,
    )

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or generic_web_legacy_search)

