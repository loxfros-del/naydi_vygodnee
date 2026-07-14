"""Versioned source-result cache keys for Search V2.

This module is deliberately storage-agnostic.  The in-memory implementation is
used by deterministic tests and can be replaced by the existing SQLite cache
without changing key semantics.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Protocol

from app.search_v2.models import (
    ProductCondition,
    QueryTier,
    RawOffer,
    SearchRequestV2,
    SourceAttempt,
    SourceQuery,
    SourceStatus,
)

from .adapters.base import SourceResult, normalize_constraint_token
from .source_registry import canonical_source_name


SOURCE_CACHE_NAMESPACE = "search_v2:source"
SOURCE_CACHE_VERSION = "2"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _request_material(request: SearchRequestV2) -> Mapping[str, Any]:
    # Keep both a normalized whole-request view and explicit identity-critical
    # fields. New dataclass fields are automatically included in ``request``.
    whole = _json_safe(request)
    return {
        "request": whole,
        "identity": {
            "category": normalize_constraint_token(getattr(request, "category", "")),
            "brand": normalize_constraint_token(getattr(request, "brand", "")),
            "canonical_model": normalize_constraint_token(getattr(request, "canonical_model", "")),
            "model_modifiers": _json_safe(getattr(request, "model_modifiers", ())),
            "required_specs": _json_safe(getattr(request, "required_specs", {})),
            "condition": normalize_constraint_token(getattr(request, "condition", "")),
            "hard_tokens": _json_safe(getattr(request, "hard_tokens", ())),
        },
    }


def source_cache_material(
    request: SearchRequestV2,
    source_query: SourceQuery,
    *,
    adapter_version: str,
    namespace: str = SOURCE_CACHE_NAMESPACE,
    cache_version: str = SOURCE_CACHE_VERSION,
) -> Mapping[str, Any]:
    return {
        "namespace": namespace,
        "cache_version": cache_version,
        "adapter_version": str(adapter_version),
        **_request_material(request),
        "source_query": {
            "source": canonical_source_name(getattr(source_query, "source", "")),
            "tier": _json_safe(getattr(source_query, "tier", "")),
            "query": normalize_constraint_token(getattr(source_query, "query", "")),
            "hard_tokens_required": _json_safe(getattr(source_query, "hard_tokens_required", ())),
            "hard_tokens_preserved": _json_safe(getattr(source_query, "hard_tokens_preserved", ())),
            "dropped_soft_tokens": _json_safe(getattr(source_query, "dropped_soft_tokens", ())),
        },
    }


def build_source_cache_key(
    request: SearchRequestV2,
    source_query: SourceQuery,
    *,
    adapter_version: str,
    namespace: str = SOURCE_CACHE_NAMESPACE,
    cache_version: str = SOURCE_CACHE_VERSION,
) -> str:
    material = source_cache_material(
        request,
        source_query,
        adapter_version=adapter_version,
        namespace=namespace,
        cache_version=cache_version,
    )
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{namespace}:{cache_version}:{digest}"


class SourceCache(Protocol):
    def get(self, key: str) -> SourceResult | None: ...

    def set(self, key: str, value: SourceResult, *, ttl: float | None = None) -> None: ...


@dataclass(frozen=True)
class _CacheEntry:
    value: SourceResult
    created_at: float
    expires_at: float


DEFAULT_TTLS: Mapping[SourceStatus, float] = {
    SourceStatus.SUCCESS: 120.0,
    SourceStatus.PARTIAL_SUCCESS: 60.0,
    SourceStatus.EMPTY: 30.0,
    SourceStatus.BLOCKED: 30.0,
    SourceStatus.RATE_LIMITED: 30.0,
    SourceStatus.UNAUTHORIZED: 30.0,
    SourceStatus.TIMEOUT: 10.0,
    SourceStatus.INVALID_RESPONSE: 10.0,
    SourceStatus.ERROR: 10.0,
}


class MemorySourceCache:
    """Small bounded LRU with short negative TTLs and deterministic clock injection."""

    def __init__(self, *, max_entries: int = 256, clock=time.monotonic) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._clock = clock
        self._items: MutableMapping[str, _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> SourceResult | None:
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._items.pop(key, None)
                return None
            if isinstance(self._items, OrderedDict):
                self._items.move_to_end(key)
            cache_info = dict(entry.value.cache_info)
            cache_info.update({"hit": True, "age": max(0.0, now - entry.created_at), "key": key})
            return replace(entry.value, cache_info=cache_info)

    def set(self, key: str, value: SourceResult, *, ttl: float | None = None) -> None:
        if value.status is SourceStatus.INVALID_QUERY_PLAN:
            return
        effective_ttl = DEFAULT_TTLS.get(value.status, 10.0) if ttl is None else float(ttl)
        if effective_ttl <= 0:
            return
        now = self._clock()
        cache_info = dict(value.cache_info)
        cache_info.update({"hit": False, "key": key})
        stored = replace(value, cache_info=cache_info)
        with self._lock:
            self._items[key] = _CacheEntry(stored, now, now + effective_ttl)
            if isinstance(self._items, OrderedDict):
                self._items.move_to_end(key)
                while len(self._items) > self.max_entries:
                    self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _parse_datetime(value: object):
    from datetime import datetime

    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _serialize_result(value: SourceResult) -> dict[str, Any]:
    return {
        "schema": "search_v2:source_result:2",
        "status": value.status.value,
        "raw_offers": [offer.to_dict() for offer in value.raw_offers],
        "attempts": [attempt.to_dict() for attempt in value.attempts],
        "duration": value.duration,
        "error": value.error,
        "rate_limit_info": _json_safe(value.rate_limit_info),
        "cache_info": _json_safe(value.cache_info),
    }


def _deserialize_result(payload: Mapping[str, Any]) -> SourceResult | None:
    if payload.get("schema") != "search_v2:source_result:2":
        return None
    try:
        status = SourceStatus(str(payload.get("status") or SourceStatus.ERROR.value))
    except ValueError:
        return None
    offers: list[RawOffer] = []
    for raw in payload.get("raw_offers") or ():
        if not isinstance(raw, Mapping):
            continue
        try:
            condition = ProductCondition(str(raw.get("condition") or ProductCondition.UNKNOWN.value))
        except ValueError:
            condition = ProductCondition.UNKNOWN
        offers.append(RawOffer(
            source=str(raw.get("source") or ""),
            platform=str(raw.get("platform") or ""),
            title=str(raw.get("title") or ""),
            url=str(raw.get("url") or ""),
            product_id=str(raw.get("product_id") or ""),
            seller_name=str(raw.get("seller_name") or ""),
            price=raw.get("price"),
            old_price=raw.get("old_price"),
            currency=str(raw.get("currency") or "RUB"),
            availability_text=str(raw.get("availability_text") or ""),
            condition=condition,
            city=str(raw.get("city") or ""),
            delivery=str(raw.get("delivery") or ""),
            image_url=str(raw.get("image_url") or ""),
            raw_metadata=dict(raw.get("raw_metadata") or {}),
            retrieved_at=_parse_datetime(raw.get("retrieved_at")) or RawOffer().retrieved_at,
        ))
    attempts: list[SourceAttempt] = []
    for raw in payload.get("attempts") or ():
        if not isinstance(raw, Mapping):
            continue
        try:
            attempt_status = SourceStatus(str(raw.get("status") or SourceStatus.ERROR.value))
        except ValueError:
            attempt_status = SourceStatus.ERROR
        tier_value = raw.get("tier")
        try:
            tier = QueryTier(str(tier_value)) if tier_value else None
        except ValueError:
            tier = None
        attempts.append(SourceAttempt(
            source=str(raw.get("source") or ""),
            query=str(raw.get("query") or ""),
            tier=tier,
            status=attempt_status,
            duration_ms=float(raw.get("duration_ms") or 0.0),
            error=str(raw.get("error") or ""),
            raw_offer_count=int(raw.get("raw_offer_count") or 0),
            cache_hit=bool(raw.get("cache_hit")),
            attempt_number=int(raw.get("attempt_number") or 1),
            started_at=_parse_datetime(raw.get("started_at")),
            completed_at=_parse_datetime(raw.get("completed_at")),
            metadata=dict(raw.get("metadata") or {}),
        ))
    return SourceResult(
        status=status,
        raw_offers=tuple(offers),
        attempts=tuple(attempts),
        duration=float(payload.get("duration") or 0.0),
        error=str(payload.get("error") or ""),
        rate_limit_info=dict(payload.get("rate_limit_info") or {}),
        cache_info=dict(payload.get("cache_info") or {}),
    )


class SQLiteSourceCache:
    """Adapter over the existing ``app.search_cache.SearchCache``.

    It uses only the benchmark/search-cache database supplied by the caller;
    the application database and its schema are never touched.
    """

    cache_version = "search-v2-source-cache-2"

    def __init__(self, backend_or_path: Any) -> None:
        from app.search_cache import SearchCache

        self.backend = (
            backend_or_path
            if isinstance(backend_or_path, SearchCache)
            else SearchCache(Path(backend_or_path), cache_version=self.cache_version)
        )

    def get(self, key: str) -> SourceResult | None:
        lookup = self.backend.get(key, cache_version=self.cache_version)
        if lookup.state != "HIT" or not isinstance(lookup.payload, Mapping):
            return None
        value = _deserialize_result(lookup.payload)
        if value is None:
            return None
        cache_info = dict(value.cache_info)
        cache_info.update({"hit": True, "age": float(lookup.cache_age_seconds), "key": key, "backend": "sqlite"})
        return replace(value, cache_info=cache_info)

    def set(self, key: str, value: SourceResult, *, ttl: float | None = None) -> None:
        if value.status is SourceStatus.INVALID_QUERY_PLAN:
            return
        attempt = value.attempts[0] if value.attempts else None
        source = attempt.source if attempt else ""
        query = attempt.query if attempt else ""
        effective_ttl = DEFAULT_TTLS.get(value.status, 10.0) if ttl is None else float(ttl)
        if effective_ttl <= 0:
            return
        self.backend.put(
            cache_key=key,
            stage=SOURCE_CACHE_NAMESPACE,
            source=source,
            query=query,
            payload=_serialize_result(value),
            status=value.status.value,
            ttl_seconds=max(1, int(effective_ttl)),
            duration_ms=round(value.duration * 1000),
            error_text=value.error,
            cache_version=self.cache_version,
        )


__all__ = [
    "DEFAULT_TTLS",
    "MemorySourceCache",
    "SQLiteSourceCache",
    "SOURCE_CACHE_NAMESPACE",
    "SOURCE_CACHE_VERSION",
    "SourceCache",
    "build_source_cache_key",
    "source_cache_material",
]
