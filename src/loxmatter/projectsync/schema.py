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

"""Attribut-Schema der Projektdatei-Objekte (Entwurf Abschnitt 3.4/6).

Zwei getrennte Ebenen mit unterschiedlicher Sicherheit:

**Update bestehender Objekte** (`desired_*_attrs`, `MANAGED_*_ATTRS`) fasst
bewusst nur Titel, den Check/CmdOn-Schluessel selbst, den Analog-Schalter und
die Einheit an - Skalierung, MinVal/MaxVal und jede Verdrahtung bleiben
unberuehrt, auch wenn ein Export inzwischen einen anderen Wert vorschlaegt.
Das ist die risikoarme Haelfte: sie aendert nur Attributwerte in einer
bereits von Config akzeptierten Struktur.

**Neuanlage** (`new_*_open_tag`, `new_cmd_children_xml`, `new_*_container_open_tag`)
baut auf den bereits gegen einen echten Import verifizierten Attributlisten
aus `export.documents` auf (siehe dortigen Moduldocstring) - fuer die
Kind-Elemente (`Co`/`IoData`/`Display`), die im Vorlagen-Schema kein
Gegenstueck haben, gibt es keine solche Verifikation; das ist der
unverifizierte Rest, den Entwurf Abschnitt 6 offen benennt."""

from __future__ import annotations

import re

from loxmatter.export.documents import (
    LoxoneCommand,
    virtual_in_udp_cmd_attributes,
    virtual_out_cmd_attributes,
)
from loxmatter.export.signals import LoxoneInput
from loxmatter.export.xml import render_attrs
from loxmatter.projectsync.ids import new_unique_id
from loxmatter.projectsync.scan import Element, parse_attrs

MANAGED_INPUT_CMD_ATTRS: tuple[str, ...] = ("Title", "Check", "Analog", "Unit")
MANAGED_OUTPUT_CMD_ATTRS: tuple[str, ...] = ("Title", "CmdOn", "CmdOff", "Analog")

_IODATA = re.compile(r"<IoData\s+([^/]*)/>")


def desired_input_cmd_attrs(entry: LoxoneInput) -> dict[str, str]:
    """Soll-Zustand der vom Update verwalteten Attribute eines bestehenden
    `VirtualUdpInCmd` (Entwurf Abschnitt 5)."""
    return {
        "Title": entry.title,
        "Check": f"{entry.key}:{entry.check_suffix}",
        "Analog": "true" if entry.analog else "false",
        "Unit": entry.unit_format,
    }


def desired_output_cmd_attrs(command: LoxoneCommand) -> dict[str, str]:
    """Soll-Zustand der vom Update verwalteten Attribute eines bestehenden
    `VirtualOutCmd`. `CmdOff` fehlt absichtlich, wenn es keinen Aus-Befehl
    gibt - ein fehlendes Attribut wird von `diff.py` nie als "muss entfernt
    werden" behandelt, nur vorhandene Attribute werden verglichen."""
    attrs = {
        "Title": command.title,
        "CmdOn": command.path,
        "Analog": "false" if command.off_path else "true",
    }
    if command.off_path:
        attrs["CmdOff"] = command.off_path
    return attrs


