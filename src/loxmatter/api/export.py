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

"""Export via the API (Spec 8, Task 5) - the same templates as `loxmatter
export` on the command line, from the same `Store`.

`build_export_router` builds an `APIRouter` with prefix `/api/export`,
wired into `loxone.server.build_app` alongside the routes from the
previous tasks of this phase.

**The same database as the command line - not one of its own.** This
module receives an already-opened `Store`, just like
`api.devices.build_device_router`; it never opens a connection to a file
itself anywhere. The actual guarantee therefore does not lie here but in
the wiring: `loxone.server.build_app` passes the same `store` that
`loxmatter run` opened at startup via `cli._resolve_store_path` on to ALL
routers - this one included. If the WebUI were instead wired up with its
own, second `Store` instance on a different path, it would assign a
second set of signal keys (Spec 6.2) for the same Matter device - a user
who exports once via CLI and once via WebUI would get two templates that
look like the same one but are wired differently.
`tests/api/test_export_api.py::test_api_export_writes_the_same_database_as_the_cli`
proves this end-to-end: the same database path, populated once via
`loxmatter export` and read once via this router, produces byte-identical
templates - not just the same `device_id`.

Because the router reads directly from `Store` (`store.signals`/
`store.commands`), not from a fresh Matter snapshot, it does not need
`export.commands` - the commands already exist as `StoredCommand` in the
database, created during commissioning (`api.devices.commission_device`)
or during the last `loxmatter export` run.

**Decision 1 - a download counts as an export.** `GET
/api/export/download` calls `Store.mark_exported` for every device
delivered, `GET /api/export/preview` never (see
`test_preview_does_not_write_anything`). A preview is unambiguously
without consequence; a downloaded ZIP, on the other hand, is the same
artifact that `loxmatter export` produces on the command line and that
unquestionably counts as "exported" there (`cli.py`'s `export` command
calls `mark_exported` for the same reason). The downside: a user who
downloads the same ZIP file twice without changing anything sees
`exported_at` jump forward both times, even though nothing changed. The
alternative - advancing `exported_at` only on an actual content change -
would need the same comparison that `changed_since_export` below already
performs anyway, and would amount to quietly turning a download into a
preview as soon as "nothing new" applies. A download that sometimes
counts and sometimes does not would be harder to explain than a timestamp
that harmlessly jumps forward on a consequence-free repeat download.

Exactly when `mark_exported` is invoked is not arbitrary here: `download`
only marks AFTER the ZIP has been fully built in memory - never device by
device during the build (review fix Important #1, 2026-09-02). An error
between two devices (a render that throws, a `store.commands`/
`store.signals` that fails, a `forget_device` from a concurrent request)
must not leave any device marked as exported whose template the client
never received for lack of a 500 response - see the docstring of
`download` below. The same consideration already stood behind the
deferred `mark_exported` call in `cli.py`'s `export` command.

**Decision 2 - the port comes from the request, not from the code.**
`download` requires `port` (UDP, VirtualInUdp) and `listen` (HTTP, the
command URLs in VirtualOut) as query parameters, with the same defaults
as `cli.py`'s `export --port`/`--listen`: `port` falls back to
`model.store.DEFAULT_UDP_PORT` (7000), `listen` to 8080 - both only
defaults, never hard-wired. A `loxmatter run --listen 9090` without a
matching `listen` value here would produce templates whose output
commands go nowhere, without the Miniserver ever reporting that (the same
fault that review fix I3 in `export.documents.render_system_templates`
already fixed once).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from loxmatter import i18n
from loxmatter.api.models import ExportDeviceOut, ExportPreviewOut, ExportStatusOut
from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_system_templates,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.outputs import to_group_outputs, to_outputs
from loxmatter.export.signals import to_inputs
from loxmatter.model.store import (
    DEFAULT_LISTEN_PORT,
    DEFAULT_UDP_PORT,
    Store,
    StoredCommand,
    StoredDevice,
    UnknownDeviceError,
)
from loxmatter.profiles.table import is_exportable

# Public, because the UI has to assign the same file name: ever since
# downloads run via `fetch` instead of a link, the browser names the file
# itself (see `web/app.js`, `download`).
ARCHIVE_NAME = "loxmatter-export.zip"
# Language-neutral (review fix Important, whole-branch review 2026-09-04):
# the file name stays ONE fixed value regardless of the UI language, only
# the content (`_readme_text()`) is translated. A file name that changes
# with the UI language would needlessly complicate every script that
# expects the ZIP structure - was always called `Import-Anleitung.txt`
# until this fix, even in the English-language export.
_README_NAME = "README.txt"


def _readme_text() -> str:
    """Like the old module constant `_README_TEXT`, but resolved fresh per
    call instead of frozen at module import - the same rationale as for
    removing `_ALREADY_SET_UP_DETAIL` in `api/auth.py` (Task 4)."""
    return i18n.t("api.export.readme_text").replace("\n", "\r\n")


def _loxone_commands(commands: Sequence[StoredCommand]) -> list[LoxoneCommand]:
    """Builds `LoxoneCommand`s from already-stored commands - the same
    composition as in `cli.py`'s `export` command, applied here to
    `StoredCommand` instead of `DeviceCommand`, because this router reads
    from the `Store` instead of from a fresh Matter snapshot (see module
    docstring)."""
    return to_outputs(commands)


def _device_preview(device: StoredDevice, store: Store) -> ExportDeviceOut:
    signals = store.signals(device.id)
    commands = store.commands(device.id)
    inputs = to_inputs(signals, device.id, device.label)
    # `is_exportable` instead of an inversion noted here (review fix
    # Fix 8, 2026-09-03): until then the rule "text, lists, structs and
    # null values produce no Loxone input" (Spec 6.6) stood as
    # `(Exportability.NONE, Exportability.TEXT)` both here and in
    # `cli.py` - two hand-copied inversions of exactly the helper that
    # already existed for this. Had the enum been extended with a future
    # `Exportability` value, CLI and API would have reported different
    # numbers of "skipped" signals without any test noticing.
    skipped = sum(1 for s in signals if not is_exportable(s.exportability))
    # hidden_count (Task 8): how many signals the UI hides in the
    # collapsed "expert" block - `StoredSignal.functional` comes
    # unchanged from `Store.register_signals`
    # (`profiles.relevance.is_functional`), no second computation here.
    hidden_count = sum(1 for s in signals if not s.functional)
    return ExportDeviceOut(
        device_id=device.id,
        label=device.label,
        viu_filename=filename_for("VIU", device.id, device.label),
        vo_filename=filename_for("VO", device.id, device.label),
        inputs=len(inputs),
        commands=len(commands),
        skipped=skipped,
        hidden_count=hidden_count,
    )


def _changed_since_export(device: StoredDevice) -> bool:
    """Whether the device has changed since its last export.

    An unknown `updated_at` (legacy database predating `_migrate_to_v2`,
    see there) counts as "changed" - the more cautious of the two possible
    assumptions, see `StoredDevice.updated_at`.

    A dedicated function, ever since `download?only_pending=true` had to
    get the same question answered as `GET /api/export/status` (review fix
    Fix 4, 2026-09-03). Two versions of this condition would be exactly
    the fault the UI previously had: the table showed one selection, the
    ZIP contained another."""
    return (
        device.exported_at is None
        or device.updated_at is None
        or device.updated_at > device.exported_at
    )


def _status_for(device: StoredDevice) -> ExportStatusOut:
    changed = _changed_since_export(device)
    return ExportStatusOut(
        device_id=device.id,
        label=device.label,
        exported_at=device.exported_at,
        changed_since_export=changed,
    )


def build_export_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/export")

    @router.get("/preview")
    async def preview(
        bridge_ip: str = Query(
            ...,
            description="IP of the bridge, as seen by the Miniserver - same as for /download,"
            " so the same request can be checked against 422 up front.",
        ),
        system: bool = Query(
            False, description="Also count the device-independent system templates."
        ),
    ) -> ExportPreviewOut:
        """What a download would produce - without writing anything.

        Never calls `Store.mark_exported` (see module docstring,
        decision 1), and otherwise changes no row either. `bridge_ip`
        itself does not feed into any of the numbers below - it only
        counts so that a missing value here shows up as a 422 just as it
        does later for `/download`, instead of only after clicking
        "Download". It therefore deliberately does not appear in any
        field of `ExportPreviewOut`, even though it is a required
        parameter."""
        devices = [_device_preview(device, store) for device in store.devices()]
        system_files = ["VIU_Matter_System.xml", "VO_Matter_System.xml"] if system else []
        return ExportPreviewOut(devices=devices, system_files=system_files)

    @router.get("/download")
    async def download(
        bridge_ip: str = Query(..., description="IP of the bridge, as seen by the Miniserver"),
        port: int = Query(DEFAULT_UDP_PORT, description="UDP port the Miniserver listens on"),
        listen: int = Query(
            DEFAULT_LISTEN_PORT,
            description="HTTP port in the generated command URL (VO template) - must match"
            " the --listen of `loxmatter run` (see module docstring, decision 2).",
        ),
        system: bool = Query(
            False, description="Also include the device-independent system templates."
        ),
        only_pending: bool = Query(
            False,
            description="Only devices that have changed since their last export (the same"
            " condition as `changed_since_export` in /status). The rest go neither into the"
            " archive nor get a new `exported_at`. Ignored if `device_id` is set.",
        ),
        device_id: int | None = Query(
            None,
            description="Export only this one device (device dashboard design,"
            " 2026-09-03, section 6, export button on the device card) - ignores"
            " `only_pending`. 404 if the device no longer exists.",
        ),
    ) -> Response:
        """Builds the ZIP in memory - no temporary file, no intermediate
        state on disk.

        Marks every device delivered as exported via `Store.mark_exported`
        (decision 1 in the module docstring) - but ONLY AFTER the archive
        has been fully built, not device by device during the build
        (review fix Important #1, 2026-09-02). Had an error occurred
        between two devices - a render that throws, a
        `store.commands`/`store.signals` that fails, a `forget_device`
        from a concurrent request -, FastAPI would have responded with
        500 and the client would have received no ZIP, while every device
        processed up to that point would nonetheless have been
        permanently marked as exported: `GET /api/export/status` would
        then have reported it as "unchanged since" even though no one
        ever received the associated template. The same discipline as in
        `cli.py`'s `export` command, which for exactly this reason
        executes its `Store.mark_exported` call only after both
        successful `write_bytes` calls. The short guide is ALWAYS
        included, regardless of `system` and even when not a single
        device is registered - an empty installation thus delivers a
        valid, non-empty ZIP instead of an empty archive or a server
        error.

        **`only_pending` (review fix Fix 4, 2026-09-03).** The UI's "only
        devices not yet exported" filter previously applied only to the
        preview table; this endpoint did not know it at all and always
        delivered all devices - and also marked all of them as exported.
        Anyone who filtered, saw a pending device, and downloaded it thus
        got the full archive and a filter that then stayed permanently
        empty. Now the same parameter decides both. Because
        `mark_exported` below only runs over `exported_device_ids` - i.e.
        over the devices actually written -, a skipped device also gets
        no new timestamp and correctly remains pending in `GET
        /api/export/status`. The system templates still depend solely on
        `system`: they belong to no device and can therefore not be
        "unchanged since the last export" either.

        **`device_id` (device dashboard design, 2026-09-03, section 6).**
        When set, the selection is restricted to exactly this one device,
        independent of `only_pending` - the export button on a device
        card never asks whether the device is "pending", it exports the
        one device that is currently visible. An unknown or removed
        device yields 404, checked BEFORE the archive is built."""
        if device_id is not None:
            try:
                store.device(device_id)
            except UnknownDeviceError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        buffer = io.BytesIO()
        # Collected instead of recorded immediately (see above) - only
        # processed below after the archive has been fully built.
        exported_device_ids: list[int] = []
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            if system:
                viu_system, vo_system = render_system_templates(bridge_ip, port, listen)
                archive.writestr("VIU_Matter_System.xml", viu_system)
                archive.writestr("VO_Matter_System.xml", vo_system)

            for device in store.devices():
                if device_id is not None:
                    if device.id != device_id:
                        continue
                elif only_pending and not _changed_since_export(device):
                    continue
                signals = store.signals(device.id)
                commands = _loxone_commands(store.commands(device.id))
                inputs = to_inputs(signals, device.id, device.label)

                archive.writestr(
                    filename_for("VIU", device.id, device.label),
                    render_virtual_in_udp(device.label, bridge_ip, port, inputs),
                )
                # Without output commands the VO template would be empty
                # apart from its basic skeleton - an import into Loxone
                # Config would bring nothing but an empty template in the
                # tree. The online signal, on the other hand, never lets
                # the VIU template be empty (see `to_inputs`), so it is
                # always produced.
                if commands:
                    archive.writestr(
                        filename_for("VO", device.id, device.label),
                        render_virtual_out(device.label, f"http://{bridge_ip}:{listen}", commands),
                    )
                exported_device_ids.append(device.id)

            # Unlike the device loop above, this one is not narrowed by
            # `device_id` (or `only_pending`): the data model has no
            # device-owns-group relationship, so there is nothing to
            # filter a group by when only one device is requested -
            # inventing such a link here would be a new rule, not a
            # missing filter. A single-device download therefore always
            # bundles every group's template alongside it, deliberately -
            # the same way `cli.py`'s `export` command always writes every
            # group's template alongside the one device it exports.
            for group in store.groups():
                group_commands = to_group_outputs(store.group_commands(group.id))
                if not group_commands:
                    # An emptied group has no outputs to offer. It keeps
                    # existing (design 4.3); it just has nothing to export.
                    continue
                archive.writestr(
                    filename_for("VO", group.id, group.label, kind="g"),
                    render_virtual_out(
                        group.label,
                        f"http://{bridge_ip}:{listen}",
                        group_commands,
                        is_group=True,
                    ),
                )

            archive.writestr(_README_NAME, _readme_text())

        # The archive is complete at this point - only now does the
        # export count (see docstring above). An error further up would
        # never have reached this line, and none of the devices processed
        # up to that point would be wrongly marked as exported.
        for device_id_written in exported_device_ids:
            store.mark_exported(device_id_written)

        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{ARCHIVE_NAME}"'},
        )

    @router.get("/status")
    async def status() -> list[ExportStatusOut]:
        """Per active device: when last exported, changed since (see
        `_status_for`). A removed device (`forget_device`) does not
        appear here - `store.devices()` already filters it out, the same
        rule as for `GET /api/devices`."""
        return [_status_for(device) for device in store.devices()]

    return router
