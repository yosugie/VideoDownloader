"""Человекочитаемое форматирование размеров, времени и прогресса."""

from __future__ import annotations

from html import escape

from bot.services.downloader import Progress

_UNITS = ("Б", "КБ", "МБ", "ГБ")


def human_size(size: int | float | None) -> str:
    """``15728640`` → ``15.0 МБ``."""
    if not size or size < 0:
        return "—"
    value = float(size)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            precision = 0 if unit == "Б" else 1
            return f"{value:.{precision}f} {unit}"
        value /= 1024
    return f"{value:.1f} {_UNITS[-1]}"


def human_duration(seconds: int | float | None) -> str:
    """``3725`` → ``1:02:05``."""
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def human_speed(speed: float | None) -> str:
    """Скорость загрузки в МБ/с или КБ/с."""
    if not speed or speed <= 0:
        return "—"
    return f"{human_size(speed)}/с"


def progress_bar(percent: float | None, width: int = 10) -> str:
    """Текстовый индикатор вида ``▰▰▰▱▱▱▱▱▱▱``."""
    if percent is None:
        return "▱" * width
    filled = max(0, min(width, round(percent / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def render_progress(progress: Progress) -> str:
    """Собирает сообщение о статусе загрузки."""
    lines = [f"<b>{escape(progress.stage)}</b>"]

    if progress.percent is not None:
        lines.append(f"{progress_bar(progress.percent)} {progress.percent:.0f}%")

    details: list[str] = []
    if progress.downloaded is not None and progress.total:
        details.append(f"{human_size(progress.downloaded)} / {human_size(progress.total)}")
    elif progress.downloaded:
        details.append(human_size(progress.downloaded))
    if progress.speed:
        details.append(human_speed(progress.speed))
    if progress.eta:
        details.append(f"осталось ~{human_duration(progress.eta)}")
    if details:
        lines.append(escape(" · ".join(details)))

    return "\n".join(lines)
