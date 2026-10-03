"""Network uncertainty retains its exact reservation; a preflight block costs zero."""
from contextlib import redirect_stdout
import io
import unittest

from avito_service.errors import ExternalServiceError
from avito_service.models import SearchRequest
from avito_service.normalization import normalize_dataset
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from avito_service.telemetry import SearchTrace
from tools.test_avito_collection_quality import EvidenceReviewer
from tools.test_avito_strict_results import console_row


class PhotoAccountingTests(unittest.TestCase):
    def test_only_retained_photo_reservation_is_accounted_and_no_followup_is_sent(self):
        for code, reserved, blocked, calls in (
            ("AI_NETWORK_ERROR", 4.25, False, 1),
            ("AI_BUDGET", 0.0, True, 0),
            ("AI_TIMEOUT", 0.0, True, 0),
        ):
            with self.subTest(code=code):
                class Reviewer(EvidenceReviewer):
                    def review_photos(self, listing, text_review):
                        self.photo_calls.append(listing.listing_id)
                        raise ExternalServiceError("fixture failure", code=code, diagnostics={
                            "accounted_cost_rub": reserved, "cost_estimated": not blocked,
                            "reservation_blocked": blocked,
                            "reservation_state": "blocked_before_request" if blocked else "retained_uncertain",
                            "transport_phase": "open_or_read", "transport_error_type": "SSLEOFError",
                            "request_outcome": "unknown", "http_status": None,
                            "transport_elapsed_seconds": 90.1, "transport_timeout_seconds": 90,
                            "transport_proxy_mode": "direct",
                        })

                listings = normalize_dataset([console_row(81000001 + index, 44_000) for index in range(3)])
                reviewer = Reviewer()
                service = AvitoAnalysisService(None, reviewer, ai_max_cost_rub=50)
                request = SearchRequest("PS5", location="Ярославль", category="gaming", pickup_only=True)
                trace = SearchTrace("photo-accounting", request)

                def progress(stage, message, percent):
                    pass

                progress.trace = trace
                with redirect_stdout(io.StringIO()):
                    reviewed = service._review_with_budget(
                        listings, {item.listing_id: evaluate_rules(item) for item in listings},
                        request, progress=progress,
                    )
                self.assertEqual(len(reviewer.photo_calls), 1)
                self.assertAlmostEqual(reviewed.ai_cost_rub, len(listings) * reviewer.text_cost + reserved)
                self.assertEqual(reviewed.photo_completed_count, 0)
                photo = trace.snapshot()["ai"]["photo"]
                self.assertEqual(photo["calls"], calls)
                self.assertAlmostEqual(photo["actual_cost_rub"], 0)
                self.assertAlmostEqual(photo["estimated_cost_rub"], reserved)
                self.assertEqual(photo["packets"][0]["budget"]["reservation_blocked"], blocked)
                self.assertEqual(photo["packets"][0]["transport_error_type"], "SSLEOFError")
                self.assertIsNone(photo["packets"][0]["http_status"])
                self.assertEqual(photo["packets"][0]["transport_elapsed_seconds"], "90.1")
                self.assertEqual(photo["packets"][0]["transport_timeout_seconds"], "90")
                self.assertEqual(photo["packets"][0]["transport_proxy_mode"], "direct")

    def test_text_packet_receipts_keep_billed_child_calls_separate_from_parent_estimate(self):
        trace = SearchTrace("mixed-text-cost", SearchRequest("PS5"))
        for amount, estimate in ((4.0, True), (0.23, False), (0.25, False)):
            trace.record_ai_packet({"listing_ids": ["81000001"], "budget": {
                "accounted_cost_rub": amount, "cost_estimated": estimate,
                "reservation_blocked": False,
            }})
        trace.record_ai("text", model="fixture", calls=3, listings=1, duration_ms=1,
                        cost_rub=4.48, cost_estimated=True,
                        actual_cost_rub=0, estimated_cost_rub=4.48)
        costs = trace.snapshot()["cost"]["by_stage"]["text_ai"]
        self.assertEqual(costs, {"actual_rub": 0.48, "estimated_rub": 4.0, "accounted_rub": 4.48})

    def test_partial_packet_diagnostics_do_not_replace_total_cost(self):
        trace = SearchTrace("partial-text-cost", SearchRequest("PS5"))
        trace.record_ai_packet({"budget": {
            "accounted_cost_rub": 0.23, "cost_estimated": False, "reservation_blocked": False,
        }})
        trace.record_ai("text", model="fixture", calls=2, listings=1, duration_ms=1,
                        cost_rub=4.23, cost_estimated=True)
        self.assertEqual(trace.snapshot()["ai"]["text"]["estimated_cost_rub"], 4.23)


if __name__ == "__main__":
    unittest.main()
