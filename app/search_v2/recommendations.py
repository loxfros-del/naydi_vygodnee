"""Select at most three unique recommendation roles."""
from __future__ import annotations

from typing import Iterable

from .models import (
    ExactMatchResult,
    Offer,
    PlatformTrust,
    ProductGroup,
    Recommendation,
    RecommendationRole,
    RiskSeverity,
    SearchRequestV2,
    SellerTrust,
)
from .ranking import rank_offers
from .risk_engine import apply_risks
from .savings import SavingsEvidence, build_savings_evidence


def _identity(offer: Offer) -> tuple[str, str]:
    return (offer.offer_id or "", (offer.url or "").casefold().rstrip("/"))


def _reasons(
    role: RecommendationRole,
    offer: Offer,
    score: float,
    group: ProductGroup,
    savings_evidence: SavingsEvidence | None,
) -> list[str]:
    del score  # Internal ranking must never become a customer-facing explanation.
    reasons: list[str] = []
    if savings_evidence:
        reasons.append(savings_evidence.client_reason)
    if offer.exact_match is ExactMatchResult.COMPATIBLE_VARIANT:
        reasons.append("модель совпадает, но конфигурация не выбрана")
    else:
        reasons.append("точная модель и обязательная конфигурация")
    if role is RecommendationRole.RELIABLE:
        reasons.append("надёжный продавец или крупная торговая сеть")
    elif role is RecommendationRole.CHEAP_WITH_RISK:
        reasons.append("минимальная цена среди сопоставимых предложений")
    return reasons


def select_recommendations(
    groups: Iterable[ProductGroup],
    *,
    request: SearchRequestV2 | None = None,
) -> list[Recommendation]:
    prepared: list[tuple[Offer, float, ProductGroup]] = []
    for group in groups:
        for offer in group.offers:
            with_risks = apply_risks(offer, group.market_stats)
            ranked = rank_offers([with_risks], group.market_stats, request)
            if ranked:
                prepared.append((with_risks, ranked[0][1], group))
    prepared.sort(key=lambda item: (-item[1], float(item[0].price or 10**18), item[0].offer_id))
    if not prepared:
        return []

    selected: list[Recommendation] = []
    used: set[tuple[str, str]] = set()

    def add(role: RecommendationRole, candidate: tuple[Offer, float, ProductGroup] | None) -> None:
        if candidate is None:
            return
        offer, score, group = candidate
        identity = _identity(offer)
        if identity in used:
            return
        used.add(identity)
        savings_evidence = build_savings_evidence(offer, group)
        selected.append(Recommendation(
            role=role,
            offer_id=offer.offer_id,
            offer=offer,
            score=score,
            reasons=_reasons(role, offer, score, group, savings_evidence),
            risks=list(offer.risk_flags),
            checks=[risk.title for risk in offer.risk_flags if risk.severity in {RiskSeverity.WARNING, RiskSeverity.HIGH}],
            savings_evidence=savings_evidence,
        ))

    add(RecommendationRole.BEST_OVERALL, prepared[0])
    best_price = float(prepared[0][0].price or 0)
    remaining = [item for item in prepared[1:] if _identity(item[0]) not in used]
    cheap_pool = [
        item for item in remaining
        if item[0].price and float(item[0].price) < best_price
    ]
    cheap = min(cheap_pool, key=lambda item: (float(item[0].price or 10**18), -item[1])) if cheap_pool else None
    add(RecommendationRole.CHEAP_WITH_RISK, cheap)

    remaining = [item for item in prepared if _identity(item[0]) not in used]
    reliable_pool = [
        item for item in remaining
        if item[0].platform_trust is PlatformTrust.HIGH_RETAIL
        or item[0].seller.trust is SellerTrust.HIGH
        or item[0].seller.official is True
    ]
    reliable = max(reliable_pool, key=lambda item: (item[1], -(item[0].price or 10**18))) if reliable_pool else None
    add(RecommendationRole.RELIABLE, reliable)
    return selected[:3]


__all__ = ["select_recommendations"]
