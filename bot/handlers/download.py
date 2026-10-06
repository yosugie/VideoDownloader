"""Основной сценарий: прислал ссылку — получил файл.

Выбирать формат не нужно. Бот пробует скачать видео, а если видеодорожки
в посте нет (фотокарусель Instagram, слайдшоу TikTok) — забирает картинки.
Звук отдельно можно попросить командой /mp3.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from bot.config import Settings
from bot.keyboards import DownloadAction, retry_as_audio
from bot.services.downloader import (
    KIND_AUDIO,
    KIND_VIDEO,
    Downloader,
    DownloaderError,
    Media,
    MediaTooLarge,
    NoVideoStream,
    Progress,
)
from bot.services.links import (
    SUPPORTED_PLATFORMS,
    find_url,
    normalize_host,
    resolve_platform,
)
from bot.services.photos import PhotoAlbum, PhotoDownloader
from bot.services.stats import STATUS_ERROR, STATUS_OK, Stats
from bot.utils.cache import PendingLinks
from bot.utils.format import render_progress
from bot.utils.quota import DailyQuota
from bot.utils.state import RuntimeState

log = logging.getLogger(__name__)

router = Router(name="download")

#: Как часто обновляем сообщение с прогрессом. Чаще нельзя — Telegram
#: ограничивает частоту правок одного сообщения.
_PROGRESS_INTERVAL = 3.5

#: Что бот скачивает. KIND_VIDEO при необходимости сам перейдёт к фото.
KIND_PHOTOS = "photos"

Payload = Media | PhotoAlbum


# ── точки входа ──────────────────────────────────────────────────────


def _user_id(message: Message) -> int:
    return message.from_user.id if message.from_user else 0


@router.message(F.text, ~F.text.startswith("/"))
async def on_link(
    message: Message,
    settings: Settings,
    downloader: Downloader,
    photos: PhotoDownloader,
    links: PendingLinks,
    quota: DailyQuota,
    stats: Stats,
    runtime: RuntimeState,
) -> None:
    """Ссылка в сообщении — сразу начинаем скачивать."""
    if runtime.paused and not settings.is_admin(_user_id(message)):
        await message.answer(
            "⏸ Бот временно на паузе — владелец ненадолго его отключил.\n"
            "Попробуйте позже."
        )
        return

    url = find_url(message.text)
    if url is None:
        await message.answer(
            "Пришлите ссылку на видео или пост — я скачаю.\n\n"
            "Поддерживаю:\n"
            + "\n".join(f"  • {platform}" for platform in SUPPORTED_PLATFORMS)
            + "\n\nСправка: /help"
        )
        return

    platform = resolve_platform(url, allow_any_site=settings.allow_any_site)
    if platform is None:
        await message.answer(
            "🤔 Эту ссылку я не поддерживаю.\n\n"
            "Умею скачивать из:\n"
            + "\n".join(f"  • {item}" for item in SUPPORTED_PLATFORMS)
        )
        return

    status = await message.answer(f"{platform} — принято.\n⏳ Начинаю…")
    await _handle(
        status,
        url,
        KIND_VIDEO,
        _user_id(message),
        settings,
        downloader,
        photos,
        links,
        quota,
        stats,
    )


@router.message(Command("mp3"))
async def cmd_mp3(
    message: Message,
    command: CommandObject,
    settings: Settings,
    downloader: Downloader,
    photos: PhotoDownloader,
    links: PendingLinks,
    quota: DailyQuota,
    stats: Stats,
    runtime: RuntimeState,
) -> None:
    """Забрать только звуковую дорожку."""
    if runtime.paused and not settings.is_admin(_user_id(message)):
        await message.answer("⏸ Бот временно на паузе. Попробуйте позже.")
        return

    url = find_url(command.args)
    if url is None:
        await message.answer(
            "Пришлите ссылку вместе с командой:\n"
            "<code>/mp3 https://youtu.be/…</code>"
        )
        return

    if resolve_platform(url, allow_any_site=settings.allow_any_site) is None:
        await message.answer("🤔 Эту ссылку я не поддерживаю.")
        return

    status = await message.answer("🎵 Принято.\n⏳ Достаю звук…")
    await _handle(
        status,
        url,
        KIND_AUDIO,
        _user_id(message),
        settings,
        downloader,
        photos,
        links,
        quota,
        stats,
    )


@router.callback_query(DownloadAction.filter())
async def on_retry(
    callback: CallbackQuery,
    callback_data: DownloadAction,
    settings: Settings,
    downloader: Downloader,
    photos: PhotoDownloader,
    links: PendingLinks,
    quota: DailyQuota,
    stats: Stats,
) -> None:
    """Запасная кнопка «только звук»."""
    await callback.answer()
    status = callback.message
    if not isinstance(status, Message):
        return

    pending = links.get(callback_data.token)
    if pending is None:
        with contextlib.suppress(TelegramBadRequest):
            await status.edit_text("⌛️ Ссылка устарела — пришлите её ещё раз.")
        return

    with contextlib.suppress(TelegramBadRequest):
        await status.edit_text("🎵 Достаю звук…", reply_markup=None)
    user_id = callback.from_user.id if callback.from_user else 0
    await _handle(
        status,
        pending.url,
        KIND_AUDIO,
        user_id,
        settings,
        downloader,
        photos,
        links,
        quota,
        stats,
    )


# ── общий конвейер ───────────────────────────────────────────────────


async def _handle(
    status: Message,
    url: str,
    kind: str,
    user_id: int,
    settings: Settings,
    downloader: Downloader,
    photos: PhotoDownloader,
    links: PendingLinks,
    quota: DailyQuota,
    stats: Stats,
) -> None:
    """Скачивает и отправляет, показывая прогресс и разбирая ошибки."""
    if not settings.is_admin(user_id) and not quota.allow(user_id):
        with contextlib.suppress(TelegramBadRequest):
            await status.edit_text(
                f"🚦 На сегодня хватит: лимит {quota.limit} загрузок в сутки.\n"
                "Счётчик обнулится завтра."
            )
        return

    # По этой строке видно, кто пользуется ботом: без неё некого вносить
    # в BLOCKED_USER_IDS. Полная ссылка пишется только на уровне DEBUG.
    log.info("Загрузка: пользователь=%s вид=%s сайт=%s", user_id, kind, normalize_host(url))
    log.debug("Ссылка пользователя %s: %s", user_id, url)

    started = time.monotonic()
    host = normalize_host(url)

    def note(
        outcome: str,
        *,
        size: int = 0,
        error: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Отмечает исход в учёте. Сбой учёта работе бота не мешает."""
        stats.record(
            user_id=user_id,
            kind=kind,
            host=host,
            url=url,
            size=size,
            seconds=round(time.monotonic() - started, 1),
            status=outcome,
            error=error,
            detail=detail,
        )

    progress = Progress()
    reporter = asyncio.create_task(_report_progress(status, progress))
    payload: Payload | None = None

    try:
        payload = await _download(url, kind, progress, downloader, photos)
    except MediaTooLarge as exc:
        await _stop(reporter)
        note(STATUS_ERROR, error=type(exc).__name__, detail=exc.detail)
        await _fail(
            status,
            f"😕 {escape(str(exc))}",
            exc,
            user_id,
            settings,
            _offer_audio(url, kind, links),
        )
        return
    except DownloaderError as exc:
        await _stop(reporter)
        note(STATUS_ERROR, error=type(exc).__name__, detail=exc.detail)
        # Ссылка не сработала — попытку возвращаем, она не виновата.
        quota.refund(user_id)
        await _fail(status, f"❌ {escape(str(exc))}", exc, user_id, settings)
        return
    except Exception:
        await _stop(reporter)
        note(STATUS_ERROR, error="Непредвиденная ошибка")
        log.exception("Непредвиденная ошибка при загрузке %s", url)
        with contextlib.suppress(TelegramBadRequest):
            await status.edit_text(
                "💥 Что-то сломалось на моей стороне. Попробуйте ещё раз чуть позже."
            )
        return
    finally:
        await _stop(reporter)

    size = payload.size

    try:
        if isinstance(payload, PhotoAlbum):
            await _send_album(status, payload, settings)
        else:
            await _send_media(status, payload, settings)
    except TelegramEntityTooLarge:
        note(STATUS_ERROR, error="TelegramEntityTooLarge")
        with contextlib.suppress(TelegramBadRequest):
            await status.edit_text(
                f"😕 Telegram не принял файл: он больше {settings.max_file_size_mb} МБ."
            )
        return
    except TelegramBadRequest as exc:
        log.warning("Telegram отклонил файл: %s", exc)
        note(STATUS_ERROR, error="TelegramBadRequest")
        with contextlib.suppress(TelegramBadRequest):
            await status.edit_text("❌ Telegram не принял файл. Попробуйте ещё раз.")
        return
    finally:
        payload.cleanup()

    note(STATUS_OK, size=size)

    with contextlib.suppress(TelegramBadRequest):
        await status.delete()


