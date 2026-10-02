"""Shared fail-closed verification states for text, photo and final gates."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .matching import matches_listing_request
from .models import AIReview, AnalyzedListing, NormalizedListing, ReviewVerdict, RiskFinding, SearchRequest, Severity


class VerificationState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"


@dataclass(frozen=True, slots=True)
class VerificationDecision:
    state: VerificationState
    primary_reason: str = ""
    secondary_reasons: tuple[str, ...] = ()
    next_stage: str = ""


def evaluate_verification(
    listing: NormalizedListing,
    risks: tuple[RiskFinding, ...],
    review: AIReview,
    request: SearchRequest,
    *,
    stage: str,
) -> VerificationDecision:
    """Evaluate known failures separately from evidence still obtainable later."""
    blockers = tuple(item.code for item in risks if item.severity is not Severity.INFO)
    if blockers:
        return VerificationDecision(VerificationState.FAIL, blockers[0], blockers[1:])
    if not review.text_analyzed:
        return VerificationDecision(VerificationState.NEEDS_EVIDENCE, "TEXT_REVIEW_INCOMPLETE", next_stage="text_ai")
    failures: list[str] = []
    if not review.matches_request:
        failures.append("AI_REQUEST_MISMATCH")
    if review.verdict is ReviewVerdict.REJECT:
        failures.append("AI_VERDICT_REJECT")
    elif review.verdict is ReviewVerdict.CAUTION:
        failures.append("AI_VERDICT_CAUTION")
    if review.conflicts:
        failures.append("AI_CONFLICT")
    if review.defects:
        failures.append("AI_DEFECT")
    if review.price_conditions:
        failures.append("AI_PRICE_CONDITION")
    if review.confidence < 0.5:
        failures.append("AI_LOW_CONFIDENCE")
    if not matches_listing_request(
        listing, request, identified_model=review.identified_model,
        storage=review.storage, sim_variant=review.sim_variant,
        condition=review.condition, final=True,
    ):
        failures.append("FINAL_REQUEST_MISMATCH")
    if failures:
        return VerificationDecision(VerificationState.FAIL, failures[0], tuple(failures[1:]))

    item = AnalyzedListing(listing, risks, review)
    if stage == "text":
        if review.is_complete_for(listing) and item.condition_evidence_complete():
            return VerificationDecision(VerificationState.PASS)
        return VerificationDecision(
            VerificationState.NEEDS_EVIDENCE,
            "CONDITION_EVIDENCE_INCOMPLETE" if not item.condition_evidence_complete() else "PHOTO_NOT_ANALYZED",
            next_stage="photo_ai",
        )

    if review.error:
        return VerificationDecision(VerificationState.NEEDS_EVIDENCE, "AI_REVIEW_ERROR", next_stage="photo_ai")
    if not review.is_complete_for(listing):
        return VerificationDecision(VerificationState.NEEDS_EVIDENCE, "PHOTO_COVERAGE_INCOMPLETE", next_stage="photo_ai")
    if not item.condition_evidence_complete():
        return VerificationDecision(VerificationState.FAIL, "CONDITION_EVIDENCE_INCOMPLETE")
    if not item.configuration_evidence_complete():
        return VerificationDecision(VerificationState.FAIL, "CONFIGURATION_EVIDENCE_INCOMPLETE")
    if review.verdict is ReviewVerdict.CAUTION:
        return VerificationDecision(VerificationState.FAIL, "AI_VERDICT_CAUTION")
    return VerificationDecision(VerificationState.PASS)
