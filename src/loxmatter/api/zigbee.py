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

"""The Zigbee radio setting, and the pairing surface the web UI drives.

Two halves, and they are in one module because they are one stick: `GET`
and `PUT /api/zigbee/radio` choose the coordinator, and
`/api/zigbee/permit` and `/api/zigbee/pairing*` open it for new devices and
say what is happening to each one that arrives.

**Pairing is deliberately outside `DeviceSource`** (boundary design 3.2).
Matter takes a code and returns one device; Zigbee opens the network and
devices arrive afterwards, possibly several, possibly none. There is no
honest shared method for those two, so these routes call `ZigbeeSource` by
name - and read it from `ZigbeeRuntime` on every request rather than
capturing it, because a radio change replaces the object and a captured one
would go on answering for the stick the user stopped using.

**A bridge-owned setting, not a sidecar request** (spec correction 3).
zigpy runs in this process, so the change is applied by reconnecting the
source rather than by recreating the container the request is being served
from: nothing here writes `radios-request.json`, no container is recreated,
and Thread and Bluetooth are untouched by construction - the change never
reaches the file they travel in.

**The one thing this module exists to prevent.** On the maintainer's Pi
(measured 12 September 2026) there are exactly two USB serial sticks. Both
report `10c4:ea60`, both are major 188, and one of them is the radio his
live Thread border router is running on. The fingerprint table reports that
stick as a perfectly good EZSP Zigbee coordinator, because it IS one -
nothing in that layer can or should refuse it. This module is the only
thing standing between the picker and a user selecting the radio their
entire Thread network depends on.

**The row states the pairing tab shows are not all stored anywhere.**
`PairingRow.state` carries the four the source itself can see; the three
beside them come from here, per request, and `_row_status` and `_stuck`
below say where each one is read from and why none of them may be
remembered on the row.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from loxmatter import i18n
from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import ZigbeeRadioSettings, settings_for_path
from loxmatter.radios.fingerprints import (
    FlowControl,
    RadioType,
    match_fingerprint,
)
from loxmatter.radios.inventory import (
    SerialRadio,
    is_same_device,
    match_current_device,
    scan_serial,
)
from loxmatter.radios.sidecar import read_radios_state
from loxmatter.sources import DeviceUnreachableError, bounded_source_call
from loxmatter.zigbee.runtime import ZigbeeRuntime
from loxmatter.zigbee.source import PERMIT_MAX_SECONDS, PairingRow, ZigbeeSource

logger = logging.getLogger(__name__)

# "waiting_wake", not the design table's own prose label ("waiting to
# wake") - the pairing tab tests for the wire form under this exact
# spelling, so this is the one spot where matching the design document's
# English prose instead of the sibling task's already-written fixture would
# be the wrong call.
PairingRowStatus = Literal[
    "joined", "interviewing", "ready", "failed", "configuring", "waiting_wake", "stuck"
]

# Design 3.1: "no progress for 60 s (mains) / 90 s (battery)". Two numbers
# because a battery device's interview is slow by nature - it answers when
# it happens to be awake - and calling it stuck at the mains threshold would
# accuse a device that is merely asleep 30 s before it deserves it.
STUCK_AFTER_SECONDS_MAINS: Final = 60
STUCK_AFTER_SECONDS_BATTERY: Final = 90


class ZigbeeRadioIn(BaseModel):
    """`path` of `None` is "no Zigbee stick" and is always accepted.

    The three Advanced fields are optional and are read ONLY for a stick
    the fingerprint table does not recognise (see `_settings_from`):
    refusing to work with an unlisted stick would be worse than letting the
    user say what it is, but a value typed into a disclosure must never
    override a detection.

    `radio_type` and `flow_control` are the LITERALS `fingerprints` uses,
    not free strings, so a value outside them is a 422 from the schema
    rather than a stored setting that only fails minutes later, inside
    `connect()`, as "the Zigbee radio could not be started". The store
    itself still types them as `str` - a SQLite text column guarantees
    nothing - and `build_zigbee_source` says so where it reads them back.
    """

    path: str | None
    radio_type: RadioType | None = None
    # A baud rate of 0 is not "use the default", it is a typo. Refusing it
    # in the schema is what keeps `_settings_from` from silently turning it
    # into one.
    baudrate: int | None = Field(default=None, gt=0)
    flow_control: FlowControl | None = None


class ZigbeePermitIn(BaseModel):
    """How long to open the network for, in seconds.

    **Bounded IN THE SCHEMA, at the protocol maximum zigpy itself asserts.**
    254 s is what `ControllerApplication.permit` allows (`assert 0 <= time_s
    <= 254`), and a request for more is a 422 here rather than an
    `AssertionError` from inside the library - which under `python -O` would
    be no check at all and an out-of-range broadcast instead.

    **There is no unlimited mode, and `0xFF` is not one.** Zigbee2MQTT
    removed its permanent `permit_join` in 2.0 as a security concern
    (design 3.1): a network left open forever is a network any passing
    device can join. `255` is refused like any other out-of-range value, so
    the traditional "forever" spelling cannot slip through as one.

    A duration of 0 is Stop: it closes the window at once, which is what
    both the Stop button and leaving the tab send."""

    duration: int = Field(ge=0, le=PERMIT_MAX_SECONDS)


class ZigbeePairingPatch(BaseModel):
    """The name and room of a device being adopted off the pairing tab.

    Both optional and both `None` for "unchanged", the same convention
    `PATCH /api/devices/{id}` already uses; an empty `room` means "no
    room", which is the room `<select>`'s own encoding."""

    name: str | None = None
    room: str | None = None


