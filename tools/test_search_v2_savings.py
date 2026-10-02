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
        self.assertEqual(evidence.client_reason, "экономия 10 000 ₽ по цене товара относительно типичной цены 80 000 ₽; доставка и условия скидок проверяются отдельно")
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

    def current_group(self):
        chosen = self.offer("chosen", 70_000, source="ozon")
        references = [
            self.offer("a", 80_000, source="dns"),
            self.offer("b", 85_000, source="mvideo"),
            self.offer("c", 90_000, source="citilink"),
        ]
        return chosen, references

    def test_selected_merchant_is_not_an_independent_reference(self) -> None:
        chosen, references = self.current_group()
        references[0].seller.name = chosen.seller.name
        chosen.seller.seller_id = "ozon-id"
        references[0].seller.seller_id = "dns-id"
        self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))

    def test_same_url_with_different_seller_labels_is_not_independent(self) -> None:
        chosen, references = self.current_group()
        references[1].url = references[0].url + "?utm_source=duplicate"
        self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))

    def test_seller_alias_chain_cannot_restore_selected_merchant(self) -> None:
        chosen, references = self.current_group()
        chosen.seller.seller_id = "merchant-id"
        renamed = self.offer("renamed", 120_000, source="ozon", seller="Новое имя магазина")
        renamed.seller.seller_id = "merchant-id"
        references[0].seller.name = renamed.seller.name
        references[0].seller.seller_id = "other-platform-id"
        self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references, renamed)))

    def test_late_alias_link_merges_already_seen_references(self) -> None:
        chosen, references = self.current_group()
        references[0].seller.seller_id = "same-id"
        link = self.offer("alias-link", 120_000, source="dns", seller=references[1].seller.name)
        link.seller.seller_id = "same-id"
        for rows in (references + [link], [link] + references):
            self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *rows)))

    def test_foreign_or_missing_currency_cannot_be_ruble_evidence(self) -> None:
        for currency in ("USD", "EUR", ""):
            for position in ("selected", "reference"):
                with self.subTest(currency=currency, position=position):
                    chosen, references = self.current_group()
                    target = chosen if position == "selected" else references[0]
                    target.currency = currency
                    self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))

    def test_explicit_unavailability_or_rejected_verification_blocks_evidence(self) -> None:
        for field in ("model_verified", "link_verified", "price_verified", "availability_verified"):
            for stage in ("automatic_verification", "manual_verification", "final_verification"):
                with self.subTest(field=field, stage=stage):
                    chosen, references = self.current_group()
                    setattr(getattr(chosen, stage), field, False)
                    self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))
        chosen, references = self.current_group()
        chosen.availability.available = False
        self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))
        chosen, references = self.current_group()
        references[0].automatic_verification.price_verified = False
        self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))

    def test_resolved_manual_confirmation_supersedes_automatic_failure(self) -> None:
        chosen, references = self.current_group()
        chosen.automatic_verification.price_verified = False
        chosen.manual_verification.price_verified = True
        self.assertIsNotNone(build_savings_evidence(chosen, self.group(chosen, *references)))

    def test_unknown_merchant_cannot_prove_independence(self) -> None:
        chosen, references = self.current_group()
        references[0].seller = SellerInfo()
        self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))

    def test_lowest_listing_from_same_merchant_is_used_regardless_of_order(self) -> None:
        chosen, references = self.current_group()
        more_expensive = self.offer("expensive", 120_000, source="dns", seller=references[0].seller.name)
        evidence = build_savings_evidence(chosen, self.group(chosen, more_expensive, *references))
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.baseline_price, 85_000)
        self.assertNotIn("expensive", evidence.reference_offer_ids)

    def test_nonfinite_price_never_reaches_savings_arithmetic(self) -> None:
        for price in (float("inf"), float("nan")):
            chosen, references = self.current_group()
            references[0].price = price
            self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))

    def test_conditional_price_is_not_unconditional_savings_evidence(self) -> None:
        for data in ({"price_conditions": "цена с картой"}, {"price_kind": "installment"}):
            for position in ("selected", "reference"):
                for field in ("facts", "raw_metadata"):
                    with self.subTest(data=data, position=position, field=field):
                        chosen, references = self.current_group()
                        target = chosen if position == "selected" else references[0]
                        setattr(target, field, data)
                        self.assertIsNone(build_savings_evidence(chosen, self.group(chosen, *references)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
