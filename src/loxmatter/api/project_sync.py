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

"""`POST /api/export/project-sync` (design `docs/superpowers/specs/
2026-09-03-project-file-sync-design.md`, section 7).

Accepts an uploaded Loxone project file and returns a diff plan plus the
patched file in a single response - the same `Store` that
`api.export` and `api.devices` also receive (see their module docstrings
for the rationale: a second, independently opened store would assign a
second set of signal keys for the same device).

**Two error paths, handled differently:**

- `ProjectFormatError` - the uploaded file is not a valid/recognisable
  Loxone project (no `ControlList`, an unclosed tag, not a UTF-8 text
  file) - becomes a comprehensible 400 with the message the exception
  already carries. No server error, no bare 500.
- `AmbiguousMiniserverError` (subclass of `ProjectFormatError`) - the file
  configures more than one Miniserver and none was selected. If it
  carries `candidates` (at least one Miniserver found), the endpoint
  returns, INSTEAD of a 400, a normal 200 response with
  `needs_miniserver_selection=True` and `available_miniservers` - the
  WebUI then shows a selection field instead of an error (user request
  after the review: select instead of typing the IP by hand). Only the
  "none configured at all" case (empty `candidates`, nothing to select)
  remains a genuine 400."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from loxmatter import i18n
from loxmatter.api.models import (
    ProjectSyncEntryOut,
    ProjectSyncLinkOut,
    ProjectSyncMiniserverOut,
    ProjectSyncNameDifferenceOut,
    ProjectSyncOrphanOut,
    ProjectSyncPlanOut,
    ProjectSyncRoomDifferenceOut,
    ProjectSyncRoomOut,
    ProjectSyncSplitRoomsOut,
)
from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store
from loxmatter.projectsync.diff import PlanEntry, SyncPlan
from loxmatter.projectsync.index import AmbiguousMiniserverError, ProjectFormatError
from loxmatter.projectsync.reconcile import Reconciliation, Side, SyncDecisions
from loxmatter.projectsync.sync import ProjectSyncResult, apply_decisions_to_store, run_sync

_SIDES: tuple[Side, ...] = ("loxone", "loxmatter")


def _parse_decisions(raw: str | None) -> SyncDecisions:
    """`{"rooms": {"device:5": "loxone"}, "names": {...}, "delete": ["d8"]}`
    - an unknown side or a wrong shape is the client's mistake, a 400."""
    if not raw:
        return SyncDecisions()
    try:
        data: Any = json.loads(raw)
        rooms = {str(k): v for k, v in dict(data.get("rooms", {})).items()}
        names = {str(k): v for k, v in dict(data.get("names", {})).items()}
        delete = frozenset(str(prefix) for prefix in data.get("delete", []))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=400, detail=i18n.t("api.errors.projectsync_bad_decisions")
        ) from exc
    if any(v not in _SIDES for v in [*rooms.values(), *names.values()]):
        raise HTTPException(status_code=400, detail=i18n.t("api.errors.projectsync_bad_decisions"))
    return SyncDecisions(rooms=rooms, names=names, delete=delete)


def _reconciliation_out(reconciliation: Reconciliation) -> dict[str, Any]:
    return {
        "room_differences": [
            ProjectSyncRoomDifferenceOut(
                owner=d.owner.ref,
                label=d.owner.label,
                loxone=d.loxone,
                loxmatter=d.owner.room,
                preselect=d.preselect,
            )
            for d in reconciliation.rooms
        ],
        "name_differences": [
            ProjectSyncNameDifferenceOut(
                owner=n.owner.ref, loxone=n.loxone, loxmatter=n.owner.label, preselect=n.preselect
            )
            for n in reconciliation.names
        ],
        "split_rooms": [
            ProjectSyncSplitRoomsOut(
                owner=s.owner.ref, label=s.owner.label, loxmatter=s.owner.room, rooms=s.rooms
            )
            for s in reconciliation.split
        ],
        "orphans": [
            ProjectSyncOrphanOut(
                prefix=o.prefix,
                label=o.label,
                reason=o.reason,
                group_labels=o.group_labels,
                titles=[e.title for e in o.entries],
                links=[ProjectSyncLinkOut(title=link.title, page=link.page) for link in o.links],
                delete_by_default=o.delete_by_default,
            )
            for o in reconciliation.orphans
        ],
    }


def _target_room(plan: SyncPlan, entry: PlanEntry) -> str | None:
    assignment = plan.room_for(entry)
    if assignment is None or not assignment.targets_a_room:
        return None
    return assignment.loxone_title


