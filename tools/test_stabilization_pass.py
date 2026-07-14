"""Offline regressions for the 2026-07-13 stabilization pass."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app import db  # noqa: E402
from app.ai_cards_service import (  # noqa: E402
    assign_candidate_roles,
    build_fallback_ai_cards,
    is_ai_card_candidate_eligible,
)
from app.candidate_verifier import VERIFIED_OK, VerifiedCandidate  # noqa: E402
from app.db import Request, SearchResult  # noqa: E402
from app.exact_match import (  # noqa: E402
    COMPATIBLE_VARIANT,
    MODEL_MISMATCH,
    REQUIRED_SPEC_MISMATCH,
    match_candidate,
)
from app.handlers.admin import (  # noqa: E402
    _facts_detail_lines,
    edit_price_process,
    format_alice_card,
    parse_admin_price,
)
from app.request_parser import build_request_search_query, normalize_request_data  # noqa: E402
from app.search_evidence import assess_source_trust  # noqa: E402
from app.search_policy import normalize_for_admin_save, should_save_for_admin  # noqa: E402
from app.services.recommendations import RecommendationService  # noqa: E402
from app.services.request_wizard import RequestWizard  # noqa: E402
from app.states import AdminStates  # noqa: E402
from app.ui_formatters import format_client_card, source_display_name  # noqa: E402


def facts(**overrides) -> str:
    value = {
        "verify_status": "VERIFIED_GOOD",
        "exact_match": "EXACT",
        "exact_product_verified": True,
        "product_page_verified": True,
        "price_verified": True,
        "availability_verified": True,
        "seller_verified": True,
        "available": True,
        "platform_trust": "HIGH",
        "seller_trust": "UNKNOWN",
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False)


def card(
    idx: int,
    *,
    title: str = "Apple iPhone 16 Pro 256 ГБ",
    price: int = 79_990,
    source: str = "ozon_search",
    url: str = "https://www.ozon.ru/product/iphone-16-pro-1/",
    score: float = 90,
    status: str = "CANDIDATE",
    facts_json: str | None = None,
) -> SearchResult:
    return SearchResult(
        id=idx, request_id=1, title=title, price=price, source=source, url=url,
        score=score, status=status, origin="auto", facts_json=facts_json or facts(),
        snippet="Успейте купить по акции — хит продаж", risk_flags="[]",
    )


class FakeState:
    def __init__(self, result_id: int):
        self.data = {"editprice_result_id": result_id, "preserved": "yes"}
        self.state = AdminStates.editing_price.state

    async def get_data(self): return dict(self.data)
    async def get_state(self): return self.state
    async def set_state(self, value): self.state = value
    async def update_data(self, **kwargs): self.data.update(kwargs)


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = SimpleNamespace(id=1)
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs):
        self.answers.append(text)


class StructuredRequestTests(unittest.TestCase):
    def _iphone_wizard(self) -> dict:
        wizard = RequestWizard()
        answers = {
            "category": "smartphones", "product": "iPhone 16 Pro", "budget": "80000",
            "city": "Ярославль", "condition": "new", "phone_memory": "256",
            "phone_color": "не важно", "phone_sim_region": "не важно",
            "requirements": "не важно", "priority": "balance",
        }
        while not wizard.is_complete:
            result = wizard.answer(answers.get(wizard.current_question.key, "не важно"))
            self.assertTrue(result, result.error)
        return wizard.to_request_payload()

    def test_structured_iphone_snapshot_and_clean_query(self):
        payload = self._iphone_wizard()
        self.assertEqual(payload["brand"], "Apple")
        self.assertEqual(payload["model"], "iPhone 16")
        self.assertEqual(payload["model_modifiers"], ["pro"])
        self.assertEqual(payload["storage_gb"], 256)
        self.assertEqual(payload["clean_search_query"], "iPhone 16 Pro 256 ГБ до 80000 Ярославль")
        self.assertNotIn("Какой объём", payload["clean_search_query"])
        self.assertNotIn("Какой объём", payload["important_criteria"])

    def test_legacy_question_requirement_is_normalized_without_db_write(self):
        request = Request(
            id=1, user_id=1, product_name="iPhone 16 Pro", original_query="iPhone 16 Pro",
            budget="80000", city="Ярославль", important_criteria="Какой объём памяти нужен: 256",
        )
        normalized = normalize_request_data(request)
        self.assertEqual(normalized["storage_gb"], 256)
        query = build_request_search_query(request)
        self.assertEqual(query, "iPhone 16 Pro 256 ГБ до 80000 Ярославль")
        self.assertNotIn("нужен", query.lower())


class ExactAndPolicyTests(unittest.TestCase):
    def setUp(self):
        self.request = Request(
            id=1, user_id=1, product_name="iPhone 16 Pro", original_query="iPhone 16 Pro",
            budget="80000", city="Ярославль",
            requirements_json=json.dumps({
                "product_name": "iPhone 16 Pro", "brand": "Apple", "model": "iPhone 16",
                "model_modifiers": ["pro"], "storage_gb": 256,
                "required_criteria": {"storage_gb": 256}, "condition": "new",
            }, ensure_ascii=False),
        )

    def test_base_iphone_and_wrong_storage_are_hard_mismatches(self):
        base = match_candidate(self.request, {"title": "Apple iPhone 16 256 ГБ"})
        storage = match_candidate(self.request, {"title": "Apple iPhone 16 Pro 128 ГБ"})
        self.assertEqual(base.status, MODEL_MISMATCH)
        self.assertEqual(storage.status, REQUIRED_SPEC_MISMATCH)

    def test_unspecified_color_is_compatible(self):
        request = {"original_query": "iPhone 16 Pro", "product_name": "iPhone 16 Pro"}
        result = match_candidate(request, {"title": "Apple iPhone 16 Pro 256 ГБ синий"})
        self.assertEqual(result.status, COMPATIBLE_VARIANT)

    def test_wrong_model_is_drop_and_never_normal(self):
        candidate = normalize_for_admin_save({
            "title": "Apple iPhone 16 128 ГБ", "url": "https://www.ozon.ru/product/1/",
            "price": 45_000, "status": "VERIFIED_GOOD", "verify_status": "VERIFIED_GOOD",
            "product_quality_level": "good", "exact_match_status": MODEL_MISMATCH,
            "exact_match_reason": "модель: нужна iPhone 16 Pro, найдена iPhone 16",
            "reasons": ["точная модель"],
        })
        self.assertEqual(candidate["verify_status"], "WRONG_PRODUCT")
        self.assertFalse(should_save_for_admin(candidate))
        self.assertNotIn("точная модель", " ".join(candidate["reasons"]).lower())

    def test_strong_exact_candidate_loses_stale_weak_reason(self):
        candidate = normalize_for_admin_save({
            "title": "Apple iPhone 16 Pro 256 ГБ", "url": "https://www.ozon.ru/product/1/",
            "price": 79_990, "price_source": "structured_page", "price_confidence": "high",
            "status": "VERIFIED_GOOD", "verify_status": "VERIFIED_GOOD",
            "product_quality_level": "weak", "budget_status": "IN_BUDGET", "available": True,
            "product_card_confidence": "high", "verification_confidence": "high",
            "exact_match_status": "EXACT", "exact_match_reason": "точная модель",
            "reasons": ["точная модель", "нет признаков конкретной модели", "", "точная модель"],
        })
        self.assertEqual(candidate["verify_status"], "VERIFIED_GOOD")
        reasons = " | ".join(candidate["reasons"]).lower()
        self.assertNotIn("нет признаков", reasons)
        self.assertEqual(sum("точная модель" in item.lower() for item in candidate["reasons"]), 1)


class TrustAndMarketplaceTests(unittest.TestCase):
    def test_ozon_block_does_not_lower_platform_or_invent_seller(self):
        item = {
            "title": "Apple iPhone 16 Pro 256 ГБ", "url": "https://www.ozon.ru/product/1/",
            "source": "ozon_search", "price": 79_990,
            "facts": {"verification_access": "BLOCKED", "exact_match": "EXACT"},
        }
        trust = assess_source_trust(item, "VERIFY_BLOCKED")
        self.assertEqual(trust.platform_trust, "HIGH")
        self.assertEqual(trust.platform_type, "MARKETPLACE")
        self.assertEqual(trust.seller_trust, "UNKNOWN")
        self.assertEqual(trust.verification_access, "BLOCKED")
        self.assertEqual(trust.source_confidence, "high")

    def test_known_retailers_keep_high_platform_and_seller_trust(self):
        cases = (
            ("dns_search", "https://www.dns-shop.ru/product/1/", "DNS"),
            ("citilink_search", "https://www.citilink.ru/product/1/", "Ситилинк"),
            ("mvideo_search", "https://www.mvideo.ru/products/1", "М.Видео"),
        )
        for source, url, name in cases:
            with self.subTest(source=source):
                trust = assess_source_trust({"title": "Товар Model 1", "source": source, "url": url, "price": 10_000}, "VERIFIED_OK")
                self.assertEqual((trust.platform_name, trust.platform_trust), (name, "HIGH"))
                self.assertEqual(trust.seller_trust, "HIGH")

    def test_marketplace_seller_rating_is_separate_from_platform(self):
        trust = assess_source_trust({
            "title": "Товар Model 1", "source": "yandex_market_search",
            "url": "https://market.yandex.ru/product--model/1", "price": 10_000,
            "seller": "Seller", "rating": 4.8, "reviews_count": 200,
        }, "VERIFIED_OK")
        self.assertEqual(trust.platform_trust, "HIGH")
        self.assertEqual(trust.seller_trust, "HIGH")

    def test_avito_is_classified_and_professional_seller_can_be_medium(self):
        trust = assess_source_trust({
            "title": "Новый iPhone 16 Pro 256 ГБ", "source": "avito_search",
            "url": "https://www.avito.ru/yaroslavl/telefony/iphone_123456",
            "price": 65_000, "facts": {"seller_type": "shop", "seller": "Магазин"},
        }, "VERIFIED_OK")
        self.assertEqual(trust.platform_type, "CLASSIFIED")
        self.assertEqual(trust.platform_trust, "CLASSIFIED")
        self.assertEqual(trust.seller_trust, "MEDIUM")

    def test_exact_new_avito_offer_is_saved_but_wrong_model_is_not(self):
        good = normalize_for_admin_save({
            "title": "Новый iPhone 16 Pro 256 ГБ", "url": "https://www.avito.ru/yaroslavl/telefony/iphone_123456",
            "source": "avito_search", "price": 65_000, "status": "VERIFIED_OK", "verify_status": "VERIFIED_OK",
            "product_quality_level": "good", "exact_match_status": "EXACT", "available": True,
        })
        wrong = normalize_for_admin_save({**good, "exact_match_status": MODEL_MISMATCH, "exact_match_reason": "другая модель"})
        self.assertTrue(should_save_for_admin(good))
        self.assertFalse(should_save_for_admin(wrong))

    def test_used_and_new_are_not_compatible(self):
        result = match_candidate(
            {"original_query": "новый iPhone 16 Pro 256 ГБ", "condition": "new"},
            {"title": "iPhone 16 Pro 256 ГБ б/у"},
        )
        self.assertEqual(result.status, REQUIRED_SPEC_MISMATCH)


class PriceFSMTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.settings.DB_PATH
        db.settings.DB_PATH = str(Path(self.tmp.name) / "fsm.db")
        db.init_db()
        self.req_id = db.create_request(user_id=1, product_name="iPhone 16 Pro")
        self.result_id = db.create_search_result(
            request_id=self.req_id, title="iPhone 16 Pro 256 ГБ", price=None,
            facts_json=facts(verify_status="PRICE_MISSING", price_verified=False),
        )

    def tearDown(self):
        db.settings.DB_PATH = self.old_db
        self.tmp.cleanup()

    async def test_requested_price_forms(self):
        for value in ("45000", "45 000", "45к", "45000 ₽", "45 тыс", "45 тысяч"):
            with self.subTest(value=value):
                self.assertEqual(parse_admin_price(value), 45_000)

    async def test_invalid_price_preserves_fsm(self):
        state = FakeState(self.result_id)
        message = FakeMessage("не цена")
        await edit_price_process(message, state)
        self.assertEqual(state.state, AdminStates.editing_price.state)
        self.assertEqual(state.data["editprice_result_id"], self.result_id)
        self.assertIsNone(db.get_search_result(self.result_id).price)

    async def test_valid_price_saves_manual_evidence_and_only_exits_price_state(self):
        state = FakeState(self.result_id)
        message = FakeMessage("45к")
        await edit_price_process(message, state)
        saved = db.get_search_result(self.result_id)
        saved_facts = json.loads(saved.facts_json)
        self.assertEqual(saved.price, 45_000)
        self.assertTrue(saved.price_verified)
        self.assertTrue(saved_facts["manual_verified"]["price"])
        self.assertEqual(saved_facts["price_evidence"], "manual_admin")
        self.assertEqual(saved_facts["verify_status"], "NEED_MANUAL_CHECK")
        self.assertIsNone(state.state)
        self.assertEqual(state.data["preserved"], "yes")


class AICardAndRoleTests(unittest.TestCase):
    def test_wrong_unavailable_and_unconfirmed_blocked_are_ineligible(self):
        wrong = card(1, facts_json=facts(exact_match=MODEL_MISMATCH, exact_product_verified=False))
        unavailable = card(2, facts_json=facts(verify_status="UNAVAILABLE", available=False))
        blocked = card(3, facts_json=facts(
            verify_status="VERIFY_BLOCKED", verification_access="BLOCKED",
            manual_verified={"model": True, "url": True, "price": True},
        ))
        self.assertFalse(is_ai_card_candidate_eligible(wrong))
        self.assertFalse(is_ai_card_candidate_eligible(unavailable))
        self.assertFalse(is_ai_card_candidate_eligible(blocked))

    def test_blocked_offer_needs_all_manual_confirmations(self):
        pending_audit = card(1, facts_json=facts(
            verify_status="VERIFY_BLOCKED", verification_access="BLOCKED",
            manual_verified={"model": True, "url": True, "price": True, "availability": True},
        ))
        self.assertFalse(is_ai_card_candidate_eligible(pending_audit))
        confirmed = card(2, facts_json=facts(
            verify_status="VERIFY_BLOCKED", verification_access="BLOCKED",
            manual_verified={"model": True, "url": True, "price": True, "availability": True},
            manual_verified_by="1", manual_verified_at="2026-07-13T12:00:00+00:00",
        ))
        self.assertTrue(is_ai_card_candidate_eligible(confirmed))

    def test_roles_are_unique_and_match_best_cheap_reliable(self):
        req = Request(id=1, user_id=1, budget="80000")
        marketplace = card(1, price=79_990, score=95, facts_json=facts(platform_type="MARKETPLACE"))
        retail = card(
            2, price=85_000, score=85, source="dns_search",
            url="https://www.dns-shop.ru/product/2/", facts_json=facts(platform_type="RETAIL", seller_trust="HIGH"),
        )
        avito = card(
            3, price=65_000, score=70, source="avito_search",
            url="https://www.avito.ru/yaroslavl/telefony/iphone_123456",
            facts_json=facts(platform_type="CLASSIFIED", platform_trust="CLASSIFIED"),
        )
        roles = assign_candidate_roles(req, [marketplace, retail, avito])
        self.assertEqual(roles, {1: "BEST", 3: "BUDGET", 2: "BACKUP"})
        self.assertEqual(len(set(roles.values())), len(roles))

    def test_one_offer_does_not_fill_all_roles_and_fallback_ignores_ad_snippet(self):
        req = Request(id=1, user_id=1, budget="80000")
        item = card(1)
        roles = assign_candidate_roles(req, [item])
        self.assertEqual(roles, {1: "BEST"})
        draft = build_fallback_ai_cards(req, [item])[0]
        self.assertNotIn("Успейте", draft["why"])
        self.assertNotIn("хит продаж", draft["why"])


class RenderingTests(unittest.TestCase):
    def test_client_card_is_human_readable_and_has_no_diagnostics(self):
        item = card(1, facts_json=facts(
            platform_name="Ozon", platform_type="MARKETPLACE", seller="",
            seller_verified=False, manual_seller_state="REQUIRES_CHECK",
            manual_verified_by="1", manual_verified_at="2026-07-13T12:00:00+00:00",
            verification_access="BLOCKED", blocked_reason="403 captcha",
            sim_variant="eSIM", storage_gb=256,
        ))
        item.origin = "alice"
        item.status = "BEST"
        item.ai_card_status = "APPROVED"
        item.price_verified = True
        item.link_check_status = "VERIFIED"
        item.risk_flags = json.dumps(["403 blocked", "browser verification", "проверить гарантию"], ensure_ascii=False)
        rendered = format_client_card(item, "BEST")
        self.assertIn("Площадка: Ozon", rendered)
        self.assertIn("Продавец: требует проверки", rendered)
        self.assertIn("Что подтверждено", rendered)
        self.assertIn("Что проверить", rendered)
        for forbidden in ("WEAK_CANDIDATE", "VERIFIED_GOOD", "confidence", "evidence", "403", "captcha", "browser", "непроверенный сайт"):
            self.assertNotIn(forbidden.lower(), rendered.lower())

    def test_admin_card_hides_raw_but_debug_keeps_it(self):
        item = card(1, facts_json=facts(
            platform_name="Ozon", platform_type="MARKETPLACE", verification_access="BLOCKED",
            fetch_status_code=403, blocked_reason="captcha", browser_used=True,
        ))
        item.origin = "alice"
        item.status = "BEST"
        item.ai_card_status = "DRAFT"
        item.risk_flags = json.dumps(["403 blocked", "проверить продавца"], ensure_ascii=False)
        normal = format_alice_card(item, 1)
        debug = "\n".join(_facts_detail_lines(item))
        self.assertNotIn("403", normal)
        self.assertNotIn("captcha", normal.lower())
        self.assertIn("403", debug)
        self.assertIn("captcha", debug.lower())
        self.assertIn("Automatic verification", debug)
        self.assertIn("Manual verification", debug)
        self.assertIn("Final presentation state", debug)

    def test_source_names_are_human_readable(self):
        expected = {
            "ozon_search": "Ozon", "yandex_market_direct": "Яндекс Маркет",
            "wildberries": "Wildberries", "dns_search": "DNS",
            "citilink_search": "Ситилинк", "mvideo_search": "М.Видео", "avito_search": "Авито",
        }
        for source, display in expected.items():
            with self.subTest(source=source):
                self.assertEqual(source_display_name(source), display)

    def test_recommendation_service_hides_wrong_approved_legacy_card(self):
        wrong = card(1, status="BEST", facts_json=facts(exact_match=MODEL_MISMATCH, exact_product_verified=False))
        wrong.origin = "alice"
        wrong.ai_card_status = "APPROVED"
        self.assertEqual(RecommendationService.select([wrong]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
