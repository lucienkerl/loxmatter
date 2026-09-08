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

Accepts an uploaded Loxone project file and returns a diff plan plus both
patched file variants in a single response - the same `Store` that
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
  remains a genuine 400.

`patch.MissingCaptionError` belongs to neither of the two: an otherwise
well-formed project that is merely missing the `VirtualInCaption` or
`VirtualOutCaption` section is, per design section 8, a limit of the
experimental path, not a reason to discard the whole response. `run_sync`
therefore catches it itself and returns `patched_with_new_devices=None`
plus `new_devices_unavailable_reason`; the plan and the conservative file
reach the user normally."""

from __future__ import annotations

import base64

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from loxmatter.api.models import ProjectSyncEntryOut, ProjectSyncMiniserverOut, ProjectSyncPlanOut
from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store
from loxmatter.projectsync.diff import SyncPlan
from loxmatter.projectsync.index import AmbiguousMiniserverError, ProjectFormatError
from loxmatter.projectsync.sync import run_sync


def _entries_out(plan: SyncPlan) -> list[ProjectSyncEntryOut]:
    return [
        ProjectSyncEntryOut(
            kind=entry.kind,
            device_id=entry.device_id,
            device_label=entry.device_label,
            key=entry.key,
            title=entry.title,
            status=entry.status.value,
            changes={name: [old, new] for name, (old, new) in entry.changes.items()},
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
            " `needs_miniserver_selection=True` with the found Miniservers to choose from.",
        ),
    ) -> ProjectSyncPlanOut:
        """Builds the diff plan and both patched file variants in memory -
        writes nothing to disk and marks no device as exported (unlike
        `/api/export/download`: an uploaded project file is not a
        downloaded template, see design section 4)."""
        raw = await file.read()
        try:
            result = run_sync(
                raw,
                store,
                bridge_ip=bridge_ip,
                port=port,
                listen=listen,
                miniserver_ip=miniserver_ip,
            )
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

        with_new_devices = result.patched_with_new_devices
        return ProjectSyncPlanOut(
            entries=_entries_out(result.plan),
            has_changes=result.plan.has_changes,
            patched_conservative_base64=base64.b64encode(result.patched_conservative).decode(
                "ascii"
            ),
            patched_with_new_devices_base64=(
                None
                if with_new_devices is None
                else base64.b64encode(with_new_devices).decode("ascii")
            ),
            new_devices_unavailable_reason=result.new_devices_unavailable_reason,
        )

    return router
