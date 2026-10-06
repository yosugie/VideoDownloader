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
