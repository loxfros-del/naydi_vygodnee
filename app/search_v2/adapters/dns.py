from __future__ import annotations

from .base import LegacyBridgeAdapter, SourceCapabilities, direct_retail_legacy_search


class DnsAdapter(LegacyBridgeAdapter):
    name = "dns"
    platform = "DNS"
    version = "legacy-direct-1"
    capabilities = SourceCapabilities(kind="reliable_anchor", structured_endpoint=True, supports_city=True)

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or direct_retail_legacy_search("dns"))


DNSAdapter = DnsAdapter
