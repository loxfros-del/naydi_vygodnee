"""Diagnostic runner for direct retail structured sources."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.sources.direct_retail_source import debug_search_direct_retail_sources


QUERIES = (
    "наушники до 10000",
    "ноутбук до 50000",
    "телевизор 4k до 45000",
)


def _print_candidate(index: int, item: dict) -> None:
    print(f"  {index}. title: {item.get('title') or '-'}")
    print(f"     price: {item.get('price') if item.get('price') is not None else '-'}")
    print(f"     url: {item.get('url') or '-'}")
    print(f"     source: {item.get('source') or '-'}")
    print(f"     price_source: {item.get('price_source') or '-'}")
    print(f"     price_reliability: {item.get('price_reliability') or '-'}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"DIRECT_RETAIL_ENABLED: {bool(settings.DIRECT_RETAIL_ENABLED)}")
    print(f"DIRECT_RETAIL_TIMEOUT_SECONDS: {settings.DIRECT_RETAIL_TIMEOUT_SECONDS}")
    print(f"DIRECT_RETAIL_MAX_RESULTS: {settings.DIRECT_RETAIL_MAX_RESULTS}")

    for query in QUERIES:
        print("\n" + "=" * 72)
        print(f"query: {query}")
        for debug in debug_search_direct_retail_sources(query):
            print("-" * 72)
            print(f"source: {debug.get('source') or '-'}")
            print(f"status: {debug.get('status') or '-'}")
            print(f"elapsed: {debug.get('elapsed', 0)}s")
            print(f"raw count: {debug.get('raw_count', 0)}")
            print(f"candidates count: {debug.get('candidates_count', 0)}")
            error = debug.get("error") or ""
            if error:
                print(f"error: {error}")
            candidates = list(debug.get("candidates") or [])
            if not candidates:
                print("first 3 candidates: -")
                continue
            print("first 3 candidates:")
            for index, item in enumerate(candidates[:3], 1):
                _print_candidate(index, item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
