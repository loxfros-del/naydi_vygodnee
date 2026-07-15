"""Bounded V2 query planner that rejects lost hard constraints before I/O."""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Iterable, Sequence

from .models import QueryPlan, QueryTier, SearchRequestV2, SourceQuery, SourceStatus
from .request_normalizer import spec_token


DISCOVERY_SOURCES = ("yandex_market", "ozon", "avito", "wildberries")
RELIABLE_ANCHORS = ("dns", "citilink", "mvideo", "official_store")
GENERIC_FALLBACKS = ("generic_search",)
DEFAULT_SOURCES = DISCOVERY_SOURCES + RELIABLE_ANCHORS + GENERIC_FALLBACKS

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
}


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
        fragments.append(condition_value)
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


def _query_text(request: SearchRequestV2, tier: QueryTier) -> tuple[str, list[str]]:
    parts = _required_fragments(request)
    dropped: list[str] = []
    if tier is QueryTier.STRICT:
        if request.budget:
            parts.append(f"до {request.budget}")
        if request.city:
            parts.append(request.city)
    else:
        if request.budget:
            dropped.append(f"до {request.budget}")
        if request.city:
            dropped.append(request.city)
        if tier is QueryTier.SOURCE_EXACT:
            parts.append("купить")
    return _clean(" ".join(_dedupe_fragments(parts))), dropped


def make_source_query(
    request: SearchRequestV2,
    source: str,
    tier: QueryTier,
    *,
    priority: int,
    query: str | None = None,
) -> SourceQuery:
    """Build and validate one query; invalid output is safe to reject pre-network."""
    generated, dropped = _query_text(request, tier)
    return validate_source_query(SourceQuery(
        query=_clean(query if query is not None else generated),
        source=normalize_source_name(source),
        tier=tier,
        priority=priority,
        hard_tokens_required=list(request.hard_tokens),
        dropped_soft_tokens=dropped,
    ))


def _tiers_for_source(source: str, *, include_over_budget: bool) -> tuple[QueryTier, ...]:
    if include_over_budget:
        return (QueryTier.STRICT, QueryTier.OVER_BUDGET_EXACT)
    if source in DISCOVERY_SOURCES or source in GENERIC_FALLBACKS:
        return (QueryTier.STRICT, QueryTier.SOURCE_EXACT)
    return (QueryTier.STRICT, QueryTier.EXACT_RELAXED)


def build_query_plan(
    request: SearchRequestV2,
    *,
    sources: Iterable[str] | None = None,
    max_queries_per_source: int = 2,
    include_over_budget: bool = False,
) -> QueryPlan:
    """Plan at most two exact queries/source and isolate every rejected query."""
    per_source_limit = max(1, min(int(max_queries_per_source), 2))
    source_names = _dedupe_fragments(
        normalize_source_name(source) for source in (sources or DEFAULT_SOURCES)
    )
    accepted: list[SourceQuery] = []
    rejected: list[SourceQuery] = []
    for source in source_names:
        seen: set[str] = set()
        for offset, tier in enumerate(_tiers_for_source(source, include_over_budget=include_over_budget)):
            candidate = make_source_query(
                request, source, tier, priority=100 - offset * 10,
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
    ) -> QueryPlan:
        return build_query_plan(
            request,
            sources=sources,
            max_queries_per_source=self.max_queries_per_source,
            include_over_budget=include_over_budget,
        )


plan_queries = build_query_plan


__all__ = [
    "DEFAULT_SOURCES", "DISCOVERY_SOURCES", "GENERIC_FALLBACKS", "RELIABLE_ANCHORS",
    "QueryPlannerV2", "build_query_plan", "make_source_query", "normalize_source_name",
    "plan_queries", "validate_source_query",
]
