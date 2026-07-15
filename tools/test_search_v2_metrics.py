from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.metrics import build_search_metrics
from app.search_v2.models import (
    AvailabilityInfo, AvailabilityStatus, ExactMatchResult, Offer, ProductGroup,
    Recommendation, RecommendationRole, SourceAttempt, SourceStatus,
)


class SearchV2MetricsTests(unittest.TestCase):
    def test_all_quality_metrics_are_derived_without_network(self) -> None:
        good = Offer(
            offer_id="a", source="ozon", title="Exact", price=100,
            exact_match=ExactMatchResult.EXACT,
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
        )
        second = Offer(
            offer_id="b", source="dns", title="Variant", price=110,
            exact_match=ExactMatchResult.COMPATIBLE_VARIANT,
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
        )
        wrong = Offer(offer_id="c", exact_match=ExactMatchResult.MODEL_MISMATCH)
        metrics = build_search_metrics(
            attempts=[
                SourceAttempt(source="ozon", status=SourceStatus.SUCCESS, cache_hit=True),
                SourceAttempt(source="dns", status=SourceStatus.TIMEOUT),
                SourceAttempt(source="avito", status=SourceStatus.BLOCKED),
            ],
            raw_offer_count=3,
            offers=[good, second],
            rejected_offers=[wrong],
            groups=[ProductGroup(group_id="g")],
            recommendations=[
                Recommendation(role=RecommendationRole.BEST_OVERALL, offer_id="a", offer=good),
                Recommendation(role=RecommendationRole.RELIABLE, offer_id="b", offer=second),
            ],
            duration_ms=321,
            manual_review_required=True,
        )
        self.assertAlmostEqual(metrics.source_success_rate, 1 / 3, places=4)
        self.assertAlmostEqual(metrics.source_timeout_rate, 1 / 3, places=4)
        self.assertAlmostEqual(metrics.blocked_rate, 1 / 3, places=4)
        self.assertEqual(metrics.raw_offer_count, 3)
        self.assertEqual(metrics.exact_offer_count, 1)
        self.assertEqual(metrics.wrong_model_rejection_count, 1)
        self.assertEqual(metrics.valid_price_rate, 1.0)
        self.assertEqual(metrics.product_group_count, 1)
        self.assertEqual(metrics.recommendation_count, 2)
        self.assertTrue(metrics.top1_exact)
        self.assertEqual(metrics.top3_useful, 2)
        self.assertAlmostEqual(metrics.duplicate_rate, 1 / 3, places=4)
        self.assertEqual(metrics.source_diversity, 2)
        self.assertEqual(metrics.duration_ms, 321)
        self.assertEqual(metrics.cache_hit_count, 1)
        self.assertTrue(metrics.manual_review_required)

    def test_empty_metrics_are_zero_and_safe(self) -> None:
        metrics = build_search_metrics(
            attempts=[], raw_offer_count=0, offers=[], rejected_offers=[], groups=[],
            recommendations=[], duration_ms=-1,
        )
        self.assertEqual(metrics.source_success_rate, 0)
        self.assertEqual(metrics.valid_price_rate, 0)
        self.assertEqual(metrics.duplicate_rate, 0)
        self.assertFalse(metrics.top1_exact)
        self.assertEqual(metrics.duration_ms, 0)


if __name__ == "__main__":
    unittest.main()
