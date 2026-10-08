"""Проверки окружения при запуске бота."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from aiogram import Dispatcher

import bot.__main__ as main
from tests.test_downloader import make_settings

NETSCAPE = "\n".join(
    [
        "# Netscape HTTP Cookie File",
        "\t".join([".instagram.com", "TRUE", "/", "TRUE", "0", "sessionid", "secret"]),
        "\t".join([".instagram.com", "TRUE", "/", "TRUE", "0", "ds_user_id", "42"]),
    ]
)


def test_valid_cookies_are_reported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(NETSCAPE, encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="bot"):
        main._check_cookies(make_settings(tmp_path, cookies_file=cookies))

    assert "2 записей" in caplog.text
    assert "instagram.com" in caplog.text


def test_json_export_is_rejected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Частая ошибка: расширение отдаёт JSON вместо формата Netscape."""
    cookies = tmp_path / "cookies.txt"
    cookies.write_text('[{"name": "sessionid", "value": "secret"}]', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="bot"):
        main._check_cookies(make_settings(tmp_path, cookies_file=cookies))

    assert "JSON" in caplog.text
    assert "Netscape" in caplog.text


def test_empty_file_is_reported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="bot"):
        main._check_cookies(make_settings(tmp_path, cookies_file=cookies))

    assert "не нашлось ни одной записи" in caplog.text


def test_browser_source_is_mentioned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="bot"):
        main._check_cookies(make_settings(tmp_path, cookies_from_browser="firefox"))

    assert "firefox" in caplog.text


def test_silent_when_cookies_are_not_configured(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="bot"):
        main._check_cookies(make_settings(tmp_path))

    assert caplog.text == ""


def test_cookie_values_are_not_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """В логи попадают домены и счётчик, но не сами значения."""
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(NETSCAPE, encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="bot"):
        main._check_cookies(make_settings(tmp_path, cookies_file=cookies))

    assert "secret" not in caplog.text


# ── сломанный источник cookies не должен ломать весь бот ───────────────


def test_unusable_browser_is_disabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Firefox не установлен — раньше это ломало скачивание отовсюду."""
    monkeypatch.setattr(
        main, "_browser_cookies_error", lambda browser: "could not find firefox cookies database"
    )
    settings = make_settings(tmp_path, cookies_from_browser="firefox")

    with caplog.at_level(logging.ERROR, logger="bot"):
        result = main._check_cookies(settings)

    assert result.cookies_from_browser is None, "источник должен быть отключён"
    assert "БЕЗ cookies" in caplog.text
    assert "YouTube и TikTok" in caplog.text


def test_working_browser_is_kept(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "_browser_cookies_error", lambda browser: None)
    settings = make_settings(tmp_path, cookies_from_browser="firefox")

    with caplog.at_level(logging.INFO, logger="bot"):
        result = main._check_cookies(settings)

    assert result.cookies_from_browser == "firefox"
    assert "firefox" in caplog.text


def test_broken_cookie_file_is_disabled(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text('[{"name": "sessionid"}]', encoding="utf-8")

    result = main._check_cookies(make_settings(tmp_path, cookies_file=cookies))

    assert result.cookies_file is None


def test_good_cookie_file_is_kept(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(NETSCAPE, encoding="utf-8")

    result = main._check_cookies(make_settings(tmp_path, cookies_file=cookies))

    assert result.cookies_file == cookies


def test_missing_browser_reports_real_error(tmp_path: Path) -> None:
    """Проверка обращается к настоящему механизму yt-dlp, а не к заглушке."""
    error = main._browser_cookies_error("firefox")
    # В окружении без Firefox это строка с причиной, с Firefox — None.
    assert error is None or isinstance(error, str)


# ── зависимости, без которых часть сервисов не работает ────────────────


def test_impersonation_backend_is_installed() -> None:
    """
    TikTok отдаёт страницу-проверку, которую yt-dlp проходит, только
    притворяясь браузером. Без curl-cffi это невозможно, и ошибка
    выглядит как невнятное "Unexpected response from webpage request".
    """
    import curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget  # noqa: F401


def test_yt_dlp_sees_impersonate_targets() -> None:
    """Мало установить библиотеку — yt-dlp должен её увидеть."""
    from yt_dlp import YoutubeDL

    with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        targets = ydl._get_available_impersonate_targets()

    assert targets, "yt-dlp не нашёл ни одной цели — проверки TikTok не пройти"


def test_startup_warns_without_curl_cffi(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если библиотека пропадёт, владелец должен узнать об этом из журнала."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "curl_cffi":
            raise ImportError("нет такого модуля")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level(logging.WARNING, logger="bot"):
        main.check_environment(make_settings(tmp_path))

    assert "curl-cffi" in caplog.text
    assert "TikTok" in caplog.text


# ── имена зависимостей ─────────────────────────────────────────────────

#: Имена, которые aiogram подставляет в обработчики сам. Если положить
#: своё значение под таким именем, оно будет молча затёрто: "state", к
#: примеру, на каждом событии заменяется на FSMContext, и обработчик
#: получает совсем не то, что ожидает.
AIOGRAM_RESERVED = frozenset(
    {
        "bot",
        "state",
        "raw_state",
        "fsm_storage",
        "event_update",
        "event_router",
        "event_context",
        "event_from_user",
        "event_chat",
        "handler",
    }
)


def test_dependencies_do_not_shadow_aiogram(dispatcher: Dispatcher) -> None:
    """
    Наши зависимости не должны называться так же, как внутренние.

    Ошибку такого рода не видно ни линтером, ни обычными тестами: бот
    запускается, а падает на каждом событии.
    """
    ours = set(dispatcher.workflow_data) - {"dispatcher"}

    clashes = ours & AIOGRAM_RESERVED
    assert not clashes, f"эти имена aiogram перезапишет: {sorted(clashes)}"


def test_reserved_list_matches_aiogram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сверяемся с самим aiogram, чтобы список не устарел молча."""
    import inspect

    from aiogram.fsm.middleware import FSMContextMiddleware

    source = inspect.getsource(FSMContextMiddleware.__call__)
    assert '"state"' in source, "aiogram больше не занимает имя state — список пора обновить"


def test_no_databases_are_tracked_in_git() -> None:
    """
    В репозитории не должно быть баз данных.

    Однажды тестовая база попала в коммит и заблокировала обновление на
    сервере: git отказался перезаписывать чужой файл. Хуже того, пройди
    обновление — рабочая статистика была бы затёрта.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git недоступен")

    tracked = result.stdout.split()
    databases = [name for name in tracked if name.endswith((".db", ".sqlite", ".sqlite3"))]
    assert not databases, f"в репозитории лежат базы данных: {databases}"
