# Phase 3: Exporter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aus den Signalen eines eingelernten Geräts eine Vorlagendatei erzeugen, die Loxone Config ohne Nacharbeit importiert und die bei jedem weiteren Export die bestehende Verdrahtung unangetastet lässt.

**Architecture:** Vier neue Module mit klaren Grenzen: `export/xml.py` baut Loxone-XML als Bytes (Escaping, BOM, CRLF) und weiß nichts von Matter; `profiles/` ist eine YAML-Datentabelle plus Lader, die Signale benennt und ihre Exportierbarkeit entscheidet; `model/` hält Geräte und Signale in SQLite und vergibt unveränderliche Schlüssel; `export/documents.py` setzt daraus pro Gerät ein `VIU_`- und ein `VO_`-Dokument zusammen. Die Zerlegung aus Phase 1 bleibt unberührt.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, `mypy` (strict), `PyYAML`, `sqlite3` aus der Standardbibliothek.

## Global Constraints

Aus Spec und Plan, gelten für jede Task:

- **Tests laufen ohne Hardware und ohne Netzwerkzugriff.** Ein Test, der ein echtes Gerät braucht, wird übersprungen und verrottet (Spec 10.1).
- **Deutsch in Prosa, Kommentaren, Docstrings und Fehlermeldungen**, Englisch in Bezeichnern und Commit-Präfixen.
- **Alle Datenklassen unveränderlich** (`frozen=True`), solange kein Grund dagegen spricht.
- **Dateiformat der Vorlagen: UTF-8 mit BOM, CRLF-Zeilenenden.** Dateinamen `VIU_d<device_id>_<label>.xml` und `VO_d<device_id>_<label>.xml`, mit auf ASCII normalisiertem Gerätelabel — die `device_id` ist nicht Dekoration, sondern der einzige Teil des Namens, der Eindeutigkeit garantiert, weil die Normalisierung verlustbehaftet ist (Spec 6.1).
- **Der Loxone-Platzhalter `<v>` steht in einem XML-Attribut und muss als `&lt;v&gt;` geschrieben werden.** Ein unescaptes `<v>` macht die Datei für Loxone Config unlesbar. Der Platzhalter `\v` in `Check` ist davon nicht betroffen (Spec 6.1).
- **Schlüssel sind opak und unveränderlich**, Format `d<device_id>_<endpoint>_<slug>`. Lesbare Namen leben ausschließlich in `Title` und `Comment`. `device_id` wird nie wiederverwendet (Spec 6.2).
- **Eine Vorlagendatei pro Gerät**, alle Geräte teilen sich einen UDP-Port, Default 7000. Der Port ist pro Gerät konfigurierbar. Grenze des Miniservers: 50 verschiedene Eingangs-Ports (Spec 6.2).
- **Zieleinheit ist die des Loxone-Bausteins, nicht die SI-Einheit.** Leistung in kW. Ausgabe mit bis zu 6 Nachkommastellen, nachlaufende Nullen abgeschnitten — 300 mW muss als `0.0003` ankommen, nicht als `0` (Spec 7.3). **Die Umrechnung selbst gehört zum UDP-Sender in Phase 4**; hier wird nur festgelegt und exportiert, welche Einheit ein Signal trägt.
- **`Unit` in der Vorlage ist ein Formatstring, kein Einheitentext** (Spec 7.3): `<v.N> Einheit`, wobei `N` die Zahl der auf der Loxone-Oberfläche angezeigten Nachkommastellen ist. Für Leistung schreiben wir `<v.6> kW`, nicht das sonst übliche `<v.3>` — mit drei Nachkommastellen zeigt ein 300-mW-Standby-Verbraucher `0.000` an. Die Zuordnung Einheit → Formatstring steht als Datentabelle in `profiles/table.py` (Task 2), nicht als Verzweigung im Exporter.
- `uv run ruff check .`, `uv run ruff format --check .` und `uv run mypy` müssen sauber bleiben. ruff formatiert auch Python-Blöcke in Markdown.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `src/loxmatter/export/xml.py` | Loxone-XML als Bytes: Attribut-Escaping, BOM, CRLF. Kennt kein Matter |
| `src/loxmatter/profiles/clusters.yaml` | Datentabelle: Cluster/Attribut → Kurzname, Einheit, analog/digital |
| `src/loxmatter/profiles/table.py` | Lädt die Tabelle, entscheidet Exportierbarkeit und Benennung |
| `src/loxmatter/model/store.py` | SQLite: Geräte, Signale, unveränderliche Schlüssel, Export-Zustand |
| `src/loxmatter/export/documents.py` | Setzt pro Gerät `VIU_` und `VO_` zusammen |
| `src/loxmatter/cli.py` | erweitert um `loxmatter export` |
| `tests/fixtures/loxone/` | Referenzvorlagen aus echtem Loxone Config (Golden Files) |

---

### Task 1: XML-Grundlage und Beleg gegen echtes Loxone Config

Das größte Risiko der Phase zuerst: ob Loxone Config eine von uns erzeugte Datei
tatsächlich annimmt. Die Schemaform ist gegen eine Referenzimplementierung verifiziert
(Spec 6.1), aber noch nie von diesem Code erzeugt worden.

**Files:**
- Create: `src/loxmatter/export/__init__.py`
- Create: `src/loxmatter/export/xml.py`
- Create: `tests/export/test_xml.py`
- Create: `tests/fixtures/loxone/` (Referenzdateien aus Loxone Config)

**Interfaces:**
- Consumes: nichts
- Produces:
  - `render_document(root: str, root_attrs: Sequence[tuple[str, str]], children: Sequence[tuple[str, Sequence[tuple[str, str]]]]) -> bytes`
  - `BOM: str`, `CRLF: str`

- [ ] **Step 1: Write the failing test**

`tests/export/test_xml.py`:

```python
from loxmatter.export.xml import render_document


def test_document_starts_with_utf8_bom():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    assert out.startswith(b"\xef\xbb\xbf")


def test_document_uses_crlf_line_endings():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    assert b"\r\n" in out
    assert b"\n" not in out.replace(b"\r\n", b"")


def test_declaration_comes_first():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    text = out.decode("utf-8-sig")
    assert text.splitlines()[0] == '<?xml version="1.0" encoding="utf-8"?>'


def test_loxone_value_placeholder_is_escaped():
    """Ein unescaptes <v> macht die Datei fuer Loxone Config unlesbar."""
    out = render_document(
        "VirtualOut",
        [("Title", "T")],
        [("VirtualOutCmd", [("CmdOn", "/cmd/d1_1_level/<v>")])],
    )
    text = out.decode("utf-8-sig")
    assert "&lt;v&gt;" in text
    assert "/<v>" not in text


def test_backslash_v_in_check_is_left_alone():
    """\\v ist Loxones Wertplatzhalter in der Befehlserkennung, kein XML."""
    out = render_document(
        "VirtualInUdp",
        [("Title", "T")],
        [("VirtualInUdpCmd", [("Check", "d1_1_temp:\\v")])],
    )
    assert "d1_1_temp:\\v" in out.decode("utf-8-sig")


def test_quotes_and_ampersands_are_escaped():
    out = render_document("VirtualInUdp", [("Title", 'Klaus & "Otto"')], [])
    text = out.decode("utf-8-sig")
    assert "&amp;" in text
    assert "&quot;" in text or "&#34;" in text


def test_children_are_rendered_as_self_closing_elements():
    out = render_document(
        "VirtualInUdp",
        [("Title", "T")],
        [("VirtualInUdpCmd", [("Title", "A")]), ("VirtualInUdpCmd", [("Title", "B")])],
    )
    text = out.decode("utf-8-sig")
    assert text.count("<VirtualInUdpCmd ") == 2
    assert text.count("/>") == 2
    assert text.rstrip().endswith("</VirtualInUdp>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_xml.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.export'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/export/xml.py`:

```python
"""Baut Loxone-Vorlagendateien als Bytes.

Absichtlich ohne XML-Bibliothek: Loxone Config ist beim Format waehlerisch, und
die verifizierte Referenzimplementierung baut die Dateien ebenfalls als Text.
Ein Serialisierer duerfte Attribute umsortieren oder die Deklaration anders
schreiben, was hier niemand nachpruefen kann.

Dieses Modul kennt kein Matter. Es weiss nur, wie eine Loxone-Vorlage aussieht.
"""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import quoteattr

BOM = "\ufeff"
CRLF = "\r\n"
DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'

Attrs = Sequence[tuple[str, str]]


def _render_attrs(attrs: Attrs) -> str:
    return " ".join(f"{name}={quoteattr(value)}" for name, value in attrs)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_xml.py -v`
Expected: PASS, 7 Tests

- [ ] **Step 5: Referenzvorlagen aus echtem Loxone Config holen**

**Dieser Schritt braucht einen Menschen mit Loxone Config.** Ohne ihn ist der Rest
der Phase Blindflug: die Schemaform stammt aus einer fremden Referenzimplementierung,
nicht aus Config selbst.

In Loxone Config:

1. Peripherie → Virtuelle Eingänge → Virtueller UDP-Eingang anlegen, Port 7000,
   Adresse leer lassen.
2. Zwei Befehle darunter anlegen: einen analogen (Befehlserkennung `d1_1_temp:\v`)
   und einen digitalen (`d1_1_online:\v`).
3. Das Objekt mit Rechtsklick → **Als Vorlage speichern**.
4. Dasselbe für einen virtuellen Ausgang mit Adresse `http://192.168.1.50:8080` und
   zwei Befehlen: einer analog mit `CmdOn` = `/cmd/d1_1_level/<v>`, einer digital.
