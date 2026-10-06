"""Белый список пользователей."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

log = logging.getLogger(__name__)

_DENIED_TEXT = (
    "⛔️ <b>Нет доступа</b>\n\n"
    "Это личный бот, им пользуется ограниченный круг людей.\n"
    "Ваш Telegram ID: <code>{user_id}</code>\n\n"
    "Передайте его владельцу бота, если доступ нужен."
)


class AccessMiddleware(BaseMiddleware):
    """Пропускает только пользователей из ``ALLOWED_USER_IDS``.

    Если список пуст, бот открыт для всех.
    """

    def __init__(
        self,
        allowed_user_ids: frozenset[int],
        blocked_user_ids: frozenset[int] = frozenset(),
    ) -> None:
        self._allowed = allowed_user_ids
        self._blocked = blocked_user_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        if user is not None and user.id in self._blocked:
            log.warning("Заблокированный пользователь: id=%s", user.id)
            return None

        # Пустой белый список означает «бот открыт для всех».
        if not self._allowed:
            return await handler(event, data)

        if user is None or user.id in self._allowed:
            return await handler(event, data)

        log.warning("Отклонён доступ: id=%s username=%s", user.id, user.username)
        await self._deny(event, user.id)
        return None

    @staticmethod
    async def _deny(event: TelegramObject, user_id: int) -> None:
        text = _DENIED_TEXT.format(user_id=user_id)
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer("Нет доступа к этому боту.", show_alert=True)
