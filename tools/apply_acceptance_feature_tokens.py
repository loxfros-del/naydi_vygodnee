"""One-shot repair for independent feature tokens in the acceptance runner."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "search_v2_acceptance.py"
TEST = ROOT / "tools" / "test_search_v2_acceptance_runner.py"
TRIGGER = ROOT / ".ci" / "apply-acceptance-feature-tokens"
SELF = Path(__file__).resolve()

OLD = '''    for key, value in specs.items():
        token = spec_token(str(key), value)
        if token and token not in hard_tokens:
            hard_tokens.append(token)'''
NEW = '''    for key, value in specs.items():
        values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
        for item in values:
            token = spec_token(str(key), item)
            if token and token not in hard_tokens:
                hard_tokens.append(token)'''

TEST_OLD = '        self.assertIn("ANC TWS wireless", request.hard_tokens)'
TEST_NEW = '''        self.assertIn("ANC", request.hard_tokens)
        self.assertIn("TWS", request.hard_tokens)
        self.assertIn("wireless", request.hard_tokens)'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"acceptance feature-token anchor not found: {label}")


def main() -> int:
    RUNNER.write_text(replace_once(RUNNER.read_text(encoding="utf-8"), OLD, NEW, "runner"), encoding="utf-8")
    TEST.write_text(replace_once(TEST.read_text(encoding="utf-8"), TEST_OLD, TEST_NEW, "test"), encoding="utf-8")
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