5. Die erzeugten Dateien aus `Dokumente\Loxone\Loxone Config\Templates\VirtualIn\`
   bzw. `...\VirtualOut\` nach `tests/fixtures/loxone/` kopieren.

- [ ] **Step 6: Referenz gegen unsere Ausgabe halten**

`tests/export/test_reference.py`:

```python
"""Vergleicht unsere Ausgabe mit Vorlagen, die Loxone Config selbst erzeugt hat.

Weicht hier etwas ab, ist die Referenz massgeblich, nicht unser Code.
"""

from pathlib import Path

import pytest

REFERENCE_DIR = Path(__file__).parents[1] / "fixtures" / "loxone"
REFERENCES = sorted(REFERENCE_DIR.glob("*.xml"))


def test_reference_templates_exist():
    assert REFERENCES, "Task 1 Schritt 5 wurde nicht ausgefuehrt — keine Referenz da"


@pytest.mark.parametrize("path", REFERENCES, ids=lambda p: p.stem)
def test_reference_is_utf8_with_bom(path):
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("path", REFERENCES, ids=lambda p: p.stem)
def test_reference_uses_crlf(path):
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


@pytest.mark.parametrize("path", REFERENCES, ids=lambda p: p.stem)
def test_reference_declaration_matches_ours(path):
    from loxmatter.export.xml import DECLARATION

    first = path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert first == DECLARATION
```

Run: `uv run pytest tests/export/test_reference.py -v`

Schlägt einer dieser Tests fehl, **nicht den Test anpassen**: dann sieht eine echte
Loxone-Vorlage anders aus als angenommen, und `xml.py` muss folgen. Trage den Befund
in Spec 6.1 ein.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/export tests/export tests/fixtures/loxone
git commit -m "feat(export): XML-Grundlage, gegen echte Loxone-Vorlagen belegt"
```

**Nachtrag (2026-09-02) — diese Task ist bereits implementiert und committet, die
Codeblöcke oben bleiben unverändert. Zwei Dinge, die seither gelernt wurden:**

- **`xml.sax.saxutils.quoteattr` wurde in der Umsetzung ersetzt.** Es wechselt bei
  einem `"` im Wert die Anführungszeichen-Art (liefert dann ein mit `'` umschlossenes
  Attribut) statt zu escapen — für Loxone Config, das durchgängig `"`-Attribute
  erwartet, unbrauchbar.
- **Die Referenzvorlagen sind da.** Schritt 5–7 dieser Task sind erledigt: Zwei
  sanitisierte Ableitungen aus echten Vorlagen liegen unter
  `tests/fixtures/loxone/VIU_Referenz.xml` und `tests/fixtures/loxone/VO_Referenz.xml`.
  Der volle Fundus aus einer echten Installation (91 `VirtualInUdpCmd`,
  19 `VirtualOutCmd` über 26 Dateien) hat die vier Abweichungen in Spec 6.1,
  „Korrektur 2026-09-02" belegt, die die folgenden Tasks nachziehen.

---

### Task 2: Profiltabelle und Exportierbarkeit

Spec 6.6 hält fest, dass von 159 Attributen eines realen Geräts nur 109 auf einen
Loxone-Eingang abbildbar sind. Diese Task baut die Regel dafür.

**Files:**
- Create: `src/loxmatter/profiles/__init__.py`
- Create: `src/loxmatter/profiles/clusters.yaml`
- Create: `src/loxmatter/profiles/table.py`
- Create: `tests/profiles/test_table.py`
- Modify: `pyproject.toml` (Abhängigkeit `pyyaml`)

**Interfaces:**
- Consumes: `SignalRef`, `SignalKind` aus `loxmatter.matter.models`
- Produces:
  - `class Exportability(str, Enum)` — `ANALOG`, `DIGITAL`, `TEXT`, `NONE`
  - `classify(value: object) -> Exportability` — allein aus dem Wert
  - `class Profile` — frozen: `slug: str`, `unit: str`, `exportability: Exportability`
  - `lookup(ref: SignalRef, value: object) -> Profile` — Tabelle mit Fallback
  - `unit_format(unit: str) -> str` — Loxone-Formatstring für eine Einheit (Spec 7.3),
    z. B. `"kW"` → `"<v.6> kW"`; leere Einheit → `""`

- [ ] **Step 1: Write the failing test**

`tests/profiles/test_table.py`:

```python
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.table import Exportability, classify, lookup, unit_format


def test_bool_is_digital():
    assert classify(True) is Exportability.DIGITAL


def test_numbers_are_analog():
    assert classify(0) is Exportability.ANALOG
    assert classify(-42) is Exportability.ANALOG
    assert classify(1.5) is Exportability.ANALOG


def test_strings_are_text():
    assert classify("IKEA of Sweden") is Exportability.TEXT


def test_lists_and_structs_are_not_exportable():
    """Spec 6.6: Loxone hat fuer verschachtelte Werte keine Entsprechung."""
    assert classify([29, 31, 40]) is Exportability.NONE
    assert classify([{"0": 5, "1": True}]) is Exportability.NONE
    assert classify({"0": 5}) is Exportability.NONE


def test_null_is_not_exportable():
    """Spec 6.6: gelieferte Nullwerte sind eine eigene Kategorie."""
    assert classify(None) is Exportability.NONE


def test_known_attribute_gets_name_and_unit():
    ref = SignalRef(1, 1026, 0, SignalKind.ATTRIBUTE)  # TemperatureMeasurement
    profile = lookup(ref, 2150)
    assert profile.slug == "temp"
    assert profile.unit == "°C"
    assert profile.exportability is Exportability.ANALOG


def test_power_is_named_and_carries_kw():
    """Spec 7.3: Zieleinheit ist die des Loxone-Bausteins, nicht die SI-Einheit."""
    ref = SignalRef(2, 144, 8, SignalKind.ATTRIBUTE)  # ActivePower
    profile = lookup(ref, 5000)
    assert profile.slug == "power"
    assert profile.unit == "kW"


def test_unknown_cluster_still_gets_a_profile():
    """Spec 3.5: die Tabelle ist Anreicherung, kein Gatekeeper."""
    ref = SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 42)
    assert profile.exportability is Exportability.ANALOG
    assert profile.slug == "c64999_a7"
    assert profile.unit == ""


def test_unknown_cluster_with_unmappable_value_is_not_exportable():
    ref = SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)
    assert lookup(ref, [1, 2, 3]).exportability is Exportability.NONE


def test_events_are_digital_regardless_of_value():
    """Spec 6.3: ein Event wird zum Impuls, es hat keinen Wert."""
    ref = SignalRef(1, 59, 1, SignalKind.EVENT)
    assert lookup(ref, None).exportability is Exportability.DIGITAL


def test_unit_format_widens_power_to_six_decimals():
    """Spec 7.3: mit <v.3> zeigt ein 300-mW-Standby-Verbraucher 0.000 an."""
    assert unit_format("kW") == "<v.6> kW"
    assert unit_format("kWh") == "<v.6> kWh"


def test_unit_format_uses_one_decimal_for_the_common_units():
    assert unit_format("°C") == "<v.1> °C"
    assert unit_format("%") == "<v.1>%"
    assert unit_format("V") == "<v.1> V"
    assert unit_format("A") == "<v.1> A"


def test_unit_format_for_empty_unit_is_empty():
    assert unit_format("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/profiles/test_table.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.profiles'`
(bzw. `ImportError`, sobald `table.py` existiert, aber `unit_format` noch fehlt)

- [ ] **Step 3: Abhängigkeit ergänzen**

In `pyproject.toml` unter `dependencies` ergänzen: `"pyyaml>=6.0"`. Dann `uv sync`.

- [ ] **Step 4: Tabelle anlegen**

`src/loxmatter/profiles/clusters.yaml`:

```yaml
# Anreicherung fuer bekannte Cluster. Unbekanntes wird nicht verworfen, sondern
# bekommt einen generischen Namen (Spec 3.5).
#
# Zieleinheit ist die, die der Loxone-Baustein erwartet, nicht die SI-Einheit
# (Spec 7.3). Die Umrechnung selbst macht der UDP-Sender in Phase 4.
clusters:
  6:
    name: onoff
    attributes:
      0: {slug: onoff, unit: ""}
  8:
    name: level
    attributes:
      0: {slug: level, unit: "%"}
  1026:
    name: temperature
    attributes:
      0: {slug: temp, unit: "°C"}
  1029:
    name: humidity
    attributes:
      0: {slug: humidity, unit: "%"}
  144:
    name: power
    attributes:
      4: {slug: voltage, unit: "V"}
      5: {slug: current, unit: "A"}
      8: {slug: power, unit: "kW"}
  145:
    name: energy
    attributes:
      1: {slug: energy_imported, unit: "kWh"}
      2: {slug: energy_exported, unit: "kWh"}
  59:
    name: switch
    attributes:
      0: {slug: positions, unit: ""}
      1: {slug: position, unit: ""}
    events:
      1: {slug: press}
      2: {slug: longpress}
      3: {slug: shortrelease}
      4: {slug: longrelease}
      5: {slug: multipress_ongoing}
      6: {slug: multipress}
```

- [ ] **Step 5: Write minimal implementation**

`src/loxmatter/profiles/table.py`:

