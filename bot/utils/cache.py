"""Короткоживущий кэш ссылок.

В ``callback_data`` Telegram помещается только 64 байта, поэтому сама
ссылка туда не влезает. Вместо неё кладём короткий токен, а URL храним
в памяти процесса.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingLink:
    url: str
    platform_key: str
    created_at: float


class PendingLinks:
    """Хранилище ссылок, ожидающих выбора формата."""

    def __init__(self, *, ttl_seconds: int = 3600, max_items: int = 1000) -> None:
        self._ttl = ttl_seconds
        self._max_items = max_items
        self._items: OrderedDict[str, PendingLink] = OrderedDict()

    def put(self, url: str, platform_key: str) -> str:
        """Сохраняет ссылку и возвращает токен для ``callback_data``."""
        self._evict()
        token = secrets.token_urlsafe(8)
        self._items[token] = PendingLink(url, platform_key, time.monotonic())
        self._items.move_to_end(token)
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)
        return token

    def get(self, token: str) -> PendingLink | None:
        """Возвращает ссылку по токену либо ``None``, если она устарела."""
        self._evict()
        return self._items.get(token)

    def pop(self, token: str) -> PendingLink | None:
        self._evict()
        return self._items.pop(token, None)

    def _evict(self) -> None:
        deadline = time.monotonic() - self._ttl
        while self._items:
            token, item = next(iter(self._items.items()))
            if item.created_at >= deadline:
                break
            self._items.pop(token, None)

    def __len__(self) -> int:
        self._evict()
        return len(self._items)
