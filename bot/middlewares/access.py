"""Допуск к боту.

Список допущенных живёт в базе и меняется на ходу, поэтому middleware
спрашивает его на каждом событии, а не держит копию у себя. Из настроек
берутся только те, кто допущен изначально, и чёрный список.

Гостю оставлены ровно два действия — команда ``/start`` и кнопка заявки
под ней. Всё остальное для него закрыто: иначе заявка была бы не входом,
а формальностью.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from bot.config import Settings
from bot.keyboards import AccessRequest
from bot.services.access import Registry

log = logging.getLogger(__name__)

_GUEST_HINT = (
    "🔒 <b>Бот работает по заявкам</b>\n\n"
    "Нажмите /start, чтобы отправить заявку владельцу."
)


class AccessMiddleware(BaseMiddleware):
    """Решает, пускать ли человека дальше."""

    def __init__(self, settings: Settings, registry: Registry) -> None:
        self._settings = settings
        self._registry = registry
        #: Заявки принимает администратор. Если его нет, принимать их
        #: некому, и бот ведёт себя по-старому — по списку из настроек.
        self._gated = bool(settings.admin_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if self._is_blocked(user.id):
            log.warning("Заблокированный пользователь: id=%s", user.id)
            return None

        member = self._registry.get(user.id)
        data["member"] = member
        data["is_member"] = allowed = self._is_allowed(user.id, member)

        if allowed:
            return await handler(event, data)

        if self._gated and _is_guest_action(event):
            return await handler(event, data)

        log.info("Не допущен: id=%s username=%s", user.id, user.username)
        await self._deny(event)
        return None

    # ── правила ──────────────────────────────────────────────────────

    def _is_blocked(self, user_id: int) -> bool:
        return user_id in self._settings.blocked_user_ids or self._registry.blocked(user_id)

    def _is_allowed(self, user_id: int, member: object) -> bool:
        if user_id in self._settings.admin_ids:
            return True
        if getattr(member, "allowed", False):
            return True
        if self._gated:
            return False
        # Без администратора список из настроек работает как прежде,
        # а пустой список означает «бот открыт для всех».
        allowed = self._settings.allowed_user_ids
        return not allowed or user_id in allowed

    async def _deny(self, event: TelegramObject) -> None:
        if isinstance(event, Message):
            await event.answer(_GUEST_HINT)
        elif isinstance(event, CallbackQuery):
            await event.answer("Нужно получить доступ: нажмите /start", show_alert=True)


def _is_guest_action(event: TelegramObject) -> bool:
    """Что гостю позволено: поздороваться и подать заявку."""
    if isinstance(event, Message):
        text = (event.text or "").strip()
        return text == "/start" or text.startswith("/start@")
    if isinstance(event, CallbackQuery):
        return (event.data or "").startswith(AccessRequest.__prefix__)
    return False
