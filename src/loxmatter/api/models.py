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

"""Antwortmodelle der REST-API.

Bewusst getrennt von den Speichermodellen in `model.store`: was die
Oberflaeche sieht, ist eine Sicht auf den Zustand, keine Abbildung der
Tabellen. Aendert sich das Schema, aendert sich nicht zwangslaeufig die API.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SignalOut(BaseModel):
    """`exportable`/`reason` (Spec 6.6) und `exported` (vom Nutzer umschaltbar,
    siehe `model.store.StoredSignal.exported`) sagen, was TECHNISCH auf einen
    Loxone-Eingang passt und was DAVON in den naechsten Export soll -
    `functional` (Aufgabe 8) beantwortet eine dritte, unabhaengige Frage: ob
    `profiles.relevance.is_functional` dieses Signal fuer den GERAETETYP als
    gewollt einstuft. Die Oberflaeche nutzt allein dieses Feld, um die
    Signalliste in "Funktional" und "Experte" zu gliedern (`api.devices.
    _signal_out` liest es unveraendert aus `StoredSignal.functional`) - eine
    zweite Berechnung der Regel gibt es weder in der API-Schicht noch in
    JavaScript.

    `resend` (Entwurf periodischer Resend, 2026-09-04) ist eine VIERTE,
    wieder unabhaengige Frage: ob der periodische Timer (`Runtime.
    resend_marked`) dieses Signal auch ohne Aenderung erneut senden soll.
    Betrifft `/resync` und den Bridge-Start (`Runtime.resend_all`) nicht -
    die ignorieren dieses Feld bewusst, siehe dortigen Docstring."""

    model_config = ConfigDict(frozen=True)

    key: str
    path: str
    kind: str
    title: str
    unit: str
    value: float | bool | str | None
    exportable: bool
    reason: str | None
    exported: bool
    functional: bool
    resend: bool


class DeviceOut(BaseModel):
    """`signal_count`/`exportable_count` sagen, wie viele Signale es gibt und
    wie viele davon technisch auf einen Loxone-Eingang passen (Spec 6.6) -
    beide unabhaengig davon, ob sie tatsaechlich als Vorlage exportiert
    wuerden. `next_export_count` (Nachbesserung Fix 7, Phase 6) ist die
    davon verschiedene Zahl, die die Gerätekachel bis dahin gar nicht zeigte:
    wie viele `LoxoneInput`s (inklusive Online-Signal) der naechste Export
    tatsaechlich erzeugt (`export.signals.to_inputs`, gefiltert auf
    `exported`) - fuer die Steckdose der Testvorlage etwa 159 Signale, 110
    exportierbar, aber nur 6 im naechsten Export (5 funktionale plus das
    Online-Signal)."""

    model_config = ConfigDict(frozen=True)

    id: int
    node_id: int
    label: str
    online: bool
    signal_count: int
    exportable_count: int
    next_export_count: int


class SignalPatch(BaseModel):
    """Was sich an einem Signal ueberhaupt aendern laesst.

    Spec 6.2: der Schluessel ist die Verdrahtung in Loxone. Waere er hier
    aenderbar, koennte ein Klick in der Oberflaeche einen Baustein im Haus
    still totlegen - deshalb kennt dieses Modell gar kein `key`-Feld. Ein
    mitgeschicktes `key` landet bei Pydantic niemals auf dem Objekt und wird
    von `devices.rename_signal` entsprechend nie gelesen, geschweige denn
    angewendet - das ist keine Frage der Sorgfalt im Handler, sondern eine,
    die dieses Modell strukturell unmoeglich macht. Grund ist Pydantic v2s
    eigener Default fuer unbekannte Felder, `extra="ignore"` (Berichtigung
    M1, Review 2026-09-02: hier stand faelschlich `extra="allow"` als
    Default - das Gegenteil, es wuerde unbekannte Felder gerade behalten).
    """

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    exported: bool | None = None
    resend: bool | None = None


class DeviceRename(BaseModel):
    """`PATCH /api/devices/{device_id}` - einzig das Label ist aenderbar,
    genau wie bei einem Signal nur `title`/`exported` (siehe `SignalPatch`).
    Weder `node_id` noch `id` gehoeren hier her, aus demselben Grund: keine
    Chance, sie versehentlich zu uebernehmen."""

    model_config = ConfigDict(frozen=True)

    label: str


class CommandOut(BaseModel):
    """Ein Bedienelement fuer `GET /api/devices/{device_id}/controls` (Task 4).

    Traegt bewusst nur, was ein Klick braucht - der Schluessel zum Ausloesen
    und der Slug als Beschriftung. `takes_value` sagt der Oberflaeche, ob ein
    einfacher Knopf reicht (z. B. `on`) oder ein Regler noetig ist (z. B.
    `level`)."""

    model_config = ConfigDict(frozen=True)

    key: str
    slug: str
    takes_value: bool


class ControlsOut(BaseModel):
    """Antwort von `GET /api/devices/{device_id}/controls` (Task 4).

    `hidden_raw_commands` (Review-Fix Minor #4, 2026-09-02): wie viele
    Kommandos des Geraets herausgefiltert wurden, weil `profiles.table.
    command_slug` sie nicht kennt (siehe `CommandOut` oben und der
    Docstring der Route). Der Filter selbst bleibt richtig - ein Knopf ohne
    erkennbare Bedeutung waere nutzlos -, aber ohne diese Zahl verschwindet
    ein unbekanntes Kommando fuer eine Person, die ein fremdes Geraet
    diagnostiziert, spurlos. Mit ihr laesst sich die Oberflaeche schreiben:
    "N weitere Kommandos vorhanden, aber nicht benannt" statt stillschweigend
    nichts zu zeigen."""

    model_config = ConfigDict(frozen=True)

    commands: list[CommandOut]
    hidden_raw_commands: int


class ValueIn(BaseModel):
    """Rumpf von `POST /api/commands/{key}` und `POST /api/signals/{key}/write`
    (Task 4) - derselbe String-Wert, den `/cmd/{key}/{value}` (Phase 4) als
    Pfad-Segment entgegennimmt. Ein Wert, ein Typ, an beiden Stellen (Spec 4.2)."""

    model_config = ConfigDict(frozen=True)

    value: str


class CommissionRequest(BaseModel):
    """`POST /api/devices/commission` - der Pairing-Code vom Geraet oder
    seiner Verpackung (11-stellig oder der 21-stellige `MT:`-Code, Spec 7.1).

    `thread_dataset` ist optional: nur Thread-Geraete brauchen ihn, und nur
    dann, bevor `commission_with_code` ueberhaupt versucht wird (siehe
    `BridgeMatterClient.commission_with_code` - ohne vorherigen
    `set_thread_dataset`-Aufruf scheitert ein Thread-Geraet mit "Required
    network information not provided").
    """

    model_config = ConfigDict(frozen=True)

    code: str
    thread_dataset: str | None = None


class ExportDeviceOut(BaseModel):
    """Ein Geraet in der Antwort von `GET /api/export/preview` (Task 5).

    Spiegelt die Ausgabe von `loxmatter export` auf der Kommandozeile
    (`cli.py`) als Zahlen statt als Terminalzeilen: `inputs` und `commands`
    sind die Anzahl der Objekte, die in den beiden Vorlagendateien
    entstuenden, `skipped` die Signale, die keinen Loxone-Eingang ergeben
    (Listen, Strukturen, Texte, Nullwerte - Spec 6.6), unabhaengig vom
    `exported`-Flag. `viu_filename`/`vo_filename` sind dieselben Namen, die
    auch im ZIP von `GET /api/export/download` landen (`filename_for`) - so
    kann die Oberflaeche vor dem Herunterladen bereits zeigen, welche
    Dateien entstehen.

    `hidden_count` (Aufgabe 8): wie viele Signale dieses Geraets die
    Signalliste standardmaessig im zugeklappten "Experte"-Block versteckt,
    weil `profiles.relevance.is_functional` sie nicht als gewollt einstuft
    (`StoredSignal.functional`) - unabhaengig davon, ob sie technisch
    exportierbar waeren. Fuer die Steckdose der Testvorlage sind das 154 von
    159 Signalen."""

    model_config = ConfigDict(frozen=True)

    device_id: int
    label: str
    viu_filename: str
    vo_filename: str
    inputs: int
    commands: int
    skipped: int
    hidden_count: int


class ExportPreviewOut(BaseModel):
    """Antwort von `GET /api/export/preview` (Task 5) - reine Vorschau, kein
    Schreibzugriff (siehe `api.export.preview`)."""

    model_config = ConfigDict(frozen=True)

    devices: list[ExportDeviceOut]
    system_files: list[str]


class ExportStatusOut(BaseModel):
    """Ein Geraet in der Antwort von `GET /api/export/status` (Task 5).

    `exported_at` ist `None`, solange ein Geraet noch nie ueber `GET
    /api/export/download` (API) oder `loxmatter export` (CLI) exportiert
    wurde - beide schreiben denselben Zeitstempel in dieselbe Datenbank
    (siehe `model.store.Store.mark_exported`). `changed_since_export` ist in
    diesem Fall ebenfalls `True`: ohne einen vorherigen Export gibt es nichts,
    wogegen der aktuelle Stand unveraendert sein koennte."""

    model_config = ConfigDict(frozen=True)

    device_id: int
    label: str
    exported_at: str | None
    changed_since_export: bool


class BridgeSettingsOut(BaseModel):
    """Antwort von `GET`/`PATCH /api/settings` (Geraete-Dashboard-Entwurf,
    Abschnitt 4). `bridge_ip`/`saved_at` sind `None`, solange niemand die
    Verbindung zum Miniserver eingerichtet hat - der Fall, in dem die
    Oberflaeche den Export-Knopf an jeder Geraetekarte deaktiviert."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    saved_at: str | None


class BridgeSettingsIn(BaseModel):
    """Rumpf von `PATCH /api/settings` - alle drei Felder zusammen, kein
    Teil-Update: sie gehoeren fachlich zusammen (dieselbe virtuelle
    Verbindung), ein Teil-Update koennte sonst eine gueltige IP mit einem
    inzwischen falschen Port stehen lassen. `min_length=1` auf `bridge_ip`
    ergibt 422 bei leerem Feld, ohne einen eigenen Validator."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str = Field(min_length=1)
    udp_port: int
    listen_port: int


class ProjectSyncEntryOut(BaseModel):
    """Eine Zeile im Diff-Plan von `POST /api/export/project-sync` (Entwurf
    Abschnitt 5/7). `changes` ist ausserhalb von `status == "updated"` immer
    leer."""

    model_config = ConfigDict(frozen=True)

    kind: str
    device_id: int
    device_label: str
    key: str
    title: str
    status: str
    changes: dict[str, list[str]]


class ProjectSyncPlanOut(BaseModel):
    """Antwort von `POST /api/export/project-sync` - Plan und beide
    gepatchten Datei-Varianten in einer Antwort (Entwurf Abschnitt 4/7): kein
    zweiter Server-Roundtrip, der "Bestaetigen"-Schritt ist rein
    clientseitig."""

    model_config = ConfigDict(frozen=True)

    entries: list[ProjectSyncEntryOut]
    has_changes: bool
    patched_conservative_base64: str
    # `None`, wenn die experimentelle Variante fuer diese Datei nicht gebaut
    # werden konnte (z. B. fehlender `VirtualInCaption`-Abschnitt, Entwurf
    # Abschnitt 8). Der Plan und die konservative Variante bleiben davon
    # unberuehrt - die Oberflaeche zeigt dann nur den Grund statt des
    # Download-Angebots.
    patched_with_new_devices_base64: str | None
    new_devices_unavailable_reason: str | None


class ResendIntervalOut(BaseModel):
    """Antwort von `GET`/`PATCH /api/settings/resend-interval` (Entwurf
    periodischer Resend, 2026-09-04, Abschnitt 5)."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float


class ResendIntervalIn(BaseModel):
    """Rumpf von `PATCH /api/settings/resend-interval`. `gt=0` faengt einen
    nicht-positiven Wert bereits hier ab (422 ohne eigenen Validator); die
    tatsaechliche Untergrenze (`MIN_RESEND_INTERVAL_SECONDS`) prueft
    `ResendSettingsStore.set_interval_seconds` selbst, siehe dort."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float = Field(gt=0)
