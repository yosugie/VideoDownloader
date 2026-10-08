"""Конфигурация бота: читается один раз из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Конфигурация задана неверно."""


def _env(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом, а не {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} должен быть не меньше {minimum}, получено {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _env_ids(name: str) -> frozenset[int]:
    raw = _env(name)
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").replace(" ", ",").split(","):
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(
                f"{name} должен содержать только числовые Telegram ID, "
                f"не удалось разобрать {chunk!r}"
            ) from exc
    return frozenset(ids)


def _env_path(name: str) -> Path | None:
    raw = _env(name)
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True, slots=True)
class Settings:
    """Все настройки бота."""

    bot_token: str
    allowed_user_ids: frozenset[int]
    blocked_user_ids: frozenset[int]
    admin_ids: frozenset[int]
    daily_limit_per_user: int
    max_file_size_mb: int
    max_duration_minutes: int
    max_video_height: int
    max_concurrent_downloads: int
    min_free_disk_mb: int
    max_download_rate: str | None
    rate_limit_seconds: float
    upload_timeout: int
    download_dir: Path
    stats_db: Path
    access_db: Path
    stats_store_urls: bool
    allow_any_site: bool
    cookies_file: Path | None
    cookies_from_browser: str | None
    proxy: str | None
    extractor_args: str | None
    telegram_proxy: str | None
    api_base_url: str | None
    log_level: str

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def min_free_disk_bytes(self) -> int:
        return self.min_free_disk_mb * 1024 * 1024

    @property
    def max_duration_seconds(self) -> int:
        return self.max_duration_minutes * 60

    @property
    def is_private(self) -> bool:
        """True, если доступ ограничен белым списком."""
        return bool(self.allowed_user_ids)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


def load_settings(env_file: str | os.PathLike[str] | None = ".env") -> Settings:
    """Собирает :class:`Settings` из окружения и файла ``.env``."""
    if env_file is not None and Path(env_file).is_file():
        load_dotenv(env_file)

    token = _env("BOT_TOKEN")
    if not token:
        raise ConfigError(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env "
            "и впишите токен, полученный у @BotFather."
        )

    download_dir = _env_path("DOWNLOAD_DIR") or Path("downloads")
    download_dir.mkdir(parents=True, exist_ok=True)

    cookies_file = _env_path("COOKIES_FILE")
    if cookies_file is not None and not cookies_file.is_file():
        raise ConfigError(f"COOKIES_FILE указывает на несуществующий файл: {cookies_file}")

    browser = _env("COOKIES_FROM_BROWSER")
    if browser is not None:
        browser = browser.lower()

    return Settings(
        bot_token=token,
        allowed_user_ids=_env_ids("ALLOWED_USER_IDS"),
        blocked_user_ids=_env_ids("BLOCKED_USER_IDS"),
        admin_ids=_env_ids("ADMIN_IDS"),
        daily_limit_per_user=_env_int("DAILY_LIMIT_PER_USER", 0, minimum=0),
        max_file_size_mb=_env_int("MAX_FILE_SIZE_MB", 50),
        max_duration_minutes=_env_int("MAX_DURATION_MINUTES", 90),
        max_video_height=_env_int("MAX_VIDEO_HEIGHT", 1080, minimum=144),
        max_concurrent_downloads=_env_int("MAX_CONCURRENT_DOWNLOADS", 2),
        min_free_disk_mb=_env_int("MIN_FREE_DISK_MB", 2048, minimum=0),
        max_download_rate=_env("MAX_DOWNLOAD_RATE"),
        upload_timeout=_env_int("UPLOAD_TIMEOUT_SECONDS", 600, minimum=30),
        rate_limit_seconds=float(_env_int("RATE_LIMIT_SECONDS", 2, minimum=0)),
        download_dir=download_dir,
        stats_db=_env_path("STATS_DB") or Path("stats.db"),
        access_db=_env_path("ACCESS_DB") or Path("access.db"),
        stats_store_urls=_env_bool("STATS_STORE_URLS", False),
        allow_any_site=_env_bool("ALLOW_ANY_SITE", False),
        cookies_file=cookies_file,
        cookies_from_browser=browser,
        proxy=_env("PROXY"),
        extractor_args=_env("EXTRACTOR_ARGS"),
        telegram_proxy=_env("TELEGRAM_PROXY"),
        api_base_url=_env("TELEGRAM_API_BASE_URL"),
        log_level=(_env("LOG_LEVEL") or "INFO").upper(),
    )
