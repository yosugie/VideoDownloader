"""Тесты допуска пользователей к боту."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from bot.middlewares.access import AccessMiddleware


@dataclass
class FakeUser:
    id: int
    username: str = "кто-то"


class FakeEvent:
    """Не Message и не CallbackQuery, поэтому ответить о запрете некуда."""


def run_middleware(
    middleware: AccessMiddleware, user: FakeUser | None
) -> bool:
    """Возвращает True, если запрос дошёл до обработчика."""
    reached = False

    async def handler(event: object, data: dict) -> str:
        nonlocal reached
        reached = True
        return "готово"

    data = {"event_from_user": user} if user is not None else {}
    asyncio.run(middleware(handler, FakeEvent(), data))
    return reached


def test_open_bot_lets_everyone_in() -> None:
    """Пустой белый список — бот работает для всех."""
    middleware = AccessMiddleware(frozenset())
    assert run_middleware(middleware, FakeUser(12345)) is True


def test_whitelist_blocks_strangers() -> None:
    middleware = AccessMiddleware(frozenset({1}))
    assert run_middleware(middleware, FakeUser(999)) is False
    assert run_middleware(middleware, FakeUser(1)) is True


def test_blocklist_works_on_open_bot() -> None:
    """Главный случай: бот открыт, но конкретного человека выгнали."""
    middleware = AccessMiddleware(frozenset(), frozenset({666}))

    assert run_middleware(middleware, FakeUser(666)) is False
    assert run_middleware(middleware, FakeUser(777)) is True


def test_blocklist_beats_whitelist() -> None:
    """Если человек есть в обоих списках, запрет сильнее."""
    middleware = AccessMiddleware(frozenset({666}), frozenset({666}))
    assert run_middleware(middleware, FakeUser(666)) is False


def test_event_without_user_passes_open_bot() -> None:
    assert run_middleware(AccessMiddleware(frozenset()), None) is True
