"""Одно редактируемое клиентское сообщение о ходе подбора."""
from __future__ import annotations

from app.db import get_request, update_request
from app.ui_formatters import format_progress


async def update_client_progress(bot, request_id: int, stage: str) -> int | None:
    request = get_request(int(request_id))
    if request is None:
        return None
    text = format_progress(stage, request.id)
    message_id = getattr(request, "progress_message_id", None)
    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=request.user_id,
                message_id=int(message_id),
                text=text,
                parse_mode="HTML",
            )
            return int(message_id)
        except Exception:
            # Сообщение могло быть удалено или стать недоступным — создаём новое.
            pass
    try:
        message = await bot.send_message(request.user_id, text, parse_mode="HTML")
    except Exception:
        return None
    new_id = getattr(message, "message_id", None)
    if new_id:
        update_request(request.id, progress_message_id=int(new_id))
        return int(new_id)
    return None


__all__ = ["update_client_progress"]
