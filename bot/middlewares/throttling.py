"""Ограничение частоты запросов, чтобы бот не захлёбывался."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User


class ThrottlingMiddleware(BaseMiddleware):
    """Не чаще одного запроса в ``interval`` секунд на пользователя."""

    def __init__(self, interval: float = 2.0) -> None:
        self._interval = interval
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self._interval <= 0:
            return await handler(event, data)

        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        previous = self._last_seen.get(user.id)
        if previous is not None and now - previous < self._interval:
            await self._warn(event)
            return None

        self._last_seen[user.id] = now
        self._cleanup(now)
        return await handler(event, data)

    async def _warn(self, event: TelegramObject) -> None:
        text = "⏳ Слишком быстро. Подождите пару секунд и повторите."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=False)
        elif isinstance(event, Message):
            await event.answer(text)

    def _cleanup(self, now: float) -> None:
        """Выбрасывает старые записи, чтобы словарь не рос бесконечно."""
        if len(self._last_seen) < 10_000:
            return
        deadline = now - self._interval * 10
        self._last_seen = {
            user_id: seen for user_id, seen in self._last_seen.items() if seen > deadline
        }
