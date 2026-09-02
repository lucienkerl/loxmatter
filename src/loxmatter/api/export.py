"""Export ueber die API (Spec 8, Task 5) - dieselben Vorlagen wie
`loxmatter export` auf der Kommandozeile, aus demselben `Store`.

`build_export_router` baut einen `APIRouter` mit Praefix `/api/export`,
eingebunden in `loxone.server.build_app` neben den Routen aus den vorigen
Tasks dieser Phase.

**Dieselbe Datenbank wie die Kommandozeile - keine eigene.** Dieses Modul
nimmt einen bereits geoeffneten `Store` entgegen, genau wie
`api.devices.build_device_router`; es oeffnet nirgends selbst eine
Verbindung zu einer Datei. Die eigentliche Garantie liegt deshalb nicht
hier, sondern in der Verdrahtung: `loxone.server.build_app` reicht
denselben `store`, den `loxmatter run` beim Start ueber
`cli._resolve_store_path` geoeffnet hat, an ALLE Router weiter - diesen
hier eingeschlossen. Waere die WebUI stattdessen mit einer eigenen, zweiten
`Store`-Instanz auf einem anderen Pfad verdrahtet, vergaebe sie fuer
dasselbe Matter-Geraet einen zweiten Satz Signalschluessel (Spec 6.2) - ein
Nutzer, der einmal per CLI und einmal per WebUI exportiert, bekaeme zwei
Vorlagen, die wie dieselbe aussehen, aber unterschiedlich verdrahtet sind.
`tests/api/test_export_api.py::test_api_export_writes_the_same_database_as_the_cli`
belegt das end-to-end: derselbe Datenbankpfad, einmal ueber `loxmatter
export` befuellt, einmal ueber diesen Router gelesen, erzeugt byteidentische
Vorlagen - nicht nur denselben `device_id`.

Weil der Router direkt aus `Store` liest (`store.signals`/`store.commands`),
nicht aus einem frischen Matter-Abbild, braucht er `export.commands` nicht -
die Kommandos stehen bereits als `StoredCommand` in der Datenbank, angelegt
beim Einlernen (`api.devices.commission_device`) oder beim letzten
`loxmatter export`-Lauf.

**Entscheidung 1 - ein Download zaehlt als Export.** `GET
/api/export/download` ruft fuer jedes ausgelieferte Geraet
`Store.mark_exported` auf, `GET /api/export/preview` nie (siehe
`test_preview_does_not_write_anything`). Eine Vorschau ist unzweideutig
folgenlos; ein heruntergeladenes ZIP ist dagegen dasselbe Artefakt, das
`loxmatter export` auf der Kommandozeile erzeugt und das dort unbestritten
als "exportiert" zaehlt (`cli.py`s `export`-Kommando ruft `mark_exported`
aus demselben Grund auf). Der Nachteil: eine Nutzerin, die dieselbe ZIP-Datei
zweimal herunterlaedt, ohne etwas zu aendern, sieht `exported_at` beide Male
weiterspringen, obwohl sich nichts geaendert hat. Die Alternative -
`exported_at` nur bei einer tatsaechlichen inhaltlichen Aenderung
fortschreiben - wuerde denselben Vergleich brauchen, den
`changed_since_export` unten ohnehin zieht, und liefe darauf hinaus, einen
Download klammheimlich zu einer Vorschau zu machen, sobald "nichts neu" ist.
Ein Download, der manchmal zaehlt und manchmal nicht, waere schwerer zu
erklaeren als ein Zeitstempel, der bei einem folgenlosen erneuten Download
harmlos vorspringt.

Wann genau `mark_exported` faellt, ist dabei nicht beliebig: `download`
markiert erst, NACHDEM das ZIP im Speicher vollstaendig aufgebaut ist - nie
Geraet fuer Geraet waehrend des Aufbaus (Review-Fix Important #1,
2026-09-02). Ein Fehler zwischen zwei Geraeten (ein Rendern, das wirft, ein
`store.commands`/`store.signals`, das scheitert, ein `forget_device` aus
einer parallelen Anfrage) darf kein Geraet als exportiert zuruecklassen,
dessen Vorlage der Client mangels 500er-Antwort nie bekommen hat - siehe den
Docstring von `download` unten. Dieselbe Ueberlegung stand schon hinter dem
verzoegerten `mark_exported`-Aufruf in `cli.py`s `export`-Kommando.

**Entscheidung 2 - der Port kommt aus der Anfrage, nicht aus dem Code.**
`download` verlangt `port` (UDP, VirtualInUdp) und `listen` (HTTP, die
Kommando-URLs in VirtualOut) als Query-Parameter, mit denselben Vorgaben wie
`cli.py`s `export --port`/`--listen`: `port` faellt auf
`model.store.DEFAULT_UDP_PORT` (7000) zurueck, `listen` auf 8080 - beides
nur Defaults, nie fest verdrahtet. Ein `loxmatter run --listen 9090` ohne
passenden `listen`-Wert hier erzeugte Vorlagen, deren Ausgangsbefehle ins
Leere liefen, ohne dass der Miniserver das je meldet (derselbe Fehler, den
Review-Fix I3 in `export.documents.render_system_templates` schon einmal
behoben hat).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence

from fastapi import APIRouter, Query
from fastapi.responses import Response

from loxmatter.api.models import ExportDeviceOut, ExportPreviewOut, ExportStatusOut
from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_system_templates,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.signals import to_inputs
from loxmatter.model.store import DEFAULT_UDP_PORT, Store, StoredCommand, StoredDevice
from loxmatter.profiles.table import Exportability

# Wie in cli.py: Text und Nullwerte/Listen/Strukturen ergeben keinen
# Loxone-Eingang (Spec 6.6) - unabhaengig vom `exported`-Flag, das ein
# technisch exportierbares Signal nur an- oder abschaltet.
_UNEXPORTABLE = (Exportability.NONE, Exportability.TEXT)

_DEFAULT_LISTEN_PORT = 8080

# Oeffentlich, weil die Oberflaeche denselben Dateinamen vergeben muss:
# seit die Downloads ueber `fetch` statt ueber einen Link laufen, benennt
# der Browser die Datei selbst (siehe `web/app.js`, `download`).
ARCHIVE_NAME = "loxmatter-export.zip"
_README_NAME = "Import-Anleitung.txt"

_README_TEXT = (
    """\
