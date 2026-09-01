"""Admin counters, AI integrity/approval and client report safety."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app import db  # noqa: E402
from app.ai_cards_service import generate_ai_cards_from_candidates, parse_ai_cards_for_candidates  # noqa: E402
from app.handlers.admin import admin_queue_counts  # noqa: E402
from app.keyboards import kb_admin_request  # noqa: E402
from app.link_checks import LinkCheckStatus  # noqa: E402
from app.report_builder import build_full_report  # noqa: E402
from app.services.ai_review import AdminReviewService, AIReviewTransitionError  # noqa: E402
from app.services.recommendations import RecommendationService  # noqa: E402
from app.services.product_services import SearchOrchestrationService  # noqa: E402
from app.product_config import SearchMode  # noqa: E402


class ProductAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.settings.DB_PATH = str(Path(self.tmp.name) / "admin.db")
        db.init_db()
    def tearDown(self): self.tmp.cleanup()

    def _candidate(self, idx=1, status="BEST"):
        facts = json.dumps({
            "verify_status": "VERIFIED_GOOD", "exact_match": "EXACT",
            "exact_product_verified": True, "product_page_verified": True,
            "price_verified": True, "availability_verified": True,
            "seller_verified": True, "available": True, "price_confidence": "high",
        }, ensure_ascii=False)
        return db.SearchResult(
            id=idx, request_id=1, title="Samsung S24", price=70000, source="DNS",
            url="https://www.dns-shop.ru/product/samsung-s24", snippet="Подходит по памяти",
            risk_flags=json.dumps(["проверить гарантию"], ensure_ascii=False), status=status,
            facts_json=facts,
        )

    def test_admin_counts_and_safe_actions(self):
        requests = [db.Request(id=i, user_id=i, status=status) for i, status in enumerate(
            ["NEW", "SEARCHING", "ADMIN_REVIEW", "READY", "WAITING_PAYMENT", "DELIVERED", "FAILED"], 1
        )]
        counts = admin_queue_counts(requests)
        self.assertEqual(counts["new"], 1)
        self.assertEqual(counts["review"], 1)
        callbacks = [button.callback_data for row in kb_admin_request(1, "PAID").inline_keyboard for button in row]
        self.assertIn("markready_1", callbacks)
        self.assertNotIn("sendwithoutpay_1", callbacks)
        self.assertNotIn("debugsearch_1", callbacks)
        self.assertIs(SearchOrchestrationService.route(db.Request(id=99, user_id=1, product_name="автомобиль")), SearchMode.MANUAL)
        self.assertIs(SearchOrchestrationService.route(db.Request(id=100, user_id=1, product_name="ноутбук")), SearchMode.AUTO)

    def test_ai_hallucinations_are_ignored_and_fallback_is_text(self):
        req = db.Request(id=1, user_id=1, product_name="Смартфон", budget="80000")
        candidate = self._candidate()
        raw = json.dumps({"cards": [{
            "candidate_id": 1, "title": "Fake", "price": 1, "url": "https://evil.test",
            "why": "Хороший баланс", "risks": ["риск"], "manual_check": ["наличие"],
        }]}, ensure_ascii=False)
        card = parse_ai_cards_for_candidates(raw, req, [candidate])[0]
        self.assertEqual(card["name"], candidate.title)
        self.assertEqual(card["price_num"], candidate.price)
        self.assertEqual(card["link"], candidate.url)
        result = generate_ai_cards_from_candidates(req, [candidate])
        self.assertTrue(result["success"])
        self.assertTrue(result["fallback"])
        self.assertEqual(result["cards"][0]["ai_card_status"], "DRAFT")

    def test_unapproved_hidden_then_approved_visible(self):
        req_id = db.create_request(user_id=1, product_name="Смартфон", budget="80000")
        db.replace_alice_results(req_id, [{
            "candidate_id": 10, "name": "Samsung S24", "price_num": 70000,
            "store": "DNS", "link": "https://www.dns-shop.ru/product/samsung-s24",
            "pluses": ["Баланс"], "risks": [], "role": "BEST", "ai_card_status": "GENERATED",
            "facts": json.loads(self._candidate().facts_json),
        }])
        card = db.get_alice_results(req_id)[0]
        self.assertEqual(RecommendationService().for_request(req_id), [])
        AdminReviewService().approve(card.id)
        self.assertEqual(len(RecommendationService().for_request(req_id)), 1)

    def test_approval_is_blocked_until_final_checklist(self):
        req_id = db.create_request(user_id=1, product_name="Смартфон", budget="80000")
        card_id = db.create_search_result(
            request_id=req_id, title="Samsung S24", price=70000, source="DNS",
            url="https://www.dns-shop.ru/product/samsung-s24", status="BEST",
            origin="alice", ai_card_status="GENERATED", facts_json="{}",
        )
        with self.assertRaises(AIReviewTransitionError):
            AdminReviewService().approve(card_id)

    def test_client_report_has_max_three_and_no_diagnostics(self):
        req_id = db.create_request(user_id=1, product_name="Телевизор", budget="50000")
        roles = [("BEST", "DNS", "https://www.dns-shop.ru/product/a"),
                 ("BUDGET", "Ozon", "https://www.ozon.ru/product/b"),
                 ("BACKUP", "М.Видео", "https://www.mvideo.ru/products/c"),
                 ("APPROVED", "DNS", "https://www.dns-shop.ru/product/d")]
        for index, (role, source, url) in enumerate(roles, 1):
            db.create_search_result(
                request_id=req_id, title=f"Model {index}", price=30000 + index,
                source=source, url=url, snippet="Причина", status=role, origin="alice",
                price_verified=True, link_check_status=LinkCheckStatus.VERIFIED.value,
                ai_card_status="APPROVED", risk_flags="[]", score=99,
                facts_json=self._candidate(index).facts_json,
            )
        report = build_full_report(db.get_request(req_id))
        self.assertLessEqual(report.count("Почему рекомендуем:"), 3)
        self.assertNotIn("score", report.lower())
        self.assertNotIn("verify_status", report.lower())
        self.assertNotIn("yandex_market_direct", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
