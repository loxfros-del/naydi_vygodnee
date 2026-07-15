"""One bounded six-case live smoke for Search Engine V2.

The runner never invokes the legacy benchmark or writes the application DB.
It flushes one short source line and atomically saves JSON after every partial
source snapshot and completed case, so an interrupted environment is readable.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_cache import DEFAULT_CACHE_PATH, sanitize_payload
from app.search_v2.cache import SQLiteSourceCache
from app.search_v2.models import ProductCondition, SearchRequestV2
from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.page_verifier import verify_offer_page
from app.search_v2.query_planner import QueryPlannerV2
from app.search_v2.serialization import to_jsonable
from app.search_v2.service import SearchServiceV2


DEFAULT_OUTPUT = ROOT / "data" / "search_v2_live_smoke.json"


def cases() -> list[tuple[str, SearchRequestV2]]:
    return [
        ("phone", SearchRequestV2(
            original_query="iPhone 16 Pro 256 ГБ новый до 80к в Ярославле",
            category="phone", brand="Apple", canonical_model="iPhone 16",
            model_modifiers=["Pro"], required_specs={"storage_gb": 256},
            budget=80_000, city="Ярославль", condition=ProductCondition.NEW,
            supported_category=True, hard_tokens=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
        )),
        ("laptop", SearchRequestV2(
            original_query="ноутбук Ryzen 5 16/512 до 60к",
            category="laptop", canonical_model="ноутбук Ryzen 5",
            required_specs={"ram_gb": 16, "ssd_gb": 512}, budget=60_000,
            condition=ProductCondition.NEW, supported_category=True,
            hard_tokens=["ноутбук Ryzen 5", "RAM 16 ГБ", "SSD 512 ГБ", "new"],
        )),
        ("tv", SearchRequestV2(
            original_query="телевизор 55 4K 120 Гц для PS5 до 70к",
            category="tv", canonical_model="телевизор",
            required_specs={"diagonal": 55, "refresh_rate": 120, "resolution": "4K"},
            optional_specs={"use_case": "PS5"}, budget=70_000,
            condition=ProductCondition.NEW, supported_category=True,
            hard_tokens=["телевизор", "55 дюймов", "120 Гц", "4K", "new"],
        )),
        ("headphones", SearchRequestV2(
            original_query="TWS-наушники ANC до 12к",
            category="headphones", canonical_model="TWS наушники",
            required_specs={"features": ["ANC"]}, budget=12_000,
            condition=ProductCondition.NEW, supported_category=True,
            hard_tokens=["TWS наушники", "ANC", "new"],
        )),
        ("monitor", SearchRequestV2(
            original_query="монитор 27 QHD 144 Гц до 35к",
            category="monitor", canonical_model="монитор",
            required_specs={"diagonal": 27, "refresh_rate": 144, "resolution": "QHD"},
            budget=35_000, condition=ProductCondition.NEW, supported_category=True,
            hard_tokens=["монитор", "27 дюймов", "144 Гц", "QHD", "new"],
        )),
        ("chair", SearchRequestV2(
            original_query="офисное кресло для 8 часов до 15к",
            category="chair", canonical_model="офисное кресло",
            optional_specs={"use_case": "8 часов"}, budget=15_000,
            condition=ProductCondition.NEW, supported_category=True,
            hard_tokens=["офисное кресло", "new"],
        )),
    ]


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


async def run(args: argparse.Namespace) -> int:
    if not args.confirm_live:
        raise SystemExit("Refusing network smoke without --confirm-live")
    output = Path(args.output).resolve()
    payload: dict[str, Any] = {
        "schema": "search_v2:live_smoke:1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "settings": {
            "case_timeout": args.case_timeout,
            "source_timeout": args.source_timeout,
            "max_source_concurrency": 3,
            "max_queries_per_source": 1,
            "result_limit": args.result_limit,
        },
        "cases": [],
    }
    _write(output, payload)

    orchestrator = SearchSourceOrchestrator(
        cache=SQLiteSourceCache(DEFAULT_CACHE_PATH),
        max_concurrency=3,
        per_source_timeout=args.source_timeout,
        case_timeout=max(1.0, args.case_timeout - 1.0),
        result_limit=args.result_limit,
    )
    service = SearchServiceV2(
        planner=QueryPlannerV2(max_queries_per_source=1),
        orchestrator=orchestrator,
        overall_timeout=args.case_timeout,
        page_verifier=verify_offer_page,
        page_verification_timeout=min(6.0, args.source_timeout),
        page_verification_limit=4,
    )

    for case_id, request in cases():
        case_payload: dict[str, Any] = {
            "case_id": case_id,
            "query": request.original_query,
            "partials": [],
            "result": None,
            "error": "",
        }
        payload["cases"].append(case_payload)
        _write(output, payload)

        async def snapshot(stage, value):
            statuses = {
                source: result.status.value
                for source, result in value.source_results.items()
            }
            partial = {
                "stage": stage,
                "completed_sources": list(value.completed_sources),
                "source_statuses": statuses,
                "raw_offer_count": value.raw_offer_count,
                "attempt_count": value.attempt_count,
                "duration_ms": round(value.duration * 1000, 1),
            }
            case_payload["partials"].append(partial)
            _write(output, payload)
            print(
                f"SOURCE case={case_id} stage={stage} done={','.join(value.completed_sources)} "
                f"raw={value.raw_offer_count} statuses={statuses}",
                flush=True,
            )

        try:
            result = await service.search(request, on_snapshot=snapshot)
            case_payload["result"] = sanitize_payload(to_jsonable(result))
            print(
                f"CASE case={case_id} status={result.status.value} raw={result.raw_offer_count} "
                f"offers={len(result.normalized_offers)} exact={result.metrics.exact_offer_count} "
                f"recommendations={len(result.recommendations)} duration_ms={result.duration:.0f}",
                flush=True,
            )
        except Exception as exc:
            case_payload["error"] = f"{type(exc).__name__}: live case failed"
            print(f"CASE case={case_id} status=ERROR type={type(exc).__name__}", flush=True)
        _write(output, payload)

    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["summary"] = {
        "case_count": len(payload["cases"]),
        "completed": sum(item.get("result") is not None for item in payload["cases"]),
        "errors": sum(bool(item.get("error")) for item in payload["cases"]),
        "output": str(output),
    }
    _write(output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)
    return 0 if payload["summary"]["completed"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--case-timeout", type=float, default=18.0)
    parser.add_argument("--source-timeout", type=float, default=6.0)
    parser.add_argument("--result-limit", type=int, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
