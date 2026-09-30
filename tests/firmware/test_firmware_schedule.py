"""The daily firmware check (design 2026-09-30, section 6.2)."""

import asyncio
from datetime import datetime, time, timedelta, timezone

import pytest

from loxmatter.firmware.schedule import run_daily, seconds_until

TZ = timezone(timedelta(hours=2))


def test_seconds_until_later_today():
    assert seconds_until(datetime(2026, 9, 30, 5, 0, tzinfo=TZ), time(6, 0)) == 3600


def test_seconds_until_tomorrow_when_the_time_has_passed():
    assert seconds_until(datetime(2026, 9, 30, 7, 0, tzinfo=TZ), time(6, 0)) == 23 * 3600


def test_seconds_until_exactly_now_is_tomorrow():
    assert seconds_until(datetime(2026, 9, 30, 6, 0, tzinfo=TZ), time(6, 0)) == 24 * 3600


class _Stop(Exception):
    pass


class _Settings:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def get_daily_check_enabled(self) -> bool:
        return self.enabled


class _Checker:
    def __init__(self) -> None:
        self.runs = 0

    async def check_all(self) -> None:
        self.runs += 1


def _driver(days: int):
    """A clock that jumps to the requested wake-up time, for `days` days."""
    state = {"now": datetime(2026, 9, 30, 5, 0, tzinfo=TZ), "sleeps": 0}

    async def sleep(seconds: float) -> None:
        state["sleeps"] += 1
        if state["sleeps"] > days:
            raise _Stop
        state["now"] += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    return state, (lambda: state["now"]), sleep


async def test_runs_once_a_day_at_six():
    state, now, sleep = _driver(days=3)
    checker = _Checker()
    with pytest.raises(_Stop):
        await run_daily(checker, _Settings(True), now=now, sleep=sleep)
    assert checker.runs == 3
    assert state["now"].time() == time(6, 0)


async def test_does_not_run_when_switched_off():
    _, now, sleep = _driver(days=2)
    checker = _Checker()
    with pytest.raises(_Stop):
        await run_daily(checker, _Settings(False), now=now, sleep=sleep)
    assert checker.runs == 0


async def test_a_failing_check_does_not_end_the_schedule():
    _, now, sleep = _driver(days=2)

    class Failing(_Checker):
        async def check_all(self) -> None:
            self.runs += 1
            raise RuntimeError("boom")

    checker = Failing()
    with pytest.raises(_Stop):
        await run_daily(checker, _Settings(True), now=now, sleep=sleep)
    assert checker.runs == 2
