"""Быстрые детерминированные инварианты для финальной политики поиска."""
from __future__ import annotations

import sys
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.candidate_verifier import PRICE_MISSING, VerifiedCandidate, _extract_laptop_facts
from app.db import Request, SearchResult
from app.handlers.admin import _facts_detail_lines, _telegram_chunks, format_search_result_card
from app.price_extractor import extract_price
from app.product_quality import final_product_quality_level, has_model_mismatch
from app.product_search import ProductCandidate, _apply_final_search_policy, _verification_rank
from app.search_policy import (
    is_normal_candidate,
    normalize_for_admin_save,
    should_save_for_admin,
    summarize_saved_statuses,
)


PRODUCT_URL = "https://shop.example/product/test-item"


def policy_candidate(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "title": "Тестовый товар с подтвержденной моделью",
        "url": PRODUCT_URL,
        "source": "direct_store",
        "price": 10_000,
        "score": 100,
        "verify_status": "VERIFIED_GOOD",
        "status": "VERIFIED_GOOD",
        "product_quality_level": "good",
        "budget_status": "IN_BUDGET",
        "reasons": [],
    }
    candidate.update(overrides)
    return normalize_for_admin_save(candidate)


def request(product_name: str, *, budget: str = "10000", original_query: str = "") -> Request:
    return Request(
        id=0,
        user_id=0,
        product=product_name,
        product_name=product_name,
        budget=budget,
        original_query=original_query or product_name,
    )


