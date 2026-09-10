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

"""Creating, changing and inspecting groups (design 2026-09-10).

A group is a named sender without a node of its own: several devices of
the same category behind one Loxone virtual output. It has no signals, no
live values and no online state - those are properties of a node, and
inventing an aggregate for six lamps with six brightnesses is exactly the
quiet fiction Spec 8.1 exists to prevent.

**There is no control route here.** A group is driven through `POST
/api/commands/{key}` like a device, because group and device command keys
share one namespace - see `api/control.py`. Only the *reading* half, the
controls route, lives here, because its answer differs: value ranges are
intersected across the members and the initial values carry an
attribution.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from loxmatter.api.control import ValueReader
from loxmatter.api.models import (
    CommandOut,
    ControlRange,
    GroupControlsOut,
    GroupIn,
    GroupMembersIn,
    GroupOut,
    GroupPatch,
)
from loxmatter.model.store import (
    CategoryMismatchError,
    Store,
    StoredGroup,
    UnknownDeviceError,
    UnknownGroupError,
)
from loxmatter.profiles.table import command_control, command_slug

# ColorTempPhysicalMinMireds / ColorTempPhysicalMaxMireds, the same pair
# `api/control.py` reads for a single device.
_CLUSTER_COLOR = 768
_ATTR_CT_PHYS_MIN_MIREDS = 16395
_ATTR_CT_PHYS_MAX_MIREDS = 16396


def build_groups_router(store: Store, values: ValueReader) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _out(group: StoredGroup) -> GroupOut:
        members = store.group_members(group.id)
        return GroupOut(
            id=group.id,
            label=group.label,
            room=group.room,
            category=group.category,
            member_ids=[device.id for device in members],
            member_labels=[device.label for device in members],
            command_count=len(store.group_commands(group.id)),
        )

    def _require(group_id: int) -> StoredGroup:
        try:
            return store.group(group_id)
        except UnknownGroupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _kelvin_range(group_id: int) -> ControlRange | None:
        """The members' color-temperature ranges, intersected.

        Maximum of the minima, minimum of the maxima: a slider offering a
        span that half the group silently clamps is the same silent
        failure the lamp-controls design removed for a single device.

        An empty intersection (two members whose ranges do not overlap at
        all, or a member that has not reported its limits yet) yields
        `None`; the caller then falls back to a plain number field
        instead of a slider spanning a range no value satisfies. The
        command itself stays in the group and stays exported - `/cmd`
        with an explicit value still reaches every member that accepts it.

        **Difference from the device path (`api/control.py`,
        `_kelvin_range`): no endpoint filter.** The device version
        restricts to `signal.ref.endpoint == endpoint` because it is
        answering for one specific command on one specific endpoint. A
        group command carries no endpoint at all (design 4.1) - a group
        command is one `(cluster_id, command_id)` pair shared across
        members, dispatched per member without regard to which endpoint
        carries it (see `Store.group_targets`). So this function looks at
        every ColorControl signal of the member, on any endpoint. For a
        member that happens to carry ColorControl on more than one
        endpoint, `keys` below is keyed by `element_id`, so the second
        endpoint's signal for the same attribute overwrites the first
        outright - endpoints are never combined. `_signal_order` sorts
        signals by `(rank, endpoint, cluster_id, ...)`, so the
        highest-numbered endpoint's complete min+max pair is the one that
        wins, and every earlier endpoint's values are simply dropped.
        There is no way from a bare group command to prefer one endpoint
        over another, and the design does not ask for one.
        """
        wanted = (_ATTR_CT_PHYS_MIN_MIREDS, _ATTR_CT_PHYS_MAX_MIREDS)
        lows: list[int] = []
        highs: list[int] = []
        for device in store.group_members(group_id):
            keys = {
                signal.ref.element_id: signal.key
                for signal in store.signals(device.id)
                if signal.ref.cluster_id == _CLUSTER_COLOR and signal.ref.element_id in wanted
            }
            current = values.last_values_for(device.id)
            mireds: list[float] = []
            for element_id in wanted:
                key = keys.get(element_id)
                value = current.get(key) if key is not None else None
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    mireds = []
                    break
                mireds.append(float(value))
            if not mireds:
                continue
            kelvins = sorted(int(1_000_000 / mired) for mired in mireds)
            lows.append(kelvins[0])
            highs.append(kelvins[1])

        if not lows:
            return None
        low, high = max(lows), min(highs)
        if low >= high:
            return None
        return ControlRange(min=low, max=high)

    @router.get("/groups")
    async def list_groups() -> list[GroupOut]:
        return [_out(group) for group in store.groups()]

    @router.post("/groups", status_code=201)
    async def create_group(body: GroupIn) -> GroupOut:
        try:
            group = store.create_group(body.label, body.member_ids, body.room)
        except (CategoryMismatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _out(group)

    @router.get("/groups/{group_id}")
    async def read_group(group_id: int) -> GroupOut:
        return _out(_require(group_id))

    @router.patch("/groups/{group_id}")
    async def patch_group(group_id: int, body: GroupPatch) -> GroupOut:
        _require(group_id)
        if body.label is not None:
            store.rename_group(group_id, body.label)
        if body.room is not None:
            store.set_group_room(group_id, body.room)
        return _out(store.group(group_id))

    @router.put("/groups/{group_id}/members")
    async def replace_members(group_id: int, body: GroupMembersIn) -> GroupOut:
        _require(group_id)
        try:
            store.set_group_members(group_id, body.member_ids)
        except (CategoryMismatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _out(store.group(group_id))

    @router.delete("/groups/{group_id}", status_code=204)
    async def delete_group(group_id: int) -> None:
        _require(group_id)
        store.delete_group(group_id)

    @router.get("/groups/{group_id}/controls")
    async def group_controls(group_id: int) -> GroupControlsOut:
        _require(group_id)
        stored = store.group_commands(group_id)
        members = store.group_members(group_id)
        seed = members[0] if members else None
        named: list[CommandOut] = []
        for command in stored:
            if command_slug(command.cluster_id, command.command_id) is None:
                continue
            control = command_control(command.cluster_id, command.command_id)
            control_range = _kelvin_range(group_id) if control == "kelvin" else None
            if control == "kelvin" and control_range is None:
                # Deliberately different from the device path
                # (`api/control.py`), which keeps `control == "kelvin"`
                # with `range=None` there: for a device, an unreadable
                # range is MISSING DATA - the device may well have one
                # common value once it reports it, and a slider that
                # cannot yet be built is not a reason to change what kind
                # of control it is. For a group, an empty intersection is
                # a KNOWN INCOMPATIBILITY between members' ranges (or one
                # member that has never reported), and `"unknown"` is
                # exactly its documented meaning (`command_control`'s
                # docstring): no guessed range, so the UI falls back to a
                # plain number field. A number field is the honest control
                # here - the user types a value and each lamp clamps it,
                # which beats a slider spanning a span no value satisfies.
                # `control` is also typed `str` on `CommandOut`, never
                # `None`, so this could not be `None` regardless.
                control = "unknown"
            named.append(
                CommandOut(
                    key=command.key,
                    slug=command.slug,
                    takes_value=command.takes_value,
                    control=control,
                    range=control_range,
                )
            )
        return GroupControlsOut(
            commands=named,
            hidden_raw_commands=len(stored) - len(named),
            seed_device_id=seed.id if seed is not None else None,
            seed_device_label=seed.label if seed is not None else None,
        )

    return router
