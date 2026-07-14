"""Link comparison, blocked/manual correction and analytics roundtrips."""
from __future__ import annotations

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
from app.services.analytics import build_product_metrics, record_feedback, track_event  # noqa: E402
from app.services.link_comparison import (  # noqa: E402
    ComparisonValidationError, prepare_comparison_links, save_comparison_links,
    validate_comparison_urls,
)


class LinkComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.settings.DB_PATH = str(Path(self.tmp.name) / "links.db")
        db.init_db()
    def tearDown(self): self.tmp.cleanup()

    def test_limits_invalid_and_duplicates(self):
        urls = validate_comparison_urls([
            "https://ozon.ru/product/1?utm_source=x",
            "https://www.ozon.ru/product/1",
            "https://market.yandex.ru/product/2",
        ])
        self.assertEqual(len(urls), 2)
        with self.assertRaises(ComparisonValidationError):
            validate_comparison_urls(["not-url", "https://ozon.ru/1"])
        with self.assertRaises(ComparisonValidationError):
            validate_comparison_urls([f"https://example.com/{i}" for i in range(11)])

    def test_blocked_page_is_manual_and_admin_can_correct(self):
        urls = ["https://ozon.ru/product/1", "https://market.yandex.ru/product/2"]
        items = prepare_comparison_links(urls, blocked_urls=[urls[0]])
        self.assertTrue(items[0].manual_check_required)
        self.assertIn("недоступна", " ".join(items[0].risks))
        req_id = db.create_request(user_id=1, product_name="Сравнение", request_mode="LINK_COMPARISON")
        saved = save_comparison_links(user_id=1, request_id=req_id, items=items)
        updated = db.update_comparison_link(saved[0].id, title="Исправленный товар", price=19990, status="MANUAL_CHECKED")
        self.assertEqual(updated.price, 19990)
        self.assertEqual(updated.title, "Исправленный товар")

    def test_feedback_and_metrics(self):
        req_id = db.create_request(user_id=5, product_name="Наушники")
        track_event("request_started", user_id=5, request_id=req_id, created_at="2026-01-01T10:00:00")
        track_event("request_completed", user_id=5, request_id=req_id)
        track_event("recommendation_ready", request_id=req_id)
        track_event("delivered", request_id=req_id, created_at="2026-01-01T10:30:00")
        record_feedback(user_id=5, request_id=req_id, rating=2, comment="Не помогло")
        metrics = build_product_metrics()
        self.assertEqual(metrics["requests_completed"], 1)
        self.assertEqual(metrics["delivered"], 1)
        self.assertEqual(metrics["average_delivery_seconds"], 1800.0)
        self.assertEqual(len(db.get_low_feedback(max_rating=2)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
