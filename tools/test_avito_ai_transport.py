"""Offline SSE regressions for the observed AITUNNEL connection that never ends."""
from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.normalization import normalize_listing


def event(content=None, reason=None, **extra):
    value = {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": reason}], **extra}
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()


class Stream:
    def __init__(self, chunks, *, clock=None):
        self.chunks = iter(chunks)
        self.timeouts = []
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=self.timeouts.append)))
        self.closed = False
        self.reads = 0
        self.clock = clock

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read1(self, _):
        self.reads += 1
        if self.clock:
            self.clock[0] += 1
        chunk = next(self.chunks, b"")
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def read(self):
        raise AssertionError("SSE must never read to EOF")


class AvitoAITransportTests(unittest.TestCase):
    def setUp(self):
        self.reviewer = OpenAICompatibleReviewer(ServiceConfig(
            ai_base_url="https://api.aitunnel.ru/v1", ai_api_key="offline-test-key",
            ai_model="qwen3.8-flash", ai_timeout_seconds=18,
        ))
        self.payload = {"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "Проверка"}], "max_tokens": 2400}

    def request(self, stream, payload=None):
        with patch("avito_service.ai.urlopen", return_value=stream) as opened:
            result = self.reviewer._request(payload or self.payload)
        return result, opened

    def test_stop_without_done_or_eof_returns_and_closes_connection(self):
        stream = Stream([event('{"ok":true}'), event("", "stop"), TimeoutError("never EOF")])
        result, opened = self.request(stream)
        self.assertEqual(self.reviewer._content(result), '{"ok":true}')
        self.assertTrue(stream.closed)
        self.assertLessEqual(stream.timeouts[-1], 0.5)
        self.assertGreater(stream.timeouts[-1], 0)
        sent = json.loads(opened.call_args.args[0].data)
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["stream_options"], {"include_usage": True})
        self.assertEqual(sent["reasoning"], {"effort": "none"})
        self.assertNotIn("stream", self.payload)
        self.assertTrue(result["usage"]["cost_estimated"])
        self.assertGreater(self.reviewer._cost_rub(result), 0)

    def test_separate_usage_trailer_is_preserved_without_eof(self):
        usage = b'data: {"choices":[],"usage":{"cost_rub":0.127,"prompt_tokens":123}}\n\n'
        stream = Stream([event("{}"), event("", "stop"), usage, AssertionError("must not read again")])
        result, _ = self.request(stream)
        self.assertEqual(result["usage"]["cost_rub"], 0.127)
        self.assertEqual(result["usage"]["prompt_tokens"], 123)
        self.assertNotIn("cost_estimated", result["usage"])
        self.assertEqual(stream.reads, 3)

    def test_429_retry_keeps_stream_deadline_without_worker_deadline(self):
        stream = Stream([event("{}", "stop"), TimeoutError("never EOF")])
        limited = HTTPError("https://api.aitunnel.ru/v1/chat/completions", 429, "rate limited", {"Retry-After": "0.1"}, None)
        with patch("avito_service.ai.urlopen", side_effect=[limited, stream]) as opened, patch("avito_service.ai.time.sleep"):
            result = self.reviewer._request(self.payload)
        self.assertEqual(self.reviewer._content(result), "{}")
        self.assertEqual(opened.call_count, 2)
        self.assertLessEqual(stream.timeouts[-1], 0.5)

    def test_transport_timeout_and_network_error_have_distinct_codes(self):
        for reason, expected in (
            (TimeoutError("timed out"), "AI_TIMEOUT"),
            (OSError("connection reset"), "AI_NETWORK_ERROR"),
        ):
            with self.subTest(expected=expected):
                with patch("avito_service.ai.urlopen", side_effect=URLError(reason)):
                    with self.assertRaises(ExternalServiceError) as caught:
                        self.reviewer._request(self.payload)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(
                    caught.exception.diagnostics["transport_error_type"],
                    type(reason).__name__,
                )

    def test_usage_on_stop_does_not_wait_for_another_event(self):
        stream = Stream([event("{}", "stop", usage={"cost_rub": 0.02}), AssertionError("must not read again")])
        result, _ = self.request(stream)
        self.assertEqual(self.reviewer._cost_rub(result), 0.02)
        self.assertEqual(stream.reads, 1)

    def test_utf8_and_crlf_events_can_split_at_any_byte(self):
        raw = b": heartbeat\r\n\r\n" + event('{"text":"Проверено"}', "stop").replace(b"\n", b"\r\n")
        stream = Stream([bytes([byte]) for byte in raw] + [b"data: [DONE]\n\n"])
        result, _ = self.request(stream)
        self.assertEqual(json.loads(self.reviewer._content(result)), {"text": "Проверено"})

    def test_eof_done_and_timeout_without_stop_never_accept_complete_json(self):
        for tail in (b"", b"data: [DONE]\n\n", TimeoutError("incomplete")):
            with self.subTest(tail=tail):
                with self.assertRaises(ExternalServiceError):
                    self.request(Stream([event('{"verdict":"approve"}'), tail]))

    def test_length_and_other_finish_reasons_reject_even_valid_json(self):
        for reason in ("length", "content_filter", "tool_calls", "error"):
            with self.subTest(reason=reason):
                with self.assertRaises(ExternalServiceError) as caught:
                    self.request(Stream([event('{"verdict":"approve"}', reason)]))
                self.assertEqual(caught.exception.code, "AI_INVALID_RESPONSE")

    def test_incomplete_terminal_event_is_not_a_finished_response(self):
        with self.assertRaises(ExternalServiceError):
            self.request(Stream([event("{}"), event("", "stop")[:-1], b""]))

    def test_provider_error_after_content_rejects(self):
        with self.assertRaises(ExternalServiceError) as caught:
            self.request(Stream([event("{}"), event("", "error", error={"code": 500, "message": "private provider details"})]))
        self.assertEqual(caught.exception.code, "AI_INVALID_RESPONSE")
        self.assertNotIn("private provider", str(caught.exception))

    def test_error_in_usage_trailer_is_not_hidden_by_stop(self):
        with self.assertRaises(ExternalServiceError):
            self.request(Stream([event("{}", "stop"), b'data: {"error":{"code":500}}\n\n']))

    def test_malformed_and_oversized_streams_are_rejected(self):
        for data in (b"data: {not json}\n\n", b"data: \xff\n\n", b"x" * 2_000_001):
            with self.subTest(length=len(data)):
                with self.assertRaises(ExternalServiceError) as caught:
                    self.request(Stream([data]))
                self.assertEqual(caught.exception.code, "AI_INVALID_RESPONSE")

    def test_trickle_cannot_extend_worker_deadline(self):
        clock = [100.0]
        self.reviewer.set_deadline(103.0)
        stream = Stream([b": heartbeat\n\n"] * 10, clock=clock)
        with patch("avito_service.ai.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(ExternalServiceError) as caught:
                self.request(stream)
        self.assertEqual(caught.exception.code, "AI_TIMEOUT")
        self.assertLessEqual(stream.reads, 3)
        self.assertEqual(stream.timeouts, [3.0, 2.0, 1.0])

    def test_missing_cost_photo_reserve_exceeds_same_text_request(self):
        text, _ = self.request(Stream([event("{}", "stop"), b""]))
        photo_payload = {**self.payload, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Проверка"},
            {"type": "image_url", "image_url": {"url": "https://example.invalid/photo.jpg", "detail": "high"}},
        ]}]}
        photo, _ = self.request(Stream([event("{}", "stop"), b""]), photo_payload)
        self.assertGreater(self.reviewer._cost_rub(photo), self.reviewer._cost_rub(text) + 1)

    def test_unknown_model_missing_usage_reserves_configured_budget(self):
        result, opened = self.request(Stream([event("{}", "stop"), b""]), {**self.payload, "model": "different-model"})
        self.assertEqual(self.reviewer._cost_rub(result), self.reviewer.config.ai_max_cost_rub)
        self.assertNotIn("reasoning", json.loads(opened.call_args.args[0].data))

    def test_existing_json_parser_still_rejects_invalid_completed_content(self):
        listing = normalize_listing({"id": "12345678", "title": "iPhone 14", "description": "Исправен", "price": 30_000})
        stream = Stream([event('{"text_analyzed":true', "stop"), b""])
        with patch("avito_service.ai.urlopen", return_value=stream):
            with self.assertRaises(ExternalServiceError):
                self.reviewer.review_text(listing)

    def test_successful_text_review_propagates_usage_cost(self):
        listing = normalize_listing({"id": "12345678", "title": "iPhone 14", "description": "Исправен", "price": 30_000})
        answer = json.dumps({"text_analyzed": True, "matches_request": True, "identified_model": "iPhone 14", "verdict": "caution", "confidence": 0.5})
        with patch("avito_service.ai.urlopen", return_value=Stream([event(answer, "stop", usage={"cost_rub": 0.08})])):
            review = self.reviewer.review_text(listing)
        self.assertEqual(review.cost_rub, 0.08)
        self.assertFalse(review.cost_estimated)
        self.assertEqual(review.request_count, 1)
        self.assertTrue(review.text_analyzed)

    def test_estimate_flag_survives_json_retry_and_batch_allocation(self):
        listings = tuple(normalize_listing({"id": item, "title": "iPhone 14", "description": "Исправен", "price": 30_000}) for item in ("12345678", "12345679"))
        answer = json.dumps({"reviews": [{
            "listing_id": item.listing_id, "text_analyzed": True,
            "matches_request": True, "identified_model": "iPhone 14",
            "confidence": 0.7, "verdict": "caution",
        } for item in listings]})
        invalid = Stream([event("bad JSON", "stop"), b""])
        valid = Stream([event(answer, "stop", usage={"cost_rub": 0.08})])
        with patch("avito_service.ai.urlopen", side_effect=[invalid, valid]):
            reviews = self.reviewer.review_text_batch(listings)
        self.assertTrue(all(review.cost_estimated for review in reviews))
        self.assertGreater(sum(review.cost_rub for review in reviews), 0.08)
        self.assertEqual(sum(review.request_count for review in reviews), 2)

    def test_photo_review_merges_estimated_cost_from_text_or_photo(self):
        from avito_service.models import AIReview, ReviewVerdict
        from dataclasses import replace
        listing = normalize_listing({"id": "12345678", "title": "iPhone 14", "description": "Исправен", "price": 30_000, "images": ["https://01.img.avito.st/photo.jpg"]})
        text = AIReview(listing_id=listing.listing_id, text_analyzed=True, photos_analyzed=False, matches_request=True, verdict=ReviewVerdict.CAUTION, cost_rub=0.1)
        answer = json.dumps({"text_analyzed": True, "photos_analyzed": True, "photo_coverage": [1], "matches_request": True, "verdict": "caution"})
        for text_estimated, photo_usage in ((False, {}), (True, {"cost_rub": 0.02})):
            with self.subTest(text_estimated=text_estimated):
                with patch("avito_service.ai.urlopen", return_value=Stream([event(answer, "stop", usage=photo_usage), b""])):
                    review = self.reviewer.review_photos(listing, replace(text, cost_estimated=text_estimated))
                self.assertTrue(review.cost_estimated)
                self.assertGreater(review.cost_rub, text.cost_rub)

    def test_other_hosts_keep_nonstream_transport_and_payload(self):
        for host in ("api.example.invalid", "api.aitunnel.ru.example.invalid"):
            with self.subTest(host=host):
                reviewer = OpenAICompatibleReviewer(ServiceConfig(ai_base_url=f"https://{host}/v1", ai_api_key="offline", ai_model="qwen3.8-flash"))
                response = SimpleNamespace(read=lambda: b'{"choices":[{"message":{"content":"{}"}}]}')
                with patch("avito_service.ai.urlopen") as opened:
                    opened.return_value.__enter__.return_value = response
                    result = reviewer._request(self.payload)
                self.assertEqual(reviewer._content(result), "{}")
                self.assertEqual(json.loads(opened.call_args.args[0].data), self.payload)


if __name__ == "__main__":
    unittest.main()
