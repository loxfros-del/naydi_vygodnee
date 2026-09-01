"""Minimal, dependency-free Telegram Mini App authentication helpers.

The browser sends ``Telegram.WebApp.initData`` as one opaque string.  This
module validates the official HMAC before the search endpoint accepts any
request.  It deliberately returns only the small identity needed for a future
transport layer and never persists it.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from urllib.parse import parse_qsl


_MAX_INIT_DATA_BYTES = 8 * 1024
_ALLOWED_FUTURE_SKEW_SECONDS = 60


class TelegramMiniAppAuthError(ValueError):
    """Raised when Telegram Mini App data cannot be authenticated safely."""


@dataclass(frozen=True, slots=True)
class TelegramMiniAppIdentity:
    """Authenticated Telegram identity; no request or database state is held."""

    user_id: int
    username: str = ""


class TelegramMiniAppValidator:
    """Validate a Telegram WebApp ``initData`` string using the bot token.

    ``clock`` is injected in tests so expiry checks remain deterministic.
    The default 24-hour validity protects a copied old init-data string while
    still allowing a person to keep the Mini App open during a normal day.
    """

    def __init__(
        self,
        bot_token: str,
        *,
        max_age_seconds: int = 24 * 60 * 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._bot_token = str(bot_token or "").strip()
        self._max_age_seconds = int(max_age_seconds)
        self._clock = clock
        if not self._bot_token:
            raise ValueError("Telegram bot token is required for Mini App validation.")
        if self._max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive.")

    def validate(self, init_data: object) -> TelegramMiniAppIdentity:
        raw = str(init_data or "")
        if not raw or len(raw.encode("utf-8")) > _MAX_INIT_DATA_BYTES:
            raise TelegramMiniAppAuthError("invalid init data")
        try:
            pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise TelegramMiniAppAuthError("invalid init data") from exc

        values: dict[str, str] = {}
        for key, value in pairs:
            if not key or key in values:
                raise TelegramMiniAppAuthError("invalid init data")
            values[key] = value

        received_hash = values.pop("hash", "")
        if not received_hash or len(received_hash) != 64:
            raise TelegramMiniAppAuthError("invalid init data")
        data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret_key = hmac.new(
            b"WebAppData",
            self._bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            raise TelegramMiniAppAuthError("invalid init data")

        try:
            auth_date = int(values["auth_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TelegramMiniAppAuthError("invalid init data") from exc
        now = int(self._clock())
        if auth_date > now + _ALLOWED_FUTURE_SKEW_SECONDS or now - auth_date > self._max_age_seconds:
            raise TelegramMiniAppAuthError("expired init data")

        try:
            user = json.loads(values["user"])
            user_id = int(user["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TelegramMiniAppAuthError("invalid init data") from exc
        if user_id <= 0:
            raise TelegramMiniAppAuthError("invalid init data")
        return TelegramMiniAppIdentity(user_id=user_id, username=str(user.get("username") or "")[:64])


def build_telegram_miniapp_validator() -> TelegramMiniAppValidator:
    """Build the runtime validator without exposing the configured token."""
    from app.config import settings

    return TelegramMiniAppValidator(settings.BOT_TOKEN)


__all__ = [
    "TelegramMiniAppAuthError",
    "TelegramMiniAppIdentity",
    "TelegramMiniAppValidator",
    "build_telegram_miniapp_validator",
]
