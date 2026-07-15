"""Deterministic acceptance for category-level Search Engine V2 requests."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:ci_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.search_v2.models import (  # noqa: E402
    ExactMatchResult,
    ProductCondition,
    RawOffer,
    SearchRequestV2,
)
from app.search_v2.normalization import normalize_raw_offer  # noqa: E402


def raw(title: str, *, product_id: str = "1", price: int = 50_000, metadata=None) -> RawOffer:
    return RawOffer(
        source="yandex_market",
        platform="Яндекс Маркет",
        title=title,
        url=f"https://market.yandex.ru/product/{product_id}",
        product_id=product_id,
        seller_name="Проверяемый продавец",
        price=price,
        availability_text="в наличии",
        condition=ProductCondition.NEW,
        raw_metadata=metadata or {},
    )


class SearchV2GenericRequirementTests(unittest.TestCase):
    def test_laptop_ram_ssd_and_cpu_are_extracted(self) -> None:
        request = SearchRequestV2(
            category="laptop",
            canonical_model="ноутбук Ryzen 5",
            required_specs={"ram_gb": 16, "ssd_gb": 512},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Ноутбук ASUS Vivobook Ryzen 5 RAM 16 ГБ SSD 512 ГБ", product_id="laptop-1"),
            request,
        )
        self.assertEqual(offer.identity.key_configuration["ram_gb"], 16)
        self.assertEqual(offer.identity.key_configuration["ssd_gb"], 512)
        self.assertEqual(offer.identity.key_configuration["cpu_family"], "Ryzen 5")
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_compact_slash_laptop_configuration_is_extracted(self) -> None:
        request = SearchRequestV2(
            category="laptop",
            canonical_model="ноутбук Ryzen 5",
            required_specs={"ram_gb": 16, "ssd_gb": 512},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Ноутбук Lenovo Ryzen 5 16/512GB", product_id="laptop-2"),
            request,
        )
        self.assertEqual(offer.identity.key_configuration["ram_gb"], 16)
        self.assertEqual(offer.identity.key_configuration["ssd_gb"], 512)
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_monitor_accepts_higher_refresh_rate(self) -> None:
        request = SearchRequestV2(
            category="monitor",
            canonical_model="монитор",
            required_specs={"diagonal": 27, "resolution": "QHD", "refresh_rate": 144},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Монитор Xiaomi 27 дюймов QHD 180 Гц", product_id="monitor-1", price=29_990),
            request,
        )
        self.assertEqual(offer.identity.key_configuration["resolution"], "QHD")
        self.assertEqual(offer.identity.refresh_rate, 180)
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_monitor_rejects_wrong_resolution(self) -> None:
        request = SearchRequestV2(
            category="monitor",
            canonical_model="монитор",
            required_specs={"diagonal": 27, "resolution": "QHD", "refresh_rate": 144},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Монитор Acer 27 дюймов FHD 165 Гц", product_id="monitor-2", price=20_000),
            request,
        )
        self.assertEqual(offer.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_tv_hard_specs_become_exact(self) -> None:
        request = SearchRequestV2(
            category="tv",
            canonical_model="телевизор",
            required_specs={"diagonal": 55, "resolution": "4K", "refresh_rate": 120},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Телевизор TCL 55C855 55 дюймов 4K 144 Гц", product_id="tv-1", price=69_990),
            request,
        )
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_headphones_tws_anc_become_exact(self) -> None:
        request = SearchRequestV2(
            category="headphones",
            canonical_model="TWS наушники",
            required_specs={"features": ["ANC"]},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("TWS наушники Soundcore Liberty 4 NC с ANC", product_id="hp-1", price=9_990),
            request,
        )
        self.assertIn("ANC", offer.identity.key_configuration["features"])
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_office_chair_category_alias_is_exact(self) -> None:
        request = SearchRequestV2(
            category="chair",
            canonical_model="офисное кресло",
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Компьютерное кресло Metta Samurai", product_id="chair-1", price=14_900),
            request,
        )
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_specific_iphone_rules_remain_strict(self) -> None:
        request = SearchRequestV2(
            category="phone",
            brand="Apple",
            canonical_model="iPhone 16",
            model_modifiers=["Pro"],
            required_specs={"storage_gb": 256},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        wrong_storage = normalize_raw_offer(
            raw("Apple iPhone 16 Pro 128 ГБ", product_id="phone-1", price=79_000),
            request,
        )
        wrong_variant = normalize_raw_offer(
            raw("Apple iPhone 16 Pro Max 256 ГБ", product_id="phone-2", price=79_000),
            request,
        )
        self.assertEqual(wrong_storage.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)
        self.assertEqual(wrong_variant.exact_match, ExactMatchResult.MODEL_MISMATCH)

    def test_generic_identity_uses_real_model_not_category_word(self) -> None:
        request = SearchRequestV2(
            category="tv",
            canonical_model="телевизор",
            required_specs={"diagonal": 55, "resolution": "4K"},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        first = normalize_raw_offer(raw("Телевизор TCL 55C855 55 дюймов 4K", product_id="tv-a"), request)
        second = normalize_raw_offer(raw("Телевизор Hisense 55U7KQ 55 дюймов 4K", product_id="tv-b"), request)
        self.assertNotEqual(first.identity.canonical_model.casefold(), "телевизор")
        self.assertNotEqual(first.identity.canonical_key, second.identity.canonical_key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