```python
"""Benennt Signale und entscheidet, ob sie nach Loxone exportierbar sind.

Grundsatz aus Spec 3.5: die Tabelle reichert an, sie filtert nicht. Ein
unbekannter Cluster bekommt einen generischen Namen und wird trotzdem
exportiert, sofern sein Wert ueberhaupt auf einen Loxone-Eingang passt.

Spec 6.6: Listen, Strukturen und Nullwerte passen nicht. Sie bleiben Signale
und sind in der Oberflaeche sichtbar, werden aber nie zu Loxone-Objekten.

Spec 7.3: `Unit` in der Vorlage ist ein Formatstring fuer die Loxone-Oberflaeche
(`<v.N> Einheit`), keine Einheitenbezeichnung. `unit_format` traegt diese
Abbildung als Datentabelle, nicht als Verzweigung im Exporter.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from loxmatter.matter.models import SignalKind, SignalRef

_TABLE_PATH = Path(__file__).with_name("clusters.yaml")


class Exportability(str, Enum):
    ANALOG = "analog"
    DIGITAL = "digital"
    TEXT = "text"
    NONE = "none"


@dataclass(frozen=True)
class Profile:
    slug: str
    unit: str
    exportability: Exportability


def classify(value: object) -> Exportability:
    """Entscheidet allein am Wert, ob Loxone ihn aufnehmen kann."""
    if isinstance(value, bool):
        return Exportability.DIGITAL
    if isinstance(value, (int, float)):
        return Exportability.ANALOG
    if isinstance(value, str):
        return Exportability.TEXT
    return Exportability.NONE


@functools.cache
def _table() -> dict[int, dict[str, Any]]:
    raw = yaml.safe_load(_TABLE_PATH.read_text(encoding="utf-8"))
    return {int(k): v for k, v in (raw.get("clusters") or {}).items()}


def lookup(ref: SignalRef, value: object) -> Profile:
    """Liefert Name, Einheit und Exportierbarkeit fuer ein Signal."""
    cluster = _table().get(ref.cluster_id, {})
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    entry = (cluster.get(section) or {}).get(ref.element_id)

    if ref.kind is SignalKind.EVENT:
        slug = entry["slug"] if entry else f"c{ref.cluster_id}_e{ref.element_id}"
        return Profile(slug=slug, unit="", exportability=Exportability.DIGITAL)

    if entry:
        return Profile(
            slug=entry["slug"], unit=entry.get("unit", ""), exportability=classify(value)
        )
    return Profile(
        slug=f"c{ref.cluster_id}_a{ref.element_id}",
        unit="",
        exportability=classify(value),
    )


# Nachkommastellen je Einheit fuer den Loxone-Formatstring (Spec 7.3). Leistung
# steht bewusst nicht bei den uebrigen physikalischen Groessen mit 1 Dezimale:
# von mW nach kW sind sechs Groessenordnungen, und mit <v.3> verschwindet ein
# 300-mW-Standby-Verbraucher als 0.000 auf der Oberflaeche.
_UNIT_DECIMALS: dict[str, int] = {
    "kW": 6,
    "kWh": 6,
    "°C": 1,
    "%": 1,
    "V": 1,
    "A": 1,
}

# Loxone schreibt vor Prozent keine Leerstelle (`<v>%`), vor jeder anderen
# Einheit dagegen schon (`<v.3> kW`, `<v.1> °C`) — belegt an den 26 realen
# Vorlagen aus Spec 6.1.
_UNITS_WITHOUT_LEADING_SPACE: frozenset[str] = frozenset({"%"})


def unit_format(unit: str) -> str:
    """Loxone-Formatstring fuer eine Einheit, oder "" wenn keine bekannt ist."""
    decimals = _UNIT_DECIMALS.get(unit)
    if decimals is None:
        return ""
    separator = "" if unit in _UNITS_WITHOUT_LEADING_SPACE else " "
    return f"<v.{decimals}>{separator}{unit}"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/profiles/test_table.py -v`
Expected: PASS, 13 Tests

- [ ] **Step 7: Gegen die echten Fixtures halten**

`tests/profiles/test_real_devices.py`:

```python
"""Prueft die Tabelle an den echten Geraeten aus Phase 1."""

import json
from pathlib import Path

from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.table import Exportability, lookup

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_plug_matches_the_breakdown_recorded_in_spec_6_6():
    """Spec 6.6, Tabelle: 102 analog, 7 digital, 13 Text, 37 nicht abbildbar."""
    snap = load("ikea_grillplats_plug.json")
    signals = extract_signals(snap)
    zaehlung = {kind: 0 for kind in Exportability}
    for ref in signals:
        zaehlung[lookup(ref, snap.attributes.get(ref.path)).exportability] += 1

    assert len(signals) == 159
    assert zaehlung[Exportability.ANALOG] == 102
    assert zaehlung[Exportability.DIGITAL] == 7
    assert zaehlung[Exportability.TEXT] == 13
    assert zaehlung[Exportability.NONE] == 37  # 32 Listen/Structs + 5 Nullwerte


def test_only_109_of_the_plugs_signals_reach_a_udp_input():
    """Nicht 45, sondern 50 fallen weg - die 5 Nullwerte kommen zu den 45 dazu."""
    snap = load("ikea_grillplats_plug.json")
    abbildbar = [
        ref
        for ref in extract_signals(snap)
        if lookup(ref, snap.attributes.get(ref.path)).exportability
        in (Exportability.ANALOG, Exportability.DIGITAL)
    ]
    assert len(abbildbar) == 109


def test_plug_power_attribute_carries_kw():
    snap = load("ikea_grillplats_plug.json")
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 8)
    assert lookup(ref, snap.attributes.get(ref.path)).unit == "kW"


def test_every_button_event_is_named():
    snap = load("ikea_bilresa_button.json")
    events = [s for s in extract_signals(snap) if s.cluster_id == 59 and s.kind.value == "event"]
    assert len(events) == 12
    assert all(not lookup(e, None).slug.startswith("c59_e") for e in events)
```

Run: `uv run pytest tests/profiles/ -v`

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/profiles tests/profiles pyproject.toml uv.lock
git commit -m "feat(profiles): Cluster-Tabelle und Exportierbarkeit nach Spec 6.6"
```

---

### Task 3: Persistenz und unveränderliche Schlüssel

Der Schlüssel ist die Verdrahtung in Loxone (Spec 6.2). Er muss einen Neustart, ein
Umbenennen und jeden weiteren Export überleben.

**Files:**
- Create: `src/loxmatter/model/__init__.py`
- Create: `src/loxmatter/model/store.py`
- Create: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `SignalRef`, `SignalKind`, `NodeSnapshot`; `lookup` aus `profiles.table`
- Produces:
  - `class Store` mit `__init__(self, path: Path | str)`, `close() -> None`
  - `Store.register_device(snapshot: NodeSnapshot) -> int` — liefert die stabile `device_id`
  - `Store.register_signals(device_id: int, snapshot: NodeSnapshot) -> list[StoredSignal]`
  - `class StoredSignal` — frozen: `key: str`, `ref: SignalRef`, `title: str`, `unit: str`, `exportability: Exportability`
  - `Store.signals(device_id: int) -> list[StoredSignal]`
  - `Store.udp_port(device_id: int) -> int`

- [ ] **Step 1: Write the failing test**

`tests/model/test_store.py`:

```python
import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    yield s
    s.close()


def test_device_id_is_stable_across_registrations(store):
    snap = load("ikea_grillplats_plug.json")
    first = store.register_device(snap)
    assert store.register_device(snap) == first


def test_device_id_is_never_reused(store):
    plug = load("ikea_grillplats_plug.json")
    button = load("ikea_bilresa_button.json")
    first = store.register_device(plug)
    store.forget_device(first)
    assert store.register_device(button) != first


