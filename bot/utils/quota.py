"""Суточный лимит загрузок на пользователя.

Когда бот открыт для всех, один человек может занять его на весь день —
намеренно или просто от увлечения. Лимит считается в памяти процесса:
для домашнего бота этого достаточно, но при перезапуске счётчики
обнуляются, и это осознанный размен в пользу простоты.
"""

from __future__ import annotations

from datetime import date


class DailyQuota:
    """Счётчик загрузок, обнуляющийся с началом новых суток."""

    def __init__(self, limit: int) -> None:
        self._limit = max(0, limit)
        self._day = date.today()
        self._used: dict[int, int] = {}

    @property
    def limit(self) -> int:
        """0 означает «без ограничений»."""
        return self._limit

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    def allow(self, user_id: int) -> bool:
        """Разрешает загрузку и увеличивает счётчик. ``False`` — лимит исчерпан."""
        if not self.enabled:
            return True
        self._rollover()
        used = self._used.get(user_id, 0)
        if used >= self._limit:
            return False
        self._used[user_id] = used + 1
        return True

    def remaining(self, user_id: int) -> int | None:
        """Сколько загрузок осталось. ``None`` — лимита нет."""
        if not self.enabled:
            return None
        self._rollover()
        return max(0, self._limit - self._used.get(user_id, 0))

    def refund(self, user_id: int) -> None:
        """Возвращает попытку — например, когда ссылка оказалась нерабочей."""
        if not self.enabled:
            return
        self._rollover()
        used = self._used.get(user_id, 0)
        if used > 0:
            self._used[user_id] = used - 1

    def users_today(self) -> int:
        self._rollover()
        return len(self._used)

    def _rollover(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._used.clear()
