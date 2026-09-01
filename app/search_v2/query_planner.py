"""Bounded V2 query planner that rejects lost hard constraints before I/O."""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Any, Iterable, Mapping, Sequence

from app.category_registry import get_category_spec

from .models import QueryPlan, QueryTier, SearchRequestV2, SourceQuery, SourceStatus
from .request_normalizer import spec_token


DISCOVERY_SOURCES = ("yandex_market", "ozon", "avito", "wildberries")
RELIABLE_ANCHORS = ("dns", "citilink", "mvideo", "official_store")
EXTERNAL_WEB_DISCOVERY_SOURCES = ("yandex_web",)
GENERIC_FALLBACKS = ("generic_search",)
DEFAULT_SOURCES = DISCOVERY_SOURCES + RELIABLE_ANCHORS + EXTERNAL_WEB_DISCOVERY_SOURCES + GENERIC_FALLBACKS

_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^0-9a-zа-яё+]+", re.IGNORECASE)
_SOURCE_ALIASES = {
    "яндекс маркет": "yandex_market", "yandex market": "yandex_market",
    "market": "yandex_market", "ozon": "ozon", "озон": "ozon",
    "avito": "avito", "авито": "avito", "wildberries": "wildberries",
    "wb": "wildberries", "dns": "dns", "днс": "dns",
    "ситилинк": "citilink", "citilink": "citilink",
    "мвидео": "mvideo", "м.видео": "mvideo", "mvideo": "mvideo",
    "generic": "generic_search", "generic_search": "generic_search",
    "yandex_web": "yandex_web", "yandex_search": "yandex_web",
}
_QUERY_CONDITION_WORDS = {
    "new": "новый",
    "used": "б/у",
    "refurbished": "восстановленный",
}
_TEMPLATE_STATIC_CONSTRAINTS = (
    # A category template is allowed to name a specialised configuration only
    # when that configuration is already an explicit required parameter.
    # This prevents a generic coffee-machine request from silently becoming an
    # "automatic" search or a wired-headphone request from becoming wireless.
    ("автоматическ", (("automatic", ("true",)), ("machine_type", ("automatic", "auto")))),
    ("рожков", (("machine_type", ("espresso", "рожков")),)),
    ("капсуль", (("machine_type", ("capsule", "капсуль")),)),
    ("milk system", (("cappuccinator", ("true",)), ("milk_system", ("milk", "молоч")))),
    ("ips", (("ips", ("true",)), ("panel", ("ips",)))),
    ("qhd", (("resolution", ("qhd", "2k", "1440p")),)),
    ("беспровод", (("wireless", ("true",)), ("connection", ("wireless", "bluetooth", "tws", "беспровод")))),
    ("лидар", (("lidar", ("true",)), ("navigation", ("lidar", "лидар")))),
    ("вертикальн", (("type", ("vertical", "stick", "вертикаль")),)),
    ("независимые пружины", (("spring_type", ("independent", "независим")),)),
    ("подъемным механизмом", (("lift_mechanism", ("true",)),)),
)


