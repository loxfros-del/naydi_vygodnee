"""Fail-fast quota preflight for provider-backed Search V2 acceptance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:preflight_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.sources.searchapi_account import get_searchapi_usage  # noqa: E402


DEFAULT_CASES = ROOT / "tools" / "live_acceptance_cases.json"
DEFAULT_OUTPUT = ROOT / "data" / "searchapi_preflight.json"


def requested_cases(path: Path, limit: int) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("acceptance dataset must be a JSON list")
    return min(len(payload), limit) if limit > 0 else len(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--buffer", type=int, default=2)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    provider = os.getenv("SEARCH_V2_SHOPPING_PROVIDER", "auto").strip().casefold() or "auto"
    searchapi_key = bool(os.getenv("SEARCHAPI_API_KEY", ""))
    serpapi_key = bool(os.getenv("SERPAPI_API_KEY", ""))
    case_count = requested_cases(Path(args.cases), args.limit)
    required = case_count + max(0, args.buffer)

    payload = {
        "provider": provider,
        "case_count": case_count,
        "buffer": max(0, args.buffer),
        "required_searches": required,
        "searchapi_key_present": searchapi_key,
        "serpapi_key_present": serpapi_key,
        "searchapi_usage": None,
        "can_run": True,
        "reason": "",
    }

    needs_searchapi = provider in {"auto", "searchapi"} and searchapi_key
    if needs_searchapi:
        usage = get_searchapi_usage()
        payload["searchapi_usage"] = usage.to_dict()
        remaining = usage.remaining_credits
        if usage.status != "success":
            if provider == "searchapi" or not serpapi_key:
                payload["can_run"] = False
                payload["reason"] = f"SearchApi quota preflight failed: {usage.status}: {usage.error}"
        elif remaining is not None and remaining < required:
            if provider == "searchapi" or not serpapi_key:
                payload["can_run"] = False
                payload["reason"] = (
                    f"SearchApi has {remaining} remaining searches, but this run needs at least {required}. "
                    "Add SerpApi fallback, reduce --limit, wait for quota reset, or upgrade the plan."
                )
    elif provider == "searchapi" and not searchapi_key:
        payload["can_run"] = False
        payload["reason"] = "SEARCHAPI_API_KEY is missing"
    elif provider == "serpapi" and not serpapi_key:
        payload["can_run"] = False
        payload["reason"] = "SERPAPI_API_KEY is missing"
    elif provider == "auto" and not (searchapi_key or serpapi_key):
        payload["can_run"] = False
        payload["reason"] = "No structured shopping provider key is configured"

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["can_run"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
