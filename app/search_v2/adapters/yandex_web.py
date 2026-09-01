"""Optional Yandex web discovery for external product-page candidates.

This adapter never treats a search snippet as a final offer. Every returned URL
is marked for page verification by the web service before it can receive a
price or appear in a recommendation.
"""
from __future__ import annotations

from typing import Any, Mapping

from .base import LegacyBridgeAdapter, SourceCapabilities


def yandex_web_legacy_search(query: str, request: Any, context: Any) -> Mapping[str, Any]:
    """Return optional API discovery rows without exposing credentials or errors."""
    from app.sources.yandex_search_source import debug_yandex_search

    outcome = debug_yandex_search(
        query,
        parsed={"city": str(getattr(request, "city", "") or "")},
        timeout=getattr(context, "timeout", None),
    )
    status = str(outcome.get("status") or "empty").casefold()
    if status in {"disabled", "missing_credentials", "sdk_missing", "empty"}:
        return {"status": "empty", "candidates": []}
    if status != "ok":
        return {"status": "error", "candidates": [], "error": "external discovery unavailable"}
    rows: list[dict[str, Any]] = []
    region_confirmed = bool(outcome.get("region_confirmed"))
    region_id = str(outcome.get("region_id") or "")
    for value in outcome.get("candidates") or ():
        if not isinstance(value, Mapping):
            continue
        row = dict(value)
        row["page_verification_required"] = True
        row["region_scope_confirmed"] = region_confirmed
        row["region_id"] = region_id
        if region_confirmed:
            row["city"] = str(getattr(request, "city", "") or "")
        rows.append(row)
    return {"status": "ok", "candidates": rows}


class YandexWebDiscoveryAdapter(LegacyBridgeAdapter):
    name = "yandex_web"
    platform = "Яндекс Поиск"
    version = "optional-web-discovery-1"
    capabilities = SourceCapabilities(
        kind="fallback",
        structured_endpoint=True,
        supports_city=True,
        supports_category_filter=True,
        requires_page_verification=True,
        optional=True,
    )

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or yandex_web_legacy_search)


__all__ = ["YandexWebDiscoveryAdapter", "yandex_web_legacy_search"]
