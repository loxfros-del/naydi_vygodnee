from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from app.search_v2.adapters.base import SourceAdapter, SourceCapabilities, SourceContext, SourceResult
from app.search_v2.cache import (
    MemorySourceCache,
    SOURCE_CACHE_NAMESPACE,
    SQLiteSourceCache,
    build_source_cache_key,
    source_cache_material,
)
from app.search_v2.models import (
    ProductCondition,
    QueryTier,
    RawOffer,
    SearchRequestV2,
    SourceAttempt,
    SourceQuery,
    SourceStatus,
)
from app.search_v2.orchestrator import SearchOrchestrator
from app.search_v2.source_policy import SourcePolicy
from app.search_v2.source_registry import SourceRegistry
from tools.test_search_v2_adapters import make_query, make_request


class FakeAdapter(SourceAdapter):
    platform = "Test"
    version = "fake-1"
    capabilities = SourceCapabilities()

    def __init__(
        self,
        name: str,
        *,
        delay: float = 0.0,
        status: SourceStatus = SourceStatus.SUCCESS,
        error: str = "",
        raises: bool = False,
        tracker: dict[str, int] | None = None,
        delays_by_query: dict[str, float] | None = None,
    ) -> None:
        self.name = name
        self.delay = delay
        self.status = status
        self.error = error
        self.raises = raises
        self.tracker = tracker
        self.delays_by_query = delays_by_query or {}
        self.calls: list[str] = []

    async def search(self, request, source_query, context):
        self.calls.append(source_query.query)
        if self.tracker is not None:
            self.tracker["active"] = self.tracker.get("active", 0) + 1
            self.tracker["max_active"] = max(self.tracker.get("max_active", 0), self.tracker["active"])
        try:
            await asyncio.sleep(self.delays_by_query.get(source_query.query, self.delay))
            if self.raises:
                raise RuntimeError(self.error or f"{self.name} failed")
            offers = ()
            if self.status in {SourceStatus.SUCCESS, SourceStatus.PARTIAL_SUCCESS}:
                offers = (RawOffer(
                    source=self.name,
                    platform=self.platform,
                    title=f"{request.canonical_model} 256 ГБ from {self.name}",
                    url=f"https://example.test/{self.name}/{len(self.calls)}",
                    product_id=f"{self.name}-{len(self.calls)}",
                    price=79_000,
                    condition=ProductCondition.NEW,
                ),)
            attempt = SourceAttempt(
                source=self.name,
                query=source_query.query,
                tier=source_query.tier,
                status=self.status,
                raw_offer_count=len(offers),
                error=self.error,
            )
            return SourceResult(
                status=self.status,
                raw_offers=offers,
                attempts=(attempt,),
                error=self.error,
            )
        finally:
            if self.tracker is not None:
                self.tracker["active"] -= 1


