"""Тесты защиты ресурсов сервера: место на диске и мусор от прошлых запусков."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

import pytest

import bot.__main__ as main
from bot.services import downloader as dl
from bot.services import photos as ph
from bot.services.downloader import Progress
from tests.test_downloader import make_settings


class FakeUsage:
    def __init__(self, free_mb: float) -> None:
        self.free = int(free_mb * 1024 * 1024)
        self.total = 100 * 1024 * 1024 * 1024
        self.used = self.total - self.free


# ── порог свободного места ─────────────────────────────────────────────


def test_download_refused_when_disk_is_nearly_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl.shutil, "disk_usage", lambda path: FakeUsage(500))
    settings = make_settings(tmp_path, min_free_disk_mb=2048)

    with pytest.raises(dl.NotEnoughSpace):
        dl.ensure_disk_space(settings)


def test_download_allowed_with_enough_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl.shutil, "disk_usage", lambda path: FakeUsage(10_000))
    dl.ensure_disk_space(make_settings(tmp_path, min_free_disk_mb=2048))


def test_zero_threshold_disables_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl.shutil, "disk_usage", lambda path: FakeUsage(0))
    dl.ensure_disk_space(make_settings(tmp_path, min_free_disk_mb=0))


def test_unreadable_disk_does_not_block_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не смогли узнать место — это не повод отказывать пользователю."""

    def boom(path: object) -> None:
        raise OSError("нет доступа")

    monkeypatch.setattr(dl.shutil, "disk_usage", boom)
    dl.ensure_disk_space(make_settings(tmp_path, min_free_disk_mb=2048))


def test_photo_download_checks_space_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl.shutil, "disk_usage", lambda path: FakeUsage(100))
    worker = ph.PhotoDownloader(make_settings(tmp_path, min_free_disk_mb=2048))

    with pytest.raises(dl.NotEnoughSpace):
        worker._fetch_sync("https://instagram.com/p/abc/", Progress())


def test_refusal_is_a_downloader_error() -> None:
    """Значит, пользователь получит внятное сообщение, а не падение бота."""
    assert issubclass(dl.NotEnoughSpace, dl.DownloaderError)


# ── мусор от прошлого запуска ──────────────────────────────────────────


def test_leftovers_are_removed_on_start(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "vd-abc123").mkdir()
    (tmp_path / "vd-img-def456").mkdir()
    (tmp_path / "vd-xyz" / "try1").mkdir(parents=True)

    with caplog.at_level(logging.INFO, logger="bot"):
        main.cleanup_leftovers(make_settings(tmp_path))

    assert list(tmp_path.iterdir()) == []
    assert "3 временных каталогов" in caplog.text


def test_cleanup_keeps_other_files(tmp_path: Path) -> None:
    """Чужое добро не трогаем — в каталоге может лежать что угодно."""
    (tmp_path / "vd-temp").mkdir()
    (tmp_path / "cookies.txt").write_text("важное", encoding="utf-8")
    (tmp_path / "архив").mkdir()

    main.cleanup_leftovers(make_settings(tmp_path))

    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["cookies.txt", "архив"]


def test_cleanup_is_quiet_when_nothing_to_do(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="bot"):
        main.cleanup_leftovers(make_settings(tmp_path))
    assert caplog.text == ""


def test_real_temp_dirs_match_cleanup_pattern(tmp_path: Path) -> None:
    """Имена временных каталогов и шаблон очистки не должны разойтись."""
    settings = make_settings(tmp_path, min_free_disk_mb=0)

    # Создаём каталоги теми же средствами, что и сам загрузчик.
    video_dir = Path(tempfile.mkdtemp(prefix="vd-", dir=settings.download_dir))
    photo_dir = Path(tempfile.mkdtemp(prefix="vd-img-", dir=settings.download_dir))
    assert video_dir.is_dir() and photo_dir.is_dir()

    main.cleanup_leftovers(settings)

    assert not video_dir.exists()
    assert not photo_dir.exists()


def test_shutil_is_imported_in_main() -> None:
    assert hasattr(main, "shutil") and main.shutil is shutil
