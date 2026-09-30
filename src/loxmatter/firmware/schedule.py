# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The daily firmware check at 06:00 local time (design 2026-09-30, 6.2).

It only ever checks; installing always takes a click. A run missed while
the bridge was down is not caught up."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Final, Protocol

logger = logging.getLogger(__name__)

DAILY_CHECK_AT: Final = time(6, 0)


class _Checker(Protocol):
    async def check_all(self) -> None: ...


class _Settings(Protocol):
    def get_daily_check_enabled(self) -> bool: ...


def _instant(moment: datetime) -> datetime:
    """The absolute instant; a naive value is read as the machine's local time."""
    return moment.astimezone(UTC)


def _next_target(now: datetime, at: time) -> datetime:
    """The next `at` on the local calendar, in `now`'s own time zone, so a DST
    change moves the offset but not the wall-clock hour. Exactly `at` counts as
    tomorrow, so a wake-up at 06:00:00 never schedules a second run for it."""
    target = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
    if _instant(target) <= _instant(now):
        target = datetime.combine(now.date() + timedelta(days=1), at, tzinfo=now.tzinfo)
    return target


def seconds_until(now: datetime, at: time) -> float:
    """Seconds from `now` to the next `at`."""
    return (_instant(_next_target(now, at)) - _instant(now)).total_seconds()


def _local_now() -> datetime:
    # Naive on purpose: an `astimezone()` value carries a fixed offset and would
    # keep it across a DST change. Naive values are read as system local time.
    return datetime.now()  # noqa: DTZ005 - naive local time, see above


async def run_daily(
    checker: _Checker,
    settings: _Settings,
    *,
    now: Callable[[], datetime] = _local_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    at: time = DAILY_CHECK_AT,
) -> None:
    while True:
        target = _next_target(now(), at)
        # A wake-up can come early (timer resolution, a wall-clock step), so
        # sleep until the target has really passed before running.
        while (remaining := (_instant(target) - _instant(now())).total_seconds()) > 0:
            await sleep(remaining)
        try:
            if settings.get_daily_check_enabled():
                await checker.check_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the daily firmware check failed")
