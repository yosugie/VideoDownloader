"""Тесты развилки «видео или картинки» без обращения к Telegram."""

from __future__ import annotations

import asyncio

import pytest

from bot.handlers.download import KIND_PHOTOS, _download
from bot.services.downloader import (
    KIND_AUDIO,
    KIND_VIDEO,
    AuthRequired,
    NoVideoStream,
    Progress,
)


class FakeDownloader:
    """Загрузчик видео, который ведёт себя так, как скажут."""

    def __init__(self, result: object = "видео", error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, url: str, kind: str, progress: Progress) -> object:
        self.calls.append((url, kind))
        if self.error is not None:
            raise self.error
        return self.result


class FakePhotos:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    async def fetch(self, url: str, progress: Progress) -> object:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return "альбом"


def run(coro):
    return asyncio.run(coro)


def test_video_is_returned_when_available() -> None:
    video, photos = FakeDownloader(), FakePhotos()

    result = run(_download("https://x/1", KIND_VIDEO, Progress(), video, photos))

    assert result == "видео"
    assert photos.calls == [], "за картинками ходить было незачем"


def test_falls_back_to_photos_when_post_has_no_video() -> None:
    """Главный случай: Instagram ответил «No video formats found»."""
    video = FakeDownloader(error=NoVideoStream("нет видеодорожки"))
    photos = FakePhotos()

    result = run(_download("https://instagram.com/p/x/", KIND_VIDEO, Progress(), video, photos))

    assert result == "альбом"
    assert photos.calls == ["https://instagram.com/p/x/"]


def test_photo_failure_propagates_to_caller() -> None:
    video = FakeDownloader(error=NoVideoStream("нет видеодорожки"))
    photos = FakePhotos(error=AuthRequired("нужны cookies"))

    with pytest.raises(AuthRequired):
        run(_download("https://instagram.com/p/x/", KIND_VIDEO, Progress(), video, photos))


def test_auth_error_does_not_trigger_photo_fallback() -> None:
    """Если сервис просит логин, картинки он тоже не отдаст."""
    video = FakeDownloader(error=AuthRequired("нужны cookies"))
    photos = FakePhotos()

    with pytest.raises(AuthRequired):
        run(_download("https://instagram.com/p/x/", KIND_VIDEO, Progress(), video, photos))
    assert photos.calls == []


def test_audio_request_never_goes_to_photos() -> None:
    video = FakeDownloader(result="звук")
    photos = FakePhotos()

    result = run(_download("https://x/1", KIND_AUDIO, Progress(), video, photos))

    assert result == "звук"
    assert video.calls == [("https://x/1", KIND_AUDIO)]
    assert photos.calls == []


def test_explicit_photo_request_skips_video() -> None:
    video, photos = FakeDownloader(), FakePhotos()

    result = run(_download("https://x/1", KIND_PHOTOS, Progress(), video, photos))

    assert result == "альбом"
    assert video.calls == []


def test_progress_text_changes_before_photo_fallback() -> None:
    """Пользователь должен видеть, что бот переключился на картинки."""
    video = FakeDownloader(error=NoVideoStream("нет видеодорожки"))
    photos = FakePhotos()
    progress = Progress()

    run(_download("https://instagram.com/p/x/", KIND_VIDEO, progress, video, photos))

    assert "картинк" in progress.stage.lower()
