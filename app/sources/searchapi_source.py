"""SearchApi Google Shopping source."""
from __future__ import annotations

import logging
from queue import Empty, Queue
from threading import Thread
import time
from types import SimpleNamespace
from typing import Any

import requests

from app.config import settings
from app.price_extractor import extract_price
from app.price_guard import normalize_price_candidate

logger = logging.getLogger(__name__)

SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"
SEARCHAPI_SOURCE = "searchapi_google_shopping"


def _max_results() -> int:
    try:
        return max(1, min(int(settings.SEARCHAPI_MAX_RESULTS or 5), 20))
    except (TypeError, ValueError):
        return 5


def _timeout_seconds() -> int:
    try:
        return max(1, int(settings.SEARCHAPI_TIMEOUT_SECONDS or 15))
    except (TypeError, ValueError):
        return 15


def _request_params(
    query: str,
    *,
    gl: str | None = None,
    hl: str | None = None,
    location: str | None = None,
    simplified: bool = False,
) -> dict[str, str]:
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": settings.SEARCHAPI_API_KEY,
    }
    optional = {
        "gl": settings.SEARCHAPI_GL if gl is None else gl,
        "hl": settings.SEARCHAPI_HL if hl is None else hl,
        "location": settings.SEARCHAPI_LOCATION if location is None else location,
    }
    for key, value in optional.items():
        text = str(value or "").strip()
        if text or not simplified:
            params[key] = text
    return params


def _public_params(params: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in params.items() if key != "api_key"}


def _request_attempt(params: dict[str, str], attempt_timeout: float) -> requests.Response:
    result_queue: Queue = Queue(maxsize=1)
    request_timeout = (
        max(0.5, min(3.0, attempt_timeout)),
        max(0.5, attempt_timeout),
    )

    def run() -> None:
        try:
            response = requests.get(SEARCHAPI_ENDPOINT, params=params, timeout=request_timeout)
        except Exception as exc:
            result_queue.put(("error", exc))
            return
        result_queue.put(("response", response))

    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(max(0.5, attempt_timeout))
    if thread.is_alive():
        raise requests.exceptions.ReadTimeout(
            f"SearchApi request exceeded {attempt_timeout:.1f}s attempt limit"
        )
    try:
        kind, value = result_queue.get_nowait()
    except Empty as exc:
        raise requests.exceptions.RequestException("SearchApi request ended without response") from exc
    if kind == "error":
        raise value
    return value


def _request_with_deadline(
    params: dict[str, str],
    *,
    started: float,
    total_timeout: int,
) -> tuple[requests.Response, int]:
    deadline = started + max(0.5, total_timeout - 0.5)
    retry_count = 0
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout(f"SearchApi source timeout after {total_timeout}s")
        attempts_left = 2 - attempt
        attempt_timeout = remaining if attempt else max(0.5, remaining / attempts_left)
        try:
            return _request_attempt(params, attempt_timeout), retry_count
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            if attempt == 0 and deadline - time.monotonic() > 0.5:
                retry_count += 1
                logger.debug("SearchApi Google Shopping retry after connection/read timeout")
                continue
            setattr(exc, "searchapi_retry_count", retry_count)
            raise


def _parsed_namespace(parsed: dict | None) -> SimpleNamespace:
    parsed = parsed or {}
    return SimpleNamespace(
        budget=parsed.get("budget", ""),
        original_query=parsed.get("original_query", "") or parsed.get("query", ""),
        product_name=parsed.get("product_name", "") or parsed.get("product", ""),
        product=parsed.get("product", "") or parsed.get("product_name", ""),
        important_criteria=parsed.get("important_criteria", ""),
    )


