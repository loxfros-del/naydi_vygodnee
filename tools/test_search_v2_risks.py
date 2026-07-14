from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.models import (
    AvailabilityInfo, AvailabilityStatus, ExactMatchResult, MarketStats, Offer,
    PlatformTrust, PriceClass, ProductCondition, RiskFlag, RiskSeverity,
    SellerInfo, SellerTrust, VerificationAccess,
)
from app.search_v2.risk_engine import apply_risks, assess_seller_trust, evaluate_risks, risk_penalty


class SearchV2RiskTests(unittest.TestCase):
    def offer(self, **changes) -> Offer:
        value = Offer(
            offer_id="offer-1",
            source="ozon",
            platform="Ozon",
            title="Apple iPhone 16 Pro 256 ГБ",
            url="https://ozon.ru/product/1",
            price=75_000,
            exact_match=ExactMatchResult.EXACT,
            platform_trust=PlatformTrust.HIGH_MARKETPLACE,
            seller=SellerInfo(name="Unknown Store", trust=SellerTrust.UNKNOWN),
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
            condition=ProductCondition.NEW,
            verification_access=VerificationAccess.FULL,
        )
        return replace(value, **changes)

    def codes(self, offer: Offer, stats: MarketStats | None = None) -> set[str]:
        return {item.code for item in evaluate_risks(offer, stats)}

    def test_marketplace_platform_and_unknown_seller_are_separate(self) -> None:
        offer = self.offer()
        self.assertEqual(offer.platform_trust, PlatformTrust.HIGH_MARKETPLACE)
        self.assertEqual(assess_seller_trust(offer), SellerTrust.UNKNOWN)
        self.assertIn("UNKNOWN_SELLER", self.codes(offer))
        self.assertNotIn("LOW_SELLER_TRUST", self.codes(offer))
        self.assertIsNone(offer.seller.rating)
        self.assertIsNone(offer.seller.official)

    def test_blocked_access_is_not_a_fraud_accusation(self) -> None:
        offer = self.offer(verification_access=VerificationAccess.BLOCKED)
        risks = evaluate_risks(offer)
        text = " ".join(f"{item.code} {item.title} {item.explanation}" for item in risks).casefold()
        self.assertNotIn("мошенн", text)
        self.assertNotIn("fraud", text)
        self.assertEqual(offer.platform_trust, PlatformTrust.HIGH_MARKETPLACE)

    def test_avito_private_and_few_reviews_are_honest_risks(self) -> None:
        offer = self.offer(
            source="avito", platform="Avito", platform_trust=PlatformTrust.CLASSIFIED,
            seller=SellerInfo(name="Иван", seller_type="private", reviews_count=2),
        )
        codes = self.codes(offer)
        self.assertIn("PRIVATE_SELLER", codes)
        self.assertIn("FEW_SELLER_REVIEWS", codes)
        self.assertNotIn("UNAVAILABLE", codes)
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_very_cheap_offer_is_kept_and_flagged(self) -> None:
        offer = self.offer(price=55_000)
        stats = MarketStats(
            median=80_000,
            price_classes={offer.offer_id: PriceClass.VERY_CHEAP},
            deviation_percent={offer.offer_id: -31.25},
        )
        updated = apply_risks(offer, stats)
        self.assertEqual(updated.price, 55_000)
        self.assertIn("VERY_CHEAP_PRICE", {item.code for item in updated.risk_flags})
        cheap = next(item for item in updated.risk_flags if item.code == "VERY_CHEAP_PRICE")
        self.assertEqual(cheap.severity, RiskSeverity.HIGH)
        self.assertIn("-31.2%", cheap.evidence[0])

    def test_unavailable_used_refurbished_and_metadata_risks(self) -> None:
        unavailable = self.offer(availability=AvailabilityInfo(status=AvailabilityStatus.OUT_OF_STOCK, available=False))
        self.assertIn("UNAVAILABLE", self.codes(unavailable))
        used = self.offer(condition=ProductCondition.USED)
        self.assertIn("USED_ITEM", self.codes(used))
        refurbished = self.offer(condition=ProductCondition.REFURBISHED)
        self.assertIn("REFURBISHED_ITEM", self.codes(refurbished))
        metadata = self.offer(raw_metadata={"activation_unknown": True, "incomplete_set": True, "esim_only": True})
        codes = self.codes(metadata)
        self.assertTrue({"UNKNOWN_ACTIVATION", "INCOMPLETE_SET", "REGION_OR_SIM_VARIANT"}.issubset(codes))

    def test_manual_resolution_removes_only_resolved_penalty(self) -> None:
        resolved = RiskFlag(code="OLD_PRICE", severity=RiskSeverity.HIGH, resolved_by_manual=True)
        active = RiskFlag(code="UNKNOWN_WARRANTY", severity=RiskSeverity.WARNING)
        offer = self.offer(risk_flags=[resolved, active])
        self.assertEqual(risk_penalty(offer), 4.0)
        self.assertGreater(risk_penalty(replace(offer, risk_flags=[active, replace(active, code="OTHER")])), 4.0)


if __name__ == "__main__":
    unittest.main()
