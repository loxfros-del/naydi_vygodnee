"""Local HTTP API and static server for the separate Avito review panel."""
from __future__ import annotations

from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import time
from typing import Any
from urllib.parse import urlparse

from .ai import OpenAICompatibleReviewer, UnavailableReviewer
from .apify import ZenStudioProvider
from .config import ServiceConfig, expanded_review_config, load_config
from .errors import AvitoServiceError, ConfigurationError, ExternalServiceError, InvalidDatasetError
from .jobs import SearchJobRegistry
from .market_cache import MarketSnapshotCache
from .models import SearchRequest
from .service import AvitoAnalysisService
from .support import SupportInbox
from .spending import SpendingGuard
from .telemetry import PilotTraceStore


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "avito_web"
MAX_REQUEST_BYTES = 10 * 1024 * 1024
MAX_SUPPORT_BYTES = 64 * 1024
SUPPORT_PATH = ROOT / "runtime" / "support_requests.jsonl"


def build_service(config: ServiceConfig | None = None) -> AvitoAnalysisService:
    current = config if config is not None else expanded_review_config(load_config())
    apify_cap = min(current.apify_max_charge_usd, 2.0)
    current = replace(
        current,
        apify_max_charge_usd=apify_cap,
    )
    reviewer = OpenAICompatibleReviewer(current) if current.ai_ready else UnavailableReviewer()
    return AvitoAnalysisService(
        ZenStudioProvider(current, SpendingGuard(ROOT / "runtime" / "avito_spend.json",
                                                daily_limit_usd=3, billing_cycle_day=9)),
        reviewer,
        ai_concurrency=current.ai_concurrency,
        ai_text_batch_size=current.ai_text_batch_size,
        ai_text_max_listings=current.ai_text_max_listings,
        ai_max_listings=current.ai_max_listings,
        ai_max_cost_rub=current.effective_ai_budget_rub,
        ai_cache_ttl_seconds=current.ai_cache_ttl_seconds,
        report_max_cost_rub=current.report_max_cost_rub,
        usd_rub_rate=current.usd_rub_rate,
        minimum_report_price_rub=current.minimum_report_price_rub,
        target_cost_multiplier=current.target_cost_multiplier,
        market_cache=MarketSnapshotCache(ROOT / "runtime" / "avito_market_cache"),
    )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(" ", ""))
    except (TypeError, ValueError):
        raise ValueError("Цена должна быть целым числом.") from None


