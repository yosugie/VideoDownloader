"""Разбор ограничения скорости скачивания."""

from __future__ import annotations

import re

_RATE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([kmg])?\s*$", re.IGNORECASE)
_FACTORS = {"k": 1024, "m": 1024**2, "g": 1024**3}


def parse_rate(raw: str | None) -> int | None:
    """
    ``"5M"`` → ``5242880``. ``None`` или мусор — без ограничения.

    Ограничение скорости нужно, когда бот делит канал с чем-то ещё:
    без него одна загрузка забирает всю полосу, и соседние службы
    начинают заметно тормозить.
    """
    if not raw:
        return None
    match = _RATE_RE.match(raw)
    if match is None:
        return None
    value = float(match.group(1).replace(",", "."))
    suffix = (match.group(2) or "").lower()
    result = int(value * _FACTORS.get(suffix, 1))
    return result if result > 0 else None


def format_rate(limit: int | None) -> str:
    """Для сообщений и журнала."""
    if not limit:
        return "без ограничения"
    if limit >= 1024**2:
        return f"{limit / 1024**2:.1f} МБ/с"
    return f"{limit / 1024:.0f} КБ/с"
