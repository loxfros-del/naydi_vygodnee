"""Deterministic category-specific fact extraction for Search Engine V2."""
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


def raw(title: str, product_id: str, price: int = 30_000) -> RawOffer:
    return RawOffer(
        source="shopping_search",
        platform="Яндекс Маркет",
        title=title,
        url=f"https://market.yandex.ru/product/{product_id}",
        product_id=product_id,
        seller_name="Проверяемый продавец",
        price=price,
        availability_text="В наличии",
        condition=ProductCondition.NEW,
        raw_metadata={"price_source": "structured_api"},
    )


class SearchV2CategoryFactsTests(unittest.TestCase):
    def test_oled_tv_matrix_and_hard_specs_are_exact(self) -> None:
        request = SearchRequestV2(
            category="tv",
            canonical_model="телевизор",
            required_specs={"matrix": "OLED", "diagonal": 55, "resolution": "4K", "refresh_rate": 120},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(raw("Телевизор LG OLED C4 55 дюймов 4K 144 Гц", "tv-oled"), request)
        self.assertEqual(offer.identity.key_configuration["matrix"], "OLED")
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_qled_tv_rejects_oled_requirement(self) -> None:
        request = SearchRequestV2(
            category="tv",
            canonical_model="телевизор",
            required_specs={"matrix": "OLED", "diagonal": 55, "resolution": "4K"},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(raw("Телевизор TCL QLED 55 дюймов 4K", "tv-qled"), request)
        self.assertEqual(offer.identity.key_configuration["matrix"], "QLED")
        self.assertEqual(offer.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_ultrawide_monitor_facts_are_exact(self) -> None:
        request = SearchRequestV2(
            category="monitor",
            canonical_model="монитор",
            required_specs={"diagonal": 34, "resolution": "WQHD", "refresh_rate": 144, "ultrawide": True},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(raw("Ультраширокий монитор Xiaomi 34 дюйма WQHD 180 Гц", "monitor-ultra"), request)
        self.assertTrue(offer.identity.key_configuration["ultrawide"])
        self.assertEqual(offer.identity.key_configuration["resolution"], "QHD")
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_headphones_connector_wireless_and_anc_are_exact(self) -> None:
        request = SearchRequestV2(
            category="headphones",
            canonical_model="наушники",
            required_specs={"connector": "USB-C", "wireless": True, "features": ["ANC"]},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Беспроводные TWS наушники с ANC и зарядкой USB-C", "headphones-anc", 9_990),
            request,
        )
        facts = offer.identity.key_configuration
        self.assertEqual(facts["connector"], "USB-C")
        self.assertTrue(facts["wireless"])
        self.assertIn("ANC", facts["features"])
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_gpu_requirement_is_extracted_and_exact(self) -> None:
        request = SearchRequestV2(
            category="laptop",
            canonical_model="ноутбук",
            required_specs={"gpu": "RTX 4060", "ram_gb": 16, "ssd_gb": 512},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Игровой ноутбук ASUS TUF RTX 4060 RAM 16 ГБ SSD 512 ГБ", "laptop-gpu", 119_990),
            request,
        )
        self.assertEqual(offer.identity.key_configuration["gpu"], "RTX 4060")
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_gaming_chair_load_is_exact(self) -> None:
        request = SearchRequestV2(
            category="chair",
            canonical_model="кресло",
            required_specs={"type": "gaming", "load_capacity_kg": 150},
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(raw("Игровое кресло ThunderX3 нагрузка до 180 кг", "chair-game", 19_990), request)
        facts = offer.identity.key_configuration
        self.assertEqual(facts["type"], "gaming")
        self.assertEqual(facts["load_capacity_kg"], 180)
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_ergonomic_mesh_chair_supports_are_exact(self) -> None:
        request = SearchRequestV2(
            category="chair",
            canonical_model="кресло",
            required_specs={
                "type": "ergonomic",
                "material": "mesh",
                "headrest": True,
                "lumbar_support": True,
            },
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        offer = normalize_raw_offer(
            raw("Эргономичное сетчатое кресло с подголовником и поясничной поддержкой", "chair-ergo", 24_990),
            request,
        )
        facts = offer.identity.key_configuration
        self.assertEqual(facts["type"], "ergonomic")
        self.assertEqual(facts["material"], "mesh")
        self.assertTrue(facts["headrest"])
        self.assertTrue(facts["lumbar_support"])
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)

    def test_named_chair_size_b_is_exact(self) -> None:
        request = SearchRequestV2(
            category="chair",
            brand="Herman Miller",
            canonical_model="Aeron",
            required_specs={"size": "B"},
            condition=ProductCondition.ANY,
            supported_category=True,
        )
        offer = normalize_raw_offer(raw("Herman Miller Aeron размер B", "aeron-b", 69_990), request)
        self.assertEqual(offer.identity.size, "B")
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
