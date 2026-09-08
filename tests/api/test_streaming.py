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

"""The WebSocket mechanics that both live routes share."""

from __future__ import annotations

import asyncio

from loxmatter.api.streaming import QUEUE_MAXSIZE, BoundedQueue


def test_the_queue_drops_the_oldest_entry_when_it_is_full():
    """Drop-oldest, not drop-newest: a live view wants the current state,
    the stale entry is expendable."""
    queue = BoundedQueue(maxsize=2, connection_label="test")
    queue.put({"n": 1})
    queue.put({"n": 2})
    queue.put({"n": 3})

    assert asyncio.run(_drain(queue, 2)) == [{"n": 2}, {"n": 3}]


async def _drain(queue: BoundedQueue, count: int) -> list[dict[str, object]]:
    return [await queue.get() for _ in range(count)]


def test_putting_never_blocks_and_never_raises():
    """`put` runs in the observer's call path - even in a foreign thread
    for the log handler. If it blocked or raised, it would drag the
    observed path with it."""
    queue = BoundedQueue(maxsize=1, connection_label="test")
    for n in range(1000):
        queue.put({"n": n})


def test_the_default_size_matches_what_the_value_stream_used():
    """Taken from api/live.py, not newly chosen: the value is justified
    there, and two different sizes would be a question nobody could answer."""
    assert QUEUE_MAXSIZE == 512
