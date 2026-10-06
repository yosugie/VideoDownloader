"""Тесты форматирования."""

from __future__ import annotations

import pytest

from bot.services.downloader import Progress
from bot.utils.format import (
    human_duration,
    human_size,
    progress_bar,
    render_progress,
)


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "—"),
        (None, "—"),
        (512, "512 Б"),
        (1024, "1.0 КБ"),
        (15 * 1024 * 1024, "15.0 МБ"),
        (2 * 1024**3, "2.0 ГБ"),
    ],
)
def test_human_size(size: int | None, expected: str) -> None:
    assert human_size(size) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "—"), (0, "0:00"), (59, "0:59"), (61, "1:01"), (3725, "1:02:05")],
)
def test_human_duration(seconds: int | None, expected: str) -> None:
    assert human_duration(seconds) == expected


def test_progress_bar_bounds() -> None:
    assert progress_bar(0) == "▱" * 10
    assert progress_bar(100) == "▰" * 10
    assert progress_bar(None) == "▱" * 10
    assert len(progress_bar(37)) == 10
    # Значения за пределами диапазона не должны ломать длину строки.
    assert len(progress_bar(150)) == 10


def test_render_progress_includes_stage_and_percent() -> None:
    progress = Progress(stage="Скачиваю…", percent=50.0, downloaded=500, total=1000, speed=1024)
    text = render_progress(progress)
    assert "Скачиваю…" in text
    assert "50%" in text
    assert "1.0 КБ/с" in text
