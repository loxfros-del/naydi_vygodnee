#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.models import (  # noqa: E402
    AvailabilityStatus, ExactMatchResult, PlatformTrust, ProductCondition, RawOffer,
    SearchRequestV2, SellerTrust,
)
from app.search_v2.normalization import (  # noqa: E402
    extract_storage, normalize_offers, normalize_raw_offer,
)


class SearchV2NormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = SearchRequestV2(
            original_query="iPhone 16 Pro 256 ГБ до 80000",
            category="phone",
            brand="Apple",
            canonical_model="iPhone 16",
            model_modifiers=["pro"],
            required_specs={"storage": 256},
            budget=80_000,
            condition=ProductCondition.NEW,
            supported_category=True,
        )

    def test_marketplace_offer_is_normalized_without_inventing_seller_trust(self) -> None:
        raw = RawOffer(
            source="ozon_direct", platform="Ozon", title="Apple iPhone 16 Pro 256 ГБ новый",
            url="https://www.ozon.ru/product/123", product_id="123", seller_name="Example Store",
            price=79_990, availability_text="В наличии", condition=ProductCondition.NEW,
            raw_metadata={"seller_rating": 4.5, "seller_reviews_count": 8, "price_verified": True},
        )
        offer = normalize_raw_offer(raw, self.request)

        self.assertTrue(offer.offer_id.startswith("ozon direct:"))
        self.assertEqual(offer.price, 79_990)
        self.assertEqual(offer.platform_trust, PlatformTrust.HIGH_MARKETPLACE)
        self.assertEqual(offer.seller.trust, SellerTrust.UNKNOWN)
        self.assertEqual(offer.availability.status, AvailabilityStatus.IN_STOCK)
        self.assertTrue(offer.availability.available)
        self.assertEqual(offer.identity.brand, "Apple")
        self.assertEqual(offer.identity.canonical_model, "iPhone 16")
        self.assertEqual(offer.identity.storage, 256)
        self.assertIn("pro", offer.identity.modifiers)
        self.assertEqual(offer.condition, ProductCondition.NEW)
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)
        self.assertTrue(offer.automatic_verification.price_verified)
        self.assertTrue(offer.final_verification.model_verified)

    def test_retail_and_configuration_parsers(self) -> None:
        raw = RawOffer(
            source="dns_search", platform="DNS", title="Ноутбук Lenovo Ryzen 5 16/512 ГБ новый",
            seller_name="DNS", price=59_990, availability_text="доступен",
            raw_metadata={"seller_official": True},
        )
        request = SearchRequestV2(category="laptop", canonical_model="Lenovo Ryzen 5")
        offer = normalize_raw_offer(raw, request)
        self.assertEqual(extract_storage("16/512"), 512)
        self.assertEqual(extract_storage("1 ТБ"), 1024)
        self.assertEqual(offer.identity.storage, 512)
        self.assertEqual(offer.platform_trust, PlatformTrust.HIGH_RETAIL)
        self.assertEqual(offer.seller.trust, SellerTrust.HIGH)
        self.assertTrue(offer.seller.verified)

    def test_batch_preserves_order(self) -> None:
        raws = [RawOffer(source="x", title=f"iPhone 16 Pro 256 ГБ #{index}", price=70_000 + index) for index in range(3)]
        offers = normalize_offers(raws, self.request)
        self.assertEqual(len(offers), 3)
        self.assertEqual([offer.price for offer in offers], [70_000, 70_001, 70_002])


if __name__ == "__main__":
    unittest.main(verbosity=2)