def _integer(value: Any, name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(str(value).replace(" ", ""))
    except (TypeError, ValueError):
        raise ValueError(f"Поле «{name}» должно быть целым числом.") from None


def parse_search_request(payload: Any) -> SearchRequest:
    if not isinstance(payload, dict):
        raise ValueError("Нужен JSON-объект параметров поиска.")
    raw_attributes = payload.get("attributes") or {}
    if not isinstance(raw_attributes, dict):
        raise ValueError("Дополнительные характеристики должны быть объектом.")
    attributes = tuple(
        (str(name).strip()[:80], str(value).strip()[:160])
        for name, value in list(raw_attributes.items())[:12]
        if str(name).strip() and str(value).strip()
    )
    return SearchRequest(
        query=str(payload.get("query") or "").strip()[:300],
        location=str(payload.get("location") or "Россия").strip()[:100],
        category=str(payload.get("category") or "all").strip()[:100],
        max_results=_integer(payload.get("maxResults"), "Объявлений для сбора", 20),
        mode=str(payload.get("mode") or "bargain").strip().casefold(),
        priority=str(payload.get("priority") or "balanced").strip().casefold(),
        desired_results=_integer(payload.get("desiredResults"), "Количество вариантов", 5),
        price_min=_optional_int(payload.get("priceMin")),
        price_max=_optional_int(payload.get("priceMax")),
        required_storage=str(payload.get("requiredStorage") or "").strip()[:80],
        required_sim=str(payload.get("requiredSim") or "").strip()[:80],
        required_condition=str(payload.get("requiredCondition") or "").strip()[:80],
        attributes=attributes,
        pickup_only=payload.get("pickupOnly", False),
    )


class AvitoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False if os.name == "nt" else ThreadingHTTPServer.allow_reuse_address
    allow_reuse_port = False if os.name == "nt" else ThreadingHTTPServer.allow_reuse_port

    def server_bind(self) -> None:
        if os.name == "nt":
            # Windows SO_REUSEADDR permits another process to share a live port.
            # Claim it exclusively before TCPServer performs bind().
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(
        self,
        address: tuple[str, int],
        service: AvitoAnalysisService,
        config: ServiceConfig,
        support_path: Path = SUPPORT_PATH,
        owner_token: str = "",
        access_token: str = "",
    ) -> None:
        self.local_only = _loopback_host(address[0])
        self.owner_token = owner_token
        self.access_token = access_token
        if not self.local_only and not (owner_token or access_token):
            raise ConfigurationError("Для сетевого доступа задайте AVITO_OWNER_TOKEN или AVITO_ACCESS_TOKEN в окружении процесса.")
        if any(token and len(token) < 32 for token in (owner_token, access_token)):
            raise ConfigurationError("Ключ доступа должен содержать не менее 32 символов.")
        if owner_token and access_token and secrets.compare_digest(owner_token.encode(), access_token.encode()):
            raise ConfigurationError("Ключ владельца и ключ доступа участников должны различаться.")
        self.analysis_service = service
        self.service_config = config
        trace_store = (
            PilotTraceStore(ROOT / "runtime" / "avito_pilot_traces")
            if config.live_pilot else None
        )
        self.jobs = SearchJobRegistry(service, trace_store=trace_store)
        self.support = SupportInbox(support_path)
        super().__init__(address, AvitoRequestHandler)


class AvitoRequestHandler(BaseHTTPRequestHandler):
    server: AvitoHTTPServer
    protocol_version = "HTTP/1.1"

    def _owner_authenticated(self) -> bool:
        return self._matches_token(self.server.owner_token)

    def _matches_token(self, expected: str) -> bool:
        value = self.headers.get("Authorization", "")
        return bool(expected) and secrets.compare_digest(value.encode(), f"Bearer {expected}".encode())

    def _require_access(self, *, owner_only: bool = False) -> bool:
        owner = self._owner_authenticated()
        allowed = owner if owner_only else (
            owner or self._matches_token(self.server.access_token) or
            (self.server.local_only and not self.server.access_token and not self.headers.get("Authorization"))
        )
        if not allowed:
            self.close_connection = True
            self._json(HTTPStatus.UNAUTHORIZED, {
                "error": "Введите ключ владельца." if owner_only else "Введите ключ доступа к поиску.",
                "code": "OWNER_AUTH_REQUIRED" if owner_only else "ACCESS_REQUIRED",
                "retryable": False,
            })
        return allowed

    def _same_origin(self) -> bool:
        # Reject browser requests from foreign pages, including DNS rebinding to localhost.
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        try:
            parsed_host = urlparse(f"//{host}")
            allowed = bool(host) and not (self.server.local_only and not _loopback_host(parsed_host.hostname or ""))
            if origin:
                parsed_origin = urlparse(origin)
                allowed = allowed and parsed_origin.scheme in {"http", "https"} and parsed_origin.netloc == host
        except ValueError:
            allowed = False
        if fetch_site == "cross-site":
            allowed = False
        if not allowed:
            self.close_connection = True
            self._json(HTTPStatus.FORBIDDEN, {"error": "Запрос с другого сайта отклонён.", "code": "FOREIGN_ORIGIN", "retryable": False})
        return allowed

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https://*.avito.st data:; "
            "style-src 'self'; font-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )

    def _bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._bytes(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _read_json(self, *, max_bytes: int = MAX_REQUEST_BYTES) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Некорректный Content-Length.") from None
        if length <= 0 or length > max_bytes:
            raise ValueError(f"Размер JSON должен быть от 1 байта до {max_bytes // 1024} КБ.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Тело запроса должно быть валидным UTF-8 JSON.") from None

    def do_GET(self) -> None:  # noqa: N802
        if not self._same_origin():
            return
        path = urlparse(self.path).path
        if path == "/api/session":
            if self._require_access():
                self._json(HTTPStatus.OK, {"owner": self._owner_authenticated(), "authenticated": True})
            return
        if path == "/health":
            self._json(HTTPStatus.OK, {
                "ok": True,
                "service": "avito-review",
                "status": "configured" if all(self.server.service_config.readiness().values()) else "setup-required",
                "ready": self.server.service_config.readiness(),
                "aiModel": self.server.service_config.ai_model,
                "sourceLimit": self.server.service_config.safe_apify_listing_limit,
                "accessRequired": not self.server.local_only or bool(self.server.access_token),
            })
            return
        if path.startswith("/api/avito/jobs/"):
            if not self._require_access():
                return
            job_id = path.rsplit("/", 1)[-1]
            job = self.server.jobs.get(job_id)
            if job is None:
                self._json(HTTPStatus.NOT_FOUND, {
                    "error": "Эта задача больше недоступна. Запустите поиск снова.",
                    "code": "JOB_NOT_FOUND",
                    "retryable": False,
                })
                return
            if job.owner_only and not self._require_access(owner_only=True):
                return
            self._json(HTTPStatus.OK, job.snapshot(include_admin=self._owner_authenticated()))
            return
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/query_normalization.js": ("query_normalization.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/fonts/Onest-Variable.ttf": ("fonts/Onest-Variable.ttf", "font/ttf"),
        }
        target = files.get(path)
        if target is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Страница не найдена."})
            return
        file_path = STATIC_ROOT / target[0]
        try:
            body = file_path.read_bytes()
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Интерфейс не собран."})
            return
        self._bytes(HTTPStatus.OK, body, target[1])

    def do_POST(self) -> None:  # noqa: N802
        if not self._same_origin():
            return
        path = urlparse(self.path).path
        cancel_prefix = "/api/avito/jobs/"
        cancel_job_id = ""
        if path.startswith(cancel_prefix) and path.endswith("/cancel"):
            cancel_job_id = path[len(cancel_prefix):-len("/cancel")].strip("/")
        if path not in {
            "/api/avito/search",
            "/api/avito/analyze-dataset",
            "/api/avito/jobs",
            "/api/support",
        } and not cancel_job_id:
            self._json(HTTPStatus.NOT_FOUND, {"error": "API-метод не найден."})
            return
        if not self._require_access(owner_only=path == "/api/avito/analyze-dataset"):
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.close_connection = True
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Нужен Content-Type application/json.", "code": "INVALID_CONTENT_TYPE", "retryable": False})
            return
        try:
            if cancel_job_id:
                cancel_payload = self._read_json(max_bytes=1024)
                if not isinstance(cancel_payload, dict):
                    raise ValueError("Нужен JSON-объект отмены поиска.")
                job = self.server.jobs.get(cancel_job_id)
                if job is None:
                    self._json(HTTPStatus.NOT_FOUND, {
                        "error": "Эта задача больше недоступна. Запустите поиск снова.",
                        "code": "JOB_NOT_FOUND",
                        "retryable": False,
                    })
                    return
                if job.owner_only and not self._require_access(owner_only=True):
                    return
                cancelled = self.server.jobs.cancel(cancel_job_id)
                self._json(HTTPStatus.OK, cancelled.snapshot(include_admin=self._owner_authenticated()))
                return
            if path == "/api/support":
                payload = self._read_json(max_bytes=MAX_SUPPORT_BYTES)
                self._json(HTTPStatus.CREATED, self.server.support.create(payload))
                return
            action = "реальный поиск" if path in {"/api/avito/search", "/api/avito/jobs"} else "анализ Dataset"
            print(f"[avito] Начат {action}.", flush=True)
            payload = self._read_json()
            if not self.server.service_config.ai_ready:
                raise ConfigurationError(
                    "AI не подключена. Перезапустите Avito Review после настройки AI Tunnel."
                )
            if path in {"/api/avito/search", "/api/avito/jobs"}:
                if not self.server.service_config.apify_ready:
                    raise ConfigurationError(
                        "Zen не подключён. Перезапустите Avito Review после настройки Apify."
                    )
                parse_started = time.perf_counter()
                search = parse_search_request(payload)
                parse_ms = round((time.perf_counter() - parse_started) * 1000, 3)
                job = self.server.jobs.start(search, parse_ms=parse_ms)
            else:
                if not isinstance(payload, dict):
                    raise ValueError("Нужен JSON-объект с полем listings.")
                parse_started = time.perf_counter()
                search = parse_search_request(payload.get("search") or {
                    "query": payload.get("query") or "Импортированный набор Avito",
                    "location": payload.get("location") or "Россия",
                })
                parse_ms = round((time.perf_counter() - parse_started) * 1000, 3)
                job = self.server.jobs.start_dataset(
                    search, payload.get("listings"), parse_ms=parse_ms,
                )
            self._json(HTTPStatus.ACCEPTED, job.snapshot(include_admin=self._owner_authenticated()))
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {
                "error": str(exc),
                "code": "INVALID_REQUEST",
                "retryable": False,
            })
        except InvalidDatasetError as exc:
            self._json(HTTPStatus.BAD_REQUEST, exc.public_dict())
        except ConfigurationError as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, exc.public_dict())
        except ExternalServiceError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, exc.public_dict())
        except AvitoServiceError as exc:
            status = HTTPStatus.TOO_MANY_REQUESTS if exc.code in {"SEARCH_BUSY", "SEARCH_RATE_LIMIT", "SUPPORT_RATE_LIMIT"} else HTTPStatus.SERVICE_UNAVAILABLE
            self._json(status, exc.public_dict())
        except Exception:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "Внутренняя ошибка Avito Service.",
                "code": "INTERNAL_ERROR",
                "retryable": True,
            })

    def log_message(self, format: str, *args: object) -> None:
        # Keep the default console useful without ever logging request bodies or credentials.
        print(f"[avito-http] {self.command} {urlparse(self.path).path} {args[1] if len(args) > 1 else ''}")


def _loopback_host(host: str) -> bool:
    if host.casefold() in {"localhost", "localhost."}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_server(
    host: str = "127.0.0.1",
    port: int = 8091,
    *,
    service: AvitoAnalysisService | None = None,
    config: ServiceConfig | None = None,
    support_path: Path = SUPPORT_PATH,
    owner_token: str | None = None,
    access_token: str | None = None,
) -> AvitoHTTPServer:
    current = config if config is not None else expanded_review_config(load_config())
    return AvitoHTTPServer(
        (host, port),
        service or build_service(current),
        current,
        support_path=support_path,
        owner_token=os.environ.get("AVITO_OWNER_TOKEN", "").strip() if owner_token is None else owner_token,
        access_token=os.environ.get("AVITO_ACCESS_TOKEN", "").strip() if access_token is None else access_token,
    )
