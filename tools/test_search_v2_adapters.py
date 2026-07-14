from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import unittest

from app.search_v2.adapters import (
    AvitoAdapter,
    CitilinkAdapter,
    DnsAdapter,
    GenericExactSearchAdapter,
    MVideoAdapter,
    OzonAdapter,
    WildberriesAdapterV2,
    YandexMarketAdapter,
)
from app.search_v2.adapters.base import (
    SourceContext,
    SourceResult,
    classify_source_exception,
    raw_offer_from_legacy,
    validate_source_query,
)
from app.search_v2.models import (
    ProductCondition,
    QueryTier,
    SearchRequestV2,
    SourceQuery,
    SourceStatus,
)
from app.search_v2.source_policy import SourcePolicy
from app.search_v2.source_registry import SourceRegistry, build_default_registry, canonical_source_name


def make_request(*, model: str = "iPhone 16 Pro", storage: int = 256) -> SearchRequestV2:
    return SearchRequestV2(
        original_query=f"Apple {model} {storage} ГБ новый",
        category="phone",
        brand="Apple",
        canonical_model=model,
        model_modifiers=["Pro"] if "Pro" in model else [],
        required_specs={"storage_gb": storage},
        budget=80_000,
        city="Ярославль",
        condition=ProductCondition.NEW,
        supported_category=True,
        hard_tokens=["Apple", model, f"{storage} ГБ", "new"],
    )


def make_query(source: str, *, text: str = "Apple iPhone 16 Pro 256 ГБ новый", priority: int = 0) -> SourceQuery:
    hard = ["Apple", "iPhone 16 Pro", "256 ГБ", "new"]
    return SourceQuery(
        query=text,
        source=source,
        tier=QueryTier.SOURCE_EXACT,
        priority=priority,
        hard_tokens_required=hard,
        hard_tokens_preserved=list(hard),
    )


class _HttpError(RuntimeError):
    def __init__(self, code: int, text: str = "http error") -> None:
        super().__init__(text)
        self.status_code = code


