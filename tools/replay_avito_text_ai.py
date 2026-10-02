"""Replay saved Avito listings through text AI only; this module cannot call Apify."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.ai import OpenAICompatibleReviewer, incomplete_review
from avito_service.config import expanded_review_config, load_config
from avito_service.errors import AvitoServiceError
from avito_service.models import AIReview, AnalyzedListing, NormalizedListing, ReviewVerdict, SearchRequest, SellerSummary
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from avito_service.verification import evaluate_verification


def _saved_items(path: Path, *, replayed_at: str) -> tuple[AnalyzedListing, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError("Нужен сохранённый market-cache JSON с массивом items")
    result = []
    for record in raw["items"]:
        listing_data = dict(record["listing"])
        listing_data["seller"] = SellerSummary(**listing_data["seller"])
        for field in ("images", "badges"):
            listing_data[field] = tuple(listing_data.get(field, ()))
        listing_data["verified_at"] = ""
        listing_data["verification_status"] = "unverified"
        # This tool diagnoses text decisions against saved evidence. Rebase only
        # the in-memory observation time so the normal 15-minute live freshness
        # gate does not turn an offline replay into a false photo-ineligible result.
        # The report labels this explicitly and no refreshed/public result is made.
        listing_data["collected_at"] = replayed_at
        listing = NormalizedListing(**listing_data)
        review_data = dict(record.get("review") or {})
        review_data["verdict"] = ReviewVerdict(review_data.get("verdict") or "caution")
        for field in (
            "photo_coverage", "description_findings", "photo_findings", "defects",
            "price_conditions", "conflicts", "manual_checks",
        ):
            review_data[field] = tuple(review_data.get(field, ()))
        review = AIReview(**review_data)
        result.append(AnalyzedListing(listing, evaluate_rules(listing), review))
    return tuple(result)


def _request(args: argparse.Namespace) -> SearchRequest:
    return SearchRequest(
        args.query,
        location=args.location,
        category=args.category,
        max_results=args.max_results,
        desired_results=args.desired_results,
        price_max=args.price_max,
        pickup_only=args.pickup_only,
    )


def _safe_report(
    *, request: SearchRequest, candidates: tuple[NormalizedListing, ...],
    reviews: dict[str, AIReview], packets: list[dict[str, Any]], photo_ids: tuple[str, ...],
    budget: dict[str, float | int | None], dry_run: bool,
    top_level_errors: list[dict[str, Any]],
    text_decisions: list[dict[str, Any]], photo_ids_before_bargain: tuple[str, ...],
    bargain_diagnostics: list[dict[str, Any]], snapshot_name: str,
) -> dict[str, Any]:
    retried_ids = sorted({
        listing_id
        for packet in packets if int(packet.get("attempt") or 0) > 1
        for listing_id in packet.get("listing_ids", ())
    })
    split_ids = sorted({
        listing_id
        for packet in packets if int(packet.get("split_depth") or 0) > 0
        for listing_id in packet.get("listing_ids", ())
    })
    complete_ids = [
        listing.listing_id for listing in candidates
        if reviews[listing.listing_id].text_analyzed and not reviews[listing.listing_id].error
    ]
    failed_ids = [listing.listing_id for listing in candidates if listing.listing_id not in complete_ids]
    for decision in bargain_diagnostics:
        decision["photo_ai_attempted"] = False
        decision["photo_ai_completed"] = False
        decision["final_revalidated"] = False
        decision["final_status"] = (
            "rejected" if decision.get("photo_admission_status") == "fail" else "pending"
        )
        decision["final_reason_code"] = (
            decision.get("photo_admission_reason_code") or "PHOTO_STAGE_NOT_RUN_IN_TEXT_REPLAY"
        )
    bargain_funnel = {
        "text_safe": len(bargain_diagnostics),
        "bargain_evaluated": sum(
            item["status"] in {"pass", "fail"} for item in bargain_diagnostics
        ),
        "bargain_pass": sum(item["status"] == "pass" for item in bargain_diagnostics),
        "bargain_fail": sum(item["status"] == "fail" for item in bargain_diagnostics),
        "bargain_fail_reasons": dict(Counter(
            item["reason_code"] for item in bargain_diagnostics if item["status"] == "fail"
        )),
        "photo_ai_eligible": sum(bool(item["photo_ai_eligible"]) for item in bargain_diagnostics),
        "photo_ai_attempted": 0,
        "photo_ai_completed": 0,
        "final_revalidated": 0,
        "final": 0,
        "bargain": 0,
        "exact_match": 0,
        "rejected": sum(item["final_status"] == "rejected" for item in bargain_diagnostics),
        "pending": sum(item["final_status"] == "pending" for item in bargain_diagnostics),
    }
    return {
        "mode": "dry-run" if dry_run else "text-ai-only",
        "query": request.query,
        "expected": len(candidates),
        "complete": len(complete_ids),
        "retried": len(retried_ids),
        "split": len(split_ids),
        "failed": len(failed_ids),
        "photo_eligible": len(photo_ids),
        "photo_eligible_before_bargain": len(photo_ids_before_bargain),
        "complete_ids": complete_ids,
        "retried_ids": retried_ids,
        "split_ids": split_ids,
        "failed_ids": failed_ids,
        "photo_eligible_ids": list(photo_ids),
        "photo_eligible_before_bargain_ids": list(photo_ids_before_bargain),
        "text_decision_counts": dict(Counter(item["state"] for item in text_decisions)),
        "text_decisions": text_decisions,
        "bargain_funnel": bargain_funnel,
        "bargain_decisions": bargain_diagnostics,
        "saved_snapshot": snapshot_name,
        "packets": packets,
        "top_level_errors": top_level_errors,
        "budget": budget,
        "apify_runs": 0,
        "saved_evidence_rebased_for_offline_freshness": True,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--query", default="ps5")
    parser.add_argument("--location", default="Ярославль")
    parser.add_argument("--category", default="gaming")
    parser.add_argument("--max-results", type=int, default=200)
    parser.add_argument("--desired-results", type=int, default=3)
    parser.add_argument("--price-max", type=int)
    parser.add_argument("--pickup-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    replayed_at = datetime.now(timezone.utc).isoformat()
    saved = _saved_items(args.input, replayed_at=replayed_at)
    request = _request(args)
    config = expanded_review_config(load_config())
    reviewer = OpenAICompatibleReviewer(config)
    service = AvitoAnalysisService(
        None,
        reviewer,
        ai_text_batch_size=config.ai_text_batch_size,
        ai_text_max_listings=config.ai_text_max_listings,
        ai_max_listings=config.ai_max_listings,
        ai_max_cost_rub=config.effective_ai_budget_rub,
        report_max_cost_rub=config.report_max_cost_rub,
        usd_rub_rate=config.usd_rub_rate,
    )
    listings = tuple(item.listing for item in saved)
    risks = {listing.listing_id: evaluate_rules(listing) for listing in listings}
    candidate_ids = service._candidate_ids(listings, risks, request)
    by_listing_id = {listing.listing_id: listing for listing in listings}
    candidates = tuple(by_listing_id[listing_id] for listing_id in candidate_ids)
    packets: list[dict[str, Any]] = []
    top_level_errors: list[dict[str, Any]] = []
    reviewer.set_packet_telemetry(lambda record: packets.append(dict(record)))
    reviews = {
        listing.listing_id: incomplete_review(listing, "Text-only replay не проверял объявление.")
        for listing in listings
    }
    if not args.dry_run:
        if not config.ai_ready:
            raise SystemExit("Text-AI configuration is incomplete")
        reviewer.begin_budget(config.effective_ai_budget_rub)
        for offset in range(0, len(candidates), config.ai_text_batch_size):
            batch = candidates[offset:offset + config.ai_text_batch_size]
            try:
                for review in reviewer.review_text_batch(batch, request):
                    reviews[review.listing_id] = review
            except AvitoServiceError as exc:
                diagnostics = dict(getattr(exc, "diagnostics", {}) or {})
                top_level_errors.append({
                    "code": exc.code,
                    "retryable": exc.retryable,
                    "http_status": diagnostics.get("http_status"),
                    "provider_error_code": diagnostics.get("provider_error_code"),
                    "reservation_state": diagnostics.get("reservation_state"),
                    "accounted_cost_rub": diagnostics.get("accounted_cost_rub"),
                })
                if exc.code in {"AI_AUTH", "AI_QUOTA", "AI_BUDGET"}:
                    break
    reviewer.set_packet_telemetry(None)
    text_decisions = []
    for listing in candidates:
        review = reviews[listing.listing_id]
        decision = evaluate_verification(listing, risks[listing.listing_id], review, request, stage="text")
        text_decisions.append({
            "listing_id": listing.listing_id,
            "state": decision.state.value,
            "primary_reason": decision.primary_reason,
            "next_stage": decision.next_stage,
            "verdict": review.verdict.value,
            "matches_request": review.matches_request,
            "confidence": review.confidence,
            "text_analyzed": review.text_analyzed,
            "error": bool(review.error),
        })
    photo_ids_before_bargain = service._photo_candidate_ids(
        listings,
        risks,
        reviews,
        len(candidates),
        request,
        market_analyzed=saved,
        require_bargain_evidence=False,
    ) if not args.dry_run else ()
    bargain_diagnostics: list[dict[str, Any]] = []
    photo_ids = service._photo_candidate_ids(
        listings,
        risks,
        reviews,
        config.ai_max_listings,
        request,
        market_analyzed=saved,
        require_bargain_evidence=True,
        bargain_diagnostics=bargain_diagnostics,
    ) if not args.dry_run else ()
    report = _safe_report(
        request=request, candidates=candidates, reviews=reviews, packets=packets,
        photo_ids=photo_ids, budget=reviewer.budget_snapshot(), dry_run=args.dry_run,
        top_level_errors=top_level_errors,
        text_decisions=text_decisions, photo_ids_before_bargain=photo_ids_before_bargain,
        bargain_diagnostics=bargain_diagnostics, snapshot_name=args.input.name,
    )
    output = args.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = ROOT / "runtime" / "avito_text_replays" / f"{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "packets": len(packets), "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
