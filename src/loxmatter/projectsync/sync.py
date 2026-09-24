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

"""Ties parsing, diffing and patching together into a single call - what
`api.project_sync` invokes (design section 4: one request, no
intermediate state on the server)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from loxmatter import i18n
from loxmatter.model.store import Store, StoredCommand, StoredGroupCommand, StoredSignal
from loxmatter.projectsync.diff import SyncPlan, build_plan
from loxmatter.projectsync.index import ProjectFormatError, build_index
from loxmatter.projectsync.patch import apply_plan
from loxmatter.projectsync.savedate import recorded_utc_offset

__all__ = ["ProjectFormatError", "ProjectSyncResult", "run_sync"]


@dataclass(frozen=True)
class ProjectSyncResult:
    plan: SyncPlan
    patched: bytes


def run_sync(
    raw: bytes,
    store: Store,
    *,
    bridge_ip: str,
    port: int,
    listen: int,
    miniserver_ip: str | None = None,
    utc_offset: timedelta | None = None,
    now: datetime | None = None,
) -> ProjectSyncResult:
    """`miniserver_ip` selects the `LoxLIVE` block (= Miniserver) to compare
    against, if the project file has several configured (see
    `index.build_index`/`index.AmbiguousMiniserverError`) - with exactly
    one Miniserver in the file it remains optional.

    `utc_offset` is the user's, for the wall-clock half of the "last
    saved" stamp (`projectsync.savedate`). The bridge's own clock zone is
    no substitute: in a container it is usually UTC. Without one, the
    offset the file itself last recorded stands in - right unless the
    clocks changed in between - and UTC only when the file records none.
    `now` exists for tests."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # A wrong file (image, ZIP, UTF-16 export) must not deliver a bare
        # UnicodeDecodeError here - at the endpoint that would be an HTTP
        # 500 instead of a comprehensible message (design section 8).
        raise ProjectFormatError(i18n.t("projectsync.not_utf8")) from exc
    # `AmbiguousMiniserverError` (subclass of `ProjectFormatError`) is
    # deliberately left unhandled here and propagates up to
    # `api.project_sync`. There it is caught SPECIFICALLY (not just via
    # the generic `except ProjectFormatError` -> HTTP 400): if it carries
    # `candidates` (several Miniservers found), the endpoint returns,
    # instead of an error, a 200 response with
    # `needs_miniserver_selection=True` for the selection field in the
    # WebUI (user request after the review) - only the "none configured at
    # all" case (empty `candidates`) remains a genuine 400. Without an
    # unambiguous Miniserver, there is no place to compare against at all.
    index = build_index(text, miniserver_ip)
    devices = store.devices()
    signals_by_device: dict[int, Sequence[StoredSignal]] = {
        device.id: store.signals(device.id) for device in devices
    }
    commands_by_device: dict[int, Sequence[StoredCommand]] = {
        device.id: store.commands(device.id) for device in devices
    }
    groups = store.groups()
    commands_by_group: dict[int, Sequence[StoredGroupCommand]] = {
        group.id: store.group_commands(group.id) for group in groups
    }
    plan = build_plan(
        index,
        devices,
        signals_by_device,
        commands_by_device,
        groups=groups,
        commands_by_group=commands_by_group,
    )
    # A `ProjectFormatError` from `_installation_suffix` (finding N1 from
    # the re-review: via `apply_plan` -> `_new_signal_edit`/
    # `_new_device_edit` -> `new_unique_id`, as soon as an entry needs a
    # new ID) is deliberately left unhandled: it means "this file's ID
    # format cannot be recognised at all" (design section 10), which should
    # rightly fail the whole upload (`api.project_sync` already catches
    # `ProjectFormatError` into a comprehensible 400).
    if utc_offset is None:
        recorded = None if index.document is None else recorded_utc_offset(index.document.attrs)
        utc_offset = recorded if recorded is not None else timedelta(0)
    saved_at = (now or datetime.now(UTC)).astimezone(timezone(utc_offset))
    patched = apply_plan(
        index,
        plan,
        devices,
        signals_by_device,
        commands_by_device,
        groups=groups,
        commands_by_group=commands_by_group,
        bridge_ip=bridge_ip,
        port=port,
        listen=listen,
        saved_at=saved_at,
    )
    return ProjectSyncResult(plan, patched)
