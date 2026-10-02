from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.final_verification import FinalStatus, FinalVerificationInput, classify_final_bargain
from avito_service.normalization import normalize_listing
from avito_service.risk_rules import evaluate_rules
from tools.finalize_avito_candidates import _photo_review_admissible, _saved_seller_fallbacks
from tools.test_avito_service import raw_listing


def evidence(**changes):
    values = dict(
        listing_id="1", advertised_price=50_000, effective_price=50_000,
        conservative_market_price=60_000, market_median=62_000,
        comparable_count=3, independent_seller_count=3, stable_seller_count=3,
        estimated_extra_costs=1_000, price_confidence="exact", sku_exact=True,
        text_status="passed", photo_status="passed", live_status="active_price_verified",
    )
    values.update(changes)
    return FinalVerificationInput(**values)


class FinalVerificationTests(unittest.TestCase):
    def test_photo_admission_uses_listing_safety_not_market_sample(self):
        listing = normalize_listing(raw_listing(12101))
        listing = replace(
            listing, verified_at=datetime.now(timezone.utc).isoformat(),
            verification_status="verified",
        )
        risks = evaluate_rules(listing)
        self.assertTrue(_photo_review_admissible(
            listing, live=True, text_status="passed", sku_exact=True,
            photo_status="missing", risks=risks,
        ))
        self.assertFalse(_photo_review_admissible(
            listing, live=True, text_status="failed", sku_exact=True,
            photo_status="missing", risks=risks,
        ))
        self.assertFalse(_photo_review_admissible(
            listing, live=True, text_status="passed", sku_exact=False,
            photo_status="missing", risks=risks,
        ))
        defective = normalize_listing(raw_listing(12102, description="Экран не работает."))
        defective = replace(
            defective, verified_at=datetime.now(timezone.utc).isoformat(),
            verification_status="verified",
        )
        self.assertFalse(_photo_review_admissible(
            defective, live=True, text_status="passed", sku_exact=True,
            photo_status="missing", risks=evaluate_rules(defective),
        ))

    def test_saved_seller_fallback_does_not_invent_stable_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "all-decisions.json"
            path.write_text(json.dumps([{
                "listing": {"listing_id": "42", "seller": {
                    "name": "Магазин", "seller_type": "company", "is_shop": True,
                }}
            }]), encoding="utf-8")
            seller = _saved_seller_fallbacks(Path(directory))["42"]
        self.assertEqual(seller.name, "Магазин")
        self.assertEqual(seller.kind, "company")
        self.assertFalse(seller.identity_hash)

    def test_confirmed_bargain_requires_live_complete_evidence(self):
        result = classify_final_bargain(evidence())
        self.assertEqual(result.final_status, FinalStatus.CONFIRMED_BARGAIN)
        self.assertEqual(result.conservative_net_saving, 9_000)

    def test_three_independent_sellers_can_have_high_confidence_without_stable_ids(self):
        result = classify_final_bargain(evidence(independent_seller_count=3, stable_seller_count=0))
        self.assertEqual(result.final_status, FinalStatus.HIGH_CONFIDENCE_BARGAIN)
        self.assertIn("STABLE_SELLER_IDS_INCOMPLETE", result.blockers)

    def test_zero_or_two_comparables_keeps_exact_match_without_savings(self):
        for changes in (
            {"comparable_count": 0, "independent_seller_count": 0,
             "conservative_market_price": None, "market_median": None},
            {"comparable_count": 2, "independent_seller_count": 2},
        ):
            with self.subTest(changes=changes):
                result = classify_final_bargain(evidence(**changes))
                self.assertEqual(result.final_status, FinalStatus.EXACT_MATCH)
                self.assertIsNone(result.saving_rub)
                self.assertIsNone(result.saving_percent)

    def test_sku_mismatch_still_rejects_even_when_market_exists(self):
        result = classify_final_bargain(evidence(sku_exact=False, comparable_count=0,
                                                 independent_seller_count=0))
        self.assertEqual(result.final_status, FinalStatus.REJECT)
        self.assertIn("SKU_NOT_EXACT", result.blockers)

    def test_one_or_two_comparables_cannot_confirm_bargain(self):
        result = classify_final_bargain(evidence(independent_seller_count=1, stable_seller_count=1))
        self.assertEqual(result.final_status, FinalStatus.EXACT_MATCH)
        self.assertIsNone(result.saving_rub)
        self.assertIn("INDEPENDENT_SELLER_COUNT_1_LT_3", result.blockers)

    def test_price_equal_to_benchmark_is_rejected(self):
        result = classify_final_bargain(evidence(effective_price=60_000))
        self.assertEqual(result.final_status, FinalStatus.EXACT_MATCH)
        self.assertIn("NOT_BELOW_CONSERVATIVE_MARKET", result.blockers)

    def test_price_above_benchmark_cannot_be_high_confidence(self):
        result = classify_final_bargain(evidence(effective_price=61_000, independent_seller_count=0))
        self.assertEqual(result.final_status, FinalStatus.EXACT_MATCH)
        self.assertIsNone(result.saving_rub)

    def test_photo_or_live_gap_requires_review(self):
        for changes in ({"photo_status": "missing"}, {"live_status": "not_refreshed"}):
            with self.subTest(changes=changes):
                self.assertEqual(classify_final_bargain(evidence(**changes)).final_status, FinalStatus.REVIEW)

    def test_review_reports_all_incomplete_layers(self):
        result = classify_final_bargain(evidence(comparable_count=2, photo_status="missing"))
        self.assertEqual(result.final_status, FinalStatus.REVIEW)
        self.assertIsNone(result.saving_rub)
        self.assertIn("COMPARABLE_COUNT_2_LT_3", result.blockers)
        self.assertIn("PHOTO_CHECK_INCOMPLETE", result.blockers)

    def test_unknown_internal_parts_is_manual_check_not_reject(self):
        result = classify_final_bargain(evidence(manual_checks=("INTERNAL_PARTS_ORIGINALITY_UNKNOWN",)))
        self.assertEqual(result.final_status, FinalStatus.CONFIRMED_BARGAIN)
        self.assertIn("INTERNAL_PARTS_ORIGINALITY_UNKNOWN", result.manual_checks)

    def test_configured_minimum_bargain_does_not_block_exact_match(self):
        result = classify_final_bargain(evidence(
            effective_price=58_990,
            conservative_market_price=62_499,
            market_median=64_999,
            minimum_bargain_rub=5_000,
            minimum_bargain_percent=10.0,
        ))
        self.assertIsNone(result.saving_rub)
        self.assertIsNone(result.saving_percent)
        self.assertEqual(result.final_status, FinalStatus.EXACT_MATCH)
        self.assertIn("MINIMUM_BARGAIN_NOT_MET", result.blockers)

    def test_zero_minimum_preserves_any_positive_bargain(self):
        result = classify_final_bargain(evidence(
            effective_price=59_999,
            conservative_market_price=60_000,
            estimated_extra_costs=0,
        ))
        self.assertEqual(result.final_status, FinalStatus.CONFIRMED_BARGAIN)


if __name__ == "__main__":
    unittest.main()