def _as_int_price(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(round(value)) if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    parsed = extract_price(text, min_price=1, max_price=10_000_000)
    if parsed is not None:
        return parsed
    if not any(marker in text.lower() for marker in ("₽", "руб", "rub", "р.")):
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _reviews_count(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def _map_item(item: dict[str, Any], parsed: dict | None) -> dict | None:
    title = _first_text(item, "title", "name")
    link = _first_text(item, "link", "product_link", "offers_link")
    if not title or not link:
        return None

    price = _as_int_price(item.get("extracted_price"))
    if price is None:
        price = _as_int_price(item.get("price"))

    price_text = _first_text(item, "price", "extracted_price")
    seller = _first_text(item, "seller", "source", "merchant", "store")
    guard = normalize_price_candidate(
        None,
        _parsed_namespace(parsed),
        price=price,
        raw_price=price,
        price_source="searchapi",
        source=SEARCHAPI_SOURCE,
        title=title,
        snippet=f"{seller} {price_text}",
        text=f"{title} {seller} {price_text}",
    )

    has_store_link = bool(_first_text(item, "link", "product_link"))
    price_reliability = "medium" if guard.price is not None and has_store_link else "none"
    risk_flags: list[str] = []
    if not has_store_link:
        price_reliability = "low" if guard.price is not None else "none"
        risk_flags.append("нет прямой ссылки на товар/магазин")

    return {
        "title": title,
        "price": guard.price,
        "url": link,
        "source": SEARCHAPI_SOURCE,
        "store": seller,
        "seller": seller,
        "rating": item.get("rating"),
        "reviews_count": _reviews_count(item.get("reviews") or item.get("reviews_count")),
        "price_source": "structured_api" if guard.price is not None else "",
        "price_reliability": price_reliability,
        "price_rejected_reason": guard.price_rejected_reason,
        "price_from_budget_suspect": guard.price_from_budget_suspect,
        "bad_price_context": guard.bad_price_context,
        "external_source": "searchapi",
        "risk_flags": risk_flags,
        "raw": item,
        "body": " ".join(part for part in (seller, price_text) if part),
    }


def _iter_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("shopping_results", "shopping_ads", "popular_products"):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def debug_searchapi_google_shopping(
    query: str,
    parsed: dict | None = None,
    *,
    gl: str | None = None,
    hl: str | None = None,
    location: str | None = None,
    simplified_params: bool = False,
) -> dict[str, Any]:
    timeout_seconds = _timeout_seconds()
    params = _request_params(query, gl=gl, hl=hl, location=location, simplified=simplified_params)
    info: dict[str, Any] = {
        "enabled": bool(settings.SEARCHAPI_ENABLED),
        "api_key_present": bool(settings.SEARCHAPI_API_KEY),
        "endpoint": SEARCHAPI_ENDPOINT,
        "status": "disabled",
        "error_class": "",
        "error": "",
        "elapsed": 0.0,
        "top_level_keys": [],
        "count": 0,
        "candidates": [],
        "params": _public_params(params),
        "timeout": timeout_seconds,
        "retry_count": 0,
    }
    if not settings.SEARCHAPI_ENABLED or not settings.SEARCHAPI_API_KEY:
        if settings.SEARCHAPI_ENABLED and not settings.SEARCHAPI_API_KEY:
            info["status"] = "missing_api_key"
        return info

    started = time.monotonic()
    try:
        response, retry_count = _request_with_deadline(params, started=started, total_timeout=timeout_seconds)
        info["elapsed"] = round(time.monotonic() - started, 3)
        info["retry_count"] = retry_count
        info["status_code"] = response.status_code
        if info["elapsed"] > timeout_seconds:
            raise requests.exceptions.Timeout(f"SearchApi source timeout after {timeout_seconds}s")
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.ReadTimeout as exc:
        info["elapsed"] = round(time.monotonic() - started, 3)
        info["retry_count"] = getattr(exc, "searchapi_retry_count", info["retry_count"])
        info["status"] = "timeout"
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]
        logger.debug("SearchApi Google Shopping timeout: query=%r elapsed=%.3fs", query, info["elapsed"])
        return info
    except requests.exceptions.ConnectionError as exc:
        info["elapsed"] = round(time.monotonic() - started, 3)
        info["retry_count"] = getattr(exc, "searchapi_retry_count", info["retry_count"])
        info["status"] = "connection_error"
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]
        logger.debug(
            "SearchApi Google Shopping connection error: query=%r elapsed=%.3fs error=%s",
            query, info["elapsed"], info["error"],
        )
        return info
    except requests.exceptions.Timeout as exc:
        info["elapsed"] = round(time.monotonic() - started, 3)
        info["status"] = "timeout"
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]
        logger.debug("SearchApi Google Shopping timeout: query=%r elapsed=%.3fs", query, info["elapsed"])
        return info
    except requests.exceptions.HTTPError as exc:
        info["elapsed"] = round(time.monotonic() - started, 3)
        error_response = getattr(exc, "response", None)
        status_code = getattr(error_response, "status_code", None)
        if status_code is not None:
            info["status_code"] = status_code
        if error_response is not None:
            try:
                error_payload = error_response.json()
            except ValueError:
                error_payload = None
            if isinstance(error_payload, dict):
                info["top_level_keys"] = sorted(str(key) for key in error_payload.keys())[:30]
        info["status"] = "auth" if status_code in {401, 403} else "http_error"
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]
        logger.debug(
            "SearchApi Google Shopping failed: query=%r status=%s elapsed=%.3fs",
            query, info["status"], info["elapsed"],
        )
        return info
    except requests.exceptions.RequestException as exc:
        info["elapsed"] = round(time.monotonic() - started, 3)
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            info["status_code"] = status_code
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]
        info["status"] = "error"
        logger.debug(
            "SearchApi Google Shopping failed: query=%r status=error elapsed=%.3fs error=%s",
            query, info["elapsed"], info["error"],
        )
        return info
    except ValueError as exc:
        info["elapsed"] = round(time.monotonic() - started, 3)
        info["status"] = "bad_json"
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]
        logger.debug("SearchApi Google Shopping bad json: query=%r elapsed=%.3fs", query, info["elapsed"])
        return info
    if not isinstance(payload, dict):
        info["status"] = "bad_json"
        info["error_class"] = "TypeError"
        info["error"] = f"unexpected json type: {type(payload).__name__}"
        logger.debug("SearchApi Google Shopping bad json: query=%r elapsed=%.3fs", query, info["elapsed"])
        return info

    info["top_level_keys"] = sorted(str(key) for key in payload.keys())[:30]
    rows: list[dict] = []
    for item in _iter_items(payload):
        mapped = _map_item(item, parsed)
        if mapped:
            rows.append(mapped)
        if len(rows) >= _max_results():
            break
    info["candidates"] = rows
    info["count"] = len(rows)
    info["status"] = "ok" if rows else "empty"
    logger.info(
        "SearchApi Google Shopping: query=%r status=%s elapsed=%.3fs count=%d keys=%s",
        query, info["status"], info["elapsed"], info["count"], info["top_level_keys"],
    )
    return info


def search_searchapi_google_shopping(query: str, parsed: dict | None = None) -> list[dict]:
    debug = debug_searchapi_google_shopping(query, parsed=parsed)
    return list(debug.get("candidates") or [])
