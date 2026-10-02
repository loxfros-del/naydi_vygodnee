"""Privacy-safe rejection routing and aggregate diagnostics."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .matching import diagnose_request_mismatch, evidence_known, matches_listing_request, matches_requested_city
from .models import AnalyzedListing, Recommendation, ReviewVerdict, RiskFinding, SearchRequest, Severity
from .request_intent import check_request_compatibility
from .ranking import is_customer_safe


class AmbiguityState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class TextRoute:
    state: AmbiguityState
    needs_ai: bool
    reason_codes: tuple[str, ...] = ()
    mismatch_field: str = ""


def route_text_ai(
    listing, request: SearchRequest, risks: Iterable[RiskFinding],
) -> TextRoute:
    """Mirror current hard filters, then retain mandatory safety review.

    KNOWN does not imply that a publishable listing may skip text analysis: the
    current product contract still requires reading the complete untrusted
    description for defects, price conditions and contradictions.
    """
    findings = tuple(risks)
    blocking = tuple(
        item.code for item in findings
        if item.severity is Severity.CRITICAL or item.code in {
            "INVALID_DIRECT_URL", "MISSING_DESCRIPTION", "MISSING_PHOTOS",
            "NON_FINAL_PRICE", "PHOTO_SET_INCOMPLETE",
        }
    )
    if blocking:
        return TextRoute(AmbiguityState.KNOWN, False, tuple(dict.fromkeys(blocking)))
    if not matches_requested_city(listing, request, final=True):
        return TextRoute(AmbiguityState.KNOWN, False, ("LOCATION_MISMATCH",), "city")
    compatibility = check_request_compatibility(listing, request)
    if not compatibility.matches:
        state = AmbiguityState.CONFLICT if "CONFLICT" in compatibility.reason else AmbiguityState.KNOWN
        return TextRoute(state, False, ("REQUEST_FILTER_MISMATCH", compatibility.reason),
                         compatibility.field or "other")
    if not listing.acquisition_price or not matches_listing_request(listing, request):
        diagnostic = diagnose_request_mismatch(listing, request)
        return TextRoute(AmbiguityState.KNOWN, False, ("REQUEST_FILTER_MISMATCH",),
                         diagnostic.field or "other")

    required_values = (
        (request.required_storage, listing.storage),
        (request.required_sim, listing.sim_variant),
        (request.required_condition, listing.condition),
    )
    unknown = any(expected and not evidence_known(actual) for expected, actual in required_values)
    # Even fully structured KNOWN rows need the mandatory text safety scan.
    return TextRoute(AmbiguityState.UNKNOWN if unknown else AmbiguityState.KNOWN, True,
                     ("MANDATORY_TEXT_SAFETY_REVIEW",))


def _review_reasons(item: AnalyzedListing, request: SearchRequest) -> tuple[str, tuple[str, ...]]:
    review = item.ai_review
    reasons: list[str] = []
    if not review.text_analyzed:
        reasons.append("TEXT_REVIEW_INCOMPLETE")
    if review.text_analyzed and not review.matches_request:
        reasons.append("AI_REQUEST_MISMATCH")
    if review.verdict is ReviewVerdict.REJECT:
        reasons.append("AI_VERDICT_REJECT")
    if review.conflicts:
        reasons.append("AI_CONFLICT")
    if review.defects:
        reasons.append("AI_DEFECT")
    if review.price_conditions:
        reasons.append("AI_PRICE_CONDITION")
    if review.text_analyzed and review.confidence < 0.5:
        reasons.append("AI_LOW_CONFIDENCE")
    if review.text_analyzed and not matches_listing_request(
        item.listing, request, identified_model=review.identified_model,
        storage=review.storage, sim_variant=review.sim_variant,
        condition=review.condition, final=True,
    ):
        reasons.append("FINAL_REQUEST_MISMATCH")
    if not item.condition_evidence_complete():
        reasons.append("CONDITION_EVIDENCE_INCOMPLETE")
    if not item.configuration_evidence_complete():
        reasons.append("CONFIGURATION_EVIDENCE_INCOMPLETE")
    if not review.photos_analyzed:
        reasons.append("PHOTO_NOT_ANALYZED")
    elif not review.is_complete_for(item.listing):
        reasons.append("PHOTO_COVERAGE_INCOMPLETE")
    if review.error:
        reasons.append("AI_REVIEW_ERROR")
    return (reasons[0] if reasons else "FINAL_SAFETY_REJECTED", tuple(reasons[1:]))


def aggregate_rejections(
    analyzed: tuple[AnalyzedListing, ...], request: SearchRequest,
    candidate_ids: set[str], recommendations: tuple[Recommendation, ...],
) -> tuple[dict[str, object], ...]:
    """Aggregate only stable predicate codes; never include seller raw text."""
    recommendation_by_id = {item.analyzed.listing.listing_id: item for item in recommendations}
    stages: dict[str, tuple[Counter[str], Counter[str], int]] = {}
    mismatch_fields: dict[str, Counter[str]] = {}

    def add(stage: str, primary: str, secondary: tuple[str, ...], mismatch_field: str = "") -> None:
        primary_counts, secondary_counts, count = stages.setdefault(stage, (Counter(), Counter(), 0))
        primary_counts[primary] += 1
        secondary_counts.update(set(secondary))
        stages[stage] = (primary_counts, secondary_counts, count + 1)
        if mismatch_field:
            mismatch_fields.setdefault(stage, Counter())[mismatch_field] += 1

    for item in analyzed:
        identifier = item.listing.listing_id
        recommendation = recommendation_by_id.get(identifier)
        visible = bool(
            recommendation and is_customer_safe(item, request)
            and item.ai_review.is_complete_for(item.listing)
        )
        if visible:
            continue
        if identifier not in candidate_ids:
            codes = tuple(dict.fromkeys(
                risk.code for risk in item.deterministic_risks
                if risk.severity is not Severity.INFO
            ))
            compatibility = check_request_compatibility(item.listing, request)
            if not codes and not matches_requested_city(item.listing, request, final=True):
                codes = ("LOCATION_MISMATCH",)
            if not codes and not compatibility.matches:
                codes = (compatibility.reason or "REQUEST_FILTER_MISMATCH",)
            if not codes:
                codes = ("REQUEST_FILTER_MISMATCH",)
            diagnostic = diagnose_request_mismatch(item.listing, request, final=True)
            add("before_text_ai", codes[0], codes[1:], diagnostic.field if not diagnostic.matches else "")
            continue

        primary, secondary = _review_reasons(item, request)
        review = item.ai_review
        if not review.text_analyzed or review.verdict is ReviewVerdict.REJECT or not review.matches_request \
                or review.conflicts or review.defects or review.price_conditions or review.confidence < 0.5:
            add("after_text_ai", primary, secondary)
        elif not review.photos_analyzed or not review.is_complete_for(item.listing):
            add("before_or_during_photo_ai", primary, secondary)
        elif not item.listing.is_freshly_verified():
            add("final_revalidation", "FINAL_REVALIDATION_FAILED", (primary, *secondary))
        else:
            add("final_safety", primary, secondary)

    order = ("before_text_ai", "after_text_ai", "before_or_during_photo_ai",
             "ranking", "final_revalidation", "final_safety")
    return tuple({
        "stage": stage,
        "rejected": stages[stage][2],
        "primary": dict(sorted(stages[stage][0].items())),
        "secondary": dict(sorted(stages[stage][1].items())),
        "mismatchFields": dict(sorted(mismatch_fields.get(stage, {}).items())),
    } for stage in order if stage in stages)
