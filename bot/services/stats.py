"""Учёт загрузок в SQLite.

Журнал systemd показывает происходящее сейчас, но считать по нему
неудобно, да и чистится он по своему расписанию. Поэтому каждая
загрузка записывается в базу: так можно ответить, сколько людей
пользовалось ботом за неделю и какой сервис популярнее.

Используется sqlite3 из стандартной библиотеки — отдельная СУБД ради
нескольких тысяч строк была бы избыточной.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT    NOT NULL,
    user_id  INTEGER NOT NULL,
    kind     TEXT    NOT NULL,
    host     TEXT    NOT NULL,
    url      TEXT,
    size     INTEGER NOT NULL DEFAULT 0,
    seconds  REAL,
    status   TEXT    NOT NULL,
    error    TEXT,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS downloads_at_idx ON downloads(at);
CREATE INDEX IF NOT EXISTS downloads_user_idx ON downloads(user_id);
"""

STATUS_OK = "ok"
STATUS_ERROR = "error"


@dataclass(slots=True)
class Failure:
    """Один сбой: что именно и у кого."""

    at: str
    user_id: int
    host: str
    kind: str
    error: str
    detail: str | None = None


@dataclass(slots=True)
class Summary:
    """Сводка за период."""

    period: str
    total: int = 0
    failed: int = 0
    users: int = 0
    bytes_sent: int = 0
    by_host: list[tuple[str, int]] = field(default_factory=list)
    by_kind: list[tuple[str, int]] = field(default_factory=list)
    top_users: list[tuple[int, int]] = field(default_factory=list)
    top_errors: list[tuple[str, int]] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return self.total - self.failed


class Stats:
    """Хранилище учёта загрузок."""

    def __init__(self, path: Path, *, store_urls: bool = False) -> None:
        self._path = path
        self._store_urls = store_urls
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._migrate()
            self._connection.commit()

    def _migrate(self) -> None:
        """Доводит старую базу до текущей схемы."""
        existing = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(downloads)")
        }
        for column, definition in (("detail", "TEXT"),):
            if column not in existing:
                log.info("Добавляю в учёт колонку %s", column)
                self._connection.execute(
                    f"ALTER TABLE downloads ADD COLUMN {column} {definition}"
                )

    @property
    def path(self) -> Path:
        return self._path

    @property
    def stores_urls(self) -> bool:
        return self._store_urls

    def record(
        self,
        *,
        user_id: int,
        kind: str,
        host: str,
        url: str,
        size: int = 0,
        seconds: float | None = None,
        status: str = STATUS_OK,
        error: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Записывает одну загрузку. Сбой учёта не должен мешать работе бота."""
        try:
            with self._lock:
                self._connection.execute(
                    "INSERT INTO downloads "
                    "(at, user_id, kind, host, url, size, seconds, status, error, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        user_id,
                        kind,
                        host,
                        url if self._store_urls else None,
                        max(0, size),
                        seconds,
                        status,
                        error,
                        (detail or "")[:1000] or None,
                    ),
                )
                self._connection.commit()
        except sqlite3.Error:
            log.warning("Не удалось записать статистику", exc_info=True)

    def summary(self, days: int | None, *, limit: int = 5) -> Summary:
        """
        Сводка за последние ``days`` суток. ``None`` — за всё время.

        ``days=1`` означает «сегодня», отсчёт идёт от начала текущих суток,
        а не от «двадцати четырёх часов назад».
        """
        period = "за всё время" if days is None else _period_name(days)
        summary = Summary(period=period)
        since = _since(days)
        where, params = ("WHERE at >= ?", (since,)) if since else ("", ())

        try:
            with self._lock:
                row = self._connection.execute(
                    # where собирается здесь же, пользовательских данных в нём нет
                    f"SELECT COUNT(*) AS total, "
                    f"COUNT(DISTINCT user_id) AS users, "
                    f"SUM(size) AS bytes_sent, "
                    f"SUM(status != '{STATUS_OK}') AS failed "
                    f"FROM downloads {where}",
                    params,
                ).fetchone()
                summary.total = row["total"] or 0
                summary.users = row["users"] or 0
                summary.bytes_sent = row["bytes_sent"] or 0
                summary.failed = row["failed"] or 0

                summary.by_host = self._group(where, params, "host", limit)
                summary.by_kind = self._group(where, params, "kind", limit)
                summary.top_users = [
                    (int(key), count)
                    for key, count in self._group(where, params, "user_id", limit)
                ]
                summary.top_errors = self._group(
                    where + (" AND " if where else "WHERE ") + "error IS NOT NULL",
                    params,
                    "error",
                    limit,
                )
        except sqlite3.Error:
            log.warning("Не удалось собрать статистику", exc_info=True)

        return summary

    def recent_errors(self, limit: int = 10) -> list[Failure]:
        """
        Последние сбои с исходным текстом.

        Чем больше людей пользуется ботом, тем быстрее по этому списку
        видно, что именно сломалось у сервиса: одинаковые сообщения
        собираются в одном месте вместо того, чтобы теряться в журнале.
        """
        try:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT at, user_id, host, kind, error, detail FROM downloads "
                    "WHERE status != ? ORDER BY id DESC LIMIT ?",
                    (STATUS_OK, limit),
                ).fetchall()
        except sqlite3.Error:
            log.warning("Не удалось прочитать список ошибок", exc_info=True)
            return []
        return [
            Failure(
                at=row["at"],
                user_id=row["user_id"],
                host=row["host"],
                kind=row["kind"],
                error=row["error"] or "—",
                detail=row["detail"],
            )
            for row in rows
        ]

    def frequent_errors(self, days: int | None = 7, limit: int = 5) -> list[tuple[str, int]]:
        """Какие сообщения об ошибках повторяются чаще всего."""
        since = _since(days)
        where, params = ("AND at >= ?", (since,)) if since else ("", ())
        try:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT COALESCE(detail, error) AS text, COUNT(*) AS count "
                    f"FROM downloads WHERE status != ? {where} "
                    "GROUP BY text ORDER BY count DESC LIMIT ?",
                    (STATUS_OK, *params, limit),
                ).fetchall()
        except sqlite3.Error:
            log.warning("Не удалось сгруппировать ошибки", exc_info=True)
            return []
        return [(str(row["text"] or "—"), row["count"]) for row in rows]

    def _group(
        self, where: str, params: tuple, column: str, limit: int
    ) -> list[tuple[str, int]]:
        rows = self._connection.execute(
            # Имена колонок задаются вызывающим кодом, не пользователем.
            f"SELECT {column} AS key, COUNT(*) AS count "
            f"FROM downloads {where} "
            f"GROUP BY {column} ORDER BY count DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [(str(row["key"]), row["count"]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _since(days: int | None) -> str | None:
    if days is None:
        return None
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days=max(0, days - 1))
    return start.isoformat(timespec="seconds")


def _period_name(days: int) -> str:
    if days <= 1:
        return "за сегодня"
    if days == 7:
        return "за неделю"
    if days == 30:
        return "за месяц"
    return f"за {days} дней"
