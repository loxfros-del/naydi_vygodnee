"""Offline checks for deliberately resuming a saved photo/final-page review."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import URLError

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.models import ReviewVerdict
from avito_service.normalization import normalize_listing
from avito_service.service import AvitoAnalysisService
from tools import recheck_avito_finalist as recheck
from tools.test_avito_collection_quality import EvidenceProvider, EvidenceReviewer
from tools.test_avito_strict_results import console_row


class RecheckReviewer(EvidenceReviewer):
    build_photo_payload = staticmethod(OpenAICompatibleReviewer.build_photo_payload)

    def __init__(self):
        super().__init__()
        self.reserve = 4.5
        self.retry_rate_limits = True
        self.allow_retry_calls = []
        self.before_photo = None
        self.photo_changes = {}
        self.photo_error = None
        self.limit = None

    def estimate_payload_cost(self, payload):
        self.payload = payload
        return self.reserve

    def begin_budget(self, limit):
        self.limit = limit

    def set_deadline(self, deadline):
        self.deadline = deadline

    def budget_snapshot(self):
        return {"limit_rub": self.limit, "committed_rub": self.reserve,
                "active_reservations": 0}

    def review_photos(self, listing, text_review, *, allow_retry=True):
        self.allow_retry_calls.append(allow_retry)
        if self.before_photo:
            self.before_photo()
        if self.photo_error:
            self.photo_calls.append(listing.listing_id)
            raise self.photo_error
        return replace(super().review_photos(listing, text_review), **self.photo_changes)


class RecheckFinalistTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.row = console_row(8125174775, 48_000, model="PlayStation 5 Slim")
        self.row["images"] = [f"https://01.img.avito.st/fixture-{index}.jpg" for index in range(9)]
        self.row["imageCount"] = 9
        self.row["parameters"]["Встроенная память"] = "1 ТБ"
        self.listing = normalize_listing(self.row)
        self.reviewer = RecheckReviewer()
        self.text_review = self.reviewer.review_text(self.listing)
        self.provider = EvidenceProvider()
        self.provider.candidate_rows = [self.row]
        self.provider.spending_guard = object()
        self.service = AvitoAnalysisService(self.provider, self.reviewer)
        self.config = ServiceConfig(
            apify_token="fixture", ai_api_key="fixture", ai_base_url="https://example.invalid/v1",
            ai_model="gpt-4.1-mini", ai_max_cost_rub=50, apify_max_charge_usd=2,
            report_max_cost_rub=250,
        )
        self.stack.enter_context(patch.object(recheck, "ROOT", self.root))
        self.stack.enter_context(patch.object(recheck, "load_config", return_value=self.config))
        self.load_listing = self.stack.enter_context(patch.object(recheck, "load_listing", return_value=self.listing))
        self.load_review = self.stack.enter_context(patch.object(recheck, "load_text_review", return_value=self.text_review))
        self.stack.enter_context(patch.object(recheck, "build_service", return_value=self.service))
        self.account = self.stack.enter_context(patch.object(
            recheck, "account_bounded_spending_guard",
            return_value=(self.provider.spending_guard, {"new_spend_ceiling_usd": 0.4}),
        ))
        self.account_provider = self.stack.enter_context(patch.object(recheck, "ZenStudioProvider"))
        self.pilot_provider = self.stack.enter_context(patch.object(recheck, "PilotProvider", return_value=self.provider))
        # A missed injection must fail the test instead of connecting to any provider.
        self.stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("External network forbidden")))
        self.output = self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(redirect_stderr(io.StringIO()))
        self.argv = ["--source-output", str(self.root / "saved"), "--listing-id", self.listing.listing_id]

    def run_live(self):
        recheck.main(self.argv + ["--live", "--confirm-photo-resend", "--authorized-apify-budget-usd", "1"])

    def read_result(self):
        paths = list((self.root / "runtime" / "avito_pilot").glob("recheck-*/result.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_default_is_offline_plan_without_account_or_paid_requests(self):
        recheck.main(self.argv)
        self.account.assert_not_called()
        self.account_provider.assert_not_called()
        self.pilot_provider.assert_not_called()
        self.assertEqual(self.reviewer.photo_calls, [])
        self.assertEqual(self.provider.refresh_calls, [])
        self.assertFalse((self.root / "runtime").exists())
        plan = json.loads(self.output.getvalue().removeprefix("RECHECK_PLAN "))
        self.assertFalse(plan["live"])
        self.assertEqual(plan["photo_count"], 9)
        self.assertFalse(plan["automatic_paid_retries"])

    def test_live_requires_separate_confirmation_before_loading_candidate(self):
        with self.assertRaises(SystemExit) as caught:
            recheck.main(self.argv + ["--live", "--authorized-apify-budget-usd", "1"])
        self.assertEqual(caught.exception.code, 2)
        self.load_listing.assert_not_called()
        self.account.assert_not_called()

    def test_route_is_explicit_in_preflight_and_service_configuration(self):
        with patch.object(recheck, "build_service", return_value=self.service) as builder:
            recheck.main(self.argv + ["--ai-proxy-mode", "direct"])
        self.assertEqual(builder.call_args.args[0].ai_proxy_mode, "direct")
        plan = json.loads(self.output.getvalue().removeprefix("RECHECK_PLAN "))
        self.assertEqual(plan["ai_proxy_mode"], "direct")
        self.account.assert_not_called()
        self.assertEqual(self.reviewer.photo_calls, [])

    def test_invalid_route_is_rejected_before_loading_candidate(self):
        with self.assertRaises(SystemExit):
            recheck.main(self.argv + ["--ai-proxy-mode", "fallback"])
        self.load_listing.assert_not_called()
        self.account.assert_not_called()

    def test_live_requires_finite_positive_remaining_allowance_at_most_eight(self):
        for value in (None, "0", "-1", "8.01", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as caught:
                args = self.argv + ["--live", "--confirm-photo-resend"]
                if value is not None:
                    args += ["--authorized-apify-budget-usd", value]
                recheck.main(args)
            self.assertEqual(caught.exception.code, 2)
        self.load_listing.assert_not_called()
        self.account.assert_not_called()

    def test_bad_text_or_excessive_ai_reserve_stops_before_account_or_photo(self):
        self.load_review.return_value = replace(self.text_review, defects=("Повреждён HDMI-разъём",))
        with self.assertRaises(ValueError):
            self.run_live()
        self.load_review.return_value = self.text_review
        self.reviewer.reserve = 51
        with self.assertRaises(ValueError):
            self.run_live()
        self.account.assert_not_called()
        self.assertEqual(self.reviewer.photo_calls, [])

    def test_uncertain_provider_error_is_persisted_before_one_photo_attempt(self):
        def before_photo():
            result = self.read_result()
            self.assertTrue(result["photo_attempted"])
            self.assertFalse(result["final_refresh_attempted"])
            self.assertEqual(result["finalists"], [])
            self.assertEqual(result["photo_cost"], {
                "reservation_state": "retained_uncertain", "accounted_cost_rub": 4.5,
                "cost_estimated": True,
            })
            self.assertFalse(self.reviewer.retry_rate_limits)
            self.account.assert_called_once()

        self.reviewer.before_photo = before_photo
        self.reviewer.photo_error = ExternalServiceError("hidden raw error", code="AI_NETWORK_ERROR", diagnostics={
            "http_status": None, "transport_error_type": "SSLEOFError", "transport_phase": "tls",
            "tls_reason": "UNEXPECTED_EOF_WHILE_READING", "request_outcome": "unknown",
            "transport_elapsed_seconds": 90.1, "transport_timeout_seconds": 90,
            "transport_proxy_mode": "direct",
            "reservation_state": "retained_uncertain", "accounted_cost_rub": 4.5,
            "cost_estimated": True, "raw_secret": "must-never-be-saved",
        })
        self.run_live()
        result = self.read_result()
        self.assertEqual(self.reviewer.allow_retry_calls, [False])
        self.assertEqual(self.reviewer.photo_calls, [self.listing.listing_id])
        self.assertEqual(self.provider.refresh_calls, [])
        self.assertEqual(result["blocker"], "AI_NETWORK_ERROR")
        self.assertEqual(result["photo_error"]["transport_error_type"], "SSLEOFError")
        self.assertEqual(result["photo_error"]["tls_reason"], "UNEXPECTED_EOF_WHILE_READING")
        self.assertEqual(result["photo_error"]["transport_elapsed_seconds"], 90.1)
        self.assertEqual(result["photo_error"]["transport_timeout_seconds"], 90)
        self.assertEqual(result["photo_error"]["transport_proxy_mode"], "direct")
        self.assertEqual(result["photo_cost"]["accounted_cost_rub"], 4.5)
        self.assertNotIn("raw_secret", json.dumps(result))
        self.assertNotIn("hidden raw error", json.dumps(result))

    def test_unwrapped_transport_error_keeps_persistent_liability_and_no_finalist(self):
        self.reviewer.photo_error = URLError(socket.timeout("private transport details"))
        with self.assertRaises(URLError):
            self.run_live()
        result = self.read_result()
        self.assertEqual(result["error_type"], "URLError")
        self.assertEqual(result["blocker"], "RECHECK_ERROR")
        self.assertEqual(result["photo_cost"]["reservation_state"], "retained_uncertain")
        self.assertEqual(result["photo_cost"]["accounted_cost_rub"], 4.5)
        self.assertEqual(result["finalists"], [])
        self.assertFalse(result["final_refresh_attempted"])
        self.assertEqual(self.provider.refresh_calls, [])
        self.assertNotIn("private transport details", json.dumps(result))

    def test_incomplete_photo_coverage_never_refreshes(self):
        self.reviewer.photo_changes = {"photo_coverage": (1,), "photos_analyzed": True}
        self.run_live()
        result = self.read_result()
        self.assertFalse(result["photo"]["complete"])
        self.assertFalse(result["final_refresh_attempted"])
        self.assertEqual(result["finalists"], [])
        self.assertEqual(self.provider.refresh_calls, [])

    def test_complete_caution_photo_never_refreshes(self):
        self.reviewer.photo_changes = {"verdict": ReviewVerdict.CAUTION, "conflicts": ("Повреждение на фото",)}
        self.run_live()
        result = self.read_result()
        self.assertTrue(result["photo"]["complete"])
        self.assertFalse(result["final_refresh_attempted"])
        self.assertEqual(result["finalists"], [])
        self.assertEqual(self.provider.refresh_calls, [])

    def test_full_safe_photo_then_live_page_uses_real_safety_and_no_invented_savings(self):
        self.run_live()
        result = self.read_result()
        self.assertEqual(self.reviewer.allow_retry_calls, [False])
        self.assertFalse(self.reviewer.retry_rate_limits)
        self.assertEqual(self.provider.refresh_calls, [((self.listing.listing_id,), 0.4)])
        self.assertTrue(result["photo"]["complete"])
        self.assertTrue(result["final_refresh_attempted"])
        self.assertEqual(result["final_refresh"]["status"], "verified")
        self.assertEqual(len(result["finalists"]), 1)
        card = result["finalists"][0]
        self.assertEqual(card["listing"]["id"], self.listing.listing_id)
        self.assertEqual(card["listing"]["price"], 48_000)
        self.assertEqual(card["listing"]["verificationStatus"], "verified")
        self.assertIsNone(card["savingAmount"])
        self.assertFalse(card["belowMarket"])
        self.assertFalse(card["belowComparables"])

    def test_page_removed_after_safe_photo_cannot_become_finalist(self):
        self.provider.refresh_changes[self.listing.listing_id] = {"status": "removed"}
        self.run_live()
        result = self.read_result()
        self.assertTrue(result["final_refresh_attempted"])
        self.assertEqual(result["final_refresh"]["status"], "failed")
        self.assertEqual(result["finalists"], [])
        self.assertEqual(result["blocker"], "FINAL_REFRESH_OR_SAFETY")


if __name__ == "__main__":
    unittest.main()
