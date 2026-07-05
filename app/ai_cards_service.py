"""Автоматическое создание ИИ-карточек из результатов автопоиска."""
from __future__ import annotations

import json
import re
from typing import Any

import requests

from app.config import settings
from app.db import Request, SearchResult, to_int_price
from app.price_extractor import format_price


ALLOWED_AI_ROLES = {"BEST", "BACKUP", "BUDGET", "CAUTION", "REJECTED"}


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


def _best_candidates(candidates: list[SearchResult], limit: int = 20) -> list[SearchResult]:
    valid = [item for item in candidates if item.status != "REJECTED_AUTO" and item.title]
    valid.sort(
        key=lambda item: (
            0 if _direct_url(item.url) else 1,
            0 if item.price else 1,
            -(item.score or 0.0),
            item.id,
        )
    )
    return valid[:limit]


def build_ai_cards_prompt(req: Request, candidates: list[SearchResult]) -> str:
    """Строит строгий prompt для превращения кандидатов в 3-5 карточек."""
    budget = _budget_value(req)
    selected = [_normalize_candidate(item, idx) for idx, item in enumerate(_best_candidates(candidates), 1)]
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
        "Ты помогаешь админу Telegram-бота выбрать товары из уже найденных кандидатов.\n"
        "Верни только строгий JSON без Markdown и без пояснений.\n"
        "Формат ответа:\n"
        '{"cards":[{"role":"BEST","title":"","price":0,"store":"","url":"","why":"","risks":[],"manual_check":[],"confidence":"medium"}]}\n\n'
        "Допустимые role: BEST, BACKUP, BUDGET, CAUTION, REJECTED.\n"
        "Правила:\n"
        "- не выдумывай ссылки, используй url только из candidates;\n"
        "- не выдумывай цены, используй price только из candidates;\n"
        "- если у candidate есть facts, объясняй выбор на основе facts, а не длинного snippet;\n"
        "- учитывай facts.budget_status, facts.ps5_flags и facts.warnings;\n"
        "- если цена выше бюджета, не ставь BEST;\n"
        "- если ссылки нет или она подозрительная, роль не выше BACKUP;\n"
        "- выбери максимум 3-5 карточек для проверки админом;\n"
        "- REJECTED ставь только явному мусору, не показываемому клиенту;\n"
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


def generate_ai_cards_from_candidates(req: Request, candidates: list[SearchResult]) -> dict:
    """Генерирует ИИ-карточки из кандидатов и возвращает success/message/cards."""
    selected = _best_candidates(candidates)
    if len(selected) < 3:
        return {
            "success": False,
            "message": "Мало кандидатов для ИИ-карточек. Сначала запустите автопоиск.",
            "cards": [],
        }

    prompt = build_ai_cards_prompt(req, selected)
    try:
        raw = call_ai_cards_model(prompt)
        cards = parse_ai_cards_json(raw, budget=_budget_value(req))
    except Exception as exc:
        return {
            "success": False,
            "message": f"Не удалось сделать ИИ-карточки: {exc}. Можно использовать ручной сценарий Алисы/GigaChat.",
            "cards": [],
        }

    if not cards:
        return {
            "success": False,
            "message": "AI API не вернул карточки. Используйте ручной сценарий Алисы/GigaChat.",
            "cards": [],
        }

    return {"success": True, "message": "ИИ-карточки готовы.", "cards": cards, "raw_response": raw}
