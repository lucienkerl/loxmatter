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

"""The one-shot `zhaquirks.setup()` warm-up.

Measured inside `python:3.12-slim` arm64 on an M1 Pro (research F.4):
2.1-2.7 s warm, 3.1 s cold, RSS +73-75 MB, +857 modules. Extrapolated to a
Pi 4 that is 9-15 s, and to a Pi 5 4-6 s - on a cold SD card, worse.

Three rules follow from that number, and this module exists to hold all
three in one place:

1. **Never at import time.** `import loxmatter.zigbee.quirks` must stay
   cheap; the cost is paid by calling `ensure_quirks_loaded()`.
2. **Never on the event loop.** `cli._run` starts uvicorn only after
   `attach`, so a blocking warm-up there is time `/health` and the web UI
   spend not answering. It goes to the default executor.
3. **Never twice.** The flag is process-wide, which is exactly the scope of
   the registry `zhaquirks.setup()` fills.

Only called when a Zigbee radio is configured - an installation without one
never pays any of this."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_duration_seconds: float | None = None


def _default_setup() -> None:
    """The real warm-up. Imported inside the function, never at module
    level: the import itself is most of the cost this module exists to
    defer, and a test with a fake `setup` must not pay it."""
    import zhaquirks

    zhaquirks.setup()


def quirks_loaded() -> bool:
    return _duration_seconds is not None


def _reset_for_tests() -> None:
    """Only `tests/zigbee/test_quirks.py` calls this. The guard is
    process-wide by design, so a test that wants a fresh one has to say so
    explicitly rather than depend on module import order."""
    global _duration_seconds
    _duration_seconds = None


async def ensure_quirks_loaded(*, setup: Callable[[], None] = _default_setup) -> float:
    """Runs the quirks registry warm-up once per process; returns its
    measured duration in seconds.

    Every caller gets the same measurement, including the ones that arrived
    while the first was still running - the alternative, returning 0.0 to
    the losers of the race, would put a number nobody measured into the
    startup log.

    The `asyncio.Lock` rather than a bare flag check: two `attach` calls
    racing on startup would both see `None` and both start a 15-second
    import storm on a Pi.
    """
    if _duration_seconds is not None:
        return _duration_seconds
    async with _lock:
        if _duration_seconds is not None:
            return _duration_seconds
        started = time.monotonic()
        running = asyncio.get_running_loop().run_in_executor(None, setup)
        try:
            # Shielded, and waited out when the caller is cancelled - even
            # across a SECOND cancellation while that wait is itself under
            # way (`Task.cancel()` can keep delivering one at every await
            # point until this coroutine actually returns). A radio change
            # cancels the supervisor wherever it is, this `await` included,
            # and the executor thread cannot be cancelled with it: releasing
            # the lock here while `setup` still ran would let the next
            # attempt start a SECOND `zhaquirks.setup()` beside the first -
            # both filling one process-wide registry at once. A bare
            # `await running` after only ONE `except` does not hold that
            # guarantee - a second cancellation right there abandons the
            # wait exactly as the first one would have - so the loop below
            # keeps re-shielding `running` until it actually reports done,
            # before this ever falls through to the check that follows.
            await asyncio.shield(running)
        except asyncio.CancelledError:
            while not running.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(running)
            with contextlib.suppress(Exception):
                await running
            if running.done() and not running.cancelled() and running.exception() is None:
                # Logged here too: the registry IS loaded, and this is the
                # only measurement of it this process will make. An apply
                # during the very first warm-up cancels exactly this await,
                # and the next caller returns the stored figure silently -
                # the line the hardware checklist reads would never appear.
                _record(started)
            raise
        return _record(started)


def _record(started: float) -> float:
    """Stores and logs the warm-up's duration, once per process."""
    global _duration_seconds
    _duration_seconds = time.monotonic() - started
    logger.info("zigbee quirks registry loaded in %.1f s", _duration_seconds)
    return _duration_seconds
