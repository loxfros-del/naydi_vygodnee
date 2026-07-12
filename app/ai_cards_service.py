"""Автоматическое создание ИИ-карточек из результатов автопоиска."""
from __future__ import annotations

import json
import re
from typing import Any

import requests

from app.config import settings
from app.db import Request, SearchResult, to_int_price
from app.price_extractor import format_price
from app.services.ai_review import (
    AI_CARD_DRAFT,
    AI_CARD_ERROR,
    AI_CARD_GENERATED,
)


ALLOWED_AI_ROLES = {"BEST", "BACKUP", "BUDGET", "CAUTION", "REJECTED"}
MAX_AI_CARDS = 3


def _budget_value(req: Request) -> int | None:
    return int(req.budget) if req.budget and req.budget.isdigit() else None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _direct_url(url: str) -> bool:
    return (url or "").strip().startswith(("http://", "https://"))


def _normalize_candidate(item: SearchResult, index: int) -> dict:
    try:
        risk_flags = json.loads(item.risk_flags) if item.risk_flags else []
    except json.JSONDecodeError:
        risk_flags = []
    if not isinstance(risk_flags, list):
        risk_flags = [str(risk_flags)]
    try:
        facts = json.loads(item.facts_json) if getattr(item, "facts_json", "") else {}
    except json.JSONDecodeError:
        facts = {}
    if not isinstance(facts, dict):
        facts = {}
    return {
        "id": item.id,
        "candidate_id": item.id,
        "index": index,
        "title": item.title or "",
        "price": item.price,
        "source": item.source or "",
        "url": item.url or "",
        "facts": facts,
        "snippet": "" if facts else (item.snippet or ""),
        "risk_flags": risk_flags,
        "score": item.score or 0.0,
    }


def _candidate_identity(item: SearchResult) -> tuple[str, str]:
    item_id = int(getattr(item, "id", 0) or 0)
    if item_id:
        return "id", str(item_id)
    url = str(getattr(item, "url", "") or "").strip().casefold().rstrip("/")
    if url:
        return "url", url
    title = " ".join(str(getattr(item, "title", "") or "").casefold().split())
    source = " ".join(str(getattr(item, "source", "") or "").casefold().split())
    return "title", f"{title}|{source}"


def _best_candidates(candidates: list[SearchResult], limit: int = MAX_AI_CARDS) -> list[SearchResult]:
    blocked_statuses = {"REJECTED", "REJECTED_AUTO", "DO_NOT_BUY", "CAUTION"}
    valid = [
        item for item in candidates
        if str(getattr(item, "status", "") or "").upper() not in blocked_statuses
        and str(getattr(item, "origin", "") or "").lower() != "alice"
        and bool(str(getattr(item, "title", "") or "").strip())
    ]
    valid.sort(
        key=lambda item: (
            0 if _direct_url(item.url) else 1,
            0 if item.price else 1,
            -(item.score or 0.0),
            item.id,
        )
    )
    selected: list[SearchResult] = []
    seen: set[tuple[str, str]] = set()
    for item in valid:
        identity = _candidate_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(item)
        if len(selected) >= min(max(limit, 0), MAX_AI_CARDS):
            break
    return selected


