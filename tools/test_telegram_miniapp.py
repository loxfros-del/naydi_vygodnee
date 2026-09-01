#!/usr/bin/env python3
"""Deterministic Mini App authentication and read-only search boundary checks."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from http.client import HTTPConnection
import json
import sys
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.models import SearchRequestV2, SearchResultStatus, SearchResultV2  # noqa: E402
from app.services.telegram_miniapp import (  # noqa: E402
    TelegramMiniAppAuthError,
    TelegramMiniAppValidator,
)
from app.services.web_request_service import InMemorySearchRequestService  # noqa: E402
from app.web_api import search_telegram_miniapp  # noqa: E402
from tools.run_nova_web import NovaWebHandler  # noqa: E402
import tools.run_nova_web as nova_web  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


TOKEN = "123456:mini-app-test-token"
NOW = 1_800_000_000


def signed_init_data(*, user: dict | None = None, auth_date: int = NOW, extra: dict[str, str] | None = None) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAH-mini-app-test",
        "user": json.dumps(user or {"id": 42, "username": "nova_test"}, separators=(",", ":")),
        **(extra or {}),
    }
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode("utf-8"), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(values)


class FakeSearchService:
    def __init__(self) -> None:
        self.request = None

    async def search(self, request):
        self.request = request
        return SearchResultV2(
            normalized_request=SearchRequestV2(canonical_model=str(request.get("model") or "")),
            status=SearchResultStatus.NO_EXACT_MATCH,
        )


class TelegramMiniAppValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = TelegramMiniAppValidator(TOKEN, clock=lambda: NOW)

    def test_accepts_correct_signature_and_returns_only_identity(self) -> None:
        identity = self.validator.validate(signed_init_data())

        self.assertEqual(identity.user_id, 42)
        self.assertEqual(identity.username, "nova_test")
        self.assertEqual(set(identity.__dataclass_fields__), {"user_id", "username"})

    def test_rejects_tampered_signature_duplicate_key_and_expiry(self) -> None:
        valid = signed_init_data()
        tampered = valid.replace("nova_test", "not_nova", 1)

        for init_data in (tampered, valid + "&auth_date=1", signed_init_data(auth_date=NOW - 86_401)):
            with self.subTest(init_data=init_data[:24]):
                with self.assertRaises(TelegramMiniAppAuthError):
                    self.validator.validate(init_data)

    def test_rejects_invalid_user_even_if_signature_is_correct(self) -> None:
        with self.assertRaises(TelegramMiniAppAuthError):
            self.validator.validate(signed_init_data(user={"id": 0}))


class TelegramMiniAppSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_miniapp_uses_same_nonpersistent_search_boundary(self) -> None:
        service = FakeSearchService()
        boundary = InMemorySearchRequestService(
            search_service=service,
            request_normalizer=lambda payload: {"model": payload["product"], "category": "manual"},
        )
        response = await search_telegram_miniapp(
            {"initData": signed_init_data(), "search": {"product": "MacBook Air M4"}},
            validator=TelegramMiniAppValidator(TOKEN, clock=lambda: NOW),
            request_service=boundary,
        )

        self.assertEqual(service.request, {"model": "MacBook Air M4", "category": "manual"})
        self.assertEqual(response["status"], "NO_EXACT_MATCH")
        self.assertIn("Точных предложений", response["message"])

    async def test_authentication_happens_before_search_payload_is_used(self) -> None:
        service = FakeSearchService()
        boundary = InMemorySearchRequestService(
            search_service=service,
            request_normalizer=lambda _payload: self.fail("unauthenticated data reached search"),
        )

        with self.assertRaises(TelegramMiniAppAuthError):
            await search_telegram_miniapp(
                {"initData": "bad", "search": {"product": "anything"}},
                validator=TelegramMiniAppValidator(TOKEN, clock=lambda: NOW),
                request_service=boundary,
            )
        self.assertIsNone(service.request)


class MiniAppServerContractTests(unittest.TestCase):
    def test_server_exposes_a_separate_authenticated_route(self) -> None:
        source = (ROOT / "tools" / "run_nova_web.py").read_text(encoding="utf-8")

        self.assertIn('"/api/miniapp/search"', source)
        self.assertIn('"/api/telegram/session"', source)
        self.assertIn('response = {"telegram": True}', source)
        self.assertIn("TelegramMiniAppAuthError", source)
        self.assertIn("HTTPStatus.UNAUTHORIZED", source)
        self.assertIn("search_telegram_miniapp", source)

    def test_session_endpoint_validates_signature_without_returning_telegram_id(self) -> None:
        original_validator = NovaWebHandler.miniapp_validator
        original_service = NovaWebHandler.search_service
        NovaWebHandler.miniapp_validator = TelegramMiniAppValidator(TOKEN, clock=lambda: NOW)
        NovaWebHandler.search_service = None
        server = ThreadingHTTPServer(("127.0.0.1", 0), NovaWebHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            body = json.dumps({"initData": signed_init_data()}).encode("utf-8")
            connection.request("POST", "/api/telegram/session", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload, {"telegram": True, "displayName": "@nova_test"})

            connection.request(
                "POST", "/api/telegram/session", b'{"initData":"bad"}', {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            self.assertEqual(json.loads(response.read()), {"message": "Откройте поиск через Telegram."})
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            NovaWebHandler.miniapp_validator = original_validator
            NovaWebHandler.search_service = original_service

    def test_browser_server_starts_without_a_miniapp_token_and_session_fails_closed(self) -> None:
        original_validator = NovaWebHandler.miniapp_validator
        original_service = NovaWebHandler.search_service
        with (
            patch.object(nova_web, "build_web_service", return_value=object()),
            patch.object(nova_web, "build_telegram_miniapp_validator", side_effect=RuntimeError("no token")),
        ):
            server = nova_web.make_server(port=0)
        self.assertIsNone(NovaWebHandler.miniapp_validator)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request(
                "POST", "/api/telegram/session", b'{"initData":"anything"}', {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 503)
            self.assertEqual(json.loads(response.read()), {"message": "Поиск временно недоступен. Попробуйте ещё раз."})
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            NovaWebHandler.miniapp_validator = original_validator
            NovaWebHandler.search_service = original_service


if __name__ == "__main__":
    unittest.main(verbosity=2)
