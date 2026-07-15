from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.presentation import format_client_recommendation, format_client_recommendations


class SearchV2PresentationTests(unittest.TestCase):
    def offer(self, offer_id: str = "o1", **extra):
        value = {
            "offer_id": offer_id,
            "title": "Apple iPhone 16 Pro 256 ГБ",
            "price": 79_990,
            "currency": "RUB",
            "platform": "Ozon",
            "seller": {"name": "Example Store"},
            "url": f"https://example.test/{offer_id}",
            "manual_verification": {
                "model_verified": True,
                "link_verified": True,
                "price_verified": True,
                "availability_verified": True,
                "verified_at": "13.07.2026 20:30",
            },
        }
        value.update(extra)
        return value

    def test_best_card_contains_product_facts(self) -> None:
        text = format_client_recommendation({
            "role": "BEST_OVERALL",
            "offer": self.offer(),
            "reasons": ["точная модель и память", "цена ниже медианы"],
        })
        self.assertIn("⭐ Лучший выбор", text)
        self.assertIn("iPhone 16 Pro 256", text)
        self.assertIn("79 990 ₽", text)
        self.assertIn("Ozon · Example Store", text)
        self.assertIn("Почему выбрали", text)
        self.assertIn("точная модель и память", text)
        self.assertIn("Проверено специалистом", text)

    def test_technical_diagnostics_never_reach_client(self) -> None:
        text = format_client_recommendation({
            "role": "CHEAP_WITH_RISK",
            "offer": self.offer(),
            "risk_flags": [
                {"code": "BLOCKED_ACCESS", "title": "403 captcha browser provider"},
                {"code": "UNKNOWN_SELLER", "title": "Проверьте рейтинг продавца"},
            ],
        })
        self.assertIn("Дешевле", text)
        self.assertIn("Проверьте рейтинг продавца", text)
        self.assertNotIn("403", text)
        self.assertNotIn("captcha", text)
        self.assertNotIn("browser", text)
        self.assertNotIn("provider", text)

    def test_resolved_risk_and_incomplete_manual_stamp_are_hidden(self) -> None:
        offer = self.offer(manual_verification={"model_verified": True, "verified_at": "today"})
        text = format_client_recommendation({
            "role": "RELIABLE",
            "offer": offer,
            "risk_flags": [{"title": "Старая цена", "resolved_by_manual": True}],
        })
        self.assertIn("Надёжный вариант", text)
        self.assertNotIn("Старая цена", text)
        self.assertNotIn("Проверено специалистом", text)

    def test_collection_is_unique_and_capped_at_three(self) -> None:
        rows = [
            {"role": "BEST_OVERALL", "offer": self.offer("a")},
            {"role": "CHEAP_WITH_RISK", "offer": self.offer("a")},
            {"role": "RELIABLE", "offer": self.offer("b")},
            {"role": "CHEAP_WITH_RISK", "offer": self.offer("c")},
            {"role": "RELIABLE", "offer": self.offer("d")},
        ]
        rendered = format_client_recommendations(rows)
        self.assertEqual(len(rendered), 3)
        self.assertIn("a", rows[0]["offer"]["url"])
        self.assertTrue(any("Надёжный вариант" in item for item in rendered))
        self.assertTrue(any("Дешевле" in item for item in rendered))


if __name__ == "__main__":
    unittest.main()
