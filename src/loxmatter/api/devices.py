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

**Removal (Task 2): `remove_node` first, then `forget_device`.** Removing
a device is two steps that cannot sit in one transaction (one is a
network call to matter-server, the other a local SQLite write) - either
one can succeed while the other fails. The two possible orders leave
behind states of different severity on a partial failure:

- **`forget_device` first, then `remove_node` fails:** `Store` considers
  the device removed, it disappears from `GET /api/devices` - but it
  still hangs in the Matter fabric. From this moment on, the WebUI has no
  more `device_id` under which a renewed removal could be triggered. A
  silent leftover no longer reachable from the UI.
- **`remove_node` first, then `forget_device` fails:** the device has
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
`DELETE` remains possible" above assumes that `remove_node` may be called again
against a device that was already removed from the fabric on the first (partially
failed) attempt - so it is retry-safe against `matter-server`. `tests/api/conftest.py::FakeMatterClient.remove_node`
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
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.models import (
    CommissionRequest,
    DeviceOut,
    DevicePatch,
    RoomRename,
    SignalOut,
    SignalPatch,
)
from loxmatter.export.commands import extract_commands
from loxmatter.export.signals import to_inputs
from loxmatter.matter.client import BridgeMatterClient, CommissioningError, MatterUnavailableError
from loxmatter.matter.otbr import (
    ThreadDatasetUnavailableError,
    fetch_active_dataset,
    validated_dataset,
)
from loxmatter.model.store import Store, StoredDevice, StoredSignal, UnknownDeviceError
from loxmatter.profiles.categories import CATEGORY_RANK, category_for
from loxmatter.profiles.endpoints import endpoint_labels
from loxmatter.profiles.table import Exportability, is_exportable

logger = logging.getLogger(__name__)

# Where the Thread dataset comes from when matter-server does not (or no
# longer) have it. A dedicated type instead of the bare call, so `build_app`
# can replace it in tests with a source that has no network - the same seam
# pattern as `session_factory` in `matter/client.py`.
ThreadDatasetSource = Callable[[], Awaitable[str]]

# Where the checked dataset came from - `validated_dataset` prepends this to
# its messages. The fetched one names the URL of the Border Router there,
# the manually entered one this line; it ends up exclusively in the log,
# never in the response (see `commission_device`).
#
# A key instead of a finished sentence, and resolved only at call time: the
# messages from `validated_dataset` have run through `i18n.t()` since the
# i18n phase, a fixed German chunk in the middle of an English sentence
# would be half a translation. At import time the language is also not yet
# determined at all (cli.py only sets it afterwards).
_MANUAL_DATASET_ORIGIN_KEY = "api.devices.manual_dataset_origin"

# Why a signal is not exportable (Spec 6.6) - only for the two cases that
# `Exportability` distinguishes from ANALOG/DIGITAL. `NONE` covers both
# lists/structs and (silently, see Spec 6.6) null values; the table cannot
# tell these two apart, because `classify()` itself does not either.
#
# Keys rather than finished sentences, resolved at call time, for the same
# reason as `_MANUAL_DATASET_ORIGIN_KEY` above: a hard German chunk inside an
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

    def last_heard_for(self, device_id: int) -> str | None: ...

    async def set_online(self, device_id: int, online: bool) -> None: ...


def _signal_out(
    signal: StoredSignal, values: dict[str, float | bool], labels: dict[int, str]
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
        exportable=exportable,
        reason=reason,
        exported=signal.exported,
        functional=signal.functional,
        resend=signal.resend,
        endpoint=signal.ref.endpoint,
        cluster_id=signal.ref.cluster_id,
        endpoint_label=endpoint_label,
    )


def _device_out(device: StoredDevice, store: Store, runtime: RuntimeValues) -> DeviceOut:
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
    # next_export_count (follow-up Fix 7, Phase 6): the same composition as
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
        label=device.label,
        online=online,
        last_heard=last_heard,
        signal_count=len(signals),
        exportable_count=exportable_count,
        next_export_count=next_export_count,
        room=device.room,
        category=category.value,
        category_rank=CATEGORY_RANK[category],
    )


