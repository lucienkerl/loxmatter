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

"""`GET` and `PUT /api/zigbee/radio` - which stick is the Zigbee
coordinator.

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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from loxmatter import i18n
from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import ZigbeeRadioSettings
from loxmatter.radios.fingerprints import (
    DEFAULT_UNKNOWN,
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
from loxmatter.timestamps import now_iso
from loxmatter.zigbee.runtime import ZigbeeRuntime


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

    From `match_fingerprint(radio)` when the table recognises the stick,
    and from the request's own Advanced fields when it does not - falling
    back to `DEFAULT_UNKNOWN`'s values, never to a guess presented as a
    detection. A recognised stick ignores the Advanced fields entirely: the
    table is the measured answer, and a stale disclosure value silently
    overriding it would open a coordinator at the wrong speed.
    """
    radio = None if body.path is None else next((r for r in serial if r.path == body.path), None)
    fingerprint = None if radio is None else match_fingerprint(radio)
    if fingerprint is not None:
        radio_type = fingerprint.radio_type
        baudrate = fingerprint.baudrate
        flow_control = fingerprint.flow_control
    else:
        radio_type = DEFAULT_UNKNOWN.radio_type if body.radio_type is None else body.radio_type
        baudrate = DEFAULT_UNKNOWN.baudrate if body.baudrate is None else body.baudrate
        flow_control = (
            DEFAULT_UNKNOWN.flow_control if body.flow_control is None else body.flow_control
        )
    return ZigbeeRadioSettings(
        path=body.path,
        radio_type=radio_type,
        baudrate=baudrate,
        flow_control=flow_control,
        saved_at=now_iso(),
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
            "progress": asdict(zigbee_runtime.progress()),
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
        return {"progress": asdict(zigbee_runtime.progress())}

    return router
