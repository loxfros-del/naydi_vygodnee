"""Optional price-bearing Google Shopping adapter for Search Engine V2.

The adapter uses configured structured providers (SearchApi and/or SerpApi),
performs no I/O when credentials are absent, preserves structured prices before
the common V2 normalization boundary, and never retries anti-bot failures.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.config import settings

from .base import LegacyBridgeAdapter, SourceCapabilities, SourceContext
from ..models import SearchRequestV2


_PROVIDER_AUTO = "auto"
_PROVIDER_SEARCHAPI = "searchapi"
_PROVIDER_SERPAPI = "serpapi"
_GOOGLE_HOST_MARKERS = ("google.", "googleusercontent.", "gstatic.")


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


def provider_order() -> tuple[str, ...]:
    preferred = _preferred_provider()
    if preferred != _PROVIDER_AUTO:
        return (preferred,) if _provider_ready(preferred) else ()
    return tuple(
        provider
        for provider in (_PROVIDER_SEARCHAPI, _PROVIDER_SERPAPI)
        if _provider_ready(provider)
    )


def selected_shopping_provider() -> str:
    ordered = provider_order()
    return ordered[0] if ordered else ""


def shopping_search_available() -> bool:
    return bool(provider_order())


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


def _host(value: str) -> str:
    try:
        return urlsplit(value).netloc.casefold().removeprefix("www.")
    except ValueError:
        return ""


def _is_google_url(value: str) -> bool:
    host = _host(value)
    return any(marker in host for marker in _GOOGLE_HOST_MARKERS)


def _direct_product_url(row: Mapping[str, Any]) -> str:
    raw = row.get("raw")
    raw = raw if isinstance(raw, Mapping) else {}
    candidates = [
        str(row.get("url") or "").strip(),
        str(raw.get("link") or "").strip(),
        str(raw.get("product_link") or "").strip(),
        str(raw.get("offers_link") or "").strip(),
    ]
    # Prefer a retailer/product URL over a Google aggregation page.
    for value in candidates:
        if value and not _is_google_url(value):
            return value
    return next((value for value in candidates if value), "")


def _platform_name(row: Mapping[str, Any]) -> str:
    seller = str(row.get("seller") or row.get("store") or "").strip()
    if seller:
        return seller
    host = _host(_direct_product_url(row))
    return host or "Google Shopping"


def _currency_from_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip().casefold()
    if "₽" in text or "руб" in text or re.search(r"\brub\b", text):
        return "RUB"
    if "$" in text or re.search(r"\busd\b", text):
        return "USD"
    if "€" in text or re.search(r"\beur\b", text):
        return "EUR"
    if "£" in text or re.search(r"\bgbp\b", text):
        return "GBP"
    normalized = str(fallback or "").strip().upper()
    if normalized:
        return normalized
    # For gl=ru SearchApi numeric extracted_price is denominated in roubles.
    return "RUB" if str(getattr(settings, "SEARCHAPI_GL", "") or "").casefold() == "ru" else ""


def _numeric_price(value: Any, *, text_hint: Any = "") -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value))) if float(value) > 0 else None
    text = str(value).replace("\u00a0", " ").strip()
    if not text:
        return None
    # Remove currency/words, then distinguish decimal separators from thousands.
    compact = re.sub(r"[^0-9,\. ]", "", text).strip()
    if not compact:
        return None
    compact = compact.replace(" ", "")
    if "," in compact and "." in compact:
        decimal = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        compact = compact.replace(thousands, "").replace(decimal, ".")
    elif "," in compact:
        head, tail = compact.rsplit(",", 1)
        compact = head.replace(",", "") + (f".{tail}" if len(tail) <= 2 else tail)
    elif "." in compact:
        head, tail = compact.rsplit(".", 1)
        compact = head.replace(".", "") + (f".{tail}" if len(tail) <= 2 else tail)
    try:
        amount = float(compact)
    except ValueError:
        return None
    return int(round(amount)) if amount > 0 else None


def _structured_price(row: Mapping[str, Any], raw: Mapping[str, Any]) -> tuple[int | None, str, str]:
    price_text = raw.get("price") or row.get("raw_price") or row.get("price_text") or ""
    currency = _currency_from_text(price_text, str(row.get("currency") or ""))
    for source, value in (
        ("mapped", row.get("price")),
        ("extracted_price", raw.get("extracted_price")),
        ("price", raw.get("price")),
        ("base_price", raw.get("extracted_base_price") or raw.get("base_price")),
    ):
        amount = _numeric_price(value, text_hint=price_text)
        if amount is not None:
            return amount, currency or "RUB", source
    return None, currency or "RUB", ""


def _normalize_candidates(items: list[dict[str, Any]], *, provider: str, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        raw = dict(row.get("raw") or {}) if isinstance(row.get("raw"), Mapping) else {}
        price, currency, recovered_from = _structured_price(row, raw)
        row["url"] = _direct_product_url(row)
        row["platform"] = _platform_name(row)
        row["external_source"] = provider
        row["currency"] = currency
        # The Russian product engine compares RUB offers only. Keep foreign
        # currency evidence in metadata but do not silently rank it as roubles.
        if currency == "RUB":
            row["price"] = price
            if price is not None:
                row["price_source"] = "structured_api"
                row["price_reliability"] = "medium"
        else:
            row["price"] = None
            if price is not None:
                row["price_rejected_reason"] = f"unsupported_currency:{currency}"
        raw.update({
            "shopping_provider": provider,
            "price_source": row.get("price_source") or "structured_api",
            "structured_price_recovered_from": recovered_from,
            "structured_price_value": price,
            "structured_price_currency": currency,
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


def _run_provider(provider: str, query: str, parsed: dict[str, Any], request: SearchRequestV2) -> dict[str, Any]:
    if provider == _PROVIDER_SEARCHAPI:
        from app.sources.searchapi_source import debug_searchapi_google_shopping

        location = f"{request.city}, Russia" if request.city else None
        return debug_searchapi_google_shopping(query, parsed=parsed, location=location)
    from app.sources.serpapi_source import debug_serpapi_google_shopping

    return debug_serpapi_google_shopping(query, parsed=parsed)


def _status(debug: Mapping[str, Any], *, has_candidates: bool) -> str:
    status_code = debug.get("status_code")
    if status_code == 429:
        return "rate_limited"
    raw_status = str(debug.get("status") or "error").strip().casefold()
    aliases = {
        "ok": "success",
        "empty": "empty",
        "disabled": "empty",
        "missing_api_key": "unauthorized",
        "auth": "unauthorized",
        "rate_limited": "rate_limited",
        "timeout": "timeout",
        "bad_json": "invalid_response",
        "connection_error": "error",
        "http_error": "error",
        "error": "error",
    }
    status = aliases.get(raw_status, "success" if has_candidates else "error")
    return "partial_success" if has_candidates and status != "success" else status


def shopping_google_legacy_search(
    query: str,
    request: SearchRequestV2,
    context: SourceContext,
) -> dict[str, Any]:
    providers = provider_order()
    if not providers:
        return {
            "status": "empty",
            "candidates": [],
            "error": "structured shopping provider is not configured",
        }

    parsed = _request_payload(request)
    failures: list[str] = []
    for provider in providers:
        debug = _run_provider(provider, query, parsed, request)
        candidates = _normalize_candidates(
            list(debug.get("candidates") or []),
            provider=provider,
            limit=context.limit,
        )
        status = _status(debug, has_candidates=bool(candidates))
        if candidates:
            return {
                "status": status,
                "candidates": candidates,
                "error": str(debug.get("error") or "")[:500],
                "provider": provider,
                "elapsed": debug.get("elapsed"),
            }
        failures.append(f"{provider}:{status}:{str(debug.get('error') or '')[:180]}")
        # In auto mode a configured second provider may recover from quota,
        # timeout or an empty response. Explicit provider mode has one entry.
    final_status = "rate_limited" if any(":rate_limited:" in item for item in failures) else "empty"
    return {
        "status": final_status,
        "candidates": [],
        "error": "; ".join(failures)[:500],
        "provider": providers[-1],
    }


class ShoppingSearchAdapter(LegacyBridgeAdapter):
    name = "shopping_search"
    platform = "Google Shopping"
    version = "structured-shopping-3"
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
    "provider_order",
    "selected_shopping_provider",
    "shopping_google_legacy_search",
    "shopping_search_available",
]
