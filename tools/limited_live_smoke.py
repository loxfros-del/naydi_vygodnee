"""One-case live smoke without SQLite/cache writes.

The command is deliberately opt-in and capped: one acceptance case, at most
six saved candidates, source concurrency from the production strategy (3) and
fast verification concurrency (2).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.acceptance_metrics import evaluate_acceptance_results  # noqa: E402
from app.db import Request  # noqa: E402
from app.market_analysis import plan_recommendation_roles  # noqa: E402
from app.product_search import collect_product_candidates  # noqa: E402
from app.request_parser import full_parse  # noqa: E402


CASE_FILE = ROOT / "tools" / "live_acceptance_cases.json"
INTERNAL_ATTEMPTS = {"candidate_verifier", "quality_filter", "manual_fallback"}


def _load_case(case_id: str) -> dict[str, Any]:
    with CASE_FILE.open("r", encoding="utf-8") as handle:
        rows = json.load(handle)
    for row in rows:
        if row.get("id") == case_id:
            return dict(row)
    raise ValueError(f"unknown case id: {case_id}")


def _request(case: dict[str, Any]) -> Request:
    query = str(case["query"])
    parsed = full_parse(query)
    parsed["required_criteria"] = dict(case.get("required_specs") or {})
    parsed["budget"] = str(case.get("budget") or parsed.get("budget") or "")
    parsed["city"] = str(case.get("city") or parsed.get("city") or "")
    parsed["is_used_allowed"] = bool(case.get("used_allowed"))
    request = Request(
        id=0,
        user_id=0,
        username="limited_live_smoke",
        product=parsed.get("product_name", ""),
        original_query=query,
        product_name=parsed.get("product_name", ""),
        use_case=parsed.get("use_case", ""),
        budget=parsed["budget"],
        city=parsed["city"],
        important_criteria=parsed.get("important_criteria", ""),
        clean_search_query=parsed.get("clean_search_query", ""),
        is_used_allowed=parsed["is_used_allowed"],
    )
    request.parsed_details = parsed
    return request


def _candidate_row(candidate: Any) -> dict[str, Any]:
    facts = dict(getattr(candidate, "product_facts", {}) or {})
    verify = str(getattr(candidate, "verify_status", "") or facts.get("verify_status") or "")
    page_prediction = bool(
        facts.get("product_page_verified")
        or str(getattr(candidate, "product_card_confidence", "") or "").lower() in {"high", "medium"}
    )
    if verify in {"VERIFIED_GOOD", "VERIFIED_OK", "OVER_BUDGET_SOFT"}:
        page_truth: bool | None = True
    elif verify in {"NOT_PRODUCT_PAGE", "WRONG_PRODUCT", "REMOVED_LISTING"}:
        page_truth = False
    else:
        page_truth = None
    price_source = str(getattr(candidate, "price_source", "") or "")
    structured = price_source.lower() in {"structured", "json_ld", "direct", "direct_store", "api", "offer_dto"}
    return {
        "title": getattr(candidate, "title", ""),
        "url": getattr(candidate, "url", ""),
        "source": getattr(candidate, "source", ""),
        "seller": getattr(candidate, "seller", ""),
        "price": getattr(candidate, "price", None),
        "persisted_price": getattr(candidate, "price", None),
        "price_source": price_source,
        "structured_price_found": structured,
        "structured_price_persisted": structured and getattr(candidate, "price", None) is not None,
        "exact_match": getattr(candidate, "exact_match_status", "") or facts.get("exact_match"),
        "product_page_predicted": page_prediction,
        "actual_product_page": page_truth,
        "status": getattr(candidate, "status", ""),
        "verify_status": verify,
        "quality": getattr(candidate, "quality", ""),
        "risk_flags": list(getattr(candidate, "risk_flags", []) or []),
        "product_facts": facts,
    }


def run(case_id: str, *, source_timeout: float, verification: str) -> dict[str, Any]:
    case = _load_case(case_id)
    request = _request(case)
    started = time.monotonic()
    collection = collect_product_candidates(
        request,
        max_results=6,
        verification_mode=verification,
        source_timeout_seconds=source_timeout,
    )
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    candidates = [_candidate_row(item) for item in collection.candidates]
    assignments = plan_recommendation_roles(collection.candidates)
    market_median = next((
        ((item.get("product_facts") or {}).get("market_price") or {}).get("median")
        for item in candidates
        if ((item.get("product_facts") or {}).get("market_price") or {}).get("median")
    ), None)
    attempts = [
        {
            "source": attempt.source,
            "status": attempt.status,
            "latency_ms": int(getattr(attempt, "duration_ms", 0) or 0),
            "error": str(getattr(attempt, "error_text", "") or "")[:200],
        }
        for attempt in collection.attempts
        if attempt.source not in INTERNAL_ATTEMPTS
    ]
    acceptance_row = {
        "case_id": case_id,
        "expects_exact": True,
        "search_time_ms": duration_ms,
        "candidates": candidates,
        "source_attempts": attempts,
        "market_median": market_median,
        "roles": [item.role.value for item in assignments],
        "expected_roles": ["BEST_OVERALL"] if candidates else [],
    }
    metrics = evaluate_acceptance_results([acceptance_row]).as_dict()
    return {
        "mode": "limited_live_smoke",
        "case": case,
        "limits": {
            "cases": 1,
            "max_results": 6,
            "source_concurrency": 3,
            "verification_concurrency": 2,
            "source_timeout_seconds": source_timeout,
            "verification": verification,
        },
        "candidate_count": len(candidates),
        "metrics": metrics,
        "roles": acceptance_row["roles"],
        "top": candidates[:3],
        "source_attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One limited live acceptance smoke")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--case", default="phone_02")
    parser.add_argument("--source-timeout", type=float, default=25.0)
    parser.add_argument("--verification", choices=("fast", "none"), default="fast")
    args = parser.parse_args()
    if not args.confirm_live:
        print("ERROR: live network disabled; pass --confirm-live", file=sys.stderr)
        return 2
    try:
        result = run(
            args.case,
            source_timeout=max(5.0, min(args.source_timeout, 30.0)),
            verification=args.verification,
        )
    except Exception as exc:
        print(json.dumps({"mode": "limited_live_smoke", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
