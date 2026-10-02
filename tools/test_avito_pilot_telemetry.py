from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig, load_config
from avito_service.errors import SearchCancelledError
from avito_service.models import AnalysisReport, CostSummary, PipelineAudit, SearchRequest
from avito_service.spending import SpendingGuard
from avito_service.telemetry import PilotTraceStore, SearchTrace
from tools.summarize_avito_pilot import read_traces, render, summarize


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _report(*, final_results: int = 2, estimated: bool = False) -> AnalysisReport:
    return AnalysisReport(
        query="PlayStation 5",
        location="Ярославль",
        collected_count=10,
        analyzed_count=4,
        empty_reason="" if final_results else "Подходящих предложений нет.",
        costs=CostSummary(
            apify_cost_usd=0.12,
            apify_cost_estimated=estimated,
            ai_cost_rub=3.5,
            ai_cost_estimated=estimated,
            estimated_total_rub=15.5,
        ),
        pipeline=PipelineAudit(
            raw_collected_count=12,
            deduplicated_count=10,
            correct_city_count=9,
            request_family_matched_count=8,
            deterministic_eligible_count=4,
            text_attempted_count=4,
            text_completed_count=4,
            text_matched_count=3,
            photo_attempted_count=3,
            photo_completed_count=2,
            final_visible_count=final_results,
            rejection_reasons=({
                "stage": "after_text_ai", "rejected": 1,
                "primary": {"AI_CONFLICT": 1}, "secondary": {},
            },),
            bargain_funnel={
                "text_safe": 4, "bargain_evaluated": 4, "bargain_pass": 1,
                "bargain_fail": 3, "photo_ai_eligible": 1,
                "photo_ai_attempted": 1, "photo_ai_completed": 1,
                "final_revalidated": 1, "final": 1, "bargain": 0,
                "exact_match": 1, "rejected": 0, "pending": 0,
                "bargain_fail_reasons": {"INSUFFICIENT_COMPARABLE_SELLERS": 3},
            },
            bargain_decisions=({
                "listing_id": "1001", "status": "fail",
                "reason_code": "INSUFFICIENT_COMPARABLE_SELLERS",
                "candidate_full_price": 40_000, "reference_price": None,
                "minimum_comparable_sellers": 3,
                "photo_ai_attempted": True, "photo_ai_completed": True,
                "final_revalidated": True, "final_status": "exact_match",
                "final_reason_code": "SAVINGS_NOT_CONFIRMED",
            },),
        ),
    )


