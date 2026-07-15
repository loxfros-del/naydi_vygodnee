"""One-query sanitized SearchApi price-shape probe for debugging V2 mapping."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:probe_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.sources.searchapi_source import debug_searchapi_google_shopping  # noqa: E402


OUTPUT = ROOT / "data" / "searchapi_price_probe.json"
PRICE_KEYS = (
    "price", "extracted_price", "base_price", "extracted_base_price",
    "old_price", "original_price", "currency", "source", "seller",
    "merchant", "store", "link", "product_link", "offers_link",
)


def clean_candidate(candidate: dict) -> dict:
    raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
    return {
        "title": str(candidate.get("title") or "")[:220],
        "mapped_price": candidate.get("price"),
        "price_source": candidate.get("price_source"),
        "price_reliability": candidate.get("price_reliability"),
        "price_rejected_reason": candidate.get("price_rejected_reason"),
        "price_from_budget_suspect": candidate.get("price_from_budget_suspect"),
        "bad_price_context": candidate.get("bad_price_context"),
        "seller": str(candidate.get("seller") or "")[:160],
        "url": str(candidate.get("url") or "")[:500],
        "raw_price_fields": {key: raw.get(key) for key in PRICE_KEYS if key in raw},
        "raw_keys": sorted(str(key) for key in raw.keys())[:80],
    }


def main() -> int:
    query = "Apple iPhone 16 Pro 256 ГБ новый купить"
    debug = debug_searchapi_google_shopping(
        query,
        parsed={
            "original_query": query,
            "product": "Apple iPhone 16 Pro",
            "product_name": "Apple iPhone 16 Pro",
            "budget": 80000,
        },
    )
    payload = {
        "status": debug.get("status"),
        "status_code": debug.get("status_code"),
        "error_class": debug.get("error_class"),
        "error": str(debug.get("error") or "")[:240],
        "elapsed": debug.get("elapsed"),
        "count": debug.get("count"),
        "top_level_keys": debug.get("top_level_keys"),
        "api_key_present": bool(debug.get("api_key_present")),
        "candidates": [clean_candidate(item) for item in (debug.get("candidates") or [])],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