def test_key_format_matches_spec_6_2(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    onoff = next(s for s in signals if s.ref.cluster_id == 6 and s.ref.element_id == 0)
    assert onoff.key == f"d{device_id}_1_onoff"


def test_key_survives_a_title_change(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    before = {s.ref: s.key for s in store.register_signals(device_id, snap)}
    store.set_title(before_key := next(iter(before.values())), "Kaffeemaschine")
    after = {s.ref: s.key for s in store.signals(device_id)}
    assert after == before
    assert any(s.title == "Kaffeemaschine" for s in store.signals(device_id) if s.key == before_key)


def test_keys_are_unique_within_a_device(store):
    snap = load("ikea_bilresa_button.json")
    device_id = store.register_device(snap)
    keys = [s.key for s in store.register_signals(device_id, snap)]
    assert len(keys) == len(set(keys))


def test_reregistering_keeps_existing_keys_and_adds_new_ones(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    before = {s.ref: s.key for s in store.register_signals(device_id, snap)}
    again = {s.ref: s.key for s in store.register_signals(device_id, snap)}
    assert again == before


def test_all_devices_share_the_default_udp_port(store):
    plug = store.register_device(load("ikea_grillplats_plug.json"))
    button = store.register_device(load("ikea_bilresa_button.json"))
    assert store.udp_port(plug) == store.udp_port(button) == 7000


def test_store_survives_reopening(tmp_path):
    path = tmp_path / "persist.sqlite"
    snap = load("ikea_grillplats_plug.json")
    first = Store(path)
    device_id = first.register_device(snap)
    keys = {s.key for s in first.register_signals(device_id, snap)}
    first.close()

    second = Store(path)
    assert second.register_device(snap) == device_id
    assert {s.key for s in second.signals(device_id)} == keys
    second.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/model/test_store.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.model'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/model/store.py`:

```python
"""SQLite-Ablage fuer Geraete und Signale.

Der Schluessel eines Signals ist die Verdrahtung in Loxone (Spec 6.2). Er wird
einmal vergeben und danach nie geaendert — weder beim Umbenennen noch bei einem
erneuten Einlesen desselben Geraets. Deshalb liegt er in einer Datenbank und
nicht in einer Ableitung zur Laufzeit.

device_id wird nie wiederverwendet: ein entferntes und neu eingelerntes Geraet
bekommt neue Schluessel, damit es keine alte Verdrahtung stillschweigend erbt.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.profiles.table import Exportability, lookup

DEFAULT_UDP_PORT = 7000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id  TEXT NOT NULL,
    node_id    INTEGER NOT NULL,
    label      TEXT NOT NULL,
    udp_port   INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER NOT NULL REFERENCES device(id),
    endpoint      INTEGER NOT NULL,
    cluster_id    INTEGER NOT NULL,
    element_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    unit          TEXT NOT NULL,
    exportability TEXT NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, element_id, kind)
);
"""


@dataclass(frozen=True)
class StoredSignal:
    key: str
    ref: SignalRef
    title: str
    unit: str
    exportability: Exportability


class Store:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _device_identity(self, snapshot: NodeSnapshot) -> str:
        """Faellt auf die Node-ID zurueck: manche Geraete melden keine UniqueID (Spec 7.2)."""
        return snapshot.unique_id or f"node:{snapshot.node_id}"

    def register_device(self, snapshot: NodeSnapshot) -> int:
        identity = self._device_identity(snapshot)
        row = self._db.execute(
            "SELECT id FROM device WHERE unique_id = ? AND active = 1", (identity,)
        ).fetchone()
        if row is not None:
            return int(row["id"])

        label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or identity
        cur = self._db.execute(
            "INSERT INTO device (unique_id, node_id, label, udp_port) VALUES (?, ?, ?, ?)",
            (identity, snapshot.node_id, label, DEFAULT_UDP_PORT),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def forget_device(self, device_id: int) -> None:
        """Markiert ein Geraet als entfernt. Die id bleibt vergeben (Spec 6.2)."""
        self._db.execute("UPDATE device SET active = 0 WHERE id = ?", (device_id,))
        self._db.commit()

    def udp_port(self, device_id: int) -> int:
        row = self._db.execute("SELECT udp_port FROM device WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            raise KeyError(f"unbekanntes Geraet {device_id}")
        return int(row["udp_port"])

    def register_signals(self, device_id: int, snapshot: NodeSnapshot) -> list[StoredSignal]:
        for ref in extract_signals(snapshot):
            profile = lookup(ref, snapshot.attributes.get(ref.path))
            key = f"d{device_id}_{ref.endpoint}_{profile.slug}"
            self._db.execute(
                "INSERT OR IGNORE INTO signal "
                "(device_id, endpoint, cluster_id, element_id, kind, key, title, unit, exportability)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device_id,
                    ref.endpoint,
                    ref.cluster_id,
                    ref.element_id,
                    ref.kind.value,
                    key,
                    profile.slug,
                    profile.unit,
                    profile.exportability.value,
                ),
            )
        self._db.commit()
        return self.signals(device_id)

    def set_title(self, key: str, title: str) -> None:
        self._db.execute("UPDATE signal SET title = ? WHERE key = ?", (title, key))
        self._db.commit()

    def signals(self, device_id: int) -> list[StoredSignal]:
        rows = self._db.execute(
            "SELECT * FROM signal WHERE device_id = ? ORDER BY endpoint, cluster_id, element_id",
            (device_id,),
        ).fetchall()
        return [
            StoredSignal(
                key=r["key"],
                ref=SignalRef(
                    r["endpoint"], r["cluster_id"], r["element_id"], SignalKind(r["kind"])
                ),
                title=r["title"],
                unit=r["unit"],
                exportability=Exportability(r["exportability"]),
            )
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/model/test_store.py -v`
Expected: PASS, 8 Tests

Der Test `test_keys_are_unique_within_a_device` wird am Taster fehlschlagen, wenn
zwei Signale denselben Slug auf demselben Endpoint tragen. **Das ist kein
Testfehler, sondern eine echte Kollision**: der Schluessel muss eindeutig sein.
Ergaenze in dem Fall die Element-ID im Slug und halte die Regel in Spec 6.2 fest.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/model tests/model
git commit -m "feat(model): SQLite-Ablage mit unveraenderlichen Schluesseln"
```

---

### Task 4: Signale zu Loxone-Objekten aufbereiten

Spec 6.3: ein Event wird zu **zwei** Loxone-Objekten — einem digitalen Impuls und
einem monotonen Zähler. Spec 6.5: pro Gerät kommt ein `_online`-Signal dazu.

**Files:**
- Create: `src/loxmatter/export/signals.py`
- Create: `tests/export/test_signals.py`

**Interfaces:**
- Consumes: `StoredSignal`, `Exportability`; `unit_format` aus `profiles.table`
- Produces:
  - `class LoxoneInput` — frozen: `key: str`, `title: str`, `comment: str`, `analog: bool`, `unit_format: str`
  - `to_inputs(signals: Sequence[StoredSignal], device_id: int, device_label: str) -> list[LoxoneInput]`

- [ ] **Step 1: Write the failing test**

`tests/export/test_signals.py`:

```python
import pytest

from loxmatter.export.signals import to_inputs
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.model.store import StoredSignal
from loxmatter.profiles.table import Exportability


def signal(key, kind=SignalKind.ATTRIBUTE, exportability=Exportability.ANALOG, unit=""):
    return StoredSignal(
        key=key,
        ref=SignalRef(1, 6, 0, kind),
        title=key,
        unit=unit,
        exportability=exportability,
    )


def test_analog_attribute_becomes_one_analog_input():
    inputs = to_inputs([signal("d1_1_temp", unit="°C")], 1, "Wohnzimmer")
    assert [i.key for i in inputs] == ["d1_1_temp", "d1_online"]
    assert inputs[0].analog is True
    assert inputs[0].unit_format == "<v.1> °C"


def test_digital_attribute_becomes_one_digital_input():
    inputs = to_inputs([signal("d1_1_onoff", exportability=Exportability.DIGITAL)], 1, "Steckdose")
    assert inputs[0].analog is False
    assert inputs[0].unit_format == ""


def test_event_becomes_a_pulse_and_a_counter():
    """Spec 6.3: der Impuls erzeugt die Flanke, der Zaehler ueberlebt ein verlorenes Paket."""
    inputs = to_inputs(
        [signal("d1_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL)],
        1,
        "Taster",
    )
    keys = [i.key for i in inputs]
    assert "d1_1_press" in keys
    assert "d1_1_press_n" in keys
    pulse = next(i for i in inputs if i.key == "d1_1_press")
    counter = next(i for i in inputs if i.key == "d1_1_press_n")
    assert pulse.analog is False
    assert counter.analog is True
    assert pulse.unit_format == ""
    assert counter.unit_format == ""


def test_non_exportable_signals_are_skipped():
    """Spec 6.6: Listen und Strukturen werden nie zu Loxone-Objekten."""
    inputs = to_inputs([signal("d1_1_parts", exportability=Exportability.NONE)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_text_signals_are_skipped_for_now():
    """Der virtuelle Texteingang ist ein eigener Vorlagentyp — spaetere Ausbaustufe."""
    inputs = to_inputs([signal("d1_1_vendor", exportability=Exportability.TEXT)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_online_signal_is_added_once_per_device():
    """Spec 6.5: kostet nichts und beantwortet die haeufigste Frage."""
    inputs = to_inputs([signal("d1_1_a"), signal("d1_1_b")], 1, "Geraet")
    assert [i.key for i in inputs].count("d1_online") == 1
    online = next(i for i in inputs if i.key == "d1_online")
    assert online.analog is False


def test_unit_no_longer_lands_in_the_comment():
    """Die Einheit stand frueher im Kommentar; jetzt traegt sie unit_format (Spec 7.3)."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert "kW" not in power.comment
    assert power.unit_format


def test_power_unit_gets_the_widened_six_decimal_format():
    """Spec 7.3: mit dem sonst ueblichen <v.3> zeigt ein 300-mW-Standby-
    Verbraucher 0.000 an — deshalb <v.6> fuer Leistung."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert power.unit_format == "<v.6> kW"


def test_empty_signal_list_still_yields_the_online_input():
    assert [i.key for i in to_inputs([], 7, "Leer")] == ["d7_online"]


def test_event_counter_key_colliding_with_another_signal_raises():
    """Regression: die `_n`-Endung ist nirgends reserviert. Ein `clusters.yaml`-
    Slug kann zufaellig genau auf den Zaehler-Schluessel eines Events treffen —
    das darf nie still zwei identische `LoxoneInput`s erzeugen (siehe Review)."""
    event = signal("d3_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL)
    collider = signal("d3_1_press_n")
    with pytest.raises(ValueError, match="d3_1_press_n"):
        to_inputs([event, collider], 3, "Taster")


def test_signal_from_a_different_device_raises():
    """Regression: der Praefix wurde frueher aus den Daten geraten und ist
    jetzt ein expliziter Parameter — ein falsch zugeordnetes Signal muss laut
    scheitern statt ein Geraet stillschweigend falsch zu beschriften."""
    foreign = signal("d9_1_temp")
    with pytest.raises(ValueError, match="d9_1_temp"):
        to_inputs([foreign], 3, "Taster")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_signals.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.export.signals'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/export/signals.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_signals.py -v`
Expected: PASS, 11 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/export/signals.py tests/export/test_signals.py
git commit -m "feat(export): Events zu Impuls und Zaehler, Online-Signal je Geraet"
```

---

### Task 5: Vorlagendokumente pro Gerät

**Files:**
- Create: `src/loxmatter/export/documents.py`
- Create: `tests/export/test_documents.py`

**Interfaces:**
- Consumes: `render_document` aus `export.xml`, `LoxoneInput` aus `export.signals`
- Produces:
  - `render_virtual_in_udp(device_label: str, bridge_ip: str, port: int, inputs: Sequence[LoxoneInput]) -> bytes`
    — schreibt vor den `VirtualInUdpCmd`-Kindern ein `<Info templateType="1" minVersion="14040925"/>`
  - `render_virtual_out(device_label: str, base_url: str, commands: Sequence[LoxoneCommand]) -> bytes`
    — schreibt vor den `VirtualOutCmd`-Kindern ein `<Info templateType="3" minVersion="14040925"/>`
  - `class LoxoneCommand` — frozen: `key: str`, `title: str`, `path: str`, `analog: bool`
  - `filename_for(prefix: str, device_id: int, device_label: str) -> str`

`minVersion="14040925"` ist für beide Vorlagentypen der niedrigste an den 26 realen
Vorlagen beobachtete Wert (Spec 6.1, „Korrektur 2026-09-02") — er gate also die
wenigsten Config-Versionen aus. Ob Loxone Config diesen Wert tatsächlich akzeptiert,
prüft nicht diese Task, sondern der Import-Beleg in Task 7 Schritt 6.

- [ ] **Step 1: Write the failing test**

`tests/export/test_documents.py`:

```python
from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.signals import LoxoneInput


def inputs():
    return [
        LoxoneInput("d1_1_temp", "Temperatur", "Wohnzimmer · 1/1026/0", True, "<v.1> °C"),
        LoxoneInput("d1_online", "erreichbar", "Wohnzimmer", False, ""),
    ]


def text_of(raw: bytes) -> str:
    return raw.decode("utf-8-sig")


def test_root_carries_title_address_and_port():
    out = text_of(render_virtual_in_udp("Wohnzimmerlampe", "192.168.1.50", 7000, inputs()))
    assert 'Title="Matter — Wohnzimmerlampe"' in out
    assert 'Address="192.168.1.50"' in out
    assert 'Port="7000"' in out


def test_each_input_becomes_a_command_with_its_check_pattern():
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    assert 'Check="d1_1_temp:\\v"' in out
    assert 'Check="d1_online:\\v"' in out
    assert out.count("<VirtualInUdpCmd ") == 2


def test_analog_flag_follows_the_input():
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    temp = next(line for line in out.splitlines() if "d1_1_temp" in line)
    online = next(line for line in out.splitlines() if "d1_online" in line)
    assert 'Analog="true"' in temp
    assert 'Analog="false"' in online


def test_defaults_from_the_verified_schema_are_present():
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    for attr in (
        "Signed=",
        "SourceValLow=",
        "DestValHigh=",
        "DefVal=",
        "MinVal=",
        "MaxVal=",
        "Unit=",
        "HintText=",
    ):
        assert attr in out


def test_unit_format_is_escaped_into_the_unit_attribute():
    """Spec 6.1, Korrektur 2026-09-02: VirtualInUdpCmd hat 15 Attribute, u. a. Unit."""
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    assert 'Unit="&lt;v.1&gt; °C"' in out


def test_info_element_is_the_first_child_of_virtual_in_udp():
    """Spec 6.1, Korrektur 2026-09-02: jede Vorlage traegt ein Info-Element als erstes Kind."""
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    body_after_root = out.split(">", 1)[1]
    assert body_after_root.lstrip().startswith('<Info templateType="1" minVersion="14040925"/>')


def test_virtual_out_escapes_the_value_placeholder():
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_level", "Helligkeit", "/cmd/d1_1_level/<v>", True)],
        )
    )
    assert "&lt;v&gt;" in out
    assert "/<v>" not in out


def test_virtual_out_carries_method_and_address():
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_onoff", "Schalten", "/cmd/d1_1_onoff/1", False)],
        )
    )
    assert 'Address="http://192.168.1.50:8080"' in out
    assert 'CmdOnMethod="GET"' in out
    assert 'CmdOffMethod="GET"' in out


def test_virtual_out_cmd_has_no_id_attribute():
    """Spec 6.1, Korrektur 2026-09-02: VirtualOutCmd hat 15 Attribute und kein ID."""
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_onoff", "Schalten", "/cmd/d1_1_onoff/1", False)],
        )
    )
    assert 'ID="' not in out


def test_info_element_is_the_first_child_of_virtual_out():
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_onoff", "Schalten", "/cmd/d1_1_onoff/1", False)],
        )
    )
    body_after_root = out.split(">", 1)[1]
    assert body_after_root.lstrip().startswith('<Info templateType="3" minVersion="14040925"/>')


def test_filenames_follow_the_spec_prefixes():
    assert filename_for("VIU", 12, "Wohnzimmer Lampe") == "VIU_d12_Wohnzimmer_Lampe.xml"
    assert filename_for("VO", 7, "Küche/Steckdose") == "VO_d7_Kueche_Steckdose.xml"


def test_filename_is_ascii_only():
    name = filename_for("VIU", 3, "Büro Ölheizung —Süd")
    assert name.isascii()
    assert name.startswith("VIU_") and name.endswith(".xml")


def test_filenames_of_labels_differing_only_by_separator_do_not_collide():
    """ "Lampe 1", "Lampe_1" und "Lampe-1" normalisieren alle auf dasselbe
    Label-Segment — auf verschiedenen Geraeten muss die ID sie trotzdem
    trennen."""
    space = filename_for("VIU", 1, "Lampe 1")
    underscore = filename_for("VIU", 2, "Lampe_1")
    hyphen = filename_for("VIU", 3, "Lampe-1")
    assert len({space, underscore, hyphen}) == 3


def test_filename_with_empty_label_has_no_trailing_separator_or_empty_segment():
    """Ein Label, das komplett wegnormalisiert (nicht-ASCII, leer, nur
    Sonderzeichen), darf weder mit "_" enden noch ein leeres "__"-Segment
    hinterlassen — die Datei bleibt trotzdem eindeutig ueber die ID."""
    for label in ("厨房", "", "!!!"):
        name = filename_for("VIU", 12, label)
        assert name == "VIU_d12.xml"
        assert not name.endswith("_.xml")
        assert "__" not in name


def test_same_label_on_different_device_ids_never_collides():
    first = filename_for("VIU", 1, "Wohnzimmerlampe")
    second = filename_for("VIU", 2, "Wohnzimmerlampe")
    assert first != second
    assert first == "VIU_d1_Wohnzimmerlampe.xml"
    assert second == "VIU_d2_Wohnzimmerlampe.xml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_documents.py -v`
Expected: FAIL mit `ImportError: cannot import name 'render_virtual_in_udp'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/export/documents.py`:

```python
"""Setzt die beiden Vorlagentypen aus Spec 6.1 zusammen.

Ein VirtualInUdp traegt beliebig viele Befehle, ein Import bringt damit alle
Signale eines Geraets auf einmal ins Projekt. Eine Datei je Geraet — bei 200
Eingaengen in einem Objekt waere die Config nicht mehr navigierbar (Spec 6.2).

Die Attributnamen und ihre Defaults stammen aus dem verifizierten Schema in
Spec 6.1. Sie sind nicht frei waehlbar.

Spec 6.1, „Korrektur 2026-09-02": das Schema stammte urspruenglich aus einer
fremden Referenzimplementierung und wich in vier Punkten von dem ab, was
Loxone Config an 26 realen Vorlagen tatsaechlich schreibt — belegt, nicht
vermutet. Diese Task zieht die vier Korrekturen nach:

1. Jede Vorlage traegt ein `<Info>` als erstes Kind. `templateType` ist `1`
   fuer `VirtualInUdp`, `3` fuer `VirtualOut`. `minVersion="14040925"` ist fuer
   beide der niedrigste an den 26 Vorlagen beobachtete Wert — er gate also die
   wenigsten Config-Versionen. Ob Loxone Config diesen Wert wirklich
   akzeptiert, entscheidet nicht dieser Code, sondern der Import-Beleg in
   Task 7 Schritt 6.
2. `VirtualInUdpCmd` hat 15 Attribute, u. a. `Unit` (Formatstring, Spec 7.3)
   und `HintText`.
3. `VirtualOut` traegt `HintText` zwischen `CmdInit` und `CloseAfterSend`.
4. `VirtualOutCmd` hat 15 Attribute, kein `ID`, und `CmdOnMethod`/`CmdOffMethod`
   stehen zusammen statt verteilt.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter.export.signals import LoxoneInput
from loxmatter.export.xml import render_document

_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}

# Niedrigster an den 26 realen Vorlagen (Spec 6.1) beobachteter Wert je
# Vorlagentyp — gate damit die wenigsten Config-Versionen aus. Der eigentliche
# Beleg, dass Loxone Config diesen Wert akzeptiert, ist der Import in Task 7.
_MIN_VERSION = "14040925"


@dataclass(frozen=True)
class LoxoneCommand:
    key: str
    title: str
    path: str
    analog: bool


def _flag(value: bool) -> str:
    return "true" if value else "false"


def render_virtual_in_udp(
    device_label: str,
    bridge_ip: str,
    port: int,
    inputs: Sequence[LoxoneInput],
) -> bytes:
    info = ("Info", [("templateType", "1"), ("minVersion", _MIN_VERSION)])
    children = [
        (
            "VirtualInUdpCmd",
            [
                ("Title", entry.title),
                ("Comment", entry.comment),
                ("Address", ""),
                ("Check", f"{entry.key}:\\v"),
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
            ],
        )
        for entry in inputs
    ]
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


def render_virtual_out(
    device_label: str,
    base_url: str,
    commands: Sequence[LoxoneCommand],
) -> bytes:
    info = ("Info", [("templateType", "3"), ("minVersion", _MIN_VERSION)])
    children = [
        (
            "VirtualOutCmd",
            [
                ("Title", command.title),
                ("Comment", command.key),
                ("CmdOnMethod", "GET"),
                ("CmdOffMethod", "GET"),
                ("CmdOn", command.path),
                ("CmdOnHTTP", ""),
                ("CmdOnPost", ""),
                ("CmdOff", ""),
                ("CmdOffHTTP", ""),
                ("CmdOffPost", ""),
                ("CmdAnswer", ""),
                ("HintText", ""),
                ("Analog", _flag(command.analog)),
                ("Repeat", "0"),
                ("RepeatRate", "0"),
            ],
        )
        for command in commands
    ]
    return render_document(
        "VirtualOut",
        [
            ("Title", f"Matter — {device_label}"),
            ("Comment", "erzeugt von loxmatter"),
            ("Address", base_url),
            ("CmdInit", ""),
            ("HintText", ""),
            ("CloseAfterSend", "true"),
            ("CmdSep", ""),
        ],
        [info, *children],
    )


def filename_for(prefix: str, device_id: int, device_label: str) -> str:
    """Dateiname nach Spec 6.1, auf ASCII normalisiert.

    `device_id` ist nicht Dekoration — er ist der einzige Teil des Namens,
    der Eindeutigkeit garantiert. `Store` vergibt ihn unveraenderlich und
    verwendet ihn nirgends doppelt (siehe `export.signals`); die Normalisierung
    unten dagegen ist verlustbehaftet und bildet absichtlich viele
    unterschiedliche Labels ("Lampe 1", "Lampe_1", "Lampe-1", "厨房", "")
    auf denselben oder einen leeren String ab. Ohne die Geraete-ID wuerden
    zwei Geraete mit kollidierendem Label sich beim Export gegenseitig
    ueberschreiben — der Nutzer importiert dann eine Vorlage im Glauben, es
    seien zwei. Also: die ID hier NICHT entfernen, auch wenn sie im Namen
    redundant zum Label aussieht.

    Das Label bleibt trotzdem im Namen — es macht die Datei fuer einen
    Menschen wiedererkennbar, waehrend die ID sie eindeutig macht.
    """
    text = "".join(_UMLAUTS.get(char, char) for char in device_label)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    safe = "".join(char if char.isalnum() else "_" for char in text)
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_")
    stem = f"{prefix}_d{device_id}"
    if safe:
        stem = f"{stem}_{safe}"
    return f"{stem}.xml"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_documents.py -v`
Expected: PASS, 15 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/export/documents.py tests/export/test_documents.py
git commit -m "feat(export): Vorlagendokumente pro Geraet nach Spec 6.1"
```

---

### Task 6: Kommando-Erlaubnisliste

**Der Plan hätte hier ursprünglich für jedes lesbare Attribut einen Ausgangsbefehl
erzeugt — rund 109 bei der Steckdose, von denen fast keiner etwas bewirkt.**
Matter-Attribute sind ganz überwiegend nur lesbar. Die richtige Quelle ist
`AcceptedCommandList` (0xFFF9): sie sagt pro Cluster, welche Kommandos ein Gerät
annimmt. An den Fixtures gemessen:

| Gerät | steuerbar |
|---|---|
| GRILLPLATS Plug | `1/6` OnOff, Kommandos 0 (Aus), 1 (Ein), 2 (Umschalten), 64–66 |
| BILRESA Taster | nichts Nutzbares — nur Identify und Verwaltungscluster |

**Sicherheitsregel: Erlaubnisliste, nicht Sperrliste.** Zu den akzeptierten Kommandos
gehören Verwaltungscluster — `0/62` OperationalCredentials enthält `RemoveFabric`,
`0/48` GeneralCommissioning, `0/49` NetworkCommissioning, `0/51` GeneralDiagnostics
enthält `TestEventTrigger`. Ein Exporter, der stumpf alles ausgibt, legt einem
Loxone-Nutzer Befehle auf den Baustein, mit denen sich das Gerät aus der Fabric werfen
lässt. Bei Attributen wird Unbekanntes großzügig durchgereicht; bei Kommandos ist das
genau falsch herum.

Verwaltungscluster bleiben **auch im Rohmodus** draußen. Das ist keine Vorsichtsmaßnahme,
die man abschalten kann.

**Files:**
- Create: `src/loxmatter/export/commands.py`
- Modify: `src/loxmatter/profiles/clusters.yaml` (Abschnitt `commands`)
- Modify: `src/loxmatter/profiles/table.py`
- Create: `tests/export/test_commands.py`

**Interfaces:**
- Consumes: `NodeSnapshot`, `parse_attribute_path`, `ACCEPTED_COMMAND_LIST_ID` aus `matter.paths`
- Produces:
  - `ADMINISTRATIVE_CLUSTERS: frozenset[int]` in `profiles.table`
  - `command_slug(cluster_id: int, command_id: int) -> str | None` in `profiles.table` — `None`, wenn nicht in der Tabelle
  - `class DeviceCommand` — frozen: `endpoint: int`, `cluster_id: int`, `command_id: int`, `slug: str`, `takes_value: bool`
  - `extract_commands(snapshot: NodeSnapshot, *, raw: bool = False) -> list[DeviceCommand]`

- [ ] **Step 1: Tabelle um Kommandos erweitern**

In `src/loxmatter/profiles/clusters.yaml` beim Cluster 6 ergänzen:

```yaml
  6:
    name: onoff
    attributes:
      0: {slug: onoff, unit: ""}
    commands:
      0: {slug: off, takes_value: false}
      1: {slug: on, takes_value: false}
      2: {slug: toggle, takes_value: false}
```

Und beim Cluster 8 (LevelControl) ergänzen:

```yaml
    commands:
      0: {slug: level, takes_value: true}
      4: {slug: level_onoff, takes_value: true}
```

- [ ] **Step 2: Write the failing test**

`tests/export/test_commands.py`:

```python
import json
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.table import ADMINISTRATIVE_CLUSTERS, command_slug

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_administrative_clusters_are_named():
    """Diese Cluster duerfen nie als Loxone-Ausgang erscheinen."""
    for cluster in (42, 48, 49, 51, 60, 62, 63):
        assert cluster in ADMINISTRATIVE_CLUSTERS


def test_known_command_has_a_slug():
    assert command_slug(6, 0) == "off"
    assert command_slug(6, 1) == "on"
    assert command_slug(6, 2) == "toggle"


def test_unknown_command_has_none():
    assert command_slug(6, 99) is None
    assert command_slug(64999, 0) is None


def test_plug_yields_only_the_onoff_commands():
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert {(c.cluster_id, c.command_id) for c in commands} == {(6, 0), (6, 1), (6, 2)}
    assert all(c.endpoint == 1 for c in commands)


def test_button_yields_no_commands():
    """Ein Taster ist ein Eingabegeraet."""
    assert extract_commands(load("ikea_bilresa_button.json")) == []


def test_administrative_commands_never_appear():
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in commands)


def test_raw_mode_adds_unknown_clusters_but_not_administrative_ones():
    """Der Rohmodus erweitert die Erlaubnisliste - er hebt die Sicherheitsregel nicht auf."""
    plug = load("ikea_grillplats_plug.json")
    roh = extract_commands(plug, raw=True)
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in roh)
    assert len(roh) > len(extract_commands(plug))
    assert any(c.cluster_id == 4 for c in roh)  # Groups, unbekannt aber harmlos


