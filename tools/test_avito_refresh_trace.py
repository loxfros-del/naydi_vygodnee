"""Offline regression checks for final refresh evidence and trace counts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import unittest

from avito_service.models import AnalyzedListing, CollectionBatch, SearchRequest
from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.normalization import normalize_listing
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from avito_service.telemetry import SearchTrace
from tools.test_avito_collection_quality import EvidenceReviewer
from tools.test_avito_strict_results import console_row
from tools.test_avito_pilot_telemetry import _report


class _Progress:
    def __init__(self, trace):
        self.trace = trace

    def __call__(self, *_):
        pass


class _PartialProvider:
    def __init__(self, rows):
        self.rows = rows
        self.requested = ()

    def refresh(self, listings, **_):
        self.requested = tuple(item.listing_id for item in listings)
        return CollectionBatch(tuple(self.rows), 0.01, False)


class FinalRefreshTraceTests(unittest.TestCase):
    def test_spend_guard_rejection_records_no_paid_post_or_run(self):
        class RejectingGuard:
            def reserve(self, _):
                raise ExternalServiceError("Monthly cap reached", code="APIFY_SPEND_LIMIT")

        class Provider(ZenStudioProvider):
            def _json_request(self, *_args, **_kwargs):
                raise AssertionError("No provider request is allowed after a rejected reserve")

        events = []
        provider = Provider(ServiceConfig(apify_token="fixture"), RejectingGuard())
        with self.assertRaises(ExternalServiceError):
            provider.collect(SearchRequest("PS5", max_results=1),
                             telemetry=lambda event, record: events.append((event, record)))
        event, record = events[-1]
        self.assertEqual(event, "error")
        self.assertEqual(record["phase"], "reservation")
        self.assertFalse(record["request_reached_provider"])
        self.assertFalse(record["run_id_received"])
        self.assertTrue(record["no_run_proven"])

    def test_rating_change_does_not_invalidate_stable_seller_or_product(self):
        before = normalize_listing(console_row(88000001, 44_000))
        row = console_row(88000001, 44_000)
        row["seller"].update(ratingScore=4.2, reviewCount=600)
        after = normalize_listing(row)
        self.assertTrue(before.seller.identity_hash)
        self.assertTrue(AvitoAnalysisService._review_evidence_unchanged(before, after))
        row["seller"]["id"] = "different-seller"
        self.assertFalse(AvitoAnalysisService._review_evidence_unchanged(
            before, normalize_listing(row)))
        row["seller"]["id"] = console_row(88000001, 44_000)["seller"]["id"]
        row["description"] = "Изменённое описание товара."
        self.assertFalse(AvitoAnalysisService._review_evidence_unchanged(
            before, normalize_listing(row)))

    def test_partial_refresh_records_actual_requested_returned_and_verified(self):
        request = SearchRequest("PS5", location="Ярославль", category="gaming",
                                pickup_only=True, desired_results=3)
        rows = [console_row(88000011, 44_000), console_row(88000012, 45_000)]
        reviewer = EvidenceReviewer()
        analyzed = []
        for row in rows:
            listing = normalize_listing(row)
            review = reviewer.review_photos(listing, reviewer.review_text(listing, request))
            analyzed.append(AnalyzedListing(listing, evaluate_rules(listing), review))
        refreshed = deepcopy(rows[0])
        refreshed["seller"].update(ratingScore=4.2, reviewCount=600)
        refreshed["scrapedAt"] = datetime.now(timezone.utc).isoformat()
        provider = _PartialProvider([refreshed])
        service = AvitoAnalysisService(provider, reviewer)
        trace = SearchTrace("offline-final-refresh", request)
        result, cost, estimated = service._verify_finalists(
            tuple(analyzed), request, deadline_at=None, allowance_usd=0.02,
            progress=_Progress(trace),
        )
        self.assertEqual(set(provider.requested), {"88000011", "88000012"})
        self.assertEqual(cost, 0.01)
        self.assertFalse(estimated)
        self.assertEqual(result[0].listing.verification_status, "verified")
        self.assertEqual(result[1].listing.verification_status, "failed")
        verification = trace._snapshot()["verification"]
        self.assertEqual(verification["final_revalidation_candidates"], 2)
        self.assertEqual(verification["final_revalidation_returned"], 1)
        self.assertEqual(verification["final_revalidation_completed"], 1)
        self.assertEqual({item["listing_id"]: item["status"] for item in verification["final_revalidation_outcomes"]},
                         {"88000011": "verified", "88000012": "missing"})
        trace.finish_report(_report(final_results=1))
        verification = trace._snapshot()["verification"]
        self.assertEqual(verification["photo_completed"], 2)
        self.assertEqual(verification["final_revalidation_candidates"], 2)
        self.assertEqual(verification["final_revalidation_returned"], 1)
        self.assertEqual(verification["final_revalidation_completed"], 1)

    def test_trace_only_accepts_public_ids_and_fixed_reason_codes(self):
        trace = SearchTrace("offline-safe-trace", SearchRequest("PS5"))
        trace.record_final_refresh(("123", "secret-token", "456"),
                                   returned_ids=("123", "456"),
                                   outcomes={"123": "verified", "456": "untrusted detail"})
        rows = trace._snapshot()["verification"]["final_revalidation_outcomes"]
        self.assertEqual(rows, [{"listing_id": "123", "returned": True, "status": "verified"},
                                {"listing_id": "456", "returned": True, "status": "not_run"}])


if __name__ == "__main__":
    unittest.main()
