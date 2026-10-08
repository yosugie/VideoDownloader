"""Инлайн-клавиатуры бота.

Выбор «видео или аудио» убран: бот сам решает, что скачивать. Кнопка
осталась ровно одна — запасной вариант, когда видео не влезло в лимит
Telegram и имеет смысл забрать хотя бы звук.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.downloader import KIND_AUDIO


class AccessDecision(CallbackData, prefix="acc"):
    """Решение владельца по заявке."""

    action: str
    user_id: int


class MemberAction(CallbackData, prefix="mem"):
    """Что владелец делает с человеком из списка допущенных."""

    action: str
    user_id: int


def manage_member(
    user_id: int, profile_url: str | None, *, blocked: bool = False
) -> InlineKeyboardMarkup:
    """Убрать человека из списка, а заблокированного — вернуть.

    Возврат нужен, чтобы блокировка не была тупиком: записи нет —
    человек снова может подать заявку.
    """
    builder = InlineKeyboardBuilder()
    drop = MemberAction(action="drop", user_id=user_id).pack()
    if blocked:
        builder.row(InlineKeyboardButton(text="↩️ Разблокировать", callback_data=drop))
    else:
        builder.row(
            InlineKeyboardButton(text="❌ Исключить", callback_data=drop),
            InlineKeyboardButton(
                text="🚫 Заблокировать",
                callback_data=MemberAction(action="block", user_id=user_id).pack(),
            ),
        )
    if profile_url:
        builder.row(InlineKeyboardButton(text="👤 Профиль", url=profile_url))
    return builder.as_markup()


def decide_access(user_id: int, profile_url: str | None) -> InlineKeyboardMarkup:
    """Принять, отклонить и заглянуть в профиль."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Принять",
            callback_data=AccessDecision(action="approve", user_id=user_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=AccessDecision(action="reject", user_id=user_id).pack(),
        ),
    )
    if profile_url:
        builder.row(InlineKeyboardButton(text="👤 Профиль", url=profile_url))
    return builder.as_markup()


class DownloadAction(CallbackData, prefix="dl"):
    """Нажатие на запасную кнопку скачивания."""

    kind: str
    token: str


def retry_as_audio(token: str) -> InlineKeyboardMarkup:
    """Предложение забрать звук, когда видео отдать не вышло."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎵 Скачать только звук",
            callback_data=DownloadAction(kind=KIND_AUDIO, token=token).pack(),
        )
    )
    return builder.as_markup()
