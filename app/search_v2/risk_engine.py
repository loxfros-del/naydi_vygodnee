"""Structured commercial risks; transport diagnostics are deliberately excluded."""
from __future__ import annotations

from dataclasses import replace

from .models import (
    AvailabilityStatus,
    ExactMatchResult,
    MarketStats,
    Offer,
    PlatformTrust,
    PriceClass,
    ProductCondition,
    RiskFlag,
    RiskSeverity,
    SellerTrust,
)


def assess_seller_trust(offer: Offer) -> SellerTrust:
    seller = offer.seller
    if seller.verified is True or seller.official is True:
        return SellerTrust.HIGH
    if offer.platform_trust is PlatformTrust.HIGH_RETAIL and seller.name:
        return SellerTrust.HIGH
    if seller.rating is not None and seller.rating >= 4.7 and (seller.reviews_count or 0) >= 100:
        return SellerTrust.HIGH
    if seller.rating is not None and seller.rating >= 4.3 and (seller.reviews_count or 0) >= 10:
        return SellerTrust.MEDIUM
    if seller.rating is not None and seller.rating < 4.0:
        return SellerTrust.LOW
    return seller.trust if seller.trust is not SellerTrust.UNKNOWN else SellerTrust.UNKNOWN


def _risk(code: str, severity: RiskSeverity, title: str, explanation: str, *evidence: str) -> RiskFlag:
    return RiskFlag(
        code=code,
        severity=severity,
        title=title,
        explanation=explanation,
        evidence=[item for item in evidence if item],
        confidence=0.9,
    )


def evaluate_risks(offer: Offer, market_stats: MarketStats | None = None) -> list[RiskFlag]:
    risks: list[RiskFlag] = []
    seller_trust = assess_seller_trust(offer)
    seller_type = (offer.seller.seller_type or "unknown").casefold()
    metadata = offer.raw_metadata if isinstance(offer.raw_metadata, dict) else {}

    if offer.availability.status is AvailabilityStatus.OUT_OF_STOCK:
        risks.append(_risk("UNAVAILABLE", RiskSeverity.CRITICAL, "Нет в наличии", "Недоступное предложение нельзя рекомендовать."))
    if offer.exact_match is ExactMatchResult.GENERIC_MATCH:
        risks.append(_risk("MODEL_NEEDS_CHECK", RiskSeverity.HIGH, "Модель требует проверки", "Не все обязательные характеристики подтверждены."))
    if seller_trust is SellerTrust.LOW:
        risks.append(_risk("LOW_SELLER_TRUST", RiskSeverity.HIGH, "Низкое доверие к продавцу", "Рейтинг продавца ниже безопасного ориентира."))
    elif seller_trust is SellerTrust.UNKNOWN:
        risks.append(_risk("UNKNOWN_SELLER", RiskSeverity.WARNING, "Продавец не подтверждён", "Площадка и продавец оцениваются отдельно."))

    is_avito = offer.platform_trust is PlatformTrust.CLASSIFIED or "avito" in f"{offer.platform} {offer.source}".casefold()
    if is_avito and seller_type == "private":
        risks.append(_risk("PRIVATE_SELLER", RiskSeverity.WARNING, "Частный продавец", "Нужно проверить историю продавца и условия возврата."))
    if is_avito and (offer.seller.reviews_count or 0) < 5:
        risks.append(_risk("FEW_SELLER_REVIEWS", RiskSeverity.WARNING, "Мало отзывов", "Недостаточно истории для уверенной оценки продавца."))
    if is_avito and bool(metadata.get("new_account")):
        risks.append(_risk("NEW_SELLER_ACCOUNT", RiskSeverity.HIGH, "Новый аккаунт", "У продавца короткая история на площадке."))

    condition = offer.condition if offer.condition is not ProductCondition.UNKNOWN else (offer.identity.condition if offer.identity else ProductCondition.UNKNOWN)
    if condition is ProductCondition.USED:
        risks.append(_risk("USED_ITEM", RiskSeverity.WARNING, "Товар бывший в употреблении", "Состояние нельзя смешивать с новым товаром."))
    elif condition is ProductCondition.REFURBISHED:
        risks.append(_risk("REFURBISHED_ITEM", RiskSeverity.HIGH, "Восстановленный товар", "Нужно проверить качество восстановления и гарантию."))

    price_class = market_stats.price_classes.get(offer.offer_id, PriceClass.UNKNOWN) if market_stats else PriceClass.UNKNOWN
    if price_class is PriceClass.VERY_CHEAP:
        deviation = market_stats.deviation_percent.get(offer.offer_id) if market_stats else None
        risks.append(_risk(
            "VERY_CHEAP_PRICE", RiskSeverity.HIGH, "Цена сильно ниже рынка",
            "Предложение не удаляется, но требует проверки товара, продавца и условий оплаты.",
            f"Отклонение: {deviation:.1f}%" if deviation is not None else "",
        ))
    if not offer.seller.warranty:
        risks.append(_risk("UNKNOWN_WARRANTY", RiskSeverity.WARNING, "Гарантия не подтверждена", "Уточните срок и исполнителя гарантии."))
    if metadata.get("activation_unknown"):
        risks.append(_risk("UNKNOWN_ACTIVATION", RiskSeverity.WARNING, "Активация неизвестна", "Проверьте дату и факт активации устройства."))
    if metadata.get("incomplete_set"):
        risks.append(_risk("INCOMPLETE_SET", RiskSeverity.HIGH, "Неполный комплект", "Состав комплекта отличается от стандартного."))
    if metadata.get("foreign_region") or metadata.get("esim_only"):
        risks.append(_risk("REGION_OR_SIM_VARIANT", RiskSeverity.WARNING, "Региональная версия", "Проверьте SIM-вариант, регион и сервисную поддержку."))

    unique: dict[str, RiskFlag] = {}
    for risk in risks:
        unique.setdefault(risk.code, risk)
    return list(unique.values())


def apply_risks(offer: Offer, market_stats: MarketStats | None = None) -> Offer:
    seller = replace(offer.seller, trust=assess_seller_trust(offer))
    updated = replace(offer, seller=seller)
    return replace(updated, risk_flags=evaluate_risks(updated, market_stats))


def risk_penalty(offer: Offer) -> float:
    weights = {
        RiskSeverity.INFO: 1.0,
        RiskSeverity.WARNING: 4.0,
        RiskSeverity.HIGH: 10.0,
        RiskSeverity.CRITICAL: 100.0,
    }
    return sum(weights.get(risk.severity, 4.0) for risk in offer.risk_flags if not risk.resolved_by_manual)


__all__ = ["apply_risks", "assess_seller_trust", "evaluate_risks", "risk_penalty"]
