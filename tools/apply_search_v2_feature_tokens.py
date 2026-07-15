"""One-shot repair: keep multi-value hard requirements as independent query tokens."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = ROOT / "app" / "search_v2" / "request_normalizer.py"
ACCEPTANCE_TEST = ROOT / "tools" / "test_search_v2_acceptance_runner.py"
TRIGGER = ROOT / ".ci" / "apply-search-v2-feature-tokens"
SELF = Path(__file__).resolve()

OLD = '''    for key, value in required_specs.items():
        token = spec_token(key, value)
        if token and token not in tokens:
            tokens.append(token)'''
NEW = '''    for key, value in required_specs.items():
        values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
        for item in values:
            token = spec_token(key, item)
            if token and token not in tokens:
                tokens.append(token)'''

TEST_OLD = '        self.assertIn("ANC TWS wireless", request.hard_tokens)'
TEST_NEW = '''        self.assertIn("ANC", request.hard_tokens)
        self.assertIn("TWS", request.hard_tokens)
        self.assertIn("wireless", request.hard_tokens)'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"feature-token anchor not found: {label}")


def main() -> int:
    text = NORMALIZER.read_text(encoding="utf-8")
    NORMALIZER.write_text(replace_once(text, OLD, NEW, "normalizer"), encoding="utf-8")
    test = ACCEPTANCE_TEST.read_text(encoding="utf-8")
    ACCEPTANCE_TEST.write_text(replace_once(test, TEST_OLD, TEST_NEW, "acceptance test"), encoding="utf-8")
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
