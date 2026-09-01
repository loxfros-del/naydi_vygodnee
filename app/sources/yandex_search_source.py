"""Yandex Search API XML source for product-link discovery.

The provider returns web documents, so prices are intentionally not trusted here.
Existing product-page verification extracts the actual price later in the pipeline.
"""
from __future__ import annotations

import logging
from importlib.util import find_spec
import time
from typing import Any, Mapping
from xml.etree import ElementTree

from app.config import settings

logger = logging.getLogger(__name__)

YANDEX_SEARCH_SOURCE = "yandex_search_api"

# Region is passed only for cities with a documented Yandex id. Unknown text
# stays out of the API parameter instead of becoming an invalid region.
_CITY_REGION_IDS = {
    "москва": "213",
    "москва и московская область": "1",
    "московская область": "1",
    "санкт-петербург": "2",
    "санкт петербург": "2",
    "петербург": "2",
    "спб": "2",
    "ярославль": "16",
}


def _max_results() -> int:
    try:
        return max(1, min(int(settings.YANDEX_SEARCH_MAX_RESULTS or 10), 20))
    except (TypeError, ValueError):
        return 10


def _timeout_seconds(value: Any = None) -> float:
    try:
        timeout = float(value if value is not None else settings.YANDEX_SEARCH_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = 15.0
    return max(2.0, min(timeout, 30.0))


def yandex_region_for_city(value: Any) -> str | None:
    """Return a documented Yandex region id or ``None`` for an unknown city."""
    city = " ".join(str(value or "").replace("ё", "е").casefold().split())
    return _CITY_REGION_IDS.get(city)


def yandex_search_configuration_status() -> str:
    """Return a client-safe setup state without exposing any credential values."""
    if not bool(settings.YANDEX_SEARCH_API_ENABLED):
        return "disabled"
    if not settings.YANDEX_SEARCH_API_KEY or not settings.YANDEX_SEARCH_FOLDER_ID:
        return "missing_credentials"
    return "configured"


def yandex_search_runtime_status() -> str:
    """Return setup/readiness without invoking Yandex or exposing a secret."""
    configuration = yandex_search_configuration_status()
    if configuration != "configured":
        return configuration
    return "ready" if find_spec("yandex_ai_studio_sdk") is not None else "sdk_missing"


def _search_xml(query: str, *, region: str | None = None, timeout: Any = None) -> bytes:
    # Import lazily: the bot remains runnable while the optional source is disabled.
    from yandex_ai_studio_sdk import AIStudio

    sdk = AIStudio(folder_id=settings.YANDEX_SEARCH_FOLDER_ID, auth=settings.YANDEX_SEARCH_API_KEY)
    options: dict[str, Any] = {
        "family_mode": "moderate",
        "fix_typo_mode": "on",
        "groups_on_page": _max_results(),
        "group_mode": "deep",
        "docs_in_group": 1,
        "max_passages": 2,
        "localization": "ru",
    }
    if region and str(region).isdigit():
        options["region"] = str(region)
    search = sdk.search_api.web("ru", **options)
    return search.run(
        str(query or "")[:400],
        format="xml",
        page=0,
        timeout=_timeout_seconds(timeout),
    )


def _request_city(parsed: Mapping[str, Any] | None) -> str:
    if not isinstance(parsed, Mapping):
        return ""
    return str(parsed.get("city") or parsed.get("location") or "")


def _text(node: ElementTree.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def parse_yandex_xml(payload: bytes | str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(payload)
    rows: list[dict[str, str]] = []
    for doc in root.findall(".//doc"):
        url = _text(doc.find("url"))
        title = _text(doc.find("headline")) or _text(doc.find("title"))
        snippet = _text(doc.find("passages")) or _text(doc.find("headline"))
        if url and title:
            rows.append({
                "title": title,
                "url": url,
                "source": YANDEX_SEARCH_SOURCE,
                "store": _text(doc.find("domain")),
                "body": snippet,
                "raw": {"url": url, "title": title, "snippet": snippet},
            })
        if len(rows) >= _max_results():
            break
    return rows


def debug_yandex_search(
    query: str,
    parsed: Mapping[str, Any] | None = None,
    *,
    timeout: Any = None,
) -> dict[str, Any]:
    requested_city = _request_city(parsed)
    region_id = yandex_region_for_city(requested_city)
    info: dict[str, Any] = {
        "enabled": bool(settings.YANDEX_SEARCH_API_ENABLED),
        "api_key_present": bool(settings.YANDEX_SEARCH_API_KEY),
        "folder_id_present": bool(settings.YANDEX_SEARCH_FOLDER_ID),
        "status": yandex_search_runtime_status(),
        "error": "",
        "error_class": "",
        "elapsed": 0.0,
        "candidates": [],
        "count": 0,
        "region_id": region_id or "",
        "region_confirmed": bool(region_id),
    }
    if info["status"] != "ready":
        return info
    started = time.monotonic()
    try:
        rows = parse_yandex_xml(
            _search_xml(
                query,
                region=region_id,
                timeout=timeout,
            )
        )
    except Exception as exc:
        info["elapsed"] = round(time.monotonic() - started, 3)
        info["status"] = "error"
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]
        logger.warning("Yandex Search API failed for %r: %s", query, exc)
        return info
    info["elapsed"] = round(time.monotonic() - started, 3)
    info["candidates"] = rows
    info["count"] = len(rows)
    info["status"] = "ok" if rows else "empty"
    return info


__all__ = [
    "YANDEX_SEARCH_SOURCE",
    "debug_yandex_search",
    "parse_yandex_xml",
    "yandex_region_for_city",
    "yandex_search_configuration_status",
    "yandex_search_runtime_status",
]
