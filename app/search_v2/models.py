"""Pure, Telegram-independent domain models for Search Engine V2."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ValueEnum(str, Enum):
    """String enum that remains compatible with JSON and legacy string checks."""

    def __str__(self) -> str:
        return self.value


class QueryTier(ValueEnum):
    STRICT = "STRICT"
    EXACT_RELAXED = "EXACT_RELAXED"
    SOURCE_EXACT = "SOURCE_EXACT"
    OVER_BUDGET_EXACT = "OVER_BUDGET_EXACT"


class SourceStatus(ValueEnum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    EMPTY = "EMPTY"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    UNAUTHORIZED = "UNAUTHORIZED"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    INVALID_QUERY_PLAN = "INVALID_QUERY_PLAN"
    ERROR = "ERROR"


class SearchResultStatus(ValueEnum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    NO_EXACT_MATCH = "NO_EXACT_MATCH"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    UNSUPPORTED_CATEGORY = "UNSUPPORTED_CATEGORY"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class ExactMatchResult(ValueEnum):
    EXACT = "EXACT"
    COMPATIBLE_VARIANT = "COMPATIBLE_VARIANT"
    GENERIC_MATCH = "GENERIC_MATCH"
    REQUIRED_SPEC_MISMATCH = "REQUIRED_SPEC_MISMATCH"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    ACCESSORY = "ACCESSORY"
    UNKNOWN = "UNKNOWN"


# Convenient name used by matching/ranking modules.
ExactMatchStatus = ExactMatchResult


class ProductCondition(ValueEnum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    ANY = "any"
    UNKNOWN = "unknown"


class AvailabilityStatus(ValueEnum):
    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PREORDER = "PREORDER"
    UNKNOWN = "UNKNOWN"


class VerificationAccess(ValueEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class SellerTrust(ValueEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class PlatformTrust(ValueEnum):
    HIGH_RETAIL = "HIGH_RETAIL"
    HIGH_MARKETPLACE = "HIGH_MARKETPLACE"
    CLASSIFIED = "CLASSIFIED"
    UNKNOWN = "UNKNOWN"


class PriceClass(ValueEnum):
    VERY_CHEAP = "VERY_CHEAP"
    BELOW_MARKET = "BELOW_MARKET"
    FAIR = "FAIR"
    ABOVE_MARKET = "ABOVE_MARKET"
    VERY_EXPENSIVE = "VERY_EXPENSIVE"
    UNKNOWN = "UNKNOWN"


class RiskSeverity(ValueEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RecommendationRole(ValueEnum):
    BEST_OVERALL = "BEST_OVERALL"
    CHEAP_WITH_RISK = "CHEAP_WITH_RISK"
    RELIABLE = "RELIABLE"


class JsonModel:
    """Small convenience API; implementation lives in ``serialization``."""

    def to_dict(self) -> dict[str, Any]:
        from .serialization import to_jsonable

        result = to_jsonable(self)
        if not isinstance(result, dict):
            raise TypeError("domain model did not serialize to an object")
        return result

    def to_json(self, **kwargs: Any) -> str:
        from .serialization import to_json

        return to_json(self, **kwargs)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SearchRequestV2(JsonModel):
    original_query: str = ""
    category: str = "unknown"
    brand: str = ""
    canonical_model: str = ""
    model_modifiers: list[str] = field(default_factory=list)
    required_specs: dict[str, Any] = field(default_factory=dict)
    optional_specs: dict[str, Any] = field(default_factory=dict)
    budget: int | None = None
    city: str = ""
    condition: ProductCondition = ProductCondition.ANY
    priority: str = "balance"
    supported_category: bool = False
    hard_tokens: list[str] = field(default_factory=list)
    soft_tokens: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceQuery(JsonModel):
    query: str = ""
    source: str = ""
    tier: QueryTier = QueryTier.STRICT
    priority: int = 0
    hard_tokens_required: list[str] = field(default_factory=list)
    hard_tokens_preserved: list[str] = field(default_factory=list)
    dropped_soft_tokens: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    status: SourceStatus = SourceStatus.SUCCESS

    @property
    def is_valid(self) -> bool:
        return self.status is not SourceStatus.INVALID_QUERY_PLAN and not self.rejection_reason


@dataclass(slots=True)
class QueryPlan(JsonModel):
    request: SearchRequestV2 | None = None
    source_queries: list[SourceQuery] = field(default_factory=list)
    rejected_queries: list[SourceQuery] = field(default_factory=list)

    @property
    def queries(self) -> list[SourceQuery]:
        return self.source_queries


@dataclass(slots=True)
class SourceAttempt(JsonModel):
    source: str = ""
    query: str = ""
    tier: QueryTier | None = None
    status: SourceStatus = SourceStatus.EMPTY
    duration_ms: float = 0.0
    error: str = ""
    raw_offer_count: int = 0
    cache_hit: bool = False
    attempt_number: int = 1
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SellerInfo(JsonModel):
    name: str = ""
    seller_id: str = ""
    seller_type: str = "unknown"
    rating: float | None = None
    reviews_count: int | None = None
    trust: SellerTrust = SellerTrust.UNKNOWN
    official: bool | None = None
    warranty: str = ""
    return_policy: str = ""
    city: str = ""
    verified: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PriceInfo(JsonModel):
    current: float | None = None
    old: float | None = None
    currency: str = "RUB"
    valid: bool = False
    confidence: float = 0.0
    observed_at: datetime | None = None
    source_text: str = ""


@dataclass(slots=True)
class AvailabilityInfo(JsonModel):
    status: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    available: bool | None = None
    quantity: int | None = None
    delivery: str = ""
    city: str = ""
    pickup: str = ""
    source_text: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class VerificationState(JsonModel):
    model_verified: bool | None = None
    link_verified: bool | None = None
    price_verified: bool | None = None
    availability_verified: bool | None = None
    seller_verified: bool | None = None
    access: VerificationAccess = VerificationAccess.UNKNOWN
    verified_by: str = ""
    verified_at: datetime | None = None
    note: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RiskFlag(JsonModel):
    code: str = ""
    severity: RiskSeverity = RiskSeverity.WARNING
    title: str = ""
    explanation: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    resolved_by_manual: bool = False
    resolved_at: datetime | None = None
    resolved_by: str = ""


@dataclass(slots=True)
class ProductIdentity(JsonModel):
    category: str = "unknown"
    brand: str = ""
    canonical_model: str = ""
    modifiers: list[str] = field(default_factory=list)
    storage: int | str | None = None
    size: str | None = None
    diagonal: float | str | None = None
    refresh_rate: int | str | None = None
    key_configuration: dict[str, Any] = field(default_factory=dict)
    condition: ProductCondition = ProductCondition.UNKNOWN
    region_or_sim_variant: str = ""
    canonical_key: str = ""
    identity_confidence: float = 0.0


@dataclass(slots=True)
class RawOffer(JsonModel):
    source: str = ""
    platform: str = ""
    title: str = ""
    url: str = ""
    product_id: str = ""
    seller_name: str = ""
    price: float | None = None
    old_price: float | None = None
    currency: str = "RUB"
    availability_text: str = ""
    condition: ProductCondition = ProductCondition.UNKNOWN
    city: str = ""
    delivery: str = ""
    image_url: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Offer(JsonModel):
    offer_id: str = ""
    source: str = ""
    platform: str = ""
    title: str = ""
    url: str = ""
    product_id: str = ""
    seller: SellerInfo = field(default_factory=SellerInfo)
    price: float | None = None
    old_price: float | None = None
    currency: str = "RUB"
    availability: AvailabilityInfo = field(default_factory=AvailabilityInfo)
    condition: ProductCondition = ProductCondition.UNKNOWN
    city: str = ""
    delivery: str = ""
    image_url: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    source_confidence: float = 0.0
    product_confidence: float = 0.0
    price_confidence: float = 0.0
    availability_confidence: float = 0.0
    seller_confidence: float = 0.0
    verification_access: VerificationAccess = VerificationAccess.UNKNOWN
    exact_match: ExactMatchResult = ExactMatchResult.UNKNOWN
    risk_flags: list[RiskFlag] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=utc_now)
    cache_age: float | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    identity: ProductIdentity | None = None
    platform_trust: PlatformTrust = PlatformTrust.UNKNOWN
    automatic_verification: VerificationState = field(default_factory=VerificationState)
    manual_verification: VerificationState = field(default_factory=VerificationState)
    final_verification: VerificationState = field(default_factory=VerificationState)


@dataclass(slots=True)
class MarketStats(JsonModel):
    offer_count: int = 0
    verified_offer_count: int = 0
    minimum: float | None = None
    median: float | None = None
    trimmed_mean: float | None = None
    maximum: float | None = None
    market_range: tuple[float, float] | None = None
    deviation_percent: dict[str, float] = field(default_factory=dict)
    price_classes: dict[str, PriceClass] = field(default_factory=dict)
    currency: str = "RUB"


@dataclass(slots=True)
class ProductGroup(JsonModel):
    group_id: str = ""
    canonical_key: str = ""
    identity: ProductIdentity | None = None
    offers: list[Offer] = field(default_factory=list)
    market_stats: MarketStats | None = None


@dataclass(slots=True)
class Recommendation(JsonModel):
    role: RecommendationRole = RecommendationRole.BEST_OVERALL
    offer_id: str = ""
    offer: Offer | None = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    risks: list[RiskFlag] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SearchMetrics(JsonModel):
    source_success_rate: float = 0.0
    source_timeout_rate: float = 0.0
    blocked_rate: float = 0.0
    raw_offer_count: int = 0
    exact_offer_count: int = 0
    wrong_model_rejection_count: int = 0
    valid_price_rate: float = 0.0
    product_group_count: int = 0
    recommendation_count: int = 0
    top1_exact: bool = False
    top3_useful: int = 0
    duplicate_rate: float = 0.0
    source_diversity: int = 0
    duration_ms: float = 0.0
    cache_hit_count: int = 0
    manual_review_required: bool = False
    admin_review_time_ms: float | None = None


@dataclass(slots=True)
class SearchResultV2(JsonModel):
    normalized_request: SearchRequestV2 | None = None
    query_plan: QueryPlan | None = None
    source_attempts: list[SourceAttempt] = field(default_factory=list)
    raw_offer_count: int = 0
    normalized_offers: list[Offer] = field(default_factory=list)
    rejected_offers: list[Offer] = field(default_factory=list)
    product_groups: list[ProductGroup] = field(default_factory=list)
    market_stats: list[MarketStats] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    manual_candidates: list[Offer] = field(default_factory=list)
    metrics: SearchMetrics = field(default_factory=SearchMetrics)
    duration: float = 0.0
    status: SearchResultStatus = SearchResultStatus.ERROR
    errors: list[str] = field(default_factory=list)


__all__ = [
    "AvailabilityInfo", "AvailabilityStatus", "ExactMatchResult", "ExactMatchStatus",
    "MarketStats", "Offer", "PlatformTrust", "PriceClass", "PriceInfo",
    "ProductCondition", "ProductGroup", "ProductIdentity", "QueryPlan", "QueryTier",
    "RawOffer", "Recommendation", "RecommendationRole", "RiskFlag", "RiskSeverity",
    "SearchMetrics", "SearchRequestV2", "SearchResultStatus", "SearchResultV2",
    "SellerInfo", "SellerTrust", "SourceAttempt", "SourceQuery", "SourceStatus",
    "VerificationAccess", "VerificationState", "utc_now",
]
