"""Скачивание фотопостов через gallery-dl.

yt-dlp умеет только видео: из карусели Instagram он выбрасывает любые
узлы, где ``is_video`` не истина, а слайдшоу TikTok отдаёт одной
звуковой дорожкой. Поэтому за картинками ходим отдельным инструментом.

gallery-dl запускается подпроцессом (``python -m gallery_dl``), а не как
библиотека: так он не тянет своё состояние в наш процесс, а любая его
ошибка остаётся обычным кодом возврата.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bot.config import Settings
from bot.services.downloader import (
    AuthRequired,
    DownloaderError,
    Progress,
    clean_error_text,
    ensure_disk_space,
    looks_like_auth_error,
)

log = logging.getLogger(__name__)

#: Telegram кладёт в одну медиагруппу не больше десяти элементов.
MAX_ALBUM_ITEMS = 10

#: Лимит Bot API на отправку фотографии.
PHOTO_MAX_BYTES = 10 * 1024 * 1024

#: Сколько ждать gallery-dl целиком.
_TIMEOUT_SECONDS = 600

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"})


class NoPhotosFound(DownloaderError):
    """В посте не нашлось ни одной картинки."""


@dataclass(slots=True)
class PhotoAlbum:
    """Готовый к отправке набор картинок."""

    paths: list[Path]
    workdir: Path
    source_url: str
    skipped: int = 0
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(path.stat().st_size for path in self.paths if path.is_file())

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


class PhotoDownloader:
    """Фасад над gallery-dl."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def fetch(self, url: str, progress: Progress) -> PhotoAlbum:
        """Скачивает картинки поста."""
        return await asyncio.to_thread(self._fetch_sync, url, progress)

    def _command(self, url: str, outdir: Path) -> list[str]:
        command = [
            sys.executable, "-m", "gallery_dl",
            "--quiet",
            "--no-part",
            "--directory", str(outdir),
            "--range", f"1-{MAX_ALBUM_ITEMS}",
            "--retries", "3",
            "--http-timeout", "30",
        ]
        if self._settings.cookies_file is not None:
            command += ["--cookies", str(self._settings.cookies_file)]
        elif self._settings.cookies_from_browser:
            command += ["--cookies-from-browser", self._settings.cookies_from_browser]
        if self._settings.proxy:
            command += ["--proxy", self._settings.proxy]
        if self._settings.max_download_rate:
            command += ["--limit-rate", self._settings.max_download_rate]
        command.append(url)
        return command

    def _fetch_sync(self, url: str, progress: Progress) -> PhotoAlbum:
        ensure_disk_space(self._settings)
        workdir = Path(tempfile.mkdtemp(prefix="vd-img-", dir=self._settings.download_dir))
        try:
            progress.reset("Забираю картинки из поста…")
            try:
                result = subprocess.run(
                    self._command(url, workdir),
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise NoPhotosFound("Сервис слишком долго не отвечал.") from exc
            except OSError as exc:
                raise NoPhotosFound(
                    "Не удалось запустить gallery-dl. Проверьте, что он установлен: "
                    "pip install -r requirements.txt",
                    detail=str(exc),
                ) from exc

            # gallery-dl возвращает ненулевой код и когда часть файлов всё же
            # скачалась, поэтому сначала смотрим на результат, а не на код.
            images = self._collect(workdir)
            if not images:
                raise self._translate(result.stderr or result.stdout, url)

            usable: list[Path] = []
            skipped = 0
            for image in images:
                if image.stat().st_size <= PHOTO_MAX_BYTES:
                    usable.append(image)
                else:
                    skipped += 1

            if not usable:
                raise NoPhotosFound(
                    "Картинки в посте тяжелее 10 МБ — Telegram такие не принимает."
                )

            progress.reset("Готовлю картинки к отправке…")
            return PhotoAlbum(
                paths=usable,
                workdir=workdir,
                source_url=url,
                skipped=skipped,
            )
        except BaseException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

    @staticmethod
    def _collect(outdir: Path) -> list[Path]:
        """Картинки в порядке, в котором их разложил gallery-dl."""
        images = [
            path
            for path in outdir.rglob("*")
            if path.is_file() and path.suffix.lower() in _IMAGE_EXTS
        ]
        images.sort(key=lambda path: (str(path.parent), path.name))
        return images[:MAX_ALBUM_ITEMS]

    @staticmethod
    def _translate(stderr: str, url: str) -> DownloaderError:
        """Превращает вывод gallery-dl в понятное сообщение."""
        text = clean_error_text(stderr)
        detail = text[:600] or None

        if looks_like_auth_error(text):
            return AuthRequired(
                "🔒 Этот пост доступен только авторизованным — скачать не выйдет.",
                detail=detail,
                hint="Сервис требует вход. Помогут cookies — см. README.",
            )
        if "No suitable extractor" in text or "Unsupported URL" in text:
            return NoPhotosFound(
                "С этой ссылки картинки скачать не получится.", detail=detail
            )
        return NoPhotosFound(
            "В этом посте не нашлось ни видео, ни картинок.",
            detail=detail,
            hint="Если это Instagram, скорее всего нужны cookies — см. README.",
        )
