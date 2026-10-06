"""Тесты разбора особых параметров экстракторов."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from bot.services.downloader import Downloader
from bot.services.extractor_args import parse_extractor_args
from tests.test_downloader import make_settings


def test_empty_input_gives_nothing() -> None:
    assert parse_extractor_args(None) == {}
    assert parse_extractor_args("") == {}
    assert parse_extractor_args("   ") == {}


def test_single_setting() -> None:
    """Известный обходной путь для TikTok."""
    result = parse_extractor_args("tiktok:api_hostname=api22-normal-c-useast2a.tiktokv.com")
    assert result == {"tiktok": {"api_hostname": ["api22-normal-c-useast2a.tiktokv.com"]}}


def test_several_values_for_one_key() -> None:
    assert parse_extractor_args("youtube:player_client=android,web") == {
        "youtube": {"player_client": ["android", "web"]}
    }


def test_several_extractors_at_once() -> None:
    result = parse_extractor_args("youtube:player_client=android;tiktok:app_version=35.1.3")
    assert set(result) == {"youtube", "tiktok"}
    assert result["tiktok"]["app_version"] == ["35.1.3"]


def test_extractor_name_is_lowercased() -> None:
    assert "tiktok" in parse_extractor_args("TikTok:api_hostname=example.com")


def test_spaces_are_tolerated() -> None:
    assert parse_extractor_args(" tiktok : api_hostname = example.com ") == {
        "tiktok": {"api_hostname": ["example.com"]}
    }


@pytest.mark.parametrize("broken", ["ерунда", "tiktok", "=value", ":", "tiktok:"])
def test_broken_settings_are_skipped(broken: str, caplog: pytest.LogCaptureFixture) -> None:
    """Опечатка в необязательном параметре не должна ронять бота."""
    with caplog.at_level(logging.WARNING):
        assert parse_extractor_args(broken) == {}
    assert "Пропускаю" in caplog.text


def test_good_part_survives_broken_neighbour() -> None:
    result = parse_extractor_args("ерунда;tiktok:api_hostname=example.com")
    assert result == {"tiktok": {"api_hostname": ["example.com"]}}


def test_value_may_be_empty() -> None:
    assert parse_extractor_args("tiktok:api_hostname=") == {"tiktok": {"api_hostname": []}}


# ── попадание в настройки yt-dlp ───────────────────────────────────────


def test_args_reach_ytdlp_options() -> None:
    directory = Path(tempfile.mkdtemp())
    worker = Downloader(
        make_settings(directory, extractor_args="tiktok:api_hostname=example.com")
    )
    opts = worker._base_opts(directory)
    assert opts["extractor_args"] == {"tiktok": {"api_hostname": ["example.com"]}}


def test_no_args_means_no_option() -> None:
    directory = Path(tempfile.mkdtemp())
    opts = Downloader(make_settings(directory))._base_opts(directory)
    assert "extractor_args" not in opts
