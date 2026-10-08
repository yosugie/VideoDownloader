"""Список людей, допущенных к боту, и заявки на доступ.

Список живёт в SQLite, а не в настройках: он меняется на ходу, когда
владелец принимает заявку, и переживает перезапуск. Из ``.env`` берутся
только те, кто допущен изначально — владелец и администраторы.

Заявка — это та же запись о человеке, только со статусом ``pending``:
отдельная таблица развела бы одно и то же надвое.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bot.config import Settings

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    full_name  TEXT,
    status     TEXT NOT NULL,
    asked_at   TEXT,
    decided_at TEXT,
    decided_by INTEGER
);
CREATE INDEX IF NOT EXISTS members_status_idx ON members(status);
"""

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
BLOCKED = "blocked"


@dataclass(slots=True)
class Member:
    """Человек и его отношение к боту."""

    user_id: int
    status: str
    username: str | None = None
    full_name: str | None = None
    asked_at: str | None = None
    decided_at: str | None = None
    decided_by: int | None = None

    @property
    def allowed(self) -> bool:
        return self.status == APPROVED

    @property
    def waiting(self) -> bool:
        return self.status == PENDING

    @property
    def title(self) -> str:
        """Как назвать человека в сообщении."""
        name = (self.full_name or "").strip()
        if name and self.username:
            return f"{name} (@{self.username})"
        if name:
            return name
        if self.username:
            return f"@{self.username}"
        return str(self.user_id)

    @property
    def profile_url(self) -> str | None:
        """Ссылка на профиль, если у человека есть имя пользователя."""
        return f"https://t.me/{self.username}" if self.username else None

    @property
    def mention(self) -> str:
        """Ссылка на профиль для текста сообщения — работает и без имени."""
        return f'<a href="tg://user?id={self.user_id}">{self.title}</a>'


class Registry:
    """Хранилище участников и заявок."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    @property
    def path(self) -> Path:
        return self._path

    # ── чтение ───────────────────────────────────────────────────────

    def get(self, user_id: int) -> Member | None:
        row = self._row(user_id)
        return _member(row) if row is not None else None

    def allowed(self, user_id: int) -> bool:
        member = self.get(user_id)
        return member is not None and member.allowed

    def blocked(self, user_id: int) -> bool:
        member = self.get(user_id)
        return member is not None and member.status == BLOCKED

    def by_status(self, status: str, limit: int = 100) -> list[Member]:
        """Люди в одном состоянии: заявки по времени заявки, решённые — по решению."""
        order = "asked_at" if status == PENDING else "decided_at"
        rows = self._query(
            # Порядок — из двух строк выше, не из ввода.
            f"SELECT * FROM members WHERE status = ? ORDER BY {order} LIMIT ?",
            (status, limit),
        )
        return [_member(row) for row in rows]

    def pending(self, limit: int = 20) -> list[Member]:
        return self.by_status(PENDING, limit)

    def approved(self, limit: int = 100) -> list[Member]:
        return self.by_status(APPROVED, limit)

    def blocked_people(self, limit: int = 100) -> list[Member]:
        return self.by_status(BLOCKED, limit)

    def counts(self) -> dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS n FROM members GROUP BY status")
        return {str(row["status"]): int(row["n"]) for row in rows}

    # ── запись ───────────────────────────────────────────────────────

    def seed(self, user_ids: frozenset[int]) -> int:
        """
        Вносит изначально допущенных людей.

        Запись, которая уже есть, не трогается: иначе заблокированный
        вручную человек вернулся бы в строй после перезапуска.
        """
        added = 0
        for user_id in sorted(user_ids):
            if self.get(user_id) is None:
                self._write(user_id, APPROVED, decided_by=user_id)
                added += 1
        if added:
            log.info("Внесено в список допущенных: %d", added)
        return added

    def ask(self, user_id: int, username: str | None, full_name: str | None) -> Member:
        """
        Заводит заявку.

        Повторная заявка ничего не меняет: решение по человеку уже есть
        или ещё готовится, и перезапись сбросила бы его.
        """
        current = self.get(user_id)
        if current is not None and current.status != REJECTED:
            return current

        self._write(
            user_id,
            PENDING,
            username=username,
            full_name=full_name,
            asked_at=_now(),
        )
        member = self.get(user_id)
        assert member is not None
        return member

    def approve(self, user_id: int, by: int) -> Member | None:
        return self._decide(user_id, APPROVED, by)

    def reject(self, user_id: int, by: int) -> Member | None:
        return self._decide(user_id, REJECTED, by)

    def block(self, user_id: int, by: int) -> Member | None:
        return self._decide(user_id, BLOCKED, by)

    def forget(self, user_id: int) -> bool:
        """Убирает человека совсем — он сможет подать заявку заново."""
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM members WHERE user_id = ?", (user_id,)
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # ── внутреннее ───────────────────────────────────────────────────

    def _decide(self, user_id: int, status: str, by: int) -> Member | None:
        if self.get(user_id) is None:
            return None
        with self._lock:
            self._connection.execute(
                "UPDATE members SET status = ?, decided_at = ?, decided_by = ? "
                "WHERE user_id = ?",
                (status, _now(), by, user_id),
            )
            self._connection.commit()
        return self.get(user_id)

    def _write(
        self,
        user_id: int,
        status: str,
        *,
        username: str | None = None,
        full_name: str | None = None,
        asked_at: str | None = None,
        decided_by: int | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO members "
                "(user_id, username, full_name, status, asked_at, decided_at, decided_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, full_name, status, asked_at, _now(), decided_by),
            )
            self._connection.commit()

    def _row(self, user_id: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM members WHERE user_id = ?", (user_id,))
        return rows[0] if rows else None

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        try:
            with self._lock:
                return list(self._connection.execute(sql, params).fetchall())
        except sqlite3.Error:
            log.warning("Не удалось прочитать список допущенных", exc_info=True)
            return []


def _member(row: sqlite3.Row) -> Member:
    return Member(
        user_id=int(row["user_id"]),
        status=str(row["status"]),
        username=row["username"],
        full_name=row["full_name"],
        asked_at=row["asked_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
    )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── допуск ───────────────────────────────────────────────────────────
#
# Ответ на вопрос «пускать ли» живёт здесь один на всех: его спрашивают
# и middleware, и фильтр гостя. Разойдись они — расхождение означало бы
# чужой доступ к боту, причём молча.


def gated(settings: Settings) -> bool:
    """Работает ли бот по заявкам.

    Заявки принимает администратор. Нет его — принимать их некому, и
    бот ведёт себя по-старому, по списку из настроек.
    """
    return bool(settings.admin_ids)


def is_blocked(settings: Settings, registry: Registry, user_id: int) -> bool:
    return user_id in settings.blocked_user_ids or registry.blocked(user_id)


def is_allowed(settings: Settings, registry: Registry, user_id: int) -> bool:
    """Допущен ли человек к боту."""
    if user_id in settings.admin_ids:
        return True
    if registry.allowed(user_id):
        return True
    if gated(settings):
        return False
    # Без администратора список из настроек работает как прежде,
    # а пустой список означает «бот открыт для всех».
    allowed = settings.allowed_user_ids
    return not allowed or user_id in allowed