def _suggested_name(row: PairingRow) -> str:
    """What the name field is prefilled with: `<Manufacturer> <Model>`.

    Z2M prefills the IEEE, which nobody keeps - it is the one string about
    a device that tells its owner nothing. The IEEE is the fallback only
    when zigpy has neither name yet, which is true of a row that has not
    finished interviewing; by the time the row is ready - the only row the
    tab offers a name field on - both are there."""
    suggested = " ".join(part for part in (row.manufacturer, row.model) if part)
    return suggested or row.ieee


def _row_status(
    row: PairingRow, *, configuring: frozenset[str], pending: Sequence[str]
) -> PairingRowStatus:
    """The status the pairing tab actually shows - `PairingRow.state`
    itself, widened by the two facts configure-on-join built and nothing
    consumed until this route: `ZigbeeSource.configuring_addresses()` for a
    live configuration pass, `store.zigbee_pending.addresses_with_pending()`
    for a cluster still owed after one. Order matters - checked in the order
    a device actually passes through them - and both only ever apply to a
    `"ready"` row: `joined`, `interviewing` and `failed` are not
    `"configuring"` or `"waiting_wake"` no matter what either set contains,
    because a device cannot be mid configure-on-join before it has even
    finished interviewing.

    Independent of `_stuck`, which turns a stalled `joined`/`interviewing`
    row into `"stuck"`. The two never both apply to the same row - "stuck"
    only ever replaces `joined`/`interviewing`, never `"ready"` - so the
    order the route applies them in is free; it runs this one first only so
    that this function never has to be handed a status it did not produce.
    """
    if row.state != "ready":
        return row.state
    if row.ieee in configuring:
        return "configuring"
    if row.ieee in pending:
        return "waiting_wake"
    return "ready"


def _stuck(row: PairingRow, status: PairingRowStatus, *, now: datetime) -> PairingRowStatus:
    """A row that has stopped progressing, named for what it usually is.

    Design 3.1: 60 s for a mains device, 90 s for a battery one. **A stuck
    row is NOT an error row** - it keeps waiting and it keeps offering Retry
    and Remove, because the usual cause is a battery device that fell asleep
    and the usual fix is pressing its button. Presenting it as a failure is
    how a user comes to remove a device that was about to finish.

    Only `joined` and `interviewing` can be stuck. A `ready` row is not
    waiting for anything, and a `failed` one has already been told what
    happened."""
    if status not in ("joined", "interviewing"):
        return status
    limit = STUCK_AFTER_SECONDS_MAINS if row.is_mains_powered else STUCK_AFTER_SECONDS_BATTERY
    if (now - datetime.fromisoformat(row.changed_at)).total_seconds() >= limit:
        return "stuck"
    return status


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat(timespec="seconds")


