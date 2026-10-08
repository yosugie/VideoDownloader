"""Общее для всех проверок.

Диспетчер здесь один на весь прогон, и это не экономия: роутеры бота —
одиночки уровня модуля, и ко второму корневому роутеру aiogram их не
подключает (``Router is already attached``). Значит ``build_dispatcher``
вызывается за процесс однажды — и в проверках тоже.
"""

from __future__ import annotations

import pytest
from aiogram import Dispatcher

from bot.__main__ import build_dispatcher
from tests.test_downloader import make_settings

OWNER = 777


@pytest.fixture(scope="session")
def dispatcher(tmp_path_factory: pytest.TempPathFactory) -> Dispatcher:
    """Настоящий диспетчер бота со владельцем ``OWNER``."""
    settings = make_settings(
        tmp_path_factory.mktemp("dispatcher"), admin_ids=frozenset({OWNER})
    )
    return build_dispatcher(settings)
