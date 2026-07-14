from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import net_client


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", url: str = "https://example.test/item"):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class NetClientCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        net_client.clear_http_cache(clear_rate_limits=True)
        self.policy = net_client.DomainPolicy(min_delay=0, max_retries=1)

    def tearDown(self) -> None:
        net_client.clear_http_cache(clear_rate_limits=True)

    def test_success_response_is_cached(self) -> None:
        with patch.object(net_client, "get_domain_policy", return_value=self.policy), patch.object(
            net_client.requests, "get", return_value=FakeResponse()
        ) as get:
            first = net_client.fetch_http("https://example.test/item")
            second = net_client.fetch_http("https://example.test/item")
        self.assertTrue(first.ok)
        self.assertEqual(second.fetch_provider, "http_cache")
        self.assertEqual(get.call_count, 1)

    def test_blocked_negative_cache_has_short_ttl(self) -> None:
        clock = [100.0]
        with patch.object(net_client, "get_domain_policy", return_value=self.policy), patch.object(
            net_client.time, "monotonic", side_effect=lambda: clock[0]
        ), patch.object(net_client.requests, "get", return_value=FakeResponse(403, "captcha")) as get:
            first = net_client.fetch_http("https://example.test/blocked")
            cached = net_client.fetch_http("https://example.test/blocked")
            clock[0] += net_client.HTTP_CACHE_BLOCKED_TTL_SECONDS + 0.1
            expired = net_client.fetch_http("https://example.test/blocked")
        self.assertEqual(first.blocked_reason, "forbidden")
        self.assertEqual(cached.fetch_provider, "http_cache")
        self.assertEqual(expired.fetch_provider, "http")
        self.assertEqual(get.call_count, 2)

    def test_cache_is_lru_bounded(self) -> None:
        with patch.object(net_client, "HTTP_CACHE_MAX_ENTRIES", 2), patch.object(
            net_client, "get_domain_policy", return_value=self.policy
        ), patch.object(net_client.requests, "get", side_effect=lambda url, **kwargs: FakeResponse(url=url)):
            net_client.fetch_http("https://example.test/1")
            net_client.fetch_http("https://example.test/2")
            net_client.fetch_http("https://example.test/3")
        self.assertEqual(len(net_client._CACHE), 2)
        self.assertNotIn("https://example.test/1", net_client._CACHE)

    def test_403_and_429_are_never_retried(self) -> None:
        for status in (403, 429):
            net_client.clear_http_cache()
            with self.subTest(status=status), patch.object(
                net_client, "get_domain_policy", return_value=self.policy
            ), patch.object(net_client.requests, "get", return_value=FakeResponse(status)) as get:
                result = net_client.fetch_http(f"https://example.test/{status}", retries=1)
                self.assertTrue(result.blocked)
                self.assertEqual(get.call_count, 1)

    def test_transient_500_uses_exponential_backoff_and_one_retry(self) -> None:
        responses = [FakeResponse(500), FakeResponse(200, "recovered")]
        with patch.object(net_client, "get_domain_policy", return_value=self.policy), patch.object(
            net_client.requests, "get", side_effect=responses
        ) as get, patch.object(net_client.time, "sleep") as sleep:
            result = net_client.fetch_http("https://example.test/transient")
        self.assertTrue(result.ok)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(net_client.HTTP_RETRY_BACKOFF_BASE_SECONDS)

    def test_backoff_is_exponential_and_negative_ttls_are_short(self) -> None:
        self.assertEqual(net_client._retry_backoff_seconds(0), 0.5)
        self.assertEqual(net_client._retry_backoff_seconds(1), 1.0)
        self.assertEqual(net_client._retry_backoff_seconds(2), 2.0)
        self.assertLess(
            net_client._cache_ttl_seconds(net_client.FetchResult(ok=False, blocked_reason="request_error")),
            net_client.HTTP_CACHE_SUCCESS_TTL_SECONDS,
        )
        self.assertLess(
            net_client._cache_ttl_seconds(net_client.FetchResult(ok=False, blocked=True, blocked_reason="timeout")),
            net_client.HTTP_CACHE_SUCCESS_TTL_SECONDS,
        )

    def test_timeout_is_retried_once_then_negatively_cached(self) -> None:
        with patch.object(net_client, "get_domain_policy", return_value=self.policy), patch.object(
            net_client.requests, "get", side_effect=requests.Timeout("slow")
        ) as get, patch.object(net_client.time, "sleep"):
            first = net_client.fetch_http("https://example.test/timeout")
            second = net_client.fetch_http("https://example.test/timeout")
        self.assertEqual(first.blocked_reason, "timeout")
        self.assertEqual(first.retry_count, 1)
        self.assertEqual(second.fetch_provider, "http_cache")
        self.assertEqual(get.call_count, 2)

    def test_parallel_same_url_uses_single_network_fetch(self) -> None:
        counter = 0
        counter_lock = threading.Lock()

        def fake_get(url: str, **kwargs: object) -> FakeResponse:
            nonlocal counter
            with counter_lock:
                counter += 1
            time.sleep(0.02)
            return FakeResponse(url=url)

        with patch.object(net_client, "get_domain_policy", return_value=self.policy), patch.object(
            net_client.requests, "get", side_effect=fake_get
        ):
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda _: net_client.fetch_http("https://example.test/shared"), range(5)))
        self.assertEqual(counter, 1)
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(sum(result.fetch_provider == "http_cache" for result in results), 4)

    def test_use_cache_false_neither_reads_nor_writes_cache(self) -> None:
        with patch.object(net_client, "get_domain_policy", return_value=self.policy), patch.object(
            net_client.requests, "get", return_value=FakeResponse()
        ) as get:
            net_client.fetch_http("https://example.test/no-cache", use_cache=False)
            net_client.fetch_http("https://example.test/no-cache", use_cache=False)
        self.assertEqual(get.call_count, 2)
        self.assertFalse(net_client._CACHE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
