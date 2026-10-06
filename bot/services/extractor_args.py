"""Разбор дополнительных настроек экстракторов yt-dlp.

Сервисы иногда ломаются способом, который лечится не кодом, а особым
параметром: у TikTok, например, при недоступности обычных страниц
помогает обращение к другому хосту API. Держать такие обходные пути
в настройках удобнее, чем править код при каждом их изменении.

Формат строки повторяет ключ ``--extractor-args`` самого yt-dlp::

    tiktok:api_hostname=api22-normal-c-useast2a.tiktokv.com
    youtube:player_client=android,web;tiktok:app_version=35.1.3
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

ExtractorArgs = dict[str, dict[str, list[str]]]


def parse_extractor_args(raw: str | None) -> ExtractorArgs:
    """
    Превращает строку настроек в структуру, понятную yt-dlp.

    Непонятные куски пропускаются с предупреждением: из-за опечатки в
    необязательном параметре бот запускаться не должен.
    """
    if not raw:
        return {}

    result: ExtractorArgs = {}
    for chunk in raw.split(";"):
        entry = chunk.strip()
        if not entry:
            continue
        if ":" not in entry or "=" not in entry:
            log.warning("Пропускаю непонятную настройку экстрактора: %r", entry)
            continue

        extractor, _, rest = entry.partition(":")
        key, _, values = rest.partition("=")
        extractor = extractor.strip().lower()
        key = key.strip()
        if not extractor or not key:
            log.warning("Пропускаю непонятную настройку экстрактора: %r", entry)
            continue

        parsed = [value.strip() for value in values.split(",") if value.strip()]
        result.setdefault(extractor, {})[key] = parsed

    return result
