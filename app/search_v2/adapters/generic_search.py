from __future__ import annotations

from .base import LegacyBridgeAdapter, SourceCapabilities, generic_web_legacy_search


def generic_exact_legacy_search(query, request, context):
    """Mark broad-web links as candidates that require direct page proof."""
    rows = generic_web_legacy_search(query, request, context)
    marked = []
    for value in rows:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        row["page_verification_required"] = True
        marked.append(row)
    return marked


class GenericExactSearchAdapter(LegacyBridgeAdapter):
    name = "generic_exact"
    platform = "Веб-поиск"
    version = "legacy-exact-1"
    capabilities = SourceCapabilities(
        kind="fallback",
        requires_page_verification=True,
        optional=True,
    )

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or generic_exact_legacy_search)


GenericSearchAdapter = GenericExactSearchAdapter

__all__ = ["GenericExactSearchAdapter", "GenericSearchAdapter", "generic_exact_legacy_search"]