IMPORT-ANLEITUNG
=================

Diese ZIP-Datei enthaelt Loxone-Vorlagen, erzeugt von loxmatter.

1. Dateien, die mit "VIU_" beginnen, gehoeren in Loxone Config nach:
     Templates\\VirtualIn\\

2. Dateien, die mit "VO_" beginnen, gehoeren nach:
     Templates\\VirtualOut\\

3. Import in Loxone Config: im Projektbaum Rechtsklick auf den
   jeweiligen Ordner (Virtuelle Eingaenge bzw. Virtuelle Ausgaenge) ->
   "Vorlage importieren" -> die Datei auswaehlen.

4. VIU_Matter_System.xml und VO_Matter_System.xml (falls in dieser
   ZIP-Datei enthalten) gehoeren zu keinem einzelnen Geraet. Sie werden
   nur EINMAL pro Projekt gebraucht - nicht bei jedem weiteren Export
   erneut importieren.
"""
).replace("\n", "\r\n")  # Notepad-freundlich - Loxone Config ist eine Windows-Anwendung.


def _loxone_commands(commands: Sequence[StoredCommand]) -> list[LoxoneCommand]:
    """Baut `LoxoneCommand`s aus bereits gespeicherten Kommandos - dieselbe
    Zusammensetzung wie in `cli.py`s `export`-Kommando, hier auf
    `StoredCommand` statt `DeviceCommand` angewandt, weil dieser Router aus
    dem `Store` liest statt aus einem frischen Matter-Abbild (siehe
    Modul-Docstring)."""
    return [
        LoxoneCommand(
            key=command.key,
            title=command.slug,
            path=f"/cmd/{command.key}/" + ("<v>" if command.takes_value else "1"),
            analog=command.takes_value,
        )
        for command in commands
    ]


def _device_preview(device: StoredDevice, store: Store) -> ExportDeviceOut:
    signals = store.signals(device.id)
    commands = store.commands(device.id)
    inputs = to_inputs(signals, device.id, device.label)
    skipped = sum(1 for s in signals if s.exportability in _UNEXPORTABLE)
    return ExportDeviceOut(
        device_id=device.id,
        label=device.label,
        viu_filename=filename_for("VIU", device.id, device.label),
        vo_filename=filename_for("VO", device.id, device.label),
        inputs=len(inputs),
        commands=len(commands),
        skipped=skipped,
    )


def _status_for(device: StoredDevice) -> ExportStatusOut:
    # Ein unbekanntes `updated_at` (Alt-Datenbank vor `_migrate_to_v2`, siehe
    # dort) gilt als "geaendert" - die vorsichtigere der beiden moeglichen
    # Annahmen, siehe StoredDevice.updated_at.
    changed = (
        device.exported_at is None
        or device.updated_at is None
        or device.updated_at > device.exported_at
    )
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
            description="IP der Bruecke, aus Sicht des Miniservers - wie bei /download,"
            " damit dieselbe Anfrage vorab gegen 422 geprueft werden kann.",
        ),
        system: bool = Query(
            False, description="Auch die geraeteunabhaengigen Systemvorlagen mitzaehlen."
        ),
    ) -> ExportPreviewOut:
        """Was ein Download erzeugen wuerde - ohne etwas zu schreiben.

        Ruft `Store.mark_exported` nie auf (siehe Modul-Docstring,
        Entscheidung 1) und veraendert auch sonst keine Zeile. `bridge_ip`
        selbst geht in keine der Zahlen unten ein - es zaehlt nur mit, damit
        eine fehlende Angabe hier ebenso als 422 auffaellt wie spaeter bei
        `/download`, statt erst nach dem Klick auf "Herunterladen". Es
        taucht deshalb bewusst in keinem Feld von `ExportPreviewOut` auf,
        obwohl es Pflichtparameter ist."""
        devices = [_device_preview(device, store) for device in store.devices()]
        system_files = ["VIU_Matter_System.xml", "VO_Matter_System.xml"] if system else []
        return ExportPreviewOut(devices=devices, system_files=system_files)

    @router.get("/download")
    async def download(
        bridge_ip: str = Query(..., description="IP der Bruecke, aus Sicht des Miniservers"),
        port: int = Query(DEFAULT_UDP_PORT, description="UDP-Port, auf dem der Miniserver lauscht"),
        listen: int = Query(
            _DEFAULT_LISTEN_PORT,
            description="HTTP-Port in der erzeugten Kommando-URL (VO-Vorlage) - muss mit"
            " dem --listen von `loxmatter run` uebereinstimmen (siehe Modul-Docstring,"
            " Entscheidung 2).",
        ),
        system: bool = Query(
            False, description="Auch die geraeteunabhaengigen Systemvorlagen einschliessen."
        ),
    ) -> Response:
        """Baut das ZIP im Speicher - keine temporaere Datei, kein
        Zwischenzustand auf der Platte.

        Markiert jedes ausgelieferte Geraet ueber `Store.mark_exported` als
        exportiert (Entscheidung 1 im Modul-Docstring) - aber ERST, nachdem
        das Archiv vollstaendig aufgebaut ist, nicht Geraet fuer Geraet
        waehrend des Aufbaus (Review-Fix Important #1, 2026-09-02).
        Waere zwischen zwei Geraeten ein Fehler aufgetreten - ein Rendern,
        das wirft, ein `store.commands`/`store.signals`, das scheitert, ein
        `forget_device` aus einer parallelen Anfrage -, haette FastAPI 500
        geantwortet und der Client kein ZIP erhalten, waehrend jedes bis
        dahin verarbeitete Geraet trotzdem dauerhaft als exportiert
        vermerkt gewesen waere: `GET /api/export/status` haette es
        anschliessend als "seither unveraendert" gemeldet, obwohl niemand
        die zugehoerige Vorlage je bekommen hat. Dieselbe Disziplin wie in
        `cli.py`s `export`-Kommando, das seinen `Store.mark_exported`-Aufruf
        aus genau diesem Grund erst nach beiden erfolgreichen
        `write_bytes`-Aufrufen ausfuehrt. Die Kurzanleitung liegt IMMER bei,
        unabhaengig von `system` und selbst dann, wenn kein einziges Geraet
        registriert ist - eine leere Installation liefert so ein gueltiges,
        nicht-leeres ZIP statt eines leeren Archivs oder eines
        Serverfehlers."""
        buffer = io.BytesIO()
        # Gesammelt statt sofort vermerkt (siehe oben) - erst nach dem
        # vollstaendigen Aufbau des Archivs unten abgearbeitet.
        exported_device_ids: list[int] = []
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            if system:
                viu_system, vo_system = render_system_templates(bridge_ip, port, listen)
                archive.writestr("VIU_Matter_System.xml", viu_system)
                archive.writestr("VO_Matter_System.xml", vo_system)

            for device in store.devices():
                signals = store.signals(device.id)
                commands = _loxone_commands(store.commands(device.id))
                inputs = to_inputs(signals, device.id, device.label)

                archive.writestr(
                    filename_for("VIU", device.id, device.label),
                    render_virtual_in_udp(device.label, bridge_ip, port, inputs),
                )
                archive.writestr(
                    filename_for("VO", device.id, device.label),
                    render_virtual_out(device.label, f"http://{bridge_ip}:{listen}", commands),
                )
                exported_device_ids.append(device.id)

            archive.writestr(_README_NAME, _README_TEXT)

        # Das Archiv ist an dieser Stelle vollstaendig - jetzt erst zaehlt
        # der Export (siehe Docstring oben). Ein Fehler weiter oben haette
        # diese Zeile nie erreicht, und keines der bis dahin verarbeiteten
        # Geraete waere faelschlich als exportiert markiert.
        for device_id in exported_device_ids:
            store.mark_exported(device_id)

        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{ARCHIVE_NAME}"'},
        )

    @router.get("/status")
    async def status() -> list[ExportStatusOut]:
        """Pro aktivem Geraet: wann zuletzt exportiert, seither geaendert
        (siehe `_status_for`). Ein entferntes Geraet (`forget_device`) taucht
        hier nicht auf - `store.devices()` filtert es bereits aus, dieselbe
        Regel wie bei `GET /api/devices`."""
        return [_status_for(device) for device in store.devices()]

    return router
