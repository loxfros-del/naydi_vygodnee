"""SearchApi Google Shopping source."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import requests

from app.config import settings
from app.price_extractor import extract_price
from app.price_guard import normalize_price_candidate

logger = logging.getLogger(__name__)

SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"
SEARCHAPI_SOURCE = "searchapi_google_shopping"


def _max_results() -> int:
    try:
        return max(1, min(int(settings.SEARCHAPI_MAX_RESULTS or 5), 20))
    except (TypeError, ValueError):
        return 5


def _parsed_namespace(parsed: dict | None) -> SimpleNamespace:
    parsed = parsed or {}
    return SimpleNamespace(
        budget=parsed.get("budget", ""),
        original_query=parsed.get("original_query", "") or parsed.get("query", ""),
        product_name=parsed.get("product_name", "") or parsed.get("product", ""),
        product=parsed.get("product", "") or parsed.get("product_name", ""),
        important_criteria=parsed.get("important_criteria", ""),
    )


def _as_int_price(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(round(value)) if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    parsed = extract_price(text, min_price=1, max_price=10_000_000)
    if parsed is not None:
        return parsed
    if not any(marker in text.lower() for marker in ("₽", "руб", "rub", "р.")):
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _reviews_count(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def _map_item(item: dict[str, Any], parsed: dict | None) -> dict | None:
    title = _first_text(item, "title", "name")
    link = _first_text(item, "link", "product_link", "offers_link")
    if not title or not link:
        return None

    price = _as_int_price(item.get("extracted_price"))
    if price is None:
        price = _as_int_price(item.get("price"))

    price_text = _first_text(item, "price", "extracted_price")
    seller = _first_text(item, "seller", "source", "merchant", "store")
    guard = normalize_price_candidate(
        None,
        _parsed_namespace(parsed),
        price=price,
        raw_price=price,
        price_source="searchapi",
        source=SEARCHAPI_SOURCE,
        title=title,
        snippet=f"{seller} {price_text}",
        text=f"{title} {seller} {price_text}",
    )

    has_store_link = bool(_first_text(item, "link", "product_link"))
    price_reliability = "medium" if guard.price is not None and has_store_link else "none"
    risk_flags: list[str] = []
    if not has_store_link:
        price_reliability = "low" if guard.price is not None else "none"
        risk_flags.append("нет прямой ссылки на товар/магазин")

    return {
        "title": title,
        "price": guard.price,
        "url": link,
        "source": SEARCHAPI_SOURCE,
        "store": seller,
        "seller": seller,
        "rating": item.get("rating"),
        "reviews_count": _reviews_count(item.get("reviews") or item.get("reviews_count")),
        "price_source": "structured_api" if guard.price is not None else "",
        "price_reliability": price_reliability,
        "price_rejected_reason": guard.price_rejected_reason,
        "price_from_budget_suspect": guard.price_from_budget_suspect,
        "bad_price_context": guard.bad_price_context,
        "external_source": "searchapi",
        "risk_flags": risk_flags,
        "raw": item,
        "body": " ".join(part for part in (seller, price_text) if part),
    }


def _iter_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("shopping_results", "shopping_ads", "popular_products"):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def search_searchapi_google_shopping(query: str, parsed: dict | None = None) -> list[dict]:
    if not settings.SEARCHAPI_ENABLED or not settings.SEARCHAPI_API_KEY:
        return []

    params = {
        "engine": "google_shopping",
        "q": query,
        "gl": settings.SEARCHAPI_GL,
        "hl": settings.SEARCHAPI_HL,
        "location": settings.SEARCHAPI_LOCATION,
        "api_key": settings.SEARCHAPI_API_KEY,
    }
    try:
        response = requests.get(SEARCHAPI_ENDPOINT, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("SearchApi Google Shopping failed: %s", exc)
        return []
    if not isinstance(payload, dict):
        return []

    rows: list[dict] = []
    for item in _iter_items(payload):
        mapped = _map_item(item, parsed)
        if mapped:
            rows.append(mapped)
        if len(rows) >= _max_results():
            break
    return rows
