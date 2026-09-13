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


async def test_a_cancelled_warm_up_is_waited_out_and_never_started_twice(monkeypatch):
    """A radio change cancels the supervisor wherever it is, and on a first
    configuration that can be inside this warm-up. The executor thread
    cannot be cancelled with it: a caller that let go of the lock at once
    let the next attempt start a second `zhaquirks.setup()` beside the
    first, both filling one process-wide registry.

    Fault to prove it: `await` the executor future directly instead of
    through `asyncio.shield` plus the wait in the `except`. The second
    caller then runs `setup` again while the first is still inside it."""
    # The lock binds to the first loop that contends for it, and this test
    # contends; a fresh one keeps an earlier test's loop out of it.
    monkeypatch.setattr(quirks_module, "_lock", asyncio.Lock())
    calls: list[int] = []
    inside = threading.Event()
    release = threading.Event()

    def setup() -> None:
        calls.append(1)
        inside.set()
        release.wait(5)

    first = asyncio.ensure_future(ensure_quirks_loaded(setup=setup))
    while not inside.is_set():
        await asyncio.sleep(0.01)
    first.cancel()
    second = asyncio.ensure_future(ensure_quirks_loaded(setup=setup))
    await asyncio.sleep(0.05)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second

    assert calls == [1]
    assert quirks_loaded() is True


async def test_a_twice_cancelled_warm_up_still_waits_for_setup_to_finish(monkeypatch):
    """`Task.cancel()` can keep delivering a fresh `CancelledError` at every
    await point until the coroutine actually returns - so a SECOND
    cancellation, arriving while `ensure_quirks_loaded` is already inside
    its own wait for the executor future, must not abandon that wait
    either. It would release the lock with `setup` still running in its
    thread, and let the next attempt start a second `zhaquirks.setup()`
    beside the first, both filling one process-wide registry.

    Fault to prove it: replace the `while not running.done(): ...` loop
    in `ensure_quirks_loaded` with a single `with
    contextlib.suppress(Exception): await running` - one catch only. The
    second `cancel()` below then raises straight out of that bare `await`,
    the lock is released before `setup` is done, and `second` runs `setup`
    again while `first`'s is still blocked on `release` - `calls` comes out
    `[1, 1]` instead of `[1]`."""
    monkeypatch.setattr(quirks_module, "_lock", asyncio.Lock())
    calls: list[int] = []
    inside = threading.Event()
    release = threading.Event()

    def setup() -> None:
        calls.append(1)
        inside.set()
        release.wait(5)

    first = asyncio.ensure_future(ensure_quirks_loaded(setup=setup))
    while not inside.is_set():
        await asyncio.sleep(0.01)
    first.cancel()
    # `setup` is still blocked on `release`, so this second cancellation
    # lands on the wait loop's OWN `await asyncio.shield(running)` - the
    # case a single `except` cannot survive.
    await asyncio.sleep(0.05)
    first.cancel()
    await asyncio.sleep(0.05)
    second = asyncio.ensure_future(ensure_quirks_loaded(setup=setup))
    await asyncio.sleep(0.05)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second

    assert calls == [1]
    assert quirks_loaded() is True


async def test_a_cancelled_warm_up_that_finishes_still_logs_its_duration(monkeypatch, caplog):
    """A radio change during the very first warm-up cancels the caller
    while `zhaquirks.setup()` runs on in its thread. The registry is loaded
    all the same and the duration was measured and stored - but only the
    uncancelled path logged it, and the next caller returns the stored
    figure without a word. The hardware checklist reads that line for the
    Pi's real warm-up time, and an apply at the wrong moment hid it.

    Exactly one line: the caller that returns the stored figure adds none.

    Fault to prove it: store `_duration_seconds` in the cancelled branch
    without logging (no line), or log in the early return as well (two)."""
    monkeypatch.setattr(quirks_module, "_lock", asyncio.Lock())
    inside = threading.Event()
    release = threading.Event()

    def setup() -> None:
        inside.set()
        release.wait(5)

    caplog.set_level("INFO", logger="loxmatter.zigbee.quirks")
    first = asyncio.ensure_future(ensure_quirks_loaded(setup=setup))
    while not inside.is_set():
        await asyncio.sleep(0.01)
    first.cancel()
    await asyncio.sleep(0.05)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await ensure_quirks_loaded(setup=setup)

    lines = [r for r in caplog.records if "quirks registry loaded in" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].levelname == "INFO"
