"""Diagnostic runner for SearchApi Google Shopping source."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.sources.searchapi_source import debug_searchapi_google_shopping


CASES = (
    ("A", "sony headphones", "us", "en", ""),
    ("B", "наушники sony", "ru", "ru", ""),
    ("C", "iphone 16 pro 256", "ru", "ru", "Russia"),
)


def _bool_text(value: object) -> str:
    return "true" if bool(value) else "false"


def _print_candidate(index: int, item: dict) -> None:
    print(f"  {index}. title: {item.get('title') or '-'}")
    print(f"     price: {item.get('price') if item.get('price') is not None else '-'}")
    print(f"     url: {item.get('url') or '-'}")
    print(f"     store: {item.get('store') or item.get('seller') or '-'}")
    print(f"     price_source: {item.get('price_source') or '-'}")
    print(f"     price_reliability: {item.get('price_reliability') or '-'}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"SEARCHAPI_ENABLED: {_bool_text(settings.SEARCHAPI_ENABLED)}")
    print(f"API key present: {_bool_text(settings.SEARCHAPI_API_KEY)}")
    print(f"configured timeout: {settings.SEARCHAPI_TIMEOUT_SECONDS}")

    for label, query, gl, hl, location in CASES:
        print("\n" + "=" * 72)
        print(f"case: {label}")
        print(f"q: {query}")
        debug = debug_searchapi_google_shopping(
            query,
            gl=gl,
            hl=hl,
            location=location,
            simplified_params=True,
        )
        print(f"endpoint: {debug.get('endpoint') or '-'}")
        print(
            "params without api_key: "
            + json.dumps(debug.get("params") or {}, ensure_ascii=False, sort_keys=True)
        )
        print(f"timeout: {debug.get('timeout', 0)}")
        print(f"elapsed: {debug.get('elapsed', 0)}s")
        print(f"retry_count: {debug.get('retry_count', 0)}")
        if "status_code" in debug:
            print(f"status_code: {debug.get('status_code')}")
        print(f"status: {debug.get('status') or '-'}")

        keys = debug.get("top_level_keys") or []
        print("top-level JSON keys: " + (", ".join(keys) if keys else "-"))
        print(f"count candidates: {debug.get('count', 0)}")

        error_class = debug.get("error_class") or "-"
        error_message = debug.get("error") or "-"
        print(f"error class: {error_class}")
        print(f"error message: {error_message}")

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
