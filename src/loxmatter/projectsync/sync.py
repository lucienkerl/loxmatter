# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Bindet Parsen, Diff und Patch zu einem einzigen Aufruf zusammen - das, was
`api.project_sync` aufruft (Entwurf Abschnitt 4: ein Request, keine
Zwischenzustand auf dem Server)."""

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
    # `None`, wenn die experimentelle Variante fuer diese Datei nicht gebaut
    # werden konnte - dann traegt `new_devices_unavailable_reason` den Grund
    # (und umgekehrt: ist die Variante da, ist der Grund `None`).
    patched_with_new_devices: bytes | None
    new_devices_unavailable_reason: str | None


def run_sync(
    raw: bytes, store: Store, *, bridge_ip: str, port: int, listen: int
) -> ProjectSyncResult:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # Eine falsche Datei (Bild, ZIP, UTF-16-Export) darf hier keinen
        # nackten UnicodeDecodeError liefern - der waere im Endpunkt eine
        # HTTP 500 statt einer verstaendlichen Meldung (Entwurf Abschnitt 8).
        raise ProjectFormatError(
            "Die hochgeladene Datei ist keine gueltige UTF-8-Textdatei - eine "
            "Loxone-Projektdatei wird als UTF-8 gespeichert."
        ) from exc
    index = build_index(text)
    devices = store.devices()
    signals_by_device: dict[int, Sequence[StoredSignal]] = {
        device.id: store.signals(device.id) for device in devices
    }
    commands_by_device: dict[int, Sequence[StoredCommand]] = {
        device.id: store.commands(device.id) for device in devices
    }
    plan = build_plan(index, devices, signals_by_device, commands_by_device)
    # Ohne `try`: nur `NEW_DEVICE`-Eintraege erreichen mit
    # `include_new_devices=True` den Code, der eine Caption braucht - die
    # konservative Variante kann also gar keinen `MissingCaptionError` werfen.
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
        # Eine fehlende Caption ist laut Entwurf Abschnitt 8 eine Grenze des
        # EXPERIMENTELLEN Pfades, kein Grund, den ganzen Upload scheitern zu
        # lassen: Plan und konservative Variante bleiben nutzbar, nur diese
        # eine Variante entfaellt - mit Begruendung statt kommentarlos.
        with_new_devices = None
        reason = str(exc)
    return ProjectSyncResult(plan, conservative, with_new_devices, reason)
