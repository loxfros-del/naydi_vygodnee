"""Offline recovery of the incomplete JSON actually returned by the provider."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.models import ReviewVerdict, SearchRequest
from tools.test_avito_model_costs import listing


def valid(item, **changes):
    return {"listing_id": item.listing_id, "text_analyzed": True, "matches_request": True,
        "identified_model": "PlayStation 5 Slim", "condition": "Хорошее", "storage": "1TB",
        "sim_variant": "", "mismatch_reason": "", "description_findings": ["Описание прочитано."],
        "defects": [], "price_conditions": [], "conflicts": [], "verdict": "approve", "confidence": .9, **changes}


def incomplete(item):
    # Observed provider shape: useful findings but no analysis acknowledgment,
    # identified model or confidence. These fields cannot be inferred by code.
    return {"listing_id": item.listing_id, "matches_request": True, "condition": "Хорошее",
            "description_findings": ["Полный комплект"], "verdict": "approve"}


def response(body, cost=.2, estimated=False):
    return {"choices": [{"message": {"content": json.dumps(body)}}],
            "usage": {"cost_rub": cost, "cost_estimated": estimated}}


class TextRetryTests(unittest.TestCase):
    def setUp(self):
        self.reviewer = OpenAICompatibleReviewer(ServiceConfig(ai_model="qwen3.8-flash"))
        self.items = (listing(), replace(listing(), listing_id="1000002"))
        self.search = SearchRequest("PS5")

    def test_single_incomplete_json_is_reanalyzed_once_with_an_explicit_instruction(self):
        payloads = []
        responses = iter((response(incomplete(self.items[0]), estimated=True), response(valid(self.items[0]), .3)))
        def transport(payload):
            payloads.append(deepcopy(payload))
            return next(responses)
        with patch.object(self.reviewer, "_request", side_effect=transport):
            result = self.reviewer.review_text(self.items[0], self.search)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["messages"], payloads[1]["messages"][:-1])
        self.assertIn("Выполни полный анализ заново", payloads[1]["messages"][-1]["content"])
        self.assertTrue(result.text_analyzed)
        self.assertEqual(result.error, "")
        self.assertEqual(result.verdict, ReviewVerdict.APPROVE)
        self.assertAlmostEqual(result.cost_rub, .5)
        self.assertTrue(result.cost_estimated)
        self.assertEqual(result.request_count, 2)

    def test_missing_mandatory_fields_retry_without_promoting_omitted_values(self):
        for field in ("matches_request", "identified_model", "confidence"):
            with self.subTest(field=field):
                malformed = valid(self.items[0])
                del malformed[field]
                with patch.object(self.reviewer, "_request", side_effect=[response(malformed), response(valid(self.items[0]))]) as transport:
                    result = self.reviewer.review_text(self.items[0], self.search)
                self.assertEqual(transport.call_count, 2)
                self.assertEqual(result.error, "")

    def test_twice_incomplete_stays_unverified_and_preserves_both_charges(self):
        for batch in (False, True):
            with self.subTest(batch=batch):
                body = {"reviews": [incomplete(item) for item in self.items]} if batch else incomplete(self.items[0])
                with patch.object(self.reviewer, "_request", return_value=response(body, .2)) as transport:
                    results = self.reviewer.review_text_batch(self.items, self.search) if batch else (self.reviewer.review_text(self.items[0], self.search),)
                self.assertEqual(transport.call_count, 6 if batch else 2)
                self.assertTrue(all(not item.text_analyzed and item.error for item in results))
                self.assertTrue(all(item.verdict is ReviewVerdict.CAUTION for item in results))
                self.assertAlmostEqual(sum(item.cost_rub for item in results), 1.2 if batch else .4)
                self.assertEqual(sum(item.request_count for item in results), 6 if batch else 2)

    def test_partial_batch_retries_only_incomplete_and_keeps_valid_rejection(self):
        first = response({"reviews": [valid(self.items[0], verdict="reject", matches_request=False,
            mismatch_reason="Другая модель"), incomplete(self.items[1])]})
        second = response({"reviews": [valid(item) for item in self.items]}, .3)
        with patch.object(self.reviewer, "_request", side_effect=[first, second]) as transport:
            results = self.reviewer.review_text_batch(self.items, self.search)
        self.assertEqual(transport.call_count, 2)
        sent_again = transport.call_args_list[1].args[0]["messages"][1]["content"]
        self.assertNotIn('"id": "1000001"', sent_again)
        self.assertIn('"id": "1000002"', sent_again)
        self.assertEqual(results[0].verdict, ReviewVerdict.REJECT)
        self.assertFalse(results[0].matches_request)
        self.assertEqual(results[1].verdict, ReviewVerdict.APPROVE)
        self.assertTrue(all(item.text_analyzed for item in results))
        self.assertAlmostEqual(sum(item.cost_rub for item in results), .5)
        self.assertEqual(sum(item.request_count for item in results), 2)

    def test_auth_and_quota_never_retry(self):
        for code in ("AI_AUTH", "AI_QUOTA"):
            for batch in (False, True):
                with self.subTest(code=code, batch=batch):
                    with patch.object(self.reviewer, "_request", side_effect=ExternalServiceError("Unavailable", code=code)) as transport:
                        with self.assertRaises(ExternalServiceError) as caught:
                            if batch:
                                self.reviewer.review_text_batch(self.items, self.search)
                            else:
                                self.reviewer.review_text(self.items[0], self.search)
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(transport.call_count, 1)

    def test_timeout_batch_retries_and_splits_with_a_hard_limit(self):
        with patch.object(
            self.reviewer, "_request",
            side_effect=ExternalServiceError("Timeout", code="AI_TIMEOUT"),
        ) as transport:
            results = self.reviewer.review_text_batch(self.items, self.search)
        self.assertEqual(transport.call_count, 6)
        self.assertTrue(all(not review.text_analyzed and review.error for review in results))

    def test_truncated_json_salvages_complete_prefix(self):
        first = json.dumps(valid(self.items[0]), ensure_ascii=False)
        truncated = '{"reviews":[' + first + ',{"listing_id":"1000002"'
        parsed = self.reviewer.parse_text_batch_result(self.items, truncated)
        self.assertTrue(parsed.reviews[0].text_analyzed)
        self.assertFalse(parsed.reviews[1].text_analyzed)
        self.assertEqual(parsed.missing_ids, ("1000002",))
        self.assertEqual(parsed.parse_error, "truncated_batch_json")

    def test_single_listing_batch_emits_packet_telemetry(self):
        packets = []
        self.reviewer.set_packet_telemetry(packets.append)
        with patch.object(self.reviewer, "_request", return_value=response(valid(self.items[0]))):
            results = self.reviewer.review_text_batch((self.items[0],), self.search)
        self.assertTrue(results[0].text_analyzed)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["listing_ids"], [self.items[0].listing_id])
        self.assertEqual(packets[0]["batch_size"], 1)
        self.assertEqual(packets[0]["returned_result_count"], 1)

    def test_missing_duplicate_unknown_and_invalid_items_are_structural_errors(self):
        third = replace(listing(), listing_id="1000003")
        body = {"reviews": [
            valid(self.items[0]),
            valid(self.items[0]),
            {"listing_id": "unknown", "text_analyzed": True},
            "invalid",
        ]}
        parsed = self.reviewer.parse_text_batch_result((*self.items, third), json.dumps(body))
        self.assertEqual(parsed.duplicate_ids, ("1000001",))
        self.assertEqual(parsed.unknown_ids, ("unknown",))
        self.assertEqual(parsed.missing_ids, ("1000002", "1000003"))
        self.assertEqual(parsed.invalid_items, 1)
        self.assertTrue(all(review.error for review in parsed.reviews))

    def test_repeated_bad_batch_splits_two_and_three_then_salvages_all(self):
        items = tuple(replace(listing(), listing_id=str(2_000_000 + index)) for index in range(5))
        packets = []
        self.reviewer.set_packet_telemetry(packets.append)

        def transport(payload):
            content = payload["messages"][1]["content"]
            active = tuple(item for item in items if f'"id": "{item.listing_id}"' in content)
            if len(packets) < 2:
                return response({"reviews": [incomplete(item) for item in active]})
            return response({"reviews": [valid(item) for item in active]})

        with patch.object(self.reviewer, "_request", side_effect=transport) as wire:
            results = self.reviewer.review_text_batch(items, self.search)
        self.assertEqual(wire.call_count, 4)
        self.assertTrue(all(review.text_analyzed for review in results))
        self.assertEqual([packet["batch_size"] for packet in packets], [5, 5, 2, 3])
        self.assertTrue(packets[1]["will_split"])

    def test_one_invalid_listing_does_not_poison_four_valid_ones(self):
        items = tuple(replace(listing(), listing_id=str(3_000_000 + index)) for index in range(5))
        bad_id = items[-1].listing_id

        def transport(payload):
            content = payload["messages"][1]["content"]
            active = tuple(item for item in items if f'"id": "{item.listing_id}"' in content)
            return response({"reviews": [
                incomplete(item) if item.listing_id == bad_id else valid(item)
                for item in active
            ]})

        with patch.object(self.reviewer, "_request", side_effect=transport) as wire:
            results = self.reviewer.review_text_batch(items, self.search)
        self.assertEqual(sum(review.text_analyzed for review in results), 4)
        self.assertFalse(results[-1].text_analyzed)
        self.assertEqual(wire.call_count, 4)

    def test_json_compatibility_retry_and_incomplete_retry_share_two_call_limit(self):
        bad_json = {"choices": [{"message": {"content": "{invalid"}}], "usage": {"cost_rub": .2}}
        with patch.object(self.reviewer, "_request", side_effect=[bad_json, response(incomplete(self.items[0]), .3)]) as transport:
            result = self.reviewer.review_text(self.items[0], self.search)
        self.assertEqual(transport.call_count, 2)
        self.assertFalse(result.text_analyzed)
        self.assertTrue(result.error)
        self.assertAlmostEqual(result.cost_rub, .5)


if __name__ == "__main__":
    unittest.main()
