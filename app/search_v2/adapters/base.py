"""Async source contract and safe bridges to the existing search collectors.

Adapters deliberately stop at ``RawOffer``.  Exact matching, trust, risks and
recommendation roles belong to later Search V2 stages.
"""
from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import re
import time
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.search_v2.models import (
    ProductCondition,
    QueryTier,
    RawOffer,
    SearchRequestV2,
    SourceAttempt,
    SourceQuery,
    SourceStatus,
)


LegacySearch = Callable[[str, SearchRequestV2, "SourceContext"], Any]


@dataclass(frozen=True)
class SourceCapabilities:
    """Transport facts used for honest planning and rollout decisions.

    A ``supports_*`` flag means the source really applies that value, not
    merely that the value exists on the request. ``returns_*`` describes the
    raw response before any external product-page verification.
    """

    kind: str = "discovery"
    structured_endpoint: bool = False
    supports_city: bool = False
    supports_condition: bool = False
    supports_sku_filter: bool = False
    supports_price_filter: bool = False
    supports_category_filter: bool = False
    returns_price: bool = False
    returns_availability: bool = False
    requires_page_verification: bool = False
    optional: bool = False


@dataclass(frozen=True)
class SourceContext:
    limit: int = 10
    timeout: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceResult:
    status: SourceStatus
    raw_offers: tuple[RawOffer, ...] = ()
    attempts: tuple[SourceAttempt, ...] = ()
    duration: float = 0.0
    error: str = ""
    rate_limit_info: Mapping[str, Any] = field(default_factory=dict)
    cache_info: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {SourceStatus.SUCCESS, SourceStatus.PARTIAL_SUCCESS}


class SourceAdapter(abc.ABC):
    name: str
    platform: str
    version: str = "1"
    capabilities: SourceCapabilities = SourceCapabilities()

    @abc.abstractmethod
    async def search(
        self,
        request: SearchRequestV2,
        source_query: SourceQuery,
        context: SourceContext,
    ) -> SourceResult:
        raise NotImplementedError


_SPACE_RE = re.compile(r"[^0-9a-zа-яё+]+", re.IGNORECASE)
_TOKEN_ALIASES: Mapping[str, tuple[str, ...]] = {
    "new": ("new", "новый", "новая", "новое", "новые"),
    "used": ("used", "б у", "бу", "подержанный", "подержанная"),
    "refurbished": ("refurbished", "восстановленный", "восстановленная"),
}


def normalize_constraint_token(value: object) -> str:
    return " ".join(_SPACE_RE.sub(" ", str(value or "").casefold()).split())


def _token_present(token: object, query: object) -> bool:
    required = normalize_constraint_token(token)
    haystack = normalize_constraint_token(query)
    if not required:
        return True
    variants = _TOKEN_ALIASES.get(required, (required,))
    padded_haystack = f" {haystack} "
    return any(
        f" {normalize_constraint_token(variant)} " in padded_haystack
        for variant in variants
        if normalize_constraint_token(variant)
    )


def missing_hard_tokens(source_query: SourceQuery) -> tuple[str, ...]:
    required = tuple(str(item).strip() for item in getattr(source_query, "hard_tokens_required", ()) if str(item).strip())
    preserved = tuple(str(item).strip() for item in getattr(source_query, "hard_tokens_preserved", ()) if str(item).strip())
    query = str(getattr(source_query, "query", "") or "")
    missing: list[str] = []
    for token in required:
        in_query = _token_present(token, query)
        annotated = not preserved or any(
            normalize_constraint_token(token) == normalize_constraint_token(item)
            for item in preserved
        )
        if not in_query or not annotated:
            missing.append(token)
    return tuple(missing)


