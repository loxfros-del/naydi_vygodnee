"""Pure source planning, budgets and candidate-pool quotas."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Callable, Iterable, Sequence, TypeVar

from app.sources.offer import SourceStatus, normalize_source_status as _normalize_source_status


DISCOVERY_CORE: tuple[str, ...] = (
    "yandex_market_search",
    "ozon_search",
    "wildberries",
    "avito_search",
)

RELIABLE_ANCHORS: tuple[str, ...] = (
    "dns_search",
    "citilink_search",
    "mvideo_search",
    "official_store",
    "federal_retail",
)


class SourceGroup(str, Enum):
    DISCOVERY = "DISCOVERY_CORE"
    RELIABLE = "RELIABLE_ANCHOR"
    AUXILIARY = "AUXILIARY"


_SOURCE_ALIASES = {
    "yandex_market": "yandex_market_search",
    "yandex_market_direct": "yandex_market_search",
    "market.yandex.ru": "yandex_market_search",
    "ozon": "ozon_search",
    "ozon.ru": "ozon_search",
    "wb": "wildberries",
    "wildberries.ru": "wildberries",
    "avito": "avito_search",
    "avito.ru": "avito_search",
    "dns": "dns_search",
    "dns_direct": "dns_search",
    "dns-shop.ru": "dns_search",
    "citilink": "citilink_search",
    "citilink_direct": "citilink_search",
    "citilink.ru": "citilink_search",
    "mvideo": "mvideo_search",
    "mvideo_direct": "mvideo_search",
    "mvideo.ru": "mvideo_search",
}


def normalize_source_name(value: object) -> str:
    text = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    return _SOURCE_ALIASES.get(text, text)


def source_group(value: object) -> SourceGroup:
    source = normalize_source_name(value)
    if source in DISCOVERY_CORE:
        return SourceGroup.DISCOVERY
    if source in RELIABLE_ANCHORS:
        return SourceGroup.RELIABLE
    return SourceGroup.AUXILIARY


def normalize_source_status(value: object) -> SourceStatus:
    """Safe strategy-level facade used by a future source runner."""
    return _normalize_source_status(value, default=SourceStatus.ERROR)


@dataclass(frozen=True)
class SearchBudget:
    main_query_limit: int = 1
    variant_query_limit: int = 2
    per_discovery_source_limit: int = 1
    per_anchor_source_limit: int = 1
    max_source_concurrency: int = 3
    max_verification_concurrency: int = 2

    def __post_init__(self) -> None:
        limits = (
            (self.main_query_limit, 1, 1, "main_query_limit"),
            (self.variant_query_limit, 0, 2, "variant_query_limit"),
            (self.per_discovery_source_limit, 0, 1, "per_discovery_source_limit"),
            (self.per_anchor_source_limit, 0, 1, "per_anchor_source_limit"),
            (self.max_source_concurrency, 1, 3, "max_source_concurrency"),
            (self.max_verification_concurrency, 1, 2, "max_verification_concurrency"),
        )
        for value, minimum, maximum, name in limits:
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")


DEFAULT_SEARCH_BUDGET = SearchBudget()


@dataclass(frozen=True)
class SourceRequest:
    source: str
    group: SourceGroup
    query: str
    query_kind: str
    priority: int


@dataclass(frozen=True)
class SearchPlan:
    main_query: str
    variant_queries: tuple[str, ...]
    source_requests: tuple[SourceRequest, ...]
    budget: SearchBudget = DEFAULT_SEARCH_BUDGET

    def source_batches(self) -> tuple[tuple[SourceRequest, ...], ...]:
        """Execution waves respecting the maximum source concurrency."""
        size = self.budget.max_source_concurrency
        return tuple(
            self.source_requests[index:index + size]
            for index in range(0, len(self.source_requests), size)
        )

    def query_count(self, source: object) -> int:
        normalized = normalize_source_name(source)
        return sum(1 for item in self.source_requests if item.source == normalized)


def _clean_query(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,")


def _unique_queries(values: Iterable[object], *, exclude: str = "") -> tuple[str, ...]:
    result: list[str] = []
    seen = {exclude.casefold()} if exclude else set()
    for value in values:
        query = _clean_query(value)
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        result.append(query)
    return tuple(result)


def build_source_plan(
    main_query: object,
    variant_queries: Iterable[object] = (),
    *,
    enabled_sources: Iterable[object] | None = None,
    discovery_sources: Sequence[str] = DISCOVERY_CORE,
    reliable_anchors: Sequence[str] = RELIABLE_ANCHORS,
    budget: SearchBudget = DEFAULT_SEARCH_BUDGET,
) -> SearchPlan:
    """Build a deterministic core-first plan without query multiplication.

    Variants are retained for a generic/category fallback. Direct priority
    sources each receive only the exact main query.
    """
    main = _clean_query(main_query)
    if not main:
        raise ValueError("main_query must not be empty")
    variants = _unique_queries(variant_queries, exclude=main)[:budget.variant_query_limit]
    enabled = None
    if enabled_sources is not None:
        enabled = {normalize_source_name(item) for item in enabled_sources}

    requests: list[SourceRequest] = []
    if budget.per_discovery_source_limit:
        for offset, source_value in enumerate(discovery_sources):
            source = normalize_source_name(source_value)
            if enabled is not None and source not in enabled:
                continue
            requests.append(SourceRequest(source, SourceGroup.DISCOVERY, main, "main", 100 - offset))
    if budget.per_anchor_source_limit:
        for offset, source_value in enumerate(reliable_anchors):
            source = normalize_source_name(source_value)
            if enabled is not None and source not in enabled:
                continue
            requests.append(SourceRequest(source, SourceGroup.RELIABLE, main, "direct", 70 - offset))
    return SearchPlan(main, variants, tuple(requests), budget)


@dataclass(frozen=True)
class CandidatePoolQuota:
    total_limit: int = 10
    discovery_reserved: int = 1
    reliable_reserved: int = 1
    per_source_limit: int = 3

    def __post_init__(self) -> None:
        if self.total_limit < 1:
            raise ValueError("total_limit must be positive")
        if min(self.discovery_reserved, self.reliable_reserved) < 0:
            raise ValueError("reserved quotas must not be negative")
        if self.discovery_reserved + self.reliable_reserved > self.total_limit:
            raise ValueError("reserved quotas exceed total_limit")
        if self.per_source_limit < 1:
            raise ValueError("per_source_limit must be positive")


DEFAULT_POOL_QUOTA = CandidatePoolQuota()
T = TypeVar("T")


def select_with_source_quotas(
    ranked_items: Iterable[T],
    *,
    source_getter: Callable[[T], object],
    identity_getter: Callable[[T], object] | None = None,
    quota: CandidatePoolQuota = DEFAULT_POOL_QUOTA,
) -> list[T]:
    """Preserve discovery and reliable candidates, then fill by rank.

    Input is expected to be hard-filtered and ranked already. This function
    never scores offers or changes their quality decision.
    """
    items = list(ranked_items)
    selected: list[T] = []
    selected_ids: set[str] = set()
    per_source: dict[str, int] = {}

    def identity(item: T) -> str:
        if identity_getter is not None:
            return str(identity_getter(item))
        return str(id(item))

    def add(item: T) -> bool:
        key = identity(item)
        source = normalize_source_name(source_getter(item))
        if key in selected_ids or per_source.get(source, 0) >= quota.per_source_limit:
            return False
        selected.append(item)
        selected_ids.add(key)
        per_source[source] = per_source.get(source, 0) + 1
        return True

    def reserve(group: SourceGroup, count: int) -> None:
        added = 0
        for item in items:
            if added >= count or len(selected) >= quota.total_limit:
                break
            if source_group(source_getter(item)) == group and add(item):
                added += 1

    reserve(SourceGroup.DISCOVERY, quota.discovery_reserved)
    reserve(SourceGroup.RELIABLE, quota.reliable_reserved)
    for item in items:
        if len(selected) >= quota.total_limit:
            break
        add(item)
    return selected


__all__ = [
    "CandidatePoolQuota",
    "DEFAULT_POOL_QUOTA",
    "DEFAULT_SEARCH_BUDGET",
    "DISCOVERY_CORE",
    "RELIABLE_ANCHORS",
    "SearchBudget",
    "SearchPlan",
    "SourceGroup",
    "SourceRequest",
    "build_source_plan",
    "normalize_source_name",
    "normalize_source_status",
    "select_with_source_quotas",
    "source_group",
]
