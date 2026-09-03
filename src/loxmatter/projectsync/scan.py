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

"""Liest eine Loxone-Projektdatei als Baum aus `<C>`-Elementen, mit exakten
Byte-Spans statt eines XML-Baums.

Bewusst kein `xml.etree.ElementTree` fuer irgendetwas, das spaeter
geschrieben wird (siehe Entwurf `docs/superpowers/specs/
2026-09-03-projektdatei-sync-design.md`, Abschnitt 3.2): ein XML-Serialisierer
duerfte Attribute umsortieren oder anders schreiben, ohne dass sich das hier
nachpruefen liesse, und ein 3-MB-Projekt enthaelt weit mehr Bausteintypen als
dieses Projekt kennt. `Element.open_start`/`open_end`/`inner_end`/`outer_end`
sind deshalb der eigentliche Zweck dieses Moduls: exakte Positionen, an denen
`projectsync.patch` spaeter chirurgisch schreibt.

Nur `<C ...>`-Elemente werden hier verstanden. Alles andere (`Co`, `In`,
`IoData`, `Display`, ...) bleibt fuer dieses Modul unsichtbarer Text
innerhalb des Inhalts eines `<C>`-Elements."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_OPEN_OR_SELFCLOSE = re.compile(r"<C(?=[\s/>])")
_ATTR = re.compile(
    r'([A-Za-z_][\w]*)="((?:[^"&]|&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)*)"'
)
_CONTROL_LIST_OPEN = re.compile(r"<ControlList\b[^>]*>")


class ProjectFormatError(ValueError):
    """Die hochgeladene Datei ist keine (verstandene) Loxone-Projektdatei."""


@dataclass
class Element:
    attrs: dict[str, str]
    open_start: int
    open_end: int
    self_closing: bool
    # inner_end/children sind None/leer bei einem selbstschliessenden Element.
    inner_end: int | None
    outer_end: int
    children: list[Element] = field(default_factory=list)

    @property
    def type(self) -> str | None:
        return self.attrs.get("Type")


def parse_attrs(tag_text: str) -> dict[str, str]:
    """Liest alle `name="wert"`-Paare aus einem einzelnen Start-Tag-Text und
    entschaerft die fuenf XML-Standard-Escapes."""
    attrs: dict[str, str] = {}
    for match in _ATTR.finditer(tag_text):
        name, raw = match.group(1), match.group(2)
        value = (
            raw.replace("&quot;", '"')
            .replace("&apos;", "'")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
        )
        attrs[name] = value
    return attrs


def _find_tag_close(text: str, start: int) -> int:
    """Findet das '>', das ein Start-Tag wirklich beendet - nicht das erste
    '>' im Text danach. XML verlangt kein Escaping von '>' in Attributwerten
    (anders als '<', '&' und das Anfuehrungszeichen selbst), ein Wert wie
    `Title="Temp > 20"` ist gueltiges, unescaped XML. Ein '"' schaltet die
    Erkennung um - ein woertliches Anfuehrungszeichen INNERHALB eines Werts
    waere selbst escaped (`&quot;`), zaehlt hier also nicht als Umschalter."""
    in_quotes = False
    pos = start
    while True:
        char = text[pos]
        if char == '"':
            in_quotes = not in_quotes
        elif char == ">" and not in_quotes:
            return pos
        pos += 1


def _skip_element(text: str, open_start: int) -> tuple[int, int, bool]:
    """Ausgehend vom `<` eines `<C>`-Elements: liefert `(inner_end, outer_end,
    self_closing)`. Laeuft token-weise vorwaerts (naechstes `<C...>` oder
    naechstes `</C>`, je nachdem was zuerst kommt) und haelt dabei die
    Verschachtelungstiefe nach, um das WIRKLICH passende `</C>` zu finden,
    nicht nur das naechste im Dokument."""
    tag_close = _find_tag_close(text, open_start)
    self_closing = text[tag_close - 1] == "/"
    open_end = tag_close + 1
    if self_closing:
        return open_end, open_end, True

    depth = 1
    pos = open_end
    while depth > 0:
        next_open = _OPEN_OR_SELFCLOSE.search(text, pos)
        next_close_pos = text.find("</C>", pos)
        if next_close_pos == -1:
            raise ProjectFormatError("Unerwartetes Dateiende: <C> ohne schliessendes </C>.")
        if next_open is not None and next_open.start() < next_close_pos:
            inner_tag_close = _find_tag_close(text, next_open.end())
            inner_self_closing = text[inner_tag_close - 1] == "/"
            pos = inner_tag_close + 1
            if not inner_self_closing:
                depth += 1
        else:
            depth -= 1
            pos = next_close_pos + len("</C>")
    inner_end = pos - len("</C>")
    return inner_end, pos, False


def scan_children(text: str, start: int, end: int) -> list[Element]:
    """Alle direkten `<C>`-Kinder im Bereich `[start, end)`, rekursiv mit
    ihren eigenen `<C>`-Kindern gefuellt."""
    children: list[Element] = []
    pos = start
    while True:
        match = _OPEN_OR_SELFCLOSE.search(text, pos, end)
        if match is None:
            break
        open_start = match.start()
        tag_close = _find_tag_close(text, open_start)
        tag_text = text[open_start : tag_close + 1]
        attrs = parse_attrs(tag_text)
        inner_end, outer_end, self_closing = _skip_element(text, open_start)
        open_end = open_start + len(tag_text)
        element_children = [] if self_closing else scan_children(text, open_end, inner_end)
        children.append(
            Element(
                attrs=attrs,
                open_start=open_start,
                open_end=open_end,
                self_closing=self_closing,
                inner_end=None if self_closing else inner_end,
                outer_end=outer_end,
                children=element_children,
            )
        )
        pos = outer_end
    return children


def parse_root(text: str) -> tuple[dict[str, str], int, int, int]:
    """Findet das `<ControlList ...>`-Wurzelelement.

    Liefert `(attrs, open_start, open_end, close_start)` — `close_start` ist
    die Position von `</ControlList>`, also das Ende des Inhaltsbereichs, in
    dem `scan_children` die Top-Level-`<C>`-Elemente sucht."""
    match = _CONTROL_LIST_OPEN.search(text)
    if match is None:
        raise ProjectFormatError(
            "Keine gueltige Loxone-Projektdatei: <ControlList>-Wurzelelement fehlt."
        )
    close_start = text.rfind("</ControlList>")
    if close_start == -1:
        raise ProjectFormatError(
            "Keine gueltige Loxone-Projektdatei: </ControlList> fehlt."
        )
    return parse_attrs(match.group(0)), match.start(), match.end(), close_start
