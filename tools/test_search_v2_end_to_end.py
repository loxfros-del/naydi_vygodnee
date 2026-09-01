from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.adapters.base import SourceAdapter, SourceCapabilities, SourceContext, SourceResult
from app.net_client import FetchResult
from app.search_v2.external_page_verifier import ExternalProductPageVerifier
from app.search_v2.models import (
    ProductCondition, RawOffer, RecommendationRole, SearchRequestV2, SearchResultStatus,
    SourceAttempt, SourceQuery, SourceStatus, VerificationAccess,
)
from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.query_planner import QueryPlannerV2
from app.search_v2.service import SearchServiceV2
from app.search_v2.source_registry import SourceRegistry
from tools.search_v2_live_smoke import cases as live_cases


class FakeAdapter(SourceAdapter):
    version = "fake-1"
    capabilities = SourceCapabilities(kind="test")

    def __init__(self, name: str, platform: str, offers=(), *, status=SourceStatus.SUCCESS, error="") -> None:
        self.name = name
        self.platform = platform
        self.offers = tuple(offers)
        self.status = status
        self.error = error
        self.calls: list[SourceQuery] = []

    async def search(self, request: SearchRequestV2, source_query: SourceQuery, context: SourceContext) -> SourceResult:
        self.calls.append(source_query)
        rows = self.offers[: context.limit]
        return SourceResult(
            status=self.status,
            raw_offers=rows,
            attempts=(SourceAttempt(
                source=self.name,
                query=source_query.query,
                tier=source_query.tier,
                status=self.status,
                duration_ms=1,
                error=self.error,
                raw_offer_count=len(rows),
            ),),
            duration=0.001,
            error=self.error,
        )


def raw(
    source: str,
    platform: str,
    offer_id: str,
    title: str,
    price: int,
    url: str,
    *,
    seller: str = "",
    access: str = "FULL",
    metadata: dict | None = None,
) -> RawOffer:
    facts = {
        "price_confidence": 0.95,
        "product_page_verified": access != "BLOCKED",
        "availability_verified": True,
        "verification_access": access,
        **(metadata or {}),
    }
    if access == "BLOCKED":
        facts.update({"source_error": "403 page blocked", "source_errors": ["403 page blocked"]})
    return RawOffer(
        source=source,
        platform=platform,
        title=title,
        url=url,
        product_id=offer_id,
        seller_name=seller,
        price=price,
        availability_text="в наличии",
        condition=ProductCondition.NEW,
        city="Ярославль",
        raw_metadata=facts,
    )


def legacy_request() -> dict:
    payload = {
        "category": "phone",
        "brand": "Apple",
        "model": "iPhone 16 Pro",
        "storage_gb": 256,
        "condition": "new",
        "budget": 80_000,
        "city": "Ярославль",
    }
    return {
        "id": 101,
        "product": "iPhone 16 Pro",
        "product_name": "iPhone 16 Pro",
        "original_query": "iPhone 16 Pro 256 ГБ новый до 80 000 ₽ в Ярославле",
        "category": "phone",
        "budget": "80000",
        "city": "Ярославль",
        "condition": "new",
        "requirements_json": json.dumps(payload, ensure_ascii=False),
    }


