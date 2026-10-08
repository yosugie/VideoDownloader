"""Команды /start, /help, /id и /status."""

from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.services.access import Registry
from bot.services.downloader import Downloader
from bot.services.links import SUPPORTED_PLATFORMS
from bot.services.stats import Failure, Stats, Summary
from bot.utils.format import human_size
from bot.utils.quota import DailyQuota
from bot.utils.rate import format_rate, parse_rate
from bot.utils.state import RuntimeState

router = Router(name="commands")


def _platforms_list() -> str:
    return "\n".join(f"  • {platform}" for platform in SUPPORTED_PLATFORMS)


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings) -> None:
    name = escape(message.from_user.first_name if message.from_user else "друг")
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Я скачиваю видео и фото по ссылке. Умею:\n"
        f"{_platforms_list()}\n\n"
        "<b>Как пользоваться:</b> просто пришлите ссылку. "
        "Если в посте видео — пришлю видео, если фотографии — пришлю их.\n\n"
        "Нужен только звук? Команда <code>/mp3 ссылка</code>.\n\n"
        f"Лимит одного файла — {settings.max_file_size_mb} МБ, "
        f"длительность — до {settings.max_duration_minutes} мин.\n\n"
        "Подробнее: /help"
    )


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    extra = ""
    if settings.allow_any_site:
        extra = (
            "\nВключён режим «любой сайт»: можно пробовать ссылки "
            "и с других сервисов — вдруг получится.\n"
        )
    await message.answer(
        "<b>Что я умею</b>\n\n"
        f"{_platforms_list()}\n"
        f"{extra}\n"
        "<b>Шаги</b>\n"
        "1. Скопируйте ссылку в приложении или браузере.\n"
        "2. Пришлите её мне одним сообщением.\n"
        "3. Дождитесь файла — я покажу прогресс.\n\n"
        "Выбирать ничего не нужно: есть видео — пришлю видео, "
        "фотопост или карусель — пришлю картинки.\n\n"
        "<b>Команды</b>\n"
        "/mp3 ссылка — забрать только звук\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/id — узнать свой Telegram ID\n"
        "/status — лимиты и загруженность\n\n"
        "<b>Если что-то не вышло</b>\n"
        "• Приватный аккаунт или видео 18+ — нужна авторизация, "
        "я такое скачать не смогу.\n"
        f"• Файл тяжелее {settings.max_file_size_mb} МБ — "
        "я сам понижу качество, а если не поможет, предложу забрать звук.\n"
        "• Картинок в посте пришлю не больше 10 — это лимит Telegram.\n"
        "• Ссылка на плейлист — скачаю только первое видео.\n\n"
        "Скачивайте только то, на что у вас есть право: "
        "свои ролики, материалы с разрешения автора или свободные лицензии."
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Не удалось определить ваш ID.")
        return
    await message.answer(
        f"Ваш Telegram ID: <code>{user.id}</code>\n"
        f"ID этого чата: <code>{message.chat.id}</code>\n\n"
        "Этот номер нужно добавить в <code>ALLOWED_USER_IDS</code>, "
        "чтобы открыть доступ к боту."
    )


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    settings: Settings,
    downloader: Downloader,
    quota: DailyQuota,
    runtime: RuntimeState,
    registry: Registry,
) -> None:
    user = message.from_user
    counts = registry.counts()
    await message.answer(
        status_text(
            user_id=user.id if user is not None else None,
            settings=settings,
            downloader=downloader,
            quota=quota,
            runtime=runtime,
            people=counts,
        )
    )