class SearchInvariantTests(unittest.TestCase):
    def test_weak_is_never_normal(self) -> None:
        candidate = policy_candidate(product_quality_level="weak")
        self.assertEqual(candidate["verify_status"], "NEED_MANUAL_CHECK")
        self.assertFalse(is_normal_candidate(candidate))

    def test_bad_is_never_good_or_ok(self) -> None:
        candidate = policy_candidate(product_quality_level="bad")
        self.assertEqual(candidate["verify_status"], "WRONG_PRODUCT")
        self.assertFalse(is_normal_candidate(candidate))

    def test_price_missing_is_never_normal(self) -> None:
        candidate = policy_candidate(price=None)
        self.assertEqual(candidate["verify_status"], "PRICE_MISSING")
        self.assertFalse(is_normal_candidate(candidate))

    def test_verify_blocked_requires_real_block_reason(self) -> None:
        blocked = policy_candidate(verify_status="VERIFY_BLOCKED", reasons=["HTTP 403 forbidden"])
        manual = policy_candidate(verify_status="VERIFY_BLOCKED", reasons=["страницу надо посмотреть"])
        self.assertEqual(blocked["verify_status"], "VERIFY_BLOCKED")
        self.assertEqual(manual["verify_status"], "NEED_MANUAL_CHECK")

    def test_manual_product_card_is_saved(self) -> None:
        candidate = policy_candidate(verify_status="NEED_MANUAL_CHECK")
        self.assertTrue(should_save_for_admin(candidate))

    def test_price_missing_product_card_is_saved(self) -> None:
        candidate = policy_candidate(price=None)
        self.assertEqual(candidate["verify_status"], "PRICE_MISSING")
        self.assertTrue(should_save_for_admin(candidate))

    def test_final_policy_keeps_price_missing_product_card(self) -> None:
        req = request("наушники", budget="5000")
        candidate = ProductCandidate(title="Наушники Sony WH-C510", url=PRODUCT_URL, price=None)
        verified = VerifiedCandidate(candidate, PRICE_MISSING, price=None, keep_for_admin=False)
        _apply_final_search_policy(req, [verified])
        self.assertTrue(verified.keep_for_admin)
        self.assertEqual(candidate.status, "WEAK_CANDIDATE")
        self.assertEqual(candidate.product_facts["verify_status"], PRICE_MISSING)

    def test_price_missing_article_is_not_saved(self) -> None:
        candidate = policy_candidate(
            title="Обзор лучших ноутбуков 2026",
            url="https://shop.example/catalog/laptops",
            price=None,
        )
        self.assertFalse(should_save_for_admin(candidate))

    def test_wrong_product_is_not_saved(self) -> None:
        self.assertFalse(should_save_for_admin(policy_candidate(product_quality_level="bad")))

    def test_unavailable_is_not_saved(self) -> None:
        candidate = policy_candidate(snippet="Товар закончился, посмотреть аналоги")
        self.assertEqual(candidate["verify_status"], "UNAVAILABLE")
        self.assertFalse(should_save_for_admin(candidate))

    def test_soft_budget_overage_is_saved_but_not_normal(self) -> None:
        candidate = policy_candidate(budget_status="OVER_BUDGET_SOFT")
        self.assertEqual(candidate["verify_status"], "OVER_BUDGET_SOFT")
        self.assertTrue(should_save_for_admin(candidate))
        self.assertFalse(is_normal_candidate(candidate))

    def test_hard_budget_overage_is_not_saved(self) -> None:
        candidate = policy_candidate(budget_status="OVER_BUDGET_HARD")
        self.assertEqual(candidate["verify_status"], "OVER_BUDGET_HARD")
        self.assertFalse(should_save_for_admin(candidate))

    def test_price_extractor_rejects_specs(self) -> None:
        self.assertIsNone(extract_price("SSD 1024 ГБ"))
        self.assertIsNone(extract_price("RAM 16 ГБ SSD 512 ГБ"))

    def test_price_extractor_accepts_explicit_prices(self) -> None:
        cases = {
            "цена 49 489 ₽": 49_489,
            "Ноутбук Ryzen 5 16/512, 49 489 руб.": 49_489,
            "Наушники 2663 ₽ Bluetooth 5.3": 2_663,
            "10000 mAh, цена 1999 ₽": 1_999,
            "120 кг, стоимость 8990 ₽": 8_990,
            "1920x1080, 144 Гц, 45999 ₽": 45_999,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_price(text), expected)

    def test_iphone_17e_is_not_17_pro(self) -> None:
        req = request("iPhone 17 Pro")
        candidate = ProductCandidate(title="Apple iPhone 17e 256GB", url=PRODUCT_URL)
        self.assertTrue(has_model_mismatch(candidate, req))

    def test_iphone_17_pro_max_is_not_17_pro(self) -> None:
        req = request("iPhone 17 Pro")
        candidate = ProductCandidate(title="Apple iPhone 17 Pro Max 256GB", url=PRODUCT_URL)
        self.assertTrue(has_model_mismatch(candidate, req))

    def test_laptop_brand_uses_word_boundaries(self) -> None:
        expected = {
            "LUNNEN Ground Ryzen 7": "LUNNEN",
            "EXPEcomp EVO Ryzen 5": "EXPEcomp",
            "ECHIPS Taganay N95": "ECHIPS",
            "HP 250 G10": "HP",
            "ASUS Vivobook 15": "Asus",
            "Lenovo V15": "Lenovo",
        }
        for text, brand in expected.items():
            with self.subTest(text=text):
                self.assertEqual(_extract_laptop_facts(text)["brand"], brand)

    def test_known_wireless_headphones_rank_above_wired_basic(self) -> None:
        req = request("беспроводные наушники", budget="5000")
        wireless = ProductCandidate(
            title="Soundcore Liberty 4 NC wireless ANC",
            url="https://shop.example/product/soundcore",
            price=4_500,
            score=80,
        )
        wired = ProductCandidate(
            title="JBL Tune 110 проводные",
            url="https://shop.example/product/jbl-tune-110",
            price=1_000,
            score=80,
        )
        wireless.verify_status = "VERIFIED_GOOD"
        wired.verify_status = "VERIFIED_GOOD"
        self.assertLess(_verification_rank(req, wireless), _verification_rank(req, wired))

    def test_laptop_n95_is_not_good(self) -> None:
        candidate = ProductCandidate(title="LUNNEN N95 16GB SSD 512GB", url=PRODUCT_URL, price=40_000)
        level, _ = final_product_quality_level(candidate, is_laptop=True)
        self.assertEqual(level, "weak")

    def test_ps5_tv_without_4k_is_not_good(self) -> None:
        candidate = ProductCandidate(title="TCL 55 Full HD телевизор", url=PRODUCT_URL, price=30_000)
        level, _ = final_product_quality_level(candidate, is_ps5_tv=True)
        self.assertEqual(level, "weak")

    def test_chair_accessory_is_not_saved(self) -> None:
        candidate = policy_candidate(
            title="Газлифт для офисного кресла",
            product_quality_level="bad",
        )
        self.assertFalse(should_save_for_admin(candidate))

    def test_telegram_normal_counter_only_good_ok(self) -> None:
        summary = summarize_saved_statuses([
            policy_candidate(verify_status="VERIFIED_GOOD"),
            policy_candidate(verify_status="VERIFIED_OK"),
            policy_candidate(verify_status="NEED_MANUAL_CHECK"),
            policy_candidate(verify_status="PRICE_MISSING", price=None),
        ])
        self.assertEqual(summary["VERIFIED_GOOD"] + summary["VERIFIED_OK"], 2)
        self.assertEqual(summary["NEED_MANUAL_CHECK"], 1)
        self.assertEqual(summary["PRICE_MISSING"], 1)

    def test_telegram_chunks_split_unbroken_text(self) -> None:
        chunks = _telegram_chunks("A" * 9000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3900 for chunk in chunks))

    def test_admin_card_is_compact_and_html_safe(self) -> None:
        result = SearchResult(
            id=1, request_id=1, title="AOC <Q27> & test", price=34_990,
            source="direct_retail", score=91, status="CANDIDATE",
            risk_flags=json.dumps(["один", "два", "три", "четыре"], ensure_ascii=False),
            facts_json=json.dumps({"verify_status": "VERIFIED_GOOD"}, ensure_ascii=False),
        )
        card = format_search_result_card(result, 1)
        self.assertIn("&lt;Q27&gt;", card)
        self.assertNotIn("четыре", card)
        self.assertLess(len(card), 1000)

    def test_admin_detail_has_quality_diagnostics(self) -> None:
        facts = {
            "verify_status": "VERIFIED_GOOD", "category": "monitor", "diagonal": "27",
            "score_breakdown": {"relevance": 15, "model_match": 10},
            "exact_match": "GENERIC_MATCH", "price_confidence": "high",
            "price_evidence": "structured_page", "source_confidence": "medium",
            "verification_confidence": "high", "score_cap_reasons": ["manual_check"],
            "fact_evidence": {"diagonal": {"confidence": "high", "evidence": "title"}},
        }
        result = SearchResult(id=1, request_id=1, facts_json=json.dumps(facts, ensure_ascii=False))
        detail = "\n".join(_facts_detail_lines(result))
        self.assertIn("Score breakdown", detail)
        self.assertIn("Exact match", detail)
        self.assertIn("Facts evidence", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