def test_raw_mode_names_unknown_commands_generically():
    roh = extract_commands(load("ikea_grillplats_plug.json"), raw=True)
    unbekannt = next(c for c in roh if c.cluster_id == 4)
    assert unbekannt.slug.startswith("c4_cmd")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/export/test_commands.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.export.commands'`

- [ ] **Step 4: `profiles/table.py` erweitern**

```python
# Cluster, deren Kommandos nie als Loxone-Ausgang erscheinen duerfen. 0/62 enthaelt
# RemoveFabric, 0/48 und 0/49 die Kommissionierung, 0/51 den TestEventTrigger. Ein
# Loxone-Baustein, der eines davon ausloest, kann das Geraet unbrauchbar machen.
# Diese Liste gilt auch im Rohmodus.
ADMINISTRATIVE_CLUSTERS: frozenset[int] = frozenset(
    {42, 48, 49, 51, 52, 53, 54, 55, 60, 62, 63, 70}
)


def command_slug(cluster_id: int, command_id: int) -> str | None:
    """Name eines Kommandos, oder None wenn es nicht in der Tabelle steht."""
    entry = (_table().get(cluster_id, {}).get("commands") or {}).get(command_id)
    return entry["slug"] if entry else None


def command_takes_value(cluster_id: int, command_id: int) -> bool:
    entry = (_table().get(cluster_id, {}).get("commands") or {}).get(command_id)
    return bool(entry and entry.get("takes_value"))
```

