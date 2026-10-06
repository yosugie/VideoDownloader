"""Роутеры бота. Порядок подключения важен: download ловит любой текст."""

from aiogram import Router

from bot.handlers import commands, download, errors


def build_router() -> Router:
    """Собирает корневой роутер со всеми обработчиками."""
    root = Router(name="root")
    root.include_router(commands.router)
    root.include_router(download.router)
    root.include_router(errors.router)
    return root


__all__ = ["build_router"]
