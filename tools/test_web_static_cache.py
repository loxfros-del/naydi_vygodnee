#!/usr/bin/env python3
"""HTTP contract tests for the locally served NOVA application shell."""
from __future__ import annotations

import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import tools.run_nova_web as nova_web
from tools.run_nova_web import NovaWebHandler


class StaticShellCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), NovaWebHandler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def test_shell_assets_are_never_reused_from_http_cache(self) -> None:
        for path in (
            "/",
            "/app.js?v=next",
            "/styles.css?v=next",
            "/service-worker.js?v=next",
            "/manifest.webmanifest?v=next",
        ):
            with self.subTest(path=path):
                self.connection.request("GET", path)
                response = self.connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Cache-Control"),
                    "no-store, max-age=0, must-revalidate",
                )

    def test_non_shell_static_assets_keep_normal_cache_behavior(self) -> None:
        self.connection.request("GET", "/icon.svg")
        response = self.connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("Cache-Control"))

    def test_missing_backend_keeps_shell_available_and_api_fails_closed(self) -> None:
        original_error = NovaWebHandler.backend_import_error
        original_service = NovaWebHandler.search_service
        original_validator = NovaWebHandler.miniapp_validator
        original_ready = NovaWebHandler.backend_ready
        original_backend_factory = NovaWebHandler.backend_factory
        original_validator_factory = NovaWebHandler.miniapp_validator_factory
        with patch.object(nova_web, "build_web_service", side_effect=ImportError("missing requests")):
            server = nova_web.make_server(port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(
                response.getheader("Cache-Control"),
                "no-store, max-age=0, must-revalidate",
            )

            connection.request("GET", "/api/health")
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 503)
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["code"], "search_backend_unavailable")
            self.assertNotIn("requests", payload.values())

            connection.request(
                "POST",
                "/api/search",
                b'{"product":"MacBook Air"}',
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 503)
            self.assertIn("временно недоступен", payload["message"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            NovaWebHandler.backend_import_error = original_error
            NovaWebHandler.search_service = original_service
            NovaWebHandler.miniapp_validator = original_validator
            NovaWebHandler.backend_ready = original_ready
            NovaWebHandler.backend_factory = original_backend_factory
            NovaWebHandler.miniapp_validator_factory = original_validator_factory

    def test_api_initializes_the_normal_backend_on_first_request(self) -> None:
        original_error = NovaWebHandler.backend_import_error
        original_service = NovaWebHandler.search_service
        original_validator = NovaWebHandler.miniapp_validator
        original_ready = NovaWebHandler.backend_ready
        original_backend_factory = NovaWebHandler.backend_factory
        original_validator_factory = NovaWebHandler.miniapp_validator_factory
        expected_service = object()

        async def fake_search(payload: dict[str, str], *, service: object) -> dict[str, object]:
            return {"product": payload["product"], "service": service is expected_service}

        with (
            patch.object(nova_web, "build_web_service", return_value=expected_service),
            patch.object(nova_web, "build_telegram_miniapp_validator", return_value=None),
            patch.object(nova_web, "search_web", side_effect=fake_search),
            patch.object(nova_web, "web_search_health", return_value={"yandexWeb": "ready"}),
        ):
            server = nova_web.make_server(port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            try:
                self.assertFalse(NovaWebHandler.backend_ready)
                connection.request(
                    "POST",
                    "/api/search",
                    b'{"product":"MacBook Air"}',
                    {"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    json.loads(response.read()),
                    {"product": "MacBook Air", "service": True},
                )
                self.assertTrue(NovaWebHandler.backend_ready)

                connection.request("GET", "/api/health")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["yandexWeb"], "ready")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)
                NovaWebHandler.backend_import_error = original_error
                NovaWebHandler.search_service = original_service
                NovaWebHandler.miniapp_validator = original_validator
                NovaWebHandler.backend_ready = original_ready
                NovaWebHandler.backend_factory = original_backend_factory
                NovaWebHandler.miniapp_validator_factory = original_validator_factory


if __name__ == "__main__":
    unittest.main()