- [ ] **Step 5: `export/commands.py` schreiben**

```python
"""Leitet aus AcceptedCommandList ab, was Loxone einem Geraet sagen darf.

Nicht aus den Attributen: Matter-Attribute sind ganz ueberwiegend nur lesbar,
und ein Ausgangsbefehl je lesbarem Attribut waere zu 95 Prozent wirkungslos.

Erlaubnisliste statt Sperrliste. Bei Attributen wird Unbekanntes grosszuegig
durchgereicht; bei Kommandos waere das falsch herum, weil zu den akzeptierten
Kommandos die Verwaltungscluster gehoeren - RemoveFabric, Kommissionierung,
TestEventTrigger. ADMINISTRATIVE_CLUSTERS bleibt auch im Rohmodus gesperrt.
"""

from __future__ import annotations

from dataclasses import dataclass

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID, parse_attribute_path
from loxmatter.profiles.table import (
    ADMINISTRATIVE_CLUSTERS,
    command_slug,
    command_takes_value,
)


@dataclass(frozen=True, order=True)
class DeviceCommand:
    endpoint: int
    cluster_id: int
    command_id: int
    slug: str
    takes_value: bool


def extract_commands(snapshot: NodeSnapshot, *, raw: bool = False) -> list[DeviceCommand]:
    """Alle Kommandos, die als Loxone-Ausgang erscheinen duerfen."""
    commands: list[DeviceCommand] = []

    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        if attribute_id != ACCEPTED_COMMAND_LIST_ID:
            continue
        if cluster_id in ADMINISTRATIVE_CLUSTERS:
            continue
        if not isinstance(value, (list, tuple)):
            continue

        for command_id in (int(c) for c in value if isinstance(c, (int, float))):
            slug = command_slug(cluster_id, command_id)
            if slug is None:
                if not raw:
                    continue
                slug = f"c{cluster_id}_cmd{command_id}"
            commands.append(
                DeviceCommand(
                    endpoint=endpoint,
                    cluster_id=cluster_id,
                    command_id=command_id,
                    slug=slug,
                    takes_value=command_takes_value(cluster_id, command_id),
                )
            )

    return sorted(commands)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/export/test_commands.py -v`
