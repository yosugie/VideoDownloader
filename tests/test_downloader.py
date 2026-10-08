"""Тесты вспомогательной логики загрузчика (без обращения к сети)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bot.config import Settings
from bot.services import downloader as dl


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "bot_token": "111:test",
        "allowed_user_ids": frozenset(),
        "blocked_user_ids": frozenset(),
        "admin_ids": frozenset(),
        "daily_limit_per_user": 0,
        "max_file_size_mb": 50,
        "max_duration_minutes": 90,
        "max_video_height": 1080,
        "max_concurrent_downloads": 2,
        "min_free_disk_mb": 0,
        "max_download_rate": None,
        "rate_limit_seconds": 0.0,
        "upload_timeout": 600,
        "download_dir": tmp_path,
        "stats_db": tmp_path / "stats.db",
        "access_db": tmp_path / "access.db",
        "stats_store_urls": False,
        "allow_any_site": False,
        "cookies_file": None,
        "cookies_from_browser": None,
        "proxy": None,
        "extractor_args": None,
        "telegram_proxy": None,
        "api_base_url": None,
        "log_level": "INFO",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ── _first_entry ────────────────────────────────────────────────────────


def test_first_entry_returns_plain_video() -> None:
    info = {"id": "abc", "title": "Видео"}
    assert dl._first_entry(info) == info


def test_first_entry_unwraps_playlist() -> None:
    info = {"_type": "playlist", "entries": [{"id": "first"}, {"id": "second"}]}
    assert dl._first_entry(info)["id"] == "first"


def test_first_entry_accepts_generator_entries() -> None:
    info = {"_type": "playlist", "entries": iter([{"id": "lazy"}])}
    assert dl._first_entry(info)["id"] == "lazy"


def test_first_entry_unwraps_nested_playlist() -> None:
    info = {
        "_type": "playlist",
        "entries": [{"_type": "playlist", "entries": [{"id": "deep"}]}],
    }
    assert dl._first_entry(info)["id"] == "deep"


def test_first_entry_rejects_empty_result() -> None:
    with pytest.raises(dl.ExtractionFailed):
        dl._first_entry(None)


# ── выбор итогового файла ──────────────────────────────────────────────


def test_pick_media_file_prefers_ytdlp_path(tmp_path: Path) -> None:
    final = tmp_path / "video.mp4"
    final.write_bytes(b"x" * 100)
    (tmp_path / "junk.mp4").write_bytes(b"y" * 5000)

    info = {"requested_downloads": [{"filepath": str(final)}]}
    assert dl.Downloader._pick_media_file(tmp_path, info) == final


def test_pick_media_file_falls_back_to_largest(tmp_path: Path) -> None:
    (tmp_path / "small.mp4").write_bytes(b"x" * 10)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * 999)

    assert dl.Downloader._pick_media_file(tmp_path, {}) == big


def test_pick_media_file_ignores_thumbnails_and_junk(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x" * 100)
    (tmp_path / "cover.jpg").write_bytes(b"y" * 9999)
    (tmp_path / "clip.mp4.part").write_bytes(b"z" * 9999)
    (tmp_path / "meta.info.json").write_bytes(b"{}" * 9999)

    assert dl.Downloader._pick_media_file(tmp_path, {}) == media


def test_pick_media_file_honours_prefer_ext(tmp_path: Path) -> None:
    (tmp_path / "source.webm").write_bytes(b"x" * 9999)
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"x" * 10)

    assert dl.Downloader._pick_media_file(tmp_path, {}, prefer_ext=".mp3") == mp3


def test_pick_media_file_returns_none_when_empty(tmp_path: Path) -> None:
    assert dl.Downloader._pick_media_file(tmp_path, {}) is None


def test_pick_media_file_skips_stale_ytdlp_path(tmp_path: Path) -> None:
    """Если yt-dlp сообщил путь, которого нет, берём файл из каталога."""
    real = tmp_path / "real.mp4"
    real.write_bytes(b"x" * 100)
    info = {"requested_downloads": [{"filepath": str(tmp_path / "ghost.mp4")}]}

    assert dl.Downloader._pick_media_file(tmp_path, info) == real


# ── перевод ошибок ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ERROR: File is larger than max-filesize", dl.MediaTooLarge),
        ("ERROR: Private video. Sign in if you've been granted access", dl.AuthRequired),
        ("ERROR: Unsupported URL: https://example.com", dl.ExtractionFailed),
        ("ERROR: Video unavailable", dl.ExtractionFailed),
        ("ERROR: что-то неизвестное", dl.ExtractionFailed),
    ],
)
def test_translate_maps_errors(tmp_path: Path, message: str, expected: type) -> None:
    worker = dl.Downloader(make_settings(tmp_path))
    assert isinstance(worker._translate(Exception(message)), expected)


def test_translate_produces_russian_text(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(Exception("ERROR: login required"))
    assert isinstance(error, dl.AuthRequired)
    assert "авторизован" in str(error).lower()


# ── служебное ──────────────────────────────────────────────────────────


def test_media_cleanup_removes_workdir(tmp_path: Path) -> None:
    workdir = tmp_path / "job"
    workdir.mkdir()
    media_path = workdir / "clip.mp4"
    media_path.write_bytes(b"x")

    media = dl.Media(
        path=media_path, workdir=workdir, kind=dl.KIND_VIDEO, title="Тест", size=1
    )
    media.cleanup()
    assert not workdir.exists()


def test_progress_hook_fills_state() -> None:
    progress = dl.Progress()
    hook = dl._make_hook(progress)

    hook(
        {
            "status": "downloading",
            "downloaded_bytes": 500,
            "total_bytes": 1000,
            "speed": 2048,
            "eta": 7,
        }
    )
    assert progress.percent == pytest.approx(50.0)
    assert progress.total == 1000
    assert progress.eta == 7

    hook({"status": "finished"})
    assert progress.percent is None
    assert "кле" in progress.stage.lower()


def test_progress_hook_survives_unknown_sizes() -> None:
    progress = dl.Progress()
    hook = dl._make_hook(progress)
    hook({"status": "downloading", "downloaded_bytes": None, "total_bytes": None})
    assert progress.percent is None


def test_title_falls_back_to_probe_info() -> None:
    assert dl._title_of({}, {"title": "Из пробы"}) == "Из пробы"
    assert dl._title_of({}, {}) == "Видео"


def test_title_takes_first_line_only() -> None:
    assert dl._title_of({"title": "Первая\nвторая"}, {}) == "Первая"


def test_base_opts_wires_cookies_and_proxy(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    worker = dl.Downloader(
        make_settings(tmp_path, cookies_file=cookies, proxy="socks5://127.0.0.1:1080")
    )
    opts = worker._base_opts(tmp_path)
    assert opts["cookiefile"] == str(cookies)
    assert opts["proxy"] == "socks5://127.0.0.1:1080"
    assert opts["noplaylist"] is True


def test_base_opts_uses_browser_cookies_when_no_file(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path, cookies_from_browser="firefox"))
    opts = worker._base_opts(tmp_path)
    assert opts["cookiesfrombrowser"] == ("firefox",)
    assert "cookiefile" not in opts


def test_settings_derived_limits(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_file_size_mb=25, max_duration_minutes=10)
    assert settings.max_file_size_bytes == 25 * 1024 * 1024
    assert settings.max_duration_seconds == 600
    assert settings.is_private is False


# ── совместимость с плеером Telegram ───────────────────────────────────

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="нужны ffmpeg и ffprobe")


def make_clip(path: Path, video_codec: str, audio_codec: str = "aac") -> Path:
    """Создаёт односекундный ролик заданными кодеками."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", video_codec, "-c:a", audio_codec,
            "-shortest", str(path),
        ],
        check=True,
        timeout=120,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


