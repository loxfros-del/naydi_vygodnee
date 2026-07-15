"""Deterministic circuit-breaker and SearchApi quota preflight tests."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:ci_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.search_v2.adapters.base import SourceContext  # noqa: E402
from app.search_v2.adapters.shopping_search import ShoppingSearchAdapter  # noqa: E402
from app.search_v2.circuit_breaker import ProviderCircuitBreaker, shopping_provider_circuit  # noqa: E402
from app.search_v2.models import ProductCondition, QueryTier, SearchRequestV2, SourceQuery, SourceStatus  # noqa: E402
from app.sources.searchapi_account import get_searchapi_usage  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def request() -> SearchRequestV2:
    return SearchRequestV2(
        original_query="Apple iPhone 16 Pro 256 ГБ новый",
        category="phone",
        brand="Apple",
        canonical_model="iPhone 16",
        model_modifiers=["Pro"],
        required_specs={"storage_gb": 256},
        condition=ProductCondition.NEW,
        supported_category=True,
        hard_tokens=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
    )


def source_query() -> SourceQuery:
    return SourceQuery(
        query="Apple iPhone 16 Pro 256 ГБ new",
        source="shopping_search",
        tier=QueryTier.STRICT,
        hard_tokens_required=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
        hard_tokens_preserved=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
    )


class ProviderCircuitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        shopping_provider_circuit.reset_all()

    async def asyncTearDown(self) -> None:
        shopping_provider_circuit.reset_all()

    async def test_second_call_is_blocked_without_network_after_rate_limit(self) -> None:
        calls = 0

        def fake_search(_query, _request, _context):
            nonlocal calls
            calls += 1
            return {
                "status": "rate_limited",
                "candidates": [],
                "error": "You have used all of the searches for the month.",
            }

        adapter = ShoppingSearchAdapter(fake_search)
        first = await adapter.search(request(), source_query(), SourceContext(limit=5, timeout=3))
        second = await adapter.search(request(), source_query(), SourceContext(limit=5, timeout=3))

        self.assertEqual(first.status, SourceStatus.RATE_LIMITED)
        self.assertEqual(second.status, SourceStatus.RATE_LIMITED)
        self.assertEqual(calls, 1)
        self.assertTrue(second.rate_limit_info["circuit_open"])
        self.assertIn("provider circuit open", second.error)

    def test_circuit_expires_and_resets(self) -> None:
        now = [100.0]
        circuit = ProviderCircuitBreaker(clock=lambda: now[0])
        circuit.open("searchapi", seconds=10, reason="quota")
        self.assertTrue(circuit.snapshot("searchapi").open)
        now[0] = 111.0
        self.assertFalse(circuit.snapshot("searchapi").open)
        circuit.open("searchapi", seconds=10, reason="quota")
        circuit.reset("searchapi")
        self.assertFalse(circuit.snapshot("searchapi").open)


class SearchApiAccountTests(unittest.TestCase):
    def test_account_usage_is_safely_normalized(self) -> None:
        response = FakeResponse(200, {
            "account": {"current_month_usage": 100, "monthly_allowance": 100, "remaining_credits": 0},
            "api_usage": {"searches_this_hour": 10, "hourly_rate_limit": 100},
            "subscription": {"period_start": "2026-07-01", "period_end": "2026-08-01"},
        })
        usage = get_searchapi_usage(api_key="secret", request_get=lambda *args, **kwargs: response)
        self.assertEqual(usage.status, "success")
        self.assertEqual(usage.remaining_credits, 0)
        self.assertEqual(usage.monthly_allowance, 100)
        self.assertEqual(usage.searches_this_hour, 10)
        self.assertNotIn("secret", str(usage.to_dict()))

    def test_429_account_response_is_rate_limited(self) -> None:
        response = FakeResponse(429, {"error": "quota exhausted"})
        usage = get_searchapi_usage(api_key="secret", request_get=lambda *args, **kwargs: response)
        self.assertEqual(usage.status, "rate_limited")
        self.assertEqual(usage.status_code, 429)
        self.assertEqual(usage.error, "quota exhausted")

    def test_missing_key_never_calls_network(self) -> None:
        called = False

        def request_get(*args, **kwargs):
            nonlocal called
            called = True
            return SimpleNamespace()

        usage = get_searchapi_usage(api_key="", request_get=request_get)
        self.assertEqual(usage.status, "missing_api_key")
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
