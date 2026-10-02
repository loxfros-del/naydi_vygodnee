"""Offline decision-chain replay for saved Avito market snapshots."""
from __future__ import annotations

import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from avito_service.matching import diagnose_request_mismatch
from avito_service.models import AIReview, AnalyzedListing, NormalizedListing, ReviewVerdict, SearchRequest, SellerSummary
from avito_service.rejection import route_text_ai
from avito_service.risk_rules import evaluate_rules
from avito_service.verification import VerificationState, evaluate_verification


DEFAULT_SNAPSHOT = PROJECT_ROOT / "runtime/avito_market_cache/334d00182be4e02498ed6f9f1cc203c9dbc9cddd823e9ca499fe1b72d0aa42eb.json"


def _known(data: dict[str, Any], cls) -> dict[str, Any]:
    allowed = {item.name for item in fields(cls)}
    return {key: value for key, value in data.items() if key in allowed}


def load_snapshot(path: Path) -> tuple[AnalyzedListing, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for record in raw.get("items", ()):
        listing_data = dict(record["listing"])
        listing_data["seller"] = SellerSummary(**_known(dict(listing_data["seller"]), SellerSummary))
        for name in ("images", "badges"):
            listing_data[name] = tuple(listing_data.get(name, ()))
        listing = NormalizedListing(**_known(listing_data, NormalizedListing))
        review_data = dict(record.get("review") or {})
        review_data["verdict"] = ReviewVerdict(review_data.get("verdict") or "caution")
        for name in ("photo_coverage", "description_findings", "photo_findings", "defects",
                     "price_conditions", "conflicts", "manual_checks"):
            review_data[name] = tuple(review_data.get(name, ()))
        review = AIReview(**_known(review_data, AIReview))
        result.append(AnalyzedListing(listing, evaluate_rules(listing), review))
    return tuple(result)


def replay(snapshot: tuple[AnalyzedListing, ...], request: SearchRequest) -> dict[str, Any]:
    chains = []
    survivors = []
    for item in snapshot:
        listing, review = item.listing, item.ai_review
        route = route_text_ai(listing, request, item.deterministic_risks)
        diagnostic = diagnose_request_mismatch(
            listing, request, identified_model=review.identified_model,
            storage=review.storage, sim_variant=review.sim_variant,
            condition=review.condition, final=review.text_analyzed,
        )
        decision = evaluate_verification(
            listing, item.deterministic_risks, review, request, stage="text",
        ) if route.needs_ai else None
        chain = {
            "listing_id": listing.listing_id,
            "request_match": {
                "matches": diagnostic.matches,
                "field": diagnostic.field or None,
                "reason": diagnostic.reason or None,
            },
            "text_state": (
                decision.state.value if decision is not None else
                ("NEEDS_EVIDENCE" if route.needs_ai else "FAIL")
            ),
            "price_conditions": len(review.price_conditions),
            "defects": len(review.defects),
            "condition_evidence": item.condition_evidence_complete(),
            "photo_eligible": bool(
                decision is not None
                and decision.state is VerificationState.NEEDS_EVIDENCE
                and decision.next_stage == "photo_ai"
            ),
            "first_fatal_rejection": (
                decision.primary_reason if decision is not None and decision.state is VerificationState.FAIL
                else (route.reason_codes[0] if not route.needs_ai else None)
            ),
            "secondary_reasons": list(
                decision.secondary_reasons if decision is not None else route.reason_codes[1:]
            ),
            "next_required_stage": decision.next_stage if decision is not None else (
                "text_ai" if route.needs_ai else None
            ),
        }
        chains.append(chain)
        if route.needs_ai:
            survivors.append(chain)

    valid_text = sum(
        item["text_state"] in {"PASS", "NEEDS_EVIDENCE"}
        and item["next_required_stage"] != "text_ai"
        for item in survivors
    )
    return {
        "request": request.query,
        "snapshot_listings": len(snapshot),
        "hard_filter_survivors": len(survivors),
        "valid_text": valid_text,
        "photo_eligible": sum(item["photo_eligible"] for item in survivors),
        "definitely_rejected": sum(item["text_state"] == "FAIL" for item in survivors),
        "awaiting_text_ai": sum(item["next_required_stage"] == "text_ai" for item in survivors),
        "pre_text_rejected": len(chains) - len(survivors),
        "survivors": survivors,
        "all_decisions": chains,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", nargs="?", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    request = SearchRequest(
        "ps5", location="Ярославль", category="gaming", mode="find",
        max_results=100, desired_results=3, pickup_only=True,
    )
    result = replay(load_snapshot(args.snapshot), request)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for key in ("snapshot_listings", "hard_filter_survivors", "valid_text", "photo_eligible",
                "definitely_rejected", "awaiting_text_ai", "pre_text_rejected"):
        print(f"{key}: {result[key]}")


if __name__ == "__main__":
    main()
