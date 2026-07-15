"""Deterministic tests for the bounded Search V2 page-verifier bridge."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:ci_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.search_v2.models import (  # noqa: E402
    AvailabilityStatus,
    ExactMatchResult,
    ProductCondition,
    RawOffer,
    SearchRequestV2,
    VerificationAccess,
)
from app.search_v2.normalization import normalize_raw_offer  # noqa: E402
from app.search_v2.page_verifier import verify_offer_page  # noqa: E402


class SearchV2PageVerifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.request = SearchRequestV2(
            original_query="ноутбук Ryzen 5 16/512 до 60к",
            category="laptop",
            canonical_model="ноутбук Ryzen 5",
            required_specs={"ram_gb": 16, "ssd_gb": 512},
            budget=60_000,
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        self.offer = normalize_raw_offer(
            RawOffer(
                source="ozon",
                platform="Ozon",
                title="Ноутбук ASUS Ryzen 5 RAM 16 ГБ SSD 512 ГБ",
                url="https://www.ozon.ru/product/asus-123456789/",
                product_id="123456789",
                price=None,
                condition=ProductCondition.NEW,
                raw_metadata={"body": "Ноутбук ASUS Ryzen 5 RAM 16 ГБ SSD 512 ГБ"},
            ),
            self.request,
        )

    async def test_verified_page_updates_price_availability_and_final_state(self) -> None:
        def fake_verifier(candidate, request):
            self.assertEqual(request.budget, "60000")
            self.assertEqual(candidate.source, "ozon_search")
            candidate.price = 54_990
            candidate.availability = "AVAILABLE"
            candidate.verify_status = "VERIFIED_GOOD"
            return SimpleNamespace(
                verify_status="VERIFIED_GOOD",
                price=54_990,
                title=candidate.title,
                availability="AVAILABLE",
                facts={"ram_gb": 16, "ssd_gb": 512},
                html_loaded=True,
                reason="",
            )

        verified = await verify_offer_page(self.offer, self.request, verifier=fake_verifier)

        self.assertEqual(verified.price, 54_990)
        self.assertEqual(verified.availability.status, AvailabilityStatus.IN_STOCK)
        self.assertEqual(verified.verification_access, VerificationAccess.FULL)
        self.assertEqual(verified.exact_match, ExactMatchResult.EXACT)
        self.assertTrue(verified.automatic_verification.price_verified)
        self.assertTrue(verified.automatic_verification.link_verified)
        self.assertTrue(verified.automatic_verification.availability_verified)

    async def test_blocked_page_stays_manual_without_downgrading_model(self) -> None:
        def fake_verifier(candidate, request):
            candidate.verify_status = "VERIFY_BLOCKED"
            candidate.risk_flags = ["страница заблокировала проверку"]
            return SimpleNamespace(
                verify_status="VERIFY_BLOCKED",
                price=None,
                title=candidate.title,
                availability="UNKNOWN",
                facts={},
                html_loaded=False,
                reason="страница заблокировала проверку",
            )

        verified = await verify_offer_page(self.offer, self.request, verifier=fake_verifier)

        self.assertIsNone(verified.price)
        self.assertEqual(verified.verification_access, VerificationAccess.BLOCKED)
        self.assertEqual(verified.exact_match, ExactMatchResult.EXACT)
        self.assertFalse(verified.final_verification.price_verified)
        self.assertTrue(verified.final_verification.metadata["automatic_errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
