from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.manual_verification import apply_manual_verification, manual_checklist_complete
from app.search_v2.models import (
    ExactMatchResult, Offer, VerificationAccess, VerificationState,
)
from app.search_v2.presentation import format_client_recommendation
from app.search_v2.verification import apply_automatic_verification, verify_offer_pages


class SearchV2ManualVerificationTests(unittest.TestCase):
    def offer(self, *, exact: ExactMatchResult = ExactMatchResult.EXACT) -> Offer:
        return apply_automatic_verification(Offer(
            offer_id="o1",
            title="iPhone 16 Pro 256 ГБ",
            url="https://ozon.ru/product/1",
            price=79_990,
            exact_match=exact,
            price_confidence=0.95,
            verification_access=VerificationAccess.BLOCKED,
            raw_metadata={
                "verification_warnings": ["LINK_PAGE_403", "PRICE_UNVERIFIED"],
                "source_errors": ["403 captcha"],
                "product_page_verified": False,
                "availability_verified": False,
            },
        ))

    def approve(self, offer: Offer, *, seller_state: str = "VERIFIED") -> Offer:
        timestamp = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
        for field in ("model", "link", "price", "availability"):
            offer = apply_manual_verification(
                offer, field, verified_by="admin:7", verified_at=timestamp, note=f"checked {field}",
            )
        return apply_manual_verification(
            offer, "seller", verified_by="admin:7", verified_at=timestamp,
            note="seller checked", seller_state=seller_state,
        )

    def test_manual_confirmation_hides_stale_warning_but_keeps_debug_history(self) -> None:
        offer = self.approve(self.offer())
        self.assertTrue(manual_checklist_complete(offer))
        self.assertTrue(offer.final_verification.model_verified)
        self.assertTrue(offer.final_verification.link_verified)
        self.assertTrue(offer.final_verification.price_verified)
        self.assertTrue(offer.final_verification.availability_verified)
        self.assertTrue(offer.final_verification.seller_verified)
        self.assertEqual(offer.final_verification.warnings, [])
        self.assertIn("403 captcha", offer.final_verification.metadata["automatic_errors"])
        self.assertEqual(len(offer.manual_verification.metadata["history"]), 5)
        text = format_client_recommendation({"role": "BEST_OVERALL", "offer": offer, "reasons": ["точная модель"]})
        self.assertIn("Проверено специалистом", text)
        self.assertNotIn("403", text)
        self.assertNotIn("captcha", text)

    def test_seller_requires_check_is_a_complete_audited_decision_not_verified(self) -> None:
        offer = self.approve(self.offer(), seller_state="REQUIRES_CHECK")
        self.assertTrue(manual_checklist_complete(offer))
        self.assertFalse(offer.final_verification.seller_verified)
        self.assertEqual(offer.manual_verification.metadata["seller_state"], "REQUIRES_CHECK")
        self.assertEqual(offer.final_verification.metadata["final_manual_status"], "APPROVED")

    def test_price_and_link_values_are_audited_and_applied(self) -> None:
        offer = self.offer()
        offer = apply_manual_verification(offer, "price", verified_by="admin", note="page", value=77_700)
        offer = apply_manual_verification(offer, "link", verified_by="admin", note="direct", value="https://ozon.ru/product/2")
        self.assertEqual(offer.price, 77_700)
        self.assertEqual(offer.url, "https://ozon.ru/product/2")
        self.assertEqual(offer.manual_verification.metadata["confirmations"]["price"]["value"], 77_700)
        self.assertEqual(offer.manual_verification.metadata["confirmations"]["link"]["value"], "https://ozon.ru/product/2")

    def test_hard_mismatch_requires_explicit_noted_override(self) -> None:
        offer = self.offer(exact=ExactMatchResult.MODEL_MISMATCH)
        regular = apply_manual_verification(offer, "model", verified_by="admin", note="looked")
        self.assertFalse(regular.final_verification.model_verified)
        with self.assertRaises(ValueError):
            apply_manual_verification(offer, "model", verified_by="admin", explicit_override=True)
        overridden = apply_manual_verification(
            offer, "model", verified_by="admin", note="confirmed exact regional alias", explicit_override=True,
        )
        self.assertTrue(overridden.final_verification.model_verified)
        self.assertTrue(overridden.final_verification.metadata["explicit_model_override_applied"])

    def test_invalid_manual_actions_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            apply_manual_verification(self.offer(), "unknown", verified_by="admin")
        with self.assertRaises(ValueError):
            apply_manual_verification(self.offer(), "price", verified_by="")


class SearchV2PageVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_verification_is_bounded_ordered_and_isolated(self) -> None:
        offers = [Offer(offer_id=str(index), exact_match=ExactMatchResult.EXACT) for index in range(6)]
        active = 0
        maximum = 0

        async def verifier(offer: Offer):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.005)
            active -= 1
            if offer.offer_id == "3":
                raise RuntimeError("blocked")
            return {"link_verified": True, "model_verified": True}

        results = await verify_offer_pages(offers, verifier, max_concurrency=9, timeout=1)
        self.assertEqual(maximum, 2)
        self.assertEqual([item.offer_id for item in results], [str(index) for index in range(6)])
        self.assertTrue(results[0].final_verification.link_verified)
        self.assertIn("RuntimeError: blocked", results[3].automatic_verification.errors)
        self.assertFalse(any(item is None for item in results))


if __name__ == "__main__":
    unittest.main()
