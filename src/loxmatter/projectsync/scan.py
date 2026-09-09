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

"""Reads a Loxone project file as a tree of `<C>` elements, with exact byte
spans instead of an XML tree.

Deliberately no `xml.etree.ElementTree` for anything that gets written
back later (see design `docs/superpowers/specs/
2026-09-03-project-file-sync-design.md`, section 3.2): an XML serialiser
might reorder attributes or write them differently, with no way to check
that here, and a 3 MB project contains far more block types than this
project knows about. `Element.open_start`/`open_end`/`inner_end`/
`outer_end` are therefore this module's actual purpose: exact positions
where `projectsync.patch` later writes surgically.

Only `<C ...>` elements are understood here. Everything else (`Co`, `In`,
`IoData`, `Display`, ...) remains invisible text to this module, inside
the content of a `<C>` element."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loxmatter import i18n

_OPEN_OR_SELFCLOSE = re.compile(r"<C(?=[\s/>])")
_ATTR = re.compile(r'([A-Za-z_][\w]*)="((?:[^"&]|&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)*)"')
_CONTROL_LIST_OPEN = re.compile(r"<ControlList\b[^>]*>")


class ProjectFormatError(ValueError):
    """The uploaded file is not a (recognised) Loxone project file."""


@dataclass
class Element:
    attrs: dict[str, str]
    open_start: int
    open_end: int
    self_closing: bool
    # inner_end/children are None/empty for a self-closing element.
    inner_end: int | None
    outer_end: int
    children: list[Element] = field(default_factory=list)

    @property
    def type(self) -> str | None:
        return self.attrs.get("Type")


def parse_attrs(tag_text: str) -> dict[str, str]:
    """Reads all `name="value"` pairs from a single start-tag text and
    resolves the five standard XML escapes."""
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
    """Finds the '>' that really ends a start tag - not the first '>' in the
    text after it. XML does not require escaping '>' in attribute values
    (unlike '<', '&' and the quote character itself); a value such as
    `Title="Temp > 20"` is valid, unescaped XML. A '"' toggles the
    detection - a literal quote INSIDE a value would itself be escaped
    (`&quot;`), so it does not count as a toggle here.

    If the search runs past the end of the text (a truncated file, a
    quote that never closes), that is a format error in the uploaded
    file - not a bare `IndexError` that would reach the user as an HTTP
    500 (design section 8)."""
    in_quotes = False
    pos = start
    while pos < len(text):
        char = text[pos]
        if char == '"':
            in_quotes = not in_quotes
        elif char == ">" and not in_quotes:
            return pos
        pos += 1
    raise ProjectFormatError(i18n.t("projectsync.unexpected_eof_attribute"))


def _skip_element(text: str, open_start: int) -> tuple[int, int, bool]:
    """Starting from the `<` of a `<C>` element: returns `(inner_end,
    outer_end, self_closing)`. Advances token by token (next `<C...>` or
    next `</C>`, whichever comes first) while tracking nesting depth, to
    find the `</C>` that REALLY matches, not just the next one in the
    document."""
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
            raise ProjectFormatError(i18n.t("projectsync.unexpected_eof_control"))
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
    """All direct `<C>` children in the range `[start, end)`, filled
    recursively with their own `<C>` children."""
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
    """Finds the `<ControlList ...>` root element.

    Returns `(attrs, open_start, open_end, close_start)` — `close_start` is
    the position of `</ControlList>`, i.e. the end of the content range in
    which `scan_children` looks for the top-level `<C>` elements."""
    match = _CONTROL_LIST_OPEN.search(text)
    if match is None:
        raise ProjectFormatError(i18n.t("projectsync.missing_control_list_open"))
    close_start = text.rfind("</ControlList>")
    if close_start == -1:
        raise ProjectFormatError(i18n.t("projectsync.missing_control_list_close"))
    return parse_attrs(match.group(0)), match.start(), match.end(), close_start
