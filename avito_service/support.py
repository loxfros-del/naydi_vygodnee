"""Append-only local support inbox isolated from the Telegram database."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
from threading import Lock
import time
from typing import Any

from .errors import AvitoServiceError


class SupportInbox:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self._recent: dict[str, tuple[float, str]] = {}
        self._attempts: dict[str, list[float]] = {}

    @staticmethod
    def _text(payload: dict[str, Any], name: str, limit: int) -> str:
        value = payload.get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Поле «{name}» должно быть строкой.")
        return " ".join(str(value or "").split())[:limit]

    def create(self, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise ValueError("Нужны данные заявки в поддержку.")
        name = self._text(payload, "name", 80)
        contact = self._text(payload, "contact", 160)
        topic = self._text(payload, "topic", 80) or "Вопрос по подбору"
        raw_message = payload.get("message")
        if raw_message is not None and not isinstance(raw_message, str):
            raise ValueError("Поле «message» должно быть строкой.")
        message = str(raw_message or "").strip()[:3000]
        if len(name) < 2:
            raise ValueError("Укажите имя.")
        if len(contact) < 3:
            raise ValueError("Укажите Telegram, телефон или email для ответа.")
        if len(message) < 10:
            raise ValueError("Опишите вопрос хотя бы в десяти символах.")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"name": name, "contact": contact, "topic": topic, "message": message},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        now = time.monotonic()
        with self._lock:
            self._recent = {
                key: value for key, value in self._recent.items() if value[0] >= now - 300
            }
            duplicate = self._recent.get(fingerprint)
            if duplicate is not None:
                return {
                    "ticket": duplicate[1],
                    "message": "Заявка уже принята. Мы свяжемся с вами по указанному контакту.",
                }
            contact_key = contact.casefold()
            attempts = [stamp for stamp in self._attempts.get(contact_key, []) if stamp >= now - 600]
            if len(attempts) >= 5:
                raise AvitoServiceError(
                    "Слишком много заявок. Подождите несколько минут и попробуйте снова.",
                    code="SUPPORT_RATE_LIMIT",
                    retryable=True,
                )
            attempts.append(now)
            self._attempts[contact_key] = attempts

        ticket = f"NV-{datetime.now(timezone.utc):%y%m%d}-{secrets.token_hex(3).upper()}"
        record = {
            "ticket": ticket,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "contact": contact,
            "topic": topic,
            "message": message,
            "status": "new",
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
                self._recent[fingerprint] = (now, ticket)
        except OSError:
            raise AvitoServiceError(
                "Не удалось сохранить заявку. Попробуйте ещё раз позже.",
                code="SUPPORT_STORAGE_UNAVAILABLE",
            ) from None
        return {"ticket": ticket, "message": "Заявка принята. Мы свяжемся с вами по указанному контакту."}
