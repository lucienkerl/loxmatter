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


"""Macht aus gespeicherten Kommandos die virtuellen Ausgaenge einer Vorlage.

Gegenstueck zu `export.signals` fuer die Eingangsseite. Bewusst NICHT in
`export.commands`: das Modul leitet Kommandos aus einem Matter-Abbild ab und
liegt damit unterhalb von `model.store` (der importiert `DeviceCommand` von
dort). Ein Zugriff auf `StoredCommand` von dort waere ein Importzyklus.
"""

from __future__ import annotations

from collections.abc import Sequence

from loxmatter.export.documents import LoxoneCommand
from loxmatter.model.store import StoredCommand

ON_SLUG = "on"
OFF_SLUG = "off"
PAIRED_TITLE = "onoff"


def _command_path(command: StoredCommand) -> str:
    return f"/cmd/{command.key}/" + ("<v>" if command.takes_value else "1")


def to_outputs(commands: Sequence[StoredCommand]) -> list[LoxoneCommand]:
    """Baut die virtuellen Ausgaenge eines Geraets aus seinen Kommandos.

    **Ein und Aus gibt es zusaetzlich als EINEN kombinierten Ausgang**
    (2026-09-03). Loxone sieht fuer einen digitalen virtuellen Ausgang
    `CmdOn` und `CmdOff` vor: ein Objekt, das bei der steigenden Flanke das
    eine und bei der fallenden das andere schickt. Genau das braucht man, um
    einen Schalter direkt darauf zu legen.

    **Die einzelnen Ausgaenge bleiben trotzdem erhalten**, und das ist keine
    Unentschlossenheit, sondern der Fall, in dem das Geraet auch ausserhalb
    von Loxone geschaltet werden kann: dann folgt der Zustand in der Config
    nicht mehr dem tatsaechlichen, und man will Ein und Aus einzeln
    ausloesen koennen, statt an einer Flanke zu haengen, die vielleicht gar
    nicht kommt. Beide Varianten in der Vorlage zu haben kostet nichts - es
    sind Eintraege, aus denen man sich bedient; wer den kombinierten
    verdrahtet, laesst die einzelnen einfach liegen.

    Gepaart wird nur, was zusammengehoert: gleicher Endpunkt UND gleicher
    Cluster. Die Zuordnung stammt aus den gespeicherten Feldern, nicht aus
    dem Schluesselnamen - Schluessel sind opak (Hauptdokument 6.2), und aus
    `d1_1_on` auf den Endpunkt zurueckzuschliessen waere ein Bruch dieser
    Regel durch die Hintertuer.

    `toggle` bekommt keinen Partner: es hat keinen Gegenbefehl. Ebenso jedes
    Kommando mit Wert (`level`), das ohnehin analog ist.

    Die URLs aendern sich nicht - `/cmd/d1_1_on/1` und `/cmd/d1_1_off/1`
    bleiben, was sie waren. Neu ist allein ein zusaetzliches Loxone-Objekt,
    das beide benutzt. Der kombinierte Ausgang steht unmittelbar vor seinem
    `on`, damit die drei in der Config beieinander liegen.

    Eine Quelle fuer beide Exportwege: `cli.py`s `export`-Kommando und der
    API-Router bauten diese Liste vorher zweimal getrennt zusammen.
    """
    by_group: dict[tuple[int, int], dict[str, StoredCommand]] = {}
    for command in commands:
        by_group.setdefault((command.endpoint, command.cluster_id), {})[command.slug] = command

    pairs: dict[str, StoredCommand] = {}
    for group in by_group.values():
        on, off = group.get(ON_SLUG), group.get(OFF_SLUG)
        if on is not None and off is not None and not on.takes_value and not off.takes_value:
            pairs[on.key] = off

    result: list[LoxoneCommand] = []
    for command in commands:
        off = pairs.get(command.key)
        if off is not None:
            result.append(
                LoxoneCommand(
                    # Der Kommentar nennt beide Schluessel: in der Config
                    # ist sonst nicht zu sehen, welche zwei Befehle hier
                    # zusammengefasst sind.
                    key=f"{command.key} + {off.key}",
                    title=PAIRED_TITLE,
                    path=_command_path(command),
                    analog=False,
                    off_path=_command_path(off),
                )
            )
        result.append(
            LoxoneCommand(
                key=command.key,
                title=command.slug,
                path=_command_path(command),
                analog=command.takes_value,
            )
        )
    return result
