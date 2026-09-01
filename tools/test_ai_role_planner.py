"""Deterministic integration tests for market role planning in AI cards."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.ai_cards_service import (  # noqa: E402
    assign_candidate_roles,
    build_fallback_ai_cards,
    select_verified_ai_card_candidates,
)
from app.db import Request, SearchResult  # noqa: E402


def candidate(
    idx: int,
    *,
    source: str = "Ozon",
    url: str | None = None,
    price: int = 80_000,
    score: float = 80,
    status: str = "CANDIDATE",
    exact: str = "EXACT",
    available: bool = True,
    price_confidence: str = "high",
    price_verified: bool = True,
    platform_type: str = "MARKETPLACE",
    seller_trust: str = "UNKNOWN",
    seller_verified: bool = True,
    risks: list[str] | None = None,
) -> SearchResult:
    facts = {
        "verify_status": "VERIFIED_GOOD",
        "exact_match": exact,
        "exact_product_verified": exact == "EXACT",
        "available": available,
        "price_confidence": price_confidence,
        "price_verified": price_verified,
        "product_page_verified": True,
        "availability_verified": True,
        "platform_type": platform_type,
        "seller_trust": seller_trust,
        "seller_verified": seller_verified,
        "category": "phone",
        "brand": "Apple",
        "model": "iPhone 16",
        "model_modifiers": ["Pro"],
        "storage_gb": 256,
        "condition": "new",
    }
    return SearchResult(
        id=idx,
        request_id=1,
        title="Apple iPhone 16 Pro 256 ГБ",
        price=price,
        source=source,
        url=url or f"https://example.test/product/{idx}",
        score=score,
        status=status,
        origin="auto",
        risk_flags=json.dumps(risks or [], ensure_ascii=False),
        facts_json=json.dumps(facts, ensure_ascii=False),
        price_verified=price_verified,
    )


class AIRolePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.req = Request(id=1, user_id=1, product_name="iPhone 16 Pro", budget="90000")

    def test_planned_roles_map_to_legacy_statuses(self) -> None:
        marketplace = candidate(1, source="Ozon", price=79_000, score=97)
        avito = candidate(
            2, source="Avito", price=62_000, score=70,
            platform_type="CLASSIFIED", risks=["частный продавец"],
        )
        retail = candidate(
            3, source="DNS", price=84_000, score=88, platform_type="RETAIL",
            seller_trust="HIGH", seller_verified=True,
        )
        self.assertEqual(assign_candidate_roles(self.req, [marketplace, avito, retail]), {
            1: "BEST", 2: "BUDGET", 3: "BACKUP",
        })

    def test_explicit_admin_roles_are_preserved(self) -> None:
        best = candidate(1, status="BEST", score=10)
        budget = candidate(2, status="APPROVED_BUDGET", score=99, price=60_000)
        backup = candidate(
            3, status="APPROVED_BACKUP", score=50, source="DNS",
            platform_type="RETAIL", seller_trust="HIGH", seller_verified=True,
        )
        self.assertEqual(assign_candidate_roles(self.req, [budget, backup, best]), {
            1: "BEST", 2: "BUDGET", 3: "BACKUP",
        })

    def test_duplicate_explicit_status_does_not_evict_another_admin_role(self) -> None:
        best = candidate(1, status="BEST", score=99)
        duplicate_best = candidate(2, status="TOP", score=98)
        budget = candidate(3, status="BUDGET", price=60_000, score=70)
        backup = candidate(
            4, status="BACKUP", source="DNS", score=60, platform_type="RETAIL",
            seller_trust="HIGH", seller_verified=True,
        )
        self.assertEqual(assign_candidate_roles(
            self.req, [best, duplicate_best, budget, backup],
        ), {1: "BEST", 3: "BUDGET", 4: "BACKUP"})

    def test_url_first_identity_and_maximum_three(self) -> None:
        weaker_duplicate = candidate(
            1, url="https://www.ozon.ru/product/iphone-1/?utm_source=ad", score=70,
        )
        stronger_duplicate = candidate(
            2, url="https://www.ozon.ru/product/iphone-1?ref=search", score=99,
        )
        retail = candidate(
            3, source="DNS", score=85, platform_type="RETAIL",
            seller_trust="HIGH", seller_verified=True,
        )
        roles = assign_candidate_roles(self.req, [weaker_duplicate, stronger_duplicate, retail])
        self.assertNotIn(1, roles)
        self.assertEqual(roles, {2: "BEST", 3: "BACKUP"})
        self.assertLessEqual(len(roles), 3)

    def test_mismatch_unavailable_and_low_confidence_are_excluded(self) -> None:
        mismatch = candidate(1, exact="MODEL_MISMATCH", score=100)
        unavailable = candidate(2, available=False, score=99)
        low_confidence = candidate(3, price_confidence="low", price_verified=False, score=98)
        valid = candidate(4, score=80)
        self.assertEqual(assign_candidate_roles(
            self.req, [mismatch, unavailable, low_confidence, valid],
        ), {4: "BEST"})

    def test_no_automatic_backup_without_reliable_offer(self) -> None:
        marketplace = candidate(1, score=95)
        avito = candidate(
            2, source="Avito", price=65_000, score=70,
            platform_type="CLASSIFIED", risks=["частный продавец"],
        )
        roles = assign_candidate_roles(self.req, [marketplace, avito])
        self.assertEqual(roles, {1: "BEST", 2: "BUDGET"})
        cards = build_fallback_ai_cards(self.req, [marketplace, avito])
        self.assertEqual([item["role"] for item in cards], ["BEST", "BUDGET"])
        self.assertEqual(len({item["candidate_id"] for item in cards}), len(cards))

    def test_verified_candidates_are_auto_approved_cards(self) -> None:
        verified = candidate(1)
        cards = build_fallback_ai_cards(self.req, select_verified_ai_card_candidates([verified]))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["ai_card_status"], "APPROVED")
        self.assertEqual(cards[0]["manual_check"], [])

        incomplete = candidate(2, seller_verified=False)
        self.assertEqual(select_verified_ai_card_candidates([incomplete]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
