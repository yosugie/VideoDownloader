"""Заявки на доступ.

Команда ``/start`` для гостя и есть заявка: кнопка «Отправить заявку»
только добавляла шаг, на котором непонятно, ушло что-нибудь или нет.
Владелец получает заявку с кнопками решения и ссылкой на профиль, чтобы
знать, кого он пускает. После решения человек получает ответ, а запись
о нём остаётся в списке.
"""

from __future__ import annotations

import contextlib
import logging
from html import escape

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart, Filter
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from bot.config import Settings
from bot.keyboards import AccessDecision, decide_access
from bot.services.access import Member, Registry, is_allowed

log = logging.getLogger(__name__)

router = Router(name="access")


class IsGuest(Filter):
    """Срабатывает на тех, кто ещё не допущен.

    Спрашивает тот же ``is_allowed``, что и middleware, а не признак из
    данных: фильтры выполняются раньше внутренних middleware, и
    признака там может не оказаться вовсе — тогда гость молча уезжает в
    обычное приветствие.
    """

    async def __call__(
        self,
        event: TelegramObject,
        settings: Settings,
        registry: Registry,
        event_from_user: User | None = None,
    ) -> bool:
        if event_from_user is None:
            return False
        return not is_allowed(settings, registry, event_from_user.id)


# ── гость ────────────────────────────────────────────────────────────


@router.message(CommandStart(), IsGuest())
async def guest_start(
    message: Message,
    bot: Bot,
    settings: Settings,
    registry: Registry,
) -> None:
    """Заявка подаётся самой командой ``/start``."""
    user = message.from_user
    if user is None:
        return

    waiting = registry.get(user.id)
    if waiting is not None and waiting.waiting:
        await message.answer(
            "⏳ <b>Заявка уже отправлена</b>\n\n"
            "Владелец её пока не рассмотрел. Как решит — я напишу сюда, "
            "повторять не нужно."
        )
        return

    member = registry.ask(user.id, user.username, user.full_name)
    await message.answer(
        f"👋 Привет, <b>{escape(user.first_name or 'друг')}</b>!\n\n"
        "Я скачиваю видео и фото из YouTube, Instagram и TikTok.\n\n"
        "📨 <b>Заявка на доступ отправлена владельцу бота.</b>\n"
        "Как он её рассмотрит — я сразу напишу сюда. "
        "Ждать в чате не нужно, можно закрыть.\n\n"
        f"Ваш Telegram ID: <code>{user.id}</code>"
    )

    if not await _tell_admins(bot, settings, member):
        log.error("Заявку некому показать: ни один администратор недоступен")


# ── владелец ─────────────────────────────────────────────────────────


@router.callback_query(AccessDecision.filter())
async def decide(
    callback: CallbackQuery,
    callback_data: AccessDecision,
    settings: Settings,
    registry: Registry,
) -> None:
    """Принимает или отклоняет заявку."""
    user = callback.from_user
    if not settings.is_admin(user.id):
        await callback.answer("Решение принимает владелец бота.", show_alert=True)
        return

    approve = callback_data.action == "approve"
    member = (
        registry.approve(callback_data.user_id, by=user.id)
        if approve
        else registry.reject(callback_data.user_id, by=user.id)
    )
    if member is None:
        await callback.answer("Заявки больше нет.", show_alert=True)
        return

    await callback.answer("Принято" if approve else "Отклонено")
    log.info(
        "Заявка %s: id=%s решил id=%s",
        "принята" if approve else "отклонена",
        member.user_id,
        user.id,
    )

    if isinstance(callback.message, Message):
        mark = "✅ Принят" if approve else "❌ Отклонён"
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                f"{mark}\n\n{member.mention}\nID: <code>{member.user_id}</code>",
                reply_markup=None,
            )

    await _tell_applicant(callback.bot, member, approve=approve)


@router.message(Command("requests"))
async def pending_requests(
    message: Message,
    settings: Settings,
    registry: Registry,
) -> None:
    """Заявки, которые ещё ждут решения."""
    user = message.from_user
    if user is None or not settings.is_admin(user.id):
        return

    waiting = registry.pending()
    if not waiting:
        counts = registry.counts()
        await message.answer(
            "✅ <b>Заявок нет</b>\n\n"
            f"Допущено человек: {counts.get('approved', 0)}"
        )
        return

    await message.answer(f"📨 <b>Заявок ждёт решения: {len(waiting)}</b>")
    for member in waiting:
        await message.answer(
            _request_text(member),
            reply_markup=decide_access(member.user_id, member.profile_url),
        )


# ── отправка ─────────────────────────────────────────────────────────


def _request_text(member: Member) -> str:
    lines = [
        "📨 <b>Новая заявка</b>",
        "",
        f"Кто: {member.mention}",
        f"ID: <code>{member.user_id}</code>",
    ]
    if member.username:
        lines.append(f"Имя пользователя: @{escape(member.username)}")
    if member.asked_at:
        lines.append(f"Когда: {escape(member.asked_at.replace('T', ' '))}")
    return "\n".join(lines)


async def _tell_admins(bot: Bot, settings: Settings, member: Member) -> bool:
    """Показывает заявку каждому администратору."""
    text = _request_text(member)
    markup = decide_access(member.user_id, member.profile_url)
    delivered = False
    for admin_id in sorted(settings.admin_ids):
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            # Администратор не начинал разговор с ботом или заблокировал его.
            log.warning("Не доставил заявку администратору %s: %s", admin_id, exc)
        else:
            delivered = True
    return delivered


async def _tell_applicant(bot: Bot, member: Member, *, approve: bool) -> None:
    """Сообщает человеку решение по его заявке."""
    text = (
        "✅ <b>Доступ открыт</b>\n\n"
        "Присылайте ссылку на видео или пост — я скачаю.\n"
        "Справка: /help"
        if approve
        else "❌ <b>Заявка отклонена</b>\n\nВладелец бота не открыл доступ."
    )
    with contextlib.suppress(TelegramForbiddenError, TelegramBadRequest):
        await bot.send_message(member.user_id, text)
