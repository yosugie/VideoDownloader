"""Точка входа: ``python -m bot``."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from dataclasses import replace

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand, User

from bot import __version__
from bot.config import ConfigError, Settings, load_settings
from bot.handlers import build_router
from bot.middlewares import AccessMiddleware, ThrottlingMiddleware
from bot.services.access import Registry
from bot.services.downloader import Downloader
from bot.services.photos import PhotoDownloader
from bot.services.stats import Stats
from bot.utils.cache import PendingLinks
from bot.utils.quota import DailyQuota
from bot.utils.rate import format_rate, parse_rate
from bot.utils.state import RuntimeState

log = logging.getLogger("bot")


class StartupError(RuntimeError):
    """Бот не смог запуститься. Текст рассчитан на чтение человеком."""

_COMMANDS = [
    BotCommand(command="start", description="Начать работу"),
    BotCommand(command="mp3", description="Скачать только звук"),
    BotCommand(command="help", description="Как пользоваться"),
    BotCommand(command="status", description="Лимиты и загруженность"),
    BotCommand(command="id", description="Мой Telegram ID"),
]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # aiohttp на уровне DEBUG сыплет служебным шумом.
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def build_session(settings: Settings) -> AiohttpSession:
    """Сессия с увеличенным таймаутом: отправка файла идёт долго."""
    kwargs: dict[str, object] = {"timeout": settings.upload_timeout}
    if settings.telegram_proxy:
        kwargs["proxy"] = settings.telegram_proxy
    if settings.api_base_url:
        kwargs["api"] = TelegramAPIServer.from_base(settings.api_base_url)
    return AiohttpSession(**kwargs)  # type: ignore[arg-type]


def build_dispatcher(settings: Settings) -> Dispatcher:
    """Создаёт диспетчер и регистрирует зависимости и middleware."""
    dispatcher = Dispatcher()

    # Эти объекты aiogram подставит в хендлеры по имени аргумента.
    dispatcher["settings"] = settings
    registry = Registry(settings.access_db)
    # Владелец и администраторы допущены изначально: иначе принимать
    # заявки было бы некому.
    registry.seed(settings.admin_ids | settings.allowed_user_ids)
    dispatcher["registry"] = registry
    dispatcher["downloader"] = Downloader(settings)
    dispatcher["photos"] = PhotoDownloader(settings)
    dispatcher["links"] = PendingLinks()
    dispatcher["quota"] = DailyQuota(settings.daily_limit_per_user)
    # Имя "state" занимать нельзя: aiogram кладёт туда FSMContext на
    # каждом событии и затирает чужое значение.
    dispatcher["runtime"] = RuntimeState()
    dispatcher["stats"] = Stats(
        settings.stats_db, store_urls=settings.stats_store_urls
    )

    access = AccessMiddleware(settings, registry)
    throttling = ThrottlingMiddleware(settings.rate_limit_seconds)
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(access)
        observer.middleware(throttling)

    dispatcher.include_router(build_router())
    return dispatcher


class _QuietLogger:
    """Глушит вывод yt-dlp во время проверки cookies."""

    def debug(self, message: str) -> None:
        log.debug("cookies: %s", message)

    info = debug
    warning = debug
    error = debug


def check_environment(settings: Settings) -> Settings:
    """
    Предупреждает о том, что помешает боту работать в полную силу.

    Возвращает настройки, из которых убрано то, что заведомо не работает:
    cookies — вещь необязательная, и сломанная настройка не должна
    ронять скачивание с остальных сервисов.
    """
    if shutil.which("ffmpeg") is None:
        log.warning(
            "ffmpeg не найден в PATH. Без него не получится склеить видео со звуком "
            "и сделать MP3. Установите: apt install ffmpeg (или brew install ffmpeg)."
        )
    elif shutil.which("ffprobe") is None:
        log.warning(
            "ffprobe не найден в PATH. Без него бот не распознает кодек и не поймёт, "
            "что в посте нет видеодорожки."
        )

    try:
        import curl_cffi  # noqa: F401 - проверяем только наличие
    except ImportError:
        log.warning(
            "curl-cffi не установлен. Без него yt-dlp не может притвориться "
            "браузером, а TikTok отдаёт страницу-проверку вместо видео. "
            "Установите: pip install curl-cffi"
        )
    return _check_cookies(settings)


def cleanup_leftovers(settings: Settings) -> None:
    """
    Убирает временные каталоги, оставшиеся от прошлого запуска.

    В обычной жизни каталог удаляется сразу после отправки файла, но если
    бота прервали на полпути, мусор остаётся. На сервере, где бот делит
    диск с чем-то ещё, за этим стоит следить.
    """
    removed = 0
    for path in settings.download_dir.glob("vd-*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    if removed:
        log.info("Убрал %d временных каталогов от прошлого запуска", removed)


def _browser_cookies_error(browser: str) -> str | None:
    """Пробует прочитать cookies браузера. Возвращает текст ошибки или None."""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError as exc:
        return str(exc)
    try:
        extract_cookies_from_browser(browser, logger=_QuietLogger())
    except Exception as exc:  # noqa: BLE001
        # Браузер не установлен, профиля нет, база заблокирована — причин
        # много, и любая означает одно: этим источником пользоваться нельзя.
        return str(exc).strip()
    return None


def _check_cookies(settings: Settings) -> Settings:
    """
    Проверяет источник cookies до первого запроса.

    Неверный формат или недоступный браузер приводят к тому, что сервис
    просто отвечает отказом, а причина остаётся непонятной.
    """
    if settings.cookies_file is None:
        browser = settings.cookies_from_browser
        if not browser:
            return settings

        error = _browser_cookies_error(browser)
        if error is not None:
            log.error(
                "Не удалось прочитать cookies из браузера %r: %s\n"
                "Бот продолжит работу БЕЗ cookies — YouTube и TikTok будут "
                "скачиваться как обычно, Instagram, скорее всего, нет.\n"
                "Проверьте, что браузер установлен и хотя бы раз запускался, "
                "либо уберите COOKIES_FROM_BROWSER из .env.",
                browser,
                error,
            )
            return replace(settings, cookies_from_browser=None)

        log.info("Cookies берутся из браузера: %s", browser)
        return settings

    path = settings.cookies_file
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("Не удалось прочитать файл cookies %s: %s", path, exc)
        return replace(settings, cookies_file=None)

    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        log.warning(
            "Файл cookies %s похож на JSON, а нужен формат Netscape. "
            "В расширении браузера выберите экспорт именно в Netscape / cookies.txt.",
            path,
        )
        return replace(settings, cookies_file=None)

    records = [
        line for line in text.splitlines()
        if line.strip() and not line.startswith("#") and line.count("\t") >= 5
    ]
    if not records:
        log.warning(
            "В файле cookies %s не нашлось ни одной записи. "
            "Похоже, он пустой или сохранён не в том формате.",
            path,
        )
        return replace(settings, cookies_file=None)

    domains = {line.split("\t", 1)[0].lstrip(".").lower() for line in records}
    log.info(
        "Cookies загружены: %d записей, домены: %s",
        len(records),
        ", ".join(sorted(domains)[:5]) or "—",
    )
    return settings


async def run() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    settings = check_environment(settings)
    cleanup_leftovers(settings)

    bot = Bot(
        token=settings.bot_token,
        session=build_session(settings),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dispatcher = build_dispatcher(settings)

    try:
        me = await _whoami(bot)
        log.info(
            "VideoDownloader %s запущен как @%s (доступ: %s)",
            __version__,
            me.username,
            "белый список" if settings.is_private else "открыт для всех",
        )
        if not settings.is_private:
            if settings.daily_limit_per_user:
                log.info(
                    "Бот открыт для всех, ограничение — %d загрузок в сутки "
                    "на пользователя.",
                    settings.daily_limit_per_user,
                )
            else:
                log.warning(
                    "ALLOWED_USER_IDS пуст, а DAILY_LIMIT_PER_USER не задан: "
                    "бот отвечает кому угодно без ограничений. Для открытого "
                    "бота задайте суточный лимит."
                )

        rate = parse_rate(settings.max_download_rate)
        if rate:
            log.info("Скорость скачивания ограничена: %s", format_rate(rate))

        await bot.set_my_commands(_COMMANDS)
        await dispatcher.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()


async def _whoami(bot: Bot) -> User:
    """Первый запрос к Telegram. Заодно проверяет токен и связь."""
    try:
        return await bot.get_me()
    except TelegramUnauthorizedError as exc:
        raise StartupError(
            "Telegram не принял токен.\n\n"
            "Проверьте BOT_TOKEN в файле .env: он выглядит как 123456789:AAE...\n"
            "и копируется целиком — без пробелов, кавычек и переносов строки.\n"
            "Новый токен можно выпустить у @BotFather: /mybots -> ваш бот -> API Token."
        ) from exc
    except TelegramNetworkError as exc:
        raise StartupError(
            "Не удалось связаться с api.telegram.org.\n\n"
            f"Подробности: {exc}\n\n"
            "Проверьте интернет. Если Telegram недоступен у провайдера, укажите\n"
            "TELEGRAM_PROXY в .env или запустите бота на сервере."
        ) from exc


def main() -> int:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    except StartupError as exc:
        print(f"Не удалось запустить бота.\n\n{exc}", file=sys.stderr)
        return 3
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено пользователем")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
