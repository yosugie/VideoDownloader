"""Тесты паузы и ограничения скорости."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bot.handlers.commands import status_text
from bot.services.downloader import Downloader
from bot.services.photos import PhotoDownloader
from bot.utils.quota import DailyQuota
from bot.utils.rate import format_rate, parse_rate
from bot.utils.state import RuntimeState
from tests.test_downloader import make_settings

# ── пауза ──────────────────────────────────────────────────────────────


def test_state_starts_working() -> None:
    assert RuntimeState().paused is False


def test_pause_and_resume() -> None:
    state = RuntimeState()

    state.pause()
    assert state.paused is True
    assert state.paused_since != "—"

    state.resume()
    assert state.paused is False
    assert state.paused_since == "—"


def test_pause_is_visible_in_status(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    state = RuntimeState()
    state.pause()

    text = status_text(
        user_id=1,
        settings=settings,
        downloader=Downloader(settings),
        quota=DailyQuota(0),
        runtime=state,
    )

    assert "пауза" in text.lower()


def test_status_without_state_still_works(tmp_path: Path) -> None:
    """Старый вызов без состояния не должен падать."""
    settings = make_settings(tmp_path)
    text = status_text(
        user_id=1,
        settings=settings,
        downloader=Downloader(settings),
        quota=DailyQuota(0),
    )
    assert "Очередь" in text


# ── ограничение скорости ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5M", 5 * 1024**2),
        ("500K", 500 * 1024),
        ("1.5M", int(1.5 * 1024**2)),
        ("1048576", 1048576),
        ("", None),
        ("мусор", None),
        ("0", None),
        (None, None),
    ],
)
def test_rate_parsing(raw: str | None, expected: int | None) -> None:
    assert parse_rate(raw) == expected


def test_rate_formatting() -> None:
    assert format_rate(None) == "без ограничения"
    assert "МБ/с" in format_rate(5 * 1024**2)
    assert "КБ/с" in format_rate(500 * 1024)


def test_rate_reaches_ytdlp() -> None:
    directory = Path(tempfile.mkdtemp())
    worker = Downloader(make_settings(directory, max_download_rate="5M"))

    opts = worker._base_opts(directory)

    assert opts["ratelimit"] == 5 * 1024**2


def test_no_rate_means_no_option() -> None:
    directory = Path(tempfile.mkdtemp())
    opts = Downloader(make_settings(directory))._base_opts(directory)
    assert "ratelimit" not in opts


def test_rate_reaches_gallery_dl(tmp_path: Path) -> None:
    """Картинки тоже должны качаться в рамках общей полосы."""
    worker = PhotoDownloader(make_settings(tmp_path, max_download_rate="2M"))

    command = worker._command("https://instagram.com/p/abc/", tmp_path)

    assert command[command.index("--limit-rate") + 1] == "2M"


def test_gallery_dl_without_rate(tmp_path: Path) -> None:
    command = PhotoDownloader(make_settings(tmp_path))._command("https://x/", tmp_path)
    assert "--limit-rate" not in command


def test_admin_status_shows_rate(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, admin_ids=frozenset({7}), max_download_rate="5M"
    )
    text = status_text(
        user_id=7,
        settings=settings,
        downloader=Downloader(settings),
        quota=DailyQuota(0),
        runtime=RuntimeState(),
    )
    assert "Скорость" in text and "5.0 МБ/с" in text


def test_users_do_not_see_rate(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_download_rate="5M")
    text = status_text(
        user_id=999,
        settings=settings,
        downloader=Downloader(settings),
        quota=DailyQuota(0),
        runtime=RuntimeState(),
    )
    assert "Скорость" not in text