def validate_source_query(source_query: SourceQuery) -> tuple[bool, str]:
    if getattr(source_query, "status", SourceStatus.SUCCESS) is SourceStatus.INVALID_QUERY_PLAN:
        return False, str(getattr(source_query, "rejection_reason", "") or "invalid query plan")
    rejection = str(getattr(source_query, "rejection_reason", "") or "").strip()
    if rejection:
        return False, rejection
    if not str(getattr(source_query, "query", "") or "").strip():
        return False, "empty source query"
    missing = missing_hard_tokens(source_query)
    if missing:
        return False, "lost hard tokens: " + ", ".join(missing)
    return True, ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _model_kwargs(model: type[Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """Keep bridges compatible while domain dataclasses evolve independently."""
    if is_dataclass(model):
        names = {item.name for item in fields(model)}
        return {key: value for key, value in values.items() if key in names}
    try:
        parameters = inspect.signature(model).parameters
    except (TypeError, ValueError):
        return dict(values)
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return dict(values)
    return {key: value for key, value in values.items() if key in parameters}


def construct_model(model: type[Any], /, **values: Any) -> Any:
    return model(**_model_kwargs(model, values))


def _number(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    match = re.search(r"\d[\d\s\u00a0]*(?:[.,]\d+)?", str(value))
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(" ", "").replace("\u00a0", "").replace(",", ".")))
    except (TypeError, ValueError, OverflowError):
        return None


def _first(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def raw_offer_from_legacy(row: Mapping[str, Any], *, source: str, platform: str) -> RawOffer:
    """Lossless boundary conversion; shared normalization intentionally happens later."""
    url = str(_first(row, "url", "href", "link", "product_link") or "").strip()
    title = " ".join(str(_first(row, "title", "name") or "").split())
    product_id = str(_first(row, "product_id", "id", "sku", "nmId") or "").strip()
    fingerprint = product_id or hashlib.sha256(f"{source}|{url}|{title}".encode("utf-8")).hexdigest()[:20]
    raw_metadata = dict(row)
    condition_text = normalize_constraint_token(row.get("condition") or "unknown")
    if condition_text in {"new", "новый", "новая", "новое"}:
        condition = ProductCondition.NEW
    elif condition_text in {"used", "б у", "бу", "подержанный", "подержанная"}:
        condition = ProductCondition.USED
    elif condition_text in {"refurbished", "восстановленный", "восстановленная"}:
        condition = ProductCondition.REFURBISHED
    else:
        condition = ProductCondition.UNKNOWN
    retrieved_at = row.get("retrieved_at")
    if not isinstance(retrieved_at, datetime):
        retrieved_at = _utc_datetime()
    values = {
        "raw_id": f"{source}:{fingerprint}",
        "offer_id": f"{source}:{fingerprint}",
        "source": source,
        "platform": str(row.get("platform") or platform),
        "title": title,
        "url": url,
        "product_id": product_id,
        "seller_name": str(_first(row, "seller", "store", "merchant") or "").strip(),
        "price": _number(_first(row, "price", "current_price", "extracted_price")),
        "raw_price": _first(row, "price", "current_price", "extracted_price"),
        "old_price": _number(_first(row, "old_price", "original_price", "previous_price")),
        "currency": str(row.get("currency") or "RUB").upper(),
        "availability_text": str(_first(row, "availability", "in_stock", "stock") or ""),
        "condition": condition,
        "city": str(row.get("city") or ""),
        "delivery": str(row.get("delivery") or ""),
        "image_url": str(_first(row, "image_url", "image", "thumbnail") or ""),
        "snippet": str(_first(row, "snippet", "body", "description") or ""),
        "retrieved_at": retrieved_at,
        "raw_metadata": raw_metadata,
        "raw": raw_metadata,
    }
    return construct_model(RawOffer, **values)


def classify_source_exception(exc: BaseException) -> SourceStatus:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status_code", None)
    text = str(exc).casefold()
    if status_code == 401 or "unauthorized" in text:
        return SourceStatus.UNAUTHORIZED
    if status_code == 429 or "429" in text or "rate limit" in text or "too many requests" in text:
        return SourceStatus.RATE_LIMITED
    if status_code in {403, 498} or any(marker in text for marker in ("captcha", "forbidden", "blocked", "access denied")):
        return SourceStatus.BLOCKED
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in text or "timed out" in text:
        return SourceStatus.TIMEOUT
    if any(marker in text for marker in ("non-json", "non json", "invalid json", "bad json", "invalid response")):
        return SourceStatus.INVALID_RESPONSE
    return SourceStatus.ERROR


def make_attempt(
    *,
    source: str,
    query: str,
    status: SourceStatus,
    duration: float,
    raw_count: int = 0,
    error: str = "",
    cache_hit: bool = False,
    tier: QueryTier | None = None,
    attempt_number: int = 1,
) -> SourceAttempt:
    completed_at = _utc_datetime()
    return construct_model(
        SourceAttempt,
        source=source,
        query=query,
        status=status,
        tier=tier,
        duration=duration,
        duration_ms=round(duration * 1000, 3),
        raw_count=raw_count,
        raw_offer_count=raw_count,
        offer_count=raw_count,
        error=error,
        cache_hit=cache_hit,
        attempt_number=attempt_number,
        started_at=completed_at,
        completed_at=completed_at,
    )


def invalid_query_result(source: str, source_query: SourceQuery, error: str) -> SourceResult:
    attempt = make_attempt(
        source=source,
        query=str(getattr(source_query, "query", "") or ""),
        status=SourceStatus.INVALID_QUERY_PLAN,
        duration=0.0,
        error=error,
        tier=getattr(source_query, "tier", None),
    )
    return SourceResult(status=SourceStatus.INVALID_QUERY_PLAN, attempts=(attempt,), error=error)


class LegacyBridgeAdapter(SourceAdapter):
    """Runs a synchronous legacy collector off the event loop.

    The bridge performs no retry of its own. Existing collectors may make at
    most their already-bounded safe retry; anti-bot responses are never worked
    around here.
    """

    def __init__(self, search_callable: LegacySearch | None = None) -> None:
        self._search_callable = search_callable

    def _load_search_callable(self) -> LegacySearch:
        if self._search_callable is None:
            raise RuntimeError(f"legacy bridge for {self.name} is not configured")
        return self._search_callable

    def prepare_query(self, source_query: SourceQuery) -> str:
        return str(getattr(source_query, "query", "") or "").strip()

    async def _invoke(
        self,
        query: str,
        request: SearchRequestV2,
        context: SourceContext,
    ) -> Any:
        function = self._load_search_callable()
        if inspect.iscoroutinefunction(function):
            return await function(query, request, context)
        value = await asyncio.to_thread(function, query, request, context)
        if isinstance(value, Awaitable):
            return await value
        return value

    @staticmethod
    def _unpack_payload(payload: Any) -> tuple[Sequence[Mapping[str, Any]], str, str]:
        if payload is None:
            return (), "", ""
        if isinstance(payload, Mapping):
            rows = payload.get("raw_offers") or payload.get("offers") or payload.get("candidates") or ()
            status = str(payload.get("status") or "")
            error = str(payload.get("error") or "")
            return tuple(item for item in rows if isinstance(item, Mapping)), status, error
        if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes)):
            return tuple(item for item in payload if isinstance(item, Mapping)), "", ""
        raise ValueError(f"invalid response type: {type(payload).__name__}")

    @staticmethod
    def _payload_status(value: str, *, has_rows: bool, error: str = "") -> SourceStatus:
        normalized = normalize_constraint_token(value).replace(" ", "_")
        aliases = {
            "ok": SourceStatus.SUCCESS,
            "success": SourceStatus.SUCCESS,
            "partial_success": SourceStatus.PARTIAL_SUCCESS,
            "empty": SourceStatus.EMPTY,
            "blocked": SourceStatus.BLOCKED,
            "captcha": SourceStatus.BLOCKED,
            "rate_limited": SourceStatus.RATE_LIMITED,
            "unauthorized": SourceStatus.UNAUTHORIZED,
            "timeout": SourceStatus.TIMEOUT,
            "bad_json": SourceStatus.INVALID_RESPONSE,
            "invalid_response": SourceStatus.INVALID_RESPONSE,
            "error": SourceStatus.ERROR,
        }
        status = aliases.get(normalized)
        if status is None and error:
            status = classify_source_exception(RuntimeError(error))
        if status is None:
            status = SourceStatus.SUCCESS if has_rows else SourceStatus.EMPTY
        if has_rows and status not in {SourceStatus.SUCCESS, SourceStatus.PARTIAL_SUCCESS}:
            return SourceStatus.PARTIAL_SUCCESS
        return status

    async def search(
        self,
        request: SearchRequestV2,
        source_query: SourceQuery,
        context: SourceContext,
    ) -> SourceResult:
        valid, rejection = validate_source_query(source_query)
        if not valid:
            return invalid_query_result(self.name, source_query, rejection)

        started = time.monotonic()
        query = self.prepare_query(source_query)
        try:
            payload = await self._invoke(query, request, context)
            rows, raw_status, error = self._unpack_payload(payload)
            offers: list[RawOffer] = []
            row_errors: list[str] = []
            for row in rows[: max(0, int(context.limit))]:
                try:
                    offers.append(raw_offer_from_legacy(row, source=self.name, platform=self.platform))
                except Exception as exc:  # one malformed row must not discard useful rows
                    row_errors.append(str(exc))
            duration = time.monotonic() - started
            status = self._payload_status(raw_status, has_rows=bool(offers), error=error)
            if row_errors and offers:
                status = SourceStatus.PARTIAL_SUCCESS
            elif row_errors and not offers:
                status = SourceStatus.INVALID_RESPONSE
            combined_error = "; ".join(part for part in (error, *row_errors[:3]) if part)
            attempt = make_attempt(
                source=self.name,
                query=query,
                status=status,
                duration=duration,
                raw_count=len(offers),
                error=combined_error,
                tier=getattr(source_query, "tier", None),
            )
            return SourceResult(
                status=status,
                raw_offers=tuple(offers),
                attempts=(attempt,),
                duration=duration,
                error=combined_error,
            )
        except Exception as exc:
            duration = time.monotonic() - started
            status = classify_source_exception(exc)
            error = str(exc)[:500]
            attempt = make_attempt(
                source=self.name,
                query=query,
                status=status,
                duration=duration,
                error=error,
                tier=getattr(source_query, "tier", None),
            )
            rate_info = {"retry_after": getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")}
            return SourceResult(
                status=status,
                attempts=(attempt,),
                duration=duration,
                error=error,
                rate_limit_info=rate_info if status is SourceStatus.RATE_LIMITED else {},
            )


class SiteExactSearchBridge(LegacyBridgeAdapter):
    domain: str = ""

    def prepare_query(self, source_query: SourceQuery) -> str:
        query = super().prepare_query(source_query)
        if self.domain and f"site:{self.domain}" not in query.casefold():
            return f"{query} site:{self.domain}"
        return query


def generic_web_legacy_search(query: str, _request: SearchRequestV2, context: SourceContext) -> list[dict[str, Any]]:
    from app.product_search import GenericSearchAdapter

    return GenericSearchAdapter().search(query, limit=context.limit)


def wildberries_legacy_search(query: str, _request: SearchRequestV2, context: SourceContext) -> list[dict[str, Any]]:
    from app.product_search import WildberriesAdapter

    return WildberriesAdapter().search(query, limit=context.limit)


def direct_retail_legacy_search(key: str) -> LegacySearch:
    def invoke(query: str, request: SearchRequestV2, _context: SourceContext) -> Mapping[str, Any]:
        from app.sources.direct_retail_source import _source_by_key, debug_search_direct_retail_source, settings

        if not settings.DIRECT_RETAIL_ENABLED:
            return {"status": "empty", "candidates": [], "error": ""}

        parsed = {
            "category": getattr(request, "category", ""),
            "brand": getattr(request, "brand", ""),
            "model": getattr(request, "canonical_model", ""),
            "city": getattr(request, "city", ""),
            "budget": getattr(request, "budget", None),
            "required_specs": dict(getattr(request, "required_specs", {}) or {}),
        }
        return debug_search_direct_retail_source(_source_by_key(key), query, parsed)

    return invoke


__all__ = [
    "LegacyBridgeAdapter",
    "SiteExactSearchBridge",
    "SourceAdapter",
    "SourceCapabilities",
    "SourceContext",
    "SourceResult",
    "classify_source_exception",
    "construct_model",
    "direct_retail_legacy_search",
    "generic_web_legacy_search",
    "invalid_query_result",
    "make_attempt",
    "missing_hard_tokens",
    "normalize_constraint_token",
    "raw_offer_from_legacy",
    "validate_source_query",
    "wildberries_legacy_search",
]