Expected: PASS, 8 Tests

Schlägt `test_plug_yields_only_the_onoff_commands` mit zusätzlichen Treffern fehl,
steht ein Cluster in der Tabelle, der dort nicht hingehört — **nicht** den Test
anpassen, sondern die Tabelle prüfen.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/export/commands.py src/loxmatter/profiles tests/export/test_commands.py
git commit -m "feat(export): Ausgangsbefehle aus AcceptedCommandList mit Erlaubnisliste"
```

---

### Task 7: `loxmatter export` und der Beleg an echten Geräten

**Files:**
- Modify: `src/loxmatter/cli.py`
- Create: `tests/test_export_cli.py`
- Create: `tests/conftest.py` (autouse-Fixture, isoliert `--store-path` von der echten
  Home-Datenbank in der gesamten Testsuite)
- Create: `tests/test_store_path.py` (Rangfolge `--store-path` / `LOXMATTER_STORE` /
  Standard, sowie der Beleg für stabile Schlüssel über zwei Exporte durch dieselbe
  Datenbank)

**Interfaces:**
- Consumes: alles aus Task 1–5
- Produces: CLI-Kommando `export`

- [ ] **Step 1: Write the failing test**

`tests/test_export_cli.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from loxmatter.cli import app

FIXTURES = Path(__file__).parent / "fixtures" / "nodes"


