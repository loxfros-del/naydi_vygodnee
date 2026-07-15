"""Optional price-bearing Google Shopping adapter for Search Engine V2.

The adapter uses one configured structured provider (SearchApi or SerpApi). It
never requires credentials for imports/tests and performs no network call when
neither provider is enabled with a key. SearchApi is preferred in ``auto`` mode;
``SEARCH_V2_SHOPPING_PROVIDER=serpapi`` can select SerpApi explicitly.
"""
from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.config import settings

from .base import LegacyBridgeAdapter, SourceCapabilities, SourceContext
from ..models import SearchRequestV2


_PROVIDER_AUTO = "auto"
_PROVIDER_SEARCHAPI = "searchapi"
_PROVIDER_SERPAPI = "serpapi"


def _preferred_provider() -> str:
    value = str(
        getattr(settings, "SEARCH_V2_SHOPPING_PROVIDER", "")
        or os.getenv("SEARCH_V2_SHOPPING_PROVIDER", _PROVIDER_AUTO)
        or _PROVIDER_AUTO
    ).strip().casefold()
    return value if value in {_PROVIDER_AUTO, _PROVIDER_SEARCHAPI, _PROVIDER_SERPAPI} else _PROVIDER_AUTO


def _provider_ready(name: str) -> bool:
    if name == _PROVIDER_SEARCHAPI:
        return bool(settings.SEARCHAPI_ENABLED and settings.SEARCHAPI_API_KEY)
    if name == _PROVIDER_SERPAPI:
        return bool(settings.SERPAPI_ENABLED and settings.SERPAPI_API_KEY)
    return False


def selected_shopping_provider() -> str:
    preferred = _preferred_provider()
    if preferred != _PROVIDER_AUTO:
        return preferred if _provider_ready(preferred) else ""
    if _provider_ready(_PROVIDER_SEARCHAPI):
        return _PROVIDER_SEARCHAPI
    if _provider_ready(_PROVIDER_SERPAPI):
        return _PROVIDER_SERPAPI
    return ""


def shopping_search_available() -> bool:
    return bool(selected_shopping_provider())


def _request_payload(request: SearchRequestV2) -> dict[str, Any]:
    product = " ".join(
        part for part in (
            request.brand,
            request.canonical_model,
            " ".join(request.model_modifiers),
        ) if part
    ).strip()
    return {
        "query": request.original_query,
        "original_query": request.original_query,
        "product": product or request.category,
        "product_name": product or request.category,
        "category": request.category,
        "brand": request.brand,
        "model": request.canonical_model,
        "budget": request.budget,
        "city": request.city,
        "required_specs": dict(request.required_specs or {}),
        "important_criteria": " ".join(str(value) for value in request.optional_specs.values()),
    }


def _direct_product_url(row: Mapping[str, Any]) -> str:
    raw = row.get("raw")
    if isinstance(raw, Mapping):
        for key in ("product_link", "link", "offers_link"):
            value = str(raw.get(key) or "").strip()
            if value:
                return value
    return str(row.get("url") or "").strip()


def _platform_name(row: Mapping[str, Any]) -> str:
    seller = str(row.get("seller") or row.get("store") or "").strip()
    if seller:
        return seller
    url = _direct_product_url(row)
    try:
        host = urlsplit(url).netloc.casefold().removeprefix("www.")
    except ValueError:
        host = ""
    return host or "Google Shopping"


def _normalize_candidates(items: list[dict[str, Any]], *, provider: str, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        row["url"] = _direct_product_url(row)
        row["platform"] = _platform_name(row)
        row["external_source"] = provider
        raw = dict(row.get("raw") or {}) if isinstance(row.get("raw"), Mapping) else {}
        raw.update({
            "shopping_provider": provider,
            "price_source": row.get("price_source") or "structured_api",
            "seller": row.get("seller") or row.get("store") or "",
            "seller_rating": row.get("rating"),
            "seller_reviews_count": row.get("reviews_count"),
        })
        row["raw"] = raw
        if row.get("title") and row.get("url"):
            result.append(row)
        if len(result) >= max(1, int(limit)):
            break
    return result


def shopping_google_legacy_search(
    query: str,
    request: SearchRequestV2,
    context: SourceContext,
) -> dict[str, Any]:
    provider = selected_shopping_provider()
    if not provider:
        return {
            "status": "empty",
            "candidates": [],
            "error": "structured shopping provider is not configured",
        }

    parsed = _request_payload(request)
    if provider == _PROVIDER_SEARCHAPI:
        from app.sources.searchapi_source import debug_searchapi_google_shopping

        debug = debug_searchapi_google_shopping(query, parsed=parsed)
    else:
        from app.sources.serpapi_source import debug_serpapi_google_shopping

        debug = debug_serpapi_google_shopping(query, parsed=parsed)

    raw_status = str(debug.get("status") or "error").strip().casefold()
    status_aliases = {
        "ok": "success",
        "empty": "empty",
        "disabled": "empty",
        "missing_api_key": "unauthorized",
        "auth": "unauthorized",
        "timeout": "timeout",
        "bad_json": "invalid_response",
        "connection_error": "error",
        "http_error": "error",
        "error": "error",
    }
    candidates = _normalize_candidates(
        list(debug.get("candidates") or []),
        provider=provider,
        limit=context.limit,
    )
    status = status_aliases.get(raw_status, "success" if candidates else "error")
    if candidates and status != "success":
        status = "partial_success"
    return {
        "status": status,
        "candidates": candidates,
        "error": str(debug.get("error") or "")[:500],
        "provider": provider,
        "elapsed": debug.get("elapsed"),
    }


class ShoppingSearchAdapter(LegacyBridgeAdapter):
    name = "shopping_search"
    platform = "Google Shopping"
    version = "structured-shopping-2"
    capabilities = SourceCapabilities(
        kind="discovery",
        structured_endpoint=True,
        supports_city=True,
        supports_condition=True,
        optional=True,
    )

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or shopping_google_legacy_search)


__all__ = [
    "ShoppingSearchAdapter",
    "selected_shopping_provider",
    "shopping_google_legacy_search",
    "shopping_search_available",
]
