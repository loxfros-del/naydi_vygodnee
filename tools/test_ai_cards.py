"""Проверка AI-карточек без внешнего API."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app import db  # noqa: E402
from app.ai_cards_service import build_ai_cards_prompt, parse_ai_cards_json  # noqa: E402
from app.db import Request, SearchResult  # noqa: E402


def _candidate(idx: int, title: str, price: int, source: str, url: str, score: float) -> SearchResult:
    return SearchResult(
        id=idx,
        request_id=1,
        title=title,
        price=price,
        source=source,
        url=url,
        snippet="4K, Smart TV, подходит для PS5",
        score=score,
        risk_flags=json.dumps(["проверить гарантию"], ensure_ascii=False),
        status="CANDIDATE",
    )


def main() -> int:
    req = Request(
        id=1,
        user_id=1,
        product_name="телевизор",
        use_case="для PS5",
        budget="45000",
        city="Ярославль",
        original_query="Нужен телевизор для PS5 до 45к в Ярославле",
    )
    candidates = [
        _candidate(1, "Samsung UE43AU7100U", 43000, "DNS", "https://www.dns-shop.ru/product/samsung", 91),
        _candidate(2, "LG 43UP75006LF", 39990, "М.Видео", "https://www.mvideo.ru/products/lg", 88),
        _candidate(3, "Xiaomi TV A Pro 43", 41500, "Яндекс Маркет", "https://market.yandex.ru/product/xiaomi", 84),
        _candidate(4, "TCL 50C645", 47990, "Ozon", "https://www.ozon.ru/product/tcl", 75),
        _candidate(5, "Sber SDX-43F3111", 18000, "ОнлайнТрейд", "https://www.onlinetrade.ru/catalogue/sber", 70),
        _candidate(6, "Статья про лучшие ТВ", 0, "blog", "https://example.com/best-tv", 10),
    ]
    prompt = build_ai_cards_prompt(req, candidates)
    assert "Нужен телевизор" in prompt or "телевизор" in prompt
    assert "Samsung UE43AU7100U" in prompt

    raw = json.dumps({
        "cards": [
            {
                "role": "BEST",
                "title": "Samsung UE43AU7100U",
                "price": 43000,
                "store": "DNS",
                "url": "https://www.dns-shop.ru/product/samsung",
                "why": "Оптимальный вариант в бюджете для PS5.",
                "risks": ["60 Гц"],
                "manual_check": ["наличие", "гарантию"],
                "confidence": "high",
            },
            {
                "role": "BACKUP",
                "title": "LG 43UP75006LF",
                "price": 39990,
                "store": "М.Видео",
                "url": "https://www.mvideo.ru/products/lg",
                "why": "Запасной вариант с хорошей ценой.",
                "risks": ["слабый звук"],
                "manual_check": ["доставку"],
                "confidence": "medium",
            },
            {
                "role": "BUDGET",
                "title": "Sber SDX-43F3111",
                "price": 18000,
                "store": "ОнлайнТрейд",
                "url": "https://www.onlinetrade.ru/catalogue/sber",
                "why": "Самый дешёвый рабочий вариант.",
                "risks": ["платформа Салют ТВ"],
                "manual_check": ["отзывы"],
                "confidence": "medium",
            },
            {
                "role": "CAUTION",
                "title": "TCL 50C645",
                "price": 47990,
                "store": "Ozon",
                "url": "https://www.ozon.ru/product/tcl",
                "why": "Хороший ТВ, но выше бюджета.",
                "risks": ["выше бюджета"],
                "manual_check": ["актуальную цену"],
                "confidence": "low",
            },
        ]
    }, ensure_ascii=False)

    items = parse_ai_cards_json(raw, budget=45000)
    assert [item["role"] for item in items] == ["BEST", "BACKUP", "BUDGET", "CAUTION"]
    assert items[0]["name"] == "Samsung UE43AU7100U"
    assert items[0]["price_num"] == 43000
    assert items[0]["manual_check"] == ["наличие", "гарантию"]
    assert items[3]["within_budget"] is False

    with tempfile.TemporaryDirectory() as tmp:
        db.settings.DB_PATH = str(Path(tmp) / "ai_cards.db")
        db.init_db()
        req_id = db.create_request(
            user_id=1,
            product_name="телевизор",
            use_case="для PS5",
            budget="45000",
            city="Ярославль",
        )
        db.replace_alice_results(req_id, items)
        saved = db.get_alice_results(req_id)
        assert [item.status for item in saved] == ["BEST", "BACKUP", "BUDGET", "DO_NOT_BUY"]
        assert all(item.price_verified is False for item in saved)
        assert saved[0].link_check_status == "FOUND_UNVERIFIED"
        assert "manual_check" in saved[0].admin_note

    print("test_ai_cards: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
