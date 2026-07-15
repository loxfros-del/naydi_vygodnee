from __future__ import annotations

from .base import LegacyBridgeAdapter, SourceCapabilities, generic_web_legacy_search


class GenericExactSearchAdapter(LegacyBridgeAdapter):
    name = "generic_exact"
    platform = "Веб-поиск"
    version = "legacy-exact-1"
    capabilities = SourceCapabilities(kind="fallback", optional=True)

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or generic_web_legacy_search)


GenericSearchAdapter = GenericExactSearchAdapter