async def _download(
    url: str,
    kind: str,
    progress: Progress,
    downloader: Downloader,
    photos: PhotoDownloader,
) -> Payload:
    """Видео, звук или картинки — в зависимости от того, что в посте есть."""
    if kind == KIND_PHOTOS:
        return await photos.fetch(url, progress)
    if kind == KIND_AUDIO:
        return await downloader.fetch(url, KIND_AUDIO, progress)

    try:
        return await downloader.fetch(url, KIND_VIDEO, progress)
    except NoVideoStream:
        # Фотокарусель или слайдшоу: видеодорожки нет, зато есть картинки.
        progress.reset("Видео в посте нет — забираю картинки…")
        return await photos.fetch(url, progress)


def _offer_audio(url: str, kind: str, links: PendingLinks):
    """Клавиатура «только звук» — имеет смысл лишь когда качали видео."""
    if kind != KIND_VIDEO:
        return None
    return retry_as_audio(links.put(url, "retry"))


async def _fail(
    status: Message,
    text: str,
    exc: DownloaderError,
    user_id: int,
    settings: Settings,
    markup=None,
) -> None:
    """
    Сообщает об ошибке.

    Пользователь видит только то, что его касается. Совет про cookies и
    исходный текст от yt-dlp уходят владельцу: настроить их всё равно
    может только он.
    """
    if settings.is_admin(user_id):
        if exc.hint:
            text += f"\n\nℹ️ {escape(exc.hint)}"
        if exc.detail:
            text += f"\n\n<code>{escape(exc.detail)}</code>"
    with contextlib.suppress(TelegramBadRequest):
        await status.edit_text(text, reply_markup=markup)


