#!/usr/bin/env python3
"""Regression cases for the first reference category: smartphones."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.grouping import group_offers  # noqa: E402
from app.search_v2.models import ExactMatchResult, ProductCondition, RawOffer  # noqa: E402
from app.search_v2.normalization import normalize_raw_offer  # noqa: E402
from app.search_v2.request_normalizer import normalize_legacy_request  # noqa: E402


class SmartphoneSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = normalize_legacy_request({
            "category": "smartphones",
            "product_name": "Apple iPhone 16 Pro",
            "model": "iPhone 16 Pro",
            "condition": "new",
            "category_details": {
                "phone_memory": "256 GB",
                "phone_sim_region": "eSIM",
            },
        })

    def raw(self, title: str, *, variant: str, price: int) -> RawOffer:
        return RawOffer(
            source="phone_source",
            platform="Ozon",
            title=title,
            url=f"https://shop.example/product/{price}",
            price=price,
            condition=ProductCondition.NEW,
            availability_text="В наличии",
            raw_metadata={"sim_variant": variant, "price_verified": True},
        )

    def test_wizard_phone_payload_becomes_a_supported_strict_request(self) -> None:
        self.assertEqual(self.request.category, "phone")
        self.assertTrue(self.request.supported_category)
        self.assertEqual(self.request.canonical_model, "iPhone 16")
        self.assertEqual(self.request.model_modifiers, ["Pro"])
        self.assertEqual(self.request.required_specs["storage_gb"], 256)
        self.assertEqual(self.request.required_specs["sim_variant"], "eSIM")

    def test_wrong_memory_model_or_sim_variant_cannot_pass(self) -> None:
        exact = normalize_raw_offer(
            self.raw("Apple iPhone 16 Pro 256 GB eSIM", variant="Global eSIM", price=78_000),
            self.request,
        )
        wrong_memory = normalize_raw_offer(
            self.raw("Apple iPhone 16 Pro 128 GB eSIM", variant="Global eSIM", price=69_000),
            self.request,
        )
        wrong_model = normalize_raw_offer(
            self.raw("Apple iPhone 16 Pro Max 256 GB eSIM", variant="Global eSIM", price=82_000),
            self.request,
        )
        wrong_variant = normalize_raw_offer(
            self.raw("Apple iPhone 16 Pro 256 GB Dual SIM", variant="Dual SIM", price=75_000),
            self.request,
        )
        self.assertEqual(exact.exact_match, ExactMatchResult.EXACT)
        self.assertEqual(wrong_memory.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)
        self.assertEqual(wrong_model.exact_match, ExactMatchResult.MODEL_MISMATCH)
        self.assertEqual(wrong_variant.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_different_phone_variants_do_not_share_a_price_group(self) -> None:
        esim = normalize_raw_offer(
            self.raw("Apple iPhone 16 Pro 256 GB eSIM", variant="eSIM", price=78_000), self.request,
        )
        dual_sim = normalize_raw_offer(
            self.raw("Apple iPhone 16 Pro 256 GB Dual SIM", variant="Dual SIM", price=77_000), self.request,
        )
        groups = group_offers([esim, dual_sim])
        self.assertEqual(len(groups), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
