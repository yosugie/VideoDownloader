"""Тесты загрузки фотопостов (gallery-dl не запускается по-настоящему)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bot.services import photos as ph
from bot.services.downloader import AuthRequired, Progress
from tests.test_downloader import make_settings


def make_image(path: Path, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# ── сбор файлов ────────────────────────────────────────────────────────


def test_collect_finds_images_only(tmp_path: Path) -> None:
    make_image(tmp_path / "1.jpg")
    make_image(tmp_path / "2.png")
    (tmp_path / "info.json").write_text("{}", encoding="utf-8")
    (tmp_path / "clip.mp4").write_bytes(b"video")

    found = ph.PhotoDownloader._collect(tmp_path)

    assert [path.name for path in found] == ["1.jpg", "2.png"]


def test_collect_digs_into_subfolders(tmp_path: Path) -> None:
    make_image(tmp_path / "instagram" / "user" / "a.jpg")
    make_image(tmp_path / "instagram" / "user" / "b.jpg")

    assert len(ph.PhotoDownloader._collect(tmp_path)) == 2


def test_collect_respects_album_limit(tmp_path: Path) -> None:
    for index in range(15):
        make_image(tmp_path / f"{index:02d}.jpg")

    found = ph.PhotoDownloader._collect(tmp_path)

    assert len(found) == ph.MAX_ALBUM_ITEMS == 10
    assert found[0].name == "00.jpg", "порядок должен сохраняться"


def test_collect_on_empty_dir(tmp_path: Path) -> None:
    assert ph.PhotoDownloader._collect(tmp_path) == []


# ── командная строка ───────────────────────────────────────────────────


def test_command_has_required_flags(tmp_path: Path) -> None:
    worker = ph.PhotoDownloader(make_settings(tmp_path))
    command = worker._command("https://instagram.com/p/abc/", tmp_path)

    assert command[:3] == [sys.executable, "-m", "gallery_dl"]
    assert "--directory" in command
    assert command[command.index("--range") + 1] == "1-10"
    assert command[-1] == "https://instagram.com/p/abc/"


def test_command_passes_cookies_and_proxy(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    worker = ph.PhotoDownloader(
        make_settings(tmp_path, cookies_file=cookies, proxy="socks5://127.0.0.1:1080")
    )
    command = worker._command("https://instagram.com/p/abc/", tmp_path)

    assert command[command.index("--cookies") + 1] == str(cookies)
    assert command[command.index("--proxy") + 1] == "socks5://127.0.0.1:1080"


def test_command_uses_browser_cookies(tmp_path: Path) -> None:
    worker = ph.PhotoDownloader(make_settings(tmp_path, cookies_from_browser="firefox"))
    command = worker._command("https://instagram.com/p/abc/", tmp_path)

    assert command[command.index("--cookies-from-browser") + 1] == "firefox"
    assert "--cookies" not in command


# ── разбор ошибок ──────────────────────────────────────────────────────


def test_login_error_is_reported_without_jargon() -> None:
    error = ph.PhotoDownloader._translate(
        "login required", "https://www.instagram.com/p/abc/"
    )
    assert isinstance(error, AuthRequired)
    assert "cookies" not in str(error).lower()
    assert error.hint is not None and "cookies" in error.hint.lower()


def test_unsupported_url_is_reported() -> None:
    error = ph.PhotoDownloader._translate(
        "No suitable extractor found", "https://example.com/x"
    )
    assert isinstance(error, ph.NoPhotosFound)


def test_translate_keeps_detail() -> None:
    error = ph.PhotoDownloader._translate("странная беда", "https://example.com/x")
    assert error.detail is not None and "странная беда" in error.detail


# ── полный проход с подменой gallery-dl ────────────────────────────────


@pytest.fixture
def fake_gallery_dl(monkeypatch: pytest.MonkeyPatch):
    """Подменяет запуск gallery-dl: он просто раскладывает заданные файлы."""

    def install(files: dict[str, int], returncode: int = 0, stderr: str = ""):
        def fake_run(command, **kwargs):
            outdir = Path(command[command.index("--directory") + 1])
            for name, size in files.items():
                make_image(outdir / name, size)
            return subprocess.CompletedProcess(command, returncode, "", stderr)

        monkeypatch.setattr(ph.subprocess, "run", fake_run)

    return install


def test_fetch_collects_downloaded_photos(tmp_path: Path, fake_gallery_dl) -> None:
    fake_gallery_dl({"1.jpg": 100, "2.jpg": 200})
    worker = ph.PhotoDownloader(make_settings(tmp_path))

    album = worker._fetch_sync("https://instagram.com/p/abc/", Progress())

    assert len(album.paths) == 2
    assert album.size == 300
    assert album.skipped == 0
    album.cleanup()
    assert not album.workdir.exists()


def test_fetch_skips_oversized_photos(tmp_path: Path, fake_gallery_dl) -> None:
    fake_gallery_dl({"small.jpg": 100, "huge.jpg": ph.PHOTO_MAX_BYTES + 1})
    worker = ph.PhotoDownloader(make_settings(tmp_path))

    album = worker._fetch_sync("https://instagram.com/p/abc/", Progress())

    assert [path.name for path in album.paths] == ["small.jpg"]
    assert album.skipped == 1
    album.cleanup()


def test_fetch_raises_when_nothing_downloaded(tmp_path: Path, fake_gallery_dl) -> None:
    fake_gallery_dl({}, returncode=1, stderr="No suitable extractor found")
    worker = ph.PhotoDownloader(make_settings(tmp_path))

    with pytest.raises(ph.NoPhotosFound):
        worker._fetch_sync("https://example.com/x", Progress())


def test_fetch_succeeds_even_on_nonzero_exit(tmp_path: Path, fake_gallery_dl) -> None:
    """gallery-dl возвращает ошибку, даже когда часть файлов всё же скачалась."""
    fake_gallery_dl({"1.jpg": 100}, returncode=1, stderr="1 file skipped")
    worker = ph.PhotoDownloader(make_settings(tmp_path))

    album = worker._fetch_sync("https://instagram.com/p/abc/", Progress())

    assert len(album.paths) == 1
    album.cleanup()


def test_fetch_reports_auth_problem(tmp_path: Path, fake_gallery_dl) -> None:
    fake_gallery_dl({}, returncode=1, stderr="HttpError: login required")
    worker = ph.PhotoDownloader(make_settings(tmp_path))

    with pytest.raises(AuthRequired):
        worker._fetch_sync("https://www.instagram.com/p/abc/", Progress())


def test_fetch_cleans_up_after_failure(tmp_path: Path, fake_gallery_dl) -> None:
    fake_gallery_dl({}, returncode=1, stderr="nothing")
    worker = ph.PhotoDownloader(make_settings(tmp_path))
    before = set(tmp_path.iterdir())

    with pytest.raises(ph.NoPhotosFound):
        worker._fetch_sync("https://example.com/x", Progress())

    assert set(tmp_path.iterdir()) == before, "временный каталог должен быть удалён"


def test_gallery_dl_colours_are_stripped() -> None:
    error = ph.PhotoDownloader._translate(
        "\x1b[1;31m[error]\x1b[0m No suitable extractor found",
        "https://example.com/x",
    )
    assert error.detail is not None and "\x1b" not in error.detail
