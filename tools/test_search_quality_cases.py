from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.adapters.base import SourceAdapter, SourceCapabilities, SourceContext, SourceResult
from app.search_v2.models import (
    ExactMatchResult,
    ProductCondition,
    RawOffer,
    SearchRequestV2,
    SourceAttempt,
    SourceQuery,
    SourceStatus,
)
from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.query_planner import QueryPlannerV2
from app.search_v2.request_normalizer import normalize_legacy_request
from app.search_v2.service import SearchServiceV2
from app.search_v2.source_registry import SourceRegistry


CASES_PATH = ROOT / "data" / "search_quality_cases.json"
EXPECTED_CATEGORIES = {"phone", "laptop", "tv", "headphones", "monitor", "chair"}


def load_cases() -> list[dict]:
    with CASES_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return list(payload["cases"])


class FixtureAdapter(SourceAdapter):
    version = "golden-1"
    capabilities = SourceCapabilities(
        kind="structured",
        supports_category_filter=True,
        returns_price=True,
        returns_availability=True,
    )

    def __init__(self, name: str, platform: str, offers: list[RawOffer]) -> None:
        self.name = name
        self.platform = platform
        self.offers = tuple(offers)

    async def search(
        self,
        request: SearchRequestV2,
        source_query: SourceQuery,
        context: SourceContext,
    ) -> SourceResult:
        rows = self.offers[: context.limit]
        return SourceResult(
            status=SourceStatus.SUCCESS if rows else SourceStatus.EMPTY,
            raw_offers=rows,
            attempts=(SourceAttempt(
                source=self.name,
                query=source_query.query,
                tier=source_query.tier,
                status=SourceStatus.SUCCESS if rows else SourceStatus.EMPTY,
                duration_ms=1,
                raw_offer_count=len(rows),
            ),),
            duration=0.001,
        )


def legacy_request(case: dict, request_id: int) -> dict:
    requested = case["request"]
    payload = {
        "category": requested["category"],
        "brand": requested["brand"],
        "model": requested["model"],
        "required_criteria": requested["required_specs"],
        "budget": requested["budget"],
        "city": requested["city"],
        "condition": requested["condition"],
    }
    product = " ".join((requested["brand"], requested["model"]))
    return {
        "id": request_id,
        "product": product,
        "product_name": product,
        "original_query": product,
        "category": requested["category"],
        "budget": str(requested["budget"]),
        "city": requested["city"],
        "condition": requested["condition"],
        "requirements_json": json.dumps(payload, ensure_ascii=False),
    }


def offer(
    case: dict,
    *,
    source: str,
    platform: str,
    suffix: str,
    price: int,
    wrong: bool = False,
) -> RawOffer:
    requested = case["request"]
    facts = dict(case["wrong_facts"] if wrong else requested["required_specs"])
    facts["fact_evidence"] = {
        key: {"confidence": "high", "evidence": "page"}
        for key in facts
    }
    return RawOffer(
        source=source,
        platform=platform,
        title=case["wrong_title"] if wrong else case["exact_title"],
        url=f"https://shop.example/{source}/product/{case['id']}-{suffix}",
        product_id=f"{case['id']}-{suffix}",
        seller_name=f"{platform} verified seller",
        price=price,
        availability_text="в наличии",
        condition=ProductCondition.NEW,
        city=requested["city"],
        raw_metadata={
            "price_confidence": 0.99,
            "product_page_verified": True,
            "price_verified": True,
            "availability_verified": True,
            "seller_verified": True,
            "seller_rating": 4.9,
            "seller_reviews_count": 1000,
            "verification_access": "FULL",
            "region_scope_confirmed": True,
            "structured_facts": facts,
        },
    )


