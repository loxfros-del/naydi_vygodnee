"""No-network regressions for recovering existing paid Apify runs."""
from __future__ import annotations

import gzip
from http.client import IncompleteRead
from io import BytesIO
import json
import socket
import ssl
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.models import CollectionBatch, SearchRequest
from avito_service.normalization import normalize_dataset
from avito_service.spending import SpendingGuard
from avito_service.telemetry import SearchTrace


class Response:
    def __init__(self, value, *, compressed=False):
        data = json.dumps(value).encode("utf-8")
        self.data = gzip.compress(data) if compressed else data
        self.headers = {"Content-Encoding": "gzip"} if compressed else {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.data


def run_receipt(*, count=None, zero=False, status="SUCCEEDED"):
    run = {"id": "saved-run", "defaultDatasetId": "saved-data", "status": status, "usageTotalUsd": 0 if zero else 0.005}
    if count is not None:
        prices = {"listing-scraped": 0.00399, "detail-enriched": 0.00199,
                  "apify-default-dataset-item": 0.00001, "apify-actor-start": 0.005}
        run["pricingInfo"] = {"pricingPerEvent": {"actorChargeEvents": {
            name: {"eventPriceUsd": 0 if zero else price} for name, price in prices.items()
        }}}
        run["chargedEventCounts"] = {name: 1 if name == "apify-actor-start" else count for name in prices}
    return {"data": run}


def provider():
    return ZenStudioProvider(ServiceConfig(apify_token="never-print-this-token"))


def rows(count=1):
    return [{"id": 1000001 + index, "title": "iPhone 13", "url": f"https://www.avito.ru/moskva/telefony/iphone_{1000001 + index}"} for index in range(count)]


class AvitoTransportTests(unittest.TestCase):
    def test_start_http_failures_preserve_safe_diagnostics_and_release_only_definite_rejections(self):
        cases = (
            (400, "invalid-input", "APIFY_INVALID_INPUT", True),
            (401, "invalid-token", "APIFY_AUTH", True),
            (403, "missing-actor-rights", "APIFY_AUTH", True),
            (404, "actor-not-found", "APIFY_ACTOR_NOT_FOUND", True),
            (429, "rate-limit-exceeded", "APIFY_RATE_LIMIT", True),
            (503, "internal-server-error", "APIFY_HTTP_ERROR", False),
        )
        for status, provider_code, expected_code, definite in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                guard = SpendingGuard(Path(folder) / "spend.json")
                current = ZenStudioProvider(ServiceConfig(apify_token="never-print-this-token"), guard)
                trace = SearchTrace("failure", SearchRequest("PS5"))
                body = json.dumps({"error": {"type": provider_code,
                    "message": "never-print-this-token customer input"}}).encode()
                failure = HTTPError("https://api.apify.com/v2/acts/example/runs", status,
                                    "failed", {}, BytesIO(body))
                with patch("avito_service.apify.urlopen", side_effect=failure) as network:
                    with self.assertRaises(ExternalServiceError) as caught:
                        current.collect_market(SearchRequest("PS5"), max_charge_usd=0.10,
                                               telemetry=trace.record_apify)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(caught.exception.code, expected_code)
                run = trace.snapshot()["collection"]["apify_runs"][0]
                self.assertEqual(run["phase"], "start_run")
                self.assertEqual(run["http_status"], status)
                self.assertEqual(run["provider_error_code"], provider_code)
                self.assertEqual(run["actor_id"], "zen-studio/avito-listings-scraper")
                self.assertFalse(run["run_id_received"])
                self.assertGreaterEqual(run["start_run_duration_ms"], 0)
                self.assertNotIn("never-print-this-token", json.dumps(run))
                self.assertEqual(run["reservation_accounting"],
                                 "released_no_run" if definite else "settled_estimate")
                self.assertAlmostEqual(guard.snapshot()["dailyCommittedUsd"],
                                       0 if definite else 0.10)

    def test_network_denial_and_dns_failure_release_reservation_without_http_response(self):
        for failure, category in (
            (URLError(PermissionError(13, "Permission denied")), "network_permission_denied"),
            (URLError(socket.gaierror(11001, "name resolution failed")), "dns_resolution_failed"),
            (URLError(ConnectionRefusedError()), "connection_refused"),
            (URLError(ssl.SSLCertVerificationError()), "tls_certificate_failed"),
        ):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as folder:
                guard = SpendingGuard(Path(folder) / "spend.json")
                current = ZenStudioProvider(ServiceConfig(apify_token="never-print-this-token"), guard)
                events = []
                with patch("avito_service.apify.urlopen", side_effect=failure) as network:
                    with self.assertRaises(ExternalServiceError) as caught:
                        current.collect_market(SearchRequest("PS5"), max_charge_usd=0.10,
                                               telemetry=lambda event, data: events.append((event, data)))
                self.assertEqual(network.call_count, 1)
                self.assertEqual(caught.exception.code, "APIFY_UNAVAILABLE")
                record = events[-1][1]
                self.assertEqual(record["network_error_type"], category)
                self.assertIsNone(record["http_status"])
                self.assertFalse(record["request_reached_provider"])
                self.assertEqual(record["reservation_accounting"], "released_no_run")
                self.assertEqual(guard.snapshot()["dailyCommittedUsd"], 0)

    def test_uncertain_start_outcomes_keep_estimate_and_never_retry_post(self):
        malformed_json = Response({})
        malformed_json.data = b"{"
        for failure in (TimeoutError(), malformed_json, Response({"data": {}}),
                        Response({"unexpected": True})):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                guard = SpendingGuard(Path(folder) / "spend.json")
                current = ZenStudioProvider(ServiceConfig(apify_token="test"), guard)
                events = []
                with patch("avito_service.apify.urlopen", **(
                    {"side_effect": failure} if isinstance(failure, BaseException)
                    else {"return_value": failure})) as network:
                    with self.assertRaises(ExternalServiceError):
                        current.collect_market(SearchRequest("PS5"), max_charge_usd=0.10,
                                               telemetry=lambda event, data: events.append((event, data)))
                self.assertEqual(network.call_count, 1)
                self.assertFalse(events[-1][1].get("run_id_received"))
                self.assertEqual(events[-1][1]["reservation_accounting"], "settled_estimate")
                self.assertEqual(guard.snapshot()["settledEstimateUsd"], 0.10)

    def test_tls_eof_without_response_does_not_prove_paid_start_was_not_received(self):
        for failure in (ssl.SSLError(1, "TLS transport failed"),
                        ssl.SSLEOFError(8, "EOF occurred"),
                        URLError(ssl.SSLEOFError(8, "EOF occurred"))):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                guard = SpendingGuard(Path(folder) / "spend.json")
                current = ZenStudioProvider(ServiceConfig(apify_token="test"), guard)
                events = []
                with patch("avito_service.apify.urlopen", side_effect=failure) as network:
                    with self.assertRaises(ExternalServiceError):
                        current.collect_market(SearchRequest("PS5"), max_charge_usd=0.10,
                                               telemetry=lambda event, data: events.append((event, data)))
                self.assertEqual(network.call_count, 1)
                record = events[-1][1]
                self.assertEqual(record["network_error_type"], "tls_connection_failed")
                self.assertFalse(record["no_run_proven"])
                self.assertIsNone(record["request_reached_provider"])
                self.assertIsNone(record["http_status"])
                self.assertEqual(record["reservation_accounting"], "settled_estimate")
                self.assertAlmostEqual(guard.snapshot()["settledEstimateUsd"], 0.10)

    def test_response_read_failure_never_releases_paid_start_reservation(self):
        for status in (200, None):
            for failure in (ssl.SSLEOFError(8, "EOF occurred"), ssl.SSLCertVerificationError(),
                            PermissionError(13, "read failed"), IncompleteRead(b"partial")):
                with self.subTest(status=status, failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                    guard = SpendingGuard(Path(folder) / "spend.json")
                    current = ZenStudioProvider(ServiceConfig(apify_token="test"), guard)
                    response = Response({})
                    response.status = status
                    events = []
                    with patch.object(response, "read", side_effect=failure), \
                            patch("avito_service.apify.urlopen", return_value=response) as network:
                        with self.assertRaises(ExternalServiceError):
                            current.collect_market(SearchRequest("PS5"), max_charge_usd=0.10,
                                                   telemetry=lambda event, data: events.append((event, data)))
                    self.assertEqual(network.call_count, 1)
                    record = events[-1][1]
                    self.assertEqual(record["http_status"], status)
                    self.assertTrue(record["request_reached_provider"])
                    self.assertFalse(record["no_run_proven"])
                    self.assertEqual(record["reservation_accounting"], "settled_estimate")
                    self.assertAlmostEqual(guard.snapshot()["monthlyCommittedUsd"], 0.10)
                    self.assertAlmostEqual(guard.snapshot()["settledEstimateUsd"], 0.10)

    def test_candidate_search_prioritizes_relevance_before_full_price_ranking(self):
        for mode in ("bargain", "find"):
            with self.subTest(mode=mode):
                replies = [Response(run_receipt()), Response(rows()), Response(run_receipt(count=1))]
                request = SearchRequest("PlayStation 5", location="Ярославль", category="gaming",
                                        mode=mode, price_min=10_000, price_max=40_000)
                with patch("avito_service.apify.urlopen", side_effect=replies) as network:
                    provider().collect(request)
                payload = json.loads(network.call_args_list[0].args[0].data)
                self.assertEqual(payload["query"], "PlayStation 5")
                self.assertEqual(payload["location"], "Ярославль")
                self.assertEqual(payload["category"], "gaming")
                self.assertEqual(payload["sort"], "relevance")
                self.assertEqual((payload["priceMin"], payload["priceMax"]), (10_000, 40_000))
                self.assertTrue(payload["includeDetails"])
                self.assertNotIn("exactMatch", payload)
                self.assertNotIn("titleOnly", payload)

    def test_market_collection_keeps_exact_query_without_customer_price_limits(self):
        replies = [Response(run_receipt()), Response(rows()), Response(run_receipt(count=1))]
        request = SearchRequest("PlayStation 5", category="gaming", price_min=10_000, price_max=40_000)
        with patch("avito_service.apify.urlopen", side_effect=replies) as network:
            provider().collect_market(request)
        payload = json.loads(network.call_args_list[0].args[0].data)
        self.assertEqual(payload["query"], "PlayStation 5")
        self.assertEqual(payload["sort"], "relevance")
        self.assertNotIn("priceMin", payload)
        self.assertNotIn("priceMax", payload)
        self.assertEqual(request.price_max, 40_000)

    def test_refresh_fetches_exact_finalist_urls_without_replacement_search(self):
        raw = rows(2)
        listings = normalize_dataset(raw)
        self.assertEqual(len(listings), 2)
        replies = [Response(run_receipt()), Response(raw), Response(run_receipt(count=2))]
        with patch("avito_service.apify.urlopen", side_effect=replies) as network:
            provider().refresh(listings)
        payload = json.loads(network.call_args_list[0].args[0].data)
        self.assertEqual(payload["listingUrls"], [item["url"] for item in raw])
        self.assertEqual(payload["maxResults"], 2)
        self.assertTrue(payload["includeDetails"])
        for key in ("query", "sort", "category", "location"):
            self.assertNotIn(key, payload)

    def test_pilot_stage_labels_do_not_depend_on_actor_sort_order(self):
        from tools.run_avito_pilot import PilotProvider
        with tempfile.TemporaryDirectory() as folder:
            current = PilotProvider(ServiceConfig(), Path(folder))
            request = SearchRequest("PlayStation 5")
            with patch.object(ZenStudioProvider, "_collect_payload", return_value=CollectionBatch(())), \
                 patch.object(current, "_record_batch"):
                current.collect_market(request)
                current.collect(request)
                current.collect(SearchRequest("PlayStation 5", mode="find"))
                current.refresh(normalize_dataset(rows()))
            self.assertEqual([call["stage"] for call in current.calls],
                             ["market", "candidates", "candidates", "refresh"])

    def test_spending_limit_blocks_before_any_network_request(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = SpendingGuard(Path(folder) / "spend.json", daily_limit_usd=0.05)
            current = ZenStudioProvider(ServiceConfig(apify_token="test"), guard)
            with patch("avito_service.apify.urlopen") as network:
                with self.assertRaises(ExternalServiceError) as caught:
                    current.collect(SearchRequest("iPhone 13"), max_charge_usd=0.10)
            self.assertEqual(caught.exception.code, "APIFY_SPEND_LIMIT")
            network.assert_not_called()

    def test_complete_receipt_releases_unused_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = SpendingGuard(Path(folder) / "spend.json")
            current = ZenStudioProvider(ServiceConfig(apify_token="test"), guard)
            replies = [Response(run_receipt()), Response(rows()), Response(run_receipt(count=1))]
            with patch("avito_service.apify.urlopen", side_effect=replies):
                batch = current.collect(SearchRequest("iPhone 13"), max_charge_usd=0.10)
            self.assertAlmostEqual(guard.snapshot()["dailyCommittedUsd"], batch.apify_cost_usd)
            self.assertLess(batch.apify_cost_usd, 0.10)
            self.assertAlmostEqual(guard.snapshot()["actualSpendUsd"], batch.apify_cost_usd)
            self.assertEqual(guard.snapshot()["activeReservationUsd"], 0)

    def test_delayed_billing_keeps_full_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = SpendingGuard(Path(folder) / "spend.json")
            current = ZenStudioProvider(ServiceConfig(apify_token="test"), guard)
            replies = [Response(run_receipt()), Response(rows()), Response(run_receipt())]
            with patch("avito_service.apify.urlopen", side_effect=replies):
                current.collect(SearchRequest("iPhone 13"), max_charge_usd=0.10)
            self.assertAlmostEqual(guard.snapshot()["dailyCommittedUsd"], 0.10)
            self.assertAlmostEqual(guard.snapshot()["settledEstimateUsd"], 0.10)
            self.assertEqual(guard.snapshot()["activeReservationUsd"], 0)

    def test_ambiguous_paid_start_keeps_reservation_across_new_instances(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder) / "spend.json"
            current = ZenStudioProvider(ServiceConfig(apify_token="test"), SpendingGuard(ledger))
            with patch("avito_service.apify.urlopen", side_effect=TimeoutError()) as network:
                with self.assertRaises(ExternalServiceError):
                    current.collect(SearchRequest("iPhone 13"), max_charge_usd=0.10)
            self.assertEqual(network.call_count, 1)
            self.assertAlmostEqual(SpendingGuard(ledger).snapshot()["dailyCommittedUsd"], 0.10)
            snapshot = SpendingGuard(ledger).snapshot()
            self.assertAlmostEqual(snapshot["settledEstimateUsd"], 0.10)
            self.assertEqual(snapshot["activeReservationUsd"], 0)

    def test_production_factory_uses_shared_ledger_and_per_search_cap(self):
        from avito_service.http_api import build_service
        current = build_service(ServiceConfig(apify_max_charge_usd=2, report_max_cost_rub=250))
        self.assertEqual(current.provider.config.apify_max_charge_usd, 2.0)
        collection_budget = (current.report_max_cost_rub - current.ai_max_cost_rub) / current.usd_rub_rate
        self.assertGreaterEqual(collection_budget, 1.0)
        self.assertEqual(current.provider.spending_guard.daily_limit_units, 300_000_000)
        self.assertEqual(current.provider.spending_guard.monthly_limit_units, 1_800_000_000)
        self.assertEqual(current.provider.spending_guard.path.name, "avito_spend.json")
        self.assertEqual(current.provider.spending_guard.billing_cycle_day, 9)

    def test_production_profile_expands_ai_without_reducing_collection_budget(self):
        from avito_service.http_api import build_service
        from avito_service.config import expanded_review_config
        source = ServiceConfig(ai_max_cost_rub=15, ai_timeout_seconds=18, report_max_cost_rub=150)
        with patch("avito_service.http_api.load_config", return_value=source):
            current = build_service()
        config = current.provider.config
        self.assertEqual(config, expanded_review_config(source))
        self.assertEqual(current.ai_max_cost_rub, 15)
        self.assertEqual(config.ai_timeout_seconds, 90)
        self.assertEqual(current.ai_concurrency, 4)
        self.assertEqual(current.ai_text_max_listings, 60)
        self.assertEqual(current.ai_max_listings, 20)
        self.assertGreaterEqual((current.report_max_cost_rub - current.ai_max_cost_rub) / current.usd_rub_rate, 1)

    def test_normal_server_entrypoint_uses_expanded_production_profile(self):
        from avito_service.http_api import make_server
        from avito_service.config import expanded_review_config
        source = ServiceConfig(ai_max_cost_rub=15, ai_timeout_seconds=18)
        with patch("avito_service.http_api.load_config", return_value=source) as loader:
            with make_server("127.0.0.1", 0, owner_token="", access_token="") as server:
                self.assertEqual(server.service_config, expanded_review_config(source))
                self.assertEqual(server.analysis_service.provider.config, server.service_config)
                self.assertEqual(server.analysis_service.ai_max_cost_rub, 15)
                self.assertEqual(server.analysis_service.ai_text_max_listings, 60)
                self.assertEqual(server.analysis_service.ai_max_listings, 20)
                self.assertEqual(server.analysis_service.ai_concurrency, 4)
                self.assertEqual(server.service_config.ai_timeout_seconds, 90)
        loader.assert_called_once_with()

    def test_server_preserves_an_explicit_library_config(self):
        from avito_service.http_api import make_server
        explicit = ServiceConfig(ai_max_cost_rub=12, ai_timeout_seconds=18,
                                 ai_text_max_listings=25, ai_max_listings=7, ai_concurrency=2)
        with patch("avito_service.http_api.load_config") as loader:
            with make_server("127.0.0.1", 0, config=explicit, owner_token="", access_token="") as server:
                self.assertIs(server.service_config, explicit)
                self.assertEqual(server.analysis_service.ai_max_cost_rub, 12)
                self.assertEqual(server.analysis_service.ai_text_max_listings, 25)
                self.assertEqual(server.analysis_service.ai_max_listings, 7)
                self.assertEqual(server.analysis_service.ai_concurrency, 2)
                self.assertEqual(server.service_config.ai_timeout_seconds, 18)
        loader.assert_not_called()

    def test_dataset_timeout_retries_same_get_and_decodes_gzip(self):
        replies = [Response(run_receipt()), TimeoutError(), Response(rows(), compressed=True), Response(run_receipt(count=1))]
        with patch("avito_service.apify.urlopen", side_effect=replies) as network, patch("avito_service.apify.time.sleep"):
            batch = provider().collect(SearchRequest("iPhone 13"), deadline_at=time.monotonic() + 90)
        calls = [call.args[0] for call in network.call_args_list]
        self.assertEqual(sum(call.method == "POST" for call in calls), 1)
        dataset_calls = [call for call in calls if "/datasets/" in call.full_url]
        self.assertEqual(len(dataset_calls), 2)
        self.assertEqual(dataset_calls[0].full_url, dataset_calls[1].full_url)
        self.assertIn("clean=true&format=json&limit=", dataset_calls[0].full_url)
        self.assertEqual(dataset_calls[0].get_header("Accept-encoding"), "gzip")
        self.assertEqual(len(batch.items), 1)

    def test_exhausted_dataset_reads_preserve_private_paid_run_receipt(self):
        replies = [Response(run_receipt()), URLError("unsafe provider echo")] + [TimeoutError()] * 2
        with patch("avito_service.apify.urlopen", side_effect=replies) as network, patch("avito_service.apify.time.sleep"):
            with self.assertRaises(ExternalServiceError) as caught:
                provider().collect(SearchRequest("iPhone 13"))
        context = caught.exception.apify_context
        self.assertEqual(context["run_id"], "saved-run")
        self.assertEqual(context["dataset_id"], "saved-data")
        self.assertEqual(context["actual_cost_usd"], 0.005)
        self.assertFalse(context["billing_complete"])
        self.assertGreater(context["reserved_max_cost_usd"], 0)
        self.assertEqual(network.call_count, 4)
        public = json.dumps(caught.exception.public_dict())
        self.assertNotIn("saved-run", public)
        self.assertNotIn("never-print-this-token", public)
        self.assertNotIn("unsafe provider echo", public)

    def test_post_failure_is_never_retried(self):
        with patch("avito_service.apify.urlopen", side_effect=TimeoutError()) as network:
            with self.assertRaises(ExternalServiceError):
                provider().collect(SearchRequest("iPhone 13"))
        self.assertEqual(network.call_count, 1)

    def test_auth_error_stops_without_retry_and_keeps_run_receipt(self):
        replies = [Response(run_receipt()), HTTPError("https://api.apify.com", 403, "Forbidden", {}, None)]
        with patch("avito_service.apify.urlopen", side_effect=replies) as network:
            with self.assertRaises(ExternalServiceError) as caught:
                provider().collect(SearchRequest("iPhone 13"))
        self.assertEqual(network.call_count, 2)
        self.assertEqual(caught.exception.code, "APIFY_AUTH")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.apify_context["run_id"], "saved-run")

    def test_retry_attempts_share_the_deadline(self):
        clock = [0.0]
        def fail(request, *, timeout):
            clock[0] += timeout
            raise TimeoutError()
        def sleep(seconds):
            clock[0] += seconds
        with patch("avito_service.apify.time.monotonic", side_effect=lambda: clock[0]), patch("avito_service.apify.time.sleep", side_effect=sleep), patch("avito_service.apify.urlopen", side_effect=fail) as network:
            with self.assertRaises(ExternalServiceError):
                provider()._json_request("GET", "https://api.apify.com/v2/datasets/saved/items", timeout=45)
        self.assertEqual(network.call_count, 3)
        self.assertLessEqual(clock[0], 45)
        self.assertTrue(all(call.kwargs["timeout"] <= 20 for call in network.call_args_list))

    def test_late_event_billing_replaces_start_only_cost(self):
        replies = [Response(run_receipt()), Response(rows(21)), Response(run_receipt(count=21))]
        with patch("avito_service.apify.urlopen", side_effect=replies):
            batch = provider().collect(SearchRequest("iPhone 13", max_results=21))
        self.assertAlmostEqual(batch.apify_cost_usd, 0.13079)
        self.assertFalse(batch.apify_cost_estimated)

    def test_incomplete_billing_uses_conservative_estimate(self):
        replies = [Response(run_receipt()), Response(rows(21)), Response(run_receipt())]
        current = provider()
        with patch("avito_service.apify.urlopen", side_effect=replies):
            batch = current.collect(SearchRequest("iPhone 13", max_results=21))
        self.assertAlmostEqual(batch.apify_cost_usd, 21 * current.config.apify_full_listing_cost_usd + current.config.apify_start_cost_usd)
        self.assertTrue(batch.apify_cost_estimated)

    def test_poll_failure_keeps_existing_running_run_identity(self):
        replies = [Response(run_receipt(status="RUNNING"))] + [TimeoutError()] * 3
        with patch("avito_service.apify.urlopen", side_effect=replies) as network, patch("avito_service.apify.time.sleep"):
            with self.assertRaises(ExternalServiceError) as caught:
                provider().collect(SearchRequest("iPhone 13"))
        self.assertEqual(caught.exception.apify_context["status"], "RUNNING")
        self.assertEqual(caught.exception.apify_context["run_id"], "saved-run")
        self.assertEqual(sum(call.args[0].method == "POST" for call in network.call_args_list), 1)

    def test_explicit_complete_zero_cost_is_not_replaced_with_estimate(self):
        replies = [Response(run_receipt(zero=True)), Response([]), Response(run_receipt(count=0, zero=True))]
        with patch("avito_service.apify.urlopen", side_effect=replies):
            batch = provider().collect(SearchRequest("iPhone 13"))
        self.assertEqual(batch.apify_cost_usd, 0)
        self.assertFalse(batch.apify_cost_estimated)

    def test_actor_query_adds_requested_storage_without_mutating_request(self):
        request = SearchRequest("iPhone 13", required_storage="128 ГБ")
        current = provider()
        self.assertEqual(current._actor_input(request)["query"], "iPhone 13 128 ГБ")
        self.assertEqual(request.query, "iPhone 13")
        self.assertEqual(current._actor_input(SearchRequest("iPhone 13 128GB", required_storage="128 ГБ"))["query"], "iPhone 13 128GB")


if __name__ == "__main__":
    unittest.main()
