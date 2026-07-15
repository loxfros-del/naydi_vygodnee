"""Bounded 30-case Search Engine V2 acceptance runner.

This command is intentionally explicit about live network use. It writes a
compact checkpoint after every case, so a timeout or interrupted terminal does
not lose completed evidence. No token, API key, response body or raw HTML is
serialized.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:acceptance_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.request_parser import split_model_modifiers  # noqa: E402
from app.search_v2.cache import MemorySourceCache  # noqa: E402
from app.search_v2.models import (  # noqa: E402
    ProductCondition,
    SearchRequestV2,
)
from app.search_v2.orchestrator import SearchSourceOrchestrator  # noqa: E402
from app.search_v2.page_verifier import verify_offer_page  # noqa: E402
from app.search_v2.query_planner import QueryPlannerV2  # noqa: E402
from app.search_v2.request_normalizer import spec_token  # noqa: E402
from app.search_v2.service import SearchServiceV2  # noqa: E402
from app.search_v2.source_registry import build_default_registry  # noqa: E402


DEFAULT_CASES = ROOT / "tools" / "live_acceptance_cases.json"
DEFAULT_OUTPUT = ROOT / "data" / "search_v2_acceptance.json"
SUPPORTED_CATEGORIES = {"phone", "laptop", "tv", "headphones", "monitor", "chair"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def _condition(case: dict[str, Any], specs: dict[str, Any]) -> ProductCondition:
    raw = str(specs.pop("condition", "") or "").strip().casefold()
    if case.get("used_allowed"):
        return ProductCondition.ANY
    if raw in {"used", "б/у", "бу"}:
        return ProductCondition.USED
    if raw in {"refurbished", "восстановленный"}:
        return ProductCondition.REFURBISHED
    return ProductCondition.NEW


def _features(specs: dict[str, Any]) -> list[str]:
    features: list[str] = []
    if specs.pop("anc", False):
        features.append("ANC")
    form_factor = str(specs.pop("form_factor", "") or "").strip()
    if form_factor:
        features.append(form_factor)
    if specs.pop("wireless", False):
        features.append("wireless")
    return list(dict.fromkeys(features))


def request_from_case(case: dict[str, Any]) -> SearchRequestV2:
    specs = dict(case.get("required_specs") or {})
    brand = str(specs.pop("brand", "") or "").strip()
    model = str(specs.pop("model", "") or "").strip()
    condition = _condition(case, specs)
    features = _features(specs)
    if features:
        specs["features"] = features

    category = str(case.get("category") or "unknown").strip().casefold()
    if not model:
        model = {
            "phone": "смартфон",
            "laptop": "ноутбук",
            "tv": "телевизор",
            "headphones": "наушники",
            "monitor": "монитор",
            "chair": "кресло",
        }.get(category, category)
    base_model, modifiers = split_model_modifiers(model, ())

    hard_tokens: list[str] = []
    for item in (brand, base_model, *modifiers):
        if item and item not in hard_tokens:
            hard_tokens.append(item)
    for key, value in specs.items():
        token = spec_token(str(key), value)
        if token and token not in hard_tokens:
            hard_tokens.append(token)
    if condition not in {ProductCondition.ANY, ProductCondition.UNKNOWN}:
        hard_tokens.append(condition.value)

    return SearchRequestV2(
        original_query=str(case.get("query") or "").strip(),
        category=category,
        brand=brand,
        canonical_model=base_model,
        model_modifiers=list(modifiers),
        required_specs=specs,
        optional_specs={},
        budget=int(case.get("budget") or 0) or None,
        city=str(case.get("city") or "").strip(),
        condition=condition,
        priority="balance",
        supported_category=category in SUPPORTED_CATEGORIES,
        hard_tokens=hard_tokens,
        soft_tokens=[],
    )


def _attempt_summary(attempt: Any) -> dict[str, Any]:
    return {
        "source": str(getattr(attempt, "source", "")),
        "status": _json_value(getattr(attempt, "status", "")),
        "tier": _json_value(getattr(attempt, "tier", "")),
        "query": str(getattr(attempt, "query", ""))[:300],
        "raw_offer_count": int(getattr(attempt, "raw_offer_count", getattr(attempt, "raw_count", 0)) or 0),
        "duration_ms": round(float(getattr(attempt, "duration_ms", 0.0) or 0.0), 3),
        "cache_hit": bool(getattr(attempt, "cache_hit", False)),
        "error": str(getattr(attempt, "error", "") or "")[:240],
    }


def _recommendation_summary(item: Any) -> dict[str, Any]:
    offer = getattr(item, "offer", None)
    if offer is None:
        return {"role": _json_value(getattr(item, "role", "")), "offer_id": str(getattr(item, "offer_id", ""))}
    return {
        "role": _json_value(getattr(item, "role", "")),
        "offer_id": str(getattr(offer, "offer_id", "")),
        "title": str(getattr(offer, "title", ""))[:240],
        "price": getattr(offer, "price", None),
        "platform": str(getattr(offer, "platform", "")),
        "seller": str(getattr(getattr(offer, "seller", None), "name", "")),
        "url": str(getattr(offer, "url", ""))[:500],
        "exact_match": _json_value(getattr(offer, "exact_match", "")),
        "availability": _json_value(getattr(getattr(offer, "availability", None), "status", "")),
        "platform_trust": _json_value(getattr(offer, "platform_trust", "")),
        "seller_trust": _json_value(getattr(getattr(offer, "seller", None), "trust", "")),
    }


def _case_result(case: dict[str, Any], result: Any) -> dict[str, Any]:
    metrics = getattr(result, "metrics", None)
    return {
        "id": case["id"],
        "query": case["query"],
        "category": case["category"],
        "status": _json_value(getattr(result, "status", "")),
        "duration_ms": round(float(getattr(result, "duration", 0.0) or 0.0), 3),
        "raw_offer_count": int(getattr(result, "raw_offer_count", 0) or 0),
        "normalized_offer_count": len(getattr(result, "normalized_offers", ()) or ()),
        "rejected_offer_count": len(getattr(result, "rejected_offers", ()) or ()),
        "group_count": len(getattr(result, "product_groups", ()) or ()),
        "manual_candidate_count": len(getattr(result, "manual_candidates", ()) or ()),
        "recommendations": [_recommendation_summary(item) for item in (getattr(result, "recommendations", ()) or ())[:3]],
        "metrics": _json_value(metrics) if metrics is not None else {},
        "source_attempts": [_attempt_summary(item) for item in (getattr(result, "source_attempts", ()) or ())],
        "errors": [str(item)[:240] for item in (getattr(result, "errors", ()) or ())],
    }


def _aggregate(cases: Iterable[dict[str, Any]], *, expected: int) -> dict[str, Any]:
    rows = list(cases)
    top1_exact = sum(bool((row.get("metrics") or {}).get("top1_exact")) for row in rows)
    useful = sum(int((row.get("metrics") or {}).get("top3_useful") or 0) > 0 for row in rows)
    recommendations = sum(bool(row.get("recommendations")) for row in rows)
    completed = len(rows)
    wrong = sum(int((row.get("metrics") or {}).get("wrong_model_rejection_count") or 0) for row in rows)
    valid_price_cases = sum(float((row.get("metrics") or {}).get("valid_price_rate") or 0) > 0 for row in rows)
    errors = sum(bool(row.get("errors")) for row in rows)
    return {
        "expected_cases": expected,
        "completed_cases": completed,
        "error_cases": errors,
        "cases_with_valid_prices": valid_price_cases,
        "cases_with_recommendations": recommendations,
        "top1_exact_cases": top1_exact,
        "top3_useful_cases": useful,
        "wrong_model_rejections": wrong,
        "acceptance_targets": {
            "top1_exact_min": 24,
            "top3_useful_min": 28,
            "completed_required": expected,
        },
        "acceptance_pass": bool(
            completed == expected
            and errors == 0
            and top1_exact >= 24
            and useful >= 28
        ),
    }


def _write_checkpoint(path: Path, *, started_at: str, rows: list[dict[str, Any]], expected: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "started_at": started_at,
        "updated_at": _now(),
        "environment": {
            "shopping_provider": os.getenv("SEARCH_V2_SHOPPING_PROVIDER", "auto"),
            "searchapi_enabled": os.getenv("SEARCHAPI_ENABLED", "").casefold() in {"1", "true", "yes", "on"},
            "searchapi_key_present": bool(os.getenv("SEARCHAPI_API_KEY", "")),
            "serpapi_enabled": os.getenv("SERPAPI_ENABLED", "").casefold() in {"1", "true", "yes", "on"},
            "serpapi_key_present": bool(os.getenv("SERPAPI_API_KEY", "")),
        },
        "summary": _aggregate(rows, expected=expected),
        "cases": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    if not args.confirm_live:
        raise SystemExit("Refusing live network search without --confirm-live")
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise SystemExit("acceptance dataset must be a JSON list")
    if args.limit:
        cases = cases[: max(1, int(args.limit))]

    orchestrator = SearchSourceOrchestrator(
        registry=build_default_registry(),
        cache=MemorySourceCache(),
        max_concurrency=3,
        per_source_timeout=float(args.source_timeout),
        case_timeout=float(args.case_timeout),
        result_limit=int(args.result_limit),
    )
    service = SearchServiceV2(
        planner=QueryPlannerV2(max_queries_per_source=1),
        orchestrator=orchestrator,
        discovery_sources=("shopping_search", "ozon", "avito"),
        anchor_sources=("dns",),
        generic_sources=("generic_exact",),
        page_verifier=verify_offer_page,
        page_verification_timeout=min(6.0, float(args.source_timeout)),
        page_verification_limit=4,
        overall_timeout=float(args.case_timeout),
    )

    output = Path(args.output)
    started_at = _now()
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        case_id = str(case.get("id") or f"case_{index}")
        print(f"CASE {index}/{len(cases)} {case_id}", flush=True)
        try:
            result = await service.search(request_from_case(case))
            row = _case_result(case, result)
        except Exception as exc:  # checkpoint the failure without losing prior cases
            row = {
                "id": case_id,
                "query": str(case.get("query") or ""),
                "category": str(case.get("category") or ""),
                "status": "ERROR",
                "errors": [f"{type(exc).__name__}: {str(exc)[:220]}"],
                "recommendations": [],
                "metrics": {},
                "source_attempts": [],
            }
        rows.append(row)
        _write_checkpoint(output, started_at=started_at, rows=rows, expected=len(cases))
        print(
            f"DONE {case_id} status={row.get('status')} "
            f"offers={row.get('normalized_offer_count', 0)} "
            f"recs={len(row.get('recommendations') or [])}",
            flush=True,
        )

    summary = _aggregate(rows, expected=len(cases))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if (summary["acceptance_pass"] or not args.require_pass) else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--confirm-live", action="store_true")
    result.add_argument("--require-pass", action="store_true")
    result.add_argument("--cases", default=str(DEFAULT_CASES))
    result.add_argument("--output", default=str(DEFAULT_OUTPUT))
    result.add_argument("--limit", type=int, default=0)
    result.add_argument("--case-timeout", type=float, default=30.0)
    result.add_argument("--source-timeout", type=float, default=8.0)
    result.add_argument("--result-limit", type=int, default=10)
    return result


def main() -> int:
    return asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
