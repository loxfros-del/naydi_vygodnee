from __future__ import annotations

from dataclasses import dataclass
import time
from urllib.parse import urlparse

import requests

from app.config import settings


@dataclass
class FetchResult:
    ok: bool
    status_code: int | None = None
    html: str = ""
    final_url: str = ""
    blocked: bool = False
    blocked_reason: str = ""
    used_proxy: bool = False
    used_browser: bool = False
    fetch_provider: str = "http"
    retry_count: int = 0
    error: str = ""


_CACHE: dict[str, FetchResult] = {}
_LAST_DOMAIN_HIT: dict[str, float] = {}


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _sleep_for_domain(url: str) -> None:
    domain = _domain(url)
    if not domain:
        return

    delay = max(0, int(getattr(settings, "DOMAIN_RATE_LIMIT_SECONDS", 2) or 0))
    if delay <= 0:
        return

    now = time.time()
    last = _LAST_DOMAIN_HIT.get(domain, 0)
    wait = delay - (now - last)
    if wait > 0:
        time.sleep(wait)

    _LAST_DOMAIN_HIT[domain] = time.time()


def _proxy_config() -> dict[str, str] | None:
    use_proxy = bool(getattr(settings, "USE_PROXY", False))
    proxy_for_http = bool(getattr(settings, "PROXY_FOR_HTTP", True))
    proxy_url = str(getattr(settings, "PROXY_URL", "") or "").strip()

    if not use_proxy or not proxy_for_http or not proxy_url:
        return None

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def _blocked_reason(status_code: int | None, text: str = "") -> str:
    lowered = (text or "").lower()

    if status_code == 401:
        return "unauthorized"
    if status_code == 403:
        return "forbidden"
    if status_code == 429:
        return "rate_limited"
    if status_code == 498:
        return "blocked_498"

    if "captcha" in lowered or "капча" in lowered:
        return "captcha"
    if "access denied" in lowered or "доступ запрещ" in lowered:
        return "access_denied"
    if "too many requests" in lowered:
        return "rate_limited"

    return ""


def fetch_http(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    use_cache: bool = True,
) -> FetchResult:
    if use_cache and url in _CACHE:
        cached = _CACHE[url]
        return FetchResult(**{**cached.__dict__, "fetch_provider": "http_cache"})

    timeout = max(2, int(getattr(settings, "FETCH_TIMEOUT_SECONDS", 20) or 20))
    retries = max(0, int(getattr(settings, "FETCH_RETRIES", 2) or 2))
    proxies = _proxy_config()
    used_proxy = proxies is not None

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    }
    if headers:
        request_headers.update(headers)

    last_error = ""

    for attempt in range(retries + 1):
        try:
            _sleep_for_domain(url)

            response = requests.get(
                url,
                params=params,
                headers=request_headers,
                timeout=timeout,
                allow_redirects=True,
                proxies=proxies,
            )

            status_code = response.status_code
            reason = _blocked_reason(status_code, response.text[:2000])

            if reason:
                result = FetchResult(
                    ok=False,
                    status_code=status_code,
                    html=response.text or "",
                    final_url=response.url or url,
                    blocked=True,
                    blocked_reason=reason,
                    used_proxy=used_proxy,
                    retry_count=attempt,
                )
                _CACHE[url] = result
                return result

            if 500 <= status_code <= 599 and attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue

            response.raise_for_status()

            if not response.encoding or response.encoding.lower() in {"iso-8859-1", "windows-1252"}:
                response.encoding = response.apparent_encoding or "utf-8"

            result = FetchResult(
                ok=True,
                status_code=status_code,
                html=response.text or "",
                final_url=response.url or url,
                used_proxy=used_proxy,
                retry_count=attempt,
            )
            _CACHE[url] = result
            return result

        except requests.Timeout as exc:
            last_error = f"timeout: {exc}"
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue

            result = FetchResult(
                ok=False,
                blocked=True,
                blocked_reason="timeout",
                used_proxy=used_proxy,
                retry_count=attempt,
                error=last_error,
            )
            _CACHE[url] = result
            return result

        except requests.RequestException as exc:
            last_error = str(exc)
            reason = _blocked_reason(None, last_error)
            result = FetchResult(
                ok=False,
                blocked=bool(reason),
                blocked_reason=reason or "request_error",
                used_proxy=used_proxy,
                retry_count=attempt,
                error=last_error,
            )
            _CACHE[url] = result
            return result

    return FetchResult(
        ok=False,
        blocked_reason="unknown_error",
        used_proxy=used_proxy,
        retry_count=retries,
        error=last_error,
    )