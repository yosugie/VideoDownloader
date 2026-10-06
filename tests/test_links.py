"""Тесты разбора ссылок."""

from __future__ import annotations

import pytest

from bot.services import links


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://youtu.be/aBcD1234xyz", "https://youtu.be/aBcD1234xyz"),
        ("смотри это https://vm.tiktok.com/ZSabc123/ круто", "https://vm.tiktok.com/ZSabc123/"),
        ("(https://www.instagram.com/reel/Cx1y2z3/)", "https://www.instagram.com/reel/Cx1y2z3/"),
        ("ссылка: https://youtu.be/abc.", "https://youtu.be/abc"),
        ("без ссылки вовсе", None),
        ("", None),
        (None, None),
    ],
)
def test_find_url(text: str | None, expected: str | None) -> None:
    assert links.find_url(text) == expected


def test_find_url_takes_first() -> None:
    text = "https://youtu.be/one и https://youtu.be/two"
    assert links.find_url(text) == "https://youtu.be/one"


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.youtube.com/watch?v=aBcD1234xyz", links.YOUTUBE),
        ("https://youtu.be/aBcD1234xyz", links.YOUTUBE),
        ("https://m.youtube.com/shorts/abc", links.YOUTUBE),
        ("https://www.youtube.com/shorts/abc", links.YOUTUBE),
        ("https://www.instagram.com/reel/Cx1y2z3/", links.INSTAGRAM),
        ("https://instagr.am/p/Cx1y2z3/", links.INSTAGRAM),
        ("https://www.tiktok.com/@user/video/123", links.TIKTOK),
        ("https://vm.tiktok.com/ZSabc123/", links.TIKTOK),
        ("https://vt.tiktok.com/ZSabc123/", links.TIKTOK),
    ],
)
def test_detect_platform(url: str, platform: links.Platform) -> None:
    assert links.detect_platform(url) == platform


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/video/1",
        "https://notyoutube.com/watch?v=1",
        "https://vimeo.com/123",
        "not-a-url",
    ],
)
def test_detect_platform_unknown(url: str) -> None:
    assert links.detect_platform(url) is None


def test_lookalike_domain_is_not_matched() -> None:
    """``youtube.com.evil.net`` не должен считаться YouTube."""
    assert links.detect_platform("https://youtube.com.evil.net/watch?v=1") is None


def test_resolve_platform_respects_allow_any_site() -> None:
    url = "https://vimeo.com/123"
    assert links.resolve_platform(url, allow_any_site=False) is None
    assert links.resolve_platform(url, allow_any_site=True) == links.OTHER


def test_resolve_platform_prefers_known_service() -> None:
    url = "https://youtu.be/abc"
    assert links.resolve_platform(url, allow_any_site=True) == links.YOUTUBE


def test_normalize_host_strips_prefixes() -> None:
    assert links.normalize_host("https://www.youtube.com/watch") == "youtube.com"
    assert links.normalize_host("https://m.tiktok.com/x") == "tiktok.com"
