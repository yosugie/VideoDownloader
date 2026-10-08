"""Тесты допуска: кого пускать, кого остановить и что позволено гостю."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from aiogram.types import CallbackQuery, Message

from bot.middlewares.access import AccessMiddleware
from bot.services.access import APPROVED, BLOCKED, PENDING, Registry
from tests.test_downloader import make_settings

OWNER = 777
STRANGER = 123


@dataclass
class FakeUser:
    id: int
    username: str | None = "кто-то"
    first_name: str = "Гость"

    @property
    def full_name(self) -> str:
        return self.first_name


class FakeEvent:
    """Не Message и не CallbackQuery: ответить о запрете некуда."""


def a_message(text: str) -> Message:
    """
    Настоящее сообщение aiogram с подменённым ответом.

    Подделкой тут не обойтись: middleware различает события через
    isinstance, и самодельный класс прошёл бы мимо этой проверки.
    """
    message = Message.model_construct(message_id=1, text=text)
    said: list[str] = []

    async def answer(text: str, **kwargs: object) -> None:
        said.append(text)

    object.__setattr__(message, "answer", answer)
    object.__setattr__(message, "said", said)
    return message


def a_press(data: str) -> CallbackQuery:
    press = CallbackQuery.model_construct(id="1", data=data)
    said: list[str] = []

    async def answer(text: str = "", **kwargs: object) -> None:
        said.append(text)

    object.__setattr__(press, "answer", answer)
    object.__setattr__(press, "said", said)
    return press


def registry_at(tmp_path: Path) -> Registry:
    return Registry(tmp_path / "access.db")


def run(middleware: AccessMiddleware, user: FakeUser | None, event: object = None) -> bool:
    """Возвращает True, если запрос дошёл до обработчика."""
    reached = False

    async def handler(event: object, data: dict) -> str:
        nonlocal reached
        reached = True
        return "готово"

    data = {"event_from_user": user} if user is not None else {}
    asyncio.run(middleware(handler, event if event is not None else FakeEvent(), data))
    return reached


def gated(tmp_path: Path, registry: Registry) -> AccessMiddleware:
    """Бот с владельцем: работает по заявкам."""
    settings = make_settings(tmp_path, admin_ids=frozenset({OWNER}))
    registry.seed(frozenset({OWNER}))
    return AccessMiddleware(settings, registry)


# ── бот по заявкам ─────────────────────────────────────────────────────


def test_owner_gets_in(tmp_path: Path) -> None:
    middleware = gated(tmp_path, registry_at(tmp_path))
    assert run(middleware, FakeUser(OWNER)) is True


def test_stranger_is_stopped(tmp_path: Path) -> None:
    middleware = gated(tmp_path, registry_at(tmp_path))
    assert run(middleware, FakeUser(STRANGER)) is False


def test_approved_person_gets_in(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    middleware = gated(tmp_path, registry)
    registry.ask(STRANGER, "vasya", "Вася")
    assert run(middleware, FakeUser(STRANGER)) is False, "заявка — ещё не допуск"

    registry.approve(STRANGER, by=OWNER)
    assert run(middleware, FakeUser(STRANGER)) is True


def test_rejected_person_stays_out(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    middleware = gated(tmp_path, registry)
    registry.ask(STRANGER, None, "Вася")
    registry.reject(STRANGER, by=OWNER)
    assert run(middleware, FakeUser(STRANGER)) is False


def test_approval_takes_effect_at_once(tmp_path: Path) -> None:
    """Список спрашивается на каждом событии, а не читается при запуске."""
    registry = registry_at(tmp_path)
    middleware = gated(tmp_path, registry)
    assert run(middleware, FakeUser(STRANGER)) is False

    registry.ask(STRANGER, None, "Вася")
    registry.approve(STRANGER, by=OWNER)

    assert run(middleware, FakeUser(STRANGER)) is True, "перезапуск не нужен"


# ── что позволено гостю ────────────────────────────────────────────────


def test_guest_may_say_start(tmp_path: Path) -> None:
    middleware = gated(tmp_path, registry_at(tmp_path))
    assert run(middleware, FakeUser(STRANGER), a_message("/start")) is True


def test_guest_may_ask_for_access(tmp_path: Path) -> None:
    middleware = gated(tmp_path, registry_at(tmp_path))
    assert run(middleware, FakeUser(STRANGER), a_press("ask")) is True


def test_guest_may_not_download(tmp_path: Path) -> None:
    middleware = gated(tmp_path, registry_at(tmp_path))
    event = a_message("https://youtu.be/abc")

    assert run(middleware, FakeUser(STRANGER), event) is False
    assert event.said and "заявк" in event.said[0].lower()


def test_guest_may_not_press_other_buttons(tmp_path: Path) -> None:
    middleware = gated(tmp_path, registry_at(tmp_path))
    event = a_press("dl:audio:abc")

    assert run(middleware, FakeUser(STRANGER), event) is False
    assert event.said


def test_blocked_person_is_answered_nothing(tmp_path: Path) -> None:
    """Заблокированному бот не отвечает вовсе — спорить с ним незачем."""
    registry = registry_at(tmp_path)
    middleware = gated(tmp_path, registry)
    registry.ask(STRANGER, None, "Вася")
    registry.block(STRANGER, by=OWNER)
    event = a_message("/start")

    assert run(middleware, FakeUser(STRANGER), event) is False
    assert event.said == []


def test_blocklist_from_settings_still_works(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, admin_ids=frozenset({OWNER}), blocked_user_ids=frozenset({STRANGER})
    )
    middleware = AccessMiddleware(settings, registry_at(tmp_path))
    assert run(middleware, FakeUser(STRANGER), a_message("/start")) is False


# ── бот без владельца ведёт себя по-старому ────────────────────────────


def test_without_admins_an_empty_list_means_open(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    middleware = AccessMiddleware(settings, registry_at(tmp_path))
    assert run(middleware, FakeUser(STRANGER)) is True


def test_without_admins_the_list_from_settings_works(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, allowed_user_ids=frozenset({1}))
    middleware = AccessMiddleware(settings, registry_at(tmp_path))

    assert run(middleware, FakeUser(1)) is True
    assert run(middleware, FakeUser(STRANGER)) is False


def test_event_without_user_passes(tmp_path: Path) -> None:
    middleware = gated(tmp_path, registry_at(tmp_path))
    assert run(middleware, None) is True


# ── что middleware кладёт в данные ─────────────────────────────────────


def test_membership_is_passed_to_handlers(tmp_path: Path) -> None:
    """По этому признаку обработчик отличает гостя от своего."""
    registry = registry_at(tmp_path)
    middleware = gated(tmp_path, registry)
    seen: dict = {}

    async def handler(event: object, data: dict) -> None:
        seen.update(data)

    asyncio.run(
        middleware(handler, a_message("/start"), {"event_from_user": FakeUser(STRANGER)})
    )
    assert seen["is_member"] is False

    asyncio.run(
        middleware(handler, a_message("/start"), {"event_from_user": FakeUser(OWNER)})
    )
    assert seen["is_member"] is True


# ── само хранилище ─────────────────────────────────────────────────────


def test_seeding_does_not_revive_a_blocked_person(tmp_path: Path) -> None:
    """Иначе заблокированный владельцем вернулся бы после перезапуска."""
    registry = registry_at(tmp_path)
    registry.seed(frozenset({OWNER}))
    registry.block(OWNER, by=OWNER)

    registry.seed(frozenset({OWNER}))

    assert registry.get(OWNER).status == BLOCKED


def test_asking_twice_keeps_the_first_request(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    first = registry.ask(STRANGER, "vasya", "Вася")
    again = registry.ask(STRANGER, "petya", "Петя")

    assert again.asked_at == first.asked_at
    assert again.status == PENDING


def test_asking_again_after_a_refusal_is_allowed(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    registry.ask(STRANGER, None, "Вася")
    registry.reject(STRANGER, by=OWNER)

    assert registry.ask(STRANGER, None, "Вася").status == PENDING


def test_approving_an_unknown_person_changes_nothing(tmp_path: Path) -> None:
    assert registry_at(tmp_path).approve(999, by=OWNER) is None


def test_pending_list_waits_for_a_decision(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    registry.seed(frozenset({OWNER}))
    registry.ask(STRANGER, None, "Вася")
    registry.ask(456, None, "Петя")
    registry.approve(456, by=OWNER)

    assert [member.user_id for member in registry.pending()] == [STRANGER]
    assert registry.counts()[APPROVED] == 2


def test_forgetting_lets_a_person_start_over(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    registry.ask(STRANGER, None, "Вася")
    registry.reject(STRANGER, by=OWNER)

    assert registry.forget(STRANGER) is True
    assert registry.get(STRANGER) is None


@pytest.mark.parametrize(
    ("username", "full_name", "expected"),
    [
        ("vasya", "Вася Пупкин", "Вася Пупкин (@vasya)"),
        (None, "Вася Пупкин", "Вася Пупкин"),
        ("vasya", None, "@vasya"),
        (None, None, "123"),
    ],
)
def test_how_a_person_is_named(
    tmp_path: Path, username: str | None, full_name: str | None, expected: str
) -> None:
    registry = registry_at(tmp_path)
    assert registry.ask(123, username, full_name).title == expected


def test_profile_link_needs_a_username(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    assert registry.ask(1, "vasya", "Вася").profile_url == "https://t.me/vasya"
    assert registry.ask(2, None, "Петя").profile_url is None


def test_mention_works_without_a_username(tmp_path: Path) -> None:
    """Ссылка на профиль в тексте нужна и тем, у кого имени нет."""
    member = registry_at(tmp_path).ask(55, None, "Петя")
    assert 'href="tg://user?id=55"' in member.mention
