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

"""The one-shot quirks warm-up (design 2026-09-12 section 8.4, research F.4).

`zhaquirks.setup()` costs 2.1-2.7 s on an M1 Pro and an estimated 9-15 s on
a Pi 4, and it imports 857 modules. It therefore may not run at import time,
may not run twice, and may not run on the event loop - `cli._run` starts
uvicorn after `attach`, so anything that blocks the loop here is time the
web UI and `/health` spend not answering.

The fake `setup` below sleeps with `time.sleep`, not `asyncio.sleep`, on
purpose: only a genuinely blocking call can demonstrate that the executor
is doing its job."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from loxmatter.zigbee import quirks as quirks_module
from loxmatter.zigbee.quirks import ensure_quirks_loaded, quirks_loaded


@pytest.fixture(autouse=True)
def _fresh_process_state():
    """The guard is deliberately process-wide (that is the whole point), so
    each test starts from a clean one rather than depending on file order."""
    quirks_module._reset_for_tests()
    yield
    quirks_module._reset_for_tests()


async def test_the_warm_up_runs_exactly_once_however_often_it_is_awaited():
    """Fault to prove it: drop the `_done` guard in `ensure_quirks_loaded`
    and let every caller run `setup`. Two concurrent callers then pay the
    9-15 s cost twice on a Pi."""
    calls: list[int] = []

    def setup() -> None:
        calls.append(1)

    first, second, third = await asyncio.gather(
        ensure_quirks_loaded(setup=setup),
        ensure_quirks_loaded(setup=setup),
        ensure_quirks_loaded(setup=setup),
    )

    assert calls == [1]
    # Every caller learns the SAME measured duration, not 0.0 for the
    # losers of the race - the log line in `cli._run` reports whatever it
    # is handed, and "0.0 s" would be a measurement nobody took.
    assert first == second == third
    assert quirks_loaded() is True


async def test_the_warm_up_does_not_block_the_event_loop():
    """Fault to prove it: call `setup()` directly instead of handing it to
    `run_in_executor`. `ticks` then stays at 0 or 1, because nothing else
    on the loop gets to run for the whole of the warm-up."""
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    def setup() -> None:
        time.sleep(0.3)

    beat = asyncio.ensure_future(ticker())
    try:
        await ensure_quirks_loaded(setup=setup)
    finally:
        beat.cancel()

    # 0.3 s of blocking against a 0.01 s tick: a loop that kept running
    # managed many ticks, a blocked one managed almost none.
    assert ticks >= 5, f"the event loop was blocked during the warm-up ({ticks} ticks)"


async def test_the_warm_up_runs_off_the_event_loop_thread():
    """The direct statement of the same protection, independent of timing:
    `setup` must observe a different thread than the loop's own."""
    loop_thread = threading.get_ident()
    seen: list[int] = []

    await ensure_quirks_loaded(setup=lambda: seen.append(threading.get_ident()))

    assert seen and seen[0] != loop_thread
