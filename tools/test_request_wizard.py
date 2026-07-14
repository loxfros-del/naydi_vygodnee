"""Pure request-wizard tests without Telegram or network."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.services.request_wizard import CATEGORY_QUESTIONS, RequestWizard, parse_budget  # noqa: E402
from app.ui_formatters import format_request_summary  # noqa: E402


class RequestWizardTests(unittest.TestCase):
    def _complete(self, category="smartphones"):
        wizard = RequestWizard()
        answers = {
            "category": category, "product": "Samsung <S24>", "budget": "80к",
            "city": "Москва", "condition": "new", "requirements": "гарантия & NFC",
            "priority": "balance",
        }
        while not wizard.is_complete:
            question = wizard.current_question
            value = answers.get(question.key, "не важно")
            result = wizard.answer(value)
            self.assertTrue(result, result.error)
        return wizard

    def test_happy_path_and_category_questions(self):
        wizard = self._complete()
        payload = wizard.to_request_payload()
        self.assertEqual(payload["request_mode"], "AUTO")
        self.assertEqual(payload["budget"], "80000")
        self.assertIn("phone_memory", {q.key for q in CATEGORY_QUESTIONS["smartphones"]})
        summary = format_request_summary(payload)
        self.assertIn("Samsung &lt;S24&gt;", summary)
        self.assertIn("гарантия &amp; NFC", summary)

    def test_all_six_categories_have_specific_questions(self):
        self.assertEqual(len(CATEGORY_QUESTIONS), 6)
        self.assertTrue(all(CATEGORY_QUESTIONS.values()))

    def test_invalid_budget_back_edit_cancel(self):
        wizard = RequestWizard()
        self.assertTrue(wizard.answer("laptops"))
        self.assertTrue(wizard.answer("Ноутбук для работы"))
        invalid = wizard.answer("мало")
        self.assertFalse(invalid)
        self.assertIn("бюджет", invalid.error.lower())
        self.assertTrue(wizard.answer("100 000"))
        self.assertTrue(wizard.back())
        self.assertEqual(wizard.current_question.key, "budget")
        self.assertTrue(wizard.edit("product"))
        wizard.cancel()
        self.assertFalse(wizard.answer("другое"))

    def test_manual_mode_and_budget_parser(self):
        wizard = self._complete("manual")
        self.assertEqual(wizard.to_request_payload()["request_mode"], "MANUAL")
        self.assertEqual(parse_budget("45к"), 45000)
        self.assertIsNone(parse_budget("сто рублей"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
