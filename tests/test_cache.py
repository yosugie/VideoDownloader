"""Тесты кэша ожидающих ссылок."""

from __future__ import annotations

from bot.utils.cache import PendingLinks


def test_put_and_get_roundtrip() -> None:
    cache = PendingLinks()
    token = cache.put("https://youtu.be/abc", "youtube")
    item = cache.get(token)
    assert item is not None
    assert item.url == "https://youtu.be/abc"
    assert item.platform_key == "youtube"


def test_token_fits_callback_data_limit() -> None:
    """``callback_data`` ограничен 64 байтами, токен должен быть коротким."""
    cache = PendingLinks()
    token = cache.put("https://youtu.be/abc", "youtube")
    assert len(token.encode()) <= 16


def test_unknown_token_returns_none() -> None:
    assert PendingLinks().get("нет такого") is None


def test_pop_removes_entry() -> None:
    cache = PendingLinks()
    token = cache.put("https://youtu.be/abc", "youtube")
    assert cache.pop(token) is not None
    assert cache.get(token) is None


def test_expired_entries_are_dropped() -> None:
    cache = PendingLinks(ttl_seconds=0)
    token = cache.put("https://youtu.be/abc", "youtube")
    assert cache.get(token) is None
    assert len(cache) == 0


def test_max_items_evicts_oldest() -> None:
    cache = PendingLinks(max_items=3)
    tokens = [cache.put(f"https://youtu.be/{i}", "youtube") for i in range(5)]
    assert len(cache) == 3
    assert cache.get(tokens[0]) is None
    assert cache.get(tokens[-1]) is not None
