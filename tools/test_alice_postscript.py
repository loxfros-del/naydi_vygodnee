"""Regression tests for rejecting conversational postscript as product cards."""
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

from app.alice_service import parse_alice_response  # noqa: E402


class AlicePostscriptTests(unittest.TestCase):
    def test_conversational_postscript_is_not_a_product(self) -> None:
        text = """1. Samsung UE43AU7100U
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/product/samsung-ue43au7100u
Почему подходит: 4K, Smart TV
Риски: 60 Гц

2. LG 43UP75006LF
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: https://www.mvideo.ru/lg-43up75006lf
Почему подходит: 4K, хорошая цена
Риски: слабый звук

Надеюсь, это поможет! Если нужны уточнения — напишите."""

        items = parse_alice_response(text, budget=45_000)

        self.assertEqual([item["name"] for item in items], [
            "Samsung UE43AU7100U",
            "LG 43UP75006LF",
        ])
        self.assertTrue(all(item.get("price_num") for item in items))

    def test_text_without_product_evidence_returns_no_card(self) -> None:
        items = parse_alice_response("Надеюсь, это поможет! Если нужны уточнения — напишите.")
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
