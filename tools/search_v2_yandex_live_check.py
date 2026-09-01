"""One bounded Search V2 + Yandex live check without database writes.

The output contains aggregate quality facts only: no credential, query text,
title, URL, seller, city, request ID or raw provider error.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.adapters.yandex_web import YandexWebDiscoveryAdapter
from app.search_v2.cache import MemorySourceCache
from app.search_v2.external_page_verifier import (
    ExternalProductPageVerifier,
    verify_external_offer_if_needed,
)
from app.search_v2.models import ProductCondition, SearchRequestV2
from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.query_planner import QueryPlannerV2
from app.search_v2.service import SearchServiceV2
from app.search_v2.source_registry import SourceRegistry


LIVE_CASES = {
    "phone": SearchRequestV2(
        original_query="Samsung Galaxy A55 256 ГБ новый до 35000",
        category="phone", brand="Samsung", canonical_model="Galaxy A55",
        required_specs={"storage_gb": 256}, budget=35_000,
        hard_tokens=["Samsung", "Galaxy A55", "256 ГБ", "new"],
    ),
    "laptop": SearchRequestV2(
        original_query="Apple MacBook Air M4 16 512 новый до 170000",
        category="laptop", brand="Apple", canonical_model="MacBook Air M4",
        required_specs={"ram_gb": 16, "ssd_gb": 512}, budget=170_000,
        hard_tokens=["Apple", "MacBook Air M4", "16 ГБ", "512 ГБ", "new"],
    ),
    "tv": SearchRequestV2(
        original_query="Samsung QE55QN90D 55 4K 120 Гц новый до 180000",
        category="tv", brand="Samsung", canonical_model="QE55QN90D",
        required_specs={"diagonal": 55, "resolution": "4K", "refresh_rate": 120}, budget=180_000,
        hard_tokens=["Samsung", "QE55QN90D", "55", "4K", "120 Гц", "new"],
    ),
    "headphones": SearchRequestV2(
        original_query="Sony WH-1000XM5 беспроводные ANC новые до 45000",
        category="headphones", brand="Sony", canonical_model="WH-1000XM5",
        required_specs={"anc": True, "connection": "wireless"}, budget=45_000,
        hard_tokens=["Sony", "WH-1000XM5", "беспроводные", "ANC", "new"],
    ),
    "hyperx": SearchRequestV2(
        original_query="HyperX игровые беспроводные наушники новые до 15000",
        category="headphones", brand="HyperX", canonical_model="",
        required_specs={"connection": "wireless"}, budget=15_000,
        hard_tokens=["HyperX", "беспроводные", "new"],
    ),
    "monitor": SearchRequestV2(
        original_query="LG 27GP850-B 27 QHD 165 Гц новый до 60000",
        category="monitor", brand="LG", canonical_model="27GP850-B",
        required_specs={"diagonal": 27, "resolution": "QHD", "refresh_rate": 165}, budget=60_000,
        hard_tokens=["LG", "27GP850-B", "27", "QHD", "165 Гц", "new"],
    ),
    "chair": SearchRequestV2(
        original_query="ThunderX3 TC5 кресло с подголовником и поясничной поддержкой новое до 45000",
        category="chair", brand="ThunderX3", canonical_model="TC5",
        required_specs={"headrest": True, "lumbar_support": True}, budget=45_000,
        hard_tokens=["ThunderX3", "TC5", "headrest", "lumbar support", "new"],
    ),
}

for _case in LIVE_CASES.values():
    _case.city = "Москва"
    _case.condition = ProductCondition.NEW
    _case.supported_category = True


def _verification_ready(offer) -> bool:
    state = offer.final_verification
    return all(
        getattr(state, f"{field}_verified", None) is True
        for field in ("model", "link", "price", "availability", "seller")
    )


async def run(*, case: str = "phone", source_timeout: float, page_timeout: float) -> dict:
    adapter = YandexWebDiscoveryAdapter()
    orchestrator = SearchSourceOrchestrator(
        SourceRegistry((adapter,)),
        cache=MemorySourceCache(max_entries=8),
        max_concurrency=1,
        per_source_timeout=source_timeout,
        case_timeout=source_timeout + 1,
        result_limit=10,
    )
    verifier = ExternalProductPageVerifier(timeout=page_timeout)
    service = SearchServiceV2(
        planner=QueryPlannerV2(max_queries_per_source=1),
        orchestrator=orchestrator,
        page_verifier=lambda offer: verify_external_offer_if_needed(offer, verifier),
        page_verification_limit=6,
        page_verification_timeout=page_timeout,
        discovery_sources=("yandex_web",),
        web_discovery_sources=(),
        anchor_sources=(),
        generic_sources=(),
        overall_timeout=source_timeout + 2,
    )
    result = await service.search(LIVE_CASES[case])
    all_offers = [*result.normalized_offers, *result.rejected_offers]
    attempts = [
        {
            "source": item.source,
            "status": item.status.value,
            "duration_ms": round(item.duration_ms, 1),
            "raw_offer_count": item.raw_offer_count,
        }
        for item in result.source_attempts
    ]
    rejection_reasons = Counter(item.exact_match.value for item in result.rejected_offers)
    page_failure_reasons = Counter(
        str((item.raw_metadata.get("external_page_verification") or {}).get("reason") or "not_attempted")
        for item in result.rejected_offers
    )
    return {
        "mode": "search_v2_yandex_live_check",
        "category": case,
        "status": result.status.value,
        "raw_offer_count": result.raw_offer_count,
        "retained_offer_count": len(result.normalized_offers),
        "exact_offer_count": result.metrics.exact_offer_count,
        "recommendation_count": len(result.recommendations),
        "manual_candidate_count": len(result.manual_candidates),
        "rejected_offer_count": len(result.rejected_offers),
        "rejected_by_match": dict(sorted(rejection_reasons.items())),
        "page_failure_reasons": dict(sorted(page_failure_reasons.items())),
        "region_confirmed_count": sum(
            bool(item.raw_metadata.get("region_scope_confirmed")) for item in all_offers
        ),
        "page_verified_count": sum(
            bool(item.raw_metadata.get("external_page_verified")) for item in all_offers
        ),
        "price_verified_count": sum(
            item.final_verification.price_verified is True for item in result.normalized_offers
        ),
        "fully_verified_count": sum(_verification_ready(item) for item in result.normalized_offers),
        "duration_ms": round(result.duration, 1),
        "source_attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--source-timeout", type=float, default=8.0)
    parser.add_argument("--page-timeout", type=float, default=6.0)
    parser.add_argument("--case", choices=(*LIVE_CASES, "all"), default="phone")
    args = parser.parse_args()
    if not args.confirm_live:
        print("ERROR: pass --confirm-live", file=sys.stderr)
        return 2
    source_timeout = max(3.0, min(args.source_timeout, 15.0))
    page_timeout = max(2.0, min(args.page_timeout, 10.0))
    if args.case == "all":
        async def run_all() -> dict:
            results = []
            for case in LIVE_CASES:
                try:
                    results.append(await run(case=case, source_timeout=source_timeout, page_timeout=page_timeout))
                except Exception:
                    results.append({"category": case, "status": "ERROR"})
            return {
                "mode": "search_v2_yandex_live_matrix",
                "cases": results,
                "successful_source_cases": sum(
                    bool(item.get("source_attempts"))
                    and item["source_attempts"][0].get("status") == "SUCCESS"
                    for item in results
                ),
                "recommended_cases": sum(bool(item.get("recommendation_count")) for item in results),
                "fully_verified_cases": sum(bool(item.get("fully_verified_count")) for item in results),
            }
        result = asyncio.run(run_all())
    else:
        result = asyncio.run(run(
            case=args.case,
            source_timeout=source_timeout,
            page_timeout=page_timeout,
        ))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
