"""Deterministic tests for the bounded Search V2 acceptance runner."""
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

from app.search_v2.models import ProductCondition  # noqa: E402
from tools.search_v2_acceptance import _aggregate, request_from_case  # noqa: E402


class SearchV2AcceptanceRunnerTests(unittest.TestCase):
    def test_specific_model_case_keeps_model_storage_and_condition(self) -> None:
        request = request_from_case({
            "id": "phone",
            "query": "iPhone 16 Pro 256 ГБ до 80000 Ярославль новый",
            "category": "phone",
            "budget": 80_000,
            "required_specs": {
                "brand": "Apple",
                "model": "iPhone 16 Pro",
                "storage_gb": 256,
                "condition": "new",
            },
            "city": "Ярославль",
            "used_allowed": False,
        })
        self.assertEqual(request.brand, "Apple")
        self.assertEqual(request.canonical_model, "iPhone 16")
        self.assertEqual([item.casefold() for item in request.model_modifiers], ["pro"])
        self.assertEqual(request.required_specs["storage_gb"], 256)
        self.assertEqual(request.condition, ProductCondition.NEW)
        self.assertIn("pro", [item.casefold() for item in request.hard_tokens])
        self.assertIn("256 ГБ", request.hard_tokens)

    def test_generic_headphones_translate_anc_to_features(self) -> None:
        request = request_from_case({
            "id": "headphones",
            "query": "TWS наушники ANC до 12000",
            "category": "headphones",
            "budget": 12_000,
            "required_specs": {
                "form_factor": "TWS",
                "anc": True,
                "wireless": True,
                "condition": "new",
            },
            "city": "Ярославль",
            "used_allowed": False,
        })
        self.assertEqual(request.canonical_model, "наушники")
        self.assertEqual(request.required_specs["features"], ["ANC", "TWS", "wireless"])
        self.assertIn("ANC", request.hard_tokens)
        self.assertIn("TWS", request.hard_tokens)
        self.assertIn("wireless", request.hard_tokens)

    def test_used_allowed_case_uses_any_condition(self) -> None:
        request = request_from_case({
            "id": "used",
            "query": "iPhone 15 128 ГБ можно б/у",
            "category": "phone",
            "budget": 55_000,
            "required_specs": {"brand": "Apple", "model": "iPhone 15", "storage_gb": 128},
            "city": "Екатеринбург",
            "used_allowed": True,
        })
        self.assertEqual(request.condition, ProductCondition.ANY)
        self.assertNotIn("new", request.hard_tokens)

    def test_aggregate_requires_all_acceptance_targets(self) -> None:
        passing = [
            {
                "errors": [],
                "recommendations": [{"role": "BEST_OVERALL"}],
                "metrics": {"top1_exact": True, "top3_useful": 1, "valid_price_rate": 1.0},
            }
            for _ in range(30)
        ]
        summary = _aggregate(passing, expected=30)
        self.assertTrue(summary["acceptance_pass"])
        self.assertEqual(summary["top1_exact_cases"], 30)
        self.assertEqual(summary["top3_useful_cases"], 30)

        failing = list(passing)
        failing[0] = {"errors": ["boom"], "recommendations": [], "metrics": {}}
        summary = _aggregate(failing, expected=30)
        self.assertFalse(summary["acceptance_pass"])
        self.assertEqual(summary["error_cases"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
