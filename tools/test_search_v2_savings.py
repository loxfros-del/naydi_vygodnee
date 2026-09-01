#!/usr/bin/env python3
"""Proofs that client-visible smartphone savings have independent evidence."""
from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.models import (  # noqa: E402
    AvailabilityInfo, AvailabilityStatus, ExactMatchResult, Offer, ProductCondition,
    ProductGroup, ProductIdentity, SellerInfo, utc_now,
)
from app.search_v2.savings import build_savings_evidence  # noqa: E402


class SavingsEvidenceTests(unittest.TestCase):
    def offer(
        self,
        offer_id: str,
        price: int,
        *,
        source: str,
        seller: str | None = None,
        storage: int = 256,
        retrieved_at=None,
    ) -> Offer:
        return Offer(
            offer_id=offer_id,
            source=source,
            platform=source,
            title=f"Apple iPhone 16 Pro {storage} GB eSIM",
            url=f"https://{source}.example/product/{offer_id}",
            seller=SellerInfo(name=seller or f"seller-{offer_id}"),
            price=price,
            exact_match=ExactMatchResult.EXACT,
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
            price_confidence=0.95,
            retrieved_at=retrieved_at or utc_now(),
            identity=ProductIdentity(
                category="phone", brand="Apple", canonical_model="iPhone 16",
                modifiers=["pro"], storage=storage, condition=ProductCondition.NEW,
                region_or_sim_variant="esim",
            ),
        )

    def group(self, *offers: Offer) -> ProductGroup:
        return ProductGroup(group_id="iphone16pro-esim", offers=list(offers))

    def test_independent_current_offers_prove_ruble_saving(self) -> None:
        chosen = self.offer("chosen", 70_000, source="ozon")
        evidence = build_savings_evidence(
            chosen,
            self.group(
                chosen,
                self.offer("a", 75_000, source="dns"),
                self.offer("b", 80_000, source="mvideo"),
                self.offer("c", 90_000, source="citilink"),
            ),
        )

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.baseline_price, 80_000)
        self.assertEqual(evidence.saving_rub, 10_000)
        self.assertEqual(evidence.comparable_offer_count, 3)
        self.assertEqual(evidence.source_count, 3)
        self.assertEqual(evidence.client_reason, "экономия 10 000 ₽ относительно типичной цены 80 000 ₽")
        self.assertNotIn("chosen", evidence.reference_offer_ids)

    def test_duplicate_seller_or_one_source_cannot_create_evidence(self) -> None:
        chosen = self.offer("chosen", 70_000, source="ozon")
        duplicate_seller = self.group(
            chosen,
            self.offer("a", 80_000, source="dns", seller="Один магазин"),
            self.offer("b", 85_000, source="mvideo", seller="Один магазин"),
            self.offer("c", 90_000, source="citilink"),
        )
        one_source = self.group(
            chosen,
            self.offer("d", 80_000, source="ozon"),
            self.offer("e", 85_000, source="ozon"),
            self.offer("f", 90_000, source="ozon"),
        )
        self.assertIsNone(build_savings_evidence(chosen, duplicate_seller))
        self.assertIsNone(build_savings_evidence(chosen, one_source))

    def test_stale_or_other_configuration_cannot_be_baseline(self) -> None:
        chosen = self.offer("chosen", 70_000, source="ozon")
        stale = utc_now() - timedelta(hours=37)
        group = self.group(
            chosen,
            self.offer("a", 80_000, source="dns", retrieved_at=stale),
            self.offer("b", 85_000, source="mvideo", storage=128),
            self.offer("c", 90_000, source="citilink"),
        )
        self.assertIsNone(build_savings_evidence(chosen, group))


if __name__ == "__main__":
    unittest.main(verbosity=2)
