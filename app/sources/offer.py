"""Unified source adapter contract.

The module is intentionally independent from the current search orchestrator so
legacy adapters can migrate to the contract one by one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


class SourceStatus(str, Enum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    ERROR = "ERROR"


_STATUS_ALIASES: dict[str, SourceStatus] = {
    "ok": SourceStatus.SUCCESS,
    "success": SourceStatus.SUCCESS,
    "partial_success": SourceStatus.SUCCESS,
    "done": SourceStatus.SUCCESS,
    "cache_hit": SourceStatus.SUCCESS,
    "empty": SourceStatus.EMPTY,
    "no_results": SourceStatus.EMPTY,
    "not_found": SourceStatus.EMPTY,
    "blocked": SourceStatus.BLOCKED,
    "forbidden": SourceStatus.BLOCKED,
    "unauthorized": SourceStatus.BLOCKED,
    "access_denied": SourceStatus.BLOCKED,
    "captcha": SourceStatus.BLOCKED,
    "skipped_blocked": SourceStatus.BLOCKED,
    "auth": SourceStatus.BLOCKED,
    "rate_limited": SourceStatus.RATE_LIMITED,
    "rate_limit": SourceStatus.RATE_LIMITED,
    "too_many_requests": SourceStatus.RATE_LIMITED,
    "timeout": SourceStatus.TIMEOUT,
    "source_timeout": SourceStatus.TIMEOUT,
    "read_timeout": SourceStatus.TIMEOUT,
    "bad_json": SourceStatus.INVALID_RESPONSE,
    "invalid_json": SourceStatus.INVALID_RESPONSE,
    "non_json": SourceStatus.INVALID_RESPONSE,
    "invalid_response": SourceStatus.INVALID_RESPONSE,
    "error": SourceStatus.ERROR,
    "http_error": SourceStatus.ERROR,
    "connection_error": SourceStatus.ERROR,
    "request_error": SourceStatus.ERROR,
    "missing_api_key": SourceStatus.ERROR,
    "disabled": SourceStatus.ERROR,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_source_status(
    value: object,
    *,
    default: SourceStatus = SourceStatus.ERROR,
) -> SourceStatus:
    """Map current adapter/debug statuses to the strict public enum.

    Unknown values fail closed as ``ERROR`` instead of being reported as a
    successful source call.
    """
    if isinstance(value, SourceStatus):
        return value
    if isinstance(value, bool):
        return SourceStatus.SUCCESS if value else default
    if isinstance(value, int):
        if 200 <= value < 300:
            return SourceStatus.EMPTY if value == 204 else SourceStatus.SUCCESS
        if value in {401, 403, 498}:
            return SourceStatus.BLOCKED
        if value == 429:
            return SourceStatus.RATE_LIMITED
        if value in {408, 504}:
            return SourceStatus.TIMEOUT
        return SourceStatus.ERROR

    text = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if not text:
        return default
    if text in _STATUS_ALIASES:
        return _STATUS_ALIASES[text]
    if text.isdigit():
        return normalize_source_status(int(text), default=default)
    if "429" in text or "rate_limit" in text or "too_many" in text:
        return SourceStatus.RATE_LIMITED
    if any(marker in text for marker in ("403", "401", "498", "captcha", "blocked", "forbidden")):
        return SourceStatus.BLOCKED
    if "timeout" in text or "timed_out" in text:
        return SourceStatus.TIMEOUT
    if any(marker in text for marker in ("bad_json", "invalid_json", "non_json", "invalid_response")):
        return SourceStatus.INVALID_RESPONSE
    return default


@dataclass(frozen=True)
class Offer:
    source: str = ""
    platform: str = ""
    seller: str = ""
    title: str = ""
    url: str = ""
    product_id: str = ""
    price: int | None = None
    old_price: int | None = None
    currency: str = "RUB"
    availability: str = "UNKNOWN"
    condition: str = "unknown"
    city: str = ""
    delivery: str = ""
    seller_rating: float | None = None
    seller_reviews_count: int | None = None
    image_url: str = ""
    snippet: str = ""
    structured_facts: Mapping[str, Any] = field(default_factory=dict)
    retrieved_at: str = field(default_factory=utc_now_iso)
    source_status: SourceStatus = SourceStatus.SUCCESS
    source_error: str = ""


@dataclass(frozen=True)
class SourceBatch:
    """One source invocation, including useful partial results and diagnostics."""

    source: str
    status: SourceStatus
    offers: tuple[Offer, ...] = ()
    error: str = ""
    query: str = ""
    retrieved_at: str = field(default_factory=utc_now_iso)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(
        cls,
        payload: Mapping[str, Any] | Iterable[Mapping[str, Any]],
        *,
        source: str = "",
        query: str = "",
    ) -> "SourceBatch":
        return normalize_legacy_batch(payload, source=source, query=query)


_PLATFORM_BY_SOURCE = {
    "yandex_market_search": "Яндекс Маркет",
    "yandex_market_direct": "Яндекс Маркет",
    "ozon_search": "Ozon",
    "wildberries": "Wildberries",
    "avito_search": "Avito",
    "dns_search": "DNS",
    "dns_direct": "DNS",
    "citilink_search": "Ситилинк",
    "citilink_direct": "Ситилинк",
    "mvideo_search": "М.Видео",
    "mvideo_direct": "М.Видео",
}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value: object, cast: type[int] | type[float]) -> int | float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return cast(value)
        except (TypeError, ValueError, OverflowError):
            return None
    text = _clean_text(value).replace("\u00a0", " ")
    match = re.search(r"-?\d+(?:[.,]\d+)?", text.replace(" ", ""))
    if not match:
        return None
    try:
        return cast(float(match.group(0).replace(",", ".")))
    except (TypeError, ValueError, OverflowError):
        return None


def _platform_from_url(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""
    domain_platforms = (
        ("market.yandex.ru", "Яндекс Маркет"),
        ("ozon.ru", "Ozon"),
        ("wildberries.ru", "Wildberries"),
        ("avito.ru", "Avito"),
        ("dns-shop.ru", "DNS"),
        ("citilink.ru", "Ситилинк"),
        ("mvideo.ru", "М.Видео"),
    )
    return next((platform for suffix, platform in domain_platforms if domain == suffix or domain.endswith(f".{suffix}")), "")


def _availability(value: object) -> str:
    if isinstance(value, bool):
        return "AVAILABLE" if value else "UNAVAILABLE"
    text = _clean_text(value)
    return text.upper() if text else "UNKNOWN"


def normalize_legacy_offer(
    row: Mapping[str, Any],
    *,
    default_source: str = "",
    default_platform: str = "",
    default_status: SourceStatus = SourceStatus.SUCCESS,
    retrieved_at: str = "",
) -> Offer:
    """Convert an existing adapter row to ``Offer`` without losing raw facts."""
    raw = row.get("raw") if isinstance(row.get("raw"), Mapping) else {}
    facts_value = _first(row, "structured_facts", "product_facts", "facts")
    facts = dict(facts_value) if isinstance(facts_value, Mapping) else {}
    if raw:
        facts.setdefault("_legacy_raw", dict(raw))

    source = _clean_text(_first(row, "source", "source_name") or default_source)
    url = _clean_text(_first(row, "url", "link", "href", "product_link"))
    platform = _clean_text(row.get("platform") or default_platform)
    if not platform:
        platform = _platform_from_url(url) or _PLATFORM_BY_SOURCE.get(source, "")

    raw_product_id = _first(row, "product_id", "id", "sku", "offer_id", "nmId")
    if raw_product_id in (None, "") and raw:
        raw_product_id = _first(raw, "product_id", "id", "sku", "offer_id", "nmId")
    status_value = _first(row, "source_status", "status")

    return Offer(
        source=source,
        platform=platform,
        seller=_clean_text(_first(row, "seller", "store", "merchant")),
        title=_clean_text(_first(row, "title", "name")),
        url=url,
        product_id=_clean_text(raw_product_id),
        price=_number(_first(row, "price", "extracted_price", "current_price"), int),
        old_price=_number(_first(row, "old_price", "original_price", "previous_price"), int),
        currency=_clean_text(row.get("currency") or "RUB").upper(),
        availability=_availability(_first(row, "availability", "available")),
        condition=_clean_text(row.get("condition") or facts.get("condition") or "unknown").lower(),
        city=_clean_text(row.get("city") or facts.get("city")),
        delivery=_clean_text(row.get("delivery") or facts.get("delivery")),
        seller_rating=_number(_first(row, "seller_rating", "rating"), float),
        seller_reviews_count=_number(
            _first(row, "seller_reviews_count", "reviews_count", "reviews"), int
        ),
        image_url=_clean_text(_first(row, "image_url", "image", "thumbnail")),
        snippet=_clean_text(_first(row, "snippet", "body", "description")),
        structured_facts=facts,
        retrieved_at=_clean_text(row.get("retrieved_at") or retrieved_at) or utc_now_iso(),
        source_status=normalize_source_status(status_value, default=default_status),
        source_error=_clean_text(_first(row, "source_error", "error", "error_text")),
    )


def normalize_legacy_batch(
    payload: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    source: str = "",
    query: str = "",
) -> SourceBatch:
    """Convert current debug payloads or plain row lists to ``SourceBatch``."""
    if isinstance(payload, Mapping):
        rows_value = _first(payload, "offers", "candidates", "rows", "results") or ()
        rows = tuple(item for item in rows_value if isinstance(item, Mapping)) if isinstance(rows_value, Iterable) and not isinstance(rows_value, (str, bytes, Mapping)) else ()
        batch_source = _clean_text(_first(payload, "source", "source_name") or source)
        error = _clean_text(_first(payload, "source_error", "error", "error_text"))
        status_value = _first(payload, "source_status", "status")
        retrieved_at = _clean_text(payload.get("retrieved_at")) or utc_now_iso()
        diagnostics = {
            str(key): value
            for key, value in payload.items()
            if key not in {"offers", "candidates", "rows", "results"}
        }
    else:
        rows = tuple(item for item in payload if isinstance(item, Mapping))
        batch_source = _clean_text(source)
        error = ""
        status_value = SourceStatus.SUCCESS if rows else SourceStatus.EMPTY
        retrieved_at = utc_now_iso()
        diagnostics = {}

    default_status = SourceStatus.SUCCESS if rows else SourceStatus.EMPTY
    status = normalize_source_status(status_value, default=default_status)
    offers = tuple(
        normalize_legacy_offer(
            item,
            default_source=batch_source,
            default_status=SourceStatus.SUCCESS,
            retrieved_at=retrieved_at,
        )
        for item in rows
    )
    if status == SourceStatus.SUCCESS and not offers:
        status = SourceStatus.EMPTY
    return SourceBatch(
        source=batch_source,
        status=status,
        offers=offers,
        error=error,
        query=_clean_text(query),
        retrieved_at=retrieved_at,
        diagnostics=diagnostics,
    )


__all__ = [
    "Offer",
    "SourceBatch",
    "SourceStatus",
    "normalize_legacy_batch",
    "normalize_legacy_offer",
    "normalize_source_status",
]