def _thread_stick(update_dir: Path, serial: Sequence[SerialRadio]) -> tuple[str | None, bool]:
    """Which stick this installation is CURRENTLY using for Thread.

    Returns `(by-id path or None, in_use)`. The path comes from the same
    place `GET /api/radios` reads it: the sidecar's reported
    `RadioConfig.thread_device`, mapped onto a by-id path by
    `match_current_device` because the installer writes `/dev/ttyUSB0`
    while this card speaks by-id.

    `in_use` is gated on `thread_enabled` (OR `otbr_running`, which is the
    independently observed fact beside it), and that gate is deliberate.
    MEASURED in `deploy/updater/radios-once.sh`: the `down` path rewrites
    `COMPOSE_PROFILES` and LEAVES `RADIO_DEVICE` naming the stick. So "is
    this the stored Thread device" is NOT the same question as "is Thread
    using it", and answering only the first would permanently strand the
    user who disables Thread in order to repurpose a dual-capable stick -
    the only legitimate way to move an MG24 across.

    No state, or no reported current, means nothing is known to be using
    anything: `(None, False)`. That is not a licence to open a stick
    blindly - it is the same "the bridge validates what it can see"
    position `POST /api/radios` already takes.
    """
    state = read_radios_state(update_dir)
    if state is None or state.current is None:
        return None, False
    device, _present = match_current_device(state.current.thread_device, serial)
    return device, bool(state.current.thread_enabled or state.current.otbr_running)


def _is_thread_stick(
    radio_path: str, thread_device: str | None, thread_in_use: bool, host_dev: Path
) -> bool:
    """Whether this stick is the one Thread is running on.

    By RESOLVED major:minor, never by string compare. The same physical
    stick is `/dev/ttyUSB0` in `.env`, a by-id path on this card, and a
    third name under the container's `/host/dev` mount - three strings, one
    piece of hardware. MEASURED on the Pi: the two attached sticks are
    major 188 minors 0 and 1 and share the vendor id `10c4:ea60`, so the
    resolved minor is the only thing that separates them.
    """
    if thread_device is None or not thread_in_use:
        return False
    return is_same_device(radio_path, thread_device, host_dev)


def _settings_from(body: ZigbeeRadioIn, serial: Sequence[SerialRadio]) -> ZigbeeRadioSettings:
    """The setting to store, with the radio's own parameters filled in.

    The rule itself lives in `settings_for_path`, which `cli._run`'s
    `--zigbee-device` seeding calls too - there must not be two answers to
    "which baud rate belongs to this stick". All this adds is the request's
    Advanced disclosure as the source of the three overrides.
    """
    return settings_for_path(
        body.path,
        serial,
        radio_type=body.radio_type,
        baudrate=body.baudrate,
        flow_control=body.flow_control,
    )


