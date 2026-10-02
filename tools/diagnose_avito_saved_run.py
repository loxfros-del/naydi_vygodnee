"""Rebuild the cheap Avito funnel from saved facts without network or paid AI."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.matching import matches_listing_request, matches_requested_city
from avito_service.models import SearchRequest, Severity
from avito_service.normalization import normalize_dataset
from avito_service.risk_rules import evaluate_rules
from tools.run_avito_pilot import closest_failures


PREFILTER_CODES = {
    "INVALID_DIRECT_URL", "MISSING_DESCRIPTION", "MISSING_PHOTOS",
    "NON_FINAL_PRICE", "PHOTO_SET_INCOMPLETE",
}


def rejection_reasons(listing, request):
    reasons = []
    if not matches_requested_city(listing, request, final=True):
        reasons.append("wrong_or_unknown_city")
        return reasons
    findings = evaluate_rules(listing)
    reasons.extend(f"risk:{item.code}" for item in findings if item.severity is Severity.CRITICAL)
    reasons.extend(f"risk:{item.code}" for item in findings if item.code in PREFILTER_CODES)
    if listing.acquisition_price is None:
        reasons.append("unknown_full_price")
    if not matches_listing_request(listing, request):
        reasons.append("request_mismatch")
    return list(dict.fromkeys(reasons))


def diagnose(run_dir: Path):
    request = SearchRequest(**json.loads((run_dir / "request.json").read_text(encoding="utf-8")))
    facts_path = run_dir / "candidates-facts.json"
    if not facts_path.exists():
        facts_path = run_dir / "market-facts.json"
    listings = normalize_dataset(json.loads(facts_path.read_text(encoding="utf-8")))
    rejected = []
    for listing in listings:
        reasons = rejection_reasons(listing, request)
        if reasons:
            rejected.append({"id": listing.listing_id, "title": listing.title,
                             "location": listing.location, "reasons": reasons})
    correct_city = sum(matches_requested_city(item, request, final=True) for item in listings)
    basic_filters = len(listings) - len(rejected)
    audit_path = run_dir / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    previous = audit.get("adminAudit", {})
    high_confidence = sum(
        card.get("analysis", {}).get("complete")
        and card.get("analysis", {}).get("confidence", 0) >= 0.75
        and card.get("analysis", {}).get("verdict") == "approve"
        and not any(card.get("analysis", {}).get(name) for name in ("defects", "conflicts", "priceConditions"))
        for card in audit.get("adminRecommendations", ())
    )
    funnel = {
        "collected": len(listings), "correct_city": correct_city, "basic_filters": basic_filters,
        "text_check": previous.get("textCompleted", 0), "photo_check": previous.get("photoCompleted", 0),
        "HIGH_CONFIDENCE": high_confidence, "CONFIRMED": len(audit.get("recommendations", ())),
    }
    return {
        "source": str(facts_path), "funnel": funnel,
        "prefilterRejections": dict(Counter(reason for item in rejected for reason in item["reasons"])),
        "localPrefilterRejections": [item for item in rejected if item["location"] == "yaroslavl"],
        "closest": closest_failures(audit) if funnel["CONFIRMED"] == 0 else [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(diagnose(args.run_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