@needs_ffmpeg
def test_probe_codecs_reads_streams(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "clip.mp4", "libx264")
    assert dl._probe_codecs(clip) == ("h264", "aac")


@needs_ffmpeg
def test_vp9_is_reencoded_to_h264(tmp_path: Path) -> None:
    """VP9 Telegram показывает как замерший кадр — такое надо перекодировать."""
    clip = make_clip(tmp_path / "clip.webm", "libvpx-vp9", audio_codec="libopus")
    assert dl._probe_codecs(clip)[0] == "vp9"

    result = dl.Downloader._make_telegram_friendly(clip)

    assert result != clip, "файл должен быть пересобран"
    assert result.is_file()
    assert dl._probe_codecs(result) == ("h264", "aac")


@needs_ffmpeg
def test_h264_is_kept_without_reencoding(tmp_path: Path) -> None:
    """Совместимое видео только перекладывается в контейнер с faststart."""
    clip = make_clip(tmp_path / "clip.mp4", "libx264")
    result = dl.Downloader._make_telegram_friendly(clip)

    assert result.is_file()
    assert dl._probe_codecs(result) == ("h264", "aac")


@needs_ffmpeg
def test_broken_file_is_returned_as_is(tmp_path: Path) -> None:
    """Если ffmpeg не справился, отдаём исходник, а не падаем."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\x00\x01\x02 not a video at all")

    assert dl.Downloader._make_telegram_friendly(broken) == broken


def test_probe_codecs_on_missing_file(tmp_path: Path) -> None:
    assert dl._probe_codecs(tmp_path / "нет-такого.mp4") == (None, None)


# ── фильтр длительности ────────────────────────────────────────────────


def test_duration_filter_rejects_long_video(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path, max_duration_minutes=10))
    rejected: list[float] = []
    match = worker._duration_filter(rejected)

    assert match({"duration": 1200}) is not None
    assert rejected == [1200.0]


def test_duration_filter_passes_short_video(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path, max_duration_minutes=10))
    rejected: list[float] = []
    match = worker._duration_filter(rejected)

    assert match({"duration": 60}) is None
    assert match({}) is None, "без длительности пропускаем"
    assert rejected == []


def test_too_long_message_mentions_limits(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path, max_duration_minutes=10))
    text = str(worker._too_long([1800.0]))
    assert "30" in text and "10" in text


# ── диагностика ошибок ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    ["ERROR: No video formats found!", "ERROR: There's no video in this post"],
)
def test_no_video_is_recognised(tmp_path: Path, message: str) -> None:
    """Это развилка, а не тупик: выше по стеку такой отказ ведёт к картинкам."""
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(Exception(message))
    assert isinstance(error, dl.NoVideoStream)
    assert "фото" in str(error)


def test_translate_keeps_technical_detail(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(Exception("ERROR: совершенно неизвестная беда"))
    assert error.detail is not None
    assert "неизвестная беда" in error.detail


def test_cookies_advice_goes_to_the_owner_not_the_user(tmp_path: Path) -> None:
    """Пользователю совет про cookies бесполезен: README ему недоступен."""
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(
        Exception("ERROR: Unable to extract shared data"),
        "https://www.instagram.com/reel/Cx1y2z3/",
    )
    assert "cookies" not in str(error).lower()
    assert "readme" not in str(error).lower()
    assert error.hint is not None and "cookies" in error.hint.lower()


def test_unknown_error_on_other_site_is_generic(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(Exception("ERROR: что-то странное"), "https://youtu.be/abc")
    assert "cookies" not in str(error).lower()


def test_rate_limit_is_treated_as_auth_problem(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path))
    assert isinstance(worker._translate(Exception("HTTP Error 429")), dl.AuthRequired)


@needs_ffmpeg
def test_audio_only_file_has_no_video_stream(tmp_path: Path) -> None:
    """Так выглядит слайдшоу из TikTok: звук есть, видеодорожки нет."""
    track = tmp_path / "slideshow.mp3"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            str(track),
        ],
        check=True,
        timeout=120,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    video_codec, audio_codec = dl._probe_codecs(track)
    assert video_codec is None, "видеодорожки быть не должно"
    assert audio_codec == "mp3"


@needs_ffmpeg
def test_video_file_has_video_stream(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "clip.mp4", "libx264")
    assert dl._probe_codecs(clip)[0] == "h264"


def test_has_ffprobe_matches_environment() -> None:
    assert dl._has_ffprobe() is (shutil.which("ffprobe") is not None)


def test_no_video_stream_is_a_downloader_error() -> None:
    """Хендлер ловит его отдельной веткой, но общий обработчик тоже должен сработать."""
    assert issubclass(dl.NoVideoStream, dl.DownloaderError)


def test_ansi_colours_are_stripped_from_detail(tmp_path: Path) -> None:
    """yt-dlp подмешивает в текст цвет терминала — в Telegram это мусор."""
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(Exception("\x1b[0;31mERROR:\x1b[0m странная беда"))

    assert error.detail is not None
    assert "\x1b" not in error.detail
    assert "[0;31m" not in error.detail
    assert "ERROR: странная беда" in error.detail


def test_clean_error_text_handles_empty_input() -> None:
    assert dl.clean_error_text("") == ""
    assert dl.clean_error_text("  обычный текст  ") == "обычный текст"


def test_no_video_stream_is_not_confused_with_generic_failure(tmp_path: Path) -> None:
    """Обычный сбой не должен уводить бота за картинками."""
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(Exception("ERROR: Video unavailable"))
    assert not isinstance(error, dl.NoVideoStream)


@pytest.mark.parametrize(
    "message",
    [
        # Дословно то, что Instagram прислал на фотопост с музыкой.
        "ERROR: [Instagram] DdiaZ8st_3B: There is no video in this post",
        "ERROR: [Instagram] Ddp3oqlDe-i: No video formats found!",
        "ERROR: There's no video in this post",
        "ERROR: no media found",
    ],
)
def test_real_no_video_messages_lead_to_photos(tmp_path: Path, message: str) -> None:
    """Каждая из этих формулировок должна уводить бота за картинками."""
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(Exception(message), "https://www.instagram.com/p/abc/")
    assert isinstance(error, dl.NoVideoStream), f"не распознано: {message}"


def test_empty_media_response_is_still_auth_problem(tmp_path: Path) -> None:
    """А это другой случай — пост закрыт, картинки тоже не отдадут."""
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(
        Exception("ERROR: [Instagram] X: Instagram sent an empty media response. "
                  "Check if this post is accessible in your browser without being logged-in."),
        "https://www.instagram.com/p/abc/",
    )
    assert isinstance(error, dl.AuthRequired)
    assert not isinstance(error, dl.NoVideoStream)


# ── точность распознавания ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "video_id",
    [
        "7429183746501928374",  # «429» внутри номера ролика
        "7312904291847562819",
        "4291234567890123456",
    ],
)
def test_tiktok_ids_are_not_mistaken_for_rate_limit(tmp_path: Path, video_id: str) -> None:
    """Номера роликов TikTok содержат любые цифры, и «429» среди них — не ошибка 429."""
    worker = dl.Downloader(make_settings(tmp_path))
    error = worker._translate(
        Exception(f"ERROR: [TikTok] {video_id}: Unable to extract webpage video data"),
        "https://www.tiktok.com/@user/video/" + video_id,
    )
    assert not isinstance(error, dl.AuthRequired), "обычный сбой выдан за требование входа"


def test_real_rate_limit_is_still_recognised(tmp_path: Path) -> None:
    worker = dl.Downloader(make_settings(tmp_path))
    assert isinstance(
        worker._translate(Exception("ERROR: HTTP Error 429: Too Many Requests")),
        dl.AuthRequired,
    )


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [youtube] abc: Sign in to confirm you're not a bot",
        "ERROR: Private video. Sign in if you've been granted access",
        "ERROR: [Instagram] X: Instagram sent an empty media response",
        "ERROR: This video is age-restricted",
        "ERROR: HTTP Error 403: Forbidden",
    ],
)
def test_real_auth_messages_are_recognised(tmp_path: Path, message: str) -> None:
    worker = dl.Downloader(make_settings(tmp_path))
    assert isinstance(worker._translate(Exception(message)), dl.AuthRequired), message


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [TikTok] 7123: Unable to extract webpage video data",
        "ERROR: Unable to download webpage: timed out",
        "ERROR: [TikTok] 7123: account could not be resolved",
    ],
)
def test_ordinary_failures_are_not_auth_errors(tmp_path: Path, message: str) -> None:
    """Слово «account» само по себе ничего не значит."""
    worker = dl.Downloader(make_settings(tmp_path))
    assert not isinstance(worker._translate(Exception(message)), dl.AuthRequired), message


def test_user_messages_never_mention_internals(tmp_path: Path) -> None:
    """Ни одно сообщение пользователю не должно отсылать к README или cookies."""
    worker = dl.Downloader(make_settings(tmp_path))
    messages = [
        "ERROR: login required",
        "ERROR: Video unavailable",
        "ERROR: Unsupported URL: https://example.com",
        "ERROR: что-то неизвестное",
        "ERROR: File is larger than max-filesize",
    ]
    for message in messages:
        for url in ("https://youtu.be/a", "https://www.instagram.com/p/a/"):
            text = str(worker._translate(Exception(message), url)).lower()
            assert "readme" not in text, (message, url)
            assert "cookies" not in text, (message, url)
