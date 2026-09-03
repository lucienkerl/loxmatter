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

"""`POST /api/export/project-sync` (Entwurf `docs/superpowers/specs/
2026-09-03-projektdatei-sync-design.md`, Abschnitt 7).

Nimmt eine hochgeladene Loxone-Projektdatei entgegen und liefert Diff-Plan
plus beide gepatchten Datei-Varianten in einer Antwort - derselbe `Store`,
den auch `api.export` und `api.devices` bekommen (siehe deren
Moduldocstrings zur Begruendung: ein zweiter, unabhaengig geoeffneter Store
vergaebe fuer dasselbe Geraet einen zweiten Satz Signalschluessel).

**Zwei Fehlerpfade, beide als 400.** `run_sync` (Task 10) baut in einem
Aufruf IMMER beide Varianten - auch `include_new_devices=True` -, unabhaengig
davon, ob eine spaetere Oberflaeche diese Variante ueberhaupt anzeigt. Neben
`ProjectFormatError` (die Datei ist kein gueltiges/erkennbares Loxone-Projekt)
kann darum auch `patch.MissingCaptionError` bis hierher durchschlagen: ein
ansonsten wohlgeformtes Projekt, dem schlicht der `VirtualInCaption`- bzw.
`VirtualOutCaption`-Abschnitt fehlt, den ein komplett neu anzulegendes Geraet
dieser Art braeuchte. Anders als `ProjectFormatError` ist das kein Format-,
sondern ein Inhaltsproblem der hochgeladenen Datei - beide sind aus Sicht
dieses Endpunkts aber gleich zu behandeln: kein Serverfehler, sondern eine
verstaendliche 400 mit der deutschen Fehlermeldung, die die jeweilige
Exception schon traegt."""

from __future__ import annotations

import base64

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from loxmatter.api.models import ProjectSyncEntryOut, ProjectSyncPlanOut
from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store
from loxmatter.projectsync.diff import SyncPlan
from loxmatter.projectsync.index import ProjectFormatError
from loxmatter.projectsync.patch import MissingCaptionError
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
        file: UploadFile = File(..., description="Die hochgeladene .Loxone-Projektdatei"),
        bridge_ip: str = Query(..., description="IP der Bruecke, aus Sicht des Miniservers"),
        port: int = Query(DEFAULT_UDP_PORT, description="UDP-Port, auf dem der Miniserver lauscht"),
        listen: int = Query(
            DEFAULT_LISTEN_PORT,
            description="HTTP-Port in den Kommando-URLs neuer Ausgaenge - muss mit dem"
            " --listen von `loxmatter run` uebereinstimmen, wie bei /api/export/download.",
        ),
    ) -> ProjectSyncPlanOut:
        """Baut Diff-Plan und beide gepatchten Datei-Varianten im Speicher -
        schreibt nirgends auf die Platte und markiert kein Geraet als
        exportiert (anders als `/api/export/download`: eine hochgeladene
        Projektdatei ist keine heruntergeladene Vorlage, siehe Entwurf
        Abschnitt 4)."""
        raw = await file.read()
        try:
            result = run_sync(raw, store, bridge_ip=bridge_ip, port=port, listen=listen)
        except (ProjectFormatError, MissingCaptionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return ProjectSyncPlanOut(
            entries=_entries_out(result.plan),
            has_changes=result.plan.has_changes,
            patched_conservative_base64=base64.b64encode(result.patched_conservative).decode(
                "ascii"
            ),
            patched_with_new_devices_base64=base64.b64encode(
                result.patched_with_new_devices
            ).decode("ascii"),
        )

    return router
