"""Загрузка медиа через yt-dlp.

yt-dlp синхронный и блокирующий, поэтому вся работа уходит в поток
через :func:`asyncio.to_thread`, а прогресс складывается в объект
:class:`Progress`, который хендлер периодически читает и показывает
пользователю.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from bot.config import Settings
from bot.services.extractor_args import parse_extractor_args
from bot.utils.rate import parse_rate

log = logging.getLogger(__name__)

#: Расширения, которые yt-dlp складывает рядом как обложки.
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

#: Технические файлы, которые не являются результатом загрузки.
_JUNK_EXTS = frozenset({".part", ".ytdl", ".json", ".description", ".txt", ".vtt", ".srt"})

#: Лимит Telegram на превью: JPEG не больше 200 КБ и 320 px по стороне.
_THUMB_MAX_BYTES = 200 * 1024

#: Чем ниже по списку, тем хуже качество. Идём вниз, пока файл не влезет
#: в лимит Telegram.
#: Ступени качества, по которым бот спускается, если файл не влез.
#:
#: Ограничение считается по высоте кадра, а у вертикальных роликов —
#: а это почти весь Instagram и TikTok — высота является длинной
#: стороной. Поэтому потолок 720 режет их заметно сильнее, чем обычное
#: горизонтальное видео: кадр 1080x1920 превращается в 405x720.
_FALLBACK_HEIGHTS = (1080, 720, 480, 360)


def build_video_ladder(max_height: int, size_limit: int) -> tuple[str, ...]:
    """
    Строит набор форматов от лучшего к худшему.

    Раньше первая ступень просто брала 1080p, и если готовый файл не
    влезал в лимит, он удалялся и всё скачивалось заново ступенью ниже.
    Один ролик мог проехать по каналу дважды, а то и трижды. Теперь в
    каждую ступень встроен фильтр по размеру, поэтому заведомо лишние
    форматы отсеиваются до загрузки, а не после.

    Запись ``<?`` означает «пропусти формат, размер которого известен и
    велик, но не отбрасывай те, где размер неизвестен»: иначе на
    сервисах, которые размер не сообщают, не осталось бы ни одного
    подходящего формата.
    """
    megabytes = max(1, size_limit // (1024 * 1024))
    fits = f"[filesize_approx<?{megabytes}M]"

    heights: list[int] = []
    for height in (max_height, *_FALLBACK_HEIGHTS):
        if height <= max_height and height not in heights:
            heights.append(height)

    ladder = []
    for height in heights:
        cap = f"[height<=?{height}]"
        ladder.append(
            # H.264 со звуком AAC — то, что Telegram играет без перекодирования
            f"bv*{cap}{fits}[vcodec^=avc1]+ba[acodec^=mp4a]/"
            f"bv*{cap}{fits}+ba/"
            f"b{cap}{fits}"
        )
    ladder.append("worst[ext=mp4]/worst")
    return tuple(ladder)

#: Кодеки, которые проигрывает встроенный плеер Telegram на всех
#: платформах. VP9, AV1 и часть HEVC он показывает как замерший первый
#: кадр со звуком, поэтому такое видео приходится перекодировать.
_TELEGRAM_VIDEO_CODEC = "h264"
_TELEGRAM_AUDIO_CODECS = frozenset({"aac", "mp3"})

#: Битрейт mp3 для аудио: тоже с понижением, если не влезаем в лимит.
_AUDIO_LADDER: tuple[str, ...] = ("192", "128", "96")

KIND_VIDEO = "video"
KIND_AUDIO = "audio"


class DownloaderError(Exception):
    """Ошибка загрузки, текст которой можно показать пользователю.

    В ``detail`` остаётся исходное сообщение yt-dlp: пользователю его
    показывать незачем, а администратору оно экономит поход в логи.
    """

    def __init__(
        self,
        message: str,
        *,
        detail: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.detail = detail
        #: Что делать владельцу бота. Пользователю это бесполезно: он не
        #: может ни настроить cookies, ни заглянуть в README.
        self.hint = hint


class MediaTooLong(DownloaderError):
    """Ролик длиннее разрешённого лимита."""


class MediaTooLarge(DownloaderError):
    """Файл не влезает в лимит Telegram даже на минимальном качестве."""


class AuthRequired(DownloaderError):
    """Сервис требует авторизации (приватный аккаунт, проверка на робота)."""


class ExtractionFailed(DownloaderError):
    """Не удалось получить медиа по ссылке."""


class NotEnoughSpace(DownloaderError):
    """На диске кончается место.

    Бот может делить сервер с чем-то более важным, поэтому занимать
    последние гигабайты нельзя: пострадает не только загрузка.
    """


class NoVideoStream(DownloaderError):
    """В посте нет видеодорожки — фотокарусель или слайдшоу.

    TikTok на такие посты отдаёт только звук, Instagram пропускает
    картинки совсем. Отправлять аудио под видом видео бессмысленно:
    Telegram такой файл не примет.
    """


@dataclass(slots=True)
class Progress:
    """Состояние текущей загрузки. Пишется из рабочего потока."""

    stage: str = "Подключаюсь к сервису…"
    percent: float | None = None
    downloaded: int | None = None
    total: int | None = None
    speed: float | None = None
    eta: int | None = None

    def reset(self, stage: str) -> None:
        self.stage = stage
        self.percent = None
        self.downloaded = None
        self.total = None
        self.speed = None
        self.eta = None


@dataclass(slots=True)
class Media:
    """Готовый к отправке файл и его метаданные."""

    path: Path
    workdir: Path
    kind: str
    title: str
    size: int
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    thumbnail: Path | None = None
    uploader: str | None = None
    webpage_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def cleanup(self) -> None:
        """Удаляет временный каталог загрузки."""
        shutil.rmtree(self.workdir, ignore_errors=True)


def _first_entry(info: dict[str, Any] | None) -> dict[str, Any]:
    """Разворачивает плейлист до первого видео."""
    current = info
    seen = 0
    while isinstance(current, dict) and current.get("_type") in {"playlist", "multi_video"}:
        entries = current.get("entries")
        if entries is None:
            break
        if not isinstance(entries, list):
            entries = list(entries)
        if not entries:
            break
        current = entries[0]
        seen += 1
        if seen > 5:  # защита от вложенных плейлистов
            break
    if not isinstance(current, dict):
        raise ExtractionFailed("Сервис не вернул информацию о видео.")
    return current


#: Признаки того, что сервис требует авторизации.
#:
#: Формулировки намеренно длинные. Раньше здесь были короткие подстроки,
#: и "429" совпадал с цифрами внутри девятнадцатизначных идентификаторов
#: TikTok: любая ошибка на таком ролике выглядела как требование входа.
#: По той же причине ушли "account" и "cookies" — слишком общие слова.
_AUTH_NEEDLES = (
    "login required",
    "requested content is not available",
    "sign in to confirm",
    "sign in if you",
    "private video",
    "this video is private",
    "private account",
    "account is private",
    "age-restricted",
    "confirm your age",
    "not a bot",
    "empty media response",
    "http error 401",
    "http error 403",
    "http error 429",
    "too many requests",
    "rate-limit reached",
    "checkpoint required",
    "use --cookies",
    "unable to extract shared data",
)


def looks_like_auth_error(message: str) -> bool:
    """Похоже ли, что сервис просит авторизацию."""
    lowered = message.lower()
    return any(needle in lowered for needle in _AUTH_NEEDLES)


#: Формулировок у «видео тут нет» несколько, и отличаются они мелочами:
#: Instagram отвечает "There is no video in this post", а на других
#: сервисах встречается "No video formats found!". Поэтому ищем общий
#: кусок, а не точные фразы.
_NO_VIDEO_NEEDLES = (
    "no video",
    "no media found",
    "no formats found",
)


def _looks_like_no_video(message: str) -> bool:
    """Пост есть, но видео в нём нет — например, фотокарусель."""
    lowered = message.lower()
    return any(needle in lowered for needle in _NO_VIDEO_NEEDLES)


#: Цветовые последовательности терминала: yt-dlp подмешивает их в текст
#: ошибок, и в сообщении Telegram они выглядят как мусор вида "[0;31m".
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def clean_error_text(text: str) -> str:
    """Убирает управляющие последовательности и лишние пробелы."""
    return _ANSI_RE.sub("", text or "").strip()


def ensure_disk_space(settings: Settings) -> None:
    """Не даёт боту забить диск до конца."""
    try:
        free = shutil.disk_usage(settings.download_dir).free
    except OSError:
        log.warning("Не удалось узнать свободное место", exc_info=True)
        return
    if free < settings.min_free_disk_bytes:
        log.error(
            "Мало места: свободно %.0f МБ при пороге %d МБ",
            free / 1024 / 1024,
            settings.min_free_disk_mb,
        )
        raise NotEnoughSpace(
            "На сервере кончается место на диске. Попробуйте позже — "
            "владелец бота уже видит это в журнале."
        )


def _has_ffprobe() -> bool:
    """Есть ли ffprobe — без него про кодеки ничего сказать нельзя."""
    return shutil.which("ffprobe") is not None


def _probe_codecs(path: Path) -> tuple[str | None, str | None]:
    """Кодеки первой видео- и аудиодорожки. ``None`` — ffprobe недоступен."""

    def stream_codec(selector: str) -> str | None:
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", selector,
                    "-show_entries", "stream=codec_name",
                    "-of", "default=nw=1:nk=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        lines = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
        return lines[0] if lines else None

    return stream_codec("v:0"), stream_codec("a:0")


class Downloader:
    """Фасад над yt-dlp с лимитами и понижением качества."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)

    # ── публичный API ────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._semaphore.locked()

    async def probe(self, url: str) -> dict[str, Any]:
        """Метаданные без загрузки файла."""
        return await asyncio.to_thread(self._probe_sync, url)

    async def fetch(self, url: str, kind: str, progress: Progress) -> Media:
        """
        Скачивает видео или аудио по ссылке.

        Пока все слоты заняты, ждёт очереди — поэтому бот не захлёбывается
        от нескольких одновременных ссылок.
        """
        if self._semaphore.locked():
            progress.reset("В очереди: ждём свободный слот…")
        async with self._semaphore:
            return await asyncio.to_thread(self._fetch_sync, url, kind, progress)

    # ── настройки yt-dlp ─────────────────────────────────────────────

    def _base_opts(self, outdir: Path) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "outtmpl": str(outdir / "%(title).70B [%(id)s].%(ext)s"),
            "paths": {"home": str(outdir), "temp": str(outdir)},
            "noplaylist": True,
            "playlist_items": "1",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "ignoreerrors": False,
            "retries": 3,
            "fragment_retries": 5,
            "extractor_retries": 2,
            "socket_timeout": 30,
            # Чем больше кусков качается разом, тем «рванее» нагрузка на
            # канал. Для сервера, который делит полосу с другими, ровный
            # поток важнее пиковой скорости.
            "concurrent_fragment_downloads": 2,
            "windowsfilenames": True,
            "trim_file_name": 80,
            "overwrites": True,
            "geo_bypass": True,
            "logger": _YtdlpLogger(),
        }
        if self._settings.cookies_file is not None:
            opts["cookiefile"] = str(self._settings.cookies_file)
        elif self._settings.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self._settings.cookies_from_browser,)
        if self._settings.proxy:
            opts["proxy"] = self._settings.proxy
        rate = parse_rate(self._settings.max_download_rate)
        if rate:
            # Оставляем полосу соседям по серверу: без ограничения одна
            # загрузка забирает весь канал.
            opts["ratelimit"] = rate
        extractor_args = parse_extractor_args(self._settings.extractor_args)
        if extractor_args:
            opts["extractor_args"] = extractor_args
        return opts

    def _probe_sync(self, url: str) -> dict[str, Any]:
        opts = self._base_opts(self._settings.download_dir) | {"skip_download": True}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except (DownloadError, ExtractorError) as exc:
            raise self._translate(exc) from exc
        return _first_entry(info)

    # ── загрузка ─────────────────────────────────────────────────────

    def _fetch_sync(self, url: str, kind: str, progress: Progress) -> Media:
        ensure_disk_space(self._settings)
        workdir = Path(tempfile.mkdtemp(prefix="vd-", dir=self._settings.download_dir))
        try:
            progress.reset("Получаю информацию о видео…")
            if kind == KIND_AUDIO:
                return self._download_audio(url, workdir, progress)
            return self._download_video(url, workdir, progress)
        except BaseException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

    def _duration_filter(self, rejected: list[float]):
        """
        Фильтр yt-dlp, отсекающий слишком длинные ролики.

        Он срабатывает до начала скачивания, поэтому отдельный запрос
        метаданных не нужен: раньше их приходилось тянуть дважды, и на
        TikTok с Instagram это заметно повышало шанс словить лимит частоты.
        """
        max_seconds = self._settings.max_duration_seconds

        def match(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
            duration = info.get("duration")
            is_number = isinstance(duration, (int, float)) and not isinstance(duration, bool)
            if is_number and duration > max_seconds:
                rejected.append(float(duration))
                return f"ролик длиннее {max_seconds} с"
            return None

        return match

    def _too_long(self, rejected: list[float]) -> MediaTooLong:
        minutes = rejected[0] / 60 if rejected else 0
        return MediaTooLong(
            f"Ролик идёт {minutes:.0f} мин, а лимит — "
            f"{self._settings.max_duration_minutes} мин."
        )

    def _download_video(self, url: str, workdir: Path, progress: Progress) -> Media:
        limit = self._settings.max_file_size_bytes
        last_error: Exception | None = None
        oversize_mb: float | None = None

        ladder = build_video_ladder(
            self._settings.max_video_height, self._settings.max_file_size_bytes
        )
        for attempt, fmt in enumerate(ladder, start=1):
            attempt_dir = workdir / f"try{attempt}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            progress.reset(
                "Скачиваю видео…" if attempt == 1 else f"Понижаю качество (попытка {attempt})…"
            )

            rejected: list[float] = []
            opts = self._base_opts(attempt_dir) | {
                "format": fmt,
                # Разрешение подбирается ближайшее снизу к потолку, а при
                # равном разрешении предпочитается H.264: его Telegram
                # играет сразу, и перекодирование не тратит процессор.
                "format_sort": [
                    f"res:{self._settings.max_video_height}",
                    "vcodec:h264",
                    "acodec:aac",
                    "+size",
                ],
                "match_filter": self._duration_filter(rejected),
                "merge_output_format": "mp4",
                "writethumbnail": True,
                "max_filesize": limit,
                "progress_hooks": [_make_hook(progress)],
                "postprocessors": [
                    {"key": "FFmpegMetadata", "add_metadata": True},
                ],
            }

            try:
                with YoutubeDL(opts) as ydl:
                    raw = ydl.extract_info(url, download=True)
            except (DownloadError, ExtractorError) as exc:
                if rejected:
                    raise self._too_long(rejected) from exc
                translated = self._translate(exc, url)
                if isinstance(translated, (AuthRequired, NoVideoStream)):
                    raise translated from exc
                last_error = translated
                log.info("Формат %r не подошёл: %s", fmt, exc)
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            if rejected:
                raise self._too_long(rejected)
            info = _first_entry(raw)

            media_path = self._pick_media_file(attempt_dir, info)
            if media_path is None:
                last_error = ExtractionFailed("yt-dlp не создал файл.")
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            if _has_ffprobe() and _probe_codecs(media_path)[0] is None:
                raise NoVideoStream(
                    "В этом посте нет видеодорожки — это фотокарусель или слайдшоу."
                )

            progress.reset("Готовлю файл к отправке…")
            media_path = self._make_telegram_friendly(media_path)

            size = media_path.stat().st_size
            if size > limit:
                oversize_mb = size / 1024 / 1024
                log.info("Файл %.1f МБ больше лимита, пробую хуже", oversize_mb)
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            return Media(
                path=media_path,
                workdir=workdir,
                kind=KIND_VIDEO,
                title=_title_of(info, {}),
                size=size,
                duration=_int_or_none(info.get("duration")),
                width=_int_or_none(info.get("width")),
                height=_int_or_none(info.get("height")),
                thumbnail=self._prepare_thumbnail(attempt_dir),
                uploader=info.get("uploader"),
                webpage_url=info.get("webpage_url") or url,
            )

        if oversize_mb is not None:
            raise MediaTooLarge(
                f"Даже в минимальном качестве файл весит ~{oversize_mb:.0f} МБ, "
                f"а лимит отправки — {self._settings.max_file_size_mb} МБ."
            )
        raise last_error or ExtractionFailed("Не удалось подобрать формат для скачивания.")

    def _download_audio(self, url: str, workdir: Path, progress: Progress) -> Media:
        limit = self._settings.max_file_size_bytes
        last_error: Exception | None = None
        oversize_mb: float | None = None

        for attempt, quality in enumerate(_AUDIO_LADDER, start=1):
            attempt_dir = workdir / f"audio{attempt}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            progress.reset(
                "Скачиваю аудио…" if attempt == 1 else f"Сжимаю сильнее ({quality} kbps)…"
            )

            rejected: list[float] = []
            opts = self._base_opts(attempt_dir) | {
                "format": "ba/b",
                "match_filter": self._duration_filter(rejected),
                "writethumbnail": True,
                "progress_hooks": [_make_hook(progress)],
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": quality,
                    },
                    {"key": "FFmpegMetadata", "add_metadata": True},
                ],
            }

            try:
                with YoutubeDL(opts) as ydl:
                    raw = ydl.extract_info(url, download=True)
            except (DownloadError, ExtractorError) as exc:
                if rejected:
                    raise self._too_long(rejected) from exc
                translated = self._translate(exc, url)
                if isinstance(translated, (AuthRequired, NoVideoStream)):
                    raise translated from exc
                last_error = translated
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            if rejected:
                raise self._too_long(rejected)
            info = _first_entry(raw)

            media_path = self._pick_media_file(attempt_dir, info, prefer_ext=".mp3")
            if media_path is None:
                last_error = ExtractionFailed(
                    "Не удалось извлечь звук — вероятно, в системе нет ffmpeg."
                )
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            size = media_path.stat().st_size
            if size > limit:
                oversize_mb = size / 1024 / 1024
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            progress.reset("Готовлю файл к отправке…")
            return Media(
                path=media_path,
                workdir=workdir,
                kind=KIND_AUDIO,
                title=_title_of(info, {}),
                size=size,
                duration=_int_or_none(info.get("duration")),
                thumbnail=self._prepare_thumbnail(attempt_dir),
                uploader=info.get("uploader"),
                webpage_url=info.get("webpage_url") or url,
            )

        if oversize_mb is not None:
            raise MediaTooLarge(
                f"Аудио весит ~{oversize_mb:.0f} МБ даже при 96 kbps, "
                f"а лимит отправки — {self._settings.max_file_size_mb} МБ."
            )
        raise last_error or ExtractionFailed("Не удалось скачать аудиодорожку.")

    # ── вспомогательное ──────────────────────────────────────────────

    @staticmethod
    def _pick_media_file(
        outdir: Path,
        info: dict[str, Any],
        *,
        prefer_ext: str | None = None,
    ) -> Path | None:
        """
        Находит итоговый файл.

        Сначала смотрим, что сказал сам yt-dlp (после постобработки путь
        меняется), иначе берём самый крупный медиафайл в каталоге.
        """
        for requested in info.get("requested_downloads") or ():
            raw = requested.get("filepath") or requested.get("_filename")
            if raw:
                candidate = Path(raw)
                if candidate.is_file() and candidate.suffix.lower() not in _IMAGE_EXTS:
                    return candidate

        candidates = [
            item
            for item in outdir.rglob("*")
            if item.is_file()
            and item.suffix.lower() not in _IMAGE_EXTS
            and item.suffix.lower() not in _JUNK_EXTS
        ]
        if not candidates:
            return None
        if prefer_ext:
            preferred = [item for item in candidates if item.suffix.lower() == prefer_ext]
            if preferred:
                candidates = preferred
        return max(candidates, key=lambda item: item.stat().st_size)

    @staticmethod
    def _make_telegram_friendly(path: Path) -> Path:
        """
        Приводит видео к H.264/AAC и включает faststart.

        Telegram показывает VP9 и AV1 как замерший первый кадр со звуком —
        встроенный плеер их не декодирует. Поэтому несовместимое видео
        перекодируем, а совместимое только перекладываем в контейнер
        с faststart, чтобы оно начинало играть до полной загрузки.

        Любая осечка ffmpeg не критична: возвращаем исходный файл.
        """
        video_codec, audio_codec = _probe_codecs(path)
        if video_codec is None:
            return path

        target = path.with_name(f"tg-{path.stem}.mp4")
        if video_codec != _TELEGRAM_VIDEO_CODEC:
            log.info("Перекодирую видео %s -> h264 для Telegram", video_codec)
            command = [
                "ffmpeg", "-y", "-loglevel", "error",
                # Оставляем ядра соседям по серверу: перекодирование иначе
                # занимает процессор целиком и всё остальное начинает ждать.
                "-threads", "2",
                "-i", str(path),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(target),
            ]
            timeout = 1800
        else:
            audio_args = (
                ["-c:a", "copy"]
                if audio_codec in _TELEGRAM_AUDIO_CODECS
                else ["-c:a", "aac", "-b:a", "128k"]
            )
            command = [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                "-c:v", "copy", *audio_args,
                "-movflags", "+faststart", str(target),
            ]
            timeout = 300

        try:
            subprocess.run(
                command,
                check=True,
                timeout=timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            log.warning("ffmpeg не смог подготовить видео для Telegram", exc_info=True)
            return path

        if target.is_file() and target.stat().st_size > 0:
            return target
        return path

    @staticmethod
    def _prepare_thumbnail(outdir: Path) -> Path | None:
        """Сжимает обложку до требований Telegram. Необязательный шаг."""
        images = [
            item
            for item in outdir.rglob("*")
            if item.is_file() and item.suffix.lower() in _IMAGE_EXTS
        ]
        if not images:
            return None
        source = max(images, key=lambda item: item.stat().st_size)
        target = outdir / "telegram-thumb.jpg"
        try:
            # Список аргументов фиксирован, shell не используется.
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(source),
                    "-vf", "scale=320:-1",
                    "-frames:v", "1",
                    "-q:v", "6",
                    str(target),
                ],
                check=True,
                timeout=60,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            log.debug("Не удалось подготовить превью из %s", source, exc_info=True)
            return None
        if target.is_file() and 0 < target.stat().st_size <= _THUMB_MAX_BYTES:
            return target
        return None

    def _translate(self, exc: Exception, url: str | None = None) -> DownloaderError:
        """Превращает ошибку yt-dlp в понятное пользователю сообщение."""
        message = str(exc)
        detail = clean_error_text(message)[:600] or None
        host = (url or "").lower()

        if "File is larger than max-filesize" in message:
            return MediaTooLarge(
                f"Файл больше лимита в {self._settings.max_file_size_mb} МБ.",
                detail=detail,
            )
        if _looks_like_no_video(message):
            # Не ошибка, а развилка: дальше вызывающий код пойдёт за картинками.
            return NoVideoStream(
                "В этом посте нет видеодорожки — это фотокарусель или слайдшоу.",
                detail=detail,
            )
        if looks_like_auth_error(message):
            return AuthRequired(
                "🔒 Это видео доступно только авторизованным — скачать не выйдет.\n"
                "Бывает с закрытыми аккаунтами и роликами с возрастным ограничением.",
                detail=detail,
                hint="Сервис требует вход. Помогут cookies — см. README.",
            )
        if "Unsupported URL" in message or "no suitable" in message.lower():
            return ExtractionFailed(
                "Эту ссылку скачать не получится — сервис не поддерживается.",
                detail=detail,
            )
        if "Video unavailable" in message or "404" in message:
            return ExtractionFailed("Видео недоступно или удалено.", detail=detail)
        if "instagram" in host:
            return ExtractionFailed(
                "Instagram не отдал это видео. Попробуйте ссылку на сам ролик, "
                "а не на историю или подборку.",
                detail=detail,
                hint="Для Instagram почти всегда нужны cookies — см. README.",
            )
        return ExtractionFailed(
            "Не удалось скачать видео. Проверьте ссылку и попробуйте снова.",
            detail=detail,
        )


class _YtdlpLogger:
    """Перекидывает логи yt-dlp в стандартный logging."""

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


def _make_hook(progress: Progress):
    """Хук прогресса yt-dlp, который только обновляет общее состояние."""

    def hook(status: dict[str, Any]) -> None:
        state = status.get("status")
        if state == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            downloaded = status.get("downloaded_bytes")
            progress.stage = "Скачиваю…"
            progress.downloaded = _int_or_none(downloaded)
            progress.total = _int_or_none(total)
            progress.speed = status.get("speed")
            progress.eta = _int_or_none(status.get("eta"))
            if total and downloaded:
                progress.percent = min(100.0, downloaded / total * 100)
        elif state == "finished":
            progress.reset("Склеиваю видео и звук…")

    return hook


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _title_of(info: dict[str, Any], fallback: dict[str, Any]) -> str:
    for source in (info, fallback):
        title = source.get("title") or source.get("fulltitle") or source.get("description")
        if title:
            return str(title).strip().splitlines()[0][:200]
    return "Видео"
