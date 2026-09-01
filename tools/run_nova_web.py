#!/usr/bin/env python3
"""Run NOVA locally with the browser UI and the live Search V2 endpoint.

This is intentionally a localhost development server. It does not start the
Telegram bot and it uses an in-memory source cache only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
MAX_BODY_BYTES = 16 * 1024
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



def build_telegram_miniapp_validator() -> Any:
    """Load Mini App authentication only when an API route needs it."""
    from app.services.telegram_miniapp import build_telegram_miniapp_validator as implementation

    return implementation()


def build_web_service() -> Any:
    """Load the search backend lazily so the static shell stays available."""
    from app.web_api import build_web_service as implementation

    return implementation()


def search_web(*args: Any, **kwargs: Any) -> Any:
    from app.web_api import search_web as implementation

    return implementation(*args, **kwargs)


def search_telegram_miniapp(*args: Any, **kwargs: Any) -> Any:
    from app.web_api import search_telegram_miniapp as implementation

    return implementation(*args, **kwargs)


def web_search_health() -> dict[str, str]:
    from app.web_api import web_search_health as implementation

    return implementation()


def is_telegram_miniapp_auth_error(error: BaseException) -> bool:
    """Keep the exception type lazy together with the optional backend."""
    from app.services.telegram_miniapp import TelegramMiniAppAuthError

    return isinstance(error, TelegramMiniAppAuthError)


class NovaWebHandler(SimpleHTTPRequestHandler):
    """Static NOVA shell plus a small same-origin JSON API."""

    server_version = "NOVA/0.1"
    web_root = WEB_ROOT
    search_service = None
    miniapp_validator = None
    backend_import_error: ImportError | None = None
    # Direct handler tests may inject a service or validator.  Servers created
    # through ``make_server`` set this to False and load the backend only on
    # the first API request.
    backend_ready = True
    backend_factory = build_web_service
    miniapp_validator_factory = build_telegram_miniapp_validator
    _backend_lock = RLock()
    _SHELL_ASSETS = {
        "/",
        "/index.html",
        "/app.js",
        "/styles.css",
        "/service-worker.js",
        "/manifest.webmanifest",
    }
    _SHELL_CACHE_CONTROL = "no-store, max-age=0, must-revalidate"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(self.web_root), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        # Avoid logging request payloads such as a product name to the console.
        if self.path.startswith("/api/"):
            self.log_date_time_string()
            return
        super().log_message(format, *args)

    def end_headers(self) -> None:
        """Prevent a broken shell bundle from being reused after a deployment.

        API responses set their own cache policy in ``_send_json``.  The
        browser shell is deliberately uncached so an updated HTML document can
        immediately register a newer service worker and JavaScript bundle.
        """
        path = urlsplit(self.path).path
        if path in self._SHELL_ASSETS:
            self.send_header("Cache-Control", self._SHELL_CACHE_CONTROL)
        super().end_headers()

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_backend_unavailable(self) -> None:
        self._send_json(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "status": "unavailable",
                "service": "nova-web",
                "code": "search_backend_unavailable",
                "message": "Поиск временно недоступен. Попробуйте ещё раз.",
            },
        )

    @classmethod
    def _remember_backend_import_error(cls, error: ImportError) -> None:
        if cls.backend_import_error is not None:
            return
        cls.backend_import_error = error
        dependency = getattr(error, "name", None)
        detail = f" Не найдена зависимость: {dependency}." if dependency else ""
        print(
            "NOVA: поисковый backend не загрузился."
            f"{detail} Выполните: python -m pip install -r requirements.txt"
            " — затем перезапустите сервер.",
            file=sys.stderr,
        )

    @classmethod
    def _ensure_backend(cls) -> bool:
        """Initialize search dependencies only when an API route is used."""
        if cls.backend_import_error is not None:
            return False
        if cls.backend_ready:
            return True
        with cls._backend_lock:
            if cls.backend_import_error is not None:
                return False
            if cls.backend_ready:
                return True
            try:
                cls.search_service = cls.backend_factory()
            except ImportError as exc:
                cls._remember_backend_import_error(exc)
                return False
            # Telegram remains optional for the normal browser UI.
            try:
                cls.miniapp_validator = cls.miniapp_validator_factory()
            except Exception:
                cls.miniapp_validator = None
            cls.backend_ready = True
            return True

    def _read_json(self) -> dict[str, Any]:
        if "application/json" not in self.headers.get("Content-Type", "").casefold():
            raise ValueError("Нужен JSON-запрос.")
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Некорректный размер запроса.") from exc
        if content_length < 1 or content_length > MAX_BODY_BYTES:
            raise ValueError("Слишком большой или пустой запрос.")
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Не удалось прочитать JSON-запрос.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Заявка должна быть объектом.")
        return payload

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/health":
            if not self._ensure_backend():
                self._send_backend_unavailable()
                return
            try:
                health = web_search_health()
            except ImportError as exc:
                self._remember_backend_import_error(exc)
                self._send_backend_unavailable()
                return
            self._send_json(HTTPStatus.OK, {
                "status": "ok",
                "service": "nova-web",
                **health,
            })
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in {"/api/search", "/api/miniapp/search", "/api/telegram/session"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"message": "Маршрут не найден."})
            return
        if not self._ensure_backend():
            self._send_backend_unavailable()
            return
        try:
            payload = self._read_json()
            if path == "/api/telegram/session":
                if self.miniapp_validator is None:
                    raise RuntimeError("Mini App authentication is unavailable")
                identity = self.miniapp_validator.validate(payload.get("initData"))
                response = {"telegram": True}
                if identity.username:
                    response["displayName"] = f"@{identity.username}"
                self._send_json(HTTPStatus.OK, response)
                return
            if path == "/api/miniapp/search":
                if self.miniapp_validator is None:
                    raise RuntimeError("Mini App authentication is unavailable")
                result = asyncio.run(search_telegram_miniapp(
                    payload,
                    validator=self.miniapp_validator,
                    service=self.search_service,
                ))
            else:
                result = asyncio.run(search_web(payload, service=self.search_service))
        except Exception as exc:
            try:
                is_auth_error = is_telegram_miniapp_auth_error(exc)
            except ImportError:
                self._remember_backend_import_error(ImportError("Telegram Mini App backend is unavailable"))
                self._send_backend_unavailable()
                return
            if is_auth_error:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"message": "Откройте поиск через Telegram."})
                return
            if isinstance(exc, ImportError):
                self._remember_backend_import_error(exc)
                self._send_backend_unavailable()
                return
            if isinstance(exc, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"message": str(exc)})
                return
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"message": "Поиск временно недоступен. Попробуйте ещё раз."},
            )
            return
        self._send_json(HTTPStatus.OK, result)


def make_server(host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    NovaWebHandler.search_service = None
    NovaWebHandler.miniapp_validator = None
    NovaWebHandler.backend_import_error = None
    NovaWebHandler.backend_ready = False
    # Keep the factories themselves lazy.  This lets the visual shell start
    # even on a machine where optional Python search dependencies are absent.
    NovaWebHandler.backend_factory = build_web_service
    NovaWebHandler.miniapp_validator_factory = build_telegram_miniapp_validator
    return ThreadingHTTPServer((host, port), NovaWebHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Запустить локальный NOVA web + Search V2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = make_server(args.host, args.port)
    print(f"NOVA: http://{args.host}:{args.port}")
    print("Проверка: /api/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nNOVA остановлена.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
