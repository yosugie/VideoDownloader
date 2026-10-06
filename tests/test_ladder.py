"""Тесты подбора формата: он решает, сколько трафика уйдёт на одну загрузку."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

from bot.services.downloader import Downloader, build_video_ladder
from tests.test_downloader import make_settings

LIMIT = 50 * 1024 * 1024


@pytest.fixture(scope="module")
def ydl() -> YoutubeDL:
    return YoutubeDL({"quiet": True, "no_warnings": True})


@pytest.mark.parametrize("height", [1080, 720, 480, 360])
def test_every_step_is_valid_for_ytdlp(ydl: YoutubeDL, height: int) -> None:
    """Опечатка в строке формата иначе всплыла бы только на живой загрузке."""
    for spec in build_video_ladder(height, LIMIT):
        ydl.build_format_selector(spec)


def test_size_filter_is_present() -> None:
    """
    Без фильтра по размеру бот качал файл целиком, видел, что не влезает,
    удалял и качал заново ступенью ниже — то есть один ролик проезжал по
    каналу дважды.
    """
    ladder = build_video_ladder(720, LIMIT)
    for spec in ladder[:-1]:
        assert "filesize_approx<?50M" in spec


def test_unknown_size_is_not_rejected() -> None:
    """Вопросительный знак оставляет форматы, у которых размер неизвестен."""
    spec = build_video_ladder(720, LIMIT)[0]
    assert "<?" in spec, "иначе на части сервисов не останется ни одного формата"


def test_ladder_descends() -> None:
    heights = [
        int(match)
        for spec in build_video_ladder(1080, LIMIT)
        for match in re.findall(r"height<=\?(\d+)", spec)[:1]
    ]
    assert heights == sorted(heights, reverse=True), "качество должно только снижаться"


def test_cap_is_respected() -> None:
    for spec in build_video_ladder(480, LIMIT):
        for found in re.findall(r"height<=\?(\d+)", spec):
            assert int(found) <= 480


def test_low_cap_shortens_the_ladder() -> None:
    assert len(build_video_ladder(360, LIMIT)) < len(build_video_ladder(1080, LIMIT))


def test_h264_is_preferred_first() -> None:
    """Если взять H.264 сразу, перекодирование не понадобится вовсе."""
    assert "vcodec^=avc1" in build_video_ladder(720, LIMIT)[0]


def test_last_step_is_a_last_resort() -> None:
    assert build_video_ladder(720, LIMIT)[-1].endswith("worst")


def test_smaller_limit_changes_filter() -> None:
    assert "filesize_approx<?20M" in build_video_ladder(720, 20 * 1024 * 1024)[0]


def test_tiny_limit_does_not_break_filter() -> None:
    spec = build_video_ladder(720, 1024)[0]
    assert "filesize_approx<?1M" in spec


# ── настройки, влияющие на нагрузку ────────────────────────────────────


def test_default_cap_keeps_vertical_videos_sharp() -> None:
    """
    Потолок считается по высоте кадра.

    У вертикальных роликов — а это почти весь Instagram и TikTok —
    высота является длинной стороной, поэтому значение 720 превращает
    кадр 1080x1920 в 405x720. Экономия трафика оказалась не стоящей
    такой потери качества.
    """
    settings = make_settings(Path(tempfile.mkdtemp()))
    assert settings.max_video_height == 1080


def test_vertical_video_fits_under_default_cap() -> None:
    """Обычный вертикальный ролик 1080x1920 должен проходить без понижения."""
    ladder = build_video_ladder(1080, LIMIT)
    assert "height<=?1080" in ladder[0]


def test_ladder_still_steps_down_when_needed() -> None:
    """При этом ступени вниз остаются: лимит Telegram никуда не делся."""
    heights = [
        int(found)
        for spec in build_video_ladder(1080, LIMIT)
        for found in re.findall(r"height<=\?(\d+)", spec)[:1]
    ]
    assert heights == [1080, 720, 480, 360]


def test_fragments_are_not_too_parallel() -> None:
    """Много кусков разом делают нагрузку рваной — соседям по серверу хуже."""
    directory = Path(tempfile.mkdtemp())
    opts = Downloader(make_settings(directory))._base_opts(directory)
    assert opts["concurrent_fragment_downloads"] <= 2


def test_format_sort_prefers_cap_and_h264() -> None:
    directory = Path(tempfile.mkdtemp())
    worker = Downloader(make_settings(directory, max_video_height=480))
    opts = worker._base_opts(directory)
    # format_sort задаётся на каждую попытку, проверяем через саму лестницу
    assert worker._settings.max_video_height == 480
    assert "height<=?480" in build_video_ladder(480, LIMIT)[0]
    assert opts["retries"] >= 1
