from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from app.search_v2.models import (
    AvailabilityInfo,
    AvailabilityStatus,
    ExactMatchResult,
    MarketStats,
    Offer,
    PriceClass,
    ProductCondition,
    ProductGroup,
    ProductIdentity,
    QueryPlan,
    Recommendation,
    RecommendationRole,
    RiskFlag,
    RiskSeverity,
    SearchMetrics,
    SearchRequestV2,
    SearchResultStatus,
    SearchResultV2,
    SellerInfo,
    SellerTrust,
    SourceAttempt,
    SourceStatus,
    VerificationAccess,
)
from app.search_v2.serialization import to_json, to_jsonable


class SearchV2ModelsTests(unittest.TestCase):
    def test_request_model_and_string_enums(self) -> None:
        request = SearchRequestV2(
            original_query="iPhone 16 Pro 256 ГБ",
            category="phone",
            brand="Apple",
            canonical_model="iPhone 16",
            model_modifiers=["Pro"],
            required_specs={"storage_gb": 256},
            budget=80_000,
            city="Ярославль",
            condition=ProductCondition.NEW,
            supported_category=True,
            hard_tokens=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
        )
        self.assertEqual(request.category, "phone")
        self.assertEqual(request.canonical_model, "iPhone 16")
        self.assertEqual(request.model_modifiers, ["Pro"])
        self.assertEqual(request.required_specs["storage_gb"], 256)
        self.assertEqual(request.budget, 80_000)
        self.assertEqual(request.city, "Ярославль")
        self.assertEqual(request.condition, "new")
        self.assertTrue(request.supported_category)
        self.assertIn("Pro", request.hard_tokens)
        self.assertEqual(str(SourceStatus.INVALID_QUERY_PLAN), "INVALID_QUERY_PLAN")
        self.assertEqual(RecommendationRole.RELIABLE.value, "RELIABLE")
        self.assertEqual(ExactMatchResult.REQUIRED_SPEC_MISMATCH.value, "REQUIRED_SPEC_MISMATCH")

    def test_nested_search_result_is_json_safe(self) -> None:
        retrieved = datetime(2026, 7, 13, 12, 30, tzinfo=timezone.utc)
        identity = ProductIdentity(
            category="phone", brand="Apple", canonical_model="iPhone 16",
            modifiers=["Pro"], storage=256, condition=ProductCondition.NEW,
            canonical_key="phone|apple|iphone16|pro|256|new", identity_confidence=0.99,
        )
        offer = Offer(
            offer_id="ozon:123", source="ozon", platform="Ozon",
            title="Apple iPhone 16 Pro 256 ГБ", url="https://www.ozon.ru/product/123",
            product_id="123", seller=SellerInfo(name="Store", trust=SellerTrust.UNKNOWN),
            price=79_990, old_price=84_990, currency="RUB",
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
            condition=ProductCondition.NEW, verification_access=VerificationAccess.BLOCKED,
            exact_match=ExactMatchResult.EXACT,
            risk_flags=[RiskFlag(code="BLOCKED_ACCESS", severity=RiskSeverity.INFO)],
            retrieved_at=retrieved, identity=identity,
        )
        stats = MarketStats(
            offer_count=3, verified_offer_count=2, minimum=79_990,
            median=82_000, trimmed_mean=82_100, maximum=84_500,
            market_range=(79_990, 84_500),
            deviation_percent={"ozon:123": -2.45},
            price_classes={"ozon:123": PriceClass.FAIR},
        )
        result = SearchResultV2(
            normalized_request=SearchRequestV2(category="phone"),
            query_plan=QueryPlan(),
            source_attempts=[SourceAttempt(source="ozon", status=SourceStatus.SUCCESS, raw_offer_count=1)],
            raw_offer_count=1,
            normalized_offers=[offer],
            product_groups=[ProductGroup(group_id="g1", identity=identity, offers=[offer], market_stats=stats)],
            market_stats=[stats],
            recommendations=[Recommendation(role=RecommendationRole.BEST_OVERALL, offer_id=offer.offer_id, offer=offer)],
            metrics=SearchMetrics(raw_offer_count=1, exact_offer_count=1, top1_exact=True),
            duration=1.25,
            status=SearchResultStatus.PARTIAL_SUCCESS,
        )
        payload = result.to_dict()
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["status"], "PARTIAL_SUCCESS")
        self.assertEqual(payload["normalized_offers"][0]["price"], 79_990)
        self.assertEqual(payload["normalized_offers"][0]["seller"]["trust"], "UNKNOWN")
        self.assertEqual(payload["normalized_offers"][0]["availability"]["status"], "IN_STOCK")
        self.assertEqual(payload["normalized_offers"][0]["exact_match"], "EXACT")
        self.assertEqual(payload["normalized_offers"][0]["retrieved_at"], "2026-07-13T12:30:00+00:00")
        self.assertEqual(payload["product_groups"][0]["market_stats"]["market_range"], [79_990, 84_500])
        self.assertEqual(payload["market_stats"][0]["price_classes"]["ozon:123"], "FAIR")
        self.assertEqual(payload["recommendations"][0]["role"], "BEST_OVERALL")
        self.assertTrue(payload["metrics"]["top1_exact"])
        encoded = result.to_json()
        decoded = json.loads(encoded)
        self.assertEqual(decoded["raw_offer_count"], 1)
        self.assertEqual(decoded["source_attempts"][0]["status"], "SUCCESS")
        self.assertEqual(decoded["normalized_offers"][0]["risk_flags"][0]["severity"], "INFO")
        self.assertNotIn("<ProductCondition", encoded)

    def test_generic_json_conversion_is_deterministic(self) -> None:
        value = {
            "decimal": Decimal("79990.50"),
            "path": Path("snapshots/v2.json"),
            "bytes": "цена".encode("utf-8"),
            "set": {"ozon", "avito"},
            "tuple": (1, 2),
            "status": SourceStatus.BLOCKED,
        }
        payload = to_jsonable(value)
        self.assertEqual(payload["decimal"], "79990.50")
        self.assertEqual(payload["path"], str(Path("snapshots/v2.json")))
        self.assertEqual(payload["bytes"], "цена")
        self.assertEqual(payload["set"], ["avito", "ozon"])
        self.assertEqual(payload["tuple"], [1, 2])
        self.assertEqual(payload["status"], "BLOCKED")
        encoded_one = to_json(value)
        encoded_two = to_json(value)
        self.assertEqual(encoded_one, encoded_two)
        self.assertIn("цена", encoded_one)
        self.assertLess(encoded_one.index('"bytes"'), encoded_one.index('"decimal"'))
        with self.assertRaises(TypeError):
            to_jsonable(object())

    def test_mutable_defaults_are_isolated(self) -> None:
        first = SearchRequestV2()
        second = SearchRequestV2()
        first.hard_tokens.append("Pro")
        first.required_specs["storage_gb"] = 256
        self.assertEqual(first.hard_tokens, ["Pro"])
        self.assertEqual(second.hard_tokens, [])
        self.assertEqual(first.required_specs, {"storage_gb": 256})
        self.assertEqual(second.required_specs, {})
        result_one = SearchResultV2()
        result_two = SearchResultV2()
        result_one.errors.append("timeout")
        self.assertEqual(result_one.errors, ["timeout"])
        self.assertEqual(result_two.errors, [])
        self.assertEqual(result_two.status, SearchResultStatus.ERROR)
        self.assertEqual(result_two.metrics.recommendation_count, 0)


if __name__ == "__main__":
    unittest.main()
