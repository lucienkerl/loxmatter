"""The daily firmware check (design 2026-09-30, section 6.2)."""

import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from loxmatter.firmware.schedule import run_daily, seconds_until

BERLIN = ZoneInfo("Europe/Berlin")

TZ = timezone(timedelta(hours=2))


def test_seconds_until_later_today():
    assert seconds_until(datetime(2026, 9, 30, 5, 0, tzinfo=TZ), time(6, 0)) == 3600


def test_seconds_until_tomorrow_when_the_time_has_passed():
    assert seconds_until(datetime(2026, 9, 30, 7, 0, tzinfo=TZ), time(6, 0)) == 23 * 3600


def test_seconds_until_exactly_now_is_tomorrow():
    assert seconds_until(datetime(2026, 9, 30, 6, 0, tzinfo=TZ), time(6, 0)) == 24 * 3600


def test_target_stays_at_six_wall_clock_across_the_autumn_change():
    # 2026-10-25 is the fall-back day: the day is 25 hours long, so 07:00 to 06:00 takes 24 hours.
    now = datetime(2026, 10, 24, 7, 0, tzinfo=BERLIN)
    assert seconds_until(now, time(6, 0)) == 24 * 3600


def test_target_stays_at_six_wall_clock_across_the_spring_change():
    # 2026-03-29 is the spring-forward day: the day is 23 hours long, so 07:00 to 06:00 takes 22 hours.
    now = datetime(2026, 3, 28, 7, 0, tzinfo=BERLIN)
    assert seconds_until(now, time(6, 0)) == 22 * 3600


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


async def test_an_early_wake_up_does_not_run_the_check_twice():
    state = {"now": datetime(2026, 9, 30, 5, 0, tzinfo=TZ)}
    checker = _Checker()
    ran_at: list[datetime] = []

    async def record() -> None:
        ran_at.append(state["now"])
        checker.runs += 1

    checker.check_all = record  # type: ignore[method-assign]

    async def early_sleep(seconds: float) -> None:
        if checker.runs >= 3:
            raise _Stop
        state["now"] += timedelta(seconds=max(seconds - 0.1, 0.05))
        await asyncio.sleep(0)

    with pytest.raises(_Stop):
        await run_daily(checker, _Settings(True), now=lambda: state["now"], sleep=early_sleep)
    assert [(t.month, t.day) for t in ran_at] == [(9, 30), (10, 1), (10, 2)]
    assert all(t.time() >= time(6, 0) for t in ran_at)


async def test_a_failing_settings_read_does_not_end_the_schedule():
    _, now, sleep = _driver(days=2)

    class Broken(_Settings):
        def get_daily_check_enabled(self) -> bool:
            raise RuntimeError("store down")

    with pytest.raises(_Stop):
        await run_daily(_Checker(), Broken(True), now=now, sleep=sleep)
