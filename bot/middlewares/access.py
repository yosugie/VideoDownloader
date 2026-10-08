"""Допуск к боту.

Список допущенных живёт в базе и меняется на ходу, поэтому middleware
спрашивает его на каждом событии, а не держит копию у себя. Из настроек
берутся только те, кто допущен изначально, и чёрный список.

Гостю оставлена ровно одна дорога — команда ``/start``, она же заявка.
Всё остальное для него закрыто: иначе заявка была бы не входом, а
формальностью.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from bot.config import Settings
from bot.services.access import Registry, gated, is_allowed, is_blocked

log = logging.getLogger(__name__)

_PRIVATE = (
    "🔒 <b>Личный бот</b>\n\n"
    "Им пользуется ограниченный круг людей."
)
_INVITE = (
    "🔒 <b>Бот работает по заявкам</b>\n\n"
    "Отправьте /start — я передам заявку владельцу."
)
_WAITING = (
    "⏳ <b>Заявка ждёт решения</b>\n\n"
    "Владелец её ещё не рассмотрел. Как решит — я напишу сюда."
)
_REJECTED = (
    "❌ <b>Доступ закрыт</b>\n\n"
    "Владелец отклонил заявку. Если это недоразумение, "
    "отправьте её заново: /start"
)


class AccessMiddleware(BaseMiddleware):
    """Решает, пускать ли человека дальше.

    Подключается ВНЕШНИМ middleware (``outer_middleware``). Внутренний
    aiogram запускает уже после фильтров, и фильтр гостя не увидел бы
    решения: гость уезжал бы в обычное приветствие, а потом получал
    отказ на ссылку — замкнутый круг.
    """

    def __init__(self, settings: Settings, registry: Registry) -> None:
        self._settings = settings
        self._registry = registry

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if is_blocked(self._settings, self._registry, user.id):
            log.warning("Заблокированный пользователь: id=%s", user.id)
            return None

        if is_allowed(self._settings, self._registry, user.id):
            return await handler(event, data)

        if gated(self._settings) and _is_guest_action(event):
            return await handler(event, data)

        log.info("Не допущен: id=%s username=%s", user.id, user.username)
        await self._deny(event, user)
        return None

    async def _deny(self, event: TelegramObject, user: User) -> None:
        text = self._denial(user)
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer("Нужно получить доступ: /start", show_alert=True)

    def _denial(self, user: User) -> str:
        """Отказ говорит, что делать дальше, а не просто «нельзя»."""
        if not gated(self._settings):
            # Принимать заявки некому, звать на /start бессмысленно.
            return _PRIVATE
        member = self._registry.get(user.id)
        if member is None:
            return _INVITE
        if member.waiting:
            return _WAITING
        return _REJECTED


def _is_guest_action(event: TelegramObject) -> bool:
    """Что гостю позволено: поздороваться, это же и подаёт заявку."""
    if isinstance(event, Message):
        text = (event.text or "").strip()
        return text == "/start" or text.startswith("/start@")
    return False