def _entries_out(plan: SyncPlan) -> list[ProjectSyncEntryOut]:
    return [
        ProjectSyncEntryOut(
            kind=entry.kind,
            device_id=entry.device_id,
            device_label=entry.device_label,
            # A group and a device can share the same numeric id (design
            # 2026-09-10, section 8) - dropping this would let the WebUI's
            # per-owner grouping (keyed on device_id alone) merge a
            # group's entries into the same-numbered device's card. See
            # `ProjectSyncEntryOut.owner_kind`.
            owner_kind=entry.owner_kind,
            key=entry.key,
            title=entry.title,
            status=entry.status.value,
            changes={name: [old, new] for name, (old, new) in entry.changes.items()},
            target_room=_target_room(plan, entry),
        )
        for entry in plan.entries
    ]


def build_project_sync_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/export")

    @router.post("/project-sync")
    async def project_sync(
        file: UploadFile = File(..., description="The uploaded .Loxone project file"),
        bridge_ip: str = Query(..., description="IP of the bridge, as seen by the Miniserver"),
        port: int = Query(DEFAULT_UDP_PORT, description="UDP port the Miniserver listens on"),
        listen: int = Query(
            DEFAULT_LISTEN_PORT,
            description="HTTP port in the command URLs of new outputs - must match the"
            " --listen of `loxmatter run`, as for /api/export/download.",
        ),
        miniserver_ip: str | None = Query(
            None,
            description="Internal IP of the Miniserver (`LoxLIVE.IntAddr` in the project"
            " file, the same IP as for `loxmatter run --miniserver`). Only needed if the"
            " uploaded file configures more than one Miniserver and none has been"
            " selected yet - in that case the response instead carries"
            " `needs_miniserver_selection=True` with the found Miniservers to choose from."
            " Without it, the address saved under Settings → Miniserver connection is used"
            " when the file offers it.",
        ),
        utc_offset: int | None = Query(
            None,
            ge=-14 * 60,
            le=14 * 60,
            description="The user's UTC offset in minutes (east positive, +120 for"
            " CEST). The patched file's `Date` - the wall-clock half of its"
            ' "last saved" stamp - is written in it. Without it, the offset the file'
            " last recorded is used.",
        ),
        decisions: str | None = Form(
            None,
            description='The user\'s answers as JSON: `{"rooms": {"device:5": "loxone"},'
            ' "names": {"group:2": "loxmatter"}, "delete": ["d8"]}`. An owner left out'
            " is skipped. Without it, nothing is decided and nothing deleted.",
        ),
        apply: bool = Query(
            False,
            description="Also store the decisions in loxmatter (Loxone's rooms and names"
            " where the user picked Loxone) and record the sync's baseline - sent with"
            " the download, never with the first look at a file.",
        ),
    ) -> ProjectSyncPlanOut:
        """Builds the diff plan and the patched file in memory -
        writes nothing to disk and marks no device as exported (unlike
        `/api/export/download`: an uploaded project file is not a
        downloaded template, see design section 4)."""
        raw = await file.read()
        chosen = _parse_decisions(decisions)
        offset = None if utc_offset is None else timedelta(minutes=utc_offset)

        def sync(ip: str | None) -> ProjectSyncResult:
            return run_sync(
                raw,
                store,
                bridge_ip=bridge_ip,
                port=port,
                listen=listen,
                miniserver_ip=ip,
                utc_offset=offset,
                decisions=chosen,
            )

        try:
            try:
                result = sync(miniserver_ip)
            except AmbiguousMiniserverError as exc:
                # The address in Settings says which Miniserver this bridge
                # talks to (design 2026-09-25, section 9) - used only where
                # the file leaves the choice open and names that address.
                stored = store.settings.get().miniserver_ip
                offered = {c.int_addr for c in exc.candidates}
                if miniserver_ip is not None or stored is None or stored not in offered:
                    raise
                result = sync(stored)
        except AmbiguousMiniserverError as exc:
            if not exc.candidates:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return ProjectSyncPlanOut(
                needs_miniserver_selection=True,
                available_miniservers=[
                    ProjectSyncMiniserverOut(title=c.title, int_addr=c.int_addr)
                    for c in exc.candidates
                ],
            )
        except ProjectFormatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if apply:
            apply_decisions_to_store(store, result)
        return ProjectSyncPlanOut(
            entries=_entries_out(result.plan),
            rooms=[
                ProjectSyncRoomOut(name=a.name, status=a.status.value, loxone_title=a.loxone_title)
                for a in result.plan.rooms
            ],
            has_changes=result.plan.has_changes,
            patched_base64=base64.b64encode(result.patched).decode("ascii"),
            applied=apply,
            **_reconciliation_out(result.reconciliation),
        )

    return router
