"""Перехват исключений, до которых не дотянулись локальные try/except."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery, ErrorEvent, Message

log = logging.getLogger(__name__)

router = Router(name="errors")


@router.error()
async def on_error(event: ErrorEvent) -> bool:
    """Логирует ошибку и предупреждает пользователя, не роняя бота."""
    log.exception("Необработанная ошибка: %s", event.exception, exc_info=event.exception)

    update = event.update
    message = getattr(update, "message", None)
    callback = getattr(update, "callback_query", None)

    try:
        if isinstance(message, Message):
            await message.answer("💥 Внутренняя ошибка. Попробуйте ещё раз.")
        elif isinstance(callback, CallbackQuery):
            await callback.answer("Внутренняя ошибка. Попробуйте ещё раз.", show_alert=True)
    except Exception:
        # Сообщить не удалось — неприятно, но ронять бота из-за этого нельзя.
        log.debug("Не удалось уведомить пользователя об ошибке", exc_info=True)

    return True
