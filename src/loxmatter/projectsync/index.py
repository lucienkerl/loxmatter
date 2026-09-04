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

"""Baut aus dem Byte-Span-Baum (`projectsync.scan`) einen nach `loxmatter`-
Schluesseln durchsuchbaren Index: welche virtuellen Eingaenge/Ausgaenge gibt
es schon, und in welchem Geraete-Container stecken sie (Entwurf Abschnitt
3.3/5).

**Korrektur nach echtem Praxistest (2026-09-04):** die urspruengliche Annahme
- `VirtualInCaption`/`VirtualOutCaption` liegen direkt unter `<ControlList>` -
war falsch. An einer echten, seit Jahren gewachsenen Projektdatei geprueft:
`<ControlList>` hat genau EIN Kind, `<C Type="Document">`, und JEDER darin
konfigurierte Miniserver bekommt einen eigenen `<C Type="LoxLIVE">`-Block
(mit dessen eigener `IntAddr`, `Serial` usw.) - `VirtualInCaption`/
`VirtualOutCaption` sind Kinder DIESES `LoxLIVE`-Blocks, nicht von
`ControlList` oder `Document`. Eine Datei kann mehrere `LoxLIVE`-Bloecke
haben (mehrere Miniserver in einem Projekt) - `build_index` muss darum erst
den richtigen auswaehlen, bevor es ueberhaupt nach virtuellen Ein-/Ausgaengen
sucht. Der urspruengliche, flache Aufbau parste ohne Fehlermeldung durch,
fand aber schlicht NICHTS - jedes bestehende Geraet erschien faelschlich als
`new_device` (siehe `docs/superpowers/specs/2026-09-03-projektdatei-sync-design.md`,
Abschnitt zur Miniserver-Zuordnung, fuer die vollstaendige Herleitung)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter.projectsync.keys import key_from_check, key_from_cmd_on
from loxmatter.projectsync.scan import Element, ProjectFormatError, parse_root, scan_children

__all__ = [
    "AmbiguousMiniserverError",
    "MiniserverCandidate",
    "ProjectFormatError",
    "ProjectIndex",
    "build_index",
]

_U_ATTR = re.compile(r'\bU="([^"]*)"')
_INAME_ATTR = re.compile(r'\bIName="([^"]*)"')


@dataclass(frozen=True)
class MiniserverCandidate:
    """Ein in der Projektdatei gefundener Miniserver (`LoxLIVE`-Block), so
    wie ihn `AmbiguousMiniserverError.candidates` traegt - genug, um in der
    WebUI ein Auswahlfeld zu fuellen (Nutzerwunsch: auswaehlen statt die IP
    von Hand abzutippen), ohne den ganzen `Element`-Baum durchzureichen."""

    title: str
    int_addr: str


class AmbiguousMiniserverError(ProjectFormatError):
    """Die Projektdatei ist gueltig, aber welcher `LoxLIVE`-Block (= welcher
    konfigurierte Miniserver) gemeint ist, laesst sich nicht eindeutig
    bestimmen - entweder gibt es gar keinen, oder mehrere und keine (oder
    eine nicht passende) `miniserver_ip` wurde mitgegeben. Ohne eindeutige
    Zuordnung koennte der Abgleich sonst im falschen Miniserver-Bereich einer
    Mehr-Miniserver-Datei landen. Subklasse von `ProjectFormatError`, damit
    dieselbe Fehlerbehandlung am Upload-Endpunkt greift (klare 400-Antwort
    statt 500) - die Datei selbst ist dabei nicht fehlerhaft, nur die Anfrage
    unvollstaendig.

    `candidates` traegt die tatsaechlich gefundenen Miniserver, wenn es
    welche gibt (leer nur im "gar keiner konfiguriert"-Fall, wo es nichts
    zur Auswahl gibt) - `api.project_sync` nutzt das, um statt einer reinen
    Fehlermeldung ein Auswahlfeld anzubieten (Nutzerwunsch nach dem
    Review)."""

    def __init__(self, message: str, candidates: Sequence[MiniserverCandidate] = ()) -> None:
        super().__init__(message)
        self.candidates: list[MiniserverCandidate] = list(candidates)


@dataclass
class ProjectIndex:
    text: str
    root_attrs: dict[str, str]
    root_open_end: int
    root_close_start: int
    # Der ausgewaehlte `LoxLIVE`-Block (= Miniserver), gegen den dieser Lauf
    # abgleicht - neu angelegte Captions (siehe `patch._new_device_edit`)
    # haengen an dessen `inner_end`, nicht mehr an `root_close_start`.
    target_loxlive: Element
    virtual_in_caption: Element | None
    virtual_out_caption: Element | None
    input_cmds: dict[str, Element]
    output_cmds: dict[str, Element]
    input_containers: dict[str, Element]
    output_containers: dict[str, Element]
    all_u_values: set[str]
    all_inames: set[str]


def _find_all_loxlive(elements: list[Element]) -> list[Element]:
    """Findet alle `LoxLIVE`-Bloecke irgendwo im Baum, unabhaengig von der
    Verschachtelungstiefe - an der Referenzdatei liegen sie unter `Document`,
    nicht direkt unter `<ControlList>`. Rekursiv statt eine feste Tiefe
    anzunehmen: diese Tiefe ist selbst kein dokumentiertes, verlaessliches
    Format-Merkmal."""
    found: list[Element] = []
    for element in elements:
        if element.type == "LoxLIVE":
            found.append(element)
        found.extend(_find_all_loxlive(element.children))
    return found


def _describe(loxlives: list[Element]) -> str:
    return ", ".join(
        f"„{ll.attrs.get('Title', '?')}“ ({ll.attrs.get('IntAddr', 'keine IP bekannt')})"
        for ll in loxlives
    )


def _candidates(loxlives: list[Element]) -> list[MiniserverCandidate]:
    """Baut `AmbiguousMiniserverError.candidates` aus den gefundenen
    `LoxLIVE`-Bloecken - nur die, die auch eine `IntAddr` tragen: ohne sie
    gibt es nichts, das `miniserver_ip` beim naechsten Versuch entgegennehmen
    koennte, so ein Block waere in der Auswahl also nur ein toter Eintrag."""
    return [
        MiniserverCandidate(title=ll.attrs.get("Title", "?"), int_addr=ll.attrs["IntAddr"])
        for ll in loxlives
        if ll.attrs.get("IntAddr")
    ]


def _resolve_target_loxlive(loxlives: list[Element], miniserver_ip: str | None) -> Element:
    """Waehlt den EINEN `LoxLIVE`-Block, gegen den dieser Lauf abgleicht.

    Genau ein Block in der Datei: der ist es - unabhaengig davon, ob
    `miniserver_ip` gesetzt ist, WENN sie nicht gesetzt ist. Ist sie
    gesetzt, muss sie trotzdem passen (siehe unten): eine explizit
    mitgegebene, nicht passende IP deutet eher auf die falsche Datei hin als
    auf einen Grund, sie zu ignorieren.

    Mehrere Bloecke: `miniserver_ip` ist Pflicht und muss exakt einem
    `LoxLIVE.IntAddr` entsprechen (derselben internen Adresse, die auch
    `loxmatter run --miniserver <IP>` bekommt) - sonst koennte der Abgleich
    in der falschen Miniserver-Haelfte der Datei landen, und genau das soll
    dieses Feature nie tun."""
    if not loxlives:
        raise AmbiguousMiniserverError(
            "Diese Projektdatei enthaelt keinen einzigen konfigurierten Miniserver "
            "(keinen `LoxLIVE`-Block) - es gibt keinen Ort, an dem sich virtuelle "
            "Ein-/Ausgaenge verorten liessen."
        )
    if miniserver_ip:
        matches = [ll for ll in loxlives if ll.attrs.get("IntAddr") == miniserver_ip]
        if not matches:
            raise AmbiguousMiniserverError(
                f"Kein Miniserver mit der IP {miniserver_ip!r} in dieser Projektdatei "
                f"gefunden. Vorhanden: {_describe(loxlives)}.",
                _candidates(loxlives),
            )
        return matches[0]
    if len(loxlives) > 1:
        raise AmbiguousMiniserverError(
            f"Diese Projektdatei enthaelt mehrere Miniserver: {_describe(loxlives)}. "
            "Bitte den gewuenschten Miniserver auswaehlen.",
            _candidates(loxlives),
        )
    return loxlives[0]


def build_index(text: str, miniserver_ip: str | None = None) -> ProjectIndex:
    root_attrs, _root_open_start, root_open_end, root_close_start = parse_root(text)
    top_level = scan_children(text, root_open_end, root_close_start)

    loxlives = _find_all_loxlive(top_level)
    target_loxlive = _resolve_target_loxlive(loxlives, miniserver_ip)
    if target_loxlive.self_closing or target_loxlive.inner_end is None:
        raise AmbiguousMiniserverError(
            f"Der Miniserver „{target_loxlive.attrs.get('Title', '?')}“ hat in dieser "
            "Projektdatei noch keinerlei Konfiguration - kein Ort, an dem sich ein "
            "virtueller Ein-/Ausgang anlegen liesse."
        )

    virtual_in_caption = next(
        (e for e in target_loxlive.children if e.type == "VirtualInCaption"), None
    )
    virtual_out_caption = next(
        (e for e in target_loxlive.children if e.type == "VirtualOutCaption"), None
    )

    input_cmds: dict[str, Element] = {}
    input_containers: dict[str, Element] = {}
    if virtual_in_caption is not None:
        for container in virtual_in_caption.children:
            if container.type != "VirtualUdpIn":
                continue
            for cmd in container.children:
                if cmd.type != "VirtualUdpInCmd":
                    continue
                key = key_from_check(cmd.attrs.get("Check", ""))
                if key is not None:
                    input_cmds[key] = cmd
                    input_containers[key] = container

    output_cmds: dict[str, Element] = {}
    output_containers: dict[str, Element] = {}
    if virtual_out_caption is not None:
        for container in virtual_out_caption.children:
            if container.type != "VirtualOut":
                continue
            for cmd in container.children:
                if cmd.type != "VirtualOutCmd":
                    continue
                key = key_from_cmd_on(cmd.attrs.get("CmdOn", ""))
                if key is not None:
                    output_cmds[key] = cmd
                    output_containers[key] = container

    return ProjectIndex(
        text=text,
        root_attrs=root_attrs,
        root_open_end=root_open_end,
        root_close_start=root_close_start,
        target_loxlive=target_loxlive,
        virtual_in_caption=virtual_in_caption,
        virtual_out_caption=virtual_out_caption,
        input_cmds=input_cmds,
        output_cmds=output_cmds,
        input_containers=input_containers,
        output_containers=output_containers,
        # Ueber den gesamten Rohtext, nicht nur ueber <C>-Elemente: <Co>-
        # Verdrahtungsstummel tragen ebenfalls U-IDs, die eine neu erzeugte
        # ID nicht kollidieren duerfen (Entwurf Abschnitt 6).
        all_u_values=set(_U_ATTR.findall(text)),
        all_inames=set(_INAME_ATTR.findall(text)),
    )
