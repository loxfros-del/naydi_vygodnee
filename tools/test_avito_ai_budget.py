"""Offline per-wire AI reservations, including retries and concurrent photos."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
import unittest
from unittest.mock import patch

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.config import ServiceConfig
from avito_service.errors import ConfigurationError, ExternalServiceError
from tools.test_avito_model_costs import listing
from tools.test_avito_text_retry import incomplete, response, valid


class AIBudgetTests(unittest.TestCase):
    def setUp(self):
        self.reviewer = OpenAICompatibleReviewer(ServiceConfig(
            ai_model="gpt-4.1-mini", ai_api_key="offline-key",
            ai_base_url="https://api.aitunnel.ru/v1", ai_max_cost_rub=50,
        ))
        self.payload = self.reviewer.build_text_payload(listing(), "gpt-4.1-mini")

    def test_large_photo_payload_is_refused_before_post(self):
        item = replace(listing(), images=tuple(f"https://10.img.avito.st/photo{i}.jpg" for i in range(20)))
        text = self.reviewer.parse_text_review(item, __import__("json").dumps(valid(item)))
        payload = self.reviewer.build_photo_payload(item, "gpt-4.1-mini", text)
        self.assertGreater(self.reviewer.estimate_payload_cost(payload), 50)
        self.reviewer.begin_budget(50)
        with patch("avito_service.ai.urlopen") as wire:
            with self.assertRaises(ExternalServiceError) as caught:
                self.reviewer._request(payload)
        self.assertEqual(caught.exception.code, "AI_BUDGET")
        self.assertFalse(caught.exception.retryable)
        wire.assert_not_called()

    def test_receipt_releases_unused_reserve(self):
        self.reviewer.begin_budget(10)
        with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                patch.object(self.reviewer, "_request_unbudgeted", return_value=response({}, 2)) as wire:
            for _ in range(3):
                self.reviewer._request(self.payload)
            with self.assertRaises(ExternalServiceError) as caught:
                self.reviewer._request(self.payload)
        self.assertEqual(wire.call_count, 3)
        self.assertEqual(caught.exception.code, "AI_BUDGET")
        snapshot = self.reviewer.budget_snapshot()
        self.assertEqual(snapshot["active_reservations"], 0)
        self.assertEqual(snapshot["committed_rub"], 6.0)

    def test_budget_block_is_distinct_from_spend_and_active_reservation(self):
        self.reviewer.begin_budget(5)
        with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                patch.object(self.reviewer, "_request_unbudgeted") as wire:
            with self.assertRaises(ExternalServiceError) as caught:
                self.reviewer._request(self.payload)
        self.assertEqual(caught.exception.code, "AI_BUDGET")
        self.assertTrue(caught.exception.diagnostics["reservation_blocked"])
        self.assertEqual(caught.exception.diagnostics["accounted_cost_rub"], 0.0)
        self.assertEqual(self.reviewer.budget_snapshot()["active_reservations"], 0)
        self.assertEqual(self.reviewer.budget_snapshot()["committed_rub"], 0.0)
        wire.assert_not_called()

    def test_completed_estimate_releases_unused_reserve_and_has_no_active_hold(self):
        self.reviewer.begin_budget(10)
        estimated = response({}, 2, estimated=True)
        with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                patch.object(self.reviewer, "_request_unbudgeted", return_value=estimated):
            result = self.reviewer._request(self.payload)
        self.assertEqual(result["_budget"]["reservation_state"], "settled_estimate")
        self.assertEqual(result["_budget"]["reservation_rub"], 6)
        self.assertEqual(result["_budget"]["accounted_cost_rub"], 2)
        self.assertEqual(self.reviewer.budget_snapshot()["committed_rub"], 2.0)
        self.assertEqual(self.reviewer.budget_snapshot()["active_reservations"], 0)

    def test_failure_holds_reserve_and_next_search_can_reset_it(self):
        self.reviewer.begin_budget(10)
        with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                patch.object(self.reviewer, "_request_unbudgeted", side_effect=[
                    ExternalServiceError("Timeout", code="AI_TIMEOUT"), response({}, 2)]) as wire:
            with self.assertRaises(ExternalServiceError):
                self.reviewer._request(self.payload)
            with self.assertRaises(ExternalServiceError) as caught:
                self.reviewer._request(self.payload)
            self.assertEqual(caught.exception.code, "AI_BUDGET")
            self.assertEqual(wire.call_count, 1)
            self.reviewer.begin_budget(10)
            self.reviewer._request(self.payload)
            self.assertEqual(wire.call_count, 2)

    def test_absent_or_invalid_receipt_is_not_free(self):
        for reply in ({"choices": []}, {"usage": {"cost_rub": "NaN"}}, {"usage": None}):
            with self.subTest(reply=reply):
                self.reviewer.begin_budget(10)
                with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                        patch.object(self.reviewer, "_request_unbudgeted", return_value=reply) as wire:
                    result = self.reviewer._request(self.payload)
                    self.assertEqual(result["usage"]["cost_rub"], 6)
                    self.assertTrue(result["usage"]["cost_estimated"])
                    with self.assertRaises(ExternalServiceError):
                        self.reviewer._request(self.payload)
                    self.assertEqual(wire.call_count, 1)

    def test_concurrent_reservations_wait_for_receipt_and_never_overlap_without_room(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                self.reviewer.begin_budget(10)
                first_started, release, waiting = Event(), Event(), Event()
                calls = []
                original_wait = self.reviewer._budget_condition.wait
                def wait_for_reserve(timeout):
                    waiting.set()
                    return original_wait(timeout)
                def transport(payload):
                    calls.append(payload)
                    if len(calls) == 1:
                        first_started.set()
                        if not release.wait(3):
                            raise AssertionError("test did not release first request")
                        if failed:
                            raise ExternalServiceError("Uncertain charge", code="AI_TIMEOUT")
                    return response({}, 2)
                with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                        patch.object(self.reviewer, "_request_unbudgeted", side_effect=transport), \
                        patch.object(self.reviewer._budget_condition, "wait", side_effect=wait_for_reserve), \
                        ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(self.reviewer._request, self.payload)
                    self.assertTrue(first_started.wait(1))
                    second = pool.submit(self.reviewer._request, self.payload)
                    try:
                        self.assertTrue(waiting.wait(1))
                        self.assertEqual(len(calls), 1)
                        with self.assertRaises(ConfigurationError):
                            self.reviewer.begin_budget(50)
                    finally:
                        release.set()
                    if failed:
                        with self.assertRaises(ExternalServiceError):
                            first.result(2)
                        with self.assertRaises(ExternalServiceError) as caught:
                            second.result(2)
                        self.assertEqual(caught.exception.code, "AI_BUDGET")
                        self.assertEqual(len(calls), 1)
                    else:
                        self.assertEqual(first.result(2)["usage"]["cost_rub"], 2)
                        self.assertEqual(second.result(2)["usage"]["cost_rub"], 2)
                        self.assertEqual(len(calls), 2)

    def test_wait_for_concurrent_reserve_respects_timeout(self):
        self.reviewer.begin_budget(10)
        first_started, release = Event(), Event()
        def transport(payload):
            first_started.set()
            release.wait(3)
            return response({}, 2)
        with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                patch.object(self.reviewer, "_request_timeout", return_value=.03), \
                patch.object(self.reviewer, "_request_unbudgeted", side_effect=transport) as wire, \
                ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.reviewer._request, self.payload)
            self.assertTrue(first_started.wait(1))
            try:
                with self.assertRaises(ExternalServiceError) as caught:
                    self.reviewer._request(self.payload)
                self.assertEqual(caught.exception.code, "AI_TIMEOUT")
                self.assertEqual(wire.call_count, 1)
            finally:
                release.set()
            first.result(2)

    def test_incomplete_review_retry_requires_its_own_reserve(self):
        self.reviewer.begin_budget(1.2)
        with patch.object(self.reviewer, "estimate_payload_cost", return_value=1), \
                patch.object(self.reviewer, "_request_unbudgeted", return_value=response(incomplete(listing()), .9)) as wire:
            with self.assertRaises(ExternalServiceError) as caught:
                self.reviewer.review_text(listing())
        self.assertEqual(caught.exception.code, "AI_BUDGET")
        self.assertEqual(wire.call_count, 1)

    def test_receipt_above_estimate_blocks_all_further_work(self):
        self.reviewer.begin_budget(10)
        with patch.object(self.reviewer, "estimate_payload_cost", return_value=6), \
                patch.object(self.reviewer, "_request_unbudgeted", return_value=response({}, 11)) as wire:
            self.assertEqual(self.reviewer._request(self.payload)["usage"]["cost_rub"], 11)
            with self.assertRaises(ExternalServiceError):
                self.reviewer._request(self.payload)
            self.assertEqual(wire.call_count, 1)

    def test_unset_budget_keeps_standalone_transport_unchanged(self):
        reply = {"choices": []}
        with patch.object(self.reviewer, "_request_unbudgeted", return_value=reply):
            self.assertIs(self.reviewer._request(self.payload), reply)


if __name__ == "__main__":
    unittest.main()
