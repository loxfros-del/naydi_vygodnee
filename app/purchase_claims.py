"""Pure guard for price claims that need independent comparison evidence."""
from __future__ import annotations

import re
from typing import Any


_PRICE_ADVANTAGE_RE = re.compile(
    r"рын(?:ок|к|оч)|медиан|"
    r"экономи(?:я|ю|и|ей)\b(?!\s+(?:энерг|электроэнерг|топлив|мест|времен|памят|заряд))|"
    r"скид|дешев|дешёв|выгодн|выгод[ауые]|"
    r"(?:лучш|низш|минимальн|самая низк|самой низк).{0,24}(?:цен|стоим)|"
    r"(?:цен|стоим).{0,24}(?:ниже|лучш|низш|минимальн)|"
    r"(?:ниже|меньше).{0,24}(?:цен|стоим)|снижен.{0,12}цен|"
    r"к[еэ]шб[еэ]к|cashback",
    re.IGNORECASE,
)


def has_price_advantage_claim(value: Any) -> bool:
    """Detect assertions requiring evidence; this does not verify their truth."""
    return bool(_PRICE_ADVANTAGE_RE.search(str(value or "")))


__all__ = ["has_price_advantage_claim"]
