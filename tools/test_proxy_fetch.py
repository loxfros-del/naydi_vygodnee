"""Быстрая проверка VPN/proxy через app.net_client.fetch_http.

Запуск:
    python tools/test_proxy_fetch.py
    python tools/test_proxy_fetch.py https://market.yandex.ru
"""
from __future__ import annotations

import sys
from pathlib import Path

# Позволяет запускать файл напрямую из tools/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.net_client import fetch_http


DEFAULT_URL = "https://example.com"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    result = fetch_http(url)

    print(f"url: {url}")
    print(f"ok: {result.ok}")
    print(f"status_code: {result.status_code or 0}")
    print(f"used_proxy: {result.used_proxy}")
    print(f"blocked_reason: {result.blocked_reason or '-'}")
    print(f"fetch_provider: {result.fetch_provider or '-'}")
    print(f"retry_count: {result.retry_count}")
    print(f"final_url: {result.final_url or '-'}")
    print()
    print("note: USE_PROXY=false даёт used_proxy=False даже при системном VPN.")
    print("note: при включённом PROXY_URL/USE_PROXY used_proxy должен стать True.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
