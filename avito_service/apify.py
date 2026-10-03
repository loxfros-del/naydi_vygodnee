"""Safe Zen Studio Apify provider using the documented REST API."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import json
import math
import re
import socket
import ssl
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import ServiceConfig
from .errors import ApifyPlanRequiredError, ConfigurationError, ExternalServiceError, SearchCancelledError
from .fill_target import CollectionPage, CollectionSafetyLimits, fill_to_target
from .models import CollectionBatch, NormalizedListing, SearchRequest
from .request_intent import parse_request_signature
from .spending import SpendingGuard


_TERMINAL_FAILURES = {"FAILED", "TIMED-OUT", "ABORTED"}
_UPGRADE_REQUIRED_MESSAGE = (
    "Zen Studio ограничил сбор на бесплатном тарифе: не более 100 объявлений за запуск "
    "и 10 запусков всего. Для расширенного поиска нужно повысить тариф: выбрать план Apify выше Free; "
    "автоматический повтор не запускается."
)


def _provider_error_code(body: bytes) -> str | None:
    """Extract only a bounded code; provider messages may echo secrets/input."""
    try:
        value = json.loads(body[:8192].decode("utf-8"))
        error = value.get("error") if isinstance(value, dict) else None
        code = (error.get("type") or error.get("code")) if isinstance(error, dict) else None
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
            return code
    except (UnicodeError, ValueError):
        pass
    return None


def _network_failure(exc: BaseException) -> tuple[str, bool]:
    """Return safe error category and whether HTTP was certainly not sent."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, PermissionError):
        return "network_permission_denied", True
    if isinstance(reason, socket.gaierror):
        return "dns_resolution_failed", True
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused", True
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "tls_certificate_failed", True
    if isinstance(reason, ssl.SSLError):
        return "tls_handshake_failed", True
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "network_timeout", False
    if isinstance(reason, (UnicodeError, json.JSONDecodeError, gzip.BadGzipFile)):
        return "malformed_response", False
    return "network_error", False


class ZenStudioProvider:
    """Collect complete listings from `zen-studio/avito-listings-scraper`."""

    supports_deadline = True
    supports_collection_budget = True
    supports_cancellation = True
    supports_telemetry = True

    def _raise_if_cancelled(
        self, cancel_requested: Callable[[], bool] | None, run_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        if cancel_requested is None or not cancel_requested():
            return
        if context is not None:
            context["cancel_requested"] = True
        if run_id:
            confirmed = self._abort_run(run_id)
            if context is not None:
                context["abort_requested"] = True
                context["abort_confirmed"] = bool(confirmed)
        raise SearchCancelledError()

    def can_share_discovery(self, search: SearchRequest) -> bool:
        """Without price bounds both collection methods send the same query."""
        return search.price_min is None and search.price_max is None

    def __init__(self, config: ServiceConfig, spending_guard: SpendingGuard | None = None) -> None:
        self.config = config
        self.spending_guard = spending_guard
        self._upgrade_blocked_until = 0.0

    def _headers(self) -> dict[str, str]:
        if not self.config.apify_token:
            raise ConfigurationError("Для реального поиска задайте APIFY_TOKEN локально.")
        return {
            "Authorization": f"Bearer {self.config.apify_token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "naydi-vygodnee-avito-service/1.0",
        }

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        timeout: float = 130,
    ) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        # A lost start response may still represent a paid Actor run. Only reads
        # are safe to repeat; all attempts share one bounded time allowance.
        attempts = 3 if method.upper() == "GET" else 1
        deadline = time.monotonic() + max(0.1, float(timeout))
        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExternalServiceError("Время получения ответа Apify истекло.", code="APIFY_TIMEOUT")
            request = Request(url, data=body, headers=self._headers(), method=method)
            retry_after = 0.5 * (attempt + 1)
            response_status: int | None = None
            try:
                with urlopen(request, timeout=min(20.0, remaining) if attempts > 1 else remaining) as response:
                    response_status = getattr(response, "status", None)
                    data = response.read()
                    if "gzip" in str(response.headers.get("Content-Encoding") or "").casefold():
                        data = gzip.decompress(data)
                    return json.loads(data.decode("utf-8"))
            except HTTPError as exc:
                # Response messages can echo requests or credentials: retain a
                # bounded provider code, never the raw body or headers.
                try:
                    provider_code = _provider_error_code(exc.read(8192))
                except OSError:
                    provider_code = None
                safe_response = f"Apify HTTP {exc.code}" + (f" ({provider_code})" if provider_code else "")
                diagnostics = {
                    "http_status": exc.code,
                    "provider_error_code": provider_code,
                    "sanitized_response": safe_response,
                    "request_reached_provider": True if provider_code else None,
                    "no_run_proven": exc.code in {400, 401, 402, 403, 404, 422, 429},
                }
                if exc.code == 429:
                    try:
                        retry_after = float((exc.headers.get("Retry-After") if exc.headers else None) or 0.5)
                    except (TypeError, ValueError):
                        retry_after = 0.5
                    retry_after = max(0.1, min(retry_after, 2.0))
                if exc.code in {401, 403}:
                    error = ExternalServiceError(
                        "Apify отклонил авторизацию. Проверьте токен и доступ Actor.",
                        code="APIFY_AUTH",
                        retryable=False,
                        diagnostics=diagnostics,
                    )
                elif exc.code in {402, 429}:
                    code = "APIFY_RATE_LIMIT" if exc.code == 429 else "APIFY_PLAN_REQUIRED"
                    error_type = ExternalServiceError if exc.code == 429 else ApifyPlanRequiredError
                    error = error_type(
                        f"Apify временно не может запустить поиск, HTTP {exc.code}.",
                        code=code,
                        retryable=exc.code == 429,
                        diagnostics=diagnostics,
                    )
                else:
                    code = ("APIFY_INVALID_INPUT" if exc.code in {400, 422} else
                            "APIFY_ACTOR_NOT_FOUND" if exc.code == 404 and "/acts/" in url else
                            "APIFY_HTTP_ERROR")
                    error = ExternalServiceError(
                        f"Apify отклонил запрос, HTTP {exc.code}.",
                        code=code,
                        retryable=exc.code >= 500,
                        diagnostics=diagnostics,
                    )
            except (OSError, URLError, TimeoutError, EOFError, UnicodeError, json.JSONDecodeError) as exc:
                category, no_run_proven = _network_failure(exc)
                reason = exc.reason if isinstance(exc, URLError) else exc
                error_number = getattr(reason, "errno", None)
                error = ExternalServiceError(
                    "Apify временно недоступен или вернул некорректный ответ.",
                    code="APIFY_UNAVAILABLE",
                    diagnostics={
                        "http_status": response_status,
                        "provider_error_code": None,
                        "sanitized_response": category,
                        "network_error_type": category,
                        "network_errno": error_number if isinstance(error_number, int) else None,
                        "request_reached_provider": True if response_status is not None else
                            False if no_run_proven else None,
                        "no_run_proven": no_run_proven,
                    },
                )
            if attempt + 1 < attempts and error.retryable and deadline - time.monotonic() > retry_after + 0.1:
                time.sleep(retry_after)
                continue
            raise error from None
        raise ExternalServiceError("Apify временно ограничил частоту запросов.", code="APIFY_RATE_LIMIT")

    def _abort_run(self, run_id: str) -> bool:
        if not run_id:
            return False
        url = f"{self.config.apify_api_url}/actor-runs/{quote(run_id)}/abort?gracefully=true"
        try:
            self._json_request("POST", url, timeout=3)
            return True
        except ExternalServiceError:
            return False

    def _actor_input(self, search: SearchRequest) -> dict[str, Any]:
        capped_results = min(search.max_results, self.config.safe_apify_listing_limit)
        # Zen's current public input schema has explicit category values.
        actor_category = {
            "phones": "phones",
            "computers": "computers",
            "laptops": "laptops",
            "electronics": "electronics_all",
            "gaming": "gaming",
            "furniture": "furniture",
            "appliances": "appliances",
            "household": "appliances",
            "tools": "renovation",
            "auto_parts": "spare_parts",
            "auto": "spare_parts",
        }.get(search.category, "all")
        query = search.query.strip()
        storage = search.required_storage.strip()
        canonical = lambda value: re.sub(r"\s+", "", value.casefold().replace("гб", "gb").replace("тб", "tb"))
        if storage and canonical(storage) not in canonical(query):
            query = f"{query} {storage}"
        payload: dict[str, Any] = {
            "query": query,
            "location": search.location.strip() or "Россия",
            "category": actor_category,
            "maxResults": capped_results,
            # Product rule: complete text and photos are mandatory.
            "includeDetails": True,
            "includePhone": False,
            "includeReviews": False,
            "includeComparables": False,
        }
        # Zen's generic location input returned 140/200 rows outside Yaroslavl
        # in the 23.09 live run. Use its documented city URL override for this
        # verified Avito city path; final city matching remains mandatory.
        if search.location.strip().casefold() in {"ярославль", "yaroslavl"}:
            payload["searchUrl"] = "https://www.avito.ru/yaroslavl?" + urlencode({"q": query})
        if search.price_min is not None:
            payload["priceMin"] = search.price_min
        if search.price_max is not None:
            payload["priceMax"] = search.price_max
        return payload

    @staticmethod
    def _positive_float(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if math.isfinite(parsed) and parsed > 0 else 0.0

    def collect(
        self,
        search: SearchRequest,
        *,
        deadline_at: float | None = None,
        max_charge_usd: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        telemetry: Callable[[str, Mapping[str, Any]], None] | None = None,
        purpose: str = "candidate_collection",
    ) -> CollectionBatch:
        payload = self._actor_input(search)
        # The Actor limits the pool before our model/variant checks. Cheapest
        # first can spend that pool on accessories or a previous generation.
        # Retrieve relevant candidates; rank their verified full prices locally.
        payload["sort"] = "relevance"
        return self._collect_payload(
            payload, search.max_results, deadline_at, max_charge_usd, cancel_requested,
            telemetry, purpose,
        )

    def collect_market(
        self, search: SearchRequest, *, deadline_at: float | None = None,
        max_charge_usd: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        telemetry: Callable[[str, Mapping[str, Any]], None] | None = None,
        purpose: str = "market_collection",
    ) -> CollectionBatch:
        """Collect an independent reference sample without the buyer's price limits."""
        broad = replace(search, price_min=None, price_max=None)
        payload = self._actor_input(broad)
        payload["sort"] = "relevance"
        return self._collect_payload(
            payload, broad.max_results, deadline_at, max_charge_usd, cancel_requested,
            telemetry, purpose,
        )

    def collect_to_target(
        self, search: SearchRequest, *, deadline_at: float | None = None,
        max_charge_usd: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        telemetry: Callable[[str, Mapping[str, Any]], None] | None = None,
        purpose: str = "market_collection",
    ) -> CollectionBatch:
        """Fill unique city/family matches with bounded page and cost safety."""
        if parse_request_signature(search).requested_family == "unknown":
            return self.collect_market(
                search, deadline_at=deadline_at, max_charge_usd=max_charge_usd,
                cancel_requested=cancel_requested,
                telemetry=telemetry, purpose=purpose,
            )
        allowance = self.config.effective_apify_max_charge_usd
        if max_charge_usd is not None:
            allowance = min(allowance, self._positive_float(max_charge_usd))
        spent = 0.0

        def fetch_page(request: SearchRequest, page_number: int, query: str, page_size: int) -> CollectionPage:
            nonlocal spent
            remaining = max(0.0, allowance - spent)
            page_allowance = min(
                remaining,
                self.config.apify_start_cost_usd
                + max(1, page_size) * self.config.apify_full_listing_cost_usd,
            )
            if page_allowance < self.config.apify_start_cost_usd + self.config.apify_full_listing_cost_usd:
                return CollectionPage((), exhausted=False, stop_reason="BUDGET_LIMIT")
            page_request = replace(request, query=query, max_results=page_size,
                                   price_min=None, price_max=None)
            payload = self._actor_input(page_request)
            payload["sort"] = "relevance"
            if "searchUrl" in payload:
                payload["searchUrl"] = "https://www.avito.ru/yaroslavl?" + urlencode({
                    "q": query, "p": page_number,
                })
            batch = self._collect_payload(
                payload, page_size, deadline_at, page_allowance, cancel_requested,
                telemetry, purpose,
            )
            spent += batch.apify_cost_usd
            return CollectionPage(
                batch.items, batch.apify_cost_usd, batch.apify_cost_estimated,
                exhausted=len(batch.items) < batch.capped_count,
            )

        result = fill_to_target(search, fetch_page, CollectionSafetyLimits(
            max_raw_pages=self.config.collection_max_raw_pages,
            max_raw_listings=self.config.collection_max_raw_listings,
            max_collection_cost_usd=allowance,
            duplicate_saturation_pages=self.config.collection_duplicate_saturation_pages,
            page_size=min(100, self.config.safe_apify_listing_limit),
        ))
        return result.as_collection_batch()

    def refresh(
        self, listings: tuple[NormalizedListing, ...], *, deadline_at: float | None = None,
        max_charge_usd: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        telemetry: Callable[[str, Mapping[str, Any]], None] | None = None,
        purpose: str = "final_revalidation",
    ) -> CollectionBatch:
        """Reload the exact final URLs, never a replacement search result."""
        if not listings:
            return CollectionBatch(items=())
        from .risk_rules import _is_direct_listing_url
        if any(not _is_direct_listing_url(item.url, item.listing_id) for item in listings):
            raise ValueError("Для повторной проверки нужны прямые ссылки на объявления.")
        payload = {
            "listingUrls": [item.url for item in listings],
            "maxResults": len(listings), "includeDetails": True,
            "includePhone": False, "includeReviews": False, "includeComparables": False,
        }
        return self._collect_payload(
            payload, len(listings), deadline_at, max_charge_usd, cancel_requested,
            telemetry, purpose,
        )

    def _collect_payload(
        self, payload: dict[str, Any], requested_count: int,
        deadline_at: float | None, max_charge_usd: float | None,
        cancel_requested: Callable[[], bool] | None = None,
        telemetry: Callable[[str, Mapping[str, Any]], None] | None = None,
        purpose: str = "collection",
    ) -> CollectionBatch:
        context: dict[str, Any] = {
            "trace_run_id": f"{purpose}-{time.time_ns()}",
            "purpose": purpose,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_mono": time.monotonic(),
        }

        def reconcile_reservation() -> None:
            reservation_id = context.get("reservation_id")
            if self.spending_guard is None or not reservation_id or context.get("reservation_reconciled"):
                return
            actual = context.get("actual_cost_usd")
            final = bool(context.get("billing_complete")) and isinstance(actual, (int, float))
            try:
                if context.get("no_run_proven") and not context.get("run_id"):
                    self.spending_guard.release_unbilled(str(reservation_id))
                    context["reservation_accounting"] = "released_no_run"
                    context["accounted_cost_usd"] = 0.0
                    context["cost_estimated"] = False
                    context["reservation_reconciled"] = True
                    return
                self.spending_guard.settle(str(reservation_id), actual, final=final)
                context["reservation_reconciled"] = True
                context["reservation_accounting"] = "actual" if final else "settled_estimate"
                if final:
                    context["accounted_cost_usd"] = actual
                    context["cost_estimated"] = False
                else:
                    context["accounted_cost_usd"] = max(
                        float(context.get("accounted_cost_usd") or 0.0),
                        float(context.get("reserved_max_cost_usd") or 0.0),
                    )
                    context["cost_estimated"] = True
            except ExternalServiceError:
                # Fail closed: the journal still contains the active reserve.
                context["reservation_accounting"] = "active_reconciliation_failed"
        try:
            batch = self._collect_payload_once(
                payload, requested_count, deadline_at, max_charge_usd, context,
                cancel_requested, telemetry,
            )
            context["billing_complete"] = not batch.apify_cost_estimated
            context["accounted_cost_usd"] = batch.apify_cost_usd
            if not batch.apify_cost_estimated:
                context["actual_cost_usd"] = batch.apify_cost_usd
            reconcile_reservation()
            context.update({
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((time.monotonic() - context["started_mono"]) * 1000),
                "items": len(batch.items),
                "used": True,
                "cost_estimated": batch.apify_cost_estimated,
                "accounted_cost_usd": batch.apify_cost_usd,
            })
            self._emit_telemetry(telemetry, "finished", context)
            return batch
        except SearchCancelledError:
            reconcile_reservation()
            context.update({
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((time.monotonic() - context["started_mono"]) * 1000),
                "status": "CANCELLED",
                "cancel_requested": True,
                "used": False,
                "error_code": "SEARCH_CANCELLED",
                "error_type": "SearchCancelledError",
            })
            self._emit_telemetry(telemetry, "cancelled", context)
            raise
        except ExternalServiceError as exc:
            reconcile_reservation()
            context.update({
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((time.monotonic() - context["started_mono"]) * 1000),
                "used": False,
                "error_code": exc.code,
                "error_type": type(exc).__name__,
            })
            self._emit_telemetry(telemetry, "error", context)
            if context:
                # Internal recovery/accounting receipt. public_dict deliberately
                # stays unchanged: IDs, tokens and provider bodies do not leak.
                exc.apify_context = dict(context)
            raise

    @staticmethod
    def _emit_telemetry(
        callback: Callable[[str, Mapping[str, Any]], None] | None,
        event: str,
        context: Mapping[str, Any],
    ) -> None:
        if callback is None:
            return
        record = {
            key: context.get(key)
            for key in (
                "trace_run_id", "purpose", "run_id", "started_at", "finished_at",
                "duration_ms", "status", "reserved_max_cost_usd", "actual_cost_usd",
                "accounted_cost_usd", "cost_estimated", "items", "cancel_requested",
                "abort_requested", "abort_confirmed", "used", "error_code", "error_type",
                "reservation_accounting", "phase", "http_status", "provider_error_code",
                "sanitized_response", "network_error_type", "network_errno", "actor_id",
                "start_run_duration_ms",
                "run_id_received", "request_reached_provider", "no_run_proven",
            )
        }
        record["requested_max_total_charge_usd"] = record.pop("reserved_max_cost_usd", None)
        try:
            callback(event, record)
        except Exception:
            pass

    @staticmethod
    def _remember_run(context: dict[str, Any], run: Mapping[str, Any]) -> None:
        for source, target in (("id", "run_id"), ("defaultDatasetId", "dataset_id"), ("status", "status")):
            if run.get(source):
                context[target] = str(run[source])[:160]
        if "usageTotalUsd" in run and run["usageTotalUsd"] is not None:
            try:
                cost = float(run["usageTotalUsd"])
            except (TypeError, ValueError):
                cost = -1.0
            if math.isfinite(cost) and cost >= 0:
                context["actual_cost_usd"] = max(context.get("actual_cost_usd") or 0.0, cost)
        if run.get("finishedAt") or run.get("startedAt"):
            context["observed_at"] = str(run.get("finishedAt") or run.get("startedAt"))[:80]

    @staticmethod
    def _complete_event_cost(run: Mapping[str, Any], item_count: int) -> float | None:
        """SUCCEEDED precedes final usage billing; complete event counts are evidence."""
        pricing = run.get("pricingInfo")
        per_event = pricing.get("pricingPerEvent") if isinstance(pricing, Mapping) else None
        prices = per_event.get("actorChargeEvents") if isinstance(per_event, Mapping) else None
        counts = run.get("chargedEventCounts")
        if not isinstance(prices, Mapping) or not isinstance(counts, Mapping):
            return None
        required = {"listing-scraped": item_count, "detail-enriched": item_count,
                    "apify-default-dataset-item": item_count, "apify-actor-start": 1}
        total = 0.0
        try:
            for event, minimum in required.items():
                if float(counts.get(event, -1)) < minimum:
                    return None
            for event, value in counts.items():
                count = float(value)
                if not math.isfinite(count) or count < 0 or not count.is_integer():
                    return None
                if not count:
                    continue
                event_price = prices.get(event)
                price = float(event_price["eventPriceUsd"]) if isinstance(event_price, Mapping) else -1.0
                if not math.isfinite(price) or price < 0:
                    return None
                total += count * price
        except (TypeError, ValueError, KeyError):
            return None
        return total

    def _collect_payload_once(
        self, payload: dict[str, Any], requested_count: int,
        deadline_at: float | None, max_charge_usd: float | None,
        context: dict[str, Any],
        cancel_requested: Callable[[], bool] | None = None,
        telemetry: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> CollectionBatch:
        self._raise_if_cancelled(cancel_requested, context=context)
        if self._upgrade_blocked_until > time.monotonic():
            raise ApifyPlanRequiredError(_UPGRADE_REQUIRED_MESSAGE)
        allowance = self.config.effective_apify_max_charge_usd
        if max_charge_usd is not None:
            allowance = min(allowance, self._positive_float(max_charge_usd))
        affordable = math.floor(max(0.0, allowance - self.config.apify_start_cost_usd)
                                / self.config.apify_full_listing_cost_usd)
        capped_results = min(requested_count, self.config.safe_apify_listing_limit, affordable)
        if capped_results < 1:
            raise ExternalServiceError("Лимита недостаточно для следующего сбора.",
                                       code="COLLECTION_BUDGET", retryable=False)
        payload = dict(payload, maxResults=capped_results)
        if "listingUrls" in payload:
            payload["listingUrls"] = payload["listingUrls"][:capped_results]
        if deadline_at is not None and deadline_at - time.monotonic() < 5:
            raise ExternalServiceError("Время сбора истекло.", code="APIFY_TIMEOUT")
        print(f"[zen] Запускаю сбор: максимум {capped_results} объявлений.", flush=True)
        actor = quote(self.config.apify_actor_id.replace("/", "~"), safe="~")
        fast_deadline = deadline_at is not None
        total_remaining = max(1.0, (deadline_at or (time.monotonic() + 420)) - time.monotonic())
        initial_wait = max(1, min(20 if fast_deadline else 120, int(total_remaining - 18)))
        query = urlencode({
            "waitForFinish": initial_wait,
            "memory": 512,
            "maxTotalChargeUsd": f"{math.floor(allowance * 10000) / 10000:.4f}",
        })
        start_url = f"{self.config.apify_api_url}/acts/{actor}/runs?{query}"
        # This is the last cancel check before the paid start commitment.
        self._raise_if_cancelled(cancel_requested, context=context)
        # Reserve before POST: a timeout can conceal a successfully billed start.
        # Keep the full reservation after any ambiguous failure or delayed billing.
        context.update({
            "phase": "reservation",
            "actor_id": (self.config.apify_actor_id if re.fullmatch(
                r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", self.config.apify_actor_id) else "<invalid-actor-id>"),
            "run_id_received": False,
            "request_reached_provider": False,
            "no_run_proven": True,
        })
        if self.spending_guard is not None:
            context["reservation_id"] = self.spending_guard.reserve(allowance)
        context["reserved_max_cost_usd"] = allowance
        context["paid_post_attempted"] = True
        context["phase"] = "start_run"
        context["request_reached_provider"] = None
        context["no_run_proven"] = False
        self._emit_telemetry(telemetry, "starting", context)
        start_mono = time.monotonic()
        try:
            response = self._json_request(
                "POST", start_url, payload=payload,
                timeout=min(130, initial_wait + 8, total_remaining),
            )
        except ExternalServiceError as exc:
            context["start_run_duration_ms"] = round((time.monotonic() - start_mono) * 1000)
            context.update(exc.diagnostics)
            raise
        context["start_run_duration_ms"] = round((time.monotonic() - start_mono) * 1000)
        context["request_reached_provider"] = True
        run = response.get("data") if isinstance(response, Mapping) else None
        if not isinstance(run, Mapping):
            context["sanitized_response"] = "malformed_start_response"
            raise ExternalServiceError("Apify не вернул данные запуска.", code="APIFY_RESPONSE_INVALID")

        context.update({
            "reserved_max_cost_usd": allowance,
            "actual_cost_usd": None,
            "billing_complete": False,
            "requested_count": requested_count,
            "capped_count": capped_results,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })
        self._remember_run(context, run)

        run_id = str(run.get("id") or "")
        context["run_id_received"] = bool(run_id)
        if not run_id:
            context["sanitized_response"] = "missing_run_id"
            raise ExternalServiceError("Apify не вернул идентификатор запуска.",
                                       code="APIFY_RESPONSE_INVALID")
        self._emit_telemetry(telemetry, "started", context)
        self._raise_if_cancelled(cancel_requested, run_id, context)
        status = str(run.get("status") or "").upper()
        last_status = status
        print(f"[zen] Статус: {status or 'UNKNOWN'}.", flush=True)
        deadline = min(
            deadline_at - 3 if fast_deadline else time.monotonic() + 420,
            time.monotonic() + 420,
        )
        while status not in {"SUCCEEDED", *_TERMINAL_FAILURES} and time.monotonic() < deadline:
            self._raise_if_cancelled(cancel_requested, run_id, context)
            if not run_id:
                raise ExternalServiceError("Apify не вернул идентификатор запуска.")
            wait_seconds = max(1, min(8 if fast_deadline else 60, int(deadline - time.monotonic())))
            poll_url = (
                f"{self.config.apify_api_url}/actor-runs/{quote(run_id)}"
                f"?waitForFinish={wait_seconds}"
            )
            poll_timeout = min(wait_seconds + 5, max(0.1, deadline_at - time.monotonic())) if fast_deadline else wait_seconds + 5
            response = self._json_request("GET", poll_url, timeout=poll_timeout)
            run = response.get("data") if isinstance(response, Mapping) else None
            if not isinstance(run, Mapping):
                raise ExternalServiceError("Не удалось получить состояние запуска Apify.")
            self._remember_run(context, run)
            status = str(run.get("status") or "").upper()
            if status != last_status:
                last_status = status
                print(f"[zen] Статус: {status or 'UNKNOWN'}.", flush=True)

        self._raise_if_cancelled(cancel_requested, run_id, context)

        if status in _TERMINAL_FAILURES:
            message = str(run.get("statusMessage") or "").casefold()
            free_plan = "free" in message or "бесплат" in message
            plan_limit = any(word in message for word in ("limit", "100", "upgrade", "лимит", "тариф"))
            if free_plan and plan_limit:
                self._upgrade_blocked_until = time.monotonic() + 60
                raise ApifyPlanRequiredError(_UPGRADE_REQUIRED_MESSAGE)
            raise ExternalServiceError(f"Сбор Avito завершился со статусом {status}.")
        if status != "SUCCEEDED":
            self._abort_run(run_id)
            raise ExternalServiceError(
                "Сбор Avito не завершился за отведённое время.",
                code="APIFY_TIMEOUT",
            )

        dataset_id = str(run.get("defaultDatasetId") or "")
        if not dataset_id:
            raise ExternalServiceError("Apify не вернул набор результатов.")
        dataset_query = urlencode({"clean": "true", "format": "json", "limit": capped_results})
        dataset_url = f"{self.config.apify_api_url}/datasets/{quote(dataset_id)}/items?{dataset_query}"
        dataset_timeout = 45.0
        if fast_deadline:
            dataset_timeout = min(dataset_timeout, deadline_at - time.monotonic())
        if dataset_timeout <= 0:
            raise ExternalServiceError("Время загрузки собранных результатов истекло.", code="APIFY_TIMEOUT")
        items = self._json_request("GET", dataset_url, timeout=dataset_timeout)
        if not isinstance(items, list):
            raise ExternalServiceError("Набор результатов Apify имеет неожиданный формат.")
        if any(
            isinstance(item, Mapping) and bool(item.get("_upgradeRequired"))
            for item in items
        ):
            # Keep a short cooldown so repeated clicks do not create paid runs,
            # but recover automatically after the operator upgrades the plan.
            self._upgrade_blocked_until = time.monotonic() + 60
            raise ApifyPlanRequiredError(_UPGRADE_REQUIRED_MESSAGE)
        observed_at = str(context.get("observed_at") or datetime.now(timezone.utc).isoformat())
        safe_items = tuple(dict(item, scrapedAt=item.get("scrapedAt") or item.get("collectedAt") or observed_at)
                           for item in items if isinstance(item, Mapping))
        listing_markers = {
            "id", "avitoId", "avito_id", "itemId", "listingId", "adId",
            "url", "listingUrl", "itemUrl", "canonicalUrl", "link",
        }
        if safe_items and not any(listing_markers.intersection(item) for item in safe_items):
            raise ExternalServiceError(
                "Zen Studio вернул служебную запись вместо объявлений.",
                code="APIFY_DATASET_INVALID",
                retryable=False,
            )
        self._upgrade_blocked_until = 0.0
        # The first SUCCEEDED response can contain only the Actor start charge.
        # Refresh this existing run; this is a read and never creates another run.
        receipt_timeout = min(10.0, deadline_at - time.monotonic()) if fast_deadline else 10.0
        if run_id and receipt_timeout > 0.5:
            try:
                receipt_response = self._json_request(
                    "GET", f"{self.config.apify_api_url}/actor-runs/{quote(run_id)}", timeout=receipt_timeout,
                )
                receipt_run = receipt_response.get("data") if isinstance(receipt_response, Mapping) else None
                if isinstance(receipt_run, Mapping):
                    run = dict(run, **receipt_run)
                    self._remember_run(context, run)
            except ExternalServiceError:
                pass  # Use conservative accounting below when billing is unavailable.
        actual_cost = context.get("actual_cost_usd")
        event_cost = self._complete_event_cost(run, len(safe_items))
        estimated = event_cost is None
        conservative_cost = len(safe_items) * self.config.apify_full_listing_cost_usd + self.config.apify_start_cost_usd
        cost = max(actual_cost or 0.0, conservative_cost if estimated else event_cost)
        context["billing_complete"] = not estimated
        context["accounted_cost_usd"] = cost
        label = "оценка" if estimated else "факт"
        print(f"[zen] Получено {len(safe_items)} объявлений; расход ${cost:.4f} ({label}).", flush=True)
        return CollectionBatch(
            items=safe_items,
            apify_cost_usd=cost,
            apify_cost_estimated=estimated,
            requested_count=requested_count,
            capped_count=capped_results,
        )