def status_text(
    *,
    user_id: int | None,
    settings: Settings,
    downloader: Downloader,
    quota: DailyQuota,
    runtime: RuntimeState | None = None,
    people: dict[str, int] | None = None,
) -> str:
    """
    Состояние бота.

    Обычным пользователям показываем только то, что их касается.
    Настройки доступа, число слотов и наличие cookies — это внутреннее
    устройство, и посторонним знать его незачем.
    """
    if runtime is not None and runtime.paused:
        queue = f"пауза с {runtime.paused_since} ⏸"
    elif downloader.busy:
        queue = "все слоты заняты, придётся подождать ⏳"
    else:
        queue = "есть свободные слоты ✅"

    lines = [
        "<b>Состояние бота</b>",
        "",
        f"Очередь: {queue}",
        f"Лимит файла: {settings.max_file_size_mb} МБ",
        f"Лимит длительности: {settings.max_duration_minutes} мин",
        _quota_line(user_id, settings, quota),
    ]

    if user_id is not None and settings.is_admin(user_id):
        cookies = (
            "подключены"
            if settings.cookies_file or settings.cookies_from_browser
            else "не заданы"
        )
        access = "по белому списку 🔒" if settings.is_private else "открыт для всех 🔓"
        lines += [
            "",
            "<b>Видно только администратору</b>",
            f"Доступ: {access}",
            f"Одновременных загрузок: {settings.max_concurrent_downloads}",
            f"Скорость: {format_rate(parse_rate(settings.max_download_rate))}",
            f"Cookies: {cookies}",
            f"Людей за сегодня: {quota.users_today()}",
        ]
        if people:
            lines.append(f"Допущено человек: {people.get('approved', 0)}")
            waiting = people.get("pending", 0)
            if waiting:
                lines.append(f"⚠️ Заявок ждёт решения: {waiting} — /requests")

    return "\n".join(lines)


def _quota_line(user_id: int | None, settings: Settings, quota: DailyQuota) -> str:
    """Строка про суточный лимит — у администраторов его нет."""
    if not quota.enabled:
        return "Суточный лимит: не задан"
    if user_id is not None and settings.is_admin(user_id):
        return f"Суточный лимит: {quota.limit}, но на вас не распространяется"
    left = quota.remaining(user_id) if user_id is not None else quota.limit
    return f"Суточный лимит: {quota.limit}, осталось сегодня — {left}"


@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    command: CommandObject,
    settings: Settings,
    stats: Stats,
) -> None:
    """
    Статистика загрузок. Команда только для администраторов.

    Посторонним бот не отвечает вовсе: сообщение о том, что команда
    существует, но недоступна, само по себе лишняя подсказка. В меню
    команд она тоже не числится.
    """
    user = message.from_user
    if user is None or not settings.is_admin(user.id):
        return

    days = _parse_period(command.args)
    await message.answer(stats_report(stats.summary(days), stores_urls=stats.stores_urls))


def _parse_period(args: str | None) -> int | None:
    """``/stats`` — сегодня, ``/stats 7`` — неделя, ``/stats all`` — всё время."""
    if not args:
        return 1
    argument = args.strip().lower()
    if argument in {"all", "всё", "все"}:
        return None
    try:
        return max(1, min(365, int(argument)))
    except ValueError:
        return 1


def stats_report(summary: Summary, *, stores_urls: bool = False) -> str:
    """Собирает текст сводки."""
    lines = [f"📊 <b>Статистика {summary.period}</b>", ""]

    if not summary.total:
        lines.append("Пока ничего не скачивали.")
        return "\n".join(lines)

    lines += [
        f"Загрузок: <b>{summary.total}</b> "
        f"(удачных {summary.succeeded}, с ошибкой {summary.failed})",
        f"Людей: <b>{summary.users}</b>",
        f"Отдано: <b>{human_size(summary.bytes_sent)}</b>",
    ]

    lines += _block("Сервисы", summary.by_host)
    lines += _block("Что скачивали", _translate_kinds(summary.by_kind))
    lines += _block("Активнее всех", [(str(uid), count) for uid, count in summary.top_users])
    lines += _block("Частые ошибки", summary.top_errors)

    if not stores_urls:
        lines += ["", "<i>Ссылки не сохраняются — только сервис и объём.</i>"]

    return "\n".join(lines)


