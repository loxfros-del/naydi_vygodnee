"""Offline checks for the admin verification checklist and role gate."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.db import SearchResult  # noqa: E402
from app.handlers.admin import _require_final_role_ready  # noqa: E402
from app.keyboards import kb_admin_product, kb_alice_product  # noqa: E402


def ready_facts() -> str:
    return json.dumps({
        "exact_match": "EXACT",
        "exact_product_verified": True,
        "product_page_verified": True,
        "price_verified": True,
        "availability_verified": True,
        "seller_verified": True,
        "available": True,
    }, ensure_ascii=False)


class FakeCallback:
    def __init__(self) -> None:
        self.answers: list[tuple[str, bool]] = []
        self.from_user = SimpleNamespace(id=1)

    async def answer(self, text: str, *, show_alert: bool = False):
        self.answers.append((text, show_alert))


class AdminChecklistTests(unittest.IsolatedAsyncioTestCase):
    def test_auto_product_keyboard_has_all_five_decisions(self) -> None:
        callbacks = {
            button.callback_data
            for row in kb_admin_product(7, "CANDIDATE").inline_keyboard
            for button in row
        }
        self.assertTrue({
            "manualmodel_7", "manuallink_7", "manualprice_7", "manualavailable_7",
            "manualsellerok_7", "manualsellercheck_7",
        }.issubset(callbacks))

    def test_alice_keyboard_has_complete_checklist(self) -> None:
        callbacks = {
            button.callback_data
            for row in kb_alice_product(9, "CANDIDATE").inline_keyboard
            for button in row
        }
        self.assertTrue({
            "manualmodel_9", "alicechecklink_9", "manualprice_9", "manualavailable_9",
            "manualsellerok_9", "manualsellercheck_9",
        }.issubset(callbacks))

    async def test_role_gate_blocks_incomplete_facts(self) -> None:
        callback = FakeCallback()
        item = SearchResult(id=1, request_id=1, title="Phone", facts_json="{}")
        self.assertFalse(await _require_final_role_ready(callback, item))
        self.assertEqual(len(callback.answers), 1)
        self.assertTrue(callback.answers[0][1])
        self.assertIn("checklist", callback.answers[0][0])

    async def test_role_gate_accepts_final_automatic_state(self) -> None:
        callback = FakeCallback()
        item = SearchResult(id=1, request_id=1, title="Phone", facts_json=ready_facts())
        self.assertTrue(await _require_final_role_ready(callback, item))
        self.assertEqual(callback.answers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
