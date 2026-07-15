"""Integrated manual-review to client-safe Search V2 delivery contract."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:ci_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.search_v2.manual_verification import apply_manual_verification, manual_checklist_complete  # noqa: E402
from app.search_v2.models import (  # noqa: E402
    ExactMatchResult,
    Offer,
    Recommendation,
    RecommendationRole,
    RiskFlag,
    SellerInfo,
    VerificationAccess,
    VerificationState,
)
from app.search_v2.presentation import format_client_recommendation  # noqa: E402


class SearchV2ClientDeliveryTests(unittest.TestCase):
    def test_admin_confirmation_hides_transport_noise_but_keeps_real_risk(self) -> None:
        automatic = VerificationState(
            model_verified=True,
            link_verified=False,
            price_verified=False,
            availability_verified=False,
            seller_verified=False,
            access=VerificationAccess.BLOCKED,
            errors=["403 captcha in source adapter"],
            warnings=["NEED_MANUAL_CHECK"],
        )
        offer = Offer(
            offer_id="ozon-iphone",
            source="ozon",
            platform="Ozon",
            title="Apple iPhone 16 Pro 256 ГБ",
            url="https://www.ozon.ru/product/iphone-16-pro-123456789/",
            price=None,
            seller=SellerInfo(name="Example Store"),
            exact_match=ExactMatchResult.EXACT,
            verification_access=VerificationAccess.BLOCKED,
            automatic_verification=automatic,
            final_verification=automatic,
            risk_flags=[
                RiskFlag(
                    code="BLOCKED_AUTOMATIC_ACCESS",
                    title="403 blocked automatic verification",
                    explanation="captcha provider adapter",
                ),
                RiskFlag(
                    code="UNKNOWN_WARRANTY",
                    title="Гарантия не подтверждена",
                    explanation="Уточните срок и исполнителя гарантии.",
                ),
            ],
        )
        timestamp = datetime(2026, 7, 15, 18, 40, tzinfo=timezone.utc)
        for field, value in (
            ("model", None),
            ("link", offer.url),
            ("price", 79_990),
            ("availability", None),
        ):
            offer = apply_manual_verification(
                offer,
                field,
                True,
                verified_by="admin:1",
                verified_at=timestamp,
                note=f"Подтверждено: {field}",
                value=value,
            )
        offer = apply_manual_verification(
            offer,
            "seller",
            False,
            verified_by="admin:1",
            verified_at=timestamp,
            note="Продавца проверить перед оплатой",
            seller_state="REQUIRES_CHECK",
        )

        self.assertTrue(manual_checklist_complete(offer))
        self.assertEqual(offer.final_verification.metadata["final_manual_status"], "APPROVED")
        self.assertTrue(offer.final_verification.model_verified)
        self.assertTrue(offer.final_verification.price_verified)
        self.assertFalse(offer.final_verification.seller_verified)

        recommendation = Recommendation(
            role=RecommendationRole.BEST_OVERALL,
            offer_id=offer.offer_id,
            offer=offer,
            reasons=["Точная модель и память", "Цена в бюджете"],
            risks=offer.risk_flags,
        )
        text = format_client_recommendation(recommendation)
        lowered = text.casefold()

        self.assertIn("79 990 ₽", text)
        self.assertIn("Проверено специалистом", text)
        self.assertIn("Гарантия не подтверждена", text)
        for marker in ("403", "429", "captcha", "adapter", "provider", "need_manual_check", "weak_candidate"):
            self.assertNotIn(marker, lowered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
