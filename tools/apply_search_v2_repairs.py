"""One-shot repository repair used by CI on the Search V2 branch.

The script performs a narrow, idempotent source edit that is awkward to express
through the repository contents API because ``app/alice_service.py`` is large.
It removes itself and its trigger after applying the repair.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALICE_SERVICE = ROOT / "app" / "alice_service.py"
TRIGGER = ROOT / ".ci" / "apply-search-v2-repairs"
SELF = Path(__file__).resolve()

OLD = '        has_details = bool(item["price_num"] or item["store"] or item["link"] or item["pluses"] or item["risks"])'
NEW = '''        # The placeholder store is presentation fallback, not product evidence.
        # Without this gate a conversational postscript such as
        # "Надеюсь, это поможет" could become a fake product card.
        has_real_store = bool(
            item["store"] and item["store"] != "магазин нужно уточнить"
        )
        has_details = bool(
            item["price_num"]
            or has_real_store
            or item["link"]
            or item["pluses"]
            or item["risks"]
        )'''


def main() -> int:
    text = ALICE_SERVICE.read_text(encoding="utf-8")
    if OLD in text:
        text = text.replace(OLD, NEW, 1)
        ALICE_SERVICE.write_text(text, encoding="utf-8")
    elif NEW not in text:
        raise RuntimeError("Alice parser repair anchor was not found")

    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
