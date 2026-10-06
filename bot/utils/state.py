"""Состояние бота, которым можно управлять на ходу.

Перезапуск службы ради паузы — лишнее действие, особенно когда владелец
не за компьютером. Флаг живёт в памяти процесса: при перезапуске бот
снова включается, и это скорее удобно, чем нет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class RuntimeState:
    """Переключатель «принимаем загрузки или нет»."""

    paused: bool = False
    paused_at: datetime | None = field(default=None)

    def pause(self) -> None:
        self.paused = True
        self.paused_at = datetime.now()

    def resume(self) -> None:
        self.paused = False
        self.paused_at = None

    @property
    def paused_since(self) -> str:
        if self.paused_at is None:
            return "—"
        return self.paused_at.strftime("%H:%M")
