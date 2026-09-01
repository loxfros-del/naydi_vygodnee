#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.market_analysis import (  # noqa: E402
    analyze_market, analyze_product_groups, classify_price, is_extreme_price_outlier,
)
from app.search_v2.models import (  # noqa: E402
    AvailabilityInfo, AvailabilityStatus, ExactMatchResult, Offer, PriceClass, ProductGroup,
)


def priced(offer_id: str, price: float, *, exact=ExactMatchResult.EXACT,
           availability=AvailabilityStatus.IN_STOCK, confidence=0.9) -> Offer:
    return Offer(
        offer_id=offer_id, price=price, exact_match=exact, price_confidence=confidence,
        availability=AvailabilityInfo(status=availability, available=availability is not AvailabilityStatus.OUT_OF_STOCK),
    )


class SearchV2MarketAnalysisTests(unittest.TestCase):
    def test_median_trimmed_mean_and_exclusions(self) -> None:
        offers = [priced(str(index), price) for index, price in enumerate((70, 80, 90, 100, 1000), 1)]
        offers += [priced("wrong", 1, exact=ExactMatchResult.MODEL_MISMATCH), priced("gone", 2, availability=AvailabilityStatus.OUT_OF_STOCK)]
        stats = analyze_market(offers)
        self.assertEqual(stats.offer_count, 7)
        self.assertEqual(stats.verified_offer_count, 5)
        self.assertEqual(stats.minimum, 70)
        self.assertEqual(stats.median, 90)
        self.assertEqual(stats.trimmed_mean, 90)
        self.assertEqual(stats.maximum, 1000)
        self.assertEqual(stats.market_range, (70, 1000))
        self.assertNotIn("wrong", stats.price_classes)
        self.assertNotIn("gone", stats.price_classes)
        self.assertAlmostEqual(stats.deviation_percent["1"], -22.222, places=2)

    def test_price_classes_are_median_based(self) -> None:
        self.assertEqual(classify_price(70, 100), PriceClass.VERY_CHEAP)
        self.assertEqual(classify_price(85, 100), PriceClass.BELOW_MARKET)
        self.assertEqual(classify_price(100, 100), PriceClass.FAIR)
        self.assertEqual(classify_price(120, 100), PriceClass.ABOVE_MARKET)
        self.assertEqual(classify_price(140, 100), PriceClass.VERY_EXPENSIVE)
        self.assertEqual(classify_price(None, 100), PriceClass.UNKNOWN)

    def test_groups_receive_independent_statistics(self) -> None:
        groups = [ProductGroup(group_id="a", offers=[priced("a1", 10), priced("a2", 20)]), ProductGroup(group_id="b", offers=[priced("b1", 100)])]
        analyzed = analyze_product_groups(groups)
        self.assertEqual(len(analyzed), 2)
        self.assertEqual(analyzed[0].market_stats.median, 15)
        self.assertEqual(analyzed[1].market_stats.median, 100)

    def test_extreme_price_outlier_is_not_a_discount(self) -> None:
        cheap = priced("fake-accessory", 2_110)
        normal = priced("normal", 22_990)
        higher = priced("higher", 24_849)
        stats = analyze_market([cheap, normal, higher])
        self.assertTrue(is_extreme_price_outlier(cheap, stats))
        self.assertFalse(is_extreme_price_outlier(normal, stats))


if __name__ == "__main__":
    unittest.main(verbosity=2)
