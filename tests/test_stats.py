"""Тесты учёта загрузок."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bot.handlers.commands import _parse_period, errors_report, stats_report
from bot.services.stats import STATUS_ERROR, Stats


@pytest.fixture
def stats(tmp_path: Path) -> Stats:
    return Stats(tmp_path / "stats.db")


def add(stats: Stats, **overrides: object) -> None:
    payload: dict[str, object] = {
        "user_id": 1,
        "kind": "video",
        "host": "youtube.com",
        "url": "https://youtu.be/abc",
        "size": 1_000_000,
    }
    payload.update(overrides)
    stats.record(**payload)  # type: ignore[arg-type]


# ── запись и подсчёт ───────────────────────────────────────────────────


def test_empty_database_gives_zeroes(stats: Stats) -> None:
    summary = stats.summary(1)
    assert summary.total == 0
    assert summary.users == 0
    assert summary.bytes_sent == 0


def test_counts_downloads_and_people(stats: Stats) -> None:
    add(stats, user_id=1)
    add(stats, user_id=1)
    add(stats, user_id=2)

    summary = stats.summary(1)

    assert summary.total == 3
    assert summary.users == 2
    assert summary.bytes_sent == 3_000_000


def test_errors_are_counted_separately(stats: Stats) -> None:
    add(stats)
    add(stats, status=STATUS_ERROR, error="AuthRequired", size=0)

    summary = stats.summary(1)

    assert summary.total == 2
    assert summary.failed == 1
    assert summary.succeeded == 1
    assert summary.top_errors == [("AuthRequired", 1)]


def test_grouping_by_service_and_kind(stats: Stats) -> None:
    add(stats, host="youtube.com", kind="video")
    add(stats, host="youtube.com", kind="audio")
    add(stats, host="tiktok.com", kind="video")

    summary = stats.summary(1)

    assert summary.by_host == [("youtube.com", 2), ("tiktok.com", 1)]
    assert dict(summary.by_kind) == {"video": 2, "audio": 1}


def test_top_users_are_sorted(stats: Stats) -> None:
    for _ in range(3):
        add(stats, user_id=10)
    add(stats, user_id=20)

    assert stats.summary(1).top_users[0] == (10, 3)


# ── период ─────────────────────────────────────────────────────────────


def test_old_records_are_outside_today(stats: Stats) -> None:
    add(stats)
    long_ago = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    stats._connection.execute("UPDATE downloads SET at = ?", (long_ago,))
    stats._connection.commit()

    assert stats.summary(1).total == 0, "десятидневная запись не входит в сегодня"
    assert stats.summary(30).total == 1
    assert stats.summary(None).total == 1, "за всё время видно всё"


def test_period_names() -> None:
    assert _parse_period(None) == 1
    assert _parse_period("7") == 7
    assert _parse_period("all") is None
    assert _parse_period("всё") is None
    assert _parse_period("ерунда") == 1
    assert _parse_period("99999") == 365, "период ограничен сверху"


# ── приватность ────────────────────────────────────────────────────────


def test_urls_are_not_stored_by_default(stats: Stats) -> None:
    add(stats, url="https://youtu.be/секрет")

    stored = stats._connection.execute("SELECT url FROM downloads").fetchone()[0]
    assert stored is None
    assert stats.stores_urls is False


def test_urls_are_stored_when_allowed(tmp_path: Path) -> None:
    with_urls = Stats(tmp_path / "s.db", store_urls=True)
    add(with_urls, url="https://youtu.be/abc")

    stored = with_urls._connection.execute("SELECT url FROM downloads").fetchone()[0]
    assert stored == "https://youtu.be/abc"


def test_report_mentions_that_urls_are_not_kept(stats: Stats) -> None:
    add(stats)
    text = stats_report(stats.summary(1), stores_urls=False)
    assert "Ссылки не сохраняются" in text


# ── устойчивость ───────────────────────────────────────────────────────


def test_broken_database_does_not_raise(stats: Stats) -> None:
    """Учёт — дело вспомогательное, падать из-за него бот не должен."""
    stats.close()

    add(stats)  # запись в закрытую базу
    summary = stats.summary(1)

    assert summary.total == 0


# ── отчёт ──────────────────────────────────────────────────────────────


def test_report_is_friendly_when_empty(stats: Stats) -> None:
    assert "Пока ничего не скачивали" in stats_report(stats.summary(1))


def test_report_shows_numbers(stats: Stats) -> None:
    add(stats, size=5_242_880)
    text = stats_report(stats.summary(1))

    assert "Загрузок" in text
    assert "5.0 МБ" in text
    assert "youtube.com — 1" in text


def test_report_translates_kinds(stats: Stats) -> None:
    add(stats, kind="photos")
    assert "фото — 1" in stats_report(stats.summary(1))


# ── сбор ошибок ────────────────────────────────────────────────────────

TIKTOK_ERROR = (
    "ERROR: [TikTok] 7608078884960996615: Unexpected response from webpage "
    "request; please report this issue on https://github.com/yt-dlp/yt-dlp/issues"
)


def test_error_details_are_stored(stats: Stats) -> None:
    add(stats, status=STATUS_ERROR, error="ExtractionFailed", detail=TIKTOK_ERROR)

    failures = stats.recent_errors()

    assert len(failures) == 1
    assert failures[0].error == "ExtractionFailed"
    assert "Unexpected response" in (failures[0].detail or "")
    assert failures[0].host == "youtube.com"


def test_successful_downloads_are_not_in_errors(stats: Stats) -> None:
    add(stats)
    add(stats, status=STATUS_ERROR, error="AuthRequired")

    assert len(stats.recent_errors()) == 1


def test_recent_errors_are_newest_first(stats: Stats) -> None:
    add(stats, status=STATUS_ERROR, error="Первая")
    add(stats, status=STATUS_ERROR, error="Вторая")

    assert [f.error for f in stats.recent_errors()] == ["Вторая", "Первая"]


def test_recent_errors_respect_limit(stats: Stats) -> None:
    for index in range(10):
        add(stats, status=STATUS_ERROR, error=f"Ошибка{index}")

    assert len(stats.recent_errors(limit=3)) == 3


def test_frequent_errors_group_identical_messages(stats: Stats) -> None:
    """Главная польза: одинаковые сбои видно одной строкой со счётчиком."""
    for _ in range(4):
        add(stats, status=STATUS_ERROR, error="ExtractionFailed", detail=TIKTOK_ERROR)
    add(stats, status=STATUS_ERROR, error="AuthRequired", detail="login required")

    frequent = stats.frequent_errors()

    assert frequent[0][1] == 4
    assert "Unexpected response" in frequent[0][0]


def test_long_details_are_truncated(stats: Stats) -> None:
    add(stats, status=STATUS_ERROR, error="X", detail="я" * 5000)
    assert len(stats.recent_errors()[0].detail or "") <= 1000


def test_old_database_gets_the_new_column(tmp_path: Path) -> None:
    """База, созданная до появления колонки detail, должна продолжать работать."""
    import sqlite3

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "at TEXT NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL, "
        "host TEXT NOT NULL, url TEXT, size INTEGER NOT NULL DEFAULT 0, "
        "seconds REAL, status TEXT NOT NULL, error TEXT);"
    )
    connection.commit()
    connection.close()

    upgraded = Stats(path)
    add(upgraded, status=STATUS_ERROR, error="X", detail="подробности")

    assert upgraded.recent_errors()[0].detail == "подробности"


# ── отчёт о сбоях ──────────────────────────────────────────────────────


def test_errors_report_is_cheerful_when_empty(stats: Stats) -> None:
    report = errors_report(recent=[], frequent=[])
    assert "Сбоев не было" in report


def test_errors_report_shows_counts_and_details(stats: Stats) -> None:
    add(stats, status=STATUS_ERROR, error="ExtractionFailed", detail=TIKTOK_ERROR)

    report = errors_report(
        recent=stats.recent_errors(), frequent=stats.frequent_errors()
    )

    assert "ExtractionFailed" in report
    assert "Unexpected response" in report
    assert "youtube.com" in report


def test_github_boilerplate_is_cut_out(stats: Stats) -> None:
    """Совет завести issue на GitHub только занимает место."""
    add(stats, status=STATUS_ERROR, error="X", detail=TIKTOK_ERROR)

    report = errors_report(recent=stats.recent_errors(), frequent=[])

    assert "github.com" not in report
    assert "please report" not in report.lower()


def test_errors_report_fits_telegram_limit(stats: Stats) -> None:
    for index in range(20):
        add(stats, status=STATUS_ERROR, error=f"Ошибка{index}", detail="подробно " * 50)

    report = errors_report(
        recent=stats.recent_errors(20), frequent=stats.frequent_errors()
    )

    assert len(report) <= 4096, "сообщение Telegram длиннее 4096 символов не принимает"