class AdapterContractTests(unittest.IsolatedAsyncioTestCase):
    def test_hard_constraint_validation(self) -> None:
        valid, reason = validate_source_query(make_query("ozon"))
        self.assertTrue(valid)
        self.assertEqual(reason, "")

        missing_pro = make_query("ozon", text="Apple iPhone 16 256 ГБ новый")
        valid, reason = validate_source_query(missing_pro)
        self.assertFalse(valid)
        self.assertIn("iPhone 16 Pro", reason)

        missing_storage = make_query("ozon", text="Apple iPhone 16 Pro новый")
        valid, reason = validate_source_query(missing_storage)
        self.assertFalse(valid)
        self.assertIn("256 ГБ", reason)

        annotated_wrong = make_query("ozon")
        annotated_wrong.hard_tokens_preserved = ["Apple", "iPhone 16 Pro", "new"]
        valid, reason = validate_source_query(annotated_wrong)
        self.assertFalse(valid)
        self.assertIn("256 ГБ", reason)

        rejected = make_query("ozon")
        rejected.status = SourceStatus.INVALID_QUERY_PLAN
        rejected.rejection_reason = "planner rejected"
        self.assertEqual(validate_source_query(rejected), (False, "planner rejected"))

    def test_legacy_row_becomes_raw_offer_only(self) -> None:
        row = {
            "title": "  Apple   iPhone 16 Pro  ",
            "href": "https://ozon.ru/product/123",
            "id": 123,
            "seller": "Example Store",
            "price": "79 990 ₽",
            "old_price": 84_990,
            "currency": "rub",
            "availability": "В наличии",
            "condition": "новый",
            "city": "Ярославль",
            "delivery": "завтра",
            "image": "https://img/123.jpg",
            "rating": 4.8,
        }
        offer = raw_offer_from_legacy(row, source="ozon", platform="Ozon")
        self.assertEqual(offer.source, "ozon")
        self.assertEqual(offer.platform, "Ozon")
        self.assertEqual(offer.title, "Apple iPhone 16 Pro")
        self.assertEqual(offer.url, row["href"])
        self.assertEqual(offer.product_id, "123")
        self.assertEqual(offer.seller_name, "Example Store")
        self.assertEqual(offer.price, 79_990)
        self.assertEqual(offer.old_price, 84_990)
        self.assertEqual(offer.currency, "RUB")
        self.assertEqual(offer.availability_text, "В наличии")
        self.assertIs(offer.condition, ProductCondition.NEW)
        self.assertEqual(offer.city, "Ярославль")
        self.assertEqual(offer.delivery, "завтра")
        self.assertEqual(offer.image_url, "https://img/123.jpg")
        self.assertEqual(offer.raw_metadata["rating"], 4.8)
        self.assertIsInstance(offer.retrieved_at, datetime)

    def test_exception_classification_is_fail_closed(self) -> None:
        self.assertIs(classify_source_exception(_HttpError(401)), SourceStatus.UNAUTHORIZED)
        self.assertIs(classify_source_exception(_HttpError(403)), SourceStatus.BLOCKED)
        self.assertIs(classify_source_exception(_HttpError(429)), SourceStatus.RATE_LIMITED)
        self.assertIs(classify_source_exception(RuntimeError("captcha page")), SourceStatus.BLOCKED)
        self.assertIs(classify_source_exception(TimeoutError("late")), SourceStatus.TIMEOUT)
        self.assertIs(classify_source_exception(ValueError("invalid json")), SourceStatus.INVALID_RESPONSE)
        self.assertIs(classify_source_exception(RuntimeError("boom")), SourceStatus.ERROR)

    async def test_invalid_query_never_calls_legacy(self) -> None:
        calls: list[str] = []

        def legacy(query, _request, _context):
            calls.append(query)
            return []

        adapter = OzonAdapter(legacy)
        result = await adapter.search(
            make_request(),
            make_query("ozon", text="Apple iPhone 16 256 ГБ новый"),
            SourceContext(),
        )
        self.assertEqual(calls, [])
        self.assertIs(result.status, SourceStatus.INVALID_QUERY_PLAN)
        self.assertEqual(len(result.attempts), 1)
        self.assertIs(result.attempts[0].status, SourceStatus.INVALID_QUERY_PLAN)

    async def test_site_bridge_keeps_exact_query_and_limits_rows(self) -> None:
        seen: list[tuple[str, int]] = []

        def legacy(query, _request, context):
            seen.append((query, context.limit))
            return [
                {"title": f"iPhone 16 Pro 256 {index}", "url": f"https://ozon.ru/product/{index}", "price": 79_000 + index}
                for index in range(5)
            ]

        result = await OzonAdapter(legacy).search(make_request(), make_query("ozon"), SourceContext(limit=2))
        self.assertIs(result.status, SourceStatus.SUCCESS)
        self.assertEqual(len(result.raw_offers), 2)
        self.assertEqual(result.attempts[0].raw_offer_count, 2)
        self.assertIn("Apple iPhone 16 Pro 256 ГБ новый", seen[0][0])
        self.assertTrue(seen[0][0].endswith("site:ozon.ru/product"))
        self.assertEqual(seen[0][1], 2)

    async def test_transport_status_and_useful_partial_rows(self) -> None:
        def blocked(_query, _request, _context):
            return {"status": "blocked", "error": "403", "candidates": []}

        result = await AvitoAdapter(blocked).search(make_request(), make_query("avito"), SourceContext())
        self.assertIs(result.status, SourceStatus.BLOCKED)
        self.assertEqual(result.raw_offers, ())
        self.assertEqual(result.error, "403")

        def blocked_with_snippet(_query, _request, _context):
            return {
                "status": "blocked",
                "error": "page blocked after snippet",
                "candidates": [{"title": "Apple iPhone 16 Pro 256", "url": "https://avito.ru/item_123", "price": 70_000}],
            }

        partial = await AvitoAdapter(blocked_with_snippet).search(make_request(), make_query("avito"), SourceContext())
        self.assertIs(partial.status, SourceStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(partial.raw_offers), 1)
        self.assertIn("blocked", partial.error)

    async def test_rate_limit_has_no_retry_loop(self) -> None:
        calls = 0

        def limited(_query, _request, _context):
            nonlocal calls
            calls += 1
            raise _HttpError(429, "rate limit")

        result = await GenericExactSearchAdapter(limited).search(
            make_request(), make_query("generic_exact"), SourceContext()
        )
        self.assertEqual(calls, 1)
        self.assertIs(result.status, SourceStatus.RATE_LIMITED)
        self.assertEqual(len(result.attempts), 1)

    def test_adapter_capabilities_are_transport_facts(self) -> None:
        self.assertEqual(YandexMarketAdapter().capabilities.kind, "discovery")
        self.assertEqual(OzonAdapter().platform, "Ozon")
        self.assertTrue(AvitoAdapter().capabilities.supports_condition)
        self.assertEqual(DnsAdapter().capabilities.kind, "reliable_anchor")
        self.assertTrue(WildberriesAdapterV2().capabilities.optional)
        self.assertTrue(CitilinkAdapter().capabilities.optional)
        self.assertTrue(MVideoAdapter().capabilities.optional)
        self.assertEqual(GenericExactSearchAdapter().capabilities.kind, "fallback")


