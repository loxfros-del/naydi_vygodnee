"""Typed data contracts used by the standalone Avito pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    CAUTION = "caution"
    REJECT = "reject"


class RecommendationRole(str, Enum):
    TOP = "TOP"
    BACKUP = "BACKUP"
    BUDGET = "BUDGET"
    CAUTION = "CAUTION"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    location: str = "Россия"
    category: str = "all"
    max_results: int = 20
    mode: str = "bargain"
    priority: str = "balanced"
    desired_results: int = 5
    price_min: int | None = None
    price_max: int | None = None
    required_storage: str = ""
    required_sim: str = ""
    required_condition: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    pickup_only: bool = False

    def __post_init__(self) -> None:
        if type(self.pickup_only) is not bool:
            raise ValueError("Поле «Только самовывоз» должно быть true или false.")
        if len(self.query.strip()) < 2:
            raise ValueError("Укажите товар или модель длиной не менее двух символов.")
        if not 1 <= self.max_results <= 200:
            raise ValueError("Количество объявлений должно быть от 1 до 200.")
        if self.mode not in {"find", "bargain"}:
            raise ValueError("Режим поиска должен быть find или bargain.")
        if self.priority not in {"quality", "balanced", "budget"}:
            raise ValueError("Приоритет должен быть quality, balanced или budget.")
        if not 3 <= self.desired_results <= 10:
            raise ValueError("Количество вариантов должно быть от 3 до 10.")
        if self.price_min is not None and self.price_min < 0:
            raise ValueError("Минимальная цена не может быть отрицательной.")
        if self.price_max is not None and self.price_max < 0:
            raise ValueError("Максимальная цена не может быть отрицательной.")
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError("Минимальная цена не может быть выше максимальной.")
        if len(self.attributes) > 12:
            raise ValueError("Можно указать не более 12 характеристик.")
        for name, value in self.attributes:
            if not name.strip() or len(name) > 80 or len(value) > 160:
                raise ValueError("Некорректная дополнительная характеристика.")

    def attribute_map(self) -> dict[str, str]:
        return {name: value for name, value in self.attributes if value.strip()}


@dataclass(frozen=True, slots=True)
class SellerSummary:
    name: str = ""
    seller_type: str = ""
    rating: float | None = None
    review_count: int | None = None
    member_since: str = ""
    is_shop: bool = False
    # Stable provider identity, hashed during normalization. Never serialized.
    identity_hash: str = ""
    identity_kind: str = ""
    profile_url: str = ""

    @property
    def kind(self) -> str:
        seller_type = self.seller_type.casefold().strip()
        return "company" if self.is_shop or seller_type in {"company", "business", "shop"} else "private"

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seller_type": self.seller_type,
            "rating": self.rating,
            "review_count": self.review_count,
            "member_since": self.member_since,
            "is_shop": self.is_shop,
            "kind": self.kind,
            "stable_identity_available": bool(self.identity_hash),
            "identity_kind": self.identity_kind or "unknown",
            "profile_url": self.profile_url,
        }


@dataclass(frozen=True, slots=True)
class NormalizedListing:
    listing_id: str
    title: str
    url: str
    status: str
    price: int | None
    currency: str
    description: str
    images: tuple[str, ...]
    image_count_claimed: int
    parameters: dict[str, str]
    parameter_ids: dict[str, int]
    badges: tuple[str, ...]
    stock: str
    seller: SellerSummary
    collected_at: str
    location: str = ""
    address: str = ""
    delivery: str = ""
    verified_at: str = ""
    verification_status: str = "unverified"
    battery_health_percent: int | None = None
    repair_status: str = ""
    parts_status: str = ""
    completeness: str = ""
    mandatory_fee_rub: int | None = 0
    delivery_cost_rub: int | None = None
    delivery_required: bool = False

    @property
    def analysis_parameters(self) -> dict[str, str]:
        """Product facts used for analysis; warehouse metadata is intentionally ignored."""
        return {key: value for key, value in self.parameters.items()
                if not re.search(r"налич|остаток|доступност|stock|availability", key, re.IGNORECASE)}

    @property
    def acquisition_price(self) -> int | None:
        """Known payable amount for the explicitly stated collection basis."""
        base_price = self.effective_price
        if base_price is None or base_price <= 0 or self.mandatory_fee_rub is None or self.mandatory_fee_rub < 0:
            return None
        if self.delivery_required and (self.delivery_cost_rub is None or self.delivery_cost_rub < 0):
            return None
        return base_price + self.mandatory_fee_rub + (self.delivery_cost_rub if self.delivery_required else 0)

    @property
    def advertised_price(self) -> int | None:
        return self.price

    @property
    def price_truth(self):
        from .price_truth import analyze_price_truth

        return analyze_price_truth(self)

    @property
    def effective_price(self) -> int | None:
        return self.price_truth.effective_price

    @property
    def price_confidence(self) -> str:
        return self.price_truth.price_confidence

    @property
    def price_basis(self) -> str:
        return "required_delivery_included" if self.delivery_required else "pickup_without_optional_delivery"

    @staticmethod
    def _recent_timestamp(value: str, max_age_seconds: int = 900) -> bool:
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                return False
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            return 0 <= age <= max_age_seconds
        except (AttributeError, TypeError, ValueError):
            return False

    def is_freshly_verified(self, max_age_seconds: int = 900) -> bool:
        return self.verification_status == "verified" and self._recent_timestamp(
            self.verified_at, max_age_seconds
        )

    def is_recently_collected(self, max_age_seconds: int = 900) -> bool:
        return self._recent_timestamp(self.collected_at, max_age_seconds)

    def parameter(self, name: str) -> str:
        wanted = name.casefold()
        for key, value in self.parameters.items():
            if key.casefold() == wanted:
                return value
        return ""

    @property
    def model(self) -> str:
        return self.parameter("Модель") or self.title

    @property
    def storage(self) -> str:
        from .matching import canonical_evidence

        values = [self.parameter("Встроенная память")]
        for name, value in self.parameters.items():
            if re.fullmatch(r"встроенная\s+память\s*,\s*(?:гб|gb)", name.casefold().strip()):
                # The actor puts the unit in the parameter name and a bare
                # number in its value. Preserve contradictory aliases too.
                values.append(f"{value} GB" if re.fullmatch(r"\d+(?:[.,]\d+)?", value.strip()) else value)
        unique = {canonical_evidence("storage", value): value for value in values if value}
        return " / ".join(unique.values())

    @property
    def sim_variant(self) -> str:
        return self.parameter("SIM-карты")

    @property
    def condition(self) -> str:
        return self.parameter("Состояние")

    @property
    def activation(self) -> str:
        return self.parameter("История смартфона")

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.listing_id,
            "title": self.title,
            "url": self.url,
            "status": self.status,
            "price": self.price,
            "advertisedPrice": self.advertised_price,
            "effectivePrice": self.effective_price,
            "priceConfidence": self.price_confidence,
            "priceVariantConflicts": list(self.price_truth.conflicts),
            "priceOffers": [offer.public_dict() for offer in self.price_truth.offers],
            "paymentConditions": list(self.price_truth.payment_conditions),
            "acquisitionPrice": self.acquisition_price,
            "priceBasis": self.price_basis,
            "mandatoryFeeRub": self.mandatory_fee_rub,
            "deliveryCostRub": self.delivery_cost_rub,
            "deliveryRequired": self.delivery_required,
            "currency": self.currency,
            "descriptionExcerpt": self.description[:700],
            "image": self.images[0] if self.images else "",
            "imageCount": len(self.images),
            "parameters": dict(self.parameters),
            "badges": list(self.badges),
            "stock": self.stock,
            "seller": self.seller.public_dict(),
            "collectedAt": self.collected_at,
            "location": self.location,
            "address": self.address,
            "delivery": self.delivery,
            "verifiedAt": self.verified_at,
            "verificationStatus": self.verification_status,
            "conditionEvidence": {
                "batteryHealthPercent": self.battery_health_percent,
                "repairStatus": self.repair_status or "unknown",
                "partsStatus": self.parts_status or "unknown",
                "completeness": self.completeness or "unknown",
                "basis": "seller_listing",
            },
        }


@dataclass(frozen=True, slots=True)
class RiskFinding:
    code: str
    severity: Severity
    message: str
    evidence: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "evidence": self.evidence[:500],
        }


@dataclass(frozen=True, slots=True)
class AIReview:
    listing_id: str
    text_analyzed: bool
    photos_analyzed: bool
    photo_coverage: tuple[int, ...] = ()
    identified_model: str = ""
    storage: str = ""
    sim_variant: str = ""
    condition: str = ""
    matches_request: bool = True
    mismatch_reason: str = ""
    description_findings: tuple[str, ...] = ()
    photo_findings: tuple[str, ...] = ()
    # Explicit photo-stage evidence. ``pass`` means the photos actually show
    # enough of the visible condition / advertised kit; unknown never becomes
    # an approval merely because no defect was found.
    photo_condition_evidence: str = ""
    photo_completeness_evidence: str = ""
    defects: tuple[str, ...] = ()
    price_conditions: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    manual_checks: tuple[str, ...] = ()
    verdict: ReviewVerdict = ReviewVerdict.CAUTION
    confidence: float = 0.0
    error: str = ""
    cost_rub: float = 0.0
    request_count: int = 0
    cached: bool = False
    cost_estimated: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None

    def is_complete_for(self, listing: NormalizedListing) -> bool:
        expected = tuple(range(1, len(listing.images) + 1))
        return bool(
            self.text_analyzed
            and self.photos_analyzed
            and listing.description
            and listing.images
            and tuple(sorted(set(self.photo_coverage))) == expected
        )

    def public_dict(self, listing: NormalizedListing) -> dict[str, Any]:
        return {
            "complete": self.is_complete_for(listing),
            "textAnalyzed": self.text_analyzed,
            "photosAnalyzed": self.photos_analyzed,
            "photoCoverage": list(self.photo_coverage),
            "identifiedModel": self.identified_model,
            "storage": self.storage,
            "simVariant": self.sim_variant,
            "condition": self.condition,
            "matchesRequest": self.matches_request,
            "mismatchReason": self.mismatch_reason,
            "descriptionFindings": list(self.description_findings),
            "photoFindings": list(self.photo_findings),
            "photoConditionEvidence": self.photo_condition_evidence or "unknown",
            "photoCompletenessEvidence": self.photo_completeness_evidence or "unknown",
            "manualChecks": list(self.manual_checks),
            "conditionBasis": "listing_text_and_photos",
            "functionalConditionVerified": False,
            "defects": list(self.defects),
            "priceConditions": list(self.price_conditions),
            "conflicts": list(self.conflicts),
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class AnalyzedListing:
    listing: NormalizedListing
    deterministic_risks: tuple[RiskFinding, ...]
    ai_review: AIReview

    def market_lane(self) -> str:
        from .matching import canonical_evidence
        condition = (self.ai_review.condition or self.listing.condition).casefold().replace("ё", "е")
        if re.search(r"восстанов|refurb|рефаб", condition):
            return "refurbished"
        grade = canonical_evidence("condition", condition)
        if grade in {"good", "excellent", "used"}:
            return "used"
        if grade == "new":
            return "new_company" if self.listing.seller.kind == "company" else "new_private"
        if re.search(r"(?:^|\W)(?:б\s*/?\s*у|used)(?:$|\W)|как\s+нов|не\s+нов|отличн|идеальн|хорош|удовлетвор|бывш|витрин|распакован", condition):
            return "used"
        if re.search(r"(?:^|\W)(?:новый|новое|новая|новые|новое в упаковке|new)(?:$|\W)", condition):
            return "new_company" if self.listing.seller.kind == "company" else "new_private"
        return "unknown"

    @staticmethod
    def _history_unknown(value: str) -> bool:
        return value.strip().casefold() in {"", "unknown", "неизвестно", "не указано"}

    def _is_electronics(self) -> bool:
        identity = f"{self.listing.title} {self.ai_review.identified_model} {self.listing.model}".casefold()
        return bool(re.search(
            r"iphone|ipad|samsung|galaxy|pixel|xiaomi|redmi|realme|oneplus|huawei|honor|"
            r"macbook|ноутбук|компьютер|телефон|смартфон|планшет|телевизор|playstation|"
            r"\bps[45]\b|xbox|nintendo|видеокарт|стиральн|холодильник", identity,
        ))

    def history_warnings(self) -> tuple[str, ...]:
        """User-authorized unknown history is always disclosed, never inferred."""
        if self.market_lane() not in {"used", "refurbished"} or not self._is_electronics():
            return ()
        warnings = []
        if self._history_unknown(self.listing.repair_status):
            warnings.append("История ремонта неизвестна — уточните при осмотре.")
        if self._history_unknown(self.listing.parts_status):
            warnings.append("Оригинальность внутренних деталей не подтверждена.")
        return tuple(warnings)

    def condition_evidence_complete(self) -> bool:
        """Unknown history may be disclosed; state, kit and contradictions stay strict."""
        lane = self.market_lane()
        photo_condition_ok = self.ai_review.photo_condition_evidence == "pass"
        photo_completeness_ok = self.ai_review.photo_completeness_evidence == "pass"
        if lane == "unknown" and not photo_condition_ok:
            return False
        if (not self.listing.completeness
                or self.listing.completeness.casefold() in {"unknown", "неизвестно", "не указано"}) \
                and not photo_completeness_ok:
            return False
        if lane.startswith("new_"):
            return True
        identity = f"{self.listing.title} {self.ai_review.identified_model} {self.listing.model}".casefold()
        phone = bool(
            self.listing.sim_variant or "/telefony/" in self.listing.url
            or re.search(r"iphone|galaxy\s+[saz]\s*\d|pixel\s+\d|телефон|смартфон", identity)
        )
        if self._is_electronics() and (
            (self.listing.repair_status not in {"never_repaired", "repaired"}
             and not self._history_unknown(self.listing.repair_status))
            or (self.listing.parts_status not in {"original", "non_original"}
                and not self._history_unknown(self.listing.parts_status))
        ):
            return False
        if phone and (
            self.listing.battery_health_percent is None
            or not (self.ai_review.storage or self.listing.storage)
            or not (self.ai_review.sim_variant or self.listing.sim_variant)
        ):
            return False
        return True

    def configuration_evidence_complete(self) -> bool:
        """A shared unknown computer/phone configuration cannot substantiate a discount."""
        from .matching import evidence_known

        identity = f"{self.listing.title} {self.ai_review.identified_model} {self.listing.model}".casefold()
        phone = bool(self.listing.sim_variant or "/telefony/" in self.listing.url
                     or re.search(r"iphone|galaxy\s+[saz]\s*\d|pixel\s+\d|телефон|смартфон", identity))
        if phone and not all(evidence_known(value) for value in (
            self.ai_review.storage or self.listing.storage,
            self.ai_review.sim_variant or self.listing.sim_variant,
        )):
            return False
        computer = bool(re.search(r"macbook|ноутбук|компьютер|laptop|notebook", identity)
                        or any(part in self.listing.url for part in ("/noutbuki/", "/nastolnye_kompyutery/")))
        if computer:
            def known(names: tuple[str, ...]) -> bool:
                return any(evidence_known(self.listing.parameter(name)) for name in names)
            cpu = known(("Процессор", "CPU")) or bool(re.search(r"\bmacbook\b.*\bm\d+\b", identity))
            ram = known(("Оперативная память", "Объем оперативной памяти", "Объём оперативной памяти", "RAM"))
            gpu = known(("Видеокарта", "GPU"))
            storage = evidence_known(self.ai_review.storage or self.listing.storage) or known(
                ("Объем накопителя", "Объём накопителя", "Накопитель", "SSD", "HDD"))
            return bool(cpu and ram and gpu and storage)
        return True

    def comparable_key(self, pickup_only: bool = False) -> tuple[str, ...]:
        from .matching import canonical_evidence, canonical_text, effective_model_identity, storage_with_source_unit
        from .normalization import canonical_kit_evidence

        def clean(value: str) -> str:
            normalized = value.casefold().replace("ё", "е")
            normalized = re.sub(
                r"(?<![a-zа-я])(?:gbytes?|gb|гбайт(?:а|ов)?|гб)\b",
                " gb ",
                normalized,
            )
            normalized = re.sub(
                r"(?<![a-zа-я])(?:tbytes?|tb|тбайт(?:а|ов)?|тб)\b",
                " tb ",
                normalized,
            )
            normalized = re.sub(r"\b(?:sim\s*\+\s*esim|sim\s+esim)\b", " sim esim ", normalized)
            return re.sub(r"[^a-zа-я0-9]+", " ", normalized).strip()

        battery = self.listing.battery_health_percent
        battery_band = "unknown" if battery is None else (
            "below_80" if battery < 80 else "80_89" if battery < 90 else "90_94" if battery < 95 else "95_100"
        )
        # Category-specific specifications remain exact; absent values do not stand
        # in for a better configuration. Broad boilerplate parameters are omitted.
        spec_groups = (
            ("ram", ("Оперативная память", "Объем оперативной памяти", "Объём оперативной памяти", "RAM")),
            ("cpu", ("Процессор", "CPU")), ("gpu", ("Видеокарта", "GPU")),
            ("screen", ("Диагональ экрана", "Экран")),
            ("storage_type", ("Тип накопителя",)),
            ("storage", ("Объем накопителя", "Объём накопителя", "Накопитель", "SSD", "HDD")),
            ("drive", ("Тип привода",)), ("edition", ("Версия",)),
            ("load", ("Максимальная загрузка", "Загрузка")),
            ("width", ("Ширина",)), ("depth", ("Глубина",)), ("height", ("Высота",)),
            ("dimensions", ("Размеры", "Габариты")), ("color", ("Цвет",)),
            ("material", ("Материал",)), ("power", ("Мощность",)),
            ("voltage", ("Напряжение аккумулятора",)), ("year", ("Год выпуска", "Поколение")),
            ("size", ("Размер", "Размер одежды", "Диаметр")),
            ("compatibility", ("Применимость",)), ("part_number", ("Артикул", "Номер модели",)),
        )
        # Alias names are equivalent; contradictory supplied aliases stay separate.
        # Missing optional attributes (for example color) remain optional for both
        # offers, but a known differing variant must not support the same discount.
        specs = []
        for field, names in spec_groups:
            values = {
                canonical_evidence("storage", value) if field in {"ram", "storage"} else canonical_text(value)
                for name in names if (value := self.listing.parameter(name))
            }
            specs.append(f"{field}:" + "|".join(sorted(values)))
        explicit_drive_types = {name.casefold() for name in ("SSD", "HDD") if self.listing.parameter(name)}
        drive_type = canonical_text(self.listing.parameter("Тип накопителя"))
        if drive_type:
            explicit_drive_types.add(drive_type)
        return (
            self.market_lane(),
            effective_model_identity(self.listing, self.ai_review.identified_model),
            canonical_evidence("storage", storage_with_source_unit(self.listing, self.ai_review.storage) or self.listing.storage),
            canonical_evidence("sim", self.ai_review.sim_variant or self.listing.sim_variant),
            canonical_evidence("condition", self.ai_review.condition or self.listing.condition),
            clean(self.listing.activation),
            battery_band if self.market_lane() in {"used", "refurbished"} else "new",
            "unknown" if self._history_unknown(self.listing.repair_status) else clean(self.listing.repair_status),
            "unknown" if self._history_unknown(self.listing.parts_status) else clean(self.listing.parts_status),
            canonical_kit_evidence(self.listing.completeness) or "unknown",
            clean(self.listing.location) or "unknown",
            "pickup" if pickup_only else clean(self.listing.delivery) or "unknown",
            self.listing.currency.upper(),
            self.listing.price_basis,
            *specs,
            "drive_technology:" + "|".join(sorted(explicit_drive_types)),
        )


@dataclass(frozen=True, slots=True)
class Recommendation:
    role: RecommendationRole
    analyzed: AnalyzedListing
    reasons: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    market_median: int | None = None
    saving_amount: int | None = None
    saving_percent: float | None = None
    comparable_count: int = 0
    comparable_seller_count: int = 0
    below_market: bool = False
    below_comparables: bool = False
    market_min: int | None = None
    market_max: int | None = None
    market_confidence: str = "insufficient"
    market_evidence: tuple[dict[str, Any], ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "listing": self.analyzed.listing.public_dict(),
            "analysis": self.analyzed.ai_review.public_dict(self.analyzed.listing),
            "ruleFindings": [item.public_dict() for item in self.analyzed.deterministic_risks],
            "reasons": list(self.reasons),
            "risks": list(self.risks),
            "historyWarnings": list(self.analyzed.history_warnings()),
            "marketMedian": self.market_median,
            "savingAmount": self.saving_amount,
            "savingPercent": self.saving_percent,
            "comparableCount": self.comparable_count,
            "comparableSellerCount": self.comparable_seller_count,
            "belowMarket": self.below_market,
            "belowComparables": self.below_comparables,
            "marketRange": {"min": self.market_min, "max": self.market_max},
            "marketConfidence": self.market_confidence,
            "marketEvidence": [dict(item) for item in self.market_evidence],
        }


@dataclass(frozen=True, slots=True)
class DiscoveredListing:
    analyzed: AnalyzedListing
    discovery_status: str = "needs_review"
    reasons: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        # A source finding is never promoted by inheriting market evidence.
        payload = Recommendation(
            role=RecommendationRole.CAUTION, analyzed=self.analyzed,
            reasons=self.reasons, risks=self.risks,
        ).public_dict()
        payload["discoveryStatus"] = self.discovery_status
        return payload


@dataclass(frozen=True, slots=True)
class CollectionBatch:
    items: tuple[dict[str, Any], ...]
    apify_cost_usd: float = 0.0
    apify_cost_estimated: bool = True
    requested_count: int = 0
    capped_count: int = 0
    fill_metrics: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CostSummary:
    apify_cost_usd: float = 0.0
    apify_cost_estimated: bool = True
    ai_cost_rub: float = 0.0
    estimated_total_rub: float = 0.0
    budget_rub: float = 50.0
    within_budget: bool = True
    ai_reviewed_count: int = 0
    ai_text_reviewed_count: int = 0
    ai_photo_reviewed_count: int = 0
    ai_cached_count: int = 0
    ai_skipped_count: int = 0
    suggested_min_price_rub: int = 199
    ai_cost_estimated: bool = False

    def admin_dict(self) -> dict[str, Any]:
        return {
            "apifyCostUsd": round(self.apify_cost_usd, 4),
            "apifyCostEstimated": self.apify_cost_estimated,
            "aiCostRub": round(self.ai_cost_rub, 2),
            "aiCostEstimated": self.ai_cost_estimated,
            "estimatedTotalRub": round(self.estimated_total_rub, 2),
            "budgetRub": round(self.budget_rub, 2),
            "withinBudget": self.within_budget,
            "aiReviewedCount": self.ai_reviewed_count,
            "aiTextReviewedCount": self.ai_text_reviewed_count,
            "aiPhotoReviewedCount": self.ai_photo_reviewed_count,
            "aiCachedCount": self.ai_cached_count,
            "aiSkippedCount": self.ai_skipped_count,
            "suggestedMinPriceRub": self.suggested_min_price_rub,
        }


@dataclass(frozen=True, slots=True)
class PipelineAudit:
    collected_count: int = 0
    raw_collected_count: int = 0
    deduplicated_count: int = 0
    correct_city_count: int = 0
    request_family_matched_count: int = 0
    valid_target_count: int = 0
    requested_target: int = 0
    target_filled: bool = False
    collection_stop_reason: str = ""
    basic_filtered_count: int = 0
    deterministic_eligible_count: int = 0
    text_attempted_count: int = 0
    text_completed_count: int = 0
    text_matched_count: int = 0
    photo_attempted_count: int = 0
    photo_completed_count: int = 0
    high_confidence_count: int = 0
    final_visible_count: int = 0
    elapsed_seconds: float = 0.0
    deadline_reached: bool = False
    rejection_reasons: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    text_routing: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    bargain_funnel: dict[str, Any] = field(default_factory=dict)
    bargain_decisions: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def public_dict(self) -> dict[str, Any]:
        return {
            "rawReceived": self.raw_collected_count,
            "uniqueReceived": self.deduplicated_count,
            "rawCollected": self.raw_collected_count,
            "deduplicated": self.deduplicated_count,
            "correctCity": self.correct_city_count,
            "requestFamilyMatched": self.request_family_matched_count,
            "validTargetCount": self.valid_target_count,
            "requestedTarget": self.requested_target,
            "targetFilled": self.target_filled,
            "collectionStopReason": self.collection_stop_reason,
            "textChecked": self.text_completed_count,
            "photoChecked": self.photo_completed_count,
            "finalChecked": self.final_visible_count,
            "elapsedSeconds": round(self.elapsed_seconds, 1),
            "deadlineReached": self.deadline_reached,
            "rejectionReasons": list(self.rejection_reasons),
        }

    def admin_dict(self) -> dict[str, Any]:
        return {
            "collected": self.collected_count,
            "rawReceived": self.raw_collected_count,
            "uniqueReceived": self.deduplicated_count,
            "rawCollected": self.raw_collected_count,
            "deduplicated": self.deduplicated_count,
            "correctCity": self.correct_city_count,
            "requestFamilyMatched": self.request_family_matched_count,
            "validTargetCount": self.valid_target_count,
            "requestedTarget": self.requested_target,
            "targetFilled": self.target_filled,
            "collectionStopReason": self.collection_stop_reason,
            "basicFilters": self.basic_filtered_count,
            "deterministicEligible": self.deterministic_eligible_count,
            "textAttempted": self.text_attempted_count,
            "textCompleted": self.text_completed_count,
            "textMatched": self.text_matched_count,
            "photoAttempted": self.photo_attempted_count,
            "photoCompleted": self.photo_completed_count,
            "highConfidence": self.high_confidence_count,
            "confirmed": self.final_visible_count,
            "finalVisible": self.final_visible_count,
            "elapsedSeconds": round(self.elapsed_seconds, 2),
            "deadlineReached": self.deadline_reached,
            "rejectionReasons": list(self.rejection_reasons),
            "textRouting": list(self.text_routing),
            "bargainFunnel": dict(self.bargain_funnel),
            "bargainDecisions": [dict(item) for item in self.bargain_decisions],
        }


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    query: str
    location: str
    collected_count: int
    analyzed_count: int
    mode: str = "bargain"
    priority: str = "balanced"
    result_limit: int = 10
    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    discovered_listings: tuple[DiscoveredListing, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    admin_warnings: tuple[str, ...] = field(default_factory=tuple)
    costs: CostSummary = field(default_factory=CostSummary)
    pipeline: PipelineAudit = field(default_factory=PipelineAudit)
    request: SearchRequest | None = None
    empty_reason: str = ""
    outcome: str = ""

    @staticmethod
    def _mix_recommendations(
        visible: list[Recommendation],
        *,
        limit: int | None = None,
    ) -> tuple[Recommendation, ...]:
        if not visible:
            return ()
        # Preserve the actual best-ranked result. Seller mixing must diversify the
        # remainder, never remove a private TOP card when the client asks for three.
        first = visible[0]
        remaining = visible[1:]
        companies = [item for item in remaining if item.analyzed.listing.seller.kind == "company"]
        private = [item for item in remaining if item.analyzed.listing.seller.kind == "private"]
        mixed: list[Recommendation] = [first]
        company_streak = 1 if first.analyzed.listing.seller.kind == "company" else 0
        while companies or private:
            if companies and (company_streak < 3 or not private):
                mixed.append(companies.pop(0))
                company_streak += 1
            elif private:
                mixed.append(private.pop(0))
                company_streak = 0
        return tuple(mixed[:limit] if limit is not None else mixed)

    def _public_recommendations(self) -> tuple[Recommendation, ...]:
        from .ranking import is_customer_safe

        request = self.request or SearchRequest(query=self.query, location=self.location, mode=self.mode)
        visible = [
            item for item in self.recommendations
            if item.role in {
                RecommendationRole.TOP,
                RecommendationRole.BACKUP,
                RecommendationRole.BUDGET,
            }
            and item.analyzed.ai_review.is_complete_for(item.analyzed.listing)
            and item.analyzed.listing.is_freshly_verified()
            and item.analyzed.condition_evidence_complete()
            and item.analyzed.listing.acquisition_price is not None
            and is_customer_safe(item.analyzed, request)
            and (self.mode != "bargain" or item.analyzed.configuration_evidence_complete())
        ]
        return self._mix_recommendations(visible, limit=self.result_limit)

    def _admin_recommendations(self) -> tuple[Recommendation, ...]:
        visible = [item for item in self.recommendations if item.role is not RecommendationRole.REJECTED]
        visible.sort(
            key=lambda item: (
                not item.analyzed.ai_review.is_complete_for(item.analyzed.listing),
                item.analyzed.listing.price or 10**18,
            )
        )
        return self._mix_recommendations(visible, limit=10)

    def public_dict(self, *, include_admin: bool = False) -> dict[str, Any]:
        recommendations = self._public_recommendations()
        outcome = "SUCCESS" if recommendations else (self.outcome or "EMPTY_VERIFIED")
        payload = {
            "outcome": outcome,
            "resultPolicy": "verified_exact_matches_with_optional_savings" if self.mode == "bargain" else "verified_matches_only",
            "query": self.query,
            "location": self.location,
            "mode": self.mode,
            "priority": self.priority,
            "requestedResults": self.result_limit,
            "collectedCount": self.collected_count,
            "analyzedCount": self.analyzed_count,
            "recommendations": [item.public_dict() for item in recommendations],
            "discoveredListings": [],
            "verifiedCount": len(recommendations),
            "visibleCount": len(recommendations),
            "emptyReason": self.empty_reason if not recommendations else "",
            "warnings": list(self.warnings),
            "verification": self.pipeline.public_dict(),
        }
        if include_admin:
            payload["adminRecommendations"] = [item.public_dict() for item in self._admin_recommendations()]
            payload["adminDiscoveredListings"] = [item.public_dict() for item in self.discovered_listings]
            payload["adminWarnings"] = list(self.admin_warnings)
            payload["adminCosts"] = self.costs.admin_dict()
            payload["adminAudit"] = self.pipeline.admin_dict()
        return payload
