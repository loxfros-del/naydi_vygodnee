"""Retrieve one recent successful SearchApi shopping response without a new search."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "data" / "searchapi_history_probe.json"
HISTORY_URL = "https://www.searchapi.io/api/v1/search_history"
PRICE_KEYS = (
    "price", "extracted_price", "base_price", "extracted_base_price",
    "old_price", "original_price", "currency", "source", "seller",
    "merchant", "store", "link", "product_link", "offers_link",
)


def _shopping_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("shopping_results", "shopping_ads", "popular_products"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _sanitize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title") or item.get("name") or "")[:220],
        "price_fields": {key: item.get(key) for key in PRICE_KEYS if key in item},
        "keys": sorted(str(key) for key in item.keys())[:80],
    }


def main() -> int:
    api_key = os.getenv("SEARCHAPI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("SEARCHAPI_API_KEY is missing")
    response = requests.get(
        HISTORY_URL,
        params={"api_key": api_key, "limit": 100},
        timeout=(5, 20),
    )
    status_code = response.status_code
    try:
        history = response.json()
    except ValueError:
        history = {}
    result: dict[str, Any] = {
        "history_status_code": status_code,
        "history_keys": sorted(str(key) for key in history.keys()) if isinstance(history, dict) else [],
        "selected": None,
        "items": [],
        "error": "",
    }
    if status_code >= 400:
        result["error"] = str(history.get("error") if isinstance(history, dict) else response.text)[:300]
    else:
        searches = history.get("searches") if isinstance(history, dict) else []
        selected = None
        for search in searches or []:
            if not isinstance(search, dict) or str(search.get("status") or "").casefold() != "success":
                continue
            params = search.get("params") if isinstance(search.get("params"), dict) else {}
            engine = str(search.get("engine") or params.get("engine") or "").casefold()
            if "shopping" in engine or params.get("engine") == "google_shopping":
                selected = search
                break
        if selected:
            result["selected"] = {
                "id": selected.get("id"),
                "engine": selected.get("engine"),
                "params": {
                    key: value for key, value in (selected.get("params") or {}).items()
                    if key != "api_key"
                },
                "created_at": selected.get("created_at"),
                "json_url_present": bool(selected.get("json_url")),
            }
            json_url = str(selected.get("json_url") or "")
            if json_url:
                raw_response = requests.get(json_url, timeout=(5, 20))
                result["response_status_code"] = raw_response.status_code
                try:
                    payload = raw_response.json()
                except ValueError:
                    payload = {}
                result["response_keys"] = sorted(str(key) for key in payload.keys()) if isinstance(payload, dict) else []
                result["items"] = [_sanitize_item(item) for item in _shopping_items(payload)[:5]]
        else:
            result["error"] = "no successful google_shopping search found in recent history"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
