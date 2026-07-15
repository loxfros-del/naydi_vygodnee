#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.verification_state import (
    SELLER_REQUIRES_CHECK,
    SELLER_VERIFIED,
    apply_manual_confirmation,
    normalize_verification_facts,
    resolve_final_presentation,
)


CHECKED_AT = "2026-07-13T18:40:00+03:00"


def confirm(
    facts: dict,
    field: str,
    *,
    value=None,
    seller_state: str | None = None,
    explicit_override: bool = False,
    note: str = "проверено",
) -> dict:
    return apply_manual_confirmation(
        facts,
        field,
        verified=seller_state != SELLER_REQUIRES_CHECK,
        verified_by="admin:42",
        verified_at=CHECKED_AT,
        note=note,
        value=value,
        seller_state=seller_state,
        explicit_override=explicit_override,
    )


def confirm_required_fields(facts: dict, *, seller_state: str = SELLER_VERIFIED) -> dict:
    for field in ("model", "link", "price", "availability"):
        facts = confirm(facts, field)
    return confirm(facts, "seller", seller_state=seller_state)


class ManualVerificationStateTests(unittest.TestCase):
    def test_fully_automatic_state_needs_no_manual_audit(self) -> None:
        final = resolve_final_presentation({
            "exact_match": "EXACT",
            "exact_product_verified": True,
            "product_page_verified": True,
            "price_verified": True,
            "availability_verified": True,
            "seller_verified": True,
        })
        self.assertEqual(final["status"], "VERIFIED")
        self.assertTrue(final["presentation_ready"])
        self.assertFalse(final["specialist_verified"])

    def test_manual_seller_decision_completes_automatic_fields(self) -> None:
        facts = {
            "exact_match": "EXACT",
            "exact_product_verified": True,
            "product_page_verified": True,
            "price_verified": True,
            "availability_verified": True,
            "seller_verified": False,
        }
        facts = confirm(facts, "seller", seller_state=SELLER_REQUIRES_CHECK)
        final = resolve_final_presentation(facts)
        self.assertEqual(final["status"], "APPROVED")
        self.assertTrue(final["presentation_ready"])
        self.assertEqual(final["seller_state"], SELLER_REQUIRES_CHECK)

    def test_legacy_aliases_are_migrated_without_losing_automatic_history(self) -> None:
        legacy = {
            "exact_match": "EXACT",
            "verify_status": "VERIFY_BLOCKED",
            "final_reasons": ["403 browser blocked"],
            "manual_verified": {
                "model": True,
                "url": True,
                "price": True,
                "availability": True,
                "seller": True,
            },
            "manual_verified_by": "admin:7",
            "manual_verified_at": CHECKED_AT,
        }

        normalised = normalize_verification_facts(json.dumps(legacy, ensure_ascii=False))

        self.assertEqual(normalised["automatic_verification"]["verify_status"], "VERIFY_BLOCKED")
        self.assertIn("403 browser blocked", normalised["automatic_verification"]["warnings"])
        self.assertTrue(normalised["manual_verification"]["manual_link_verified"])
        self.assertEqual(normalised["final_presentation_state"]["final_manual_status"], "APPROVED")
        self.assertNotIn("403 browser blocked", normalised["final_presentation_state"]["warnings"])

    def test_apply_confirmation_is_non_mutating_and_keeps_audit(self) -> None:
        original = {"exact_match": "EXACT", "price_verified": False}
        updated = confirm(original, "price", value=79_990, note="цена в карточке магазина")

        self.assertNotIn("manual_verification", original)
        manual = updated["manual_verification"]
        self.assertTrue(manual["manual_price_verified"])
        self.assertEqual(manual["manual_verified_by"], "admin:42")
        self.assertEqual(manual["manual_verified_at"], CHECKED_AT)
        self.assertEqual(manual["manual_note"], "цена в карточке магазина")
        self.assertEqual(manual["confirmations"]["price"]["value"], 79_990)
        self.assertEqual(manual["history"][-1]["field"], "price")
        self.assertFalse(updated["automatic_verification"]["price_verified"])
        self.assertEqual(updated["final_presentation_state"]["final_facts"]["price"], 79_990)

    def test_regular_manual_model_confirmation_cannot_rescue_hard_mismatch(self) -> None:
        updated = confirm(
            {"exact_match": "MODEL_MISMATCH", "verify_status": "NEED_MANUAL_CHECK"},
            "model",
        )
        final = resolve_final_presentation(updated)

        self.assertEqual(final["status"], "BLOCKED")
        self.assertIn("MODEL_MISMATCH", final["blocking_reasons"])
        self.assertFalse(final["model_verified"])
        self.assertFalse(final["model_override_applied"])

    def test_explicit_model_override_requires_note_and_is_audited(self) -> None:
        with self.assertRaises(ValueError):
            apply_manual_confirmation(
                {"exact_match": "MODEL_MISMATCH"},
                "model",
                True,
                verified_by="admin:42",
                verified_at=CHECKED_AT,
                explicit_override=True,
            )

        facts = confirm(
            {"exact_match": "MODEL_MISMATCH"},
            "model",
            explicit_override=True,
            note="карточка ошибочно классифицирована; модель сверена по SKU",
        )
        facts = confirm(facts, "link")
        facts = confirm(facts, "price")
        facts = confirm(facts, "availability")
        facts = confirm(facts, "seller", seller_state=SELLER_VERIFIED)
        final = resolve_final_presentation(facts)

        self.assertEqual(final["final_manual_status"], "APPROVED")
        self.assertTrue(final["model_override_applied"])
        self.assertTrue(facts["manual_verification"]["confirmations"]["model"]["explicit_override"])

    def test_stale_warnings_are_suppressed_only_for_confirmed_fields(self) -> None:
        facts = {
            "exact_match": "EXACT",
            "verify_status": "VERIFY_BLOCKED",
            "final_reasons": [
                "403 captcha browser blocked",
                "цена не подтверждена",
                "неизвестная гарантия",
            ],
        }
        facts = confirm(facts, "link")
        final = resolve_final_presentation(facts)

        self.assertNotIn("403 captcha browser blocked", final["warnings"])
        self.assertIn("403 captcha browser blocked", final["suppressed_automatic_warnings"])
        self.assertIn("Цену нужно подтвердить.", final["warnings"])
        self.assertIn("неизвестная гарантия", final["warnings"])

        facts = confirm(facts, "price", value=79_990)
        final = resolve_final_presentation(facts)
        self.assertNotIn("Цену нужно подтвердить.", final["warnings"])
        self.assertIn("неизвестная гарантия", final["warnings"])
        self.assertIn("цена не подтверждена", final["suppressed_automatic_warnings"])

    def test_seller_requires_check_is_an_audited_approved_decision_and_visible_risk(self) -> None:
        facts = confirm_required_fields(
            {"exact_match": "EXACT", "verify_status": "NEED_MANUAL_CHECK"},
            seller_state=SELLER_REQUIRES_CHECK,
        )
        final = resolve_final_presentation(facts)

        self.assertEqual(final["final_manual_status"], "APPROVED")
        self.assertEqual(final["seller_state"], SELLER_REQUIRES_CHECK)
        self.assertFalse(final["seller_verified"])
        self.assertTrue(final["specialist_verified"])
        self.assertIn("Продавца нужно дополнительно проверить.", final["warnings"])

    def test_all_manual_confirmations_hide_only_stale_technical_warnings(self) -> None:
        initial = {
            "exact_match": "EXACT",
            "verify_status": "VERIFY_BLOCKED",
            "warnings": ["WEAK_CANDIDATE", "ссылка не проверена", "неизвестная гарантия"],
        }
        facts = confirm_required_fields(initial)
        final = facts["final_presentation_state"]

        self.assertEqual(final["status"], "APPROVED")
        self.assertEqual(final["checked_by"], "admin:42")
        self.assertEqual(final["checked_at"], CHECKED_AT)
        self.assertNotIn("WEAK_CANDIDATE", final["warnings"])
        self.assertNotIn("ссылка не проверена", final["warnings"])
        self.assertIn("неизвестная гарантия", final["warnings"])
        self.assertIn("WEAK_CANDIDATE", facts["automatic_verification"]["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
