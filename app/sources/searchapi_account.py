"""Safe SearchApi account/quota preflight.

The account endpoint does not execute a search. It is used before large live
acceptance runs so a free or limited plan is not exhausted accidentally.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any

import requests

from app.config import settings


SEARCHAPI_ACCOUNT_ENDPOINT = "https://www.searchapi.io/api/v1/me"


@dataclass(frozen=True)
class SearchApiUsage:
    status: str
    current_month_usage: int | None = None
    monthly_allowance: int | None = None
    remaining_credits: int | None = None
    searches_this_hour: int | None = None
    hourly_rate_limit: int | None = None
    period_start: str = ""
    period_end: str = ""
    status_code: int | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _integer(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_searchapi_usage(
    *,
    api_key: str | None = None,
    timeout: float = 12.0,
    request_get: Any = requests.get,
) -> SearchApiUsage:
    key = str(api_key if api_key is not None else settings.SEARCHAPI_API_KEY or os.getenv("SEARCHAPI_API_KEY", "")).strip()
    if not key:
        return SearchApiUsage(status="missing_api_key", error="SEARCHAPI_API_KEY is not configured")
    try:
        response = request_get(
            SEARCHAPI_ACCOUNT_ENDPOINT,
            params={"api_key": key},
            timeout=(min(5.0, timeout), max(1.0, timeout)),
        )
    except requests.exceptions.Timeout as exc:
        return SearchApiUsage(status="timeout", error=str(exc)[:240])
    except requests.exceptions.RequestException as exc:
        return SearchApiUsage(status="error", error=str(exc)[:240])

    status_code = getattr(response, "status_code", None)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if status_code is not None and status_code >= 400:
        error = str(payload.get("error") or payload.get("message") or getattr(response, "text", ""))[:300]
        status = "unauthorized" if status_code in {401, 403} else ("rate_limited" if status_code == 429 else "error")
        return SearchApiUsage(status=status, status_code=status_code, error=error)

    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    api_usage = payload.get("api_usage") if isinstance(payload.get("api_usage"), dict) else {}
    subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else {}
    return SearchApiUsage(
        status="success",
        current_month_usage=_integer(account.get("current_month_usage")),
        monthly_allowance=_integer(account.get("monthly_allowance")),
        remaining_credits=_integer(account.get("remaining_credits")),
        searches_this_hour=_integer(api_usage.get("searches_this_hour")),
        hourly_rate_limit=_integer(api_usage.get("hourly_rate_limit")),
        period_start=str(subscription.get("period_start") or ""),
        period_end=str(subscription.get("period_end") or ""),
        status_code=status_code,
    )


__all__ = ["SEARCHAPI_ACCOUNT_ENDPOINT", "SearchApiUsage", "get_searchapi_usage"]