# ── отправка ─────────────────────────────────────────────────────────


async def _signature(bot: Bot) -> str:
    """Подпись под файлами — только имя бота."""
    me = await bot.me()
    return f"@{me.username}" if me.username else ""


async def _send_media(status: Message, media: Media, settings: Settings) -> None:
    """Отправляет видео или аудио с метаданными для нормального превью."""
    with contextlib.suppress(TelegramBadRequest):
        await status.edit_text("📤 Отправляю файл…")

    bot = status.bot
    chat_id = status.chat.id
    caption = await _signature(bot)
    thumbnail = FSInputFile(media.thumbnail) if media.thumbnail else None
    action = ChatAction.UPLOAD_VOICE if media.kind == KIND_AUDIO else ChatAction.UPLOAD_VIDEO

    async with ChatActionSender(bot=bot, chat_id=chat_id, action=action):
        if media.kind == KIND_AUDIO:
            await bot.send_audio(
                chat_id=chat_id,
                audio=FSInputFile(media.path, filename=_safe_filename(media, ".mp3")),
                caption=caption,
                title=media.title[:64],
                performer=(media.uploader or None),
                duration=media.duration,
                thumbnail=thumbnail,
                request_timeout=settings.upload_timeout,
            )
        else:
            await bot.send_video(
                chat_id=chat_id,
                video=FSInputFile(media.path, filename=_safe_filename(media, ".mp4")),
                caption=caption,
                duration=media.duration,
                width=media.width,
                height=media.height,
                thumbnail=thumbnail,
                supports_streaming=True,
                request_timeout=settings.upload_timeout,
            )


async def _send_album(status: Message, album: PhotoAlbum, settings: Settings) -> None:
    """Отправляет картинки: одну — фотографией, несколько — медиагруппой."""
    count = len(album.paths)
    with contextlib.suppress(TelegramBadRequest):
        await status.edit_text(f"📤 Отправляю {count} шт…")

    bot = status.bot
    chat_id = status.chat.id
    caption = await _signature(bot)

    async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO):
        if count == 1:
            await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(album.paths[0]),
                caption=caption,
                request_timeout=settings.upload_timeout,
            )
        else:
            # Подпись у медиагруппы берётся с первого элемента.
            group = [
                InputMediaPhoto(
                    media=FSInputFile(path),
                    caption=caption if index == 0 else None,
                )
                for index, path in enumerate(album.paths)
            ]
            await bot.send_media_group(
                chat_id=chat_id,
                media=group,
                request_timeout=settings.upload_timeout,
            )

    if album.skipped:
        await status.answer(
            f"ℹ️ Ещё {album.skipped} шт. не отправил — они тяжелее 10 МБ."
        )


def _safe_filename(media: Media, default_ext: str) -> str:
    """Имя файла для Telegram: без служебных символов и не слишком длинное."""
    ext = media.path.suffix or default_ext
    cleaned = "".join(
        char if char.isalnum() or char in " ._-()" else "_" for char in media.title
    ).strip()
    cleaned = cleaned[:60].strip(" .") or "video"
    return f"{cleaned}{ext}"


# ── прогресс ─────────────────────────────────────────────────────────


async def _report_progress(status: Message, progress: Progress) -> None:
    """Периодически обновляет сообщение, пока идёт загрузка."""
    last_text = ""
    while True:
        await asyncio.sleep(_PROGRESS_INTERVAL)
        text = render_progress(progress)
        if text == last_text:
            continue
        try:
            await status.edit_text(text)
        except TelegramBadRequest:
            # Сообщение могли удалить, или текст не изменился — не страшно.
            pass
        except Exception:
            # Прогресс — дело вторичное и не должен ломать саму загрузку.
            log.debug("Не удалось обновить прогресс", exc_info=True)
        else:
            last_text = text


async def _stop(task: asyncio.Task[None]) -> None:
    """Аккуратно останавливает задачу с прогрессом."""
    if task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
