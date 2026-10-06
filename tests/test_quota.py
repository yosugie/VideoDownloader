"""Тесты суточного лимита загрузок."""

from __future__ import annotations

from datetime import date, timedelta

from bot.utils.quota import DailyQuota


def test_zero_limit_means_unlimited() -> None:
    quota = DailyQuota(0)
    assert not quota.enabled
    for _ in range(100):
        assert quota.allow(1)
    assert quota.remaining(1) is None


def test_limit_is_enforced_per_user() -> None:
    quota = DailyQuota(3)

    assert [quota.allow(1) for _ in range(4)] == [True, True, True, False]
    # Сосед по лимиту не страдает.
    assert quota.allow(2) is True


def test_remaining_counts_down() -> None:
    quota = DailyQuota(3)
    assert quota.remaining(1) == 3
    quota.allow(1)
    assert quota.remaining(1) == 2
    quota.allow(1)
    quota.allow(1)
    assert quota.remaining(1) == 0


def test_refund_returns_attempt() -> None:
    """Нерабочая ссылка не должна съедать лимит."""
    quota = DailyQuota(2)
    quota.allow(7)
    assert quota.remaining(7) == 1

    quota.refund(7)
    assert quota.remaining(7) == 2


def test_refund_does_not_go_below_zero() -> None:
    quota = DailyQuota(2)
    quota.refund(7)
    quota.refund(7)
    assert quota.remaining(7) == 2


def test_refund_is_harmless_without_limit() -> None:
    quota = DailyQuota(0)
    quota.refund(7)
    assert quota.remaining(7) is None


def test_counters_reset_next_day() -> None:
    quota = DailyQuota(1)
    assert quota.allow(1) is True
    assert quota.allow(1) is False

    # Притворяемся, что наступило завтра.
    quota._day = date.today() - timedelta(days=1)

    assert quota.allow(1) is True, "с новыми сутками счётчик обнуляется"


def test_users_today_counts_distinct_people() -> None:
    quota = DailyQuota(5)
    quota.allow(1)
    quota.allow(1)
    quota.allow(2)
    assert quota.users_today() == 2


def test_negative_limit_is_treated_as_unlimited() -> None:
    quota = DailyQuota(-5)
    assert not quota.enabled
    assert quota.allow(1) is True