def _clean(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip(" ,")


def normalize_source_name(value: object) -> str:
    cleaned = _clean(value).casefold().replace("-", "_")
    return _SOURCE_ALIASES.get(cleaned, cleaned.replace(" ", "_"))


def _search_form(value: object) -> str:
    text = _clean(value).casefold().replace("ё", "е")
    text = text.replace("×", "x").replace("х", "x")
    text = re.sub(r"\bgb\b", "гб", text)
    text = re.sub(r"\bhz\b", "гц", text)
    text = re.sub(r"\bнов(?:ый|ая|ое|ые)\b", "new", text)
    text = re.sub(r"\b(?:б\s*/?\s*у|бу|подержан\w*)\b", "used", text)
    text = _WORD_RE.sub(" ", text)
    return f" {_SPACE_RE.sub(' ', text).strip()} "


def _contains_token(query: str, token: str) -> bool:
    query_form = _search_form(query)
    token_form = _search_form(token).strip()
    if not token_form:
        return True
    return f" {token_form} " in query_form


def _modifier_conflict(query: str, required: Sequence[str]) -> str:
    query_form = _search_form(query)
    required_forms = {_search_form(token).strip() for token in required}
    if "pro" in required_forms and "pro max" not in required_forms and " pro max " in query_form:
        return "incompatible modifier: Pro Max replaces required Pro"
    if "new" in required_forms and " used " in query_form:
        return "incompatible condition: used replaces required new"
    if "used" in required_forms and " new " in query_form:
        return "incompatible condition: new replaces required used"
    return ""


def validate_source_query(source_query: SourceQuery) -> SourceQuery:
    """Return an accepted or INVALID_QUERY_PLAN query without doing network I/O."""
    preserved = [
        token for token in source_query.hard_tokens_required
        if _contains_token(source_query.query, token)
    ]
    missing = [token for token in source_query.hard_tokens_required if token not in preserved]
    conflict = _modifier_conflict(source_query.query, source_query.hard_tokens_required)
    if not _clean(source_query.query):
        reason = "empty product query"
    elif missing:
        reason = f"missing hard tokens: {', '.join(missing)}"
    else:
        reason = conflict
    return replace(
        source_query,
        hard_tokens_preserved=preserved,
        rejection_reason=reason,
        status=SourceStatus.INVALID_QUERY_PLAN if reason else SourceStatus.SUCCESS,
    )


def _required_fragments(request: SearchRequestV2) -> list[str]:
    fragments: list[str] = []
    if request.brand:
        fragments.append(request.brand)
    if request.canonical_model:
        fragments.append(request.canonical_model)
    elif request.category and request.category != "unknown":
        fragments.append(request.category.replace("_", " "))
    fragments.extend(request.model_modifiers)
    fragments.extend(spec_token(key, value) for key, value in request.required_specs.items())
    condition_value = getattr(request.condition, "value", str(request.condition)).casefold()
    if condition_value not in {"any", "unknown", ""}:
        fragments.append(_QUERY_CONDITION_WORDS.get(condition_value, condition_value))
    return _dedupe_fragments(fragments)


def _dedupe_fragments(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean(value)
        normalized = _search_form(cleaned).strip()
        if not cleaned or normalized in seen:
            continue
        seen.add(normalized)
        result.append(cleaned)
    return result


def _query_text(
    request: SearchRequestV2,
    tier: QueryTier,
    *,
    source: str,
    capabilities: Any = None,
) -> tuple[str, list[str]]:
    parts = _required_fragments(request)
    dropped: list[str] = []
    if tier is QueryTier.STRICT:
        if request.budget:
            parts.append(f"до {request.budget}")
        if request.city:
            # Retail and web adapters do not receive a region argument.  A
            # city appended to their text query silently narrows the national
            # catalogue and hides cheaper delivery offers.  Location belongs
            # only to Avito's text search until an adapter exposes a real
            # region parameter.
            supports_city = bool(getattr(capabilities, "supports_city", source == "avito"))
            structured_city = supports_city and bool(getattr(capabilities, "structured_endpoint", False))
            if supports_city and not structured_city:
                parts.append(request.city)
            elif not structured_city:
                dropped.append(request.city)
    else:
        if request.budget:
            dropped.append(f"до {request.budget}")
        if request.city:
            dropped.append(request.city)
        if tier is QueryTier.SOURCE_EXACT:
            parts.append("купить")
    return _clean(" ".join(_dedupe_fragments(parts))), dropped


def _template_requires_unconfirmed_configuration(template: str, request: SearchRequestV2) -> bool:
    """Reject a category phrase whose specialised configuration is unconfirmed.

    Templates contain presentation vocabulary such as ``автоматическая`` or
    ``IPS``.  Those words are safe only when the request already selects a
    compatible value for that dimension.  Missing/unknown values deliberately
    do not turn the template's claim into a new requirement.
    """
    static = _search_form(re.sub(r"\{[a-z_]+\}", " ", template)).strip()
    if not static:
        return False
    for marker, alternatives in _TEMPLATE_STATIC_CONSTRAINTS:
        if marker not in static:
            continue
        for key, compatible_values in alternatives:
            value = request.required_specs.get(key)
            if value in (None, "", False):
                continue
            normalized = _search_form(value).strip()
            if any(option in normalized for option in compatible_values):
                break
        else:
            return True
    return False


def _category_discovery_queries(
    request: SearchRequestV2,
    *,
    source: str,
    capabilities: Any = None,
) -> Iterable[str]:
    """Yield narrow category context queries without relaxing any hard token.

    The secondary slot is intentionally available only to known discovery
    sources.  Anchors and generic/web fallback keep their established exact
    query shape.  Unsupported categories also keep the old behaviour.
    """
    if not request.supported_category or request.category in {"", "unknown"}:
        return ()
    spec = get_category_spec(request.category)
    base_query, _ = _query_text(
        request,
        QueryTier.SOURCE_EXACT,
        source=source,
        capabilities=capabilities,
    )
    result: list[str] = []
    for template in spec.query_variants:
        if _template_requires_unconfirmed_configuration(template, request):
            continue
        # The base exact query already contains all placeholder values.  Only
        # use the literal category vocabulary as an additive discovery hint;
        # this prevents noisy repetitions such as a model name appearing
        # twice in the same marketplace request.
        context = _clean(re.sub(r"\{[a-z_]+\}", " ", template))
        if not context:
            continue
        if _contains_token(base_query, context):
            # The templates are ordered from broad category wording to more
            # opinionated alternatives.  Once the broad wording is already
            # present, do not fall through to a narrower phrase such as
            # ``smart tv`` or ``эргономичное``.
            return ()
        # The established exact query supplies all user constraints in their
        # source-friendly spelling (for example ``новый`` rather than the
        # canonical internal token ``new``).  Category words can only extend
        # that query, never replace a required fragment.
        query = _clean(f"{base_query} {context}")
        if query:
            result.append(query)
    return tuple(_dedupe_fragments(result))


def make_source_query(
    request: SearchRequestV2,
    source: str,
    tier: QueryTier,
    *,
    priority: int,
    query: str | None = None,
    capabilities: Any = None,
) -> SourceQuery:
    """Build and validate one query; invalid output is safe to reject pre-network."""
    normalized_source = normalize_source_name(source)
    generated, dropped = _query_text(
        request,
        tier,
        source=normalized_source,
        capabilities=capabilities,
    )
    structured_filters: dict[str, Any] = {}
    if capabilities is not None and bool(getattr(capabilities, "structured_endpoint", False)):
        if request.city and bool(getattr(capabilities, "supports_city", False)):
            structured_filters["city"] = request.city
        if request.condition.value not in {"", "any", "unknown"} and bool(
            getattr(capabilities, "supports_condition", False)
        ):
            structured_filters["condition"] = request.condition.value
        if request.category not in {"", "unknown"} and bool(
            getattr(capabilities, "supports_category_filter", False)
        ):
            structured_filters["category"] = request.category
        if request.budget and bool(getattr(capabilities, "supports_price_filter", False)):
            structured_filters["max_price"] = request.budget
    return validate_source_query(SourceQuery(
        query=_clean(query if query is not None else generated),
        source=normalized_source,
        tier=tier,
        priority=priority,
        hard_tokens_required=list(request.hard_tokens),
        dropped_soft_tokens=dropped,
        structured_filters=structured_filters,
    ))


def _tiers_for_source(source: str, *, include_over_budget: bool) -> tuple[QueryTier, ...]:
    if include_over_budget:
        return (QueryTier.STRICT, QueryTier.OVER_BUDGET_EXACT)
    if source in DISCOVERY_SOURCES or source in EXTERNAL_WEB_DISCOVERY_SOURCES or source in GENERIC_FALLBACKS:
        return (QueryTier.STRICT, QueryTier.SOURCE_EXACT)
    return (QueryTier.STRICT, QueryTier.EXACT_RELAXED)


def build_query_plan(
    request: SearchRequestV2,
    *,
    sources: Iterable[str] | None = None,
    max_queries_per_source: int = 2,
    include_over_budget: bool = False,
    source_capabilities: Mapping[str, Any] | None = None,
) -> QueryPlan:
    """Plan at most two exact queries/source and isolate every rejected query."""
    per_source_limit = max(1, min(int(max_queries_per_source), 2))
    source_names = _dedupe_fragments(
        normalize_source_name(source) for source in (sources or DEFAULT_SOURCES)
    )
    accepted: list[SourceQuery] = []
    rejected: list[SourceQuery] = []
    for source in source_names:
        capabilities = (source_capabilities or {}).get(source)
        seen: set[str] = set()
        for offset, tier in enumerate(_tiers_for_source(source, include_over_budget=include_over_budget)):
            priority = 100 - offset * 10
            # There are never more than two queries per source.  For a known
            # category, use the existing secondary discovery slot for one
            # category-aware query.  It remains SOURCE_EXACT and must pass the
            # same hard-token validator as every other planned query.
            if (
                tier is QueryTier.SOURCE_EXACT
                and source in DISCOVERY_SOURCES
                and per_source_limit > 1
            ):
                category_candidate: SourceQuery | None = None
                for query in _category_discovery_queries(
                    request,
                    source=source,
                    capabilities=capabilities,
                ):
                    candidate = make_source_query(
                        request,
                        source,
                        tier,
                        priority=priority,
                        query=query,
                        capabilities=capabilities,
                    )
                    query_key = _search_form(candidate.query)
                    if query_key in seen:
                        continue
                    if candidate.is_valid:
                        category_candidate = candidate
                        seen.add(query_key)
                        break
                    rejected.append(candidate)
                if category_candidate is not None:
                    accepted.append(category_candidate)
                    if len([item for item in accepted if item.source == source]) >= per_source_limit:
                        break
                    continue

            candidate = make_source_query(
                request,
                source,
                tier,
                priority=priority,
                capabilities=capabilities,
            )
            query_key = _search_form(candidate.query)
            if query_key in seen:
                continue
            seen.add(query_key)
            if candidate.is_valid:
                accepted.append(candidate)
            else:
                rejected.append(candidate)
            if len([item for item in accepted if item.source == source]) >= per_source_limit:
                break
    accepted.sort(key=lambda item: (-item.priority, item.source, item.query.casefold()))
    return QueryPlan(request=request, source_queries=accepted, rejected_queries=rejected)


class QueryPlannerV2:
    """Configurable facade used by orchestration without exposing planner internals."""

    def __init__(self, *, max_queries_per_source: int = 2) -> None:
        self.max_queries_per_source = max(1, min(int(max_queries_per_source), 2))

    def plan(
        self,
        request: SearchRequestV2,
        *,
        sources: Iterable[str] | None = None,
        include_over_budget: bool = False,
        source_capabilities: Mapping[str, Any] | None = None,
    ) -> QueryPlan:
        return build_query_plan(
            request,
            sources=sources,
            max_queries_per_source=self.max_queries_per_source,
            include_over_budget=include_over_budget,
            source_capabilities=source_capabilities,
        )


plan_queries = build_query_plan


__all__ = [
    "DEFAULT_SOURCES", "DISCOVERY_SOURCES", "EXTERNAL_WEB_DISCOVERY_SOURCES", "GENERIC_FALLBACKS", "RELIABLE_ANCHORS",
    "QueryPlannerV2", "build_query_plan", "make_source_query", "normalize_source_name",
    "plan_queries", "validate_source_query",
]
