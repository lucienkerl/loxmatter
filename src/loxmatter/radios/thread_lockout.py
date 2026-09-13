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

"""Which stick Thread is using, and what that leaves Zigbee allowed to do.

The bridge cannot see Thread's radio itself: `RADIO_DEVICE` lives in the
stack's `.env`, and only the updater sidecar reads it, reporting it in
`radios-state.json` as `current` (`radios/sidecar.py`). Everything here is
therefore a judgement about that report, and the one rule this module
exists to keep is that **a missing report must never read as "Thread uses
nothing"**. It used to: with no report, no stick was excluded, and the
maintainer's MG24 - his live Thread radio - was offered for Zigbee.

Two questions, answered differently on purpose:

**Choosing a stick** (`GET`/`PUT /api/zigbee/radio`) needs a report that is
current: written within the heartbeat window, with no radios change running
or waiting. Anything less and no stick may be newly chosen - only "No
Zigbee stick" - and the reason says what to do. This is the moment a
person is picking hardware, so there is no cost in asking them to wait for
the sidecar, and a stale report may name a Thread stick that has since
moved.

**Opening the stick already stored** (every `ZigbeeSource.connect()`: the
first after boot, each supervisor retry, each apply) needs a report, of
any age, that does not name that stick as Thread's. Two things decide it:

- On a reboot the bridge commonly starts before the sidecar's first pass,
  so the report on disk is from before the reboot and stale. Requiring a
  fresh one would leave a working Zigbee installation dark for as long as
  the sidecar takes to come up - or for good, if the sidecar is stopped -
  and that is exactly the stranding a transient hiccup must not cause.
- That on-disk report IS the last good report, persisted: it lives on the
  same `loxmatter-store` volume as the store, the sidecar replaces it
  atomically on every pass, and nothing but a deliberate delete removes it.
  A second copy in the store was considered and not built - it could only
  add a case where the two copies disagree. And a stale report cannot miss
  a Thread stick that moved through the product: `POST /api/radios` refuses
  to put Thread on the stick Zigbee is set up with. What it cannot see is a
  hand edit of `.env` while the sidecar is stopped, which no report could.

With no readable report at all - the sidecar never ran, the file is gone or
damaged - the stored stick is NOT opened: nothing then says it is not
Thread's. The supervisor's 1 s -> 60 s retry means it opens by itself on
the first attempt after the sidecar reports, with the reason on the card
until then.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from loxmatter import i18n
from loxmatter.radios.inventory import (
    SerialRadio,
    is_same_device,
    match_current_device,
    scan_serial,
)
from loxmatter.radios.sidecar import change_in_progress, read_radios_state, report_is_fresh

ThreadStatus = Literal["known", "unknown", "changing"]

# The sentence behind each status that forbids choosing a stick. `known`
# has none.
_SELECTION_REFUSALS: dict[ThreadStatus, str] = {
    "unknown": "api.errors.zigbee_thread_unknown",
    "changing": "api.errors.zigbee_thread_changing",
}


@dataclass(frozen=True)
class ThreadStick:
    """What the sidecar's report says about Thread's radio.

    `status` is about choosing (see the module docstring); `reported` is
    about opening. `device` is `RADIO_DEVICE`, mapped onto a by-id path
    where a detected stick matches, and is read from a stale report too -
    it is still the best statement there is about which stick to refuse.
    """

    status: ThreadStatus
    reported: bool
    device: str | None
    in_use: bool

    def selection_refusal(self) -> i18n.Message | None:
        """Why no stick may be chosen right now, or `None` if one may."""
        key = _SELECTION_REFUSALS.get(self.status)
        return None if key is None else i18n.Message.of(key)


def read_thread_stick(
    update_dir: Path, serial: Sequence[SerialRadio], *, now: datetime
) -> ThreadStick:
    """The report, judged.

    `in_use` is gated on `thread_enabled` OR `otbr_running`, deliberately.
    MEASURED in `deploy/updater/radios-once.sh`: the `down` path rewrites
    `COMPOSE_PROFILES` and LEAVES `RADIO_DEVICE` naming the stick, so "is
    this the stored Thread device" is not "is Thread using it" - and
    answering only the first would permanently strand the user who turns
    Thread off to repurpose a dual-capable stick, the only legitimate way
    to move an MG24 across.
    """
    state = read_radios_state(update_dir)
    if state is None or state.current is None:
        return ThreadStick(status="unknown", reported=False, device=None, in_use=False)
    device, _present = match_current_device(state.current.thread_device, serial)
    in_use = state.current.thread_enabled or state.current.otbr_running
    status: ThreadStatus
    if not report_is_fresh(state, now=now):
        status = "unknown"
    elif change_in_progress(update_dir, state):
        status = "changing"
    else:
        status = "known"
    return ThreadStick(status=status, reported=True, device=device, in_use=in_use)


def is_thread_stick(path: str, thread: ThreadStick, host_dev: Path) -> bool:
    """Whether `path` is the stick the report says Thread is running on.

    By RESOLVED major:minor, never by string compare. The same physical
    stick is `/dev/ttyUSB0` in `.env`, a by-id path on the card, and a third
    name under the container's `/host/dev` mount - three strings, one piece
    of hardware. MEASURED on the Pi: the two attached sticks are major 188
    minors 0 and 1 and share the vendor id `10c4:ea60`, so the resolved
    minor is the only thing that separates them.
    """
    if thread.device is None or not thread.in_use:
        return False
    return is_same_device(path, thread.device, host_dev)


def open_refusal(
    path: str, *, update_dir: Path, host_dev: Path, sys_root: Path
) -> i18n.Message | None:
    """Why the stored stick at `path` must not be opened now, or `None`.

    Called by `ZigbeeSource.connect()` before anything touches the port.
    See the module docstring for why this accepts a stale report and
    refuses a missing one.
    """
    thread = read_thread_stick(update_dir, scan_serial(host_dev, sys_root), now=datetime.now(UTC))
    if not thread.reported:
        return i18n.Message.of("api.errors.zigbee_open_thread_unknown")
    if is_thread_stick(path, thread, host_dev):
        return i18n.Message.of("api.errors.zigbee_open_is_thread_stick")
    return None
