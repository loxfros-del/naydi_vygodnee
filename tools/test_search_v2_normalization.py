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
    extract_compact_memory_config, extract_storage, is_product_page_url, normalize_offers,
    normalize_raw_offer,
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

    def test_product_family_does_not_merge_different_skus(self) -> None:
        request = SearchRequestV2(
            category="coffee_machine",
            canonical_model="Кофемашина DeLonghi Magnifica",
            supported_category=True,
        )
        start = normalize_raw_offer(RawOffer(
            source="yandex_market",
            platform="Яндекс Маркет",
            title="Автоматическая кофемашина DeLonghi Magnifica Start ECAM220.22.GB",
            url="https://market.yandex.ru/product--magnifica-start/123456",
            price=28_998,
        ), request)
        magnifica_s = normalize_raw_offer(RawOffer(
            source="wildberries",
            platform="Wildberries",
            title="Кофемашина DeLonghi Magnifica S ECAM21.117.SB",
            url="https://www.wildberries.ru/catalog/12345678/detail.aspx",
            price=27_344,
        ), request)

        self.assertNotEqual(start.identity.canonical_model, request.canonical_model)
        self.assertNotEqual(magnifica_s.identity.canonical_model, request.canonical_model)
        self.assertNotEqual(start.identity.canonical_key, magnifica_s.identity.canonical_key)

    def test_macbook_variants_match_generation_but_keep_configuration_separate(self) -> None:
        broad_request = SearchRequestV2(
            category="laptop",
            brand="Apple",
            canonical_model="Ноутбук MacBook M4",
            model_modifiers=["Air"],
            supported_category=True,
        )
        specific_request = SearchRequestV2(
            category="laptop",
            brand="Apple",
            canonical_model="Ноутбук MacBook M4",
            model_modifiers=["Air"],
            required_specs={"ram_gb": 16, "storage_gb": 256},
            supported_category=True,
        )
        compact_256 = RawOffer(
            source="yandex_market",
            platform="Яндекс Маркет",
            title="Apple MacBook Air 13 M4 16/256GB",
            url="https://market.yandex.ru/product--macbook-air-13/123456",
            price=77_199,
        )
        compact_512 = RawOffer(
            source="yandex_market",
            platform="Яндекс Маркет",
            title="Ноутбук Apple MacBook Air 15 M4 16/512GB",
            url="https://market.yandex.ru/product--macbook-air-15/123457",
            price=127_285,
        )

        alternative_256 = normalize_raw_offer(compact_256, broad_request)
        alternative_512 = normalize_raw_offer(compact_512, broad_request)
        exact_256 = normalize_raw_offer(compact_256, specific_request)
        wrong_512 = normalize_raw_offer(compact_512, specific_request)

        self.assertEqual(extract_compact_memory_config(compact_256.title), (16, 256))
        self.assertEqual(extract_compact_memory_config("MacBook Air M4 16 ГБ / 512 ГБ SSD"), (16, 512))
        self.assertEqual(alternative_256.exact_match, ExactMatchResult.COMPATIBLE_VARIANT)
        self.assertEqual(alternative_512.exact_match, ExactMatchResult.COMPATIBLE_VARIANT)
        self.assertNotEqual(alternative_256.identity.canonical_key, alternative_512.identity.canonical_key)
        self.assertEqual(alternative_256.identity.storage, 256)
        self.assertEqual(alternative_512.identity.storage, 512)
        self.assertEqual(alternative_256.identity.diagonal, 13)
        self.assertEqual(alternative_512.identity.diagonal, 15)
        self.assertEqual(exact_256.exact_match, ExactMatchResult.EXACT)
        self.assertEqual(wrong_512.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_external_verification_never_turns_search_results_into_product_pages(self) -> None:
        search = RawOffer(
            source="yandex_web", platform="Яндекс Поиск", title="Apple iPhone 16 Pro 256 ГБ новый",
            url="https://yandex.ru/search/?text=iphone+16+pro", price=75_399,
            raw_metadata={"product_page_verified": True, "external_page_verified": True},
        )
        direct = RawOffer(
            source="yandex_web", platform="Яндекс Поиск", title="Apple iPhone 16 Pro 256 ГБ новый",
            url="https://store.example/p/iphone-16-pro", price=75_399,
            raw_metadata={"product_page_verified": True, "external_page_verified": True},
        )

        rejected = normalize_raw_offer(search, self.request)
        verified = normalize_raw_offer(direct, self.request)

        self.assertTrue(rejected.raw_metadata["not_product_page"])
        self.assertNotIn("not_product_page", verified.raw_metadata)

    def test_wildberries_accepts_only_a_numeric_detail_product_route(self) -> None:
        self.assertTrue(is_product_page_url("https://www.wildberries.ru/catalog/321732159/detail.aspx"))
        self.assertTrue(is_product_page_url("https://www.wildberries.ru/catalog/321732159/detail.aspx?utm_source=nova"))
        self.assertFalse(is_product_page_url("https://www.wildberries.ru/catalog/321732159"))
        self.assertFalse(is_product_page_url("https://www.wildberries.ru/catalog/coffee-machines"))
        self.assertFalse(is_product_page_url("https://www.wildberries.ru/catalog/321732159/detail.aspx?text=coffee"))

    def test_named_generic_tech_model_matches_only_its_own_code(self) -> None:
        request = SearchRequestV2(
            category="generic_tech", brand="Canon", canonical_model="Canon EOS R50",
            supported_category=True, hard_tokens=["Canon", "Canon EOS R50"],
        )
        same = normalize_raw_offer(RawOffer(
            source="retail", platform="Retail", title="Фотоаппарат Canon EOS R50",
            url="https://shop.example/canon-eos-r50", price=60_000,
        ), request)
        other = normalize_raw_offer(RawOffer(
            source="retail", platform="Retail", title="Фотоаппарат Canon EOS R10",
            url="https://shop.example/canon-eos-r10", price=55_000,
        ), request)
        self.assertEqual(same.exact_match, ExactMatchResult.EXACT)
        self.assertEqual(other.exact_match, ExactMatchResult.MODEL_MISMATCH)

    def test_mini_led_is_not_a_conflicting_model_modifier(self) -> None:
        request = SearchRequestV2(
            category="monitor",
            brand="Xiaomi",
            canonical_model="G 27i",
            model_modifiers=["Pro"],
            required_specs={"diagonal": 27, "resolution": "QHD", "refresh_rate": 180},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        candidate = normalize_raw_offer(RawOffer(
            source="dns",
            platform="DNS",
            title="Xiaomi Mini LED Gaming Monitor G Pro 27i 27 дюймов QHD 180 Гц новый",
            url="https://www.dns-shop.ru/product/123456/",
            price=48_990,
            condition=ProductCondition.NEW,
            availability_text="в наличии",
            raw_metadata={"structured_facts": request.required_specs},
        ), request)

        self.assertNotIn("mini", candidate.identity.modifiers)
        self.assertIn("pro", candidate.identity.modifiers)
        self.assertEqual(candidate.exact_match, ExactMatchResult.EXACT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