def _block(title: str, rows: list[tuple[str, int]]) -> list[str]:
    if not rows:
        return []
    out = ["", f"<b>{title}</b>"]
    out += [f"  {escape(key)} — {count}" for key, count in rows]
    return out


def _translate_kinds(rows: list[tuple[str, int]]) -> list[tuple[str, int]]:
    names = {"video": "видео", "audio": "аудио", "photos": "фото"}
    return [(names.get(key, key), count) for key, count in rows]


@router.message(Command("errors"))
async def cmd_errors(
    message: Message,
    command: CommandObject,
    settings: Settings,
    stats: Stats,
) -> None:
    """Последние сбои с исходным текстом. Только для администраторов."""
    user = message.from_user
    if user is None or not settings.is_admin(user.id):
        return

    limit = _parse_limit(command.args)
    await message.answer(
        errors_report(
            recent=stats.recent_errors(limit),
            frequent=stats.frequent_errors(days=7),
        )
    )


def _parse_limit(args: str | None) -> int:
    if not args:
        return 5
    try:
        return max(1, min(20, int(args.strip())))
    except ValueError:
        return 5


def errors_report(*, recent: list[Failure], frequent: list[tuple[str, int]]) -> str:
    """Отчёт о сбоях: что повторяется и что случилось последним."""
    if not recent:
        return "✅ <b>Сбоев не было</b>\n\nПока всё скачивается без ошибок."

    lines = ["🧰 <b>Сбои</b>"]

    if frequent:
        lines += ["", "<b>Чаще всего за неделю</b>"]
        for text, count in frequent:
            lines.append(f"  <b>{count}×</b> {escape(_shorten(text))}")

    lines += ["", "<b>Последние</b>"]
    for failure in recent:
        when = failure.at.replace("T", " ")[5:16]
        lines.append(
            f"\n<code>{escape(when)}</code> · {escape(failure.host)} · "
            f"{escape(failure.kind)} · id {failure.user_id}\n"
            f"  {escape(failure.error)}"
        )
        if failure.detail:
            lines.append(f"  <i>{escape(_shorten(failure.detail))}</i>")

    return "\n".join(lines)[:4000]


def _shorten(text: str, limit: int = 160) -> str:
    """Сообщения yt-dlp длинные, а в них важно только начало."""
    cleaned = " ".join(text.split())
    # Хвост про то, что надо завести issue на GitHub, ничего не добавляет.
    for noise in ("; please report this issue", ". please report this issue"):
        position = cleaned.lower().find(noise)
        if position > 0:
            cleaned = cleaned[:position]
            break
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


@router.message(Command("pause"))
async def cmd_pause(
    message: Message, settings: Settings, runtime: RuntimeState
) -> None:
    """
    Перестать принимать загрузки, не останавливая службу.

    Нужно, когда серверу стало тесно: бот отвечает, но ничего не качает,
    то есть не занимает ни канал, ни процессор. Полную остановку даёт
    systemctl, но лезть за ней по SSH с телефона неудобно.
    """
    user = message.from_user
    if user is None or not settings.is_admin(user.id):
        return

    if runtime.paused:
        await message.answer(f"⏸ Бот уже на паузе с {runtime.paused_since}.")
        return

    runtime.pause()
    await message.answer(
        "⏸ <b>Пауза</b>\n\n"
        "Новые загрузки не принимаются, остальные получат вежливый отказ. "
        "Вам бот по-прежнему доступен.\n\n"
        "Включить обратно: /resume"
    )


@router.message(Command("resume"))
async def cmd_resume(
    message: Message, settings: Settings, runtime: RuntimeState
) -> None:
    """Вернуть бота в работу."""
    user = message.from_user
    if user is None or not settings.is_admin(user.id):
        return

    if not runtime.paused:
        await message.answer("▶️ Бот и так работает.")
        return

    runtime.resume()
    await message.answer("▶️ <b>Снова в работе</b>\n\nЗагрузки принимаются.")
