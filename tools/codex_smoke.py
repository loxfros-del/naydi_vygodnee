"""Smoke checks for Codex without starting Telegram polling."""
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Do not read .env during this smoke import.
os.environ.setdefault("BOT_TOKEN", "smoke:test-token")
os.environ.setdefault("ADMIN_IDS", "1")
try:
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
except Exception:
    pass


def main() -> int:
    import app.handlers.user  # noqa: F401
    import app.handlers.admin  # noqa: F401
    import app.report_builder  # noqa: F401
    import app.alice_service  # noqa: F401
    import app.keyboards  # noqa: F401
    import app.states  # noqa: F401
    from app.alice_service import parse_alice_response

    text = """Название: Samsung UE43AU7100U
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/product/samsung-ue43au7100u
Почему подходит: 4K, Smart TV
Риски: 60 Гц

Название: LG 43UP75006LF
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: ссылку нужно искать вручную
Почему подходит: 4K, хорошая цена
Риски: слабый звук
"""
    cards = parse_alice_response(text, budget=45_000)
    assert len(cards) == 2, cards
    assert all(not card["name"].startswith(("http://", "https://")) for card in cards), cards
    assert cards[0]["link"].startswith("https://www.dns-shop.ru/"), cards
    assert cards[1]["link"] == "", cards
    print("codex_smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
