"""Offline regressions for honest, understandable purchase cards."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ui_formatters import format_client_card, format_client_result_summary
from app.services.request_wizard import RequestWizard


class SelectionPresentationTests(unittest.TestCase):
    def card(self, **overrides):
        values = dict(title="Товар <пример>", price=30000, source="avito", snippet="",
                      facts_json=json.dumps({"platform_type": "CLASSIFIED"}),
                      admin_note="{}", risk_flags="[]")
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_old_snippet_cannot_claim_a_discount_without_evidence(self):
        for claim in ("Ниже рынка на 30%", "Экономия 10 000 ₽", "Дешевле остальных", "Выгодная покупка"):
            with self.subTest(claim=claim):
                result = format_client_card(self.card(snippet=claim), "BEST")
                self.assertNotIn(claim, result)
                self.assertIn("доказательства экономии в этой карточке не приложены", result)

    def test_compatible_variant_is_not_reported_as_exact(self):
        result = format_client_card(self.card(facts_json=json.dumps({"exact_match": "COMPATIBLE_VARIANT"})), "BEST")
        self.assertNotIn("точная модель и обязательные характеристики совпадают", result)
        self.assertIn("комплектацию нужно уточнить", result)

    def test_platform_alone_is_not_a_price_advantage(self):
        result = format_client_card(self.card(), "BACKUP")
        self.assertNotIn("ниже розничных", result)
        self.assertNotIn("Самый надёжный", result)
        self.assertIn("Запасной вариант", result)
        self.assertIn("состояние требует отдельной проверки", result)
        self.assertIn("Товар &lt;пример&gt;", result)

    def test_exact_match_and_relevant_explanation_remain_visible(self):
        result = format_client_card(self.card(snippet="Есть нужный разъём HDMI", facts_json=json.dumps({"exact_match": "EXACT"})), "BEST")
        self.assertIn("Есть нужный разъём HDMI", result)
        self.assertIn("точная модель и обязательные характеристики совпадают", result)

    def test_unknown_check_date_is_not_replaced_with_today_or_edit_date(self):
        for checked in ("", "invalid"):
            result = format_client_result_summary(SimpleNamespace(product="Телефон"), [self.card(updated_at="2026-09-13T12:00:00", checked_at=checked)])
            self.assertIn("Дата проверки: не указана", result)

    def test_real_check_timestamp_remains_visible(self):
        result = format_client_result_summary(SimpleNamespace(product="Телефон"), [self.card()], checked_at="2026-09-12T15:30:00")
        self.assertIn("12.09.2026 15:30", result)

    def test_unknown_optional_detail_does_not_become_a_search_requirement(self):
        wizard = RequestWizard()
        known = {"category": "smartphones", "product": "Samsung S24", "budget": "80000", "city": "Москва", "condition": "new", "priority": "balance"}
        while not wizard.is_complete:
            key = wizard.current_question.key
            response = wizard.answer(known.get(key, "не знаю"))
            self.assertTrue(response, response.error)
        self.assertNotIn("не знаю", json.dumps(wizard.to_request_payload(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