def build_service(case: dict) -> SearchServiceV2:
    prices = case["prices"]
    adapters = (
        FixtureAdapter("yandex_market", "Яндекс Маркет", [
            offer(case, source="yandex_market", platform="Яндекс Маркет", suffix="best", price=prices[0]),
            offer(
                case,
                source="yandex_market",
                platform="Яндекс Маркет",
                suffix="wrong",
                price=max(1, int(prices[0] * 0.72)),
                wrong=True,
            ),
        ]),
        FixtureAdapter("dns", "DNS", [
            offer(case, source="dns", platform="DNS", suffix="dns", price=prices[1]),
        ]),
        FixtureAdapter("citilink", "Ситилинк", [
            offer(case, source="citilink", platform="Ситилинк", suffix="citilink", price=prices[2]),
        ]),
        FixtureAdapter("mvideo", "М.Видео", [
            offer(case, source="mvideo", platform="М.Видео", suffix="mvideo", price=prices[3]),
        ]),
    )
    orchestrator = SearchSourceOrchestrator(
        SourceRegistry(adapters),
        max_concurrency=3,
        per_source_timeout=1,
        case_timeout=3,
        result_limit=10,
    )
    return SearchServiceV2(
        planner=QueryPlannerV2(max_queries_per_source=1),
        orchestrator=orchestrator,
        discovery_sources=("yandex_market",),
        anchor_sources=("dns", "citilink", "mvideo"),
        web_discovery_sources=(),
        generic_sources=(),
        overall_timeout=5,
    )


class SearchQualityCaseSchemaTests(unittest.TestCase):
    def test_matrix_has_five_unique_cases_for_each_required_category(self) -> None:
        cases = load_cases()
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({case["id"] for case in cases}), 30)
        self.assertEqual(Counter(case["request"]["category"] for case in cases), Counter({
            category: 5 for category in EXPECTED_CATEGORIES
        }))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(len(case["prices"]), 4)
                self.assertEqual(case["prices"], sorted(case["prices"]))
                self.assertTrue(case["request"]["required_specs"])
                self.assertTrue(case["wrong_facts"])


class SearchQualityGoldenPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_cases_keep_only_exact_product_and_correct_top_one(self) -> None:
        for index, case in enumerate(load_cases(), start=1):
            with self.subTest(case=case["id"]):
                normalized = normalize_legacy_request(legacy_request(case, index))
                requested = case["request"]
                self.assertTrue(normalized.supported_category)
                self.assertEqual(normalized.category, requested["category"])
                self.assertEqual(normalized.required_specs, requested["required_specs"])
                self.assertEqual(normalized.budget, requested["budget"])
                self.assertEqual(normalized.city, requested["city"])

                result = await build_service(case).search(legacy_request(case, index))
                kept_ids = {item.product_id for item in result.normalized_offers}
                rejected_by_id = {item.product_id: item for item in result.rejected_offers}
                wrong_id = f"{case['id']}-wrong"

                self.assertEqual(len(result.normalized_offers), 4)
                self.assertNotIn(wrong_id, kept_ids)
                self.assertIn(wrong_id, rejected_by_id)
                self.assertIn(rejected_by_id[wrong_id].exact_match, {
                    ExactMatchResult.MODEL_MISMATCH,
                    ExactMatchResult.REQUIRED_SPEC_MISMATCH,
                    ExactMatchResult.ACCESSORY,
                })
                self.assertTrue(all(
                    item.exact_match is ExactMatchResult.EXACT
                    for item in result.normalized_offers
                ))
                self.assertEqual(sum(len(group.offers) for group in result.product_groups), 4)
                self.assertTrue(result.recommendations)
                self.assertLessEqual(len(result.recommendations), 3)
                top_offer = next(
                    item for item in result.normalized_offers
                    if item.offer_id == result.recommendations[0].offer_id
                )
                self.assertEqual(top_offer.product_id, f"{case['id']}-best")
                self.assertTrue(result.metrics.top1_exact)
                self.assertGreaterEqual(result.metrics.source_diversity, 4)


if __name__ == "__main__":
    unittest.main()
