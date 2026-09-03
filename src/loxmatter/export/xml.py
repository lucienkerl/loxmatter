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

"""Baut Loxone-Vorlagendateien als Bytes.

Absichtlich ohne XML-Bibliothek: Loxone Config ist beim Format waehlerisch, und
die verifizierte Referenzimplementierung baut die Dateien ebenfalls als Text.
Ein Serialisierer duerfte Attribute umsortieren oder die Deklaration anders
schreiben, was hier niemand nachpruefen kann.

Dieses Modul kennt kein Matter. Es weiss nur, wie eine Loxone-Vorlage aussieht.
"""

from __future__ import annotations

from collections.abc import Sequence

BOM = "\ufeff"
CRLF = "\r\n"
DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'

Attrs = Sequence[tuple[str, str]]


def _escape_attr_value(value: str) -> str:
    """Escaped einen Attributwert fuer doppelt gequotete XML-Attribute.

    Absichtlich keine Bibliothek: ``xml.sax.saxutils.quoteattr`` wechselt je
    nach Inhalt zwischen einfachen und doppelten Anfuehrungszeichen und laesst
    doppelte Anfuehrungszeichen dann unescaped. Loxone-Vorlagen quoten
    Attribute durchgehend doppelt, deshalb hier fest verdrahtet.
    """
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _render_attrs(attrs: Attrs) -> str:
    return " ".join(f'{name}="{_escape_attr_value(value)}"' for name, value in attrs)


def render_document(
    root: str,
    root_attrs: Attrs,
    children: Sequence[tuple[str, Attrs]],
) -> bytes:
    """Erzeugt eine Vorlagendatei: UTF-8 mit BOM, CRLF, ein Kind je Zeile."""
    lines = [DECLARATION, f"<{root} {_render_attrs(root_attrs)}>"]
    lines += [f"\t<{tag} {_render_attrs(attrs)}/>" for tag, attrs in children]
    lines.append(f"</{root}>")
    return (BOM + CRLF.join(lines) + CRLF).encode("utf-8")
