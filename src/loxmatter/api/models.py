"""Antwortmodelle der REST-API.

Bewusst getrennt von den Speichermodellen in `model.store`: was die
Oberflaeche sieht, ist eine Sicht auf den Zustand, keine Abbildung der
Tabellen. Aendert sich das Schema, aendert sich nicht zwangslaeufig die API.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SignalOut(BaseModel):
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


class DeviceOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    node_id: int
    label: str
    online: bool
    signal_count: int
    exportable_count: int


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


class DeviceRename(BaseModel):
    """`PATCH /api/devices/{device_id}` - einzig das Label ist aenderbar,
    genau wie bei einem Signal nur `title`/`exported` (siehe `SignalPatch`).
    Weder `node_id` noch `id` gehoeren hier her, aus demselben Grund: keine
    Chance, sie versehentlich zu uebernehmen."""

    model_config = ConfigDict(frozen=True)

    label: str


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