def test_export_writes_both_templates_per_device(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    written = sorted(p.name for p in tmp_path.glob("*.xml"))
    assert len(written) == 2
    assert any(n.startswith("VIU_") for n in written)
    assert any(n.startswith("VO_") for n in written)


def test_exported_file_is_utf8_with_bom_and_crlf(tmp_path):
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    raw = next(tmp_path.glob("VIU_*.xml")).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_button_events_appear_as_pulse_and_counter(tmp_path):
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_bilresa_button.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VIU_*.xml")).read_text(encoding="utf-8-sig")
    assert "_press:\\v" in text
    assert "_press_n:\\v" in text


def test_non_exportable_attributes_do_not_appear(tmp_path):
    """Spec 6.6: von 159 Attributen erreichen nur 109 einen UDP-Eingang."""
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VIU_*.xml")).read_text(encoding="utf-8-sig")
    commands = text.count("<VirtualInUdpCmd ")
    assert commands == 109 + 1  # abbildbare Attribute plus Online-Signal


def test_plug_gets_only_the_onoff_commands(tmp_path):
    """Task 6: Ausgangsbefehle stammen aus AcceptedCommandList, nicht aus Attributen."""
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VO_*.xml")).read_text(encoding="utf-8-sig")
    assert text.count("<VirtualOutCmd ") == 3


def test_button_gets_no_output_commands(tmp_path):
    """Ein Taster ist ein Eingabegeraet - die VO_-Datei bleibt leer."""
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_bilresa_button.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VO_*.xml")).read_text(encoding="utf-8-sig")
    assert text.count("<VirtualOutCmd ") == 0


def test_export_reports_what_it_skipped(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert "50" in result.stdout
    assert "nicht exportierbar" in result.stdout


def test_export_fails_cleanly_when_the_second_file_cannot_be_written(tmp_path, monkeypatch):
    """Ein OSError beim zweiten write_bytes darf keinen Traceback zeigen, sondern muss
    ueber _fail() laufen — und dabei sagen, welche Datei bereits geschrieben wurde und
    welche fehlt."""
    original_write_bytes = Path.write_bytes

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        if self.name.startswith("VO_"):
            raise OSError("Kein Speicherplatz mehr auf dem Geraet")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    written = sorted(p.name for p in tmp_path.glob("*.xml"))
    assert len(written) == 1
    assert written[0].startswith("VIU_")
    assert "VO_" in result.stderr
    assert "VIU_" in result.stderr


def test_export_requires_node_or_fixture(tmp_path):
    """export teilt sich _load_snapshot mit inspect — dessen Fehlerpfade sind sonst nur
    ueber inspect getestet, nicht ueber export selbst."""
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "entweder --node oder --fixture angeben" in result.output


def test_export_reports_malformed_fixture_missing_node_id(tmp_path):
    """Dieselbe deutsche Meldung wie bei inspect (test_cli.py), hier ueber den
    export-Einstiegspunkt ausgeloest."""
    broken = tmp_path / "broken.json"
    broken.write_text('{"attributes": {}}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(broken),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "node_id" in result.stderr
```

`tests/conftest.py` (autouse für die gesamte Suite, siehe unten warum) und
`tests/test_store_path.py` (Rangfolge `--store-path` / `LOXMATTER_STORE` / Standard,
plus der Beleg für stabile Schlüssel über zwei Exporte durch dieselbe Datenbank sowie
unterschiedliche `device_id`s durch zwei getrennte Datenbanken) — beide sind Teil
dieses Tasks; ihr Inhalt steht bei der Implementierung von `--store-path` unten.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_export_cli.py -v`
Expected: FAIL — das Kommando `export` existiert nicht

- [ ] **Step 3: Write minimal implementation**

In `src/loxmatter/cli.py` ergänzen (Importe oben, Kommando unten):

```python
@app.command()
def export(
    fixture: Path | None = typer.Option(  # noqa: B008
        None, help="Gespeichertes Abbild statt eines laufenden matter-server"
    ),
    node: int | None = typer.Option(None, help="Node-ID am laufenden matter-server"),
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
    bridge_ip: str = typer.Option(..., help="IP dieser Bridge, aus Sicht des Miniservers"),
    port: int = typer.Option(7000, help="UDP-Port, auf dem der Miniserver lauscht"),
    out: Path = typer.Option(Path("."), help="Zielverzeichnis für die Vorlagen"),  # noqa: B008
    store_path: Path | None = typer.Option(  # noqa: B008
        None,
        help="Datenbank mit den Signalschlüsseln. Standard: "
        "~/.loxmatter/loxmatter.sqlite — bewusst unabhängig vom "
        "Arbeitsverzeichnis. Die Schlüssel darin sind die Verdrahtung in "
        "Loxone; ein relativer Pfad würde bei einem Aufruf aus einem anderen "
        "Verzeichnis die Datenbank verfehlen, dem Gerät eine neue device_id "
        "zuweisen und damit jede bestehende Verdrahtung stillschweigend "
        "zerstören. Alternative über die Umgebungsvariable LOXMATTER_STORE, "
        "etwa für ein eingehängtes Volume im Container.",
    ),
    raw_commands: bool = typer.Option(
        False,
        "--raw-commands",
        help="Auch Kommandos unbekannter Cluster exportieren. "
        "Verwaltungscluster bleiben in jedem Fall gesperrt.",
    ),
) -> None:
    """Erzeugt die Loxone-Vorlagen für ein Gerät.

    Der Ort der Signalschlüssel-Datenbank entscheidet über die Schlüsselstabilität —
    siehe `_resolve_store_path` und die Hilfe zu `--store-path`. Der verwendete Pfad
    wird ausgegeben, damit ein Nutzer, der versehentlich zwei Datenbanken erzeugt hat,
    das an der Ausgabe sieht statt es aus toten Bausteinen in Loxone zu erschließen.
    """
    snapshot = _load_snapshot(fixture, node, url)

    resolved_store_path = _resolve_store_path(store_path)
    typer.echo(f"Datenbank: {resolved_store_path}")
    resolved_store_path.parent.mkdir(parents=True, exist_ok=True)

    store = Store(resolved_store_path)
    try:
        device_id = store.register_device(snapshot)
        stored = store.register_signals(device_id, snapshot)
    finally:
        store.close()

    label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or f"Node {snapshot.node_id}"
    inputs = to_inputs(stored, device_id, label)
    # Ausgangsbefehle kommen aus AcceptedCommandList, nicht aus den Attributen:
    # Matter-Attribute sind fast alle nur lesbar (Task 6).
    device_commands = extract_commands(snapshot, raw=raw_commands)
    commands = [
        LoxoneCommand(
            key=f"d{device_id}_{c.endpoint}_{c.slug}",
            title=c.slug,
            path=f"/cmd/d{device_id}_{c.endpoint}_{c.slug}/" + ("<v>" if c.takes_value else "1"),
            analog=c.takes_value,
        )
        for c in device_commands
    ]

    out.mkdir(parents=True, exist_ok=True)
    viu = out / filename_for("VIU", device_id, label)
    vo = out / filename_for("VO", device_id, label)

    try:
        viu.write_bytes(render_virtual_in_udp(label, bridge_ip, port, inputs))
    except OSError as exc:
        _fail(f"{viu} konnte nicht geschrieben werden: {exc}. Es wurde noch keine Datei angelegt.")
    try:
        vo.write_bytes(render_virtual_out(label, f"http://{bridge_ip}:8080", commands))
    except OSError as exc:
        _fail(
            f"{vo} konnte nicht geschrieben werden: {exc}. "
            f"Geschrieben wurde bereits {viu}, es fehlt {vo.name}."
        )

    # Text zaehlt mit: der virtuelle Texteingang ist ein eigener Vorlagentyp
    # und kommt in einer spaeteren Ausbaustufe (Spec 6.6).
    unexportable = (Exportability.NONE, Exportability.TEXT)
    skipped = sum(1 for s in stored if s.exportability in unexportable)
    typer.echo(f"{viu.name}: {len(inputs)} Eingänge")
    typer.echo(f"{vo.name}: {len(commands)} Ausgangsbefehle")
    typer.echo(f"{skipped} Signale nicht exportierbar (Listen, Strukturen, Texte, Nullwerte)")
```

Dazu die gemeinsame Ladefunktion. `inspect` hat diese Logik heute inline; zieh sie
heraus und lass **beide** Kommandos sie benutzen, sonst steht die Fehlerbehandlung aus
Phase 1 zweimal da und driftet auseinander. Die vier deutschen Meldungen und ihre
Exit-Codes müssen unverändert bleiben — dafür gibt es Tests.

```python
def _load_snapshot(fixture: Path | None, node: int | None, url: str) -> NodeSnapshot:
    """Laedt ein Node-Abbild aus einer Datei oder von einem laufenden matter-server."""
    if fixture is not None:
        return _load_fixture(fixture)
    if node is None:
        raise typer.BadParameter("entweder --node oder --fixture angeben")

    async def run() -> NodeSnapshot:
        client = _build_client(url)
        try:
            await client.connect()
        except CannotConnect:
            _fail(f"matter-server unter {url} nicht erreichbar — läuft der Dienst?")
        except MatterUnavailableError as exc:
            _fail(f"matter-server unter {url} nicht bereit: {exc}")
        try:
            return await client.snapshot(node)
        except MatterUnavailableError:
            _fail(f"Node {node} ist am matter-server ({url}) nicht bekannt — kommissioniert?")
        finally:
            await client.disconnect()

    return asyncio.run(run())
```

Dazu die Auflösung des Store-Pfads — **niemals** wieder auf einen relativen Standard
vereinfachen, siehe Docstring:

```python
def _resolve_store_path(explicit: Path | None) -> Path:
    """Ermittelt den Pfad der Signalschlüssel-Datenbank.

    Rangfolge: `--store-path` schlägt die Umgebungsvariable `LOXMATTER_STORE`,
    die wiederum den Standard `~/.loxmatter/loxmatter.sqlite` schlägt.

    Der Standard ist absichtlich vom Arbeitsverzeichnis unabhängig. Die Datenbank hält
    die Signalschlüssel — und die Schlüssel *sind* die Verdrahtung in Loxone
    (Spec 6.2): sobald ein Nutzer einen exportierten Eingang auf einen
    Funktionsbaustein gezogen hat, verbindet nur noch der Schlüsseltext den Baustein
    mit der Bridge. Läge der Standard relativ zum Arbeitsverzeichnis (z. B.
    `loxmatter.sqlite`), würde ein Export aus einem anderen Verzeichnis — heute
    `~/exports`, morgen der Desktop, oder ein Cron-Job mit eigenem Arbeitsverzeichnis —
    die vorhandene Datenbank verfehlen. Das Werkzeug hielte das Gerät dann für neu,
    vergäbe eine neue `device_id` und damit einen komplett neuen Satz Schlüssel. Der
    Nutzer importiert die neue Vorlage, und jeder bisher verdrahtete Baustein wird
    stillschweigend tot — ohne Fehlermeldung. NICHT wieder auf einen relativen Pfad
    vereinfachen.

    `LOXMATTER_STORE` erlaubt einen abweichenden, festen Ort — etwa ein eingehängtes
    Volume in einer Container-Bereitstellung.
    """
    if explicit is not None:
        return explicit
    override = os.environ.get("LOXMATTER_STORE")
    if override:
        return Path(override)
    return Path.home() / ".loxmatter" / "loxmatter.sqlite"
```

Die Importe, die `export` zusätzlich braucht:

```python
import os

from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.commands import extract_commands
from loxmatter.export.signals import to_inputs
from loxmatter.model.store import Store
from loxmatter.profiles.table import Exportability
```

Baue `inspect` so um, dass es `_load_snapshot` benutzt, statt seine eigene Kopie zu
behalten. Alle bestehenden CLI-Tests müssen unverändert weiterlaufen.

`tests/conftest.py` bekommt außerdem ein autouse-Fixture, das `Path.home()` und
`LOXMATTER_STORE` für **jeden** Test auf ein Verzeichnis unter `tmp_path` legt — sonst
würde jeder Test, der `export` über die CLI aufruft und `--store-path` nicht selbst
setzt, den neuen Standard `~/.loxmatter/loxmatter.sqlite` treffen und in die echte
Home-Datenbank schreiben. `tests/test_store_path.py` prüft `_resolve_store_path`
gezielt mit eigenem `monkeypatch`: `--store-path` schlägt `LOXMATTER_STORE`, das
wiederum den Standard schlägt; außerdem, dass ein Gerät über zwei Exporte durch
dieselbe Datenbank dieselben Schlüssel behält, während zwei getrennte Datenbanken
unterschiedliche `device_id`s vergeben.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_export_cli.py tests/test_store_path.py -v`
Expected: PASS, 10 Tests in `test_export_cli.py`, 6 Tests in `test_store_path.py`

- [ ] **Step 5: Vollständige Prüfung**

```bash
uv run pytest -v && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 6: Der Beleg — Import in echtes Loxone Config**

**Dieser Schritt braucht einen Menschen mit Loxone Config.** Er ist der Zweck der
ganzen Phase.

```bash
uv run loxmatter export --node 3 --url ws://10.0.1.56:5580/ws \
  --bridge-ip <ip-dieses-rechners> --out ./export
```

Die beiden Dateien nach `Dokumente\Loxone\Loxone Config\Templates\VirtualIn\` bzw.
`...\VirtualOut\` kopieren, dann in Config: Peripherie → Virtuelle Eingänge →
Virtueller UDP-Eingang → Vorlage importieren.

Erwartet: das Objekt erscheint mit allen Befehlen, Titel und Kommentare lesbar,
Analog-Flags richtig. Danach dasselbe für den Taster (Node 4) und prüfen, dass
Impuls und Zähler getrennt auftauchen.

Dieser Import ist auch der eigentliche Beleg für `minVersion="14040925"`
(Task 5): Lehnt Config die Vorlage deswegen ab, war der beobachtete Minimalwert
zu niedrig — dann in Spec 6.1 nachtragen und den Wert in `documents.py`
anheben. Kein Testfall kann das vorwegnehmen, weil er dieselbe Annahme prüfen
würde, die er belegen soll.

Was dabei abweicht, geht in Spec 6.1 — **nicht** in eine Anpassung der Tests.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/cli.py tests/test_export_cli.py
git commit -m "feat(cli): loxmatter export erzeugt die Vorlagen eines Geraets"
```

---

## Abschluss der Phase

Die Phase ist fertig, wenn:

1. `uv run pytest` ohne Hardware und ohne Netz durchläuft,
2. beide echten Geräte exportiert und die Dateien **in echtem Loxone Config importiert**
   wurden,
3. ein zweiter Export desselben Geräts dieselben Schlüssel erzeugt,
4. die `VO_`-Datei der Steckdose genau die OnOff-Befehle trägt und die des Tasters
   leer ist — kein Verwaltungscluster taucht in einer der beiden auf,
5. Abweichungen vom erwarteten Format in Spec 6.1 stehen.

Nicht Teil dieser Phase: das Senden der Werte (Phase 4), die Einheitenumrechnung
(Phase 4), der virtuelle Texteingang für String-Attribute, und die Systemvorlage mit
`bridge_alive` und `/resync` — die gehört zu Phase 4, weil sie ohne laufenden Sender
nichts bewirkt.
