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

# `Unit` steht hier bewusst NICHT (Korrektur nach Anwenderbericht "die
# Einheit ist bei den virtuellen Eingaengen nicht mehr dabei", 2026-09-05):
# in einer echten Projektdatei traegt kein einziges `<C>`-Objekt ein
# `Unit`-Attribut (an allen 3710 der Referenzdatei geprueft) - die Einheit
# steht ausschliesslich im `<Display>`-Kind, siehe `new_cmd_children_xml`.
# Ein hier gepflegtes `Unit` schriebe es an eine Stelle, an der Loxone
# Config es nie liest, und liesse jeden analogen Eingang bei jedem Lauf
# erneut als "aktualisiert" erscheinen.
MANAGED_INPUT_CMD_ATTRS: tuple[str, ...] = ("Title", "Check", "Analog")
MANAGED_OUTPUT_CMD_ATTRS: tuple[str, ...] = ("Title", "CmdOn", "CmdOff", "Analog")

_IODATA = re.compile(r"<IoData\s+([^/]*)/>")


def desired_input_cmd_attrs(entry: LoxoneInput) -> dict[str, str]:
    """Soll-Zustand der vom Update verwalteten Attribute eines bestehenden
    `VirtualUdpInCmd` (Entwurf Abschnitt 5) - ohne `Unit`, siehe
    `MANAGED_INPUT_CMD_ATTRS`."""
    return {
        "Title": entry.title,
        "Check": f"{entry.key}:{entry.check_suffix}",
        "Analog": "true" if entry.analog else "false",
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
    virtual_in_udp_cmd_attributes`), ergaenzt um `Type`/`IName`/`V`/`U`/`Nio`/
    `WF`, die eine Projektdatei zusaetzlich braucht.

    **`V="178"` (Korrektur nach echtem Praxistest, 2026-09-05):** eine
    frueher fehlende Pflichtangabe - gegen die echte Referenzdatei geprueft,
    tragen dort ALLE 3710 `<C>`-Objekte ohne Ausnahme ein `V`-Attribut (fast
    immer `"178"`, nur das `Document`-Wurzelobjekt selbst traegt die volle
    Loxone-Config-Versionsnummer). Ohne `V` legte `_new_device_edit` zwar den
    Geraete-Container sichtbar an, dessen Kommando-Kinder blieben in Loxone
    Config aber leer - der vom Anwender gemeldete Fehler, der zur
    Ueberpruefung gegen die echte Datei gefuehrt hat.

    **Ohne `Unit` (Korrektur nach Anwenderbericht, 2026-09-05):** die
    Vorlagendatei fuehrt die Einheit als Attribut, eine Projektdatei nicht -
    dort steht sie im `<Display>`-Kind (`new_cmd_children_xml`), und kein
    einziges `<C>`-Objekt der Referenzdatei traegt ein `Unit`-Attribut. Aus
    der uebernommenen Vorlagen-Attributliste wird es deshalb hier wieder
    herausgefiltert; sonst landete die Einheit an einer Stelle, an der
    Loxone Config sie nie liest, und fehlte am Eingang."""
    attrs = [
        ("Type", "VirtualUdpInCmd"),
        ("IName", iname),
        ("V", "178"),
        ("U", u),
        *((name, value) for name, value in virtual_in_udp_cmd_attributes(entry) if name != "Unit"),
        ("Nio", "2"),
        ("WF", "16400"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_output_cmd_open_tag(command: LoxoneCommand, iname: str, u: str) -> str:
    """Wie `new_input_cmd_open_tag`, fuer `VirtualOutCmd` - auf
    `export.documents.virtual_out_cmd_attributes`, ebenfalls mit `V="178"`."""
    attrs = [
        ("Type", "VirtualOutCmd"),
        ("IName", iname),
        ("V", "178"),
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
    nur fuer den Experimentell-Pfad (Entwurf Abschnitt 3.4). Traegt seit der
    Korrektur oben ebenfalls `V="178"`, wie jeder andere `<C>`-Knoten in der
    echten Referenzdatei."""
    attrs = [
        ("Type", "VirtualUdpIn"),
        ("IName", iname),
        ("V", "178"),
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
        ("V", "178"),
        ("U", u),
        ("Title", f"Matter — {device_label}"),
        ("WF", "16384"),
        ("Address", base_url),
        ("CloseAfterSend", "true"),
        ("CmdSep", ";"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_caption_open_tag(kind: str, u: str) -> str:
    """Start-Tag eines frisch angelegten `VirtualInCaption`/`VirtualOutCaption`
    - nur, wenn die Projektdatei noch nie einen virtuellen Ein- bzw. Ausgang
    dieser Art hatte (Entwurf Abschnitt 8: Sonderfall der Neuanlage, ebenfalls
    hinter dem Experimentell-Haken).

    **Korrektur nach echtem Praxistest (2026-09-05):** alle vier
    `VirtualInCaption`/`VirtualOutCaption`-Objekte in der echten
    Referenzdatei tragen KEIN `IName` (anders als urspruenglich angenommen -
    das `C<n>`-Namensmuster gehoert zu anderen Objekttypen), dafuer aber
    `V="178"` und ein festes `Title` (`"Virtuelle Eingänge"`/`"Virtuelle
    Ausgänge"`, so wie Loxone Config selbst neu angelegte Captions
    beschriftet) plus `WF="16384"`, wie die Geraete-Container darunter. Kein
    `iname`-Parameter mehr - eine Caption braucht keinen."""
    if kind not in ("input", "output"):
        raise ValueError(f"Unbekannte Art {kind!r} - erwartet 'input' oder 'output'.")
    type_name = "VirtualInCaption" if kind == "input" else "VirtualOutCaption"
    title = "Virtuelle Eingänge" if kind == "input" else "Virtuelle Ausgänge"
    attrs = [
        ("Type", type_name),
        ("V", "178"),
        ("U", u),
        ("Title", title),
        ("WF", "16384"),
    ]
    return f"<C {render_attrs(attrs)}>"


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


_DEFAULT_UNIT_FORMAT = "<v.1>"


def new_cmd_children_xml(
    *,
    kind: str,
    existing_u: set[str],
    iodata_attrs: dict[str, str] | None,
    analog: bool = False,
    unit_format: str = "",
) -> str:
    """XML-Text der Kind-Elemente eines frisch angelegten Cmd-Objekts:
    Verdrahtungs-Stummel (zwei fuer einen Eingang - `AQ`/`Q` -, einer fuer
    einen Ausgang - `I`), optional ein `IoData`-Element mit uebernommenen
    Berechtigungswerten, und ein `Display`-Element (Entwurf Abschnitt 6).
    `kind` ist ``"input"`` oder ``"output"``.

    **`analog`/`unit_format` (Korrektur nach Anwenderbericht "die Einheit ist
    bei den virtuellen Eingaengen nicht mehr dabei", 2026-09-05):** das
    `Display`-Element ist der einzige Ort, an dem eine Projektdatei die
    Einheit fuehrt - als kompletter Formatstring inklusive Einheitentext
    (`<v.3> kW`), begleitet von `Type="2"` bei einem analogen Wert. So steht
    es an allen 86 analogen Eingaengen der Referenzdatei; ein festes
    `Unit="<v.1>"` wie zuvor warf die Einheit jedes Signals weg. Ist
    `unit_format` leer (analoges Signal ohne bekannte Einheit, siehe
    `profiles.table.unit_format`), bleibt der reine Formatstring - ein
    leeres `Unit=""` kommt in der Referenzdatei nirgends vor."""
    if kind == "input":
        connectors = [
            f'<Co K="AQ" U="{new_unique_id(existing_u)}"/>',
            f'<Co K="Q" U="{new_unique_id(existing_u)}"/>',
        ]
    elif kind == "output":
        connectors = [f'<Co K="I" U="{new_unique_id(existing_u)}"/>']
    else:
        raise ValueError(f"Unbekannte Art {kind!r} - erwartet 'input' oder 'output'.")

    display_attrs: list[tuple[str, str]] = []
    if analog:
        # `Type="2"` steht in der Referenzdatei ausnahmslos bei analogen
        # Werten - digitale bleiben ohne, deshalb kein fester Wert hier.
        display_attrs.append(("Type", "2"))
    display_attrs.append(("Unit", unit_format or _DEFAULT_UNIT_FORMAT))
    display_attrs.append(("StateOnly", "true"))

    parts = list(connectors)
    if iodata_attrs:
        parts.append(f"<IoData {render_attrs(list(iodata_attrs.items()))}/>")
    parts.append(f"<Display {render_attrs(display_attrs)}/>")
    return "".join(parts)
