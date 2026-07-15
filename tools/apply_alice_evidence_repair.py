"""One-shot repair for Alice product-evidence filtering."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "alice_service.py"
TRIGGER = ROOT / ".ci" / "apply-alice-evidence-repair"
SELF = Path(__file__).resolve()

OLD_DETAILS = '''        # The placeholder store is presentation fallback, not product evidence.
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
NEW_DETAILS = '''        # Arbitrary plain text must not become a store and make a fake card valid.
        # Store-only cards are accepted only when the store is from the known-store list.
        has_known_store = bool(store_re.search(str(item["store"] or "")))
        has_details = bool(
            item["price_num"]
            or has_known_store
            or item["link"]
            or item["pluses"]
            or item["risks"]
        )'''

OLD_FALLBACK = '    return _legacy_parse_alice_response(normalized, budget)'
NEW_FALLBACK = '''    legacy_items = _legacy_parse_alice_response(normalized, budget)
    return [
        item
        for item in legacy_items
        if item.get("price_num") is not None
        or bool(item.get("link"))
        or bool(item.get("pluses"))
        or bool(item.get("risks"))
        or bool(store_re.search(str(item.get("store") or "")))
    ]'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"Repair anchor not found: {label}")


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(text, OLD_DETAILS, NEW_DETAILS, "details")
    text = replace_once(text, OLD_FALLBACK, NEW_FALLBACK, "legacy fallback")
    TARGET.write_text(text, encoding="utf-8")
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