class CacheKeyTests(unittest.TestCase):
    def test_key_namespace_is_stable_and_model_strict(self) -> None:
        request = make_request()
        query = make_query("ozon")
        key = build_source_cache_key(request, query, adapter_version="1")
        self.assertTrue(key.startswith(f"{SOURCE_CACHE_NAMESPACE}:2:"))
        self.assertEqual(key, build_source_cache_key(request, query, adapter_version="1"))
        self.assertEqual(len(key.rsplit(":", 1)[-1]), 64)

        plain = replace(
            request,
            original_query="Apple iPhone 16 256 ГБ новый",
            canonical_model="iPhone 16",
            model_modifiers=[],
            hard_tokens=["Apple", "iPhone 16", "256 ГБ", "new"],
        )
        self.assertNotEqual(key, build_source_cache_key(plain, query, adapter_version="1"))

        storage_128 = replace(
            request,
            required_specs={"storage_gb": 128},
            hard_tokens=["Apple", "iPhone 16 Pro", "128 ГБ", "new"],
        )
        self.assertNotEqual(key, build_source_cache_key(storage_128, query, adapter_version="1"))
        self.assertNotEqual(key, build_source_cache_key(request, query, adapter_version="2"))

    def test_key_includes_tier_query_and_hard_annotation(self) -> None:
        request = make_request()
        query = make_query("ozon")
        key = build_source_cache_key(request, query, adapter_version="1")
        relaxed = replace(query, tier=QueryTier.EXACT_RELAXED)
        changed_text = replace(query, query=query.query + " купить")
        changed_hard = replace(query, hard_tokens_preserved=query.hard_tokens_preserved[:-1])
        alias = replace(query, source="ozon_search")
        self.assertNotEqual(key, build_source_cache_key(request, relaxed, adapter_version="1"))
        self.assertNotEqual(key, build_source_cache_key(request, changed_text, adapter_version="1"))
        self.assertNotEqual(key, build_source_cache_key(request, changed_hard, adapter_version="1"))
        self.assertEqual(key, build_source_cache_key(request, alias, adapter_version="1"))
        material = source_cache_material(request, query, adapter_version="1")
        self.assertEqual(material["identity"]["canonical_model"], "iphone 16 pro")
        self.assertEqual(material["identity"]["required_specs"], {"storage_gb": 256})

    def test_memory_cache_ttl_lru_and_invalid_plan(self) -> None:
        now = [10.0]
        cache = MemorySourceCache(max_entries=2, clock=lambda: now[0])
        success = SourceResult(status=SourceStatus.SUCCESS)
        cache.set("a", success, ttl=5)
        hit = cache.get("a")
        self.assertIsNotNone(hit)
        self.assertTrue(hit.cache_info["hit"])
        self.assertEqual(hit.cache_info["age"], 0.0)
        now[0] = 16.0
        self.assertIsNone(cache.get("a"))

        cache.set("a", success, ttl=50)
        cache.set("b", success, ttl=50)
        self.assertIsNotNone(cache.get("a"))  # a is newest
        cache.set("c", success, ttl=50)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(len(cache), 2)

        cache.set("invalid", SourceResult(status=SourceStatus.INVALID_QUERY_PLAN))
        self.assertIsNone(cache.get("invalid"))
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_sqlite_bridge_round_trip_uses_temp_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "search-cache.sqlite3"
            cache = SQLiteSourceCache(path)
            offer = RawOffer(
                source="ozon",
                platform="Ozon",
                title="Apple iPhone 16 Pro 256",
                url="https://ozon.ru/product/1",
                product_id="1",
                seller_name="Store",
                price=79_990,
                condition=ProductCondition.NEW,
                raw_metadata={"rating": 4.8},
            )
            attempt = SourceAttempt(
                source="ozon",
                query="Apple iPhone 16 Pro 256 ГБ новый",
                tier=QueryTier.STRICT,
                status=SourceStatus.SUCCESS,
                raw_offer_count=1,
            )
            result = SourceResult(
                status=SourceStatus.SUCCESS,
                raw_offers=(offer,),
                attempts=(attempt,),
                duration=0.125,
            )
            cache.set("search_v2:source:2:key", result, ttl=30)
            restored = cache.get("search_v2:source:2:key")
            self.assertIsNotNone(restored)
            self.assertIs(restored.status, SourceStatus.SUCCESS)
            self.assertEqual(len(restored.raw_offers), 1)
            self.assertEqual(restored.raw_offers[0].product_id, "1")
            self.assertEqual(restored.raw_offers[0].seller_name, "Store")
            self.assertIs(restored.raw_offers[0].condition, ProductCondition.NEW)
            self.assertEqual(restored.attempts[0].tier, QueryTier.STRICT)
            self.assertTrue(restored.cache_info["hit"])
            self.assertTrue(path.exists())


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_failure_keeps_successful_offer(self) -> None:
        good = FakeAdapter("good")
        bad = FakeAdapter("bad", raises=True, error="source exploded")
        orchestrator = SearchOrchestrator(
            SourceRegistry([good, bad]),
            per_source_timeout=0.2,
            case_timeout=0.5,
        )
        result = await orchestrator.run(make_request(), [make_query("good"), make_query("bad")])
        self.assertIs(result.status, SourceStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(result.raw_offers), 1)
        self.assertEqual(result.raw_offers[0].source, "good")
        self.assertIs(result.source_results["good"].status, SourceStatus.SUCCESS)
        self.assertIs(result.source_results["bad"].status, SourceStatus.ERROR)
        self.assertIn("source exploded", result.errors)
        self.assertEqual(len(result.snapshots), 2)
        self.assertEqual(len(result.attempts), 2)

    async def test_concurrency_is_bounded_at_three(self) -> None:
        tracker = {"active": 0, "max_active": 0}
        adapters = [FakeAdapter(name, delay=0.03, tracker=tracker) for name in ("one", "two", "three")]
        orchestrator = SearchOrchestrator(
            SourceRegistry(adapters),
            max_concurrency=3,
            per_source_timeout=0.2,
            case_timeout=0.5,
        )
        result = await orchestrator.run(make_request(), [make_query(adapter.name, priority=100) for adapter in adapters])
        self.assertEqual(tracker["max_active"], 3)
        self.assertEqual(tracker["active"], 0)
        self.assertEqual(len(result.source_results), 3)
        self.assertEqual(len(result.raw_offers), 3)
        self.assertIs(result.status, SourceStatus.SUCCESS)

        with self.assertRaises(ValueError):
            SearchOrchestrator(SourceRegistry(), max_concurrency=4)

    async def test_case_adapter_budget_and_per_source_query_budget(self) -> None:
        adapters = [FakeAdapter(name) for name in ("one", "two", "three", "four")]
        orchestrator = SearchOrchestrator(SourceRegistry(adapters))
        queries = [make_query(adapter.name, priority=100 - index) for index, adapter in enumerate(adapters)]
        result = await orchestrator.run(make_request(), queries)
        self.assertEqual(tuple(result.source_results), ("one", "two", "three"))
        self.assertEqual(adapters[3].calls, [])
        self.assertEqual(len(result.rejected_queries), 1)
        self.assertIn("adapter budget", result.rejected_queries[0][1])

        only = FakeAdapter("only")
        orchestrator = SearchOrchestrator(SourceRegistry([only]))
        three_queries = [replace(make_query("only"), query=make_query("only").query + f" {index}") for index in range(3)]
        result = await orchestrator.run(make_request(), three_queries)
        self.assertEqual(len(only.calls), 2)
        self.assertEqual(len(result.raw_offers), 2)
        self.assertEqual(len(result.rejected_queries), 1)
        self.assertIn("per-source", result.rejected_queries[0][1])

    async def test_per_source_timeout_does_not_destroy_other_results(self) -> None:
        fast = FakeAdapter("fast", delay=0.001)
        slow = FakeAdapter("slow", delay=0.2)
        orchestrator = SearchOrchestrator(
            SourceRegistry([fast, slow]),
            per_source_timeout=0.03,
            case_timeout=0.2,
        )
        result = await orchestrator.run(make_request(), [make_query("fast"), make_query("slow")])
        self.assertFalse(result.case_timed_out)
        self.assertIs(result.status, SourceStatus.PARTIAL_SUCCESS)
        self.assertEqual([offer.source for offer in result.raw_offers], ["fast"])
        self.assertIs(result.source_results["slow"].status, SourceStatus.TIMEOUT)
        self.assertEqual(len(result.source_results["fast"].raw_offers), 1)

    async def test_case_timeout_keeps_completed_snapshot(self) -> None:
        fast = FakeAdapter("fast", delay=0.001)
        slow = FakeAdapter("slow", delay=0.2)
        snapshots = []
        orchestrator = SearchOrchestrator(
            SourceRegistry([fast, slow]),
            per_source_timeout=1,
            case_timeout=0.03,
        )
        result = await orchestrator.run(
            make_request(), [make_query("fast"), make_query("slow")], on_snapshot=snapshots.append
        )
        self.assertTrue(result.case_timed_out)
        self.assertIs(result.status, SourceStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(result.raw_offers), 1)
        self.assertIs(result.source_results["slow"].status, SourceStatus.TIMEOUT)
        self.assertGreaterEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].raw_offer_count, 1)

    async def test_second_query_timeout_keeps_first_query_offer(self) -> None:
        first = make_query("source", priority=100)
        second = replace(first, query=first.query + " купить", priority=90)
        adapter = FakeAdapter("source", delays_by_query={first.query: 0.001, second.query: 0.2})
        orchestrator = SearchOrchestrator(
            SourceRegistry([adapter]),
            per_source_timeout=0.04,
            case_timeout=0.2,
        )
        result = await orchestrator.run(make_request(), [first, second])
        source_result = result.source_results["source"]
        self.assertIs(source_result.status, SourceStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(source_result.raw_offers), 1)
        self.assertEqual(len(source_result.attempts), 2)
        self.assertIs(source_result.attempts[1].status, SourceStatus.TIMEOUT)
        self.assertFalse(result.case_timed_out)

    async def test_cache_hit_and_model_distinction(self) -> None:
        adapter = FakeAdapter("source")
        cache = MemorySourceCache()
        orchestrator = SearchOrchestrator(SourceRegistry([adapter]), cache=cache)
        query = make_query("source")
        first = await orchestrator.run(make_request(), [query])
        second = await orchestrator.run(make_request(), [query])
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(first.raw_offers), 1)
        self.assertEqual(len(second.raw_offers), 1)
        self.assertEqual(second.source_results["source"].cache_info["hits"], 1)
        self.assertTrue(second.attempts[0].cache_hit)

        other_model = replace(
            make_request(),
            canonical_model="iPhone 16",
            model_modifiers=[],
            hard_tokens=["Apple", "iPhone 16", "256 ГБ", "new"],
        )
        await orchestrator.run(other_model, [query])
        self.assertEqual(len(adapter.calls), 2)

    async def test_invalid_plan_is_rejected_before_adapter(self) -> None:
        adapter = FakeAdapter("source")
        query = make_query("source", text="Apple iPhone 16 256 ГБ новый")
        orchestrator = SearchOrchestrator(SourceRegistry([adapter]))
        result = await orchestrator.run(make_request(), [query])
        self.assertEqual(adapter.calls, [])
        self.assertEqual(result.source_results, {})
        self.assertEqual(len(result.rejected_queries), 1)
        self.assertEqual(len(result.attempts), 1)
        self.assertIs(result.attempts[0].status, SourceStatus.INVALID_QUERY_PLAN)

    async def test_snapshot_callback_failure_is_isolated(self) -> None:
        adapter = FakeAdapter("source")

        def broken_callback(_snapshot):
            raise RuntimeError("observer unavailable")

        result = await SearchOrchestrator(SourceRegistry([adapter])).run(
            make_request(), [make_query("source")], on_snapshot=broken_callback
        )
        self.assertIs(result.status, SourceStatus.SUCCESS)
        self.assertEqual(len(result.raw_offers), 1)
        self.assertEqual(len(result.snapshots), 1)

    async def test_explicit_empty_registry_is_not_replaced_by_defaults(self) -> None:
        orchestrator = SearchOrchestrator(SourceRegistry())
        self.assertEqual(len(orchestrator.registry), 0)
        result = await orchestrator.run(make_request(), [make_query("ozon")])
        self.assertEqual(result.source_results, {})
        self.assertEqual(len(result.rejected_queries), 1)
        self.assertIn("not registered", result.rejected_queries[0][1])


if __name__ == "__main__":
    unittest.main()