def _commissioning_detail(exc: CommissioningError, missing_dataset_reason: str | None) -> str:
    """The message that reaches the UI.

    Without the addition, it only stated what matter-server itself says -
    "Commission with code failed for node 7." The actual reason ("Required
    network information not provided in commissioning parameters") lives
    exclusively in matter-server's log, and without access to it the
    message cannot be interpreted: BLE, pairing code, and the secured
    session to the device were all fine, only the network the device
    should have belonged to was missing.
    """
    detail = str(exc)
    if missing_dataset_reason is None:
        return detail
    return i18n.t(
        "api.devices.commissioning_thread_cause",
        detail=detail,
        reason=missing_dataset_reason,
    )


def build_device_router(
    store: Store,
    client: BridgeMatterClient | None,
    runtime: RuntimeValues,
    thread_dataset_source: ThreadDatasetSource | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    fetch_dataset = thread_dataset_source or fetch_active_dataset

    def _require_device(device_id: int) -> StoredDevice:
        try:
            return store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _require_client() -> BridgeMatterClient:
        if client is None:
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.devices.fail_no_matter_client"),
            )
        return client

    @router.get("/devices")
    async def list_devices() -> list[DeviceOut]:
        return [_device_out(device, store, runtime) for device in store.devices()]

    @router.get("/devices/{device_id}")
    async def get_device(device_id: int) -> DeviceOut:
        device = _require_device(device_id)
        return _device_out(device, store, runtime)

    @router.get("/devices/{device_id}/signals")
    async def get_signals(device_id: int) -> list[SignalOut]:
        device = _require_device(device_id)
        values = runtime.last_values_for(device_id)
        # Built once per device, not per signal - see the docstring of
        # `_signal_out`.
        labels = endpoint_labels(device.device_types)
        return [_signal_out(signal, values, labels) for signal in store.signals(device_id)]

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
        return _device_out(store.device(device.id), store, runtime)

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
        return _signal_out(updated, values, labels)

    @router.post("/devices/commission", status_code=201)
    async def commission_device(request: CommissionRequest) -> DeviceOut:
        active_client = _require_client()

        # Why this even exists: matter-server holds the Thread credentials
        # ONLY in memory and forgets them on every restart (the full
        # rationale, including a recorded real-world incident, is in
        # `matter/otbr.py`). The input field alone did not catch this - it
        # is optional and is cleared after every commissioning, so it was
        # empty the next time.
        missing_dataset_reason: str | None = None

        if request.thread_dataset is not None:
            # A manually entered dataset takes priority over the one from
            # the host: it is the path for a Thread network that does not
            # come from this Border Router.
            #
            # It is checked the same way as the fetched one -
            # `validated_dataset` from `matter/otbr.py`, deliberately the
            # same function and not a second replica of the same rule
            # (hence the import of a module-private name). Passed through
            # unchecked, a dataset inserted with a line break or as a JSON
            # structure would trigger a `bytes.fromhex` failure at
            # matter-server that comes back as a `FailedCommand` - not a
            # `MatterUnavailableError`, so a 500 "Internal Server Error",
            # the most meaningless of all responses.
            try:
                dataset = validated_dataset(
                    request.thread_dataset, i18n.t(_MANUAL_DATASET_ORIGIN_KEY)
                )
            except ThreadDatasetUnavailableError as exc:
                # 422 like a rejected pairing code: the request is
                # well-formed, but its content is unusable. The reason
                # goes in the response, the dataset itself does NOT - it
                # contains the network key of the Thread network (see
                # `matter/otbr.py`). `str(exc)` also stays out: its wording
                # asks about the Border Router, and that has nothing to do
                # with an input field. It may go into the log, where it
                # names the length.
                logger.warning("Entered Thread dataset rejected: %s", exc)
                raise HTTPException(
                    status_code=422,
                    detail=i18n.t("api.devices.fail_manual_thread_dataset"),
                ) from exc
            try:
                await active_client.set_thread_dataset(dataset)
            except MatterUnavailableError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        elif not active_client.thread_dataset_set:
            try:
                dataset = await fetch_dataset()
            except ThreadDatasetUnavailableError as exc:
                # NO abort: a WiFi device needs no Thread dataset at all,
                # and a setup without its own Border Router remains usable
                # as a result. The reason is only remembered in case
                # commissioning fails right afterwards - then it is the
                # likely cause and belongs in the message.
                missing_dataset_reason = str(exc)
                logger.warning(
                    "No Thread dataset available, commissioning proceeds without: %s", exc
                )
            else:
                try:
                    await active_client.set_thread_dataset(dataset)
                except MatterUnavailableError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc

        try:
            snapshot = await active_client.commission_with_code(request.code)
        except CommissioningError as exc:
            # 422: the request itself was well-formed, but the device
            # rejected commissioning (wrong code, already in another
            # ecosystem, timeout during the interview) - see
            # CommissioningError.
            raise HTTPException(
                status_code=422, detail=_commissioning_detail(exc, missing_dataset_reason)
            ) from exc
        except MatterUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        # The same sequence as in the CLI export (cli.py): register_device
        # before register_signals before register_commands, because both
        # need the freshly assigned device_id.
        device_id = store.register_device(snapshot, room=request.room)
        # Per its docstring, `register_device`'s `room` argument only takes
        # effect on a newly inserted row - an already known, active device
        # is caught there before the INSERT and simply keeps its previous
        # room, the selection from this request would be discarded without
        # comment (review finding, Finding 4). That is correct when
        # re-commissioning an unchanged, already known device WITHOUT a
        # chosen room - a re-commissioning must not silently clear a
        # maintained room. But if, as here, a room was explicitly selected
        # (the tile in the commissioning dialog offers it), that exact
        # choice should apply, whether the device was new or already
        # known - so it is additionally applied here via `set_room`
        # afterwards. `set_room`, not `rename_device`: the room ends up in
        # no export template, so re-commissioning with a chosen room must
        # not mark the device as "changed since" as a result.
        #
        # Deliberately UNCONDITIONAL, not only for the early-return case
        # (review finding, Finding 5): for a new device, `register_device`
        # has already set the room the same way through the INSERT row, so
        # the second write here is a no-op in that case (same
        # normalisation, `updated_at` remains untouched in both cases). A
        # case distinction "was the device new?" would need either a
        # return value from `register_device`, which its signature does
        # not provide today, or a second query before the call - the no-op
        # is the simpler and more robust choice.
        if request.room is not None:
            store.set_room(device_id, request.room)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))

        # The reachability of the new device MUST be seeded here, from
        # `snapshot.available` - exactly as `Runtime.seed_from_snapshot`
        # does for the already known devices when the bridge starts.
        #
        # The reason is an ordering that cannot be influenced from here
        # (recorded on 2026-09-04): matter-server reports `NODE_ADDED`
        # already WHILE `commission_with_code` is running
        # (`device_controller._setup_node` calls `signal_event(
        # EventType.NODE_ADDED, ...)` before the call even returns). At
        # that point, `register_device` above has not yet given the node a
        # device_id, and `BridgeMatterClient._dispatch_loop` accordingly
        # discards the notification ("update for unknown node ...
        # discarded") - the one opportunity at which `d<id>_online` would
        # have arisen by itself is thus gone before this route even gets
        # its turn again.
        #
        # For a device sitting quietly on the network, no further
        # `NODE_ADDED`/`NODE_UPDATED` notification follows after that, and
        # `_device_out` reads a missing key as `False`. The device
        # therefore showed as "offline" after commissioning and stayed
        # that way until the bridge's next restart - even though
        # matter-server had long since interviewed it and built a
        # subscription for it.
        #
        # From here on only follow-up work runs, and follow-up work must
        # not retroactively cancel the process: BEFORE `register_device`,
        # an error is a cancellation - the device is then not commissioned,
        # and an error message is the right response. AFTER it, the device
        # is in the fabric AND in the store, and an error message would
        # simply be wrong. It would lead into a dead end: the UI would
        # show "commissioning failed" and no device tile, the operator
        # would press "commission" again, and the printed code would
        # already be used up (422). The failure therefore belongs in the
        # log, not in the response. Concretely reachable via
        # `UdpSender.send` -> `socket.sendto`, which throws `OSError` when
        # the Miniserver's network is briefly down - hence `Exception` and
        # not just a single type.
        try:
            await runtime.set_online(device_id, snapshot.available)
        except Exception:
            logger.exception(
                "Could not seed reachability of freshly commissioned device %s - the "
                "device is commissioned, but its tile shows offline until the next "
                "notification from matter-server",
                device_id,
            )

        # Only now, after `register_device`: `follow_node` resolves the
        # node ID via the store, and before that there would be nothing to
        # resolve there - the same race that `NODE_ADDED` already lost
        # (see the comment above and the docstring of `follow_node`).
        # Creates the attribute subscriptions for this device and seeds
        # its values, so the signals show numbers immediately instead of
        # dashes - previously this required a restart of the bridge.
        #
        # `seed_even_without_new_paths`, because the subscriptions are, as
        # a rule, already in place by this point: the same `NODE_ADDED`
        # run that lost the reachability above has already had
        # `BridgeMatterClient`'s dispatch loop subscribe to every path of
        # this node - just without a device_id, i.e. without seeding.
        # Without the flag, this call here would find an empty diff and
        # turn back before seeding; the initial values would then never
        # arrive, and a static path (voltage with no load, battery level,
        # the off state of a plug) would remain a dash, because
        # matter-server suppresses unchanged values.
        #
        # Also follow-up work, also safeguarded (see above): the most
        # likely scenario is a matter-server that restarts immediately
        # after commissioning - then `follow_node` runs into
        # `_require_upstream` and throws `MatterUnavailableError`, even
        # though the device is fully commissioned. Without values, but
        # commissioned: the signal rows exist (they are created by
        # `register_signals` above).
        #
        # That they also fill in again is NOT carried by the next
        # `NODE_ADDED`/`NODE_UPDATED` alone - its diff is empty for a
        # device that has long been subscribed, and without the flag the
        # call would not even get to the seeding. It is carried by
        # `_seed_pending` in `BridgeMatterClient`: the bridge remembers
        # every node it still owes a snapshot - whether because the store
        # did not know it yet, or because the handler threw during
        # seeding -, and the next `follow_node` from the dispatch loop
        # catches up on it. That is why the assurance here holds for BOTH
        # cases: a failure before subscribing as well as one after (say, a
        # `sqlite3.OperationalError` under concurrent write load from the
        # resend loop).
        try:
            await active_client.follow_node(  # TRANSITIONAL (Task 5)
                int(snapshot.address), seed_even_without_new_paths=True
            )
        except Exception:
            logger.exception(
                "Could not catch up on subscriptions of freshly commissioned device %s "
                "- the device is commissioned, but its signals remain without values "
                "until the next notification from matter-server",
                device_id,
            )
        return _device_out(store.device(device_id), store, runtime)

    @router.delete("/devices/{device_id}", status_code=204)
    async def remove_device(device_id: int) -> None:
        device = _require_device(device_id)
        active_client = _require_client()
        try:
            # Order: see module docstring - the fabric first, then the store.
            await active_client.remove_node(int(device.address))  # TRANSITIONAL (Task 5)
        except MatterUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.forget_device(device.id)

    return router
