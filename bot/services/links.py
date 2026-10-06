"""Поиск ссылок в тексте сообщения и определение платформы."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

#: Ссылка внутри произвольного текста. Закрывающие скобки и знаки
#: препинания на конце отбрасываются отдельно в :func:`_trim`.
_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)

#: Мусор, который часто прилипает к ссылке, когда её копируют из текста.
_TRAILING = ".,;:!?)]}>\"'»"

#: Префиксы хоста, которые ничего не значат для определения платформы.
_HOST_PREFIXES = ("www.", "m.", "mobile.", "ru.", "web.")


@dataclass(frozen=True, slots=True)
class Platform:
    """Поддерживаемый сервис."""

    key: str
    title: str
    emoji: str

    def __str__(self) -> str:
        return f"{self.emoji} {self.title}"


YOUTUBE = Platform("youtube", "YouTube", "▶️")
INSTAGRAM = Platform("instagram", "Instagram", "📸")
TIKTOK = Platform("tiktok", "TikTok", "🎬")
OTHER = Platform("other", "Другой сайт", "🌐")

#: Домены сервисов. Ключ сравнивается с хостом целиком либо как суффикс,
#: поэтому ``vm.tiktok.com`` и ``youtu.be`` определяются автоматически.
_DOMAINS: dict[str, Platform] = {
    "youtube.com": YOUTUBE,
    "youtu.be": YOUTUBE,
    "youtube-nocookie.com": YOUTUBE,
    "instagram.com": INSTAGRAM,
    "instagr.am": INSTAGRAM,
    "cdninstagram.com": INSTAGRAM,
    "tiktok.com": TIKTOK,
    "douyin.com": TIKTOK,
}

#: Что показываем пользователю в списке поддерживаемого.
SUPPORTED_PLATFORMS: tuple[Platform, ...] = (YOUTUBE, INSTAGRAM, TIKTOK)


def _trim(url: str) -> str:
    """Убирает знаки препинания, прилипшие к концу ссылки."""
    while url and url[-1] in _TRAILING:
        # Закрывающую скобку оставляем, если она часть самой ссылки.
        if url[-1] == ")" and url.count("(") > url.count(")") - 1:
            break
        url = url[:-1]
    return url


def find_url(text: str | None) -> str | None:
    """Возвращает первую ссылку из текста или ``None``."""
    if not text:
        return None
    match = _URL_RE.search(text)
    if match is None:
        return None
    url = _trim(match.group(0))
    return url or None


def normalize_host(url: str) -> str:
    """Хост ссылки в нижнем регистре без незначащих префиксов."""
    host = (urlsplit(url).hostname or "").lower()
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    return host


def detect_platform(url: str) -> Platform | None:
    """Определяет сервис по ссылке. ``None`` — сервис не из списка."""
    host = normalize_host(url)
    if not host:
        return None
    if host in _DOMAINS:
        return _DOMAINS[host]
    for domain, platform in _DOMAINS.items():
        if host.endswith("." + domain):
            return platform
    return None


def resolve_platform(url: str, *, allow_any_site: bool) -> Platform | None:
    """
    Платформа для загрузки.

    Возвращает ``None``, если ссылка не из поддерживаемых сервисов
    и режим ``ALLOW_ANY_SITE`` выключен.
    """
    platform = detect_platform(url)
    if platform is not None:
        return platform
    return OTHER if allow_any_site else None
