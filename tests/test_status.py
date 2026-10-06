"""Тесты команды /status: что видит обычный пользователь, а что администратор."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.handlers.commands import status_text
from bot.services.downloader import Downloader
from bot.utils.quota import DailyQuota
from tests.test_downloader import make_settings

ADMIN = 777
STRANGER = 123


@pytest.fixture
def worker(tmp_path: Path) -> Downloader:
    return Downloader(make_settings(tmp_path))


def text_for(user_id: int | None, tmp_path: Path, worker: Downloader, **overrides) -> str:
    settings = make_settings(tmp_path, admin_ids=frozenset({ADMIN}), **overrides)
    quota = DailyQuota(settings.daily_limit_per_user)
    return status_text(user_id=user_id, settings=settings, downloader=worker, quota=quota)


# ── что скрыто от посторонних ──────────────────────────────────────────


def test_cookies_are_hidden_from_users(tmp_path: Path, worker: Downloader) -> None:
    """Главное требование: про cookies посторонние знать не должны."""
    assert "ookies" not in text_for(STRANGER, tmp_path, worker)


def test_internals_are_hidden_from_users(tmp_path: Path, worker: Downloader) -> None:
    text = text_for(STRANGER, tmp_path, worker)
    assert "Доступ" not in text
    assert "Одновременных загрузок" not in text
    assert "администратору" not in text


def test_cookie_file_path_never_leaks(tmp_path: Path, worker: Downloader) -> None:
    cookies = tmp_path / "secret-cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    text = text_for(STRANGER, tmp_path, worker, cookies_file=cookies)

    assert "secret-cookies" not in text
    assert str(cookies) not in text


# ── что пользователю всё-таки полезно ──────────────────────────────────


def test_user_sees_useful_limits(tmp_path: Path, worker: Downloader) -> None:
    text = text_for(STRANGER, tmp_path, worker, max_file_size_mb=50, max_duration_minutes=90)
    assert "Очередь" in text
    assert "50 МБ" in text
    assert "90 мин" in text


def test_user_sees_own_quota(tmp_path: Path, worker: Downloader) -> None:
    text = text_for(STRANGER, tmp_path, worker, daily_limit_per_user=20)
    assert "осталось сегодня — 20" in text


def test_quota_absent_is_stated_plainly(tmp_path: Path, worker: Downloader) -> None:
    assert "Суточный лимит: не задан" in text_for(STRANGER, tmp_path, worker)


# ── что видит администратор ────────────────────────────────────────────


def test_admin_sees_everything(tmp_path: Path, worker: Downloader) -> None:
    text = text_for(ADMIN, tmp_path, worker, daily_limit_per_user=20)

    assert "Cookies: не заданы" in text
    assert "Доступ" in text
    assert "Одновременных загрузок" in text
    assert "Людей за сегодня" in text


def test_admin_is_not_limited(tmp_path: Path, worker: Downloader) -> None:
    text = text_for(ADMIN, tmp_path, worker, daily_limit_per_user=20)
    assert "не распространяется" in text


def test_unknown_user_gets_short_version(tmp_path: Path, worker: Downloader) -> None:
    assert "ookies" not in text_for(None, tmp_path, worker)
