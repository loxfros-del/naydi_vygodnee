"""Offline regression tests; real config, databases and network are never used."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.purchase_claims import has_price_advantage_claim


def load_ai_cards():
    config = ModuleType("app.config")
    config.settings = SimpleNamespace()
    database = ModuleType("app.db")
    database.Request = SimpleNamespace
    database.SearchResult = SimpleNamespace
    database.to_int_price = lambda value: int(value) if value is not None else None
    review = ModuleType("app.services.ai_review")
    for state in ("APPROVED", "DRAFT", "ERROR", "GENERATED"):
        setattr(review, f"AI_CARD_{state}", state)
    http = ModuleType("requests")
    def reject_network(*args, **kwargs):
        raise AssertionError("Network must not be used by these tests")
    http.post = reject_network
    spec = importlib.util.spec_from_file_location("isolated_ai_cards_service", ROOT / "app" / "ai_cards_service.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"app.config": config, "app.db": database, "app.services.ai_review": review, "requests": http}):
        spec.loader.exec_module(module)
    return module


class ProductSelectionClaimsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ai = load_ai_cards()

    def request(self):
        return SimpleNamespace(
            budget="80000", product_name="iPhone 16 Pro 256 GB", product="", use_case="для фото",
            purpose="", important_criteria="256 GB", criteria="", city="Москва",
            is_used_allowed=False, original_query="iPhone 16 Pro 256 GB для фото",
        )

    def candidate(self, exact="EXACT"):
        facts = {
            "verify_status": "VERIFIED_GOOD", "exact_match": exact,
            "exact_product_verified": True, "product_page_verified": True,
            "price_verified": True, "availability_verified": True,
            "seller_verified": True, "available": True, "price_confidence": "high",
            "platform_type": "RETAIL",
        }
        return SimpleNamespace(
            id=1, title="iPhone 16 Pro 256 GB", price=70000, source="DNS",
            url="https://www.dns-shop.ru/product/iphone16pro", snippet="",
            score=90, risk_flags="[]", status="CANDIDATE", origin="v2",
            facts_json=json.dumps(facts), price_verified=True,
        )

    def parse(self, why, exact="EXACT"):
        raw = json.dumps({"cards": [{"candidate_id": 1, "why": why}]})
        return self.ai.parse_ai_cards_for_candidates(raw, self.request(), [self.candidate(exact)])[0]

    def test_unproved_market_claims_do_not_become_approved_why(self):
        for why in (
            "Ниже рынка на 30%", "Экономия 20 000 рублей", "Самая низкая цена",
            "Выгоднее магазинов", "Дешевле розницы", "Цена ниже медианы", "Скидка 5000 по карте",
            "Ниже обычной стоимости", "Снижение цены",
        ):
            with self.subTest(why=why):
                card = self.parse(why)
                self.assertFalse(has_price_advantage_claim(card["why"]))
                self.assertNotIn(why, card["pluses"])
                self.assertIn("цена укладывается в бюджет", card["why"])

    def test_regular_editorial_explanation_remains_useful(self):
        for why in (
            "Объём памяти подходит для хранения фотографий", "Охват 100% sRGB для работы с фото",
            "Экономичный процессор", "Экономия энергии благодаря эффективному процессору",
        ):
            with self.subTest(why=why):
                self.assertEqual(self.parse(why)["why"], why)

    def test_compatible_variant_never_promises_verified_configuration(self):
        card = self.parse("Все характеристики точно совпадают", exact="COMPATIBLE_VARIANT")
        self.assertIn("конфигурацию нужно уточнить", card["why"])
        self.assertNotIn("обязательные характеристики совпадают", card["why"])

    def test_legacy_exact_status_alias_keeps_configuration_caveat(self):
        candidate = self.candidate("COMPATIBLE_VARIANT")
        facts = json.loads(candidate.facts_json)
        facts["exact_match_status"] = facts.pop("exact_match")
        candidate.facts_json = json.dumps(facts)
        why = self.ai._factual_why(self.request(), candidate)
        self.assertIn("конфигурацию нужно уточнить", why)

    def test_prompt_distinguishes_item_price_delivery_and_conditional_discount(self):
        prompt = self.ai.build_ai_cards_prompt(self.request(), [self.candidate()])
        self.assertIn("для этого нужны отдельные доказательства сравнения", prompt)
        self.assertIn("итоговую стоимость с доставкой", prompt)
        self.assertIn("условия карт, клубов, подписок", prompt)
        self.assertIn("COMPATIBLE_VARIANT", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
