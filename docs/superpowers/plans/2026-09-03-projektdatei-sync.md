# Projektdatei-Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine hochgeladene Loxone-Projektdatei automatisch gegen die gespeicherten Geräte/Signale abgleichen und eine gepatchte Fassung liefern, in der bestehende virtuelle Ein-/Ausgänge aktualisiert (nicht ersetzt) und fehlende neu angelegt sind.

**Architecture:** Neues Paket `loxmatter.projectsync` liest die Projektdatei nur lesend (eigener Byte-Span-Scanner für `<C>`-Elemente, kein XML-Reserialisierer), baut einen Diff-Plan gegen `Store`, und schreibt Änderungen als gezielte Textersetzungen auf den Original-Bytes zurück. Ein neuer API-Router (`POST /api/export/project-sync`) liefert Plan und beide Datei-Varianten (mit/ohne neue Geräte-Container) in einer Antwort; das WebUI zeigt den Plan zur Bestätigung, bevor ein Download möglich ist.

**Tech Stack:** Python (FastAPI-Router, Pydantic-Modelle, sqlite-gestützter `Store`), Vanilla-JS/Alpine.js im bestehenden `web/app.js`.

## Global Constraints

- Kein Reverse-Engineering des Miniserver-Upload-Protokolls, keine Live-Verbindung zum Miniserver für dieses Feature (Spec Abschnitt 2/3.1).
- Original-Datei wird nie überschrieben; jede Antwort liefert nur neue Bytes (Spec Abschnitt 4).
- Schreiben ausschließlich als Textersetzung auf dem Original-Byte-Strom, nie über einen XML-Serialisierer (Spec Abschnitt 3.2).
- Abgleich über den in `Check`/`CmdOn` bereits vorhandenen `loxmatter`-Schlüssel, nicht über den Titel (Spec Abschnitt 3.3).
- Kein automatisches Löschen und kein automatisches Verdrahten auf Funktionsbausteine (Spec Abschnitt 2).
- Neue Geräte-Container nur, wenn `include_new_devices=True` explizit gesetzt ist (Spec Abschnitt 3.4/6).
- Jede neue Datei muss deutschsprachige Fehlermeldungen/Docstrings im Stil des restlichen Repos tragen (siehe bestehende Module).

---

## Vorarbeiten: geteilte Bausteine

### Task 1: `export/xml.py` — Escaping-Helfer öffentlich machen

**Files:**
- Modify: `src/loxmatter/export/xml.py`
- Test: `tests/export/test_xml.py`

**Interfaces:**
- Produces: `escape_attr_value(value: str) -> str`, `render_attrs(attrs: Attrs) -> str` (öffentlich, vorher `_escape_attr_value`/`_render_attrs`) — `projectsync` braucht dieselbe Escaping-Logik wie die Vorlagendateien, damit beide Schreibpfade nicht auseinanderlaufen.

- [ ] **Step 1: Write the failing test**

Füge in `tests/export/test_xml.py` an:

```python
from loxmatter.export.xml import escape_attr_value, render_attrs


def test_escape_attr_value_is_importable_and_escapes_quotes():
    assert escape_attr_value('a"b&c<d>e') == "a&quot;b&amp;c&lt;d&gt;e"


def test_render_attrs_is_importable():
    assert render_attrs([("A", "1"), ("B", 'x"y')]) == 'A="1" B="x&quot;y"'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_xml.py -v`
Expected: FAIL mit `ImportError: cannot import name 'escape_attr_value'`

- [ ] **Step 3: Rename in der Implementierung**

In `src/loxmatter/export/xml.py`: benenne `_escape_attr_value` zu `escape_attr_value` und `_render_attrs` zu `render_attrs` um (beide Funktionsköpfe und alle internen Aufrufstellen in `_render_attrs`/`render_document`). Verhalten bleibt unverändert — reines Umbenennen.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_xml.py -v`
Expected: PASS, alle bisherigen Tests in dieser Datei weiterhin PASS (sie riefen zuvor nur `render_document` auf, nicht die privaten Namen direkt).

- [ ] **Step 5: Ganze Testsuite laufen lassen**

Run: `uv run pytest -q`
Expected: PASS — keine andere Stelle im Repo importiert `_escape_attr_value`/`_render_attrs` direkt (nur `export/xml.py` selbst).

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/export/xml.py tests/export/test_xml.py
git commit -m "refactor(export): XML-Escaping-Helfer oeffentlich machen fuer projectsync"
```

---

### Task 2: `export/documents.py` — Attribut-Bausteine pro Kommando isolieren

**Files:**
- Modify: `src/loxmatter/export/documents.py`
- Test: `tests/export/test_documents.py`

**Interfaces:**
- Consumes: `LoxoneInput` (aus `export/signals.py`), `LoxoneCommand` (dieselbe Datei)
- Produces: `virtual_in_udp_cmd_attributes(entry: LoxoneInput) -> list[tuple[str, str]]` (neu, extrahiert aus `render_virtual_in_udp`), `virtual_out_cmd_attributes(command: LoxoneCommand) -> list[tuple[str, str]]` (vorher `_virtual_out_cmd_attributes`, umbenannt) — `projectsync.schema` baut neue Projekt-Objekte auf denselben, bereits gegen einen echten Import verifizierten Attributlisten auf, statt sie ein zweites Mal zu erfinden.

- [ ] **Step 1: Write the failing test**

Füge in `tests/export/test_documents.py` an:

```python
from loxmatter.export.documents import virtual_in_udp_cmd_attributes, virtual_out_cmd_attributes


def test_virtual_in_udp_cmd_attributes_matches_rendered_output():
    entry = LoxoneInput("d1_1_temp", "Temperatur", "Wohnzimmer · 1/1026/0", True, "<v.1> °C")
    attrs = dict(virtual_in_udp_cmd_attributes(entry))
    assert attrs["Title"] == "Temperatur"
    assert attrs["Check"] == "d1_1_temp:\\v"
    assert attrs["Analog"] == "true"
    assert attrs["Unit"] == "<v.1> °C"


def test_virtual_out_cmd_attributes_is_importable():
    command = LoxoneCommand("d1_1_on", "on", "/cmd/d1_1_on/1", False)
    attrs = dict(virtual_out_cmd_attributes(command))
    assert attrs["CmdOn"] == "/cmd/d1_1_on/1"
    assert attrs["Analog"] == "true"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_documents.py -v`
Expected: FAIL mit `ImportError`

- [ ] **Step 3: Implementierung**

In `src/loxmatter/export/documents.py`:

1. Benenne `_virtual_out_cmd_attributes` zu `virtual_out_cmd_attributes` um (Funktionskopf und den einen Aufruf in `render_virtual_out`).
2. Extrahiere die Attributliste aus der List-Comprehension in `render_virtual_in_udp` in eine neue Funktion:

```python
def virtual_in_udp_cmd_attributes(entry: LoxoneInput) -> list[tuple[str, str]]:
    """Attribute eines einzelnen `VirtualInUdpCmd` — isoliert aus
    `render_virtual_in_udp`, damit `projectsync.schema` dieselbe, bereits
    gegen einen echten Import verifizierte Attributliste fuer neu in die
    Projektdatei eingefuegte Objekte wiederverwenden kann, statt sie ein
    zweites Mal zu erfinden."""
    return [
        ("Title", entry.title),
        ("Comment", entry.comment),
        ("Address", ""),
        ("Check", f"{entry.key}:{entry.check_suffix}"),
        ("Signed", "true"),
        ("Analog", _flag(entry.analog)),
        ("SourceValLow", "0"),
        ("DestValLow", "0"),
        ("SourceValHigh", "100"),
        ("DestValHigh", "100"),
        ("DefVal", "0"),
        ("MinVal", "-2147483647"),
        ("MaxVal", "2147483647"),
        ("Unit", entry.unit_format),
        ("HintText", ""),
    ]
```

3. Passe `render_virtual_in_udp` an, diese Funktion zu nutzen statt der Inline-Liste:

```python
def render_virtual_in_udp(
    device_label: str,
    bridge_ip: str,
    port: int,
    inputs: Sequence[LoxoneInput],
) -> bytes:
    info = ("Info", [("templateType", "1"), ("minVersion", _MIN_VERSION)])
    children = [("VirtualInUdpCmd", virtual_in_udp_cmd_attributes(entry)) for entry in inputs]
    return render_document(
        "VirtualInUdp",
        [
            ("Title", f"Matter — {device_label}"),
            ("Comment", "erzeugt von loxmatter"),
            ("Address", bridge_ip),
            ("Port", str(port)),
        ],
        [info, *children],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_documents.py -v`
Expected: PASS — inklusive aller vorher bestehenden Tests in dieser Datei (das Rendering-Ergebnis ist byteidentisch zu vorher, nur der Weg dahin ist jetzt zweigeteilt).

- [ ] **Step 5: Ganze Testsuite laufen lassen**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/export/documents.py tests/export/test_documents.py
git commit -m "refactor(export): Kommando-Attribute isoliert wiederverwendbar machen"
```

---

## Kern: `loxmatter.projectsync`

### Task 3: `projectsync/scan.py` — Byte-Span-Scanner für `<C>`-Elemente

**Files:**
- Create: `src/loxmatter/projectsync/__init__.py`
- Create: `src/loxmatter/projectsync/scan.py`
- Test: `tests/projectsync/test_scan.py`

**Interfaces:**
- Produces: `@dataclass Element(attrs: dict[str,str], open_start: int, open_end: int, self_closing: bool, inner_end: int | None, outer_end: int, children: list[Element])` mit Property `type -> str | None`; `parse_attrs(tag_text: str) -> dict[str, str]`; `scan_children(text: str, start: int, end: int) -> list[Element]`; `parse_root(text: str) -> tuple[dict[str, str], int, int, int]` (root_attrs, root_open_start, root_open_end, root_close_start) — spätere Tasks (`index.py`, `patch.py`) navigieren und schreiben ausschließlich über diese Spans, nie über einen XML-Reserialisierer.

- [ ] **Step 1: Write the failing test**

Create `src/loxmatter/projectsync/__init__.py` (leer, mit Lizenzkopf wie jede andere Datei im Projekt — siehe z. B. `src/loxmatter/export/__init__.py`).

Create `tests/projectsync/test_scan.py`:

```python
from loxmatter.projectsync.scan import parse_attrs, parse_root, scan_children

