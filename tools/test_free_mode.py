"""Regression checks for the free public launch policy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.keyboards import kb_admin_request, kb_ready_for_payment
from app.product_config import get_service_packages, is_free_search_mode


def _buttons(markup):
    return [
        button
        for row in markup.inline_keyboard
        for button in row
    ]


class FreeModeTests(unittest.TestCase):
    def test_public_launch_is_free_even_if_old_prices_exist(self) -> None:
        packages = get_service_packages({"QUICK_SELECTION_PRICE_RUB": "499"})

        self.assertTrue(is_free_search_mode())
        self.assertTrue(all(item.price_rub == 0 for item in packages))
        self.assertTrue(all(item.price_label == "Бесплатно" for item in packages))

    def test_admin_actions_do_not_offer_payment_for_new_selection(self) -> None:
        buttons = _buttons(kb_admin_request(17, "ADMIN_REVIEW"))
        texts = [button.text for button in buttons]
        callbacks = [button.callback_data for button in buttons]

        self.assertIn("✅ Утвердить и подготовить отчёт", texts)
        self.assertNotIn("💳 Запросить оплату", texts)
        self.assertIn("readytopay_17", callbacks)

    def test_weak_selection_has_no_force_send_action(self) -> None:
        weak_callbacks = [button.callback_data for button in _buttons(kb_ready_for_payment(17, 74))]
        ready_buttons = _buttons(kb_ready_for_payment(17, 75))

        self.assertNotIn("forcepreview_17", weak_callbacks)
        self.assertIn("readyforpay_17", [button.callback_data for button in ready_buttons])
        self.assertIn(
            "✅ Утвердить и подготовить бесплатный отчёт",
            [button.text for button in ready_buttons],
        )


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print("test_free_mode: OK")
