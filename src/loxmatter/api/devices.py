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

"""Device and signal API of the WebUI (Spec 8, views 1 and 2).

`build_device_router` builds an `APIRouter` with prefix `/api` - wired
into `loxone.server.build_app`, which also uses the same FastAPI app for
the Loxone-side routes (`/cmd`, `/resync`, `/health`).

`client` is `None` if the bridge was started without a connection to
`matter-server` (see `build_app`). The two routes that need the Matter
client - commissioning and removal - then respond with 503 instead of
throwing an `AttributeError` on `None`; all other routes (reading,
renaming, setting the export flag) work entirely without a Matter
connection and remain usable.

**Removal: `remove` first, then `forget_device`.** Removing
a device is two steps that cannot sit in one transaction (one is a
network call to matter-server, the other a local SQLite write) - either
one can succeed while the other fails. The two possible orders leave
behind states of different severity on a partial failure:

- **`forget_device` first, then `remove` fails:** `Store` considers
  the device removed, it disappears from `GET /api/devices` - but it
  still hangs in the Matter fabric. From this moment on, the WebUI has no
  more `device_id` under which a renewed removal could be triggered. A
  silent leftover no longer reachable from the UI.
- **`remove` first, then `forget_device` fails:** the device has
  actually been removed from the fabric, but `Store` still lists it as
  active. It stays visible in `GET /api/devices` - and, as soon as
  `BridgeMatterClient.subscribe` delivers the associated `NODE_REMOVED`
  event, correctly reports itself via `Runtime.set_online` as no longer
  reachable (`d<id>_online = false`), exactly like any other device that
  loses its connection (Spec 9). A renewed `DELETE` remains possible, and
  the failed second step is an ordinary, visible server error, not a
  vanished device.

The second order thus leaves behind a visible, diagnosable state instead
of a silent one on failure; `remove_device` below therefore implements
it.

**Unverified assumption (Minor #3, Review 2026-09-02):** "A second
`DELETE` remains possible" above assumes that `remove` may be called again
against a device that was already removed from the fabric on the first (partially
failed) attempt - so it is retry-safe against `matter-server`. `tests/api/conftest.py::FakeMatterClient.remove`
merely appends each call to a list and cannot verify this assumption; whether the real
`MatterClient.remove_node` acknowledges an already-removed device with an error or
silently ignores it has not been verified against the installed `python-matter-server`
version (unlike the three methods in the docstring of `matter/client.py`, which are
explicitly verified against the source code). A failure there would not be a new problem -
it would land like any other `MatterUnavailableError` as 502 -, but the guarantee
"remains possible" is until then an assumption, not a verified fact.

**Update (8 September 2026): the assumption remains open, now against a different
server.** Since then `matter-python-client` is installed instead of `python-matter-server`;
`MatterClient.remove_node(node_id)` carries the same signature there and still sends only
`APICommand.REMOVE_NODE` with `node_id`. What the server responds with to a second
`remove_node` against an already-removed device is thus not clarified, but only even less
clarified than before: it is now a different implementation (matter.js instead of CHIP-SDK),
and the behavior in this edge case depends on it, not on the client library. Verification
remains to be done on the live service.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from loxmatter import i18n
from loxmatter.api.models import (
    CommissionRequest,
    DeviceExpertOut,
    DeviceOut,
    DevicePatch,
    EndpointClustersOut,
    IdentifyRequest,
    RoomRename,
    SignalOut,
    SignalPatch,
)
from loxmatter.commissioning.identify import IdentifyCoordinator
from loxmatter.commissioning.run import (
    CommissionFailed,
    ThreadDatasetSource,
)
from loxmatter.commissioning.run import commission as run_commission
from loxmatter.export.signals import to_inputs
from loxmatter.firmware.states import format_spec_version
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.commissioning_progress import (
    CommissioningTracker,
    Discriminator,
)
from loxmatter.matter.otbr import (
    fetch_active_dataset,
)
from loxmatter.model.store import Store, StoredDevice, StoredSignal, UnknownDeviceError
from loxmatter.profiles.catalog import cluster_name
from loxmatter.profiles.categories import CATEGORY_RANK, category_for
from loxmatter.profiles.endpoints import endpoint_labels
from loxmatter.profiles.table import Exportability, is_exportable
from loxmatter.profiles.transport import transport_for
from loxmatter.sources import (
    DeviceUnreachableError,
    IdentifySource,
    IdentifyUnsupportedError,
    SourceNotConfiguredError,
    Sources,
    bounded_source_removal,
    technology_display_name,
)

logger = logging.getLogger(__name__)


# Why a signal is not exportable (Spec 6.6) - only for the two cases that
# `Exportability` distinguishes from ANALOG/DIGITAL. `NONE` covers both
# lists/structs and (silently, see Spec 6.6) null values; the table cannot
# tell these two apart, because `classify()` itself does not either.
#
# Keys rather than finished sentences, resolved at call time, for the same
# reason as `commissioning.run.MANUAL_DATASET_ORIGIN_KEY`: a hard German chunk inside an
# English sentence is half a translation, and the language is not settled at
# import time.
_UNEXPORTABLE_REASON_KEYS: dict[Exportability, str] = {
    Exportability.TEXT: "api.devices.unexportable_text",
    Exportability.NONE: "api.devices.unexportable_none",
}


class RuntimeValues(Protocol):
    """What this route needs from `runtime` - `loxone.runtime.Runtime`
    already satisfies this unchanged (see `last_values_for`,
    `last_heard_for` and `set_online` there), a test can satisfy it with a
    simple double, without building a real `Runtime` complete with
    sender.

    `set_online` was added when commissioning had to seed the reachability
    of a freshly commissioned device itself (see `commission_device`) -
    reading alone is not enough for that."""

    def last_values_for(self, device_id: int) -> dict[str, float | bool]: ...

    def reported_at_for(self, device_id: int) -> dict[str, str]: ...

    def last_heard_for(self, device_id: int) -> str | None: ...

    async def set_online(self, device_id: int, online: bool) -> None: ...


def _signal_out(
    signal: StoredSignal,
    values: dict[str, float | bool],
    labels: dict[int, str],
    reported_at: dict[str, str],
) -> SignalOut:
    """`functional` comes unchanged from `StoredSignal.functional` -
    `profiles.relevance.is_functional` needs the device types per endpoint
    (`device_types_by_endpoint`), which this function here does not see at
    all (only `signal` and the current values). `Store.register_signals`
    already computes the result once at registration time, with the real
    device snapshot at hand, and writes it into the row - see there
    and `_migrate_to_v4` for existing devices. A second computation here
    (or even in the UI) would rebuild the same rule a second time,
    without having the snapshot it would actually need.

    `labels` arrives as a ready-made endpoint->name mapping from the caller
    (`profiles.endpoints.endpoint_labels`, built once per device) instead
    of being recomputed here per signal - with 173 signals on one
    device that would be the same computation 173 times over the same
    device types. Two cases leave `labels` empty for an endpoint:
    either the device types for the WHOLE device have not yet been
    backfilled (`device_types IS NULL`, the normal case documented by
    `Store.backfill_device_types`'s docstring for a device that was
    offline at bridge startup - `endpoint_labels(None)`
    then returns `{}`), or a SINGLE endpoint reports no
    descriptor at all and is therefore missing as a key in `device_types`,
    even though the device itself has long been backfilled (see
    `endpoint_labels`). For both cases this function falls back to
    `endpoint_plain` HERE - as its own branch rather than a `.get()`
    default, which would be evaluated for EVERY signal: with 173 signals
    on one device that would be 173 superfluous `i18n.t` calls per
    request, even though the fallback almost never applies - exactly the
    computation the paragraph above about `labels` already avoids
    once."""
    exportable = is_exportable(signal.exportability)
    reason_key = None if exportable else _UNEXPORTABLE_REASON_KEYS.get(signal.exportability)
    reason = i18n.t(reason_key) if reason_key else None
    endpoint_label = labels.get(signal.ref.endpoint)
    if endpoint_label is None:
        endpoint_label = i18n.t("web.signals.endpoint_plain", endpoint=signal.ref.endpoint)
    return SignalOut(
        key=signal.key,
        path=signal.ref.path,
        kind=signal.ref.kind.value,
        title=signal.title,
        unit=signal.unit,
        value=values.get(signal.key),
        reported_at=reported_at.get(signal.key),
        exportable=exportable,
        reason=reason,
        exported=signal.exported,
        functional=signal.functional,
        resend=signal.resend,
        endpoint=signal.ref.endpoint,
        cluster_id=signal.ref.cluster_id,
        endpoint_label=endpoint_label,
    )


def _identify_source(sources: Sources | None, technology: str) -> IdentifySource | None:
    """The source serving `technology`, if it can make a device blink."""
    if sources is None:
        return None
    try:
        source = sources.get(technology)
    except SourceNotConfiguredError:
        return None
    return source if isinstance(source, IdentifySource) else None


def identify_coordinator(store: Store, sources: Sources | None) -> IdentifyCoordinator:
    """The bridge's one `IdentifyCoordinator`: `build_app` makes it once and
    hands the same instance to the device tiles and the commissioning
    dialog, so a blink from either stops the other's (design 2026-10-02,
    section 9.2)."""

    def _stored_address(device_id: int) -> tuple[str, str]:
        device = store.device(device_id)
        return device.technology, device.address

    return IdentifyCoordinator(
        lambda technology: _identify_source(sources, technology), _stored_address
    )


def _device_out(
    device: StoredDevice,
    store: Store,
    runtime: RuntimeValues,
    supports_identify: Callable[[StoredDevice], bool] = lambda _device: False,
) -> DeviceOut:
    # `store.signals(device.id)` fetches the full row per signal here, even
    # though `list_devices` (Minor #2, review 2026-09-02) only counts them -
    # an N+1 access per device in `GET /api/devices`. Deliberately accepted
    # instead of a dedicated COUNT/SUM query: the number of devices on a
    # bridge stays small (one Loxone instance, not a fleet), `exportable_count`
    # needs `is_exportable` per row anyway - an SQL aggregation would have to
    # replicate this rule a second time in SQL and would thereby run right
    # into the drift that Important #2 above only just fixed.
    signals = store.signals(device.id)
    values = runtime.last_values_for(device.id)
    online = bool(values.get(f"d{device.id}_online", False))
    last_heard = runtime.last_heard_for(device.id)
    exportable_count = sum(1 for s in signals if is_exportable(s.exportability))
    # next_export_count: the same composition as
    # `ExportDeviceOut.inputs` in `api/export.py` (`to_inputs`, filtered on
    # `exported`) - no second, merely similar count here. The device tile
    # previously showed "159 signals, 110 exportable" above a list of five -
    # both numbers were correct, neither answered how many inputs the next
    # export would actually produce.
    next_export_count = len(to_inputs(signals, device.id, device.label))
    category = category_for(device.device_types)
    return DeviceOut(
        id=device.id,
        technology=device.technology,
        address=device.address,
        transport=transport_for(device.technology, device.network_features),
        label=device.label,
        online=online,
        last_heard=last_heard,
        signal_count=len(signals),
        exportable_count=exportable_count,
        next_export_count=next_export_count,
        room=device.room,
        category=category.value,
        category_rank=CATEGORY_RANK[category],
        identify=supports_identify(device),
    )


def _endpoints_summary(signals: list[StoredSignal]) -> list[EndpointClustersOut]:
    """One entry per endpoint, naming the clusters present on it -
    every signal's cluster, not just functional ones, since this is a
    structural inventory of the device, not a preview of what it does."""
    by_endpoint: dict[int, set[int]] = {}
    for signal in signals:
        by_endpoint.setdefault(signal.ref.endpoint, set()).add(signal.ref.cluster_id)
    return [
        EndpointClustersOut(
            endpoint=endpoint,
            clusters=[cluster_name(cid) or f"Cluster {cid}" for cid in sorted(cluster_ids)],
        )
        for endpoint, cluster_ids in sorted(by_endpoint.items())
    ]


def build_device_router(
    store: Store,
    client: BridgeMatterClient | None,
    runtime: RuntimeValues,
    thread_dataset_source: ThreadDatasetSource | None = None,
    sources: Sources | None = None,
    tracker: CommissioningTracker | None = None,
    identify: IdentifyCoordinator | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    fetch_dataset = thread_dataset_source or fetch_active_dataset
    progress = tracker or CommissioningTracker()

    def _require_device(device_id: int) -> StoredDevice:
        try:
            return store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _supports_identify(device: StoredDevice) -> bool:
        source = _identify_source(sources, device.technology)
        if source is None:
            return False
        try:
            return source.supports_identify(device.address)
        except Exception:  # noqa: BLE001 - a flag on a tile never fails the list
            return False

    coordinator = identify or identify_coordinator(store, sources)

    def _require_client() -> BridgeMatterClient:
        if client is None:
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.devices.fail_no_matter_client"),
            )
        return client

    @router.post("/devices/{device_id}/identify", status_code=204)
    async def identify_device(device_id: int, request: IdentifyRequest) -> None:
        _require_device(device_id)
        try:
            if request.on:
                await coordinator.start(device_id, renew=False)
            elif coordinator.blinking == device_id:
                await coordinator.stop()
        except IdentifyUnsupportedError as exc:
            raise HTTPException(
                status_code=409, detail=i18n.t("api.commissioning.fail_no_identify")
            ) from exc
        except (MatterUnavailableError, DeviceUnreachableError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/devices")
    async def list_devices() -> list[DeviceOut]:
        return [
            _device_out(device, store, runtime, _supports_identify) for device in store.devices()
        ]

    @router.get("/devices/{device_id}")
    async def get_device(device_id: int) -> DeviceOut:
        device = _require_device(device_id)
        return _device_out(device, store, runtime, _supports_identify)

    @router.get("/devices/{device_id}/signals")
    async def get_signals(device_id: int) -> list[SignalOut]:
        device = _require_device(device_id)
        values = runtime.last_values_for(device_id)
        # Built once per device, not per signal - see the docstring of
        # `_signal_out`.
        labels = endpoint_labels(device.device_types)
        reported_at = runtime.reported_at_for(device_id)
        return [
            _signal_out(signal, values, labels, reported_at) for signal in store.signals(device_id)
        ]

    @router.get("/devices/{device_id}/expert")
    async def get_expert(device_id: int) -> DeviceExpertOut:
        device = _require_device(device_id)
        signals = store.signals(device_id)
        return DeviceExpertOut(
            technology=device.technology,
            transport=transport_for(device.technology, device.network_features),
            address=device.address,
            vendor=device.vendor_name,
            model=device.product_name,
            firmware=device.firmware,
            matter_version=format_spec_version(device.matter_spec_version, device.technology),
            serial=device.serial_number,
            endpoints=_endpoints_summary(signals),
        )

    @router.patch("/devices/{device_id}")
    async def patch_device(device_id: int, patch: DevicePatch) -> DeviceOut:
        """Changes label and/or room. `None` means "unchanged" for both
        fields; the empty string in room means "remove".

        The two write paths are deliberately different: `rename_device`
        also sets `updated_at` (the label is exported as `Title`),
        `set_room` does not (the room is never exported anywhere). See the
        docstrings of both store methods."""
        device = _require_device(device_id)
        if patch.label is not None:
            store.rename_device(device.id, patch.label)
        if patch.room is not None:
            store.set_room(device.id, patch.room)
        return _device_out(store.device(device.id), store, runtime, _supports_identify)

    @router.post("/rooms/rename")
    async def rename_room(patch: RoomRename) -> dict[str, int]:
        """Renames a room across all active devices and all groups.

        The only route that exists for rooms at all - there are no room
        objects (design 3.2), so no `GET /api/rooms` either: the room list
        already lives inside `GET /api/devices`, and a second endpoint for
        the same information could only drift out of sync.

        404 instead of "0 renamed" if no active device AND no group
        carries the source name: a typo in the source name would
        otherwise look like a successful operation. A room that only a
        group occupies - no device left in it - is exactly the case
        `Store.rename_room` learned to cover, so it must not 404 here
        either; see that method's docstring."""
        if not patch.to_room.strip():
            raise HTTPException(status_code=422, detail=i18n.t("api.devices.room_name_required"))
        renamed = store.rename_room(patch.from_room, patch.to_room)
        if renamed == 0:
            raise HTTPException(
                status_code=404,
                detail=i18n.t("api.devices.unknown_room", room=patch.from_room),
            )
        return {"renamed": renamed}

    @router.patch("/signals/{key}")
    async def rename_signal(key: str, patch: SignalPatch) -> SignalOut:
        """Changes title, export and resend flag. The key remains untouched.

        Spec 6.2: the key is the wiring in Loxone. If it were changeable
        here, a click in the UI could silently kill a component in the
        house dead. The `SignalPatch` model therefore has no field for it
        at all - a `key` sent along anyway is discarded, not applied.

        Unlike every device-bound route (`_require_device` above), this
        route previously resolved exclusively via `signal_by_key`, without
        checking whether the associated device is even still active
        (review fix Important #4, 2026-09-02): after `DELETE
        /api/devices/{id}`, `GET /api/devices/{id}` correctly reported
        404, but `PATCH /api/signals/{key}` still mutated the row of a
        removed device without complaint - a signal row no longer visible
        anywhere in the UI, but still changeable via its key nonetheless.
        The check below closes this gap.
        """
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(
                status_code=404, detail=i18n.t("api.errors.unknown_signal_key", signal_key=key)
            )
        try:
            device = store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.signal_belongs_to_removed_device",
                    signal_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc
        if patch.title is not None:
            store.set_title(key, patch.title)
        if patch.exported is not None:
            store.set_exported(key, patch.exported)
        if patch.resend is not None:
            store.set_resend(key, patch.resend)

        updated = store.signal_by_key(key)
        assert updated is not None  # just found, not deleted within the same request
        values = runtime.last_values_for(updated.device_id)
        labels = endpoint_labels(device.device_types)
        return _signal_out(updated, values, labels, runtime.reported_at_for(updated.device_id))

    @router.get("/devices/commission/status")
    async def commission_status() -> dict[str, object]:
        """Design 2026-09-22, section 5.2. The dialog polls it every 2 s.

        Read-only: the POST route drives the sampling (see there), so a page
        that is not open costs nothing and changes nothing."""
        return progress.status()

    @router.post("/devices/commission", status_code=201)
    async def commission_device(request: CommissionRequest) -> DeviceOut:
        active_client = _require_client()

        try:
            result = await run_commission(
                code=request.code,
                room=request.room,
                discriminator=(
                    Discriminator(request.discriminator.value, request.discriminator.kind)
                    if request.discriminator is not None
                    else None
                ),
                client=active_client,
                store=store,
                runtime=runtime,
                tracker=progress,
                fetch_dataset=fetch_dataset,
                manual_dataset=request.thread_dataset,
            )
        except CommissionFailed as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
        return _device_out(store.device(result.device_id), store, runtime, _supports_identify)

    @router.delete("/devices/{device_id}", status_code=204, response_model=None)
    async def remove_device(device_id: int, forget_only: bool = False) -> JSONResponse | None:
        """Removes a device through its source, then forgets it (see the
        module docstring for the order).

        **A device whose technology has no configured source can still be
        forgotten.** Matter-server is mandatory, but "no Zigbee stick" is a
        legitimate, permanent state - a user who tried Zigbee and gave the
        stick up - and a 503 for every Zigbee tile, forever, left tiles
        nothing could delete. That 503 therefore carries `"offer":
        "forget_only"`, which the device list recognises and answers with a
        second, explicit choice; `?forget_only=true` then forgets the device
        in the store exactly as a removal does - device list, export, group
        memberships, pending Zigbee configuration - without contacting any
        radio. The device itself is not told, and the page says so.

        **Refused while the technology's source IS configured** (409): then
        the source is the way to remove the device, and for Matter - whose
        source is always there - forgetting a device the fabric still holds
        is exactly the silent leftover the removal order exists to prevent."""
        device = _require_device(device_id)
        if sources is None:
            # `build_app` derives `sources` from `client`, so this is the
            # app started without any device connection - the same 503 the
            # route answered before.
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.devices.fail_no_matter_client"),
            )
        try:
            source = sources.get(device.technology)
        except SourceNotConfiguredError as exc:
            # The registry raises this the same way for two different
            # situations, and only one of them may offer "forget only":
            #
            # - No stick is stored at all - the user tried Zigbee and gave
            #   the stick up. That is permanent, and the forget-only offer
            #   below is the only way such a tile is ever removable.
            # - A stick IS stored, but `sources.get()` still raises: a
            #   radio change is in flight (`ZigbeeRuntime._release` clears
            #   the registry before `disconnect()` returns) or the stick
            #   failed to open. That is transient - the same "there is no
            #   Zigbee radio right now" the pairing routes already answer
            #   with `api.zigbee.radio_changing` - and forgetting the
            #   device here would remove it from loxmatter while it is
            #   still joined to the network the swap is about to reopen.
            #   Read the STORED setting, not the registry that is empty in
            #   both cases, exactly as `GET /api/zigbee/radio` does.
            if exc.technology == "zigbee" and store.zigbee_settings.get().path is not None:
                raise HTTPException(
                    status_code=503, detail=i18n.t("api.zigbee.radio_changing")
                ) from exc
            if forget_only:
                logger.info(
                    "forgetting device %s (%s) without its radio: %s is not set up",
                    device.id,
                    device.address,
                    technology_display_name(device.technology),
                )
                store.forget_device(device.id)
                return None
            return JSONResponse(
                status_code=503, content={"detail": str(exc), "offer": "forget_only"}
            )
        if forget_only:
            raise HTTPException(
                status_code=409,
                detail=i18n.t(
                    "api.devices.forget_only_refused",
                    technology=technology_display_name(device.technology),
                ),
            )
        try:
            # Order: see module docstring - the fabric first, then the store.
            #
            # Bounded like every other call into a source (boundary design
            # open point 11), but by the removal's own, longer bound and not
            # by a command's 10 s: matter-server forgets the node and then
            # asks the device to leave the fabric, which for an offline
            # Thread device takes longer than that - cut off early, the
            # device stayed listed here while matter-server had dropped it.
            # See `SOURCE_REMOVAL_TIMEOUT_SECONDS`. The expiry arrives as
            # `DeviceUnreachableError`, caught right below.
            await bounded_source_removal(source.remove(device.address))
        except (MatterUnavailableError, DeviceUnreachableError) as exc:
            # One vocabulary across sources (boundary design open point 11).
            # `MatterUnavailableError` stays in the tuple rather than being
            # replaced: it is what the Matter client has always raised here
            # and every existing test asserts on it.
            #
            # A bare `TimeoutError` is deliberately NOT in this tuple. It was,
            # and nothing could reach it: the only timeout this route can
            # produce is the one above, and that arrives as
            # `DeviceUnreachableError` - by design, since `str(TimeoutError())`
            # is empty and would have made this a 502 with a blank detail. A
            # source that lets a raw `TimeoutError` escape instead of raising
            # `DeviceUnreachableError` at its own edge is a bug in that
            # source, and should look like one.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.forget_device(device.id)
        return None

    return router