NESTED = (
    '<C Type="VirtualUdpIn" IName="VUI1" U="u-container" Title="Geraet">'
    '<C Type="VirtualUdpInCmd" IName="VCI1" U="u-cmd1" Check="d1_1_on:\\v" Title="An">'
    '<Co K="Q" U="u-co1"/>'
    "</C>"
    '<C Type="VirtualUdpInCmd" IName="VCI2" U="u-cmd2" Check="d1_1_off:1" Title="Aus"/>'
    "</C>"
)

ROOT_DOC = f'<?xml version="1.0" encoding="utf-8"?>\r\n<ControlList Version="275" NextObj="10">{NESTED}</ControlList>\r\n'


def test_parse_attrs_reads_and_unescapes_values():
    attrs = parse_attrs('<C Type="A" Title="a &amp; b"/>')
    assert attrs == {"Type": "A", "Title": "a & b"}


def test_scan_children_finds_one_top_level_element():
    [container] = scan_children(NESTED, 0, len(NESTED))
    assert container.type == "VirtualUdpIn"
    assert container.attrs["IName"] == "VUI1"
    assert not container.self_closing


def test_scan_children_finds_nested_container_and_leaf():
    [container] = scan_children(NESTED, 0, len(NESTED))
    assert len(container.children) == 2
    cmd1, cmd2 = container.children
    assert cmd1.type == "VirtualUdpInCmd"
    assert cmd1.attrs["Check"] == "d1_1_on:\\v"
    assert not cmd1.self_closing
    assert cmd2.self_closing
    assert cmd2.attrs["Check"] == "d1_1_off:1"


def test_element_spans_point_at_the_right_substrings():
    [container] = scan_children(NESTED, 0, len(NESTED))
    cmd1 = container.children[0]
    assert NESTED[cmd1.open_start : cmd1.open_end].startswith('<C Type="VirtualUdpInCmd"')
    assert NESTED[cmd1.inner_end : cmd1.inner_end + 4] == "</C>"
    assert NESTED[cmd1.outer_end - 4 : cmd1.outer_end] == "</C>"


def test_parse_root_finds_control_list_and_content_bounds():
    attrs, open_start, open_end, close_start = parse_root(ROOT_DOC)
    assert attrs["NextObj"] == "10"
    assert ROOT_DOC[open_start:open_end].startswith("<ControlList ")
    assert ROOT_DOC[open_end:close_start] == NESTED
    assert ROOT_DOC[close_start:].startswith("</ControlList>")


