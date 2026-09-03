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

"""Uebersetzt gespeicherte Signale in Loxone-Eingangsobjekte.

Zwei Regeln aus der Spec pragen dieses Modul:

Spec 6.3 — ein Matter-Event hat in Loxone kein Zuhause. Ein virtueller
UDP-Eingang kennt nur Werte. Jedes Event wird deshalb zu zwei Objekten: einem
digitalen Impuls, der die Flanke erzeugt, und einem monotonen Zaehler, der ein
verlorenes UDP-Paket ueberlebt, weil er dann nur springt statt zu verschlucken.

Spec 6.6 — Listen, Strukturen, Nullwerte und Texte werden hier verworfen. Sie
bleiben in der Ablage und in der Oberflaeche sichtbar, aber sie koennen kein
Loxone-Objekt werden.

Spec 7.3 — die Einheit eines Signals wandert nicht mehr in den Kommentar,
sondern wird ueber `profiles.table.unit_format` in einen Loxone-Formatstring
uebersetzt (`unit_format`-Feld). Digitale Eingaenge und Events tragen dort
immer `""`: ein Formatstring mit Nachkommastellen ergibt fuer einen Impuls
oder einen Zaehler keinen Sinn.

Spec 6.2 — der Geraete-Praefix ``d<device_id>`` kommt hier nicht aus einer
Vermutung ueber die Signalliste, sondern vom Aufrufer, der ihn von `Store`
kennt. Und weil der Zaehler-Schluessel eines Events (`<key>_n`) frei erfunden
und nirgends reserviert ist, prueft `to_inputs` vor der Rueckgabe, dass kein
Schluessel doppelt vergeben wird — sonst haetten zwei Loxone-Objekte densel-
ben UDP-Namen und Loxone Config wuerde das nicht melden.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter.matter.models import SignalKind
from loxmatter.model.store import StoredSignal
from loxmatter.profiles.table import Exportability, unit_format


@dataclass(frozen=True)
class LoxoneInput:
    key: str
    title: str
    comment: str
    analog: bool
    unit_format: str


def to_inputs(
    signals: Sequence[StoredSignal], device_id: int, device_label: str
) -> list[LoxoneInput]:
    """Erzeugt die Eingangsobjekte eines Geraets, inklusive Online-Signal.

    Bricht laut ab, statt falsch verdrahtete Vorlagen zu erzeugen:

    - jedes Signal muss zu ``device_id`` gehoeren (Praefix ``d<device_id>_``).
      Ein Signal eines anderen Geraets in dieser Liste ist ein Aufrufer-Fehler
      und darf nicht stillschweigend ein falsch beschriftetes Geraet ergeben.
    - kein Schluessel darf zweimal vergeben werden. Der Zaehler-Schluessel
      eines Events (``<key>_n``) wird hier frei erfunden und ist in `Store`
      nirgends reserviert — trifft ihn ein spaeterer `clusters.yaml`-Slug
      zufaellig, waeren das zwei `LoxoneInput`s mit identischem Schluessel,
      also zwei Loxone-Objekte, die denselben UDP-Namen abhoeren.

    ``signal.exported`` entscheidet, ob ein Signal ueberhaupt ein
    `LoxoneInput` erzeugt (Review-Fix Important #3, 2026-09-02): vorher
    filterte diese Funktion ausschliesslich nach `exportability`, und das
    `exported`-Flag aus `PATCH /api/signals/{key}` (Spec 5) veraenderte die
    API-Antwort, aber nie eine erzeugte Vorlage — das Abschalten eines
    Signals in der Oberflaeche hatte auf den Export schlicht keine Wirkung.
    Ein Event mit `exported=False` erzeugt deshalb weder Impuls noch
    Zaehler. Das Online-Signal des Geraets bleibt davon ausdruecklich
    unberuehrt: es gehoert nicht zu einem einzelnen Signal, sondern zum
    Geraet selbst (Spec 6.5), und `StoredSignal` traegt dafuer gar kein
    `exported`-Flag.
    """
    prefix = f"d{device_id}_"
    inputs: list[LoxoneInput] = []
    # Schluessel -> deutschsprachige Herkunftsbeschreibung, fuer die Meldung
    # bei einer Kollision.
    origins: dict[str, str] = {}

    def emit(entry: LoxoneInput, origin: str) -> None:
        if entry.key in origins:
            raise ValueError(
                f"Schluessel-Kollision beim Export: {entry.key!r} wird sowohl von "
                f"{origins[entry.key]} als auch von {origin} erzeugt — das ergaebe "
                f"zwei Loxone-Objekte fuer denselben UDP-Namen."
            )
        origins[entry.key] = origin
        inputs.append(entry)

    for signal in signals:
        if not signal.key.startswith(prefix):
            raise ValueError(
                f"Signal {signal.key!r} gehoert nicht zu Geraet {device_id} "
                f"(erwartetes Praefix {prefix!r})."
            )

        if not signal.exported:
            continue

        comment = f"{device_label} · {signal.ref.path}"

        if signal.ref.kind is SignalKind.EVENT:
            emit(
                LoxoneInput(signal.key, signal.title, f"{comment} · Impuls", False, ""),
                f"dem Impuls von {signal.key!r}",
            )
            emit(
                LoxoneInput(
                    f"{signal.key}_n", f"{signal.title} Zähler", f"{comment} · Zähler", True, ""
                ),
                f"dem Zaehler von {signal.key!r}",
            )
            continue

        if signal.exportability is Exportability.ANALOG:
            emit(
                LoxoneInput(signal.key, signal.title, comment, True, unit_format(signal.unit)),
                f"dem Signal {signal.key!r}",
            )
        elif signal.exportability is Exportability.DIGITAL:
            emit(
                LoxoneInput(signal.key, signal.title, comment, False, ""),
                f"dem Signal {signal.key!r}",
            )

    online_key = f"d{device_id}_online"
    emit(
        LoxoneInput(online_key, f"{device_label} erreichbar", device_label, False, ""),
        "dem Online-Signal",
    )
    return inputs
