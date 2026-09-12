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

"""Whether a Zigbee device is still there.

zigpy itself has no concept of a device being "available" - `Device.last_seen`
just stops advancing, and nothing ever looks at it again on its own. ZHA
builds availability on top of exactly that field (`zha/zigbee/device.py`,
`Device._check_available` - read out of the installed `zha` package, not
copied from a document: `MAINS_THRESHOLD_SECONDS`, `BATTERY_THRESHOLD_SECONDS`
and the two-ping grace period below are its own numbers, `2 * 60 * 60`,
`6 * 60 * 60` and `_CHECKIN_GRACE_PERIODS = 2`, verified against
`zha/application/const.py` and `zha/zigbee/device.py`).

The half that matters most for this bridge is the half ZHA does not need:
**a lost link must push every device offline at once.** ZHA runs inside Home
Assistant, whose own entity registry already marks everything unavailable
the moment its config entry goes down; loxmatter has no such outer layer; a
Zigbee node's `available` flag is the only place that fact can live. Without
`mark_all_offline()`, a pulled coordinator leaves every device showing
whatever it last reported, forever - a motion sensor reading "no motion"
because the RADIO went quiet, not the room, is worse than one that reports
nothing at all.

Two decisions this module makes on purpose, both because a battery device
and a mains device fail differently:

- **The threshold is picked from `is_mains_powered`, never a single value
  for both.** One threshold would either declare a normal battery sensor
  dead four times a day, or take six hours to notice a dead lamp.
- **Only a mains-powered device is ever pinged**, and only after its
  threshold has already passed. A sleepy end device cannot be woken by an
  unsolicited read - there is nothing to gain by trying, and a real cost
  (a wasted radio wake, spent battery) if the attempt is made anyway. A
  mains device, by contrast, is expected to answer promptly; a read
  (`Basic.manufacturer` with `allow_cache=False`, ZHA's own literal choice)
  gives a device that simply had nothing new to report two more chances
  before it is written off, and LUMI/Aqara devices are excluded from even
  that: they are known not to answer an unsolicited read at all, mains-
  powered or not, so pinging one only wastes a wake-up and delays the
  correct answer by a sweep or two for nothing.

This module does not import zigpy: every zigpy object it reads
(`Device.last_seen`, `Device.node_desc`, `Device.manufacturer`,
`Device.non_zdo_endpoints`, `Cluster.read_attributes`) arrives already built,
through `ZigbeeSource`'s own device catalogue, exactly the way
`ZigbeeSource._facts` already reads them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final

from loxmatter.sources import RuntimeEventHandler

if TYPE_CHECKING:
    from loxmatter.zigbee.source import ZigbeeSource

logger = logging.getLogger(__name__)

__all__ = [
    "BATTERY_THRESHOLD_SECONDS",
    "CHECK_INTERVAL_SECONDS",
    "MAINS_THRESHOLD_SECONDS",
    "AvailabilityChecker",
    "is_available",
]

# ZHA's own numbers (`zha/application/const.py`,
# `CONF_DEFAULT_CONSIDER_UNAVAILABLE_MAINS` / `_BATTERY`), verified against
# the installed `zha` package rather than assumed.
MAINS_THRESHOLD_SECONDS: Final = 2 * 60 * 60
BATTERY_THRESHOLD_SECONDS: Final = 6 * 60 * 60

# ZHA's own interval (`zha/application/helpers.py`, `_REFRESH_INTERVAL =
# (30, 45)`, randomised between the two on every run). A single constant
# rather than a range: nothing here depends on jitter, and a fixed number is
# what a test can measure directly rather than sit out.
CHECK_INTERVAL_SECONDS: Final = 30.0

# ZHA's own grace period (`zha/zigbee/device.py`, `_CHECKIN_GRACE_PERIODS`):
# a mains device gets this many direct reads, one per sweep, before it is
# written off - see `test_a_mains_device_is_pinged_twice_before_it_is_declared_offline`.
_CHECKIN_GRACE_PERIODS: Final = 2

# The manufacturer name Xiaomi/Aqara devices report (verified against
# `zha/zigbee/device.py`'s own `self.manufacturer == "LUMI"` check). These
# devices do not answer an unsolicited attribute read at all, so pinging one
# only wastes a wake-up - see `test_lumi_devices_are_never_pinged`.
_LUMI_MANUFACTURER: Final = "LUMI"

# Basic cluster, `manufacturer` attribute - the exact pair ZHA itself reads
# (`zha/application/const.py`'s `ATTR_MANUFACTURER = "manufacturer"`,
# `zha/zigbee/device.py`'s `basic_cluster`). Spelled out as the numeric id
# `_endpoint_facts` already prefers over a bare name lookup, for the same
# reason: it is unambiguous regardless of what a quirk declares alongside it.
_BASIC_CLUSTER_ID: Final = 0x0000
_MANUFACTURER_ATTRIBUTE: Final = 0x0004


def _is_mains_powered(device: Any) -> bool:
    """The conservative half of the pair `ZigbeeSource._facts` already
    decided for `DeviceFacts.is_mains_powered`: a device zigpy has not yet
    interviewed carries no node descriptor at all, and is treated as battery
    powered - the six-hour threshold, not the two-hour one."""
    node_desc = device.node_desc
    return bool(node_desc is not None and node_desc.is_mains_powered)


def _basic_cluster(device: Any) -> Any | None:
    for endpoint in device.non_zdo_endpoints:
        cluster = endpoint.in_clusters.get(_BASIC_CLUSTER_ID)
        if cluster is not None:
            return cluster
    return None


def is_available(device: Any, *, now: float | None = None) -> bool:
    """Whether `device` has been heard from recently enough to count as
    reachable.

    `device.last_seen` is zigpy's own persisted timestamp
    (`zigpy.device.Device.last_seen`, an epoch float or `None` - verified
    against the installed zigpy 2.2.0), so this answers the same way right
    after a restart as it would a minute later. `ZigbeeSource._facts` calls
    this - and nothing else - to fill `NodeSnapshot.available`, which is
    what lets a reconnected bridge report the state zigpy's own database
    already knows instead of guessing "online" for everything (see
    `test_the_online_state_is_seeded_from_the_database_after_a_restart`).

    `now` is a moment, not a clock: `AvailabilityChecker` reads its own
    injected clock once per sweep and hands the float to every device in
    that sweep, so all of them are judged against the exact same instant.
    Left as `None`, this reads the wall clock itself - the shape
    `ZigbeeSource._facts` uses, which has no sweep to share a moment across.
    """
    last_seen = device.last_seen
    if last_seen is None:
        return False
    threshold = MAINS_THRESHOLD_SECONDS if _is_mains_powered(device) else BATTERY_THRESHOLD_SECONDS
    moment = time.time() if now is None else now
    return bool((moment - last_seen) < threshold)


class AvailabilityChecker:
    """Keeps `available` honest for every device of one `ZigbeeSource`,
    between the two events that actually change it: a periodic sweep that
    asks the quiet ones, and a lost link that takes all of them down at
    once.

    Constructed with the same `handler` and `resolve_device_id` a source
    hands to `subscribe()` - deliberately its own copy rather than a shared
    reference, the same way `ZigbeeSource` itself takes both as plain
    arguments rather than reaching for a global."""

    def __init__(
        self,
        source: ZigbeeSource,
        handler: RuntimeEventHandler,
        resolve_device_id: Callable[[str], int | None],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._source = source
        self._handler = handler
        self._resolve_device_id = resolve_device_id
        self._sleep = sleep
        self._now = now
        # Per address, how many sweeps in a row a mains device has been
        # asked directly and said nothing. Cleared the moment the device is
        # heard from again - by a report, by a successful ping, or by the
        # whole link going down, at which point the count means nothing.
        self._missed_checkins: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ lifecycle --

    def start(self) -> None:
        """Starts the periodic sweep, once.

        A second call while one is already running is not an error, the
        same tolerance `ZigbeeSource.subscribe` already has for its own
        dispatch task: nothing here stops a caller from asking twice."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def mark_all_offline(self) -> None:
        """THE feature this module exists for: pushes every known device
        offline at once, in response to the coordinator going away.

        zigpy has no notion of a lost link touching a device's
        availability - `last_seen` simply stops advancing, and without this
        sweep every device keeps whatever `available` it last had, forever,
        because nothing else ever asks again. A motion sensor stuck showing
        "no motion" because the RADIO went quiet, not the room, is worse
        than one that reports nothing at all.

        The ping grace counters are cleared as well: a count of missed
        check-ins means nothing once the reason nobody answered is the link
        itself, not the device."""
        self._missed_checkins.clear()
        for device in self._source._devices():
            device_id = self._resolve_device_id(str(device.ieee))
            if device_id is None:
                continue
            await self._handler.set_online(device_id, False)

    # --------------------------------------------------------------- sweep --

    async def _run(self) -> None:
        while True:
            await self._sleep(CHECK_INTERVAL_SECONDS)
            await self._sweep()

    async def _sweep(self) -> None:
        """One pass over the whole catalogue, all of it judged against the
        SAME moment - so a sweep that takes a while (pinging several quiet
        mains devices in a row) does not let the earliest device in the
        list enjoy a few extra seconds of grace the last one does not
        get."""
        moment = self._now()
        for device in self._source._devices():
            await self._check_one(device, moment)

    async def _check_one(self, device: Any, moment: float) -> None:
        address = str(device.ieee)
        device_id = self._resolve_device_id(address)
        if device_id is None:
            # Not (yet) a device the store knows - nothing to report to,
            # and nothing worth pinging on its behalf either.
            return
        if is_available(device, now=moment):
            self._missed_checkins.pop(address, None)
            await self._handler.set_online(device_id, True)
            return
        if not _is_mains_powered(device):
            # A sleepy or battery end device cannot be woken by an
            # unsolicited read - there is nothing to ping, and nothing to
            # wait for either. It is simply offline once its (longer)
            # threshold has passed.
            await self._handler.set_online(device_id, False)
            return
        if device.manufacturer == _LUMI_MANUFACTURER:
            # LUMI/Aqara devices never answer an unsolicited attribute
            # read, mains-powered or not - see `test_lumi_devices_are_never_pinged`.
            # Pinging one anyway would not save it: it would only spend the
            # grace period below pointlessly before reaching the same
            # answer, and cost the device a wake-up for nothing.
            await self._handler.set_online(device_id, False)
            return
        missed = self._missed_checkins.get(address, 0)
        if missed >= _CHECKIN_GRACE_PERIODS:
            await self._handler.set_online(device_id, False)
            return
        # Not yet declared offline: a mains device that simply had nothing
        # new to report is not a dead one, and it gets
        # `_CHECKIN_GRACE_PERIODS` direct reads, one per sweep, before it
        # is written off - see
        # `test_a_mains_device_is_pinged_twice_before_it_is_declared_offline`.
        self._missed_checkins[address] = missed + 1
        await self._ping(device, address)

    async def _ping(self, device: Any, address: str) -> None:
        """Reads `Basic.manufacturer` with `allow_cache=False` - ZHA's own
        literal choice of attribute and cache policy - so a mains device
        that has gone quiet is asked directly rather than declared dead on
        the strength of silence alone.

        A successful answer resets the grace counter at once, rather than
        waiting for `last_seen` to catch up on the next sweep: on the real
        radio a response updates `last_seen` on its own
        (`zigpy.device.Device.last_seen` is set from every received
        packet), but nothing here depends on that timing lining up with the
        next sweep.

        A failure - no answer, a raised exception, no Basic cluster at all
        - changes nothing here: the missed check-in was already counted by
        the caller before this was reached, and a device that stays silent
        simply runs out of grace on a later sweep."""
        cluster = _basic_cluster(device)
        if cluster is None:
            return
        try:
            success, _failure = await cluster.read_attributes(
                [_MANUFACTURER_ATTRIBUTE], allow_cache=False
            )
        except Exception as exc:  # noqa: BLE001 - a quiet device must not stop the sweep
            logger.debug("ping of %s got no answer: %s", address, exc)
            return
        if success.get(_MANUFACTURER_ATTRIBUTE) is not None:
            self._missed_checkins.pop(address, None)