def build_zigbee_router(
    store: Store,
    *,
    zigbee_runtime: ZigbeeRuntime,
    host_dev: Path,
    sys_root: Path,
    update_dir: Path,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/zigbee/radio")
    async def get_zigbee_radio() -> dict[str, object]:
        serial = scan_serial(host_dev, sys_root)
        thread_device, thread_in_use = _thread_stick(update_dir, serial)
        stored = store.zigbee_settings.get()
        sticks: list[dict[str, object]] = []
        for radio in serial:
            fingerprint = match_fingerprint(radio)
            is_thread = _is_thread_stick(radio.path, thread_device, thread_in_use, host_dev)
            sticks.append(
                {
                    "path": radio.path,
                    "product": radio.product,
                    "fingerprint": None if fingerprint is None else asdict(fingerprint),
                    # LISTED WITH A REASON, never filtered out. A stick that
                    # is simply absent from this list reads as a detection
                    # bug to the user, who can see it in the Thread row two
                    # rows above. `selectable` is a separate key rather than
                    # `not is_thread` computed in the page, so the card
                    # cannot drift from the server's own rule - and so that
                    # a future second reason to refuse a stick has somewhere
                    # to live.
                    "is_thread": is_thread,
                    "selectable": not is_thread,
                }
            )
        _resolved, present = match_current_device(stored.path, serial)
        return {
            "serial": sticks,
            "configured_path": stored.path,
            # Whether the stored stick is ACTUALLY THERE, resolved through
            # the live scan exactly as `GET /api/radios` does for Thread
            # (`match_current_device` -> `thread_device_present`). Without
            # it, a stored by-id path naming a node that will never return
            # is indistinguishable from a stick that is present and
            # refusing to open - and the supervisor retries the first case
            # forever while the card says only "not connected". A
            # `configured_path` of `None` reports `False` without being an
            # error, because nothing is configured.
            "configured_device_present": present,
            # What the stored stick is OPENED with. For a recognised stick
            # these are the fingerprint table's own values; for an
            # unrecognised one they are what the user typed into the
            # card's Advanced disclosure - and without them the card could
            # only prefill that disclosure with `DEFAULT_UNKNOWN`, showing
            # EZSP at 115200 beside a bridge that is in fact retrying the
            # stick as ZNP at 38400.
            "configured_radio_type": stored.radio_type,
            "configured_baudrate": stored.baudrate,
            # Translated here, per answer, and not when the attempt failed:
            # the card polls this for as long as the supervisor retries, and
            # a language switch must reach it on the next poll.
            "progress": zigbee_runtime.progress().as_json(),
        }

    @router.put("/zigbee/radio", status_code=202)
    async def put_zigbee_radio(body: ZigbeeRadioIn) -> dict[str, object]:
        serial = scan_serial(host_dev, sys_root)
        if body.path is not None:
            radio = next((item for item in serial if item.path == body.path), None)
            if radio is None:
                raise HTTPException(
                    status_code=400, detail=i18n.t("api.errors.zigbee_unknown_device")
                )
            thread_device, thread_in_use = _thread_stick(update_dir, serial)
            # THE most dangerous request this API can be sent, and the
            # reason it is checked here and not only in the page: the card
            # disables the option, but a stale tab, a second browser or a
            # curl call must not be able to point zigpy at the radio a live
            # Thread border router is running on. The card is a courtesy;
            # this is the guarantee.
            if _is_thread_stick(radio.path, thread_device, thread_in_use, host_dev):
                raise HTTPException(
                    status_code=400, detail=i18n.t("api.errors.zigbee_is_thread_stick")
                )
        settings = _settings_from(body, serial)
        store.zigbee_settings.save(settings)
        # Schedules, never awaits. `apply()` is deliberately synchronous so
        # that this line cannot accidentally grow an `await`: the
        # reconnection behind it costs a 9-15 s quirks warm-up on a Pi on a
        # first-ever configuration, plus `startup(auto_form=True)`, plus a
        # possible 7.5 s timeout on a silent port. `progress` below, read
        # again from `GET`, is what makes the fast answer honest rather
        # than merely fast.
        zigbee_runtime.apply(settings)
        return {"progress": zigbee_runtime.progress().as_json()}

    # ------------------------------------------------------------- pairing --

    def _require_source() -> ZigbeeSource:
        """The live source, or a 503 that says why there is none.

        Read per request, never captured: a radio change builds a NEW
        `ZigbeeSource`, and between the teardown and the rebuild there is
        none at all (`ZigbeeRuntime._swap`). Both are honestly "there is no
        Zigbee radio right now", with the status the rest of the bridge
        already uses for an unconfigured source - but they are not the same
        sentence. In the swap window the user has just picked a stick, and
        the pairing tab is only on screen because one is set up; telling
        them to "pick one under Settings first" there would be false.
        `ZigbeeRuntime.progress()` reports `applying` for exactly that
        window, and it is what tells the two apart."""
        source = zigbee_runtime.current()
        if source is None:
            if zigbee_runtime.progress().state == "applying":
                raise HTTPException(status_code=503, detail=i18n.t("api.zigbee.radio_changing"))
            raise HTTPException(status_code=503, detail=i18n.t("api.errors.zigbee_not_configured"))
        return source

    def _require_still_live(source: ZigbeeSource) -> None:
        """A 503 if `source` stopped being the live one during an `await`.

        A radio change releases the old source without clearing its rows -
        the devices did not go anywhere - so a row check against the source
        a request started with still passes after the swap has taken it
        away. Anything written after an `await` has to ask this first."""
        if zigbee_runtime.current() is not source:
            _require_source()
            raise HTTPException(status_code=503, detail=i18n.t("api.zigbee.radio_changing"))

    def _require_row(source: ZigbeeSource, ieee: str) -> PairingRow:
        """The pairing row this request is about.

        404 rather than a silent no-op: a row can disappear between the
        page being rendered and a button on it being pressed - the device
        was removed from another tab, or the radio was swapped and took
        every row with it - and a Retry that answers 2xx for a device
        nothing has heard of leaves the user watching a row that will never
        change."""
        for row in source.pairing_rows():
            if row.ieee == ieee:
                return row
        raise HTTPException(status_code=404, detail=i18n.t("api.zigbee.unknown_device"))

    def _require_ready_row(source: ZigbeeSource, ieee: str) -> PairingRow:
        """The row, and a 409 unless its STORED state is `"ready"`.

        The stored `PairingRow.state`, not the status the tab displays: a
        row displayed as "configuring" or "waiting_wake" is stored as
        "ready" and may be named - a sleeping sensor can wait days for the
        wake-up that clears its last pending cluster, and refusing its name
        for that long would be absurd. What is refused is a row that has
        gone back to "joined", "interviewing" or "failed": registering
        then would store whatever half an interview produced, a device tile
        with no signals and no way to tell why."""
        row = _require_row(source, ieee)
        if row.state != "ready":
            raise HTTPException(status_code=409, detail=i18n.t("api.zigbee.not_ready_yet"))
        return row

    @router.post("/zigbee/permit")
    async def open_join_window(body: ZigbeePermitIn) -> dict[str, object]:
        """Opens the network for new devices - or, with a duration of 0,
        closes it.

        Answers with the window's END TIMESTAMP rather than with the
        duration it was given, and that is the whole point of the route's
        shape: a page reloaded halfway through counts down to the truth
        instead of starting a fresh 254 s of its own, and a second tab and a
        phone agree with it. ZHA counts down in the browser from the moment
        the page opened, which is why a reloaded ZHA page cheerfully shows a
        window that closed minutes ago.

        `permit_until` is `null` when no window is open: after a Stop, and
        after a duration that has already elapsed.

        **A Stop that finds nothing open is not an error.** After a lost
        link the window has already closed with the radio
        (`ZigbeeSource._close_window`), so the Stop the tab sends every time
        the user leaves it has nothing left to do - and answering it with
        "could not be opened" would put an error in front of the user on
        every tab change after one radio blip. A Stop that fails while a
        window IS still recorded as open stays a 502: the coordinator may
        still be letting devices in, and that is worth saying."""
        source = _require_source()

        async def _permit() -> None:
            await source.permit(body.duration)

        try:
            # Bounded like every other call into a source (boundary design
            # open point 11). The wrapper exists only because
            # `bounded_source_call` takes a coroutine that returns nothing,
            # and the window's end is read back from the source below - one
            # bound and one vocabulary is worth more than the two lines it
            # costs here.
            await bounded_source_call(_permit())
        except DeviceUnreachableError as exc:
            if body.duration == 0 and source.permit_until() is None:
                return {"permit_until": None}
            # A failed Stop has its own sentence: "could not be opened" is
            # false for a request that asked to close, and this refusal is
            # the one that says the network may still be open.
            key = "api.zigbee.close_failed" if body.duration == 0 else "api.zigbee.permit_failed"
            raise HTTPException(status_code=502, detail=i18n.t(key, exc=str(exc))) from exc
        return {"permit_until": _iso(source.permit_until())}

    def _row_out(
        row: PairingRow,
        *,
        configuring: frozenset[str],
        pending: Sequence[str],
        now: datetime,
    ) -> dict[str, object]:
        status = _stuck(row, _row_status(row, configuring=configuring, pending=pending), now=now)
        device_id = store.device_id_for("zigbee", row.ieee)
        stored = None if device_id is None else store.device(device_id)
        return {
            "ieee": row.ieee,
            "state": status,
            "manufacturer": row.manufacturer,
            "model": row.model,
            # loxmatter's equivalent of Z2M's "Unsupported" badge, and a
            # different thing in a different place from a failed interview:
            # users routinely confuse the two, and a device that simply has
            # no quirk is not broken.
            "quirk_applied": row.quirk_applied,
            "discovered": row.discovered,
            "changed_at": row.changed_at,
            "suggested_name": _suggested_name(row),
            # `null` until the user has named the device and it became one
            # of the bridge's own; the tab shows the name field for exactly
            # that transition.
            "device_id": device_id,
            "name": None if stored is None else stored.label,
            "room": None if stored is None else stored.room,
        }

    @router.get("/zigbee/pairing")
    async def list_pairing() -> dict[str, object]:
        """Every device the radio has seen since it came up, and what is
        happening to it.

        Both overlays are read ONCE for the whole list rather than once per
        row - one query, one snapshot of the configuring set - and both are
        read FRESH on every request. That second half is not an
        optimisation: `zigbee_pending_config` is on disk precisely so that a
        cluster owed to a sleeping device outlives a bridge restart, and a
        "waiting to wake" cached on the row or on the source would be right
        until the next restart and silently wrong - back to a bare "ready" -
        afterwards, for exactly the battery sensor it matters most for. It
        is also what makes the way BACK need no code at all: once the wake-up
        path has cleared the last pending row, the overlay simply stops
        applying."""
        source = _require_source()
        configuring = source.configuring_addresses()
        pending = store.zigbee_pending.addresses_with_pending()
        now = datetime.now(UTC)
        return {
            "permit_until": _iso(source.permit_until()),
            "rows": [
                _row_out(row, configuring=configuring, pending=pending, now=now)
                for row in source.pairing_rows()
            ],
        }

    @router.post("/zigbee/pairing/{ieee}/retry", status_code=202)
    async def retry_pairing(ieee: str) -> dict[str, object]:
        """Interviews a device again - the Retry a failed or stuck row
        offers.

        202, and deliberately: an interview talks to a device that may be
        asleep, and holding the request open for it is the bug
        `bounded_source_call` exists to prevent one layer down. The row goes
        back to "interviewing" and the tab watches it from there."""
        source = _require_source()
        _require_row(source, ieee)
        try:
            source.retry_interview(ieee)
        except DeviceUnreachableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"state": "interviewing"}

    @router.delete("/zigbee/pairing/{ieee}", status_code=204)
    async def remove_pairing(ieee: str) -> None:
        """Asks the device to leave, and forgets it either way.

        `app.remove()` deletes the device from zigpy's database whether or
        not the leave request is ever delivered, and nothing stops the
        device rejoining - the confirmation copy in the tab says exactly
        that. Waiting for an acknowledgement a sleeping device will never
        send would leave the UI showing a device the bridge has already
        forgotten.

        The radio first, then the store, for the reason
        `api/devices.py`'s module docstring spells out: the failure that
        leaves a visible, diagnosable state beats the one that leaves a
        device nothing can reach any more."""
        source = _require_source()
        _require_row(source, ieee)
        try:
            await bounded_source_call(source.remove(ieee))
        except DeviceUnreachableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        device_id = store.device_id_for("zigbee", ieee)
        if device_id is not None:
            # Only if it was ever adopted. A device removed straight off the
            # pairing tab never became one of the bridge's own, and there is
            # nothing in the store to forget.
            store.forget_device(device_id)

    @router.patch("/zigbee/pairing/{ieee}")
    async def name_pairing(ieee: str, patch: ZigbeePairingPatch) -> dict[str, object]:
        """Names a device, and in doing so adopts it.

        This is where a Zigbee device becomes one of the bridge's own:
        zigpy knows it the moment its interview finishes, but nothing in
        loxmatter's own store does until the user says what to call it.
        Registering here rather than on `device_initialized` keeps a device
        somebody is still deciding about out of the device list, the export
        template and Loxone.

        `register_device` before `register_signals` before
        `register_commands`, the same order the Matter commissioning route
        and the CLI export use, because the last two need the freshly
        assigned id. `set_room` afterwards whenever a room was sent - for
        a device the store already knows too, and that is the point of it:
        `register_device`'s own `room` argument only takes effect on a
        newly inserted row, so a second save that moves an adopted device
        would otherwise be discarded without comment. A save that sends no
        room leaves the room alone, the `PATCH /api/devices/{id}`
        convention.

        The values follow through `follow`, which is what reaches
        `Runtime.on_node_snapshot`. `seed_even_without_new_paths` makes that
        a whole snapshot even for a device whose every path has been
        delivered before - which only a SECOND save of an already adopted
        device can be. On a first adoption nothing has been delivered yet
        (the source drops every update for a device the store does not
        know), so a snapshot goes out with or without the flag.

        **The row is checked twice: before the snapshot read and after it.**
        The read is an `await`, and for a colour lamp whose capabilities
        are not cached it is a real round trip to the device - time in
        which the device can be removed from another tab, rejoin and fall
        back to "joined", or lose its radio to a swap. Everything written
        to the store below is written only once the second check has
        passed, with no `await` between that check and the last store
        write. Without it, a removal during the read left a registered
        device no route could remove, in exactly the three places this
        route exists to keep an unnamed device out of."""
        source = _require_source()
        _require_ready_row(source, ieee)
        found: list[NodeSnapshot] = []

        async def _read() -> None:
            snapshot = await source.snapshot_for(ieee)
            if snapshot is not None:
                found.append(snapshot)

        try:
            await bounded_source_call(_read())
        except DeviceUnreachableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=404, detail=i18n.t("api.zigbee.unknown_device"))
        snapshot = found[0]
        # The second look, after the await - see the docstring. From here to
        # `register_commands` there is no `await`, so nothing can change the
        # row between this check and the writes it guards.
        _require_still_live(source)
        _require_ready_row(source, ieee)

        device_id = store.register_device(snapshot, room=patch.room)
        if patch.name is not None:
            store.rename_device(device_id, patch.name)
        if patch.room is not None:
            store.set_room(device_id, patch.room)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        try:
            await bounded_source_call(source.follow(ieee, seed_even_without_new_paths=True))
        except Exception:
            # Follow-up work, and follow-up work must not retroactively
            # cancel the adoption: the device IS registered by now, and an
            # error here would send the user back to a row they have
            # already named. The signal rows exist; their first values
            # arrive with the next report.
            logger.exception(
                "could not seed the signals of the freshly adopted Zigbee device %s", ieee
            )
        # Read-only from here on. A removal that lands during `follow` finds
        # the device already registered and forgets it in the store itself
        # (the DELETE route reads the device id AFTER its own await), so the
        # 404 this lookup then raises describes a device that is really gone.
        configuring = source.configuring_addresses()
        pending = store.zigbee_pending.addresses_with_pending()
        return _row_out(
            _require_row(source, ieee),
            configuring=configuring,
            pending=pending,
            now=datetime.now(UTC),
        )

    return router
