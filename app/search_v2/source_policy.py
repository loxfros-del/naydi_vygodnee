"""Bounded source selection and query-budget enforcement for Search V2."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from app.search_v2.models import SourceQuery

from .adapters.base import validate_source_query
from .source_registry import SourceRegistry, canonical_source_name


# Price-bearing structured shopping comes first when configured. The adapter is
# a no-I/O EMPTY source without credentials, so Ozon and Avito remain usable.
DISCOVERY_CORE = ("shopping_search", "yandex_market", "ozon", "avito", "wildberries")
RELIABLE_ANCHORS = ("dns", "citilink", "mvideo")
GENERIC_FALLBACK = ("generic_exact",)


@dataclass(frozen=True)
class SourceSelection:
    sources: tuple[str, ...]
    queries_by_source: Mapping[str, tuple[SourceQuery, ...]]
    rejected_queries: tuple[tuple[SourceQuery, str], ...] = ()

    @property
    def query_count(self) -> int:
        return sum(len(items) for items in self.queries_by_source.values())


class SourcePolicy:
    def __init__(self, *, max_adapters: int = 3, max_queries_per_source: int = 2) -> None:
        if not 1 <= max_adapters <= 3:
            raise ValueError("max_adapters must be between 1 and 3")
        if not 1 <= max_queries_per_source <= 2:
            raise ValueError("max_queries_per_source must be between 1 and 2")
        self.max_adapters = max_adapters
        self.max_queries_per_source = max_queries_per_source

    @staticmethod
    def _priority(source_query: SourceQuery) -> tuple[int, str]:
        try:
            priority = int(getattr(source_query, "priority", 100))
        except (TypeError, ValueError):
            priority = 100
        # Query planner uses a larger number for a stricter/more important tier.
        return -priority, str(getattr(source_query, "query", ""))

    def select(self, registry: SourceRegistry, queries: Iterable[SourceQuery]) -> SourceSelection:
        ordered = sorted(tuple(queries), key=self._priority)
        accepted: dict[str, list[SourceQuery]] = {}
        rejected: list[tuple[SourceQuery, str]] = []

        for source_query in ordered:
            source = canonical_source_name(getattr(source_query, "source", ""))
            valid, reason = validate_source_query(source_query)
            if not valid:
                rejected.append((source_query, reason))
                continue
            if source not in registry:
                rejected.append((source_query, f"adapter not registered: {source or '<empty>'}"))
                continue
            bucket = accepted.setdefault(source, [])
            if len(bucket) >= self.max_queries_per_source:
                rejected.append((source_query, "per-source query budget exceeded"))
                continue
            bucket.append(source_query)

        source_priority = {
            name: index
            for index, name in enumerate(DISCOVERY_CORE + RELIABLE_ANCHORS + GENERIC_FALLBACK)
        }
        ranked_sources = sorted(
            accepted,
            key=lambda source: (
                min(self._priority(item)[0] for item in accepted[source]),
                source_priority.get(source, 999),
                source,
            ),
        )
        selected = tuple(ranked_sources[: self.max_adapters])
        for source in ranked_sources[self.max_adapters :]:
            rejected.extend((item, "case adapter budget exceeded") for item in accepted[source])

        selected_queries = {source: tuple(accepted[source]) for source in selected}
        return SourceSelection(selected, selected_queries, tuple(rejected))

    def fallback_sources(self, registry: SourceRegistry) -> tuple[str, ...]:
        return tuple(source for source in GENERIC_FALLBACK if source in registry)[:1]


def default_source_order(registry: SourceRegistry) -> tuple[str, ...]:
    all_names = DISCOVERY_CORE + RELIABLE_ANCHORS + GENERIC_FALLBACK
    return tuple(name for name in all_names if name in registry)


__all__ = [
    "DISCOVERY_CORE",
    "GENERIC_FALLBACK",
    "RELIABLE_ANCHORS",
    "SourcePolicy",
    "SourceSelection",
    "default_source_order",
]
