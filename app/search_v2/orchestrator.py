"""Bounded async fan-out for Search Engine V2 source adapters."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
import time
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.search_v2.models import QueryPlan, RawOffer, SearchRequestV2, SourceAttempt, SourceQuery, SourceStatus

from .adapters.base import SourceContext, SourceResult, invalid_query_result, make_attempt
from .cache import MemorySourceCache, SourceCache, build_source_cache_key
from .source_policy import SourcePolicy, SourceSelection
from .source_registry import SourceRegistry, build_default_registry


SnapshotCallback = Callable[["PartialSnapshot"], Any | Awaitable[Any]]


@dataclass(frozen=True)
class PartialSnapshot:
    completed_sources: tuple[str, ...]
    source_results: Mapping[str, SourceResult]
    raw_offer_count: int
    attempt_count: int
    duration: float


@dataclass(frozen=True)
class OrchestrationResult:
    source_results: Mapping[str, SourceResult]
    raw_offers: tuple[RawOffer, ...]
    attempts: tuple[SourceAttempt, ...]
    snapshots: tuple[PartialSnapshot, ...]
    rejected_queries: tuple[tuple[SourceQuery, str], ...]
    status: SourceStatus
    duration: float
    errors: tuple[str, ...] = ()
    case_timed_out: bool = False

    @property
    def results(self) -> Mapping[str, SourceResult]:
        return self.source_results


def _queries_from_plan(query_plan: QueryPlan | Iterable[SourceQuery]) -> tuple[SourceQuery, ...]:
    if isinstance(query_plan, (str, bytes)):
        return ()
    if isinstance(query_plan, Iterable):
        return tuple(query_plan)
    for name in ("source_queries", "queries"):
        value = getattr(query_plan, name, None)
        if value is not None:
            return tuple(value)
    return ()


def _result_status(results: Sequence[SourceResult]) -> SourceStatus:
    if not results:
        return SourceStatus.EMPTY
    statuses = {result.status for result in results}
    has_offers = any(result.raw_offers for result in results)
    if has_offers:
        if statuses <= {SourceStatus.SUCCESS}:
            return SourceStatus.SUCCESS
        return SourceStatus.PARTIAL_SUCCESS
    if statuses == {SourceStatus.EMPTY}:
        return SourceStatus.EMPTY
    if len(statuses) == 1:
        return next(iter(statuses))
    return SourceStatus.PARTIAL_SUCCESS if SourceStatus.SUCCESS in statuses else SourceStatus.ERROR


def _combine_results(results: Sequence[SourceResult], *, duration: float) -> SourceResult:
    offers = tuple(offer for result in results for offer in result.raw_offers)
    attempts = tuple(attempt for result in results for attempt in result.attempts)
    errors = "; ".join(dict.fromkeys(result.error for result in results if result.error))
    cache_hits = sum(bool(result.cache_info.get("hit")) for result in results)
    return SourceResult(
        status=_result_status(results),
        raw_offers=offers,
        attempts=attempts,
        duration=duration,
        error=errors,
        cache_info={"hits": cache_hits, "queries": len(results)},
    )


class SearchSourceOrchestrator:
    def __init__(
        self,
        registry: SourceRegistry | None = None,
        *,
        policy: SourcePolicy | None = None,
        cache: SourceCache | None = None,
        max_concurrency: int = 3,
        per_source_timeout: float = 12.0,
        case_timeout: float = 30.0,
        result_limit: int = 10,
    ) -> None:
        if not 1 <= max_concurrency <= 3:
            raise ValueError("max_concurrency must be between 1 and 3")
        if per_source_timeout <= 0 or case_timeout <= 0:
            raise ValueError("timeouts must be positive")
        self.registry = registry if registry is not None else build_default_registry()
        self.policy = policy or SourcePolicy(max_adapters=3, max_queries_per_source=2)
        self.cache = cache if cache is not None else MemorySourceCache()
        self.max_concurrency = max_concurrency
        self.per_source_timeout = float(per_source_timeout)
        self.case_timeout = float(case_timeout)
        self.result_limit = max(1, int(result_limit))

    async def _run_source(
        self,
        source: str,
        request: SearchRequestV2,
        queries: Sequence[SourceQuery],
        semaphore: asyncio.Semaphore,
    ) -> SourceResult:
        adapter = self.registry.require(source)
        started = time.monotonic()
        deadline = started + self.per_source_timeout
        query_results: list[SourceResult] = []
        async with semaphore:
            for source_query in queries[:2]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    query_results.append(
                        self._timeout_result(source, (source_query,), f"source timeout after {self.per_source_timeout:g}s")
                    )
                    break
                key = build_source_cache_key(
                    request,
                    source_query,
                    adapter_version=str(getattr(adapter, "version", "1")),
                )
                cached = self.cache.get(key)
                if cached is not None:
                    query_results.append(cached)
                    continue
                try:
                    result = await asyncio.wait_for(
                        adapter.search(
                            request,
                            source_query,
                            SourceContext(limit=self.result_limit, timeout=remaining),
                        ),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    result = self._timeout_result(
                        source,
                        (source_query,),
                        f"source timeout after {self.per_source_timeout:g}s",
                    )
                self.cache.set(key, result)
                query_results.append(result)
                if result.status is SourceStatus.TIMEOUT:
                    break
        return _combine_results(query_results, duration=time.monotonic() - started)

    def _timeout_result(self, source: str, queries: Sequence[SourceQuery], message: str) -> SourceResult:
        query = str(getattr(queries[0], "query", "") or "") if queries else ""
        attempt = make_attempt(
            source=source,
            query=query,
            status=SourceStatus.TIMEOUT,
            duration=self.per_source_timeout,
            error=message,
            tier=getattr(queries[0], "tier", None) if queries else None,
        )
        return SourceResult(
            status=SourceStatus.TIMEOUT,
            attempts=(attempt,),
            duration=self.per_source_timeout,
            error=message,
        )

    @staticmethod
    async def _notify(callback: SnapshotCallback | None, snapshot: PartialSnapshot) -> None:
        if callback is None:
            return
        try:
            value = callback(snapshot)
            if inspect.isawaitable(value):
                await value
        except Exception:
            # Observability must never make source collection fail.
            return

    async def run(
        self,
        request: SearchRequestV2,
        query_plan: QueryPlan | Iterable[SourceQuery],
        *,
        on_snapshot: SnapshotCallback | None = None,
    ) -> OrchestrationResult:
        started = time.monotonic()
        selection = self.policy.select(self.registry, _queries_from_plan(query_plan))
        semaphore = asyncio.Semaphore(self.max_concurrency)
        queue: asyncio.Queue[tuple[str, SourceResult]] = asyncio.Queue()
        tasks: dict[str, asyncio.Task[None]] = {}

        async def worker(source: str, queries: Sequence[SourceQuery]) -> None:
            try:
                result = await self._run_source(source, request, queries, semaphore)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = str(exc)[:500]
                query = str(getattr(queries[0], "query", "") or "") if queries else ""
                attempt = make_attempt(
                    source=source,
                    query=query,
                    status=SourceStatus.ERROR,
                    duration=time.monotonic() - started,
                    error=error,
                )
                result = SourceResult(status=SourceStatus.ERROR, attempts=(attempt,), error=error)
            await queue.put((source, result))

        for source in selection.sources:
            tasks[source] = asyncio.create_task(worker(source, selection.queries_by_source[source]))

        results: dict[str, SourceResult] = {}
        snapshots: list[PartialSnapshot] = []
        deadline = started + self.case_timeout
        case_timed_out = False
        while len(results) < len(tasks):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                case_timed_out = True
                break
            try:
                source, result = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                case_timed_out = True
                break
            results[source] = result
            snapshot = PartialSnapshot(
                completed_sources=tuple(results),
                source_results=MappingProxyType(dict(results)),
                raw_offer_count=sum(len(item.raw_offers) for item in results.values()),
                attempt_count=sum(len(item.attempts) for item in results.values()),
                duration=time.monotonic() - started,
            )
            snapshots.append(snapshot)
            await self._notify(on_snapshot, snapshot)

        if case_timed_out:
            for source, task in tasks.items():
                if source in results:
                    continue
                task.cancel()
                queries = selection.queries_by_source[source]
                results[source] = self._timeout_result(source, queries, f"case timeout after {self.case_timeout:g}s")
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        else:
            await asyncio.gather(*tasks.values(), return_exceptions=True)

        rejected_results = [
            invalid_query_result(str(getattr(query, "source", "") or "unknown"), query, reason)
            for query, reason in selection.rejected_queries
            if "budget exceeded" not in reason
        ]
        source_results = tuple(results.values())
        raw_offers = tuple(offer for result in source_results for offer in result.raw_offers)
        attempts = tuple(attempt for result in source_results for attempt in result.attempts)
        attempts += tuple(attempt for result in rejected_results for attempt in result.attempts)
        errors = tuple(dict.fromkeys(result.error for result in source_results if result.error))
        status = _result_status(source_results)
        if case_timed_out and raw_offers:
            status = SourceStatus.PARTIAL_SUCCESS
        elif case_timed_out:
            status = SourceStatus.TIMEOUT
        return OrchestrationResult(
            source_results=MappingProxyType(dict(results)),
            raw_offers=raw_offers,
            attempts=attempts,
            snapshots=tuple(snapshots),
            rejected_queries=selection.rejected_queries,
            status=status,
            duration=time.monotonic() - started,
            errors=errors,
            case_timed_out=case_timed_out,
        )

    async def search(
        self,
        request: SearchRequestV2,
        query_plan: QueryPlan | Iterable[SourceQuery],
        *,
        on_snapshot: SnapshotCallback | None = None,
    ) -> OrchestrationResult:
        return await self.run(request, query_plan, on_snapshot=on_snapshot)


# Concise public name used by the V2 service layer.
SearchOrchestrator = SearchSourceOrchestrator
SourceOrchestrator = SearchSourceOrchestrator


__all__ = [
    "OrchestrationResult",
    "PartialSnapshot",
    "SearchOrchestrator",
    "SearchSourceOrchestrator",
    "SourceOrchestrator",
]