class PilotTelemetryTests(unittest.TestCase):
    def test_bargain_listing_diagnostics_are_admin_only(self) -> None:
        pipeline = _report().pipeline
        self.assertEqual(pipeline.admin_dict()["bargainDecisions"][0]["listing_id"], "1001")
        self.assertNotIn("bargainDecisions", pipeline.public_dict())

    def test_live_pilot_flag_is_explicit_and_off_by_default(self) -> None:
        self.assertFalse(ServiceConfig().live_pilot)
        with patch.dict(os.environ, {"AVITO_LIVE_PILOT": "1"}):
            self.assertTrue(load_config().live_pilot)

    def test_apify_cancel_trace_records_abort_and_paid_post_is_not_retried(self) -> None:
        class FixtureProvider(ZenStudioProvider):
            def __init__(self) -> None:
                super().__init__(ServiceConfig(apify_token="fixture"))
                self.started = False
                self.post_count = 0

            def _json_request(self, method, url, *, payload=None, timeout=130):
                self.post_count += method == "POST"
                self.started = True
                return {"data": {"id": "run-telemetry", "status": "RUNNING"}}

            def _abort_run(self, run_id: str) -> bool:
                return run_id == "run-telemetry"

        provider = FixtureProvider()
        events = []
        with self.assertRaises(SearchCancelledError):
            provider.collect(
                SearchRequest("PlayStation 5", max_results=1),
                cancel_requested=lambda: provider.started,
                telemetry=lambda event, record: events.append((event, dict(record))),
                purpose="market_collection",
            )
        self.assertEqual(provider.post_count, 1)
        self.assertEqual([event for event, _ in events], ["starting", "started", "cancelled"])
        self.assertTrue(events[-1][1]["abort_requested"])
        self.assertTrue(events[-1][1]["abort_confirmed"])

    def test_cancel_reconciles_paid_start_as_settled_estimate(self) -> None:
        with TemporaryDirectory() as directory:
            guard = SpendingGuard(Path(directory) / "spend.json")

            class FixtureProvider(ZenStudioProvider):
                def __init__(self) -> None:
                    super().__init__(ServiceConfig(apify_token="fixture"), guard)
                    self.started = False

                def _json_request(self, method, url, *, payload=None, timeout=130):
                    self.started = True
                    return {"data": {"id": "run-cancel", "status": "RUNNING"}}

                def _abort_run(self, run_id: str) -> bool:
                    return True

            provider = FixtureProvider()
            with self.assertRaises(SearchCancelledError):
                provider.collect(
                    SearchRequest("PlayStation 5", max_results=1),
                    max_charge_usd=0.1,
                    cancel_requested=lambda: provider.started,
                )
            snapshot = guard.snapshot()
            self.assertEqual(snapshot["activeReservationUsd"], 0)
            self.assertAlmostEqual(snapshot["settledEstimateUsd"], 0.1)

    def test_success_trace_is_atomic_and_contains_timings_funnel_usage_and_cost(self) -> None:
        clock = _Clock()
        with TemporaryDirectory() as directory:
            store = PilotTraceStore(Path(directory))
            trace = SearchTrace(
                "job-success", SearchRequest("PlayStation 5", location="Ярославль"),
                store=store, parse_ms=7, clock=clock,
            )
            clock.advance(0.1)
            trace.stage("collect")
            trace.record_cache("market", hit=False)
            trace.record_cache("ai", hit=False)
            trace.record_apify("started", {
                "trace_run_id": "market-1", "purpose": "market_collection",
                "run_id": "run-public", "status": "RUNNING",
                "requested_max_total_charge_usd": 0.2,
            })
            clock.advance(1.2)
            trace.record_apify("finished", {
                "trace_run_id": "market-1", "purpose": "market_collection",
                "run_id": "run-public", "status": "SUCCEEDED", "duration_ms": 1200,
                "actual_cost_usd": 0.12, "accounted_cost_usd": 0.12,
                "cost_estimated": False, "items": 12, "used": True,
            })
            trace.stage("prepare")
            clock.advance(0.05)
            trace.stage("text")
            trace.record_ai(
                "text", model="fixture-model", calls=2, listings=4, duration_ms=300,
                cost_rub=2.5, cost_estimated=False, input_tokens=100, output_tokens=20,
            )
            clock.advance(0.3)
            trace.stage("photo")
            trace.record_ai(
                "photo", model="fixture-model", calls=1, listings=2, duration_ms=200,
                cost_rub=1.0, cost_estimated=False, input_tokens=50, output_tokens=10,
            )
            clock.advance(0.2)
            trace.stage("ranking")
            clock.advance(0.02)
            trace.finish_report(_report(), response_preparation_ms=3, usd_rub_rate=100)

            saved = json.loads((Path(directory) / "job-success.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "success")
            self.assertEqual(saved["timings"]["parse_ms"], 7)
            self.assertGreater(saved["timings"]["collection_ms"], 0)
            self.assertEqual(saved["collection"]["duplicates_removed"], 2)
            self.assertEqual(saved["filtering"]["after_hard_filters"], 4)
            self.assertEqual(saved["ai"]["text"]["input_tokens"], 100)
            self.assertEqual(saved["cost"]["apify_usd"], 0.12)
            self.assertEqual(saved["cost"]["total_rub"], 15.5)
            self.assertEqual(saved["cost"]["by_stage"]["collection"]["actual_rub"], 12.0)
            self.assertEqual(saved["cost"]["by_stage"]["text_ai"]["actual_rub"], 2.5)
            self.assertEqual(saved["cost"]["by_stage"]["photo_ai"]["actual_rub"], 1.0)
            self.assertEqual(saved["filtering"]["rejection_reasons"][0]["primary"], {"AI_CONFLICT": 1})
            self.assertEqual(saved["filtering"]["bargain_funnel"]["text_safe"], 4)
            self.assertEqual(saved["filtering"]["bargain_funnel"]["photo_ai_eligible"], 1)
            self.assertEqual(saved["filtering"]["bargain_funnel"]["exact_match"], 1)
            self.assertEqual(saved["filtering"]["bargain_funnel"]["pending"], 0)
            self.assertEqual(
                saved["filtering"]["bargain_funnel"]["bargain_fail_reasons"],
                {"INSUFFICIENT_COMPARABLE_SELLERS": 3},
            )
            self.assertEqual(
                saved["filtering"]["bargain_decisions"][0]["reason_code"],
                "INSUFFICIENT_COMPARABLE_SELLERS",
            )
            self.assertFalse((Path(directory) / ".job-success.tmp").exists())

    def test_empty_trace_keeps_unknown_actual_cost_null(self) -> None:
        trace = SearchTrace("job-empty", SearchRequest("редкий товар"))
        trace.finish_report(_report(final_results=0, estimated=True), usd_rub_rate=100)
        saved = trace.snapshot()
        self.assertEqual(saved["status"], "empty")
        self.assertIsNone(saved["cost"]["apify_usd"])
        self.assertEqual(saved["cost"]["apify_estimated_usd"], 0.12)
        self.assertIsNone(saved["cost"]["ai_rub"])
        self.assertIsNone(saved["cost"]["total_rub"])
        self.assertEqual(saved["cost"]["estimated_total_rub"], 15.5)

    def test_text_packet_trace_keeps_only_safe_structural_metadata(self) -> None:
        trace = SearchTrace("job-packet", SearchRequest("ps5"))
        trace.record_ai_packet({
            "listing_ids": ["1001", "1002"], "batch_size": 2, "attempt": 2,
            "split_depth": 1, "model": "fixture", "route": "example/v1/chat/completions",
            "duration_ms": 321, "provider_error_code": "AI_INVALID_RESPONSE",
            "http_status": 200, "parse_schema_error": "truncated_batch_json",
            "expected_result_count": 2, "returned_result_count": 1,
            "completed_result_count": 1, "missing_ids": ["1002"],
            "duplicate_ids": [], "unknown_ids": ["attacker"], "invalid_items": 0,
            "finish_reason": "stop", "will_split": True,
            "budget": {"reservation_rub": 4.2, "reservation_state": "settled_estimate",
                       "accounted_cost_rub": 1.1, "cost_estimated": True},
            "raw_response": "must not be stored", "prompt": "must not be stored",
        })
        packet = trace.snapshot()["ai"]["text"]["packets"][0]
        self.assertEqual(packet["missing_ids"], ["1002"])
        self.assertEqual(packet["provider_error_code"], "AI_INVALID_RESPONSE")
        self.assertTrue(packet["will_split"])
        self.assertNotIn("raw_response", packet)
        self.assertNotIn("prompt", packet)

    def test_error_and_cancel_close_trace_without_publishing_result(self) -> None:
        error = SearchTrace("job-error", SearchRequest("ошибка"))
        error.stage("collect")
        error.finish_error("APIFY_UNAVAILABLE", "ExternalServiceError")
        self.assertEqual(error.snapshot()["status"], "error")
        self.assertEqual(error.snapshot()["error"]["code"], "APIFY_UNAVAILABLE")

        clock = _Clock()
        cancelled = SearchTrace("job-cancel", SearchRequest("отмена"), clock=clock)
        cancelled.stage("collect")
        cancelled.record_apify("started", {
            "trace_run_id": "run-trace", "run_id": "run-123",
            "purpose": "market_collection", "status": "RUNNING",
        })
        cancelled.request_cancel("collect")
        clock.advance(0.25)
        cancelled.record_apify("cancelled", {
            "trace_run_id": "run-trace", "run_id": "run-123",
            "purpose": "market_collection", "status": "CANCELLED",
            "cancel_requested": True, "abort_requested": True,
            "abort_confirmed": True, "used": False,
        })
        cancelled.worker_stopped(result_published=False)
        value = cancelled.snapshot()
        self.assertEqual(value["status"], "cancelled")
        self.assertEqual(value["cancel"]["active_apify_run_ids"], ["run-123"])
        self.assertTrue(value["cancel"]["abort_confirmed"])
        self.assertEqual(value["cancel"]["stop_latency_ms"], 250)
        self.assertFalse(value["result"]["published_after_cancel"])
        self.assertFalse(value["cancel"]["expensive_stage_started_after_cancel"])

    def test_apify_post_started_before_cancel_is_not_reported_as_new_stage_after_cancel(self) -> None:
        trace = SearchTrace("job-race", SearchRequest("ps5"))
        trace.record_apify("starting", {
            "trace_run_id": "run-race", "purpose": "market_collection",
        })
        trace.request_cancel("collect")
        trace.record_apify("started", {
            "trace_run_id": "run-race", "run_id": "run-late-response",
            "purpose": "market_collection", "status": "RUNNING",
        })
        self.assertFalse(trace.snapshot()["cancel"]["expensive_stage_started_after_cancel"])

    def test_storage_exception_is_secondary(self) -> None:
        class ExplodingStore:
            def write(self, trace):
                raise OSError("read only")

        trace = SearchTrace("job-safe", SearchRequest("товар"), store=ExplodingStore())
        trace.stage("collect")
        trace.finish_report(_report())
        self.assertEqual(trace.snapshot()["status"], "success")

    def test_summary_works_on_synthetic_traces_and_skips_corruption(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            success = SearchTrace("one", SearchRequest("ps5"), store=PilotTraceStore(path))
            success.record_cache("market", hit=True, age_seconds=12)
            success.finish_report(_report())
            empty = SearchTrace("two", SearchRequest("редкий товар"), store=PilotTraceStore(path))
            empty.finish_report(_report(final_results=0, estimated=True))
            (path / "broken.json").write_text("{", encoding="utf-8")

            traces, warnings = read_traces(path)
            result = summarize(traces)
            output = render(result)
            self.assertEqual(result["searches"], 2)
            self.assertEqual(result["statuses"]["success"], 1)
            self.assertEqual(result["statuses"]["empty"], 1)
            self.assertEqual(result["rates"]["success"], 0.5)
            self.assertEqual(result["searches_with_cache_hit"], 1)
            self.assertEqual(result["rows"][0]["apify_actual_usd"], 0.12)
            self.assertIsNone(result["rows"][0]["apify_estimated_usd"])
            self.assertIsNone(result["rows"][1]["apify_actual_usd"])
            self.assertEqual(result["rows"][1]["apify_estimated_usd"], 0.12)
            self.assertIn("LIVE PILOT SUMMARY", output)
            self.assertIn("AI checked", output)
            self.assertIn("50.0%", output)
            self.assertTrue(warnings)


if __name__ == "__main__":
    unittest.main()
