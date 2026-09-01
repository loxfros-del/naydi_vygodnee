#!/usr/bin/env python3
"""Category-fact regressions at the V2 RawOffer normalization boundary."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.models import ExactMatchResult, RawOffer, SearchRequestV2  # noqa: E402
from app.search_v2.normalization import normalize_raw_offer  # noqa: E402


def raw(title: str, *, facts: dict | None = None) -> RawOffer:
    return RawOffer(
        source="matrix", platform="Retail", title=title,
        url="https://example.test/product/123456", price=50_000,
        raw_metadata={"structured_facts": facts or {}},
    )


class SearchV2ParameterMatrixTests(unittest.TestCase):
    def test_laptop_title_facts_enable_existing_required_spec_gate(self) -> None:
        request = SearchRequestV2(
            category="laptop", canonical_model="Lenovo IdeaPad 3",
            required_specs={"cpu": "RYZEN 5 7530U", "ram_gb": 16, "ssd_gb": 512},
        )
        exact = normalize_raw_offer(raw(
            "Ноутбук Lenovo IdeaPad 3 Ryzen 5 7530U RAM 16 ГБ SSD 512 ГБ"
        ), request)
        wrong_ssd = normalize_raw_offer(raw(
            "Ноутбук Lenovo IdeaPad 3 Ryzen 5 7530U RAM 16 ГБ SSD 256 ГБ"
        ), request)

        self.assertEqual(exact.facts["ram_gb"], 16)
        self.assertEqual(exact.facts["ssd_gb"], 512)
        self.assertEqual(exact.identity.key_configuration["cpu"], "RYZEN 5 7530U")
        self.assertEqual(exact.exact_match, ExactMatchResult.EXACT)
        self.assertEqual(wrong_ssd.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_coffee_machine_type_and_cappuccinator_are_normalized(self) -> None:
        request = SearchRequestV2(
            category="coffee_machine",
            required_specs={"machine_type": "automatic", "cappuccinator": True},
        )
        exact = normalize_raw_offer(raw(
            "Автоматическая кофемашина Philips LatteGo с капучинатором"
        ), request)
        wrong_type = normalize_raw_offer(raw(
            "Капсульная кофемашина Philips с капучинатором"
        ), request)

        self.assertEqual(exact.facts["machine_type"], "automatic")
        self.assertTrue(exact.facts["cappuccinator"])
        self.assertTrue(exact.identity.key_configuration["automatic"])
        self.assertEqual(exact.exact_match, ExactMatchResult.EXACT)
        self.assertEqual(wrong_type.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_tv_resolution_and_refresh_rate_are_normalized(self) -> None:
        request = SearchRequestV2(
            category="tv", required_specs={"resolution": "4K", "refresh_rate": 120},
        )
        exact = normalize_raw_offer(raw("Телевизор TCL 55 4K 120 Гц"), request)
        wrong_refresh = normalize_raw_offer(raw("Телевизор TCL 55 4K 60 Гц"), request)

        self.assertEqual(exact.facts["resolution"], "4K")
        self.assertEqual(exact.identity.refresh_rate, "120 Гц")
        self.assertEqual(exact.exact_match, ExactMatchResult.EXACT)
        self.assertEqual(wrong_refresh.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_phone_storage_and_sim_variant_reject_wrong_variant(self) -> None:
        request = SearchRequestV2(
            category="phone", canonical_model="iPhone 16", model_modifiers=["Pro"],
            required_specs={"storage_gb": 256, "sim_variant": "eSIM"},
        )
        exact = normalize_raw_offer(raw("Apple iPhone 16 Pro 256 ГБ eSIM"), request)
        wrong_sim = normalize_raw_offer(raw("Apple iPhone 16 Pro 256 ГБ Dual SIM"), request)
        wrong_storage = normalize_raw_offer(raw("Apple iPhone 16 Pro 128 ГБ eSIM"), request)

        self.assertEqual(exact.facts["storage_gb"], 256)
        self.assertEqual(exact.identity.region_or_sim_variant, "esim")
        self.assertEqual(exact.exact_match, ExactMatchResult.EXACT)
        self.assertEqual(wrong_sim.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)
        self.assertEqual(wrong_storage.exact_match, ExactMatchResult.REQUIRED_SPEC_MISMATCH)

    def test_snippet_and_metadata_do_not_invent_a_hard_configuration(self) -> None:
        request = SearchRequestV2(
            category="laptop", canonical_model="Lenovo IdeaPad 3",
            required_specs={"ram_gb": 16, "ssd_gb": 512},
        )
        uncertain = normalize_raw_offer(raw(
            "Ноутбук Lenovo IdeaPad 3",
            facts={"ram": "16 ГБ", "ssd": "512 ГБ"},
        ), request)

        self.assertEqual(uncertain.facts["ram"], "16 ГБ")
        self.assertNotIn("ram_gb", uncertain.facts)
        self.assertNotIn("ssd_gb", uncertain.facts)
        self.assertEqual(uncertain.exact_match, ExactMatchResult.GENERIC_MATCH)

    def test_existing_structured_fact_keeps_precedence_over_title_extraction(self) -> None:
        request = SearchRequestV2(category="tv", required_specs={"resolution": "Full HD"})
        offer = normalize_raw_offer(raw(
            "Телевизор TCL 55 4K 120 Гц",
            facts={"resolution": "Full HD"},
        ), request)

        self.assertEqual(offer.facts["resolution"], "Full HD")
        self.assertEqual(offer.exact_match, ExactMatchResult.EXACT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
