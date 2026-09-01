"""Canonical request-state graph and persistent journal checks."""
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
from app.services.state_machine import (  # noqa: E402
    ALLOWED_TRANSITIONS, DELIVERED, InvalidStatusTransition, READY,
    can_transition, normalize_request_status, transition_request,
)


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.settings.DB_PATH = str(Path(self.tmp.name) / "state.db")
        db.init_db()
    def tearDown(self): self.tmp.cleanup()

    def test_graph_has_25_transitions_and_delivery_only_from_ready(self):
        edges = [(source, target) for source, targets in ALLOWED_TRANSITIONS.items() for target in targets]
        self.assertGreaterEqual(len(edges), 25)
        delivered_sources = [source for source, targets in ALLOWED_TRANSITIONS.items() if DELIVERED in targets]
        self.assertEqual(delivered_sources, [READY])
        self.assertFalse(can_transition("NEW", "DELIVERED"))
        self.assertEqual(normalize_request_status("PREVIEW_SENT"), "WAITING_PAYMENT")

    def test_persistent_happy_path_and_forbidden_transition(self):
        request_id = db.create_request(user_id=1, product_name="Телевизор")
        path = ["SEARCHING", "ADMIN_REVIEW", "WAITING_PAYMENT", "PAID", "READY", "DELIVERED"]
        for status in path:
            transition_request(request_id, status, actor="test", reason="deterministic")
        self.assertEqual(db.get_request(request_id).status, "DELIVERED")
        self.assertEqual(len(db.get_request_transitions(request_id)), len(path))

        other_id = db.create_request(user_id=2, product_name="Ноутбук")
        with self.assertRaises(InvalidStatusTransition):
            transition_request(other_id, "DELIVERED")

    def test_real_active_statuses(self):
        request_id = db.create_request(user_id=9, product_name="Монитор")
        transition_request(request_id, "SEARCHING")
        self.assertEqual(db.get_user_active_request(9).id, request_id)
        transition_request(request_id, "ADMIN_REVIEW")
        transition_request(request_id, "WAITING_PAYMENT")
        self.assertEqual(db.get_user_active_request(9).id, request_id)

    def test_same_status_transition_is_idempotent(self):
        request_id = db.create_request(user_id=10, product_name="Наушники")
        transition_request(request_id, "SEARCHING", actor="test", reason="first")
        before = db.get_request_transitions(request_id)

        repeated = transition_request(
            request_id,
            "SEARCHING",
            actor="test",
            reason="retry",
            extra_fields={"admin_note": "повторный запуск"},
        )

        self.assertEqual(repeated.status, "SEARCHING")
        self.assertEqual(repeated.admin_note, "повторный запуск")
        self.assertEqual(len(db.get_request_transitions(request_id)), len(before))


if __name__ == "__main__":
    unittest.main(verbosity=2)
