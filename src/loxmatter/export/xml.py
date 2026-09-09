# loxmatter - connects Matter devices to a Loxone Miniserver.
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

"""Builds Loxone template files as bytes.

Deliberately without an XML library: Loxone Config is picky about the
format, and the verified reference implementation also builds the files
as text. A serialiser might reorder attributes or write the declaration
differently, and nothing here could check that.

This module knows nothing about Matter. It only knows what a Loxone
template looks like.
"""

from __future__ import annotations

from collections.abc import Sequence

BOM = "\ufeff"
CRLF = "\r\n"
DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'

Attrs = Sequence[tuple[str, str]]


def escape_attr_value(value: str) -> str:
    """Escapes an attribute value for double-quoted XML attributes.

    Deliberately no library: ``xml.sax.saxutils.quoteattr`` switches
    between single and double quotes depending on the content, and then
    leaves double quotes unescaped. Loxone templates quote attributes with
    double quotes throughout, so this is hard-wired here.
    """
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_attrs(attrs: Attrs) -> str:
    return " ".join(f'{name}="{escape_attr_value(value)}"' for name, value in attrs)


def render_document(
    root: str,
    root_attrs: Attrs,
    children: Sequence[tuple[str, Attrs]],
) -> bytes:
    """Produces a template file: UTF-8 with BOM, CRLF, one child per line."""
    lines = [DECLARATION, f"<{root} {render_attrs(root_attrs)}>"]
    lines += [f"\t<{tag} {render_attrs(attrs)}/>" for tag, attrs in children]
    lines.append(f"</{root}>")
    return (BOM + CRLF.join(lines) + CRLF).encode("utf-8")
