"""Regression test: long admin debug output must fit Telegram messages."""
from __future__ import annotations

from app.handlers.admin import _telegram_chunks


def main() -> int:
    text = "🧪 <b>Debug</b>\n" + "<code>длинная строка диагностики</code>\n" * 600
    chunks = _telegram_chunks(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 3900 for chunk in chunks)
    print("Admin debug chunks: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