class SearchV2EndToEndTests(unittest.IsolatedAsyncioTestCase):
    def adapters(self):
        yandex = FakeAdapter("yandex_market", "Яндекс Маркет", [
            raw("yandex_market", "Яндекс Маркет", "ym-good", "Apple iPhone 16 Pro 256 ГБ новый", 79_990, "https://market.yandex.ru/product--iphone/123456", seller="Verified Market", metadata={"seller_verified": True}),
            raw("yandex_market", "Яндекс Маркет", "ym-plain", "Apple iPhone 16 256 ГБ новый", 62_000, "https://market.yandex.ru/product--iphone/123457"),
            raw("yandex_market", "Яндекс Маркет", "ym-max", "Apple iPhone 16 Pro Max 256 ГБ новый", 85_000, "https://market.yandex.ru/product--iphone/123458"),
        ])
        ozon = FakeAdapter("ozon", "Ozon", [
            raw("ozon", "Ozon", "oz-good", "Apple iPhone 16 Pro 256 ГБ новый", 69_990, "https://ozon.ru/product/iphone-16-pro-123456", seller="Ozon Seller", access="BLOCKED"),
            raw("ozon", "Ozon", "oz-128", "Apple iPhone 16 Pro 128 ГБ новый", 59_990, "https://ozon.ru/product/iphone-16-pro-123457"),
            raw("ozon", "Ozon", "oz-search", "Apple iPhone 16 Pro 256 ГБ новый", 60_000, "https://ozon.ru/search/?text=iphone+16+pro+256"),
        ])
        avito = FakeAdapter("avito", "Avito", [
            raw("avito", "Avito", "av-good", "Apple iPhone 16 Pro 256 ГБ новый", 65_000, "https://www.avito.ru/yaroslavl/telefony/iphone_16_pro_256_123456789", seller="Phone Shop", metadata={"seller_type": "professional", "seller_rating": 4.6, "seller_reviews_count": 30}),
        ])
        dns = FakeAdapter("dns", "DNS", [
            raw("dns", "DNS", "dns-good", "Apple iPhone 16 Pro 256 ГБ новый", 79_000, "https://www.dns-shop.ru/product/123456/", seller="DNS"),
        ])
        generic = FakeAdapter("generic_exact", "Generic", [])
        return yandex, ozon, avito, dns, generic

    def service(self, adapters) -> SearchServiceV2:
        registry = SourceRegistry(adapters)
        orchestrator = SearchSourceOrchestrator(
            registry,
            max_concurrency=3,
            per_source_timeout=1,
            case_timeout=3,
            result_limit=10,
        )
        return SearchServiceV2(orchestrator=orchestrator, overall_timeout=5)

    async def test_vertical_iphone_case_from_request_to_recommendations(self) -> None:
        adapters = self.adapters()
        snapshots: list[tuple[str, int]] = []
        result = await self.service(adapters).search(
            legacy_request(),
            on_snapshot=lambda stage, snapshot: snapshots.append((stage, snapshot.raw_offer_count)),
        )
        request = result.normalized_request
        self.assertEqual(request.category, "phone")
        self.assertEqual(request.brand, "Apple")
        self.assertEqual(request.canonical_model, "iPhone 16")
        self.assertEqual(request.model_modifiers, ["Pro"])
        self.assertEqual(request.required_specs["storage_gb"], 256)
        self.assertEqual(request.condition, ProductCondition.NEW)
        self.assertEqual(request.budget, 80_000)
        self.assertEqual(request.city, "Ярославль")
        self.assertTrue(request.supported_category)

        sent_queries = [query for adapter in adapters for query in adapter.calls]
        self.assertTrue(sent_queries)
        self.assertTrue(all("Pro" in query.query for query in sent_queries))
        self.assertTrue(all("256" in query.query for query in sent_queries))
        self.assertTrue(all(set(query.hard_tokens_required) == set(query.hard_tokens_preserved) for query in sent_queries))
        self.assertEqual(len(adapters[-1].calls), 0, "generic fallback must not run when exact offers exist")
        self.assertGreaterEqual(len(snapshots), 4)

        kept_ids = {offer.product_id for offer in result.normalized_offers}
        rejected_ids = {offer.product_id for offer in result.rejected_offers}
        self.assertEqual(kept_ids, {"ym-good", "oz-good", "av-good", "dns-good"})
        self.assertTrue({"ym-plain", "ym-max", "oz-128", "oz-search"}.issubset(rejected_ids))
        self.assertNotIn("https://ozon.ru/search/?text=iphone+16+pro+256", {offer.url for offer in result.normalized_offers})
        self.assertTrue(all(offer.exact_match.value == "EXACT" for offer in result.normalized_offers))

        self.assertEqual(len(result.product_groups), 1)
        group = result.product_groups[0]
        self.assertEqual(len(group.offers), 4)
        self.assertEqual(group.market_stats.offer_count, 4)
        self.assertEqual(group.market_stats.verified_offer_count, 4)
        self.assertIsNotNone(group.market_stats.median)
        self.assertEqual(result.market_stats[0].median, group.market_stats.median)

        blocked = next(offer for offer in result.normalized_offers if offer.product_id == "oz-good")
        self.assertEqual(blocked.verification_access, VerificationAccess.BLOCKED)
        self.assertIn(blocked.offer_id, {offer.offer_id for offer in result.manual_candidates})
        self.assertEqual(blocked.platform_trust.value, "HIGH_MARKETPLACE")
        self.assertNotEqual(blocked.seller.trust.value, "LOW")

        roles = [item.role for item in result.recommendations]
        offer_ids = [item.offer_id for item in result.recommendations]
        self.assertLessEqual(len(roles), 3)
        self.assertEqual(len(offer_ids), len(set(offer_ids)))
        self.assertIn(RecommendationRole.BEST_OVERALL, roles)
        self.assertIn(RecommendationRole.CHEAP_WITH_RISK, roles)
        self.assertIn(RecommendationRole.RELIABLE, roles)
        self.assertIn(result.status, {SearchResultStatus.MANUAL_REVIEW_REQUIRED, SearchResultStatus.PARTIAL_SUCCESS})
        self.assertGreaterEqual(result.metrics.source_diversity, 4)
        self.assertTrue(result.metrics.top1_exact)
        self.assertEqual(json.loads(result.to_json())["normalized_request"]["model_modifiers"], ["Pro"])

    async def test_partial_source_failure_keeps_successful_offer(self) -> None:
        yandex, _ozon, _avito, dns, generic = self.adapters()
        failed = FakeAdapter("ozon", "Ozon", status=SourceStatus.ERROR, error="429 rate limited")
        empty = FakeAdapter("avito", "Avito", status=SourceStatus.EMPTY)
        result = await self.service((yandex, failed, empty, dns, generic)).search(legacy_request())
        self.assertTrue(result.normalized_offers)
        self.assertIn("ym-good", {offer.product_id for offer in result.normalized_offers})
        self.assertEqual(result.status, SearchResultStatus.PARTIAL_SUCCESS)
        self.assertTrue(any(attempt.status is SourceStatus.ERROR for attempt in result.source_attempts))
        self.assertTrue(any("429" in error for error in result.errors))

    async def test_core_marketplaces_run_before_optional_web_discovery(self) -> None:
        call_order: list[str] = []

        class OrderedAdapter(FakeAdapter):
            async def search(self, request, source_query, context):
                call_order.append(self.name)
                return await super().search(request, source_query, context)

        core = OrderedAdapter("yandex_market", "Яндекс Маркет", [
            raw("yandex_market", "Яндекс Маркет", "core", "Apple iPhone 16 Pro 256 ГБ новый", 79_990, "https://market.yandex.ru/product--iphone/123456"),
        ])
        web = OrderedAdapter("yandex_web", "Яндекс Поиск", status=SourceStatus.EMPTY)
        service = SearchServiceV2(
            orchestrator=SearchSourceOrchestrator(
                SourceRegistry((core, web)), max_concurrency=2, per_source_timeout=1, case_timeout=2,
            ),
            discovery_sources=("yandex_market",),
            web_discovery_sources=("yandex_web",),
            anchor_sources=(),
            generic_sources=(),
            overall_timeout=3,
        )

        await service.search(legacy_request())

        self.assertLess(call_order.index("yandex_market"), call_order.index("yandex_web"))

    async def test_page_verification_is_bounded_and_skips_hard_mismatches(self) -> None:
        rows = [
            raw(
                "yandex_web",
                "Яндекс Поиск",
                f"good-{index}",
                "Apple iPhone 16 Pro 256 ГБ новый",
                70_000 + index * 1_000,
                f"https://shop.example/product/iphone-16-pro-{index}",
                seller=f"Seller {index}",
            )
            for index in range(5)
        ]
        rows.insert(0, raw(
            "yandex_web",
            "Яндекс Поиск",
            "wrong-model",
            "Apple iPhone 15 Pro 256 ГБ новый",
            50_000,
            "https://shop.example/product/iphone-15-pro",
            seller="Wrong Seller",
        ))
        adapter = FakeAdapter("yandex_web", "Яндекс Поиск", rows)
        verified_ids: list[str] = []

        async def verifier(offer):
            verified_ids.append(offer.product_id)
            return None

        service = SearchServiceV2(
            orchestrator=SearchSourceOrchestrator(
                SourceRegistry((adapter,)),
                max_concurrency=2,
                per_source_timeout=1,
                case_timeout=2,
                result_limit=10,
            ),
            discovery_sources=("yandex_web",),
            anchor_sources=(),
            web_discovery_sources=(),
            generic_sources=(),
            page_verifier=verifier,
            page_verification_limit=2,
            overall_timeout=3,
        )

        result = await service.search(legacy_request())

        self.assertEqual(len(verified_ids), 2)
        self.assertNotIn("wrong-model", verified_ids)
        self.assertIn("wrong-model", {offer.product_id for offer in result.rejected_offers})

    async def test_unknown_yandex_product_url_shape_reaches_bounded_verifier(self) -> None:
        adapter = FakeAdapter("yandex_web", "Яндекс Поиск", [
            raw(
                "yandex_web",
                "Яндекс Поиск",
                "unknown-route",
                "Apple iPhone 16 Pro 256 ГБ новый",
                79_990,
                "https://shop.example/SM-A556E08256DBL2E1S/",
                seller="Verified shop",
                metadata={"page_verification_required": True, "seller_verified": True},
            ),
        ])
        calls: list[str] = []

        async def verifier(offer):
            calls.append(offer.product_id)
            metadata = dict(offer.raw_metadata)
            metadata.pop("not_product_page", None)
            metadata.update({
                "product_page_verified": True,
                "external_page_verified": True,
                "price_verified": True,
                "availability_verified": True,
                "external_page_verification": {"verified": True},
            })
            return replace(offer, raw_metadata=metadata)

        service = SearchServiceV2(
            orchestrator=SearchSourceOrchestrator(
                SourceRegistry((adapter,)), max_concurrency=1, per_source_timeout=1, case_timeout=2,
            ),
            discovery_sources=("yandex_web",),
            anchor_sources=(),
            web_discovery_sources=(),
            generic_sources=(),
            page_verifier=verifier,
            page_verification_limit=1,
            overall_timeout=3,
        )

        result = await service.search(legacy_request())

        self.assertEqual(calls, ["unknown-route"])
        self.assertIn("unknown-route", {offer.product_id for offer in result.normalized_offers})

    async def test_single_expensive_exact_source_still_runs_broad_discovery(self) -> None:
        yandex = FakeAdapter("yandex_market", "Яндекс Маркет", [
            raw("yandex_market", "Яндекс Маркет", "market-expensive", "Apple iPhone 16 Pro 256 ГБ новый", 150_000, "https://market.yandex.ru/product--iphone/123456", seller="Market"),
        ])
        ozon = FakeAdapter("ozon", "Ozon", status=SourceStatus.EMPTY)
        avito = FakeAdapter("avito", "Avito", status=SourceStatus.EMPTY)
        dns = FakeAdapter("dns", "DNS", status=SourceStatus.EMPTY)
        generic = FakeAdapter("generic_exact", "Веб-поиск", [
            raw("generic_exact", "Веб-поиск", "external-low", "Apple iPhone 16 Pro 256 ГБ новый", 75_399, "https://shop.example/product/iphone-16-pro-256", seller="shop.example"),
        ])

        result = await self.service((yandex, ozon, avito, dns, generic)).search(legacy_request())

        self.assertTrue(generic.calls, "one expensive source is not enough to establish the market")
        self.assertIn("external-low", {offer.product_id for offer in result.normalized_offers})
        self.assertEqual(
            min(offer.price for offer in result.normalized_offers if offer.price),
            75_399,
        )

    async def test_two_prices_from_two_sources_still_run_broad_discovery(self) -> None:
        market = FakeAdapter("yandex_market", "Яндекс Маркет", [
            raw("yandex_market", "Яндекс Маркет", "market-one", "Apple iPhone 16 Pro 256 ГБ новый", 90_000, "https://market.yandex.ru/product--iphone/123456"),
        ])
        ozon = FakeAdapter("ozon", "Ozon", [
            raw("ozon", "Ozon", "ozon-one", "Apple iPhone 16 Pro 256 ГБ новый", 89_000, "https://ozon.ru/product/iphone-16-pro-123456"),
        ])
        avito = FakeAdapter("avito", "Avito", status=SourceStatus.EMPTY)
        dns = FakeAdapter("dns", "DNS", status=SourceStatus.EMPTY)
        generic = FakeAdapter("generic_exact", "Веб-поиск", status=SourceStatus.EMPTY)

        await self.service((market, ozon, avito, dns, generic)).search(legacy_request())

        self.assertTrue(generic.calls, "two prices are not enough for a market picture")

    async def test_yandex_web_discovery_runs_and_uses_verified_page_price(self) -> None:
        web = FakeAdapter("yandex_web", "Яндекс Поиск", [
            raw(
                "yandex_web", "Яндекс Поиск", "external-low",
                "Сниппет iPhone 16 Pro 256 ГБ", None,
                "https://store.example/product/iphone-16-pro-256",
                metadata={
                    "page_verification_required": True,
                    "product_page_verified": False,
                    "price_confidence": 0.0,
                },
            ),
        ])
        market = FakeAdapter("yandex_market", "Яндекс Маркет", [
            raw("yandex_market", "Яндекс Маркет", "market-expensive", "Apple iPhone 16 Pro 256 ГБ новый", 150_217, "https://market.yandex.ru/product--iphone/987654"),
        ])
        ozon = FakeAdapter("ozon", "Ozon", [
            raw("ozon", "Ozon", "ozon-expensive", "Apple iPhone 16 Pro 256 ГБ новый", 145_000, "https://ozon.ru/product/iphone-16-pro-987654"),
        ])
        avito = FakeAdapter("avito", "Avito", status=SourceStatus.EMPTY)
        dns = FakeAdapter("dns", "DNS", status=SourceStatus.EMPTY)
        generic = FakeAdapter("generic_exact", "Веб-поиск", status=SourceStatus.EMPTY)

        async def verifier(offer):
            if offer.source != "yandex_web":
                return None
            metadata = dict(offer.raw_metadata)
            metadata.update({
                "product_page_verified": True,
                "external_page_verified": True,
                "price_verified": True,
                "availability_verified": True,
                "external_page_verification": {"verified": True, "price_source": "json_ld:offer.price"},
            })
            return replace(
                offer,
                title="Apple iPhone 16 Pro 256 ГБ новый",
                price=75_399,
                price_confidence=0.95,
                raw_metadata=metadata,
            )

        service = self.service((web, market, ozon, avito, dns, generic))
        service = SearchServiceV2(
            orchestrator=service.orchestrator,
            web_discovery_sources=("yandex_web",),
            page_verifier=verifier,
            overall_timeout=5,
        )
        result = await service.search(legacy_request())

        self.assertTrue(web.calls, "Yandex web discovery must not wait for a marketplace failure")
        external = next(offer for offer in result.normalized_offers if offer.product_id == "external-low")
        self.assertEqual(external.price, 75_399)
        self.assertTrue(external.raw_metadata["external_page_verified"])
        self.assertTrue(external.final_verification.price_verified)
        self.assertEqual(min(offer.price for offer in result.normalized_offers if offer.price), 75_399)

    async def test_wildberries_offer_is_removed_when_direct_page_verification_fails(self) -> None:
        request = SearchRequestV2(
            category="coffee_machine",
            brand="DeLonghi",
            canonical_model="DeLonghi Magnifica S ECAM21.117.SB",
            supported_category=True,
            hard_tokens=["DeLonghi", "Magnifica", "ECAM21.117.SB"],
        )
        wildberries = FakeAdapter("wildberries", "Wildberries", [
            raw(
                "wildberries", "Wildberries", "wb-ecam21",
                "Кофемашина DeLonghi Magnifica S ECAM21.117.SB серебристый черный",
                27_344,
                "https://www.wildberries.ru/catalog/321732159/detail.aspx",
            ),
        ])

        def blocked_fetch(url: str, **_kwargs):
            return FetchResult(
                ok=False,
                status_code=429,
                final_url=url,
                blocked=True,
                blocked_reason="rate_limited",
            )

        orchestrator = SearchSourceOrchestrator(
            SourceRegistry((wildberries,)),
            max_concurrency=1,
            per_source_timeout=1,
            case_timeout=2,
            result_limit=5,
        )
        result = await SearchServiceV2(
            orchestrator=orchestrator,
            web_discovery_sources=("wildberries",),
            discovery_sources=(),
            anchor_sources=(),
            generic_sources=(),
            page_verifier=ExternalProductPageVerifier(blocked_fetch),
            overall_timeout=3,
        ).search(request)

        self.assertTrue(wildberries.calls)
        self.assertEqual(result.normalized_offers, [])
        self.assertEqual(result.recommendations, [])
        self.assertEqual({offer.product_id for offer in result.rejected_offers}, {"wb-ecam21"})
        rejected = result.rejected_offers[0]
        self.assertTrue(rejected.raw_metadata["not_product_page"])
        self.assertFalse(rejected.raw_metadata["external_page_verified"])
        self.assertIsNone(rejected.price)

    async def test_wildberries_offer_appears_only_after_direct_page_verification(self) -> None:
        request = SearchRequestV2(
            category="coffee_machine",
            brand="DeLonghi",
            canonical_model="DeLonghi Magnifica S ECAM21.117.SB",
            supported_category=True,
            hard_tokens=["DeLonghi", "Magnifica", "ECAM21.117.SB"],
        )
        wildberries = FakeAdapter("wildberries", "Wildberries", [
            raw(
                "wildberries", "Wildberries", "wb-ecam21",
                "Кофемашина DeLonghi Magnifica S ECAM21.117.SB серебристый черный",
                30_580,
                "https://www.wildberries.ru/catalog/321732159/detail.aspx",
            ),
        ])

        def verified_fetch(url: str, **_kwargs):
            return FetchResult(
                ok=True,
                status_code=200,
                final_url=url,
                html="""
                    <script type="application/ld+json">
                    {"@type":"Product",
                     "name":"Кофемашина DeLonghi Magnifica S ECAM21.117.SB серебристый черный",
                     "offers":{"@type":"Offer","price":"27344",
                     "availability":"https://schema.org/InStock"}}
                    </script>
                """,
            )

        orchestrator = SearchSourceOrchestrator(
            SourceRegistry((wildberries,)),
            max_concurrency=1,
            per_source_timeout=1,
            case_timeout=2,
            result_limit=5,
        )
        result = await SearchServiceV2(
            orchestrator=orchestrator,
            web_discovery_sources=("wildberries",),
            discovery_sources=(),
            anchor_sources=(),
            generic_sources=(),
            page_verifier=ExternalProductPageVerifier(verified_fetch),
            overall_timeout=3,
        ).search(request)

        self.assertTrue(wildberries.calls)
        offer = next(item for item in result.normalized_offers if item.product_id == "wb-ecam21")
        self.assertEqual(offer.price, 27_344)
        self.assertTrue(offer.raw_metadata["external_page_verified"])
        self.assertTrue(offer.final_verification.price_verified)
        self.assertEqual({item.offer_id for item in result.recommendations}, {offer.offer_id})

    async def test_unsupported_category_makes_no_network_call(self) -> None:
        adapters = self.adapters()
        request = SearchRequestV2(original_query="услуга ремонта", category="manual", supported_category=False)
        result = await self.service(adapters).search(request)
        self.assertEqual(result.status, SearchResultStatus.UNSUPPORTED_CATEGORY)
        self.assertEqual(result.raw_offer_count, 0)
        self.assertTrue(all(not adapter.calls for adapter in adapters))

    async def test_exact_over_budget_is_kept_when_no_in_budget_offer_exists(self) -> None:
        dns = FakeAdapter("dns", "DNS", [
            raw("dns", "DNS", "over", "Apple iPhone 16 Pro 256 ГБ новый", 85_000, "https://dns-shop.ru/product/987654/", seller="DNS"),
        ])
        empty_yandex = FakeAdapter("yandex_market", "Яндекс Маркет", status=SourceStatus.EMPTY)
        empty_ozon = FakeAdapter("ozon", "Ozon", status=SourceStatus.EMPTY)
        empty_avito = FakeAdapter("avito", "Avito", status=SourceStatus.EMPTY)
        generic = FakeAdapter("generic_exact", "Generic", status=SourceStatus.EMPTY)
        result = await self.service((empty_yandex, empty_ozon, empty_avito, dns, generic)).search(legacy_request())
        self.assertEqual({offer.product_id for offer in result.normalized_offers}, {"over"})
        self.assertEqual(result.recommendations[0].offer_id, result.normalized_offers[0].offer_id)
        self.assertGreater(result.recommendations[0].offer.price, result.normalized_request.budget)
        self.assertTrue(any(query.tier.value == "OVER_BUDGET_EXACT" for query in result.query_plan.source_queries))

    async def test_live_smoke_case_set_is_bounded_and_hard_safe(self) -> None:
        rows = live_cases()
        self.assertEqual([item[0] for item in rows], ["phone", "laptop", "tv", "headphones", "monitor", "chair"])
        self.assertEqual(len(rows), 6)
        planner = QueryPlannerV2(max_queries_per_source=1)
        for case_id, request in rows:
            with self.subTest(case_id=case_id):
                self.assertTrue(request.supported_category)
                self.assertTrue(request.hard_tokens)
                plan = planner.plan(request, sources=("ozon",))
                self.assertEqual(len(plan.source_queries), 1)
                self.assertEqual(plan.rejected_queries, [])
                query = plan.source_queries[0]
                self.assertEqual(set(query.hard_tokens_preserved), set(request.hard_tokens))


if __name__ == "__main__":
    unittest.main()