def new_input_cmd_open_tag(entry: LoxoneInput, iname: str, u: str) -> str:
    """Start-Tag eines frisch angelegten `VirtualUdpInCmd`, auf denselben
    Attributen wie die bereits verifizierte Vorlagendatei (`export.documents.
    virtual_in_udp_cmd_attributes`), ergaenzt um `Type`/`IName`/`U`/`Nio`/`WF`,
    die eine Projektdatei zusaetzlich braucht (an der Referenzdatei
    beobachtet, Entwurf Abschnitt 6)."""
    attrs = [
        ("Type", "VirtualUdpInCmd"),
        ("IName", iname),
        ("U", u),
        *virtual_in_udp_cmd_attributes(entry),
        ("Nio", "2"),
        ("WF", "16400"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_output_cmd_open_tag(command: LoxoneCommand, iname: str, u: str) -> str:
    """Wie `new_input_cmd_open_tag`, fuer `VirtualOutCmd` - auf
    `export.documents.virtual_out_cmd_attributes`."""
    attrs = [
        ("Type", "VirtualOutCmd"),
        ("IName", iname),
        ("U", u),
        *virtual_out_cmd_attributes(command),
        ("Nio", "1"),
        ("WF", "16400"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_input_container_open_tag(
    device_label: str, bridge_ip: str, port: int, iname: str, u: str
) -> str:
    """Start-Tag eines frisch angelegten `VirtualUdpIn`-Geraete-Containers -
    nur fuer den Experimentell-Pfad (Entwurf Abschnitt 3.4)."""
    attrs = [
        ("Type", "VirtualUdpIn"),
        ("IName", iname),
        ("U", u),
        ("Title", f"Matter — {device_label}"),
        ("WF", "16384"),
        ("Address", bridge_ip),
        ("Port", str(port)),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_output_container_open_tag(device_label: str, base_url: str, iname: str, u: str) -> str:
    """Wie `new_input_container_open_tag`, fuer `VirtualOut`."""
    attrs = [
        ("Type", "VirtualOut"),
        ("IName", iname),
        ("U", u),
        ("Title", f"Matter — {device_label}"),
        ("WF", "16384"),
        ("Address", base_url),
        ("CloseAfterSend", "true"),
        ("CmdSep", ";"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_caption_open_tag(kind: str, iname: str, u: str) -> str:
    """Start-Tag eines frisch angelegten `VirtualInCaption`/`VirtualOutCaption`
    - nur, wenn die Projektdatei noch nie einen virtuellen Ein- bzw. Ausgang
    dieser Art hatte (Entwurf Abschnitt 8: Sonderfall der Neuanlage, ebenfalls
    hinter dem Experimentell-Haken). `IName` folgt dem an der Referenzdatei
    beobachteten Muster `C<n>` - ein eigener Namensraum, getrennt von den
    Geraete-Containern (`VUI`/`VQ`) und ihren Kommandos (`VCI`/`VQC`)."""
    if kind not in ("input", "output"):
        raise ValueError(f"Unbekannte Art {kind!r} - erwartet 'input' oder 'output'.")
    type_name = "VirtualInCaption" if kind == "input" else "VirtualOutCaption"
    return f"<C {render_attrs([('Type', type_name), ('IName', iname), ('U', u)])}>"


def sibling_iodata_attrs(text: str, element: Element) -> dict[str, str] | None:
    """Die Attribute des `<IoData .../>`-Kindes eines bestehenden Cmd-
    Elements, falls vorhanden - Quelle fuer die Berechtigungswerte eines neu
    angelegten Geschwister-Objekts (Entwurf Abschnitt 6: dieselben Cr/Pr-
    Werte wie ein Nachbarobjekt, statt sie zu erfinden)."""
    if element.self_closing or element.inner_end is None:
        return None
    match = _IODATA.search(text, element.open_end, element.inner_end)
    if match is None:
        return None
    return parse_attrs(match.group(0))


def find_any_iodata_attrs(text: str, caption: Element | None) -> dict[str, str] | None:
    """Wie `sibling_iodata_attrs`, aber ueber den gesamten Inhalt eines
    `VirtualInCaption`/`VirtualOutCaption`-Containers gesucht - Fallback fuer
    ein komplett neues Geraet, das noch kein Geschwister-Cmd hat."""
    if caption is None or caption.self_closing or caption.inner_end is None:
        return None
    match = _IODATA.search(text, caption.open_end, caption.inner_end)
    if match is None:
        return None
    return parse_attrs(match.group(0))


def new_cmd_children_xml(
    *, kind: str, existing_u: set[str], iodata_attrs: dict[str, str] | None
) -> str:
    """XML-Text der Kind-Elemente eines frisch angelegten Cmd-Objekts:
    Verdrahtungs-Stummel (zwei fuer einen Eingang - `AQ`/`Q` -, einer fuer
    einen Ausgang - `I`), optional ein `IoData`-Element mit uebernommenen
    Berechtigungswerten, und ein `Display`-Element (Entwurf Abschnitt 6).
    `kind` ist ``"input"`` oder ``"output"``."""
    if kind == "input":
        connectors = [
            f'<Co K="AQ" U="{new_unique_id(existing_u)}"/>',
            f'<Co K="Q" U="{new_unique_id(existing_u)}"/>',
        ]
    elif kind == "output":
        connectors = [f'<Co K="I" U="{new_unique_id(existing_u)}"/>']
    else:
        raise ValueError(f"Unbekannte Art {kind!r} - erwartet 'input' oder 'output'.")

    parts = list(connectors)
    if iodata_attrs:
        parts.append(f"<IoData {render_attrs(list(iodata_attrs.items()))}/>")
    parts.append('<Display Unit="&lt;v.1&gt;" StateOnly="true"/>')
    return "".join(parts)
