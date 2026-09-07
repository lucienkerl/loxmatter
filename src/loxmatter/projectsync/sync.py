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

from loxmatter.model.store import Store, StoredCommand, StoredSignal
from loxmatter.projectsync.diff import SyncPlan, build_plan
from loxmatter.projectsync.index import ProjectFormatError, build_index
from loxmatter.projectsync.patch import MissingCaptionError, apply_plan

__all__ = ["ProjectFormatError", "ProjectSyncResult", "run_sync"]


@dataclass(frozen=True)
class ProjectSyncResult:
    plan: SyncPlan
    patched_conservative: bytes
    # `None` if the experimental variant could not be built for this file
    # - then `new_devices_unavailable_reason` carries the reason (and
    # conversely: if the variant is present, the reason is `None`).
    patched_with_new_devices: bytes | None
    new_devices_unavailable_reason: str | None


def run_sync(
    raw: bytes,
    store: Store,
    *,
    bridge_ip: str,
    port: int,
    listen: int,
    miniserver_ip: str | None = None,
) -> ProjectSyncResult:
    """`miniserver_ip` selects the `LoxLIVE` block (= Miniserver) to compare
    against, if the project file has several configured (see
    `index.build_index`/`index.AmbiguousMiniserverError`) - with exactly
    one Miniserver in the file it remains optional."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # A wrong file (image, ZIP, UTF-16 export) must not deliver a bare
        # UnicodeDecodeError here - at the endpoint that would be an HTTP
        # 500 instead of a comprehensible message (design section 8).
        raise ProjectFormatError(
            "Die hochgeladene Datei ist keine gueltige UTF-8-Textdatei - eine "
            "Loxone-Projektdatei wird als UTF-8 gespeichert."
        ) from exc
    # `AmbiguousMiniserverError` (subclass of `ProjectFormatError`) is
    # deliberately left unhandled here and propagates up to
    # `api.project_sync`. There it is caught SPECIFICALLY (not just via
    # the generic `except ProjectFormatError` -> HTTP 400): if it carries
    # `candidates` (several Miniservers found), the endpoint returns,
    # instead of an error, a 200 response with
    # `needs_miniserver_selection=True` for the selection field in the
    # WebUI (user request after the review) - only the "none configured at
    # all" case (empty `candidates`) remains a genuine 400. Without an
    # unambiguous Miniserver, there is, for NEITHER of the two variants
    # (conservative or experimental), any place to compare against at all
    # - unlike a missing caption (see below), that is not a limitation of
    # the experimental path alone.
    index = build_index(text, miniserver_ip)
    devices = store.devices()
    signals_by_device: dict[int, Sequence[StoredSignal]] = {
        device.id: store.signals(device.id) for device in devices
    }
    commands_by_device: dict[int, Sequence[StoredCommand]] = {
        device.id: store.commands(device.id) for device in devices
    }
    plan = build_plan(index, devices, signals_by_device, commands_by_device)
    # Without `try`: only `NEW_DEVICE` entries reach, with
    # `include_new_devices=True`, the code that needs a caption - so the
    # conservative variant cannot throw a `MissingCaptionError` at all.
    #
    # A `ProjectFormatError` from `_installation_suffix` (finding N1 from
    # the re-review: via `apply_plan` -> `_new_signal_edit`/
    # `_new_device_edit` -> `new_unique_id`, as soon as a `NEW_SIGNAL`
    # entry needs a new ID), by contrast, VERY MUCH CAN be thrown by this
    # conservative variant - `NEW_SIGNAL` is independent of
    # `include_new_devices`. Deliberately left unhandled: unlike a missing
    # caption (only a limitation of the experimental path), a
    # `ProjectFormatError` here means "this file's ID format cannot be
    # recognised at all" (design section 10) - a more fundamental problem
    # than a missing optional section, one that should rightly fail the
    # whole upload (`api.project_sync` already catches `ProjectFormatError`
    # into a comprehensible 400). For consistency, the same decision holds
    # further below for the experimental variant: the `except` block there
    # deliberately catches only `MissingCaptionError`, not
    # `ProjectFormatError`.
    conservative = apply_plan(
        index,
        plan,
        devices,
        signals_by_device,
        commands_by_device,
        include_new_devices=False,
        bridge_ip=bridge_ip,
        port=port,
        listen=listen,
    )
    with_new_devices: bytes | None
    reason: str | None
    try:
        with_new_devices = apply_plan(
            index,
            plan,
            devices,
            signals_by_device,
            commands_by_device,
            include_new_devices=True,
            bridge_ip=bridge_ip,
            port=port,
            listen=listen,
        )
        reason = None
    except MissingCaptionError as exc:
        # A missing caption is, per design section 8, a limitation of the
        # EXPERIMENTAL path, not a reason to fail the whole upload: the
        # plan and the conservative variant remain usable, only this one
        # variant is dropped - with a reason given, not silently.
        #
        # Deliberately ONLY `MissingCaptionError`, not `ProjectFormatError`:
        # a `ProjectFormatError` from `_installation_suffix` (see the
        # comment at the conservative-variant call above) is meant to
        # propagate up to `api.project_sync`'s `except ProjectFormatError`
        # -> HTTP 400, rather than merely disabling the experimental
        # variant here - the same file could already have triggered the
        # same error in the conservative variant, which also leaves it
        # unhandled there. Degraded behaviour only for this call would be
        # inconsistent with the one above.
        with_new_devices = None
        reason = str(exc)
    return ProjectSyncResult(plan, conservative, with_new_devices, reason)
