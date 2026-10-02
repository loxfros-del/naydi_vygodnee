"""The cheapest credible offer receives a photo slot before seller popularity wins."""
from __future__ import annotations

from dataclasses import replace
import unittest

from avito_service.models import ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_dataset
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from tools.test_avito_collection_quality import EvidenceReviewer
from tools.test_avito_strict_results import console_row


class PhotoPricePriorityTests(unittest.TestCase):
    def test_cheapest_approved_ps5_gets_a_photo_slot(self):
        rows = [console_row(81000000, 44_000)]
        rows[0]["seller"].update(ratingScore=0, reviewCount=0)
        for offset in range(1, 8):
            row = console_row(81000000 + offset, 58_000 + offset * 100)
            row["seller"].update(ratingScore=5, reviewCount=5_000)
            rows.append(row)
        listings = normalize_dataset(rows)
        reviewer = EvidenceReviewer()
        request = SearchRequest("PS5", location="Ярославль", category="gaming",
                                pickup_only=True, desired_results=3)
        reviews = {item.listing_id: reviewer.review_text(item, request) for item in listings}
        risks = {item.listing_id: evaluate_rules(item) for item in listings}
        service = AvitoAnalysisService(None, reviewer)
        selected = service._photo_candidate_ids(listings, risks, reviews, 3, request)
        self.assertEqual(len(selected), 3)
        self.assertIn("81000000", selected)

        # A cheap offer rejected by text analysis must never receive the slot.
        reviews["81000000"] = replace(reviews["81000000"], verdict=ReviewVerdict.REJECT)
        selected = service._photo_candidate_ids(listings, risks, reviews, 3, request)
        self.assertNotIn("81000000", selected)


if __name__ == "__main__":
    unittest.main()
