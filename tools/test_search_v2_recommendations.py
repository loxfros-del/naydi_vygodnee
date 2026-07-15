from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.market_analysis import analyze_market
from app.search_v2.models import (
    AvailabilityInfo, AvailabilityStatus, ExactMatchResult, Offer, PlatformTrust,
    ProductGroup, RecommendationRole, SellerInfo, SellerTrust,
)
from app.search_v2.recommendations import select_recommendations


class SearchV2RecommendationTests(unittest.TestCase):
    def offer(self, offer_id: str, price: int, *, platform: PlatformTrust, seller: SellerTrust, source: str) -> Offer:
        return Offer(
            offer_id=offer_id,
            source=source,
            platform=source,
            title="Apple iPhone 16 Pro 256 ГБ",
            url=f"https://{source}.example/product/{offer_id}",
            price=price,
            exact_match=ExactMatchResult.EXACT,
            platform_trust=platform,
            seller=SellerInfo(name=f"seller-{offer_id}", trust=seller, warranty="1 год"),
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
            price_confidence=0.95,
        )

    def group(self, *offers: Offer) -> ProductGroup:
        rows = list(offers)
        return ProductGroup(group_id="g", canonical_key="same-product", offers=rows, market_stats=analyze_market(rows))

    def test_three_roles_are_unique_when_quality_alternatives_exist(self) -> None:
        marketplace = self.offer("market", 79_000, platform=PlatformTrust.HIGH_MARKETPLACE, seller=SellerTrust.HIGH, source="ozon")
        cheap = self.offer("cheap", 62_000, platform=PlatformTrust.CLASSIFIED, seller=SellerTrust.UNKNOWN, source="avito")
        dns = self.offer("dns", 80_000, platform=PlatformTrust.HIGH_RETAIL, seller=SellerTrust.HIGH, source="dns")
        citilink = self.offer("citilink", 82_000, platform=PlatformTrust.HIGH_RETAIL, seller=SellerTrust.HIGH, source="citilink")
        recommendations = select_recommendations([self.group(marketplace, cheap, dns, citilink)])
        roles = {item.role for item in recommendations}
        ids = [item.offer_id for item in recommendations]
        self.assertLessEqual(len(recommendations), 3)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn(RecommendationRole.BEST_OVERALL, roles)
        self.assertIn(RecommendationRole.CHEAP_WITH_RISK, roles)
        self.assertIn(RecommendationRole.RELIABLE, roles)
        self.assertTrue(all(item.offer is not None for item in recommendations))

    def test_one_offer_never_occupies_all_roles(self) -> None:
        dns = self.offer("dns", 80_000, platform=PlatformTrust.HIGH_RETAIL, seller=SellerTrust.HIGH, source="dns")
        rows = select_recommendations([self.group(dns)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].role, RecommendationRole.BEST_OVERALL)
        self.assertEqual(rows[0].offer_id, "dns")

    def test_two_offers_do_not_invent_a_third(self) -> None:
        first = self.offer("a", 70_000, platform=PlatformTrust.HIGH_MARKETPLACE, seller=SellerTrust.UNKNOWN, source="ozon")
        second = self.offer("b", 80_000, platform=PlatformTrust.HIGH_RETAIL, seller=SellerTrust.HIGH, source="dns")
        rows = select_recommendations([self.group(first, second)])
        self.assertLessEqual(len(rows), 2)
        self.assertEqual(len({item.offer_id for item in rows}), len(rows))

    def test_unavailable_accessory_and_wrong_model_are_never_recommended(self) -> None:
        base = self.offer("x", 10_000, platform=PlatformTrust.HIGH_MARKETPLACE, seller=SellerTrust.HIGH, source="ozon")
        unavailable = replace(base, offer_id="u", availability=AvailabilityInfo(status=AvailabilityStatus.OUT_OF_STOCK, available=False))
        accessory = replace(base, offer_id="a", exact_match=ExactMatchResult.ACCESSORY)
        wrong = replace(base, offer_id="w", exact_match=ExactMatchResult.MODEL_MISMATCH)
        self.assertEqual(select_recommendations([self.group(unavailable, accessory, wrong)]), [])

    def test_avito_new_exact_can_be_best_or_cheap(self) -> None:
        avito = self.offer("avito", 65_000, platform=PlatformTrust.CLASSIFIED, seller=SellerTrust.MEDIUM, source="avito")
        dns = self.offer("dns", 82_000, platform=PlatformTrust.HIGH_RETAIL, seller=SellerTrust.HIGH, source="dns")
        rows = select_recommendations([self.group(avito, dns)])
        avito_roles = {item.role for item in rows if item.offer_id == "avito"}
        self.assertTrue(avito_roles)
        self.assertTrue(avito_roles <= {RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAP_WITH_RISK})
        self.assertNotEqual(avito.seller.trust, SellerTrust.LOW)


if __name__ == "__main__":
    unittest.main()