class RegistryAndPolicyTests(unittest.TestCase):
    def test_default_registry_and_aliases(self) -> None:
        registry = build_default_registry()
        self.assertEqual(len(registry), 8)
        self.assertIs(registry.get("ozon_search"), registry.get("ozon"))
        self.assertIs(registry.get("dns_direct"), registry.get("dns"))
        self.assertIs(registry.get("wb"), registry.get("wildberries"))
        self.assertEqual(canonical_source_name("market.yandex.ru"), "yandex_market")
        self.assertIn("generic_exact", registry.names())

        no_optional = build_default_registry(include_optional=False)
        self.assertEqual(no_optional.names(), ("yandex_market", "ozon", "avito", "dns", "generic_exact"))
        self.assertNotIn("wildberries", no_optional)

    def test_registry_rejects_duplicate_and_supports_replace(self) -> None:
        registry = SourceRegistry([OzonAdapter(lambda *_: [])])
        with self.assertRaises(ValueError):
            registry.register(OzonAdapter(lambda *_: []))
        replacement = OzonAdapter(lambda *_: [])
        registry.register(replacement, replace=True)
        self.assertIs(registry.require("ozon_search"), replacement)
        removed = registry.unregister("ozon.ru")
        self.assertIs(removed, replacement)
        self.assertEqual(len(registry), 0)
        with self.assertRaises(KeyError):
            registry.require("ozon")

    def test_policy_caps_adapters_and_queries(self) -> None:
        registry = build_default_registry()
        queries = [
            make_query("yandex_market", priority=100),
            make_query("ozon", priority=90),
            make_query("avito", priority=80),
            make_query("dns", priority=70),
        ]
        selection = SourcePolicy().select(registry, queries)
        self.assertEqual(selection.sources, ("yandex_market", "ozon", "avito"))
        self.assertEqual(selection.query_count, 3)
        self.assertEqual(len(selection.rejected_queries), 1)
        self.assertIn("adapter budget", selection.rejected_queries[0][1])

        three_ozon = [make_query("ozon", priority=100 - index) for index in range(3)]
        selection = SourcePolicy().select(registry, three_ozon)
        self.assertEqual(selection.sources, ("ozon",))
        self.assertEqual(len(selection.queries_by_source["ozon"]), 2)
        self.assertEqual(len(selection.rejected_queries), 1)
        self.assertIn("per-source", selection.rejected_queries[0][1])

    def test_policy_rejects_invalid_and_unknown(self) -> None:
        registry = build_default_registry()
        invalid = make_query("ozon", text="iPhone 16")
        unknown = make_query("imaginary")
        selection = SourcePolicy().select(registry, [invalid, unknown])
        self.assertEqual(selection.sources, ())
        self.assertEqual(selection.query_count, 0)
        self.assertEqual(len(selection.rejected_queries), 2)
        reasons = " ".join(reason for _query, reason in selection.rejected_queries)
        self.assertIn("lost hard tokens", reasons)
        self.assertIn("not registered", reasons)

    def test_policy_constructor_bounds(self) -> None:
        with self.assertRaises(ValueError):
            SourcePolicy(max_adapters=4)
        with self.assertRaises(ValueError):
            SourcePolicy(max_queries_per_source=3)
        self.assertEqual(SourcePolicy(max_adapters=1).max_adapters, 1)
        self.assertEqual(SourcePolicy(max_queries_per_source=1).max_queries_per_source, 1)


if __name__ == "__main__":
    unittest.main()
