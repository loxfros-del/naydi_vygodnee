from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.source_strategy import (
    CandidatePoolQuota,
    DISCOVERY_CORE,
    RELIABLE_ANCHORS,
    SearchBudget,
    SourceGroup,
    build_source_plan,
    normalize_source_status,
    select_with_source_quotas,
    source_group,
)
from app.sources.offer import (
    SourceBatch,
    SourceStatus,
    normalize_legacy_batch,
    normalize_legacy_offer,
)


class OfferContractTests(unittest.TestCase):
    def test_source_status_contract(self) -> None:
        self.assertEqual(
            {status.value for status in SourceStatus},
            {"SUCCESS", "EMPTY", "BLOCKED", "RATE_LIMITED", "TIMEOUT", "INVALID_RESPONSE", "ERROR"},
        )

    def test_safe_status_normalization(self) -> None:
        self.assertEqual(normalize_source_status("ok"), SourceStatus.SUCCESS)
        self.assertEqual(normalize_source_status("403 captcha"), SourceStatus.BLOCKED)
        self.assertEqual(normalize_source_status("429 Too Many Requests"), SourceStatus.RATE_LIMITED)
        self.assertEqual(normalize_source_status("source timeout"), SourceStatus.TIMEOUT)
        self.assertEqual(normalize_source_status("bad-json"), SourceStatus.INVALID_RESPONSE)
        self.assertEqual(normalize_source_status("unexpected-new-status"), SourceStatus.ERROR)

    def test_normalize_legacy_offer_preserves_all_contract_facts(self) -> None:
        offer = normalize_legacy_offer({
            "source": "ozon_search",
            "seller": "Example Store",
            "title": "Apple iPhone 16 Pro 256 ГБ",
            "url": "https://www.ozon.ru/product/123",
            "id": 123,
            "price": "79 990 ₽",
            "old_price": 84990,
            "availability": True,
            "condition": "new",
            "city": "Ярославль",
            "delivery": "завтра",
            "rating": "4.8",
            "reviews_count": "1 245",
            "image": "https://cdn.example/123.jpg",
            "body": "В наличии",
            "facts": {"storage_gb": 256},
            "raw": {"offer_id": "raw-123"},
        })
        self.assertEqual(offer.platform, "Ozon")
        self.assertEqual(offer.product_id, "123")
        self.assertEqual(offer.price, 79990)
        self.assertEqual(offer.old_price, 84990)
        self.assertEqual(offer.availability, "AVAILABLE")
        self.assertEqual(offer.seller_rating, 4.8)
        self.assertEqual(offer.seller_reviews_count, 1245)
        self.assertEqual(offer.structured_facts["storage_gb"], 256)
        self.assertIn("_legacy_raw", offer.structured_facts)

    def test_legacy_batch_keeps_partial_offers_with_blocked_status(self) -> None:
        batch = normalize_legacy_batch({
            "source": "ozon_search",
            "status": "blocked 403",
            "error": "captcha",
            "candidates": [{"title": "iPhone 16 Pro", "url": "https://ozon.ru/product/1", "price": 79990}],
        }, query="iPhone 16 Pro 256 ГБ")
        self.assertIsInstance(batch, SourceBatch)
        self.assertEqual(batch.status, SourceStatus.BLOCKED)
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.error, "captcha")

    def test_empty_success_batch_becomes_empty(self) -> None:
        batch = normalize_legacy_batch({"source": "wildberries", "status": "ok", "candidates": []})
        self.assertEqual(batch.status, SourceStatus.EMPTY)


class SourceStrategyTests(unittest.TestCase):
    def test_groups_match_architecture(self) -> None:
        self.assertEqual(source_group("yandex_market_direct"), SourceGroup.DISCOVERY)
        self.assertEqual(source_group("ozon_search"), SourceGroup.DISCOVERY)
        self.assertEqual(source_group("citilink_direct"), SourceGroup.RELIABLE)
        self.assertEqual(source_group("generic_web"), SourceGroup.AUXILIARY)

    def test_budget_rejects_expansion_above_hard_limits(self) -> None:
        invalid = (
            {"main_query_limit": 2},
            {"variant_query_limit": 3},
            {"per_discovery_source_limit": 2},
            {"max_source_concurrency": 4},
            {"max_verification_concurrency": 3},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SearchBudget(**kwargs)

    def test_plan_has_one_clean_main_and_at_most_two_variants(self) -> None:
        plan = build_source_plan(
            "  iPhone 16 Pro 256 ГБ   до 80000  Ярославль ",
            ["iPhone 16 Pro 256 ГБ", "iPhone 16 Pro новый", "iPhone 16 Pro доставка", "iPhone 16 Pro новый"],
        )
        self.assertEqual(plan.main_query, "iPhone 16 Pro 256 ГБ до 80000 Ярославль")
        self.assertEqual(len(plan.variant_queries), 2)
        self.assertEqual(len(set(plan.variant_queries)), 2)

    def test_each_priority_source_receives_only_one_query(self) -> None:
        plan = build_source_plan("iPhone 16 Pro 256 ГБ")
        for source in (*DISCOVERY_CORE, *RELIABLE_ANCHORS):
            self.assertEqual(plan.query_count(source), 1)

    def test_discovery_is_planned_before_reliable_anchors(self) -> None:
        plan = build_source_plan("iPhone 16 Pro 256 ГБ")
        groups = [item.group for item in plan.source_requests]
        first_reliable = groups.index(SourceGroup.RELIABLE)
        self.assertTrue(all(group == SourceGroup.DISCOVERY for group in groups[:first_reliable]))
        self.assertIn(SourceGroup.RELIABLE, groups)

    def test_source_execution_waves_never_exceed_three(self) -> None:
        plan = build_source_plan("iPhone 16 Pro 256 ГБ")
        self.assertTrue(plan.source_batches())
        self.assertTrue(all(len(batch) <= 3 for batch in plan.source_batches()))

    def test_enabled_filter_keeps_requested_core_and_anchor(self) -> None:
        plan = build_source_plan(
            "iPhone 16 Pro 256 ГБ",
            enabled_sources=["ozon", "avito", "dns", "mvideo_direct"],
        )
        self.assertEqual(
            {item.source for item in plan.source_requests},
            {"ozon_search", "avito_search", "dns_search", "mvideo_search"},
        )

    def test_pool_quota_preserves_discovery_and_anchor(self) -> None:
        ranked = [
            {"id": "g1", "source": "generic_web"},
            {"id": "g2", "source": "generic_web"},
            {"id": "o1", "source": "ozon_search"},
            {"id": "d1", "source": "dns_search"},
            {"id": "a1", "source": "avito_search"},
        ]
        selected = select_with_source_quotas(
            ranked,
            source_getter=lambda item: item["source"],
            identity_getter=lambda item: item["id"],
            quota=CandidatePoolQuota(total_limit=4, discovery_reserved=1, reliable_reserved=1, per_source_limit=2),
        )
        groups = {source_group(item["source"]) for item in selected}
        self.assertIn(SourceGroup.DISCOVERY, groups)
        self.assertIn(SourceGroup.RELIABLE, groups)
        self.assertEqual(len(selected), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