def build_ai_cards_prompt(req: Request, candidates: list[SearchResult]) -> str:
    """Строит prompt: модель редактирует текст, но не товарные факты."""
    budget = _budget_value(req)
    selected = [
        _normalize_candidate(item, idx)
        for idx, item in enumerate(_best_candidates(candidates, MAX_AI_CARDS), 1)
    ]
    payload = {
        "request": {
            "product": req.product_name or req.product,
            "use_case": req.use_case or req.purpose,
            "criteria": req.important_criteria or req.criteria,
            "budget": budget,
            "city": req.city,
            "is_used_allowed": bool(req.is_used_allowed),
            "original_query": req.original_query,
        },
        "candidates": selected,
    }
    return (
        "Ты помогаешь админу Telegram-бота подготовить короткий текст к уже выбранным товарам.\n"
        "Верни только строгий JSON без Markdown и без пояснений.\n"
        "Формат ответа:\n"
        '{"cards":[{"candidate_id":123,"why":"","risks":[],"manual_check":[]}]}\n\n'
        "Правила:\n"
        "- верни от 1 до 3 уникальных candidate_id только из candidates;\n"
        "- редактируй только why, risks и manual_check;\n"
        "- не возвращай и не меняй title, price, source, url, role или status;\n"
        "- если у candidate есть facts, объясняй выбор на основе facts, а не длинного snippet;\n"
        "- учитывай facts.budget_status, facts.ps5_flags и facts.warnings;\n"
        "- не скрывай исходные риски кандидата;\n"
        "- карточки короткие и практичные.\n\n"
        "Входные данные:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def call_ai_cards_model(prompt: str) -> str:
    """Вызывает OpenAI-compatible chat/completions API."""
    if not settings.AI_CARDS_ENABLED:
        raise RuntimeError("AI-карточки выключены: AI_CARDS_ENABLED=false.")
    if not settings.AI_API_KEY:
        raise RuntimeError("AI-карточки недоступны: не задан AI_API_KEY.")
    if not settings.AI_BASE_URL:
        raise RuntimeError("AI-карточки недоступны: не задан AI_BASE_URL.")
    if not settings.AI_MODEL:
        raise RuntimeError("AI-карточки недоступны: не задан AI_MODEL.")

    base_url = settings.AI_BASE_URL.rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {settings.AI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.AI_MODEL,
            "messages": [
                {"role": "system", "content": "Отвечай только валидным JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("AI API вернул неожиданный формат ответа.") from exc


def _extract_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def parse_ai_cards_json(text: str, budget: int | None = None) -> list[dict]:
    """Парсит JSON с cards в формат, совместимый с replace_alice_results."""
    data = _extract_json_object(text)
    raw_cards = data.get("cards", [])
    if not isinstance(raw_cards, list):
        return []

    items: list[dict] = []
    for card in raw_cards:
        if not isinstance(card, dict):
            continue
        title = str(card.get("title") or card.get("name") or "").strip()
        if not title:
            continue

        role = str(card.get("role") or "").strip().upper()
        if role not in ALLOWED_AI_ROLES:
            role = ""
        price_num = to_int_price(card.get("price"))
        url = str(card.get("url") or card.get("link") or "").strip()
        if role == "BEST" and budget is not None and price_num and price_num > budget:
            role = "CAUTION"
        if role == "BEST" and not _direct_url(url):
            role = "BACKUP"
        manual_check = _as_list(card.get("manual_check"))
        risks = _as_list(card.get("risks"))
        confidence = str(card.get("confidence") or "").strip().lower()
        item = {
            "name": title,
            "price": format_price(price_num) if price_num else "",
            "price_num": price_num,
            "store": str(card.get("store") or card.get("source") or "").strip(),
            "link": url,
            "pluses": _as_list(card.get("why") or card.get("pluses")),
            "risks": risks,
            "manual_check": manual_check,
            "notes": manual_check,
            "role": role,
            "confidence": confidence,
            "within_budget": price_num <= budget if budget is not None and price_num else None,
        }
        items.append(item)
    return items


def _dedupe_text(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text[:500])
    return result


def _candidate_risks(candidate: SearchResult) -> list[str]:
    risks: list[str] = []
    raw_risks = getattr(candidate, "risk_flags", "") or ""
    try:
        decoded = json.loads(raw_risks) if isinstance(raw_risks, str) else raw_risks
    except json.JSONDecodeError:
        decoded = [raw_risks]
    risks.extend(_as_list(decoded))

    raw_facts = getattr(candidate, "facts_json", "") or ""
    try:
        facts = json.loads(raw_facts) if isinstance(raw_facts, str) and raw_facts else {}
    except json.JSONDecodeError:
        facts = {}
    if isinstance(facts, dict):
        risks.extend(_as_list(facts.get("warnings")))
    return _dedupe_text(risks)


def _editorial_why(value: Any) -> str:
    parts = _as_list(value)
    return "; ".join(parts)[:1000]


def _card_from_candidate(
    req: Request,
    candidate: SearchResult,
    *,
    why: str,
    risks: list[str],
    manual_check: list[str],
    ai_card_status: str,
) -> dict:
    """Восстанавливает все факты и роль только из выбранного SearchResult."""
    price_num = to_int_price(getattr(candidate, "price", None))
    candidate_status = str(getattr(candidate, "status", "") or "CANDIDATE").strip().upper()
    budget = _budget_value(req)
    return {
        "candidate_id": candidate.id,
        "name": str(candidate.title or "").strip(),
        "title": str(candidate.title or "").strip(),
        "price": format_price(price_num) if price_num else "",
        "price_num": price_num,
        "store": str(candidate.source or "").strip(),
        "source": str(candidate.source or "").strip(),
        "link": str(candidate.url or "").strip(),
        "url": str(candidate.url or "").strip(),
        "why": why,
        "pluses": [why] if why else [],
        "risks": _dedupe_text(risks),
        "manual_check": _dedupe_text(manual_check),
        "notes": _dedupe_text(manual_check),
        "role": candidate_status,
        "status": candidate_status,
        "ai_card_status": ai_card_status,
        "image_file_id": str(getattr(candidate, "image_file_id", "") or ""),
        "within_budget": price_num <= budget if budget is not None and price_num else None,
    }


def parse_ai_cards_for_candidates(
    text: str,
    req: Request,
    candidates: list[SearchResult],
) -> list[dict]:
    """Привязывает редакционный ответ модели к доверенным candidate_id."""
    selected = _best_candidates(candidates, MAX_AI_CARDS)
    candidate_by_id = {int(item.id): item for item in selected}
    data = _extract_json_object(text)
    raw_cards = data.get("cards", [])
    if not isinstance(raw_cards, list):
        return []

    cards: list[dict] = []
    seen_ids: set[int] = set()
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            candidate_id = int(str(raw_card.get("candidate_id") or "").strip())
        except (TypeError, ValueError):
            continue
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)

        why = _editorial_why(raw_card.get("why"))
        risks = _dedupe_text(_candidate_risks(candidate) + _as_list(raw_card.get("risks")))
        manual_check = _dedupe_text(_as_list(raw_card.get("manual_check")))
        cards.append(_card_from_candidate(
            req,
            candidate,
            why=why,
            risks=risks,
            manual_check=manual_check,
            ai_card_status=AI_CARD_GENERATED,
        ))
        if len(cards) >= MAX_AI_CARDS:
            break
    return cards


def build_fallback_ai_cards(req: Request, candidates: list[SearchResult]) -> list[dict]:
    """Создаёт сетево-независимые текстовые draft-карточки."""
    cards: list[dict] = []
    for candidate in _best_candidates(candidates, MAX_AI_CARDS):
        why = str(candidate.snippet or "").strip()[:1000]
        if not why:
            why = "Вариант отобран из результатов поиска и ожидает ручной проверки."
        manual_check = ["подтвердить актуальную цену"]
        if _direct_url(candidate.url):
            manual_check.append("проверить ссылку и наличие")
        else:
            manual_check.append("найти прямую ссылку на товар")
        manual_check.append("проверить комплектацию и гарантию")
        cards.append(_card_from_candidate(
            req,
            candidate,
            why=why,
            risks=_candidate_risks(candidate),
            manual_check=manual_check,
            ai_card_status=AI_CARD_DRAFT,
        ))
    return cards


def generate_ai_cards_from_candidates(req: Request, candidates: list[SearchResult]) -> dict:
    """Генерирует ИИ-карточки из кандидатов и возвращает success/message/cards."""
    selected = _best_candidates(candidates, MAX_AI_CARDS)
    if not selected:
        return {
            "success": False,
            "message": "Нет подходящих кандидатов для AI-карточек.",
            "cards": [],
            "generation_status": AI_CARD_ERROR,
            "status": AI_CARD_ERROR,
        }

    prompt = build_ai_cards_prompt(req, selected)
    try:
        raw = call_ai_cards_model(prompt)
        cards = parse_ai_cards_for_candidates(raw, req, selected)
    except Exception as exc:
        cards = build_fallback_ai_cards(req, selected)
        return {
            "success": True,
            "message": "AI недоступен — созданы обычные текстовые черновики для проверки.",
            "cards": cards,
            "raw_response": "",
            "fallback": True,
            "generation_status": AI_CARD_ERROR,
            "status": AI_CARD_ERROR,
            "error": str(exc)[:500],
        }

    if not cards:
        cards = build_fallback_ai_cards(req, selected)
        return {
            "success": True,
            "message": "AI не вернул пригодный ответ — созданы обычные текстовые черновики.",
            "cards": cards,
            "raw_response": raw,
            "fallback": True,
            "generation_status": AI_CARD_ERROR,
            "status": AI_CARD_ERROR,
        }

    return {
        "success": True,
        "message": "AI-карточки созданы и ожидают проверки администратора.",
        "cards": cards,
        "raw_response": raw,
        "fallback": False,
        "generation_status": AI_CARD_GENERATED,
        "status": AI_CARD_GENERATED,
    }