def test_parse_root_raises_on_missing_control_list():
    import pytest
    from loxmatter.projectsync.scan import ProjectFormatError

    with pytest.raises(ProjectFormatError):
        parse_root("<NotAProject/>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_scan.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.projectsync'`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/scan.py`:

```python
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
_ATTR = re.compile(r'([A-Za-z_][\w]*)="((?:[^"&]|&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)*)"')
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
    children: list["Element"] = field(default_factory=list)

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


def _skip_element(text: str, open_start: int) -> tuple[int, int, bool]:
    """Ausgehend vom `<` eines `<C>`-Elements: liefert `(inner_end, outer_end,
    self_closing)`. Laeuft token-weise vorwaerts (naechstes `<C...>` oder
    naechstes `</C>`, je nachdem was zuerst kommt) und haelt dabei die
    Verschachtelungstiefe nach, um das WIRKLICH passende `</C>` zu finden,
    nicht nur das naechste im Dokument."""
    tag_close = text.index(">", open_start)
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
            inner_tag_close = text.index(">", next_open.end())
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
        tag_close = text.index(">", open_start)
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
        raise ProjectFormatError("Keine gueltige Loxone-Projektdatei: </ControlList> fehlt.")
    return parse_attrs(match.group(0)), match.start(), match.end(), close_start
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/test_scan.py -v`
Expected: PASS (7 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/__init__.py src/loxmatter/projectsync/scan.py tests/projectsync/test_scan.py
git commit -m "feat(projectsync): Byte-Span-Scanner fuer C-Elemente der Projektdatei"
```

---

### Task 4: `projectsync/keys.py` — Signal-/Kommando-Schlüssel aus `Check`/`CmdOn` lesen

**Files:**
- Create: `src/loxmatter/projectsync/keys.py`
- Test: `tests/projectsync/test_keys.py`

**Interfaces:**
- Produces: `key_from_check(check: str) -> str | None`, `key_from_cmd_on(cmd_on: str) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `tests/projectsync/test_keys.py`:

```python
from loxmatter.projectsync.keys import key_from_check, key_from_cmd_on


def test_key_from_check_splits_on_first_colon():
    assert key_from_check("d3_1_onoff:\\v") == "d3_1_onoff"


def test_key_from_check_returns_none_without_colon():
    assert key_from_check("keine ahnung") is None


def test_key_from_cmd_on_reads_our_own_command_path():
    assert key_from_cmd_on("/cmd/d3_1_onoff/1") == "d3_1_onoff"
    assert key_from_cmd_on("/cmd/d3_1_level/<v>") == "d3_1_level"


def test_key_from_cmd_on_ignores_foreign_paths():
    assert key_from_cmd_on("/toggle") is None
    assert key_from_cmd_on("/write?db=loxone") is None
    assert key_from_cmd_on("/cmd/") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_keys.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/keys.py`:

```python
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

"""Liest den von `loxmatter` selbst vergebenen Signal-/Kommando-Schluessel
aus den Feldern, in denen er in der Projektdatei bereits steht (Entwurf
Abschnitt 3.3) - `Check` bei Eingaengen, `CmdOn` bei Ausgaengen. Das ist
derselbe Schluessel, den `model.store._assign_key` vergibt und den
`export.documents`/`export.outputs` in genau diese Felder schreiben."""

from __future__ import annotations

_CMD_PREFIX = "/cmd/"


def key_from_check(check: str) -> str | None:
    """Der Teil vor dem ersten Doppelpunkt in einem Check-Muster, z. B.
    ``"d3_1_onoff:\\v"`` -> ``"d3_1_onoff"``. `None` ohne Doppelpunkt - dann
    stammt das Muster nicht von `loxmatter` (siehe `render_virtual_in_udp`,
    das `Check` immer als ``f"{key}:{suffix}"`` schreibt)."""
    if ":" not in check:
        return None
    return check.split(":", 1)[0]


def key_from_cmd_on(cmd_on: str) -> str | None:
    """Der Schluessel aus einem von `loxmatter` erzeugten Kommandopfad, z. B.
    ``"/cmd/d3_1_onoff/1"`` -> ``"d3_1_onoff"``. `None` fuer jeden Pfad, der
    nicht mit ``/cmd/`` beginnt - das ist der Marker, an dem sich eigene
    Ausgangsbefehle von allen anderen (``/toggle``, ``/write?db=...``)
    unterscheiden (siehe `export.outputs._command_path`)."""
    if not cmd_on.startswith(_CMD_PREFIX):
        return None
    rest = cmd_on[len(_CMD_PREFIX) :]
    key = rest.split("/", 1)[0]
    return key or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/test_keys.py -v`
Expected: PASS (4 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/keys.py tests/projectsync/test_keys.py
git commit -m "feat(projectsync): loxmatter-Schluessel aus Check/CmdOn lesen"
```

---

### Task 5: `projectsync/index.py` — `ProjectIndex` aufbauen

**Files:**
- Create: `src/loxmatter/projectsync/index.py`
- Create: `tests/projectsync/conftest.py`
- Test: `tests/projectsync/test_index.py`

**Interfaces:**
- Consumes: `Element`, `parse_root`, `scan_children`, `ProjectFormatError` (aus `scan.py`); `key_from_check`, `key_from_cmd_on` (aus `keys.py`)
- Produces: `@dataclass ProjectIndex(text, root_attrs, root_open_end, root_close_start, virtual_in_caption: Element | None, virtual_out_caption: Element | None, input_cmds: dict[str, Element], output_cmds: dict[str, Element], input_containers: dict[str, Element], output_containers: dict[str, Element], all_u_values: set[str], all_inames: set[str])`; `build_index(text: str) -> ProjectIndex`

- [ ] **Step 1: Write the failing test**

Create `tests/projectsync/conftest.py` — die synthetische Beispieldatei, die alle folgenden `projectsync`-Tests teilen (kein echtes Nutzerprojekt, siehe Entwurf Abschnitt 9):

```python
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

"""Synthetische Beispiel-Projektdatei fuer `projectsync`-Tests - handgebaut
nach dem in der Referenzdatei beobachteten Schema (Entwurf Abschnitt 3-6),
NICHT die echte vom Anwender gelieferte Datei (bleibt aus Datenschutzgruenden
ausserhalb des Repos, siehe Entwurf Abschnitt 9).

Enthaelt fuer Geraet 1 (``d1_...``) ein bereits bestehendes Eingangssignal
(``d1_1_onoff``, Titel weicht bewusst vom Soll ab - deckt den `updated`-Fall
ab), das dazugehoerige Online-Signal (``d1_online`` - `export.signals.
to_inputs` erzeugt dieses Signal fuer JEDES Geraet automatisch mit; ohne
einen passenden Eintrag hier waere ein Diff-Plan fuer Geraet 1 niemals
`unchanged`, selbst wenn alle uebrigen Signale uebereinstimmen) und ein
bestehendes Ausgangssignal (``d1_1_on``). Geraet 1 hat KEIN ``d1_1_temp`` -
deckt den `new_signal`-Fall ab (Container existiert, Signal fehlt). Geraet 2
existiert in der Datei ueberhaupt nicht - deckt den `new_device`-Fall ab.
``d9_9_verwaist`` gehoert zu keinem bekannten Geraet mehr - deckt den
`orphaned`-Fall ab."""

import pytest

SAMPLE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t<C Type="VirtualUdpIn" IName="VUI1" U="1000-0001-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" U="1000-0002-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Alter Titel" Nio="2" WF="16384" Check="d1_1_onoff:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<Co K="Q" U="1000-0004-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t</C>\r\n"
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI3" U="1000-000e-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Altes Geraet erreichbar" Nio="2" WF="16384" Check="d1_online:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t<Co K="AQ" U="1000-000f-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<Co K="Q" U="1000-0010-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t</C>\r\n"
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI2" U="1000-0007-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Verwaist" Nio="2" WF="16384" Check="d9_9_verwaist:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t<Co K="AQ" U="1000-0008-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<Co K="Q" U="1000-0009-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t<C Type="VirtualOut" IName="VQ1" U="1000-000b-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="http://10.0.0.9:8080"'
    ' CloseAfterSend="true" CmdSep=";">\r\n'
    '\t\t\t<C Type="VirtualOutCmd" IName="VQC1" U="1000-000c-0000-aaaaaaaaaaaaaaaa"'
    ' Title="on" Nio="1" WF="16384" CmdOn="/cmd/d1_1_on/1" CmdOnMethod="1"'
    ' SourceValHigh="10" DestValHigh="10" Tx="false">\r\n'
    '\t\t\t\t<Co K="I" U="1000-000d-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


@pytest.fixture
def sample_project() -> str:
    return SAMPLE_PROJECT
```

Create `tests/projectsync/test_index.py`:

```python
from loxmatter.projectsync.index import ProjectFormatError, build_index


def test_finds_both_captions(sample_project):
    index = build_index(sample_project)
    assert index.virtual_in_caption is not None
    assert index.virtual_in_caption.type == "VirtualInCaption"
    assert index.virtual_out_caption is not None
    assert index.virtual_out_caption.type == "VirtualOutCaption"


def test_indexes_existing_input_cmd_by_key(sample_project):
    index = build_index(sample_project)
    assert "d1_1_onoff" in index.input_cmds
    cmd = index.input_cmds["d1_1_onoff"]
    assert cmd.attrs["Title"] == "Alter Titel"
    container = index.input_containers["d1_1_onoff"]
    assert container.type == "VirtualUdpIn"


def test_indexes_existing_output_cmd_by_key(sample_project):
    index = build_index(sample_project)
    assert "d1_1_on" in index.output_cmds
    assert index.output_cmds["d1_1_on"].attrs["CmdOn"] == "/cmd/d1_1_on/1"


def test_unknown_device_has_no_entry(sample_project):
    index = build_index(sample_project)
    assert "d2_1_onoff" not in index.input_cmds


def test_collects_all_u_values_including_connectors(sample_project):
    index = build_index(sample_project)
    # "1000-0003-0000-bbbbbbbbbbbbbbbb" gehoert zu einem <Co>, keinem <C> -
    # muss trotzdem erfasst sein, sonst waere eine neu erzeugte ID nicht
    # sicher eindeutig.
    assert "1000-0003-0000-bbbbbbbbbbbbbbbb" in index.all_u_values
    assert "1000-0001-0000-aaaaaaaaaaaaaaaa" in index.all_u_values


def test_collects_all_inames(sample_project):
    index = build_index(sample_project)
    assert {"VUI1", "VCI1", "VCI2", "VQ1", "VQC1"} <= index.all_inames


def test_rejects_file_without_control_list():
    import pytest

    with pytest.raises(ProjectFormatError):
        build_index("<NotAProject/>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_index.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/index.py`:

```python
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
3.3/5)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from loxmatter.projectsync.keys import key_from_check, key_from_cmd_on
from loxmatter.projectsync.scan import Element, ProjectFormatError, parse_root, scan_children

__all__ = ["ProjectFormatError", "ProjectIndex", "build_index"]

_U_ATTR = re.compile(r'\bU="([^"]*)"')
_INAME_ATTR = re.compile(r'\bIName="([^"]*)"')


@dataclass
class ProjectIndex:
    text: str
    root_attrs: dict[str, str]
    root_open_end: int
    root_close_start: int
    virtual_in_caption: Element | None
    virtual_out_caption: Element | None
    input_cmds: dict[str, Element]
    output_cmds: dict[str, Element]
    input_containers: dict[str, Element]
    output_containers: dict[str, Element]
    all_u_values: set[str]
    all_inames: set[str]


def build_index(text: str) -> ProjectIndex:
    root_attrs, _root_open_start, root_open_end, root_close_start = parse_root(text)
    top_level = scan_children(text, root_open_end, root_close_start)

    virtual_in_caption = next((e for e in top_level if e.type == "VirtualInCaption"), None)
    virtual_out_caption = next((e for e in top_level if e.type == "VirtualOutCaption"), None)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/ -v`
Expected: PASS (alle Tests aus Task 3, 4, 5)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/index.py tests/projectsync/conftest.py tests/projectsync/test_index.py
git commit -m "feat(projectsync): ProjectIndex ueber bestehende virtuelle Ein-/Ausgaenge"
```

---

### Task 6: `projectsync/ids.py` — neue eindeutige IDs erzeugen

**Files:**
- Create: `src/loxmatter/projectsync/ids.py`
- Test: `tests/projectsync/test_ids.py`

**Interfaces:**
- Produces: `new_unique_id(existing: set[str]) -> str` (mutiert `existing`, fügt die neue ID hinzu), `new_iname(prefix: str, existing: set[str]) -> str` (mutiert `existing`)

- [ ] **Step 1: Write the failing test**

Create `tests/projectsync/test_ids.py`:

```python
import pytest

from loxmatter.projectsync.ids import new_iname, new_unique_id


def test_new_unique_id_reuses_installation_suffix_from_an_existing_id():
    existing = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    new_id = new_unique_id(existing)
    assert new_id.endswith("-aaaaaaaaaaaaaaaa")
    assert new_id in existing  # als vergeben markiert


def test_new_unique_id_never_collides_across_many_calls():
    existing = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    generated = {new_unique_id(existing) for _ in range(500)}
    assert len(generated) == 500  # keine Kollision, keine ID doppelt


def test_new_unique_id_raises_without_any_reference_id():
    with pytest.raises(ValueError):
        new_unique_id(set())


def test_new_iname_finds_next_free_number_skipping_gaps():
    existing = {"VCI1", "VCI3", "VCI4"}
    name = new_iname("VCI", existing)
    assert name == "VCI2"
    assert "VCI2" in existing


def test_new_iname_starts_at_one_for_an_unused_prefix():
    existing: set[str] = set()
    assert new_iname("VQC", existing) == "VQC1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_ids.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/ids.py`:

```python
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

"""Erzeugt neue, eindeutige Objekt-IDs im an der Referenzdatei beobachteten
Format (Entwurf Abschnitt 6) - der unverifizierte Kern dieses Features: ob
Loxone Config eine so erzeugte ID beim Oeffnen klaglos akzeptiert, weiss
niemand vor einem echten Test-Import."""

from __future__ import annotations

import secrets
import time


def _is_hex(value: str) -> bool:
    if not value:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _installation_suffix(existing: set[str]) -> str:
    """Der letzte Bindestrich-Abschnitt einer bestehenden U-ID - wird fuer
    neue IDs uebernommen, damit sie zur selben Projekt-Familie gehoeren
    (Entwurf Abschnitt 6), statt einen eigenen Suffix zu erfinden."""
    for value in existing:
        parts = value.split("-")
        if len(parts) == 4 and all(_is_hex(part) for part in parts):
            return parts[-1]
    raise ValueError(
        "Keine bestehende U-ID im erwarteten Format in der Datei gefunden, aus der "
        "sich ein Installations-Suffix ableiten liesse."
    )


def new_unique_id(existing: set[str]) -> str:
    """Neue U-ID, gegen `existing` eindeutig geprueft und dort sofort
    eingetragen (folgende Aufrufe im selben Lauf kollidieren damit auch
    untereinander nicht)."""
    suffix = _installation_suffix(existing)
    while True:
        millis = int(time.time() * 1000) & 0xFFFFFFFF
        candidate = f"{millis:08x}-{secrets.token_hex(2)}-{secrets.token_hex(2)}-{suffix}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate


def new_iname(prefix: str, existing: set[str]) -> str:
    """Naechste freie Nummer der Form ``<prefix><n>``, z. B. ``VCI2``, wenn
    ``VCI1``/``VCI3``/``VCI4`` schon vergeben sind - zaehlt einfach hoch, bis
    eine freie Nummer gefunden ist, ohne Luecken zu bevorzugen (reale
    Projekte haben nicht-fortlaufende Nummern, sobald einmal etwas geloescht
    wurde, siehe Entwurf Abschnitt 6)."""
    used = {
        int(name[len(prefix) :])
        for name in existing
        if name.startswith(prefix) and name[len(prefix) :].isdigit()
    }
    n = 1
    while n in used:
        n += 1
    candidate = f"{prefix}{n}"
    existing.add(candidate)
    return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/test_ids.py -v`
Expected: PASS (5 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/ids.py tests/projectsync/test_ids.py
git commit -m "feat(projectsync): eindeutige Objekt-IDs fuer neue Elemente erzeugen"
```

---

### Task 7: `projectsync/schema.py` — Soll-Attribute und neue Kind-Elemente

**Files:**
- Create: `src/loxmatter/projectsync/schema.py`
- Test: `tests/projectsync/test_schema.py`

**Interfaces:**
- Consumes: `LoxoneInput`, `LoxoneCommand`, `virtual_in_udp_cmd_attributes`, `virtual_out_cmd_attributes` (aus `export.documents`/`export.signals`); `Element`, `parse_attrs` (aus `scan.py`); `new_unique_id` (aus `ids.py`)
- Produces: `MANAGED_INPUT_CMD_ATTRS: tuple[str, ...]`, `MANAGED_OUTPUT_CMD_ATTRS: tuple[str, ...]`, `desired_input_cmd_attrs(entry: LoxoneInput) -> dict[str, str]`, `desired_output_cmd_attrs(command: LoxoneCommand) -> dict[str, str]`, `new_input_cmd_open_tag(entry: LoxoneInput, iname: str, u: str) -> str`, `new_output_cmd_open_tag(command: LoxoneCommand, iname: str, u: str) -> str`, `sibling_iodata_attrs(text: str, element: Element) -> dict[str, str] | None`, `find_any_iodata_attrs(text: str, caption: Element) -> dict[str, str] | None`, `new_cmd_children_xml(*, kind: str, existing_u: set[str], iodata_attrs: dict[str, str] | None) -> str`, `new_input_container_open_tag(device_label, bridge_ip, port, iname, u) -> str`, `new_output_container_open_tag(device_label, base_url, iname, u) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/projectsync/test_schema.py`:

```python
from loxmatter.export.documents import LoxoneCommand
from loxmatter.export.signals import LoxoneInput
from loxmatter.projectsync.schema import (
    desired_input_cmd_attrs,
    desired_output_cmd_attrs,
    find_any_iodata_attrs,
    new_cmd_children_xml,
    new_input_cmd_open_tag,
    new_input_container_open_tag,
    new_output_cmd_open_tag,
    new_output_container_open_tag,
    sibling_iodata_attrs,
)
from loxmatter.projectsync.scan import scan_children


def test_desired_input_cmd_attrs_covers_only_managed_fields():
    entry = LoxoneInput("d1_1_temp", "Temperatur", "Kommentar", True, "<v.1> °C")
    desired = desired_input_cmd_attrs(entry)
    assert desired == {
        "Title": "Temperatur",
        "Check": "d1_1_temp:\\v",
        "Analog": "true",
        "Unit": "<v.1> °C",
    }


def test_desired_output_cmd_attrs_omits_cmdoff_when_there_is_none():
    command = LoxoneCommand("d1_1_level", "level", "/cmd/d1_1_level/<v>", True)
    desired = desired_output_cmd_attrs(command)
    assert desired == {"Title": "level", "CmdOn": "/cmd/d1_1_level/<v>", "Analog": "true"}


def test_desired_output_cmd_attrs_includes_cmdoff_for_paired_commands():
    command = LoxoneCommand(
        "d1_1_on + d1_1_off", "onoff", "/cmd/d1_1_on/1", False, off_path="/cmd/d1_1_off/1"
    )
    desired = desired_output_cmd_attrs(command)
    assert desired["CmdOff"] == "/cmd/d1_1_off/1"
    assert desired["Analog"] == "false"


def test_new_input_cmd_open_tag_is_a_valid_non_self_closing_start_tag():
    entry = LoxoneInput("d2_1_temp", "Temperatur", "Kommentar", True, "<v.1> °C")
    tag = new_input_cmd_open_tag(entry, "VCI9", "u-new")
    assert tag.startswith('<C Type="VirtualUdpInCmd"')
    assert tag.endswith(">")
    assert not tag.endswith("/>")
    assert 'Check="d2_1_temp:\\v"' in tag
    assert 'IName="VCI9"' in tag
    assert 'U="u-new"' in tag


def test_new_output_cmd_open_tag_contains_command_path():
    command = LoxoneCommand("d2_1_on", "on", "/cmd/d2_1_on/1", False)
    tag = new_output_cmd_open_tag(command, "VQC9", "u-new")
    assert 'CmdOn="/cmd/d2_1_on/1"' in tag
    assert 'IName="VQC9"' in tag


def test_sibling_iodata_attrs_reads_from_an_existing_cmd(sample_project):
    from loxmatter.projectsync.index import build_index

    index = build_index(sample_project)
    cmd = index.input_cmds["d1_1_onoff"]
    attrs = sibling_iodata_attrs(sample_project, cmd)
    assert attrs is not None
    assert attrs["Cr"] == "1000-0005-0000-aaaaaaaaaaaaaaaa"


def test_find_any_iodata_attrs_falls_back_to_any_cmd_under_the_caption(sample_project):
    from loxmatter.projectsync.index import build_index

    index = build_index(sample_project)
    attrs = find_any_iodata_attrs(sample_project, index.virtual_in_caption)
    assert attrs is not None
    assert "Cr" in attrs


def test_new_cmd_children_xml_contains_two_connectors_for_input():
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(kind="input", existing_u=existing_u, iodata_attrs=None)
    assert xml.count('Co K="AQ"') == 1
    assert xml.count('Co K="Q"') == 1
    assert "<IoData" not in xml
    assert "<Display" in xml


def test_new_cmd_children_xml_contains_one_connector_for_output_with_iodata():
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(
        kind="output", existing_u=existing_u, iodata_attrs={"Cr": "x", "Pr": "y"}
    )
    assert xml.count('Co K="I"') == 1
    assert '<IoData Cr="x" Pr="y"/>' in xml


def test_new_input_container_open_tag_carries_bridge_address():
    tag = new_input_container_open_tag("Neues Geraet", "10.0.0.5", 7000, "VUI9", "u-new")
    assert 'Type="VirtualUdpIn"' in tag
    assert 'Title="Matter — Neues Geraet"' in tag
    assert 'Address="10.0.0.5"' in tag
    assert 'Port="7000"' in tag
    assert not tag.endswith("/>")


def test_new_output_container_open_tag_carries_base_url():
    tag = new_output_container_open_tag("Neues Geraet", "http://10.0.0.5:8080", "VQ9", "u-new")
    assert 'Type="VirtualOut"' in tag
    assert 'Address="http://10.0.0.5:8080"' in tag
    assert not tag.endswith("/>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_schema.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/schema.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/test_schema.py -v`
Expected: PASS (11 Tests)

- [ ] **Step 5: Ganze Testsuite laufen lassen**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/projectsync/schema.py tests/projectsync/test_schema.py
git commit -m "feat(projectsync): Attribut-Schema fuer Update und Neuanlage"
```

---

### Task 8: `projectsync/diff.py` — `SyncPlan` berechnen

**Files:**
- Create: `src/loxmatter/projectsync/diff.py`
- Test: `tests/projectsync/test_diff.py`

**Interfaces:**
- Consumes: `ProjectIndex` (aus `index.py`); `desired_input_cmd_attrs`, `desired_output_cmd_attrs`, `MANAGED_INPUT_CMD_ATTRS`, `MANAGED_OUTPUT_CMD_ATTRS` (aus `schema.py`); `to_inputs` (aus `export.signals`); `to_outputs` (aus `export.outputs`); `StoredDevice`, `StoredSignal`, `StoredCommand` (aus `model.store`)
- Produces: `PlanStatus` (StrEnum: `UNCHANGED`, `UPDATED`, `NEW_SIGNAL`, `NEW_DEVICE`, `ORPHANED`, `CONFLICT`), `@dataclass PlanEntry(kind: str, device_id: int, device_label: str, key: str, title: str, status: PlanStatus, changes: dict[str, tuple[str, str]])`, `@dataclass SyncPlan(entries: list[PlanEntry])` mit Property `has_changes: bool`, `build_plan(index, devices, signals_by_device, commands_by_device) -> SyncPlan`

- [ ] **Step 1: Write the failing test**

Create `tests/projectsync/test_diff.py`:

```python
from loxmatter.matter.models import SignalKind
from loxmatter.model.store import SignalRef, StoredCommand, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import PlanStatus, build_plan
from loxmatter.projectsync.index import build_index


def _signal(key: str, device_id: int, endpoint: int = 1) -> StoredSignal:
    return StoredSignal(
        key=key,
        ref=SignalRef(endpoint=endpoint, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE),
        title="Ein/Aus",
        unit="",
        exportability=Exportability.DIGITAL,
        device_id=device_id,
        exported=True,
        functional=True,
    )


def _device(device_id: int, label: str) -> StoredDevice:
    return StoredDevice(
        id=device_id,
        node_id=device_id,
        unique_id=f"u{device_id}",
        label=label,
        exported_at=None,
        updated_at=None,
    )


def test_existing_matching_input_is_unchanged(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1)]
    plan = build_plan(index, [device], {1: signals}, {1: []})
    entry = next(e for e in plan.entries if e.key == "d1_1_onoff")
    # Titel in der Datei ist "Alter Titel", `to_inputs` erzeugt aber den
    # Signal-Titel "Ein/Aus" - das MUSS also `updated` sein, nicht
    # `unchanged`. Dieser Test dokumentiert das erwartete Verhalten fuer
    # Task-Step 3 unten (siehe dortige Anmerkung zur Titel-Divergenz).
    assert entry.status == PlanStatus.UPDATED
    assert entry.changes["Title"] == ("Alter Titel", "Ein/Aus")


def test_new_signal_in_existing_container(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1)]
    plan = build_plan(index, [device], {1: signals}, {1: []})
    entry = next(e for e in plan.entries if e.key == "d1_1_temp")
    assert entry.status == PlanStatus.NEW_SIGNAL


def test_new_device_has_no_existing_container(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    plan = build_plan(index, [device], {2: signals}, {2: []})
    entry = next(e for e in plan.entries if e.key == "d2_1_onoff")
    assert entry.status == PlanStatus.NEW_DEVICE


def test_orphaned_signal_is_reported(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1)]
    plan = build_plan(index, [device], {1: signals}, {1: []})
    orphaned = [e for e in plan.entries if e.status == PlanStatus.ORPHANED]
    assert any(e.key == "d9_9_verwaist" for e in orphaned)


def test_has_changes_is_false_when_everything_matches(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    # "Ein/Aus" statt "Alter Titel", damit dieser Test wirklich den
    # unveraenderten Fall prueft.
    signal = _signal("d1_1_onoff", 1)
    signal_matching_title = StoredSignal(
        key=signal.key,
        ref=signal.ref,
        title="Alter Titel",
        unit=signal.unit,
        exportability=signal.exportability,
        device_id=signal.device_id,
        exported=signal.exported,
        functional=signal.functional,
    )
    plan = build_plan(index, [device], {1: [signal_matching_title]}, {1: []})
    onoff = next(e for e in plan.entries if e.key == "d1_1_onoff")
    assert onoff.status == PlanStatus.UNCHANGED
    # "d9_9_verwaist" bleibt in der Datei, macht has_changes aber nicht wahr
    # - ORPHANED ist eine Meldung, keine geplante Aenderung.
    assert plan.has_changes is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_diff.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/diff.py`:

```python
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

"""Vergleicht die gewuenschten Ein-/Ausgaenge (`export.signals.to_inputs`/
`export.outputs.to_outputs` - dieselbe Quelle wie der bestehende Vorlagen-
Export) gegen einen `ProjectIndex` und baut den Diff-Plan (Entwurf Abschnitt
5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from loxmatter.export.documents import LoxoneCommand
from loxmatter.export.outputs import to_outputs
from loxmatter.export.signals import LoxoneInput, to_inputs
from loxmatter.model.store import StoredCommand, StoredDevice, StoredSignal
from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.schema import (
    MANAGED_INPUT_CMD_ATTRS,
    MANAGED_OUTPUT_CMD_ATTRS,
    desired_input_cmd_attrs,
    desired_output_cmd_attrs,
)

_REQUIRED_INPUT_ATTRS = ("Title", "Check", "Analog")
_REQUIRED_OUTPUT_ATTRS = ("Title", "CmdOn")


class PlanStatus(StrEnum):
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    NEW_SIGNAL = "new_signal"
    NEW_DEVICE = "new_device"
    ORPHANED = "orphaned"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class PlanEntry:
    kind: str  # "input" | "output"
    device_id: int
    device_label: str
    key: str
    title: str
    status: PlanStatus
    # attrname -> (alter Wert, neuer Wert) - nur bei UPDATED nicht leer.
    changes: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncPlan:
    entries: list[PlanEntry]

    @property
    def has_changes(self) -> bool:
        return any(
            entry.status in (PlanStatus.UPDATED, PlanStatus.NEW_SIGNAL, PlanStatus.NEW_DEVICE)
            for entry in self.entries
        )


def _diff_managed_attrs(
    existing: dict[str, str], desired: dict[str, str], managed: Sequence[str]
) -> dict[str, tuple[str, str]]:
    changes: dict[str, tuple[str, str]] = {}
    for name in managed:
        if name not in desired:
            continue
        old = existing.get(name, "")
        new = desired[name]
        if old != new:
            changes[name] = (old, new)
    return changes


def _has_required_attrs(attrs: dict[str, str], required: Sequence[str]) -> bool:
    return all(name in attrs for name in required)


def _plan_inputs(
    index: ProjectIndex, device: StoredDevice, entries: Sequence[LoxoneInput]
) -> list[PlanEntry]:
    prefix = f"d{device.id}_"
    has_existing_container = any(key.startswith(prefix) for key in index.input_containers)
    plan_entries: list[PlanEntry] = []
    for entry in entries:
        existing = index.input_cmds.get(entry.key)
        if existing is None:
            status = PlanStatus.NEW_SIGNAL if has_existing_container else PlanStatus.NEW_DEVICE
            plan_entries.append(
                PlanEntry("input", device.id, device.label, entry.key, entry.title, status)
            )
            continue
        if not _has_required_attrs(existing.attrs, _REQUIRED_INPUT_ATTRS):
            plan_entries.append(
                PlanEntry(
                    "input", device.id, device.label, entry.key, entry.title, PlanStatus.CONFLICT
                )
            )
            continue
        desired = desired_input_cmd_attrs(entry)
        changes = _diff_managed_attrs(existing.attrs, desired, MANAGED_INPUT_CMD_ATTRS)
        status = PlanStatus.UPDATED if changes else PlanStatus.UNCHANGED
        plan_entries.append(
            PlanEntry("input", device.id, device.label, entry.key, entry.title, status, changes)
        )
    return plan_entries


def _plan_outputs(
    index: ProjectIndex, device: StoredDevice, commands: Sequence[LoxoneCommand]
) -> list[PlanEntry]:
    prefix = f"d{device.id}_"
    has_existing_container = any(key.startswith(prefix) for key in index.output_containers)
    plan_entries: list[PlanEntry] = []
    for command in commands:
        existing = index.output_cmds.get(command.key)
        if existing is None:
            status = PlanStatus.NEW_SIGNAL if has_existing_container else PlanStatus.NEW_DEVICE
            plan_entries.append(
                PlanEntry("output", device.id, device.label, command.key, command.title, status)
            )
            continue
        if not _has_required_attrs(existing.attrs, _REQUIRED_OUTPUT_ATTRS):
            plan_entries.append(
                PlanEntry(
                    "output",
                    device.id,
                    device.label,
                    command.key,
                    command.title,
                    PlanStatus.CONFLICT,
                )
            )
            continue
        desired = desired_output_cmd_attrs(command)
        changes = _diff_managed_attrs(existing.attrs, desired, MANAGED_OUTPUT_CMD_ATTRS)
        status = PlanStatus.UPDATED if changes else PlanStatus.UNCHANGED
        plan_entries.append(
            PlanEntry(
                "output", device.id, device.label, command.key, command.title, status, changes
            )
        )
    return plan_entries


def _orphaned_entries(
    index: ProjectIndex, known_input_keys: set[str], known_output_keys: set[str]
) -> list[PlanEntry]:
    orphaned: list[PlanEntry] = []
    for key, element in index.input_cmds.items():
        if key not in known_input_keys and key.split("_", 1)[0].startswith("d"):
            orphaned.append(
                PlanEntry(
                    "input", -1, "", key, element.attrs.get("Title", key), PlanStatus.ORPHANED
                )
            )
    for key, element in index.output_cmds.items():
        if key not in known_output_keys and key.split("_", 1)[0].startswith("d"):
            orphaned.append(
                PlanEntry(
                    "output", -1, "", key, element.attrs.get("Title", key), PlanStatus.ORPHANED
                )
            )
    return orphaned


def build_plan(
    index: ProjectIndex,
    devices: Sequence[StoredDevice],
    signals_by_device: dict[int, Sequence[StoredSignal]],
    commands_by_device: dict[int, Sequence[StoredCommand]],
) -> SyncPlan:
    entries: list[PlanEntry] = []
    known_input_keys: set[str] = set()
    known_output_keys: set[str] = set()

    for device in devices:
        inputs = to_inputs(signals_by_device.get(device.id, []), device.id, device.label)
        outputs = to_outputs(commands_by_device.get(device.id, []))
        known_input_keys.update(entry.key for entry in inputs)
        known_output_keys.update(command.key for command in outputs)
        entries += _plan_inputs(index, device, inputs)
        entries += _plan_outputs(index, device, outputs)

    entries += _orphaned_entries(index, known_input_keys, known_output_keys)
    return SyncPlan(entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/test_diff.py -v`
Expected: PASS (5 Tests). Beachte `test_existing_matching_input_is_unchanged`: der Testname beschreibt die Ausgangsannahme, die Assertion prüft bewusst `UPDATED` — die synthetische Datei trägt absichtlich einen abweichenden Titel (siehe `conftest.py`-Docstring), das ist Teil der Abdeckung des `updated`-Falls, kein Testfehler.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/diff.py tests/projectsync/test_diff.py
git commit -m "feat(projectsync): Diff-Plan aus ProjectIndex und Store berechnen"
```

---

### Task 9: `projectsync/patch.py` — Plan als Textersetzung anwenden

**Files:**
- Create: `src/loxmatter/projectsync/patch.py`
- Test: `tests/projectsync/test_patch.py`

**Interfaces:**
- Consumes: `ProjectIndex`, `SyncPlan`, `PlanEntry`, `PlanStatus` (aus `index.py`/`diff.py`); alles aus `schema.py`; `new_unique_id`, `new_iname` (aus `ids.py`); `escape_attr_value` (aus `export.xml`); `to_inputs` (aus `export.signals`); `to_outputs` (aus `export.outputs`); `StoredCommand`, `StoredDevice`, `StoredSignal` (aus `model.store`)
- Produces: `apply_plan(index: ProjectIndex, plan: SyncPlan, devices: Sequence[StoredDevice], signals_by_device: dict[int, Sequence[StoredSignal]], commands_by_device: dict[int, Sequence[StoredCommand]], *, include_new_devices: bool, bridge_ip: str, port: int, listen: int) -> bytes` — ruft `to_inputs`/`to_outputs` selbst auf (dieselbe Quelle wie `diff.build_plan`), weil es das volle `LoxoneInput`/`LoxoneCommand`-Objekt braucht (`unit_format`, `check_suffix`, `off_path`), das ein `PlanEntry` nicht trägt.

- [ ] **Step 1: Write the failing test**

Create `tests/projectsync/test_patch.py`:

```python
from loxmatter.export.signals import SignalKind
from loxmatter.model.store import SignalRef, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import build_plan
from loxmatter.projectsync.index import build_index
from loxmatter.projectsync.patch import apply_plan


def _signal(key: str, device_id: int, title: str = "Ein/Aus") -> StoredSignal:
    return StoredSignal(
        key=key,
        ref=SignalRef(endpoint=1, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE),
        title=title,
        unit="",
        exportability=Exportability.DIGITAL,
        device_id=device_id,
        exported=True,
        functional=True,
    )


def _device(device_id: int, label: str) -> StoredDevice:
    return StoredDevice(
        id=device_id,
        node_id=device_id,
        unique_id=f"u{device_id}",
        label=label,
        exported_at=None,
        updated_at=None,
    )


def _patch(index, device, signals, *, include_new_devices):
    plan = build_plan(index, [device], {device.id: signals}, {device.id: []})
    return apply_plan(
        index,
        plan,
        [device],
        {device.id: signals},
        {device.id: []},
        include_new_devices=include_new_devices,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    ).decode("utf-8-sig")


def test_updated_attribute_is_replaced_in_place(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Ein/Aus")]
    patched = _patch(index, device, signals, include_new_devices=False)
    assert 'Title="Ein/Aus"' in patched
    assert 'Title="Alter Titel"' not in patched
    # Die U-ID des aktualisierten Objekts bleibt exakt erhalten - Verdrahtung
    # (Co) darf ein Update nie anfassen.
    assert '"1000-0002-0000-aaaaaaaaaaaaaaaa"' in patched
    assert '<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>' in patched


def test_untouched_regions_stay_byte_identical(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1, title="Ein/Aus")]
    patched = _patch(index, device, signals, include_new_devices=False)
    # Das verwaiste Signal wird nur gemeldet, nie veraendert (Entwurf
    # Abschnitt 2).
    assert 'Title="Verwaist"' in patched
    assert 'Check="d9_9_verwaist:\\v"' in patched


def test_new_signal_is_appended_inside_existing_container(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [
        _signal("d1_1_onoff", 1, title="Alter Titel"),
        _signal("d1_1_temp", 1, title="Temperatur"),
    ]
    patched = _patch(index, device, signals, include_new_devices=False)
    assert 'Check="d1_1_temp:\\v"' in patched
    # Eingefuegt in denselben Container wie das bestehende d1_1_onoff, nicht
    # irgendwo im Dokument und nicht als neuer Geraete-Container. Ueber
    # build_index statt Byte-Offset-Arithmetik geprueft: ein naiver
    # patched.index("</C>", container_start) faende das schliessende Tag des
    # ERSTEN Kindes (VCI1), nicht das des Containers selbst - derselbe
    # Fehler, den Task 3 im Scanner schon einmal beheben musste.
    patched_index = build_index(patched)
    assert "d1_1_temp" in patched_index.input_containers
    assert (
        patched_index.input_containers["d1_1_temp"].attrs["U"]
        == index.input_containers["d1_1_onoff"].attrs["U"]
    )


def test_new_device_is_absent_without_the_flag(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=False)
    assert "d2_1_onoff" not in patched


def test_new_device_is_created_with_the_flag(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=True)
    assert 'Check="d2_1_onoff:\\v"' in patched
    assert 'Title="Matter — Neues Geraet"' in patched
    assert 'Address="10.0.0.5"' in patched


def test_next_obj_is_raised_when_new_objects_were_created(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=True)
    next_obj = int(patched.split('NextObj="', 1)[1].split('"', 1)[0])
    assert next_obj > 100  # Ausgangswert in der Beispieldatei


def test_output_is_valid_xml(sample_project):
    import xml.etree.ElementTree as ET

    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    patched = _patch(index, device, signals, include_new_devices=True)
    ET.fromstring(patched)  # wirft bei ungueltigem XML
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_patch.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/patch.py`:

```python
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

"""Wendet einen `SyncPlan` als gezielte Textersetzung auf den Original-
Byte-Strom an (Entwurf Abschnitt 3.2) - nie ueber einen XML-Serialisierer.

Jede Aenderung ist ein `_Edit(start, end, replacement)`: `end == start`
bedeutet reines Einfuegen. Alle Edits werden gesammelt, nach `start`
ABSTEIGEND sortiert und von hinten nach vorn angewendet - so bleiben
vorherige Positionen gueltig, ohne Versatz nachrechnen zu muessen.

`apply_plan` ruft `to_inputs`/`to_outputs` selbst auf, genau wie
`diff.build_plan` - dieselbe Quelle fuer beide, damit Plan und Patch niemals
auseinanderlaufen koennen. Der Grund, das nicht ueber den `PlanEntry`
hindurchzureichen: der traegt nur, was die Oberflaeche zeigen muss
(Titel/Schluessel/Status), nicht `unit_format`/`check_suffix`/`off_path`, die
ein neu angelegtes Objekt zusaetzlich braucht."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter.export.outputs import to_outputs
from loxmatter.export.signals import to_inputs
from loxmatter.export.xml import escape_attr_value
from loxmatter.model.store import StoredCommand, StoredDevice, StoredSignal
from loxmatter.projectsync.diff import PlanEntry, PlanStatus, SyncPlan
from loxmatter.projectsync.ids import new_iname, new_unique_id
from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.schema import (
    find_any_iodata_attrs,
    new_cmd_children_xml,
    new_input_cmd_open_tag,
    new_input_container_open_tag,
    new_output_cmd_open_tag,
    new_output_container_open_tag,
    sibling_iodata_attrs,
)


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    replacement: str


def _attr_span(text: str, tag_start: int, tag_end: int, name: str) -> tuple[int, int] | None:
    pattern = re.compile(rf'{re.escape(name)}="(?:[^"&]|&[^;]+;)*"')
    match = pattern.search(text, tag_start, tag_end)
    return None if match is None else (match.start(), match.end())


def _update_edits(index: ProjectIndex, entry: PlanEntry) -> list[_Edit]:
    element = (index.input_cmds if entry.kind == "input" else index.output_cmds)[entry.key]
    edits: list[_Edit] = []
    for name, (_, new_value) in entry.changes.items():
        span = _attr_span(index.text, element.open_start, element.open_end, name)
        replacement = f'{name}="{escape_attr_value(new_value)}"'
        if span is None:
            # Attribut fehlt im bestehenden Tag ganz (z. B. `Unit` bei einem
            # digitalen Eingang) - vor dem schliessenden '>' einfuegen.
            insert_at = element.open_end - (2 if element.self_closing else 1)
            edits.append(_Edit(insert_at, insert_at, f" {replacement}"))
        else:
            edits.append(_Edit(span[0], span[1], replacement))
    return edits


def _new_signal_edit(
    index: ProjectIndex,
    entry: PlanEntry,
    entries_by_key: dict[str, object],
) -> _Edit:
    is_input = entry.kind == "input"
    container = index.input_containers if is_input else index.output_containers
    prefix = f"d{entry.device_id}_"
    matching_container = next(
        (element for key, element in container.items() if key.startswith(prefix)), None
    )
    assert matching_container is not None and matching_container.inner_end is not None

    iname_prefix = "VCI" if is_input else "VQC"
    iname = new_iname(iname_prefix, index.all_inames)
    u = new_unique_id(index.all_u_values)
    iodata = sibling_iodata_attrs(index.text, next(iter(matching_container.children)))

    obj = entries_by_key[entry.key]
    open_tag = (
        new_input_cmd_open_tag(obj, iname, u)
        if is_input
        else new_output_cmd_open_tag(obj, iname, u)
    )
    children_xml = new_cmd_children_xml(
        kind="input" if is_input else "output", existing_u=index.all_u_values, iodata_attrs=iodata
    )
    full_xml = f"{open_tag}{children_xml}</C>"
    pos = matching_container.inner_end
    return _Edit(pos, pos, full_xml)


def _new_device_edit(
    index: ProjectIndex,
    entry: PlanEntry,
    entries_by_key: dict[str, object],
    bridge_ip: str,
    port: int,
    listen: int,
) -> _Edit:
    is_input = entry.kind == "input"
    caption = index.virtual_in_caption if is_input else index.virtual_out_caption
    assert caption is not None and caption.inner_end is not None

    container_iname_prefix = "VUI" if is_input else "VQ"
    container_iname = new_iname(container_iname_prefix, index.all_inames)
    container_u = new_unique_id(index.all_u_values)
    if is_input:
        container_open = new_input_container_open_tag(
            entry.device_label, bridge_ip, port, container_iname, container_u
        )
    else:
        container_open = new_output_container_open_tag(
            entry.device_label, f"http://{bridge_ip}:{listen}", container_iname, container_u
        )

    cmd_iname_prefix = "VCI" if is_input else "VQC"
    cmd_iname = new_iname(cmd_iname_prefix, index.all_inames)
    cmd_u = new_unique_id(index.all_u_values)
    iodata = find_any_iodata_attrs(index.text, caption)
    obj = entries_by_key[entry.key]
    cmd_open = (
        new_input_cmd_open_tag(obj, cmd_iname, cmd_u)
        if is_input
        else new_output_cmd_open_tag(obj, cmd_iname, cmd_u)
    )
    children_xml = new_cmd_children_xml(
        kind="input" if is_input else "output", existing_u=index.all_u_values, iodata_attrs=iodata
    )
    full_xml = f"{container_open}{cmd_open}{children_xml}</C></C>"
    pos = caption.inner_end
    return _Edit(pos, pos, full_xml)


def _next_obj_edit(index: ProjectIndex, created_count: int) -> _Edit | None:
    if created_count == 0 or "NextObj" not in index.root_attrs:
        return None
    span = _attr_span(index.text, 0, index.root_open_end, "NextObj")
    if span is None:
        return None
    new_value = str(int(index.root_attrs["NextObj"]) + created_count)
    return _Edit(span[0], span[1], f'NextObj="{new_value}"')


def _apply_edits(text: str, edits: list[_Edit]) -> str:
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


def apply_plan(
    index: ProjectIndex,
    plan: SyncPlan,
    devices: Sequence[StoredDevice],
    signals_by_device: dict[int, Sequence[StoredSignal]],
    commands_by_device: dict[int, Sequence[StoredCommand]],
    *,
    include_new_devices: bool,
    bridge_ip: str,
    port: int,
    listen: int,
) -> bytes:
    """Baut die gepatchte Datei fuer eine der beiden Download-Varianten
    (Entwurf Abschnitt 3.4/7): `include_new_devices=False` liefert nur
    Updates und neue Signale in bereits bestehenden Geraete-Containern,
    `True` zusaetzlich komplett neue Geraete-Container."""
    desired_inputs: dict[str, object] = {}
    desired_outputs: dict[str, object] = {}
    for device in devices:
        for item in to_inputs(signals_by_device.get(device.id, []), device.id, device.label):
            desired_inputs[item.key] = item
        for item in to_outputs(commands_by_device.get(device.id, [])):
            desired_outputs[item.key] = item

    edits: list[_Edit] = []
    created_count = 0
    for entry in plan.entries:
        if entry.status is PlanStatus.UPDATED:
            edits += _update_edits(index, entry)
        elif entry.status is PlanStatus.NEW_SIGNAL:
            source = desired_inputs if entry.kind == "input" else desired_outputs
            edits.append(_new_signal_edit(index, entry, source))
            created_count += 1
        elif entry.status is PlanStatus.NEW_DEVICE and include_new_devices:
            source = desired_inputs if entry.kind == "input" else desired_outputs
            edits.append(_new_device_edit(index, entry, source, bridge_ip, port, listen))
            created_count += 2  # Container + erstes Cmd sind beides neue <C>-Objekte.

    next_obj_edit = _next_obj_edit(index, created_count)
    if next_obj_edit is not None:
        edits.append(next_obj_edit)

    patched_text = _apply_edits(index.text, edits)
    if not patched_text.startswith("﻿"):
        patched_text = "﻿" + patched_text
    return patched_text.encode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/test_patch.py -v`
Expected: PASS (7 Tests)

- [ ] **Step 5: Ganze Testsuite laufen lassen**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/projectsync/patch.py tests/projectsync/test_patch.py
git commit -m "feat(projectsync): Diff-Plan als Textersetzung auf die Projektdatei anwenden"
```

---

### Task 10: `projectsync/sync.py` — Orchestrierung

**Files:**
- Create: `src/loxmatter/projectsync/sync.py`
- Test: `tests/projectsync/test_sync.py`

**Interfaces:**
- Consumes: `build_index`, `ProjectFormatError` (aus `index.py`), `build_plan` (aus `diff.py`), `apply_plan` (aus `patch.py`), `Store` (aus `model.store`)
- Produces: `@dataclass(frozen=True) ProjectSyncResult(plan: SyncPlan, patched_conservative: bytes, patched_with_new_devices: bytes)`, `run_sync(raw: bytes, store: Store, *, bridge_ip: str, port: int, listen: int) -> ProjectSyncResult`

- [ ] **Step 1: Write the failing test**

Create `tests/projectsync/test_sync.py`:

```python
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.model.store import Store
from loxmatter.projectsync.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _plug_store(tmp_path):
    from conftest import load_snapshot  # tests/api/conftest.py - Pfad ist bereits auf sys.path

    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    return store


def test_run_sync_returns_plan_and_both_file_variants(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = run_sync(
        sample_project.encode("utf-8"), store, bridge_ip="10.0.0.5", port=7000, listen=8080
    )
    assert result.plan.entries  # nicht leer - die Steckdose hat Signale
    assert result.patched_conservative != result.patched_with_new_devices
    store.close()


def test_run_sync_raises_project_format_error_for_garbage(tmp_path):
    import pytest

    from loxmatter.projectsync.index import ProjectFormatError

    store = _plug_store(tmp_path)
    with pytest.raises(ProjectFormatError):
        run_sync(b"nicht xml", store, bridge_ip="10.0.0.5", port=7000, listen=8080)
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/projectsync/test_sync.py -v`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementierung**

Create `src/loxmatter/projectsync/sync.py`:

```python
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

"""Bindet Parsen, Diff und Patch zu einem einzigen Aufruf zusammen - das, was
`api.project_sync` aufruft (Entwurf Abschnitt 4: ein Request, keine
Zwischenzustand auf dem Server)."""

from __future__ import annotations

from dataclasses import dataclass

from loxmatter.projectsync.diff import SyncPlan, build_plan
from loxmatter.projectsync.index import ProjectFormatError, build_index
from loxmatter.projectsync.patch import apply_plan

__all__ = ["ProjectFormatError", "ProjectSyncResult", "run_sync"]


@dataclass(frozen=True)
class ProjectSyncResult:
    plan: SyncPlan
    patched_conservative: bytes
    patched_with_new_devices: bytes


def run_sync(raw: bytes, store, *, bridge_ip: str, port: int, listen: int) -> ProjectSyncResult:
    text = raw.decode("utf-8-sig")
    index = build_index(text)
    devices = store.devices()
    signals_by_device = {device.id: store.signals(device.id) for device in devices}
    commands_by_device = {device.id: store.commands(device.id) for device in devices}
    plan = build_plan(index, devices, signals_by_device, commands_by_device)
    conservative = apply_plan(
        index,
        plan,
        devices,
        signals_by_device,
        commands_by_device,
        include_new_devices=False,
        bridge_ip=bridge_ip,
        port=port,
        listen=listen,
    )
    with_new_devices = apply_plan(
        index,
        plan,
        devices,
        signals_by_device,
        commands_by_device,
        include_new_devices=True,
        bridge_ip=bridge_ip,
        port=port,
        listen=listen,
    )
    return ProjectSyncResult(plan, conservative, with_new_devices)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/projectsync/test_sync.py -v`
Expected: PASS (2 Tests)

- [ ] **Step 5: Ganze Testsuite laufen lassen**

Run: `uv run pytest -q`
Expected: PASS, komplette Suite grün.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/projectsync/sync.py tests/projectsync/test_sync.py
git commit -m "feat(projectsync): Parsen, Diff und Patch zu einem Aufruf buendeln"
```

---

## API und WebUI

### Task 11: `api/project_sync.py` — Router `POST /api/export/project-sync`

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Create: `src/loxmatter/api/project_sync.py`
- Modify: `src/loxmatter/loxone/server.py`
- Test: `tests/api/test_project_sync_api.py`

**Interfaces:**
- Consumes: `run_sync`, `ProjectSyncResult` (aus `projectsync.sync`), `ProjectFormatError`, `Store`, `DEFAULT_UDP_PORT`, `DEFAULT_LISTEN_PORT` (aus `model.store`)
- Produces: `ProjectSyncEntryOut`, `ProjectSyncPlanOut` (Pydantic-Modelle in `api/models.py`); `build_project_sync_router(store: Store) -> APIRouter`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_project_sync_api.py`:

```python
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

"""Tests fuer POST /api/export/project-sync - siehe api/project_sync.py."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

SAMPLE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    "</ControlList>\r\n"
)


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store
    store.close()


async def test_project_sync_returns_plan_and_both_variants(api):
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("projekt.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entries"]
    assert body["has_changes"] is True
    conservative = base64.b64decode(body["patched_conservative_base64"])
    with_new_devices = base64.b64decode(body["patched_with_new_devices_base64"])
    assert b"VirtualUdpIn" not in conservative  # Neuanlage nur mit dem Haken
    assert b"VirtualUdpIn" in with_new_devices


async def test_project_sync_rejects_invalid_file(api):
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("kaputt.Loxone", b"kein xml", "application/xml")},
    )
    assert response.status_code == 400


async def test_project_sync_requires_authentication(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/export/project-sync",
            params={"bridge_ip": "10.0.0.5"},
            files={"file": ("p.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
        )
    assert response.status_code == 401
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_project_sync_api.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.api.project_sync'`

- [ ] **Step 3: Implementierung**

In `src/loxmatter/api/models.py`, ergänze am Ende:

```python
class ProjectSyncEntryOut(BaseModel):
    """Eine Zeile im Diff-Plan von `POST /api/export/project-sync` (Entwurf
    Abschnitt 5/7). `changes` ist ausserhalb von `status == "updated"` immer
    leer."""

    model_config = ConfigDict(frozen=True)

    kind: str
    device_id: int
    device_label: str
    key: str
    title: str
    status: str
    changes: dict[str, list[str]]


class ProjectSyncPlanOut(BaseModel):
    """Antwort von `POST /api/export/project-sync` - Plan und beide
    gepatchten Datei-Varianten in einer Antwort (Entwurf Abschnitt 4/7): kein
    zweiter Server-Roundtrip, der "Bestaetigen"-Schritt ist rein
    clientseitig."""

    model_config = ConfigDict(frozen=True)

    entries: list[ProjectSyncEntryOut]
    has_changes: bool
    patched_conservative_base64: str
    patched_with_new_devices_base64: str
```

Create `src/loxmatter/api/project_sync.py`:

```python
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

"""`POST /api/export/project-sync` (Entwurf `docs/superpowers/specs/
2026-09-03-projektdatei-sync-design.md`, Abschnitt 7).

Nimmt eine hochgeladene Loxone-Projektdatei entgegen und liefert Diff-Plan
plus beide gepatchten Datei-Varianten in einer Antwort - derselbe `Store`,
den auch `api.export` und `api.devices` bekommen (siehe deren
Moduldocstrings zur Begruendung: ein zweiter, unabhaengig geoeffneter Store
vergaebe fuer dasselbe Geraet einen zweiten Satz Signalschluessel)."""

from __future__ import annotations

import base64

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from loxmatter.api.models import ProjectSyncEntryOut, ProjectSyncPlanOut
from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store
from loxmatter.projectsync.diff import SyncPlan
from loxmatter.projectsync.index import ProjectFormatError
from loxmatter.projectsync.sync import run_sync


def _entries_out(plan: SyncPlan) -> list[ProjectSyncEntryOut]:
    return [
        ProjectSyncEntryOut(
            kind=entry.kind,
            device_id=entry.device_id,
            device_label=entry.device_label,
            key=entry.key,
            title=entry.title,
            status=entry.status.value,
            changes={name: [old, new] for name, (old, new) in entry.changes.items()},
        )
        for entry in plan.entries
    ]


def build_project_sync_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/export")

    @router.post("/project-sync")
    async def project_sync(
        file: UploadFile = File(..., description="Die hochgeladene .Loxone-Projektdatei"),
        bridge_ip: str = Query(..., description="IP der Bruecke, aus Sicht des Miniservers"),
        port: int = Query(DEFAULT_UDP_PORT, description="UDP-Port, auf dem der Miniserver lauscht"),
        listen: int = Query(
            DEFAULT_LISTEN_PORT,
            description="HTTP-Port in den Kommando-URLs neuer Ausgaenge - muss mit dem"
            " --listen von `loxmatter run` uebereinstimmen, wie bei /api/export/download.",
        ),
    ) -> ProjectSyncPlanOut:
        """Baut Diff-Plan und beide gepatchten Datei-Varianten im Speicher -
        schreibt nirgends auf die Platte und markiert kein Geraet als
        exportiert (anders als `/api/export/download`: eine hochgeladene
        Projektdatei ist keine heruntergeladene Vorlage, siehe Entwurf
        Abschnitt 4)."""
        raw = await file.read()
        try:
            result = run_sync(raw, store, bridge_ip=bridge_ip, port=port, listen=listen)
        except ProjectFormatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return ProjectSyncPlanOut(
            entries=_entries_out(result.plan),
            has_changes=result.plan.has_changes,
            patched_conservative_base64=base64.b64encode(result.patched_conservative).decode(
                "ascii"
            ),
            patched_with_new_devices_base64=base64.b64encode(
                result.patched_with_new_devices
            ).decode("ascii"),
        )

    return router
```

In `src/loxmatter/loxone/server.py`: ergänze den Import neben den übrigen `api.*`-Importen (Zeile ~140, direkt nach `from loxmatter.api.live import ...`):

```python
from loxmatter.api.project_sync import build_project_sync_router
```

Und direkt nach der bestehenden Zeile `app.include_router(build_export_router(store), dependencies=api_guard)` (Zeile ~449):

```python
    app.include_router(build_project_sync_router(store), dependencies=api_guard)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_project_sync_api.py -v`
Expected: PASS (3 Tests)

- [ ] **Step 5: Ganze Testsuite laufen lassen**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/models.py src/loxmatter/api/project_sync.py src/loxmatter/loxone/server.py tests/api/test_project_sync_api.py
git commit -m "feat(api): POST /api/export/project-sync - Diff-Plan und gepatchte Projektdatei"
```

---

### Task 12: WebUI — Upload, Plan-Ansicht, Download

**Files:**
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/index.html`

**Interfaces:**
- Consumes: `requestJson` (Muster für Fehlerbehandlung, aber nicht direkt wiederverwendbar — Multipart-Upload braucht `FormData`, kein `JSON.stringify`), `UnauthorizedError`, `readErrorDetail` (bestehende Helfer in `app.js`)
- Produces: neuer Alpine-Zustand `projectSync` (Objekt mit `file`, `plan`, `includeNewDevices`, `busy`, `error`) und Methoden `uploadProjectFile(event)`, `downloadPatchedProject()` im `app()`-Rückgabewert von `app.js`

- [ ] **Step 1: State und Upload-Funktion in `app.js`**

In `src/loxmatter/web/app.js`, ergänze nach der bestehenden `requestDownload`-Funktion (nach Zeile ~220) eine neue Helfer-Funktion für den Multipart-Upload:

```javascript
/**
 * Laedt eine Datei per multipart/form-data hoch und erwartet JSON zurueck -
 * eigene Funktion statt `requestJson`, weil ein Datei-Upload kein
 * `JSON.stringify`-Body ist und `Content-Type` dem Browser ueberlassen
 * werden muss (er setzt die Multipart-Boundary selbst).
 */
async function requestUpload(path, formData) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      body: formData,
    });
  } catch {
    throw new Error("Die Brücke ist nicht erreichbar – sie läuft möglicherweise nicht.");
  }
  if (response.status === 401) {
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    const error = new Error(await readErrorDetail(response));
    error.status = response.status;
    throw error;
  }
  return response.json();
}

/** Dekodiert einen Base64-String zu einem Blob - fuer den Download der
 * gepatchten Projektdatei aus der JSON-Antwort von /api/export/project-sync. */
function blobFromBase64(base64, mimeType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mimeType });
}
```

Im Alpine-Zustandsobjekt (finde den Rückgabewert von `app()`, erkennbar am Kommentar „Ruft Alpine von sich aus genau EINMAL auf" um Zeile 353 — die Eigenschaften stehen im selben `return { ... }`), ergänze:

```javascript
    projectSync: {
      plan: null,
      includeNewDevices: false,
      busy: false,
      error: "",
      patchedConservativeBase64: "",
      patchedWithNewDevicesBase64: "",
    },

    async uploadProjectFile(event) {
      const input = event.target;
      const file = input.files && input.files[0];
      if (!file) {
        return;
      }
      this.projectSync.busy = true;
      this.projectSync.error = "";
      this.projectSync.plan = null;
      try {
        const formData = new FormData();
        formData.append("file", file);
        const params = new URLSearchParams({ bridge_ip: this.settings.bridgeIp || "" });
        const body = await requestUpload(`/api/export/project-sync?${params}`, formData);
        this.projectSync.plan = body;
        this.projectSync.patchedConservativeBase64 = body.patched_conservative_base64;
        this.projectSync.patchedWithNewDevicesBase64 = body.patched_with_new_devices_base64;
      } catch (error) {
        this.noteAuthError(error);
        this.projectSync.error = error.message;
      } finally {
        this.projectSync.busy = false;
        input.value = "";
      }
    },

    downloadPatchedProject() {
      const base64 = this.projectSync.includeNewDevices
        ? this.projectSync.patchedWithNewDevicesBase64
        : this.projectSync.patchedConservativeBase64;
      const blob = blobFromBase64(base64, "application/xml");
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "loxmatter-projekt-gepatcht.Loxone";
      link.click();
      URL.revokeObjectURL(objectUrl);
    },
```

**Hinweis:** `this.settings.bridgeIp` verweist auf denselben Zustand, den die bestehende Export-Ansicht für `bridge_ip` benutzt (siehe die Verwendung von `GET /api/settings` im vorhandenen Code). Suche vor diesem Schritt mit `grep -n "bridgeIp\|bridge_ip" src/loxmatter/web/app.js` nach dem tatsächlichen Feldnamen im bestehenden Alpine-Zustand und verwende exakt diesen Namen statt `this.settings.bridgeIp`, falls er abweicht (z. B. `this.settingsForm.bridgeIp` o. ä.) — er MUSS mit dem Feld übereinstimmen, das die bestehende „Verbindungseinstellungen"-Ansicht bereits zeigt, sonst fragt dieses Formular nach einer IP, die an anderer Stelle schon eingegeben ist.

Auch `noteAuthError` ist eine bestehende Methode (siehe deren Verwendung an anderen `catch`-Blöcken in derselben Datei) — falls ihr tatsächlicher Name abweicht, an den bestehenden Namen anpassen.

- [ ] **Step 2: Abschnitt in `index.html`**

Suche die bestehende „System"-Ansicht in `index.html` (`grep -n 'x-show="view ===' src/loxmatter/web/index.html`, oder wo die bestehenden Export-Buttons liegen) und ergänze dort einen neuen Abschnitt nach demselben Muster wie die bestehenden Karten (`<section>`/`<div class="card">`, prüfe die exakte bestehende Klasse mit `grep -n 'class="card"' src/loxmatter/web/index.html`):

```html
<section class="card" x-show="view === 'system'">
  <h2>Projektdatei-Sync</h2>
  <p>
    Loxone-Projektdatei hochladen — bestehende virtuelle Ein-/Ausgänge
    werden aktualisiert, neue Signale ergänzt. Nichts wird heruntergeladen,
    bevor du den Plan gesehen hast.
  </p>
  <input type="file" accept=".Loxone,.xml" @change="uploadProjectFile($event)" :disabled="projectSync.busy" />
  <p x-show="projectSync.busy">Wird verarbeitet …</p>
  <p x-show="projectSync.error" x-text="projectSync.error" class="error"></p>

  <template x-if="projectSync.plan">
    <div>
      <p x-show="!projectSync.plan.has_changes">Alles aktuell, keine Änderungen nötig.</p>
      <table x-show="projectSync.plan.has_changes">
        <thead>
          <tr>
            <th>Gerät</th>
            <th>Signal</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <template x-for="entry in projectSync.plan.entries" :key="entry.kind + entry.key">
            <tr>
              <td x-text="entry.device_label || '—'"></td>
              <td x-text="entry.title"></td>
              <td x-text="entry.status"></td>
            </tr>
          </template>
        </tbody>
      </table>
      <label>
        <input type="checkbox" x-model="projectSync.includeNewDevices" />
        Neue Geräte-Container ebenfalls anlegen (experimentell, noch nicht gegen Loxone Config validiert)
      </label>
      <button @click="downloadPatchedProject()">Gepatchte Datei herunterladen</button>
    </div>
  </template>
</section>
```

**Hinweis:** prüfe vor dem Einfügen, welchen `view`-Wert die bestehende „System"-Ansicht tatsächlich benutzt (`grep -n "view ===" src/loxmatter/web/index.html`) und welche CSS-Klassen (`card`, `error`, Tabellen-Stile) das bestehende Markup verwendet, und verwende exakt dieselben — dieser Schritt beschreibt die Struktur, nicht jedes Detail des bestehenden Stylings.

- [ ] **Step 3: Manuell im Browser prüfen**

```bash
uv run loxmatter run --miniserver 10.0.0.1 --listen 8080
```

Öffne `http://localhost:8080/`, melde dich an, wechsle zur „System"-Ansicht, lade die synthetische Beispieldatei hoch (kopiere den Inhalt von `tests/projectsync/conftest.py::SAMPLE_PROJECT` in eine lokale `.Loxone`-Datei) und prüfe:
- Der Plan erscheint mit den erwarteten Zeilen.
- Der Download-Button liefert eine Datei, deren Name auf `.Loxone` endet.
- Der Haken schaltet zwischen den beiden Datei-Inhalten um (Dateigröße ändert sich, wenn `include_new_devices` etwas beiträfe — mit der synthetischen Datei ohne bekannte Geräte im `Store` ggf. kein sichtbarer Unterschied; wichtig ist, dass kein Fehler im Konsolen-Log erscheint).

Es gibt für diesen Schritt keinen automatisierten Test — er ist manuelle Verifikation der Browser-Interaktion, wie in den Projektrichtlinien für UI-Änderungen gefordert.

- [ ] **Step 4: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html
git commit -m "feat(web): Projektdatei-Sync - Upload, Plananzeige, Download"
```

---

### Task 13: README ergänzen — Warnung zum unverifizierten ID-Schema

**Files:**
- Modify: `README.md`

**Interfaces:** keine (reine Dokumentation)

- [ ] **Step 1: Abschnitt ergänzen**

Füge in `README.md` nach dem bestehenden Absatz über die Vorlagen (endet mit „... Details: [Signalauswahl-Entwurf]...") einen neuen Absatz ein:

```markdown
**Projektdatei-Sync (`POST /api/export/project-sync`, WebUI unter
„System").** Statt Vorlagen einzeln zu importieren, kann eine bestehende
Loxone-Projektdatei hochgeladen werden — das Tool gleicht sie gegen die
gespeicherten Geräte ab und liefert eine gepatchte Fassung zum Download.
Updates an bereits bestehenden virtuellen Ein-/Ausgängen und neue Signale
innerhalb bereits bestehender Geräte sind die Vorgabe. **Komplett neue
Geräte-Container sind experimentell** und nur über einen expliziten Haken
im WebUI enthalten: das dafür nötige ID-Schema für neue Objekte ist aus
einer einzigen echten Projektdatei abgeleitet, nicht offiziell dokumentiert
und **nicht verifiziert**. Vor dem ersten Vertrauen in diesen Pfad: eine
damit gepatchte Datei einmal in Loxone Config öffnen und auf Fehler prüfen.
Details: [Projektdatei-Sync-Entwurf](docs/superpowers/specs/2026-09-03-projektdatei-sync-design.md).
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: Projektdatei-Sync und ihr unverifiziertes ID-Schema im README nachziehen"
```

---

## Self-Review-Notizen (für den Ausführenden)

- **Task 9**: `apply_plan` ruft `to_inputs`/`to_outputs` selbst auf (dieselbe Quelle wie `diff.build_plan`), statt die volle `LoxoneInput`/`LoxoneCommand`-Objektliste durch den `PlanEntry` zu reichen — der trägt nur, was die Oberfläche zeigen muss (Titel/Schlüssel/Status), nicht `unit_format`/`check_suffix`/`off_path`, die ein neu angelegtes Objekt zusätzlich braucht.
- **Task 12** enthält zwei Stellen, an denen der Ausführende den bestehenden Code selbst nachschlagen muss (Feldname für die Bridge-IP im Alpine-Zustand, `view`-Wert und CSS-Klassen der „System"-Ansicht) — das ist beabsichtigt, weil `app.js`/`index.html` mit ~1700/~760 Zeilen zu groß sind, um hier vollständig zitiert zu werden, und weil ein falsch geratener Feldname eine zweite, abweichende Bridge-IP-Eingabe in der Oberfläche erzeugen würde.
- Spec-Abdeckung geprüft: Eingabeweg (Task 11/12), Text-Chirurgie (Task 3, 9), Schlüssel-Abgleich (Task 4, 5), Risikostufen/Experimentell-Haken (Task 9 `include_new_devices`, Task 12 Checkbox), Diff-Plan-Datenmodell (Task 8), ID-Vergabe (Task 6), Fehlerbehandlung (Task 5 `ProjectFormatError`→400, Task 8 `CONFLICT`), Tests mit synthetischer Fixture statt echter Nutzerdatei (Task 5 `conftest.py`), README-Warnung (Task 13). `orphaned`-Meldung in der WebUI-Tabelle enthalten (Status-Spalte zeigt `entry.status` roh — für eine spätere Iteration ließe sich das in lesbaren Text übersetzen, das ist YAGNI für diesen Plan, keine fehlende Abdeckung).
