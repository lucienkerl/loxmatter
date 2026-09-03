# Signalauswahl — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine Steckdose exportiert nach Loxone fünf bedeutungsvolle Werte statt 109 technisch abbildbarer, und der kWh-Zählerstand kommt erstmals überhaupt an.

**Architecture:** Ein neuer Begriff `Relevance` entscheidet ausschließlich über den *Vorgabewert* der bestehenden Spalte `exported`; die Exportmechanik bleibt unberührt. Die Auswahl folgt Matters eigenem Aufbau — der Descriptor-Cluster nennt je Endpunkt einen standardisierten Gerätetyp, Root Node und OTA Requestor sind Verwaltung. Bei Clustern, die die Profiltabelle kennt, zählen zusätzlich nur deren benannte Attribute. Ein neues Tabellenfeld `field` holt eine Zahl aus einer Matter-Struktur.

**Tech Stack:** Python 3.12, SQLite (`PRAGMA user_version`-Migrationen), FastAPI, Pydantic v2, Alpine.js 3.17.1 (vendort, kein Build-Schritt), pytest, ruff, mypy strict.

**Entwurfsdokument:** [`docs/superpowers/specs/2026-09-03-signalauswahl-design.md`](../specs/2026-09-03-signalauswahl-design.md). Bei Widerspruch zwischen Plan und Entwurf gilt der Entwurf; melde den Widerspruch.

## Global Constraints

- **Deutsch** in Prosa, Kommentaren, Docstrings, Hilfetexten und Fehlermeldungen; **Englisch** in allen Bezeichnern — auch in Testnamen, JS-Variablen, JSON-Feldnamen und YAML-Schlüsseln.
- Alle Tests laufen **ohne Hardware und ohne Netzzugriff**.
- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` (strict über `src` und `scripts`) müssen am Ende jeder Aufgabe sauber sein.
- Ausgangslage: 477 Tests grün auf `main`, HEAD `171a4b3`.
- **Schlüssel sind unveränderlich** (Hauptdokument 6.2). Keine Aufgabe dieses Plans darf einen bestehenden Signalschlüssel ändern. Ein umbenannter Schlüssel ist ein stillschweigend toter Funktionsbaustein in einer fremden Loxone-Config.
- **Prüfe jede fremde Signatur gegen die installierte Fassung**, statt sie aus diesem Plan zu übernehmen (`uv run python -c "import inspect; ..."`). Dieser Plan hat sich in früheren Phasen mehrfach geirrt; die Prüfung hat es jedes Mal aufgefangen.
- `tests/fixtures/VirtualIn/` und `tests/fixtures/VirtualOut/` **nicht lesen** — unbereinigte Vorlagen aus einer echten Installation, absichtlich git-ignoriert.
- Keine Verbindung zu einem Host im Heimnetz des Anwenders. Unter `10.0.1.56` läuft ein echter matter-server mit echten Geräten.

## Dateien

| Datei | Zuständigkeit |
|---|---|
| `src/loxmatter/profiles/relevance.py` | **neu** — Gerätetyp-Regel: welche Signale sind standardmäßig gewollt |
| `src/loxmatter/profiles/catalog.py` | **neu** — Attributnamen aus dem chip-SDK, nur für die Anzeige |
| `src/loxmatter/profiles/table.py` | ergänzt — Feinauswahl bei bekannten Clustern, Strukturfeld, Titel |
| `src/loxmatter/profiles/clusters.yaml` | ergänzt — PowerSource, Strukturfelder für Energie |
| `src/loxmatter/loxone/values.py` | ergänzt — Zahl aus Struktur ziehen |
| `src/loxmatter/model/store.py` | ergänzt — Vorgabewert von `exported`, Migration auf Schema v3 |
| `src/loxmatter/api/models.py` | ergänzt — `relevance` im Signal-Payload |
| `src/loxmatter/api/devices.py` | ergänzt — `relevance` befüllen |
| `src/loxmatter/api/export.py` | ergänzt — Vorschau nennt ausgeblendete Signale |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | ergänzt — Funktional/Experte-Blöcke |

---

### Task 1: Gerätetypen aus dem Descriptor lesen

Die Grundlage. Ohne diesen Schritt kennt nichts die Endpunkt-Rollen.

**Files:**
- Create: `src/loxmatter/profiles/relevance.py`
- Test: `tests/profiles/test_relevance.py`

**Interfaces:**
- Consumes: `loxmatter.matter.models.NodeSnapshot` (Feld `attributes: Mapping[str, Any]`, Pfade der Form `"<endpoint>/<cluster>/<attribute>"`), `loxmatter.matter.paths.parse_attribute_path`.
- Produces:
  - `DESCRIPTOR_CLUSTER_ID: int` (= 29), `DEVICE_TYPE_LIST_ID: int` (= 0)
  - `ROOT_NODE_DEVICE_TYPE: int`, `OTA_REQUESTOR_DEVICE_TYPE: int`, `POWER_SOURCE_DEVICE_TYPE: int`
  - `UTILITY_DEVICE_TYPES: frozenset[int]`
  - `device_types_by_endpoint(snapshot: NodeSnapshot) -> dict[int, frozenset[int]]`

- [ ] **Step 1: Die drei Gerätetyp-Nummern belegen**

Das installierte SDK enthält **keine** Gerätetyp-Tabelle — nur Cluster. Prüfe zuerst selbst:

```bash
uv run python -c "
import chip.clusters.Objects as O, os
print(os.path.dirname(O.__file__))
"
```

Die Nummern stehen in der Matter Device Library Specification der CSA. **Nicht aus diesem Plan übernehmen.** Belege sie und schreibe die Quelle als Kommentar an die Konstante. Erwartete Werte zur Gegenkontrolle — wenn deine Quelle abweicht, gilt deine Quelle, und du meldest die Abweichung:

- Root Node = 0x0016
- OTA Requestor = 0x0012
- Power Source = 0x0011

Gegenprobe an den eingecheckten Abbildern (die müssen zu deiner Quelle passen):

```bash
uv run python -c "
import json
for f in ('ikea_grillplats_plug.json','ikea_bilresa_button.json'):
    d=json.load(open('tests/fixtures/nodes/'+f))
    a=d.get('attributes') or d
    print(f, {k: v for k,v in a.items() if k.endswith('/29/0')})
"
```

Erwartete Ausgabe: die Steckdose hat auf Endpunkt 0 die Typen 18 und 22, der Taster zusätzlich 17 — und der Taster ist das batteriebetriebene Gerät.

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

```python
"""Gerätetypen je Endpunkt aus dem Descriptor-Cluster."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.relevance import (
    OTA_REQUESTOR_DEVICE_TYPE,
    POWER_SOURCE_DEVICE_TYPE,
    ROOT_NODE_DEVICE_TYPE,
    device_types_by_endpoint,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _snapshot(name: str) -> NodeSnapshot:
    return NodeSnapshot.from_raw(json.loads((FIXTURES / name).read_text()))


def test_the_plug_declares_a_utility_endpoint_and_two_application_endpoints():
    types = device_types_by_endpoint(_snapshot("ikea_grillplats_plug.json"))
    assert ROOT_NODE_DEVICE_TYPE in types[0]
    assert OTA_REQUESTOR_DEVICE_TYPE in types[0]
    assert ROOT_NODE_DEVICE_TYPE not in types[1]
    assert ROOT_NODE_DEVICE_TYPE not in types[2]


def test_the_button_declares_a_power_source_on_its_utility_endpoint():
    """Der Batteriestand liegt nicht zufaellig auf Endpunkt 0 - das Geraet
    deklariert dort den Geraetetyp Power Source. Genau darauf stuetzt sich
    die Ausnahme in Task 2; ohne diese Zusicherung waere sie geraten."""
    types = device_types_by_endpoint(_snapshot("ikea_bilresa_button.json"))
    assert POWER_SOURCE_DEVICE_TYPE in types[0]


def test_an_endpoint_without_a_descriptor_is_absent_rather_than_empty():
    """Fehlt der Descriptor, soll der Aufrufer das unterscheiden koennen von
    'Descriptor da, aber leer' - beides fuehrt spaeter zur selben
    Entscheidung, aber aus verschiedenen Gruenden."""
    snapshot = NodeSnapshot.from_raw({"node_id": 1, "attributes": {"7/6/0": True}})
    assert device_types_by_endpoint(snapshot) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "kein Wörterbuch",
        [{"1": 3}],
        [{"0": "keine Zahl"}],
        [None],
        42,
    ],
)
def test_an_unexpected_descriptor_shape_yields_no_device_types(raw):
    """Ein nicht konformes Geraet darf keinen Absturz ausloesen. Der
    Endpunkt gilt dann als typlos - und damit spaeter (Task 2) als
    Nutz-Endpunkt: im Zweifel ein Eingang zu viel, nie ein fehlender Wert."""
    snapshot = NodeSnapshot.from_raw({"node_id": 1, "attributes": {"0/29/0": raw}})
    assert device_types_by_endpoint(snapshot) == {0: frozenset()}
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/profiles/test_relevance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.profiles.relevance'`

- [ ] **Step 4: Die minimale Implementierung schreiben**

```python
"""Welche Signale ein Anwender standardmaessig will (Entwurf 2026-09-03, 4.1).

Getrennt von `Exportability` und mit Absicht in einem eigenen Modul: die
Frage "laesst sich der Wert auf einen UDP-Eingang abbilden" (table.py) und
die Frage "will ihn jemand" sind verschiedene Fragen mit verschiedenen
Antworten. Ein Thread-Funkzaehler ist exportierbar, aber nicht relevant.

Die Auswahl stuetzt sich nicht auf eine Liste von Cluster-Nummern, die
jemand fuer langweilig haelt, sondern auf Matters eigenen Aufbau: der
Descriptor-Cluster traegt auf jedem Endpunkt eine standardisierte
Geraetetyp-Liste. Ein Geraet ohne diese Angabe wird nicht zertifiziert -
die Regel traegt damit fuer jeden Hersteller und jeden Geraetetyp, auch
fuer solche, die dieses Werkzeug nie gesehen hat.
"""

from __future__ import annotations

from typing import Any

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import parse_attribute_path

DESCRIPTOR_CLUSTER_ID = 29
DEVICE_TYPE_LIST_ID = 0

# Quelle: Matter Device Library Specification (CSA). NICHT aus dem
# installierten chip-SDK ableitbar - dessen Katalog umfasst Cluster, keine
# Geraetetypen (in Task 1 geprueft).
ROOT_NODE_DEVICE_TYPE = 0x0016
OTA_REQUESTOR_DEVICE_TYPE = 0x0012
POWER_SOURCE_DEVICE_TYPE = 0x0011

UTILITY_DEVICE_TYPES: frozenset[int] = frozenset(
    {ROOT_NODE_DEVICE_TYPE, OTA_REQUESTOR_DEVICE_TYPE}
)


def _device_type_ids(raw: object) -> frozenset[int]:
    """Die Geraetetyp-Nummern aus einem `DeviceTypeList`-Wert.

    matter-server liefert Strukturen als Woerterbuch mit dem Feld-Tag als
    ZEICHENKETTE, nicht mit dem Feldnamen: eine DeviceTypeStruct kommt als
    ``{"0": <Typ>, "1": <Revision>}`` an. Beides - Zeichenkette und Zahl -
    wird akzeptiert, weil eine andere Serialisierung dieselbe Struktur
    genauso plausibel als ``{0: ...}`` liefern koennte.

    Alles Unerwartete ergibt eine leere Menge statt einer Ausnahme: ein
    nicht konformes Geraet soll die Zerlegung nicht anhalten.
    """
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    ids: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        value: Any = entry.get("0", entry.get(0))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ids.add(int(value))
    return frozenset(ids)


def device_types_by_endpoint(snapshot: NodeSnapshot) -> dict[int, frozenset[int]]:
    """Die deklarierten Geraetetypen je Endpunkt.

    Ein Endpunkt ohne Descriptor taucht gar nicht auf - der Aufrufer
    unterscheidet damit "nicht gemeldet" von "gemeldet, aber leer", auch
    wenn beide spaeter zur selben Entscheidung fuehren.
    """
    result: dict[int, frozenset[int]] = {}
    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        if cluster_id != DESCRIPTOR_CLUSTER_ID or attribute_id != DEVICE_TYPE_LIST_ID:
            continue
        result[endpoint] = _device_type_ids(value)
    return result
```

- [ ] **Step 5: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/profiles/test_relevance.py -v`
Expected: PASS, 8 Tests (4 Fälle der Parametrisierung plus die vier übrigen — zähle nach, die Parametrisierung hat 5 Fälle, also 8 Tests gesamt)

- [ ] **Step 6: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add src/loxmatter/profiles/relevance.py tests/profiles/test_relevance.py
git commit -m "feat(profiles): Geraetetypen je Endpunkt aus dem Descriptor lesen"
```

---

### Task 2: Die Relevanz-Regel

**Files:**
- Modify: `src/loxmatter/profiles/relevance.py`
- Modify: `src/loxmatter/profiles/table.py` (neue Funktion `names_element`)
- Test: `tests/profiles/test_relevance.py`, `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: `device_types_by_endpoint` aus Task 1; `loxmatter.matter.models.SignalRef` (Felder `endpoint`, `cluster_id`, `element_id`, `kind`), `SignalKind`.
- Produces:
  - `BOILERPLATE_CLUSTERS: frozenset[int]`
  - `UTILITY_ENDPOINT_KEEP_CLUSTERS: frozenset[int]`
  - `is_functional(ref: SignalRef, device_types: dict[int, frozenset[int]]) -> bool`
  - in `table.py`: `names_element(ref: SignalRef) -> bool` — ob die Tabelle den Cluster kennt UND dieses Element dort benannt ist

- [ ] **Step 1: Den fehlschlagenden Test für `names_element` schreiben**

Ergänze `tests/profiles/test_table.py`:

```python
def test_names_element_separates_named_from_generic_within_a_known_cluster():
    """Die Tabelle kennt Cluster 6 und benennt dort nur Attribut 0. Genau
    diese Unterscheidung traegt die Feinauswahl: `onoff` ist gewollt,
    StartUpOnOff (0x4003) nicht."""
    known = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    generic = SignalRef(1, 6, 0x4003, SignalKind.ATTRIBUTE)
    assert names_element(known) is True
    assert names_element(generic) is False


def test_names_element_is_false_for_a_cluster_the_table_does_not_know():
    """Ein unbekannter Cluster benennt nichts. Der Aufrufer (relevance)
    darf daraus NICHT 'alles aus' folgern - siehe dort."""
    assert names_element(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is False


def test_names_element_covers_events_too():
    """Cluster 59 benennt seine Ereignisse; die Feinauswahl darf einen
    Tastendruck nicht als unbenannt verwerfen."""
    assert names_element(SignalRef(1, 59, 1, SignalKind.EVENT)) is True
```

Der Import oben in der Datei ist um `names_element` zu ergänzen.

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/profiles/test_table.py -k names_element -v`
Expected: FAIL — `ImportError: cannot import name 'names_element'`

- [ ] **Step 3: `names_element` implementieren**

In `src/loxmatter/profiles/table.py`, direkt nach `lookup`:

```python
def knows_cluster(cluster_id: int) -> bool:
    """Ob die Profiltabelle diesen Cluster ueberhaupt fuehrt."""
    return cluster_id in _table()


def names_element(ref: SignalRef) -> bool:
    """Ob die Profiltabelle genau dieses Element namentlich fuehrt.

    Getrennt von `lookup`, weil `lookup` fuer ein unbenanntes Element einen
    generischen Namen erfindet (`c6_a16387`) und die Unterscheidung damit
    verliert. Die Feinauswahl in `profiles.relevance` braucht sie aber:
    innerhalb eines bekannten Clusters ist "benannt" das Kennzeichen fuer
    "gewollt".
    """
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return False
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    return ref.element_id in (cluster.get(section) or {})
```

Prüfe, ob `SignalKind` in `table.py` bereits importiert ist; `lookup` verwendet es, also ja.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/profiles/test_table.py -k names_element -v`
Expected: PASS, 3 Tests

- [ ] **Step 5: Den fehlschlagenden Test für `is_functional` schreiben**

Ergänze `tests/profiles/test_relevance.py`:

```python
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.relevance import is_functional

_PLUG_TYPES = {0: frozenset({18, 22}), 1: frozenset({266}), 2: frozenset({1296})}
_BUTTON_TYPES = {0: frozenset({17, 18, 22}), 1: frozenset({15}), 2: frozenset({15})}


def test_a_thread_diagnostics_counter_on_the_root_endpoint_is_not_functional():
    ref = SignalRef(0, 53, 4, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_the_battery_level_on_a_root_endpoint_is_functional():
    """Der Ausnahmefall, den der Descriptor selbst begruendet: der Taster
    deklariert auf Endpunkt 0 zusaetzlich Power Source."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _BUTTON_TYPES) is True


def test_the_battery_cluster_is_not_functional_where_no_power_source_is_declared():
    """Dieselbe Cluster-Nummer auf einem Endpunkt ohne Power-Source-Typ
    bleibt Verwaltung. Die Regel haengt am deklarierten Geraetetyp, nicht an
    der Cluster-Nummer - sonst waere sie doch wieder nur eine Liste."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_onoff_on_an_application_endpoint_is_functional():
    assert is_functional(SignalRef(1, 6, 0, _KIND), _PLUG_TYPES) is True


def test_a_generic_attribute_of_a_known_cluster_is_not_functional():
    """StartUpOnOff (0x4003) sitzt legitim bei OnOff, will aber niemand in
    Loxone. Die Tabelle kennt Cluster 6 und benennt dort nur Attribut 0."""
    assert is_functional(SignalRef(1, 6, 0x4003, _KIND), _PLUG_TYPES) is False


def test_every_attribute_of_an_unknown_cluster_stays_functional():
    """Die Grundwette des Projekts (Hauptdokument 3.5): ein Geraetetyp, den
    dieses Werkzeug nie gesehen hat, funktioniert trotzdem. Waere das hier
    falsch, laege ein fremdes Geraet stumm - ohne dass jemand merkte, dass
    etwas fehlt."""
    assert is_functional(SignalRef(1, 4711, 99, _KIND), _PLUG_TYPES) is True


def test_identify_groups_and_descriptor_are_never_functional():
    for cluster_id in (3, 4, 29):
        assert is_functional(SignalRef(1, cluster_id, 0, _KIND), _PLUG_TYPES) is False


def test_an_endpoint_without_a_declared_type_counts_as_an_application_endpoint():
    """Im Zweifel ein Eingang zu viel, nie ein fehlender Wert."""
    assert is_functional(SignalRef(9, 4711, 0, _KIND), _PLUG_TYPES) is True


def test_events_of_a_known_cluster_stay_functional():
    """Ein verworfenes Ereignis waere ein Tastendruck, der in Loxone nie
    ankommt - die erste Anforderung dieses Projekts ueberhaupt."""
    for event_id in (1, 2, 3, 4, 5, 6):
        ref = SignalRef(1, 59, event_id, SignalKind.EVENT)
        assert is_functional(ref, _BUTTON_TYPES) is True
```

Ergänze oben in der Datei `_KIND = SignalKind.ATTRIBUTE`.

- [ ] **Step 6: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/profiles/test_relevance.py -k is_functional -v`
Expected: FAIL — `ImportError: cannot import name 'is_functional'`

- [ ] **Step 7: `is_functional` implementieren**

Ergänze `src/loxmatter/profiles/relevance.py`:

```python
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.table import knows_cluster, names_element

# Auf jedem Endpunkt Verwaltung, unabhaengig vom Geraetetyp: Identify
# (Blinken zur Identifikation), Groups (Matter-Gruppenverwaltung) und der
# Descriptor selbst. Keiner davon hat eine Bedeutung fuer eine
# Hausautomation.
BOILERPLATE_CLUSTERS: frozenset[int] = frozenset({3, 4, DESCRIPTOR_CLUSTER_ID})

# Cluster, die auf einem Verwaltungs-Endpunkt dennoch gewollt sind - aber
# nur, wenn das Geraet den zugehoerigen Nutz-Geraetetyp dort auch
# deklariert. Der Batteriestand ist der Fall, der das noetig macht.
UTILITY_ENDPOINT_KEEP_CLUSTERS: dict[int, int] = {
    47: POWER_SOURCE_DEVICE_TYPE,  # PowerSource
}


def is_functional(ref: SignalRef, device_types: dict[int, frozenset[int]]) -> bool:
    """Ob dieses Signal standardmaessig gewollt ist (Entwurf 2026-09-03, 4).

    Drei Schichten, in dieser Reihenfolge:

    1. Boilerplate-Cluster sind nie gewollt, auf keinem Endpunkt.
    2. Auf einem Verwaltungs-Endpunkt (Root Node oder OTA Requestor) ist
       nur gewollt, was zu einem dort ebenfalls deklarierten Nutz-
       Geraetetyp gehoert.
    3. Auf einem Nutz-Endpunkt ist alles gewollt - ausser bei einem
       Cluster, den die Profiltabelle kennt: dort nur die benannten
       Elemente. Ein unbekannter Cluster bleibt vollstaendig gewollt
       (Hauptdokument 3.5).

    Ereignisse unterliegen Schicht 3 nicht: sie sind in der Tabelle
    ohnehin namentlich gefuehrt, und ein verworfenes Ereignis waere ein
    Tastendruck, der in Loxone nie ankommt.
    """
    if ref.cluster_id in BOILERPLATE_CLUSTERS:
        return False

    declared = device_types.get(ref.endpoint, frozenset())
    if declared & UTILITY_DEVICE_TYPES:
        required = UTILITY_ENDPOINT_KEEP_CLUSTERS.get(ref.cluster_id)
        return required is not None and required in declared

    if ref.kind is SignalKind.EVENT:
        return True
    if knows_cluster(ref.cluster_id):
        return names_element(ref)
    return True
```

- [ ] **Step 8: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/profiles/test_relevance.py -v`
Expected: PASS

- [ ] **Step 9: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Relevanz aus dem Geraetetyp je Endpunkt ableiten"
```

---

### Task 3: PowerSource in die Profiltabelle

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Test: `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: `lookup` aus `table.py`, unverändert.
- Produces: keine neue Signatur — nur Tabellendaten.

- [ ] **Step 1: Attribut-Nummer und Einheit belegen**

```bash
uv run python -c "
import chip.clusters.Objects as O
print('BatPercentRemaining ->', O.PowerSource.Attributes.BatPercentRemaining.attribute_id)
"
```

Erwartet: `12`.

Die **Einheit** steht nicht im SDK. Die Matter Application Cluster Specification gibt `BatPercentRemaining` in halben Prozent an (Wertebereich 0–200). Belege das und schreibe die Quelle als Kommentar an den Eintrag. Weicht deine Quelle ab, gilt deine Quelle — melde die Abweichung, ein falscher Faktor zeigt in Loxone dauerhaft den doppelten oder halben Ladestand.

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

```python
def test_the_battery_level_is_named_and_scaled_to_percent():
    """Matter zaehlt BatPercentRemaining in halben Prozent (0-200). Ohne
    den Faktor zeigte Loxone bei voller Batterie 200 %."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 190)
    assert profile.slug == "battery"
    assert profile.unit == "%"
    assert scale_factor(ref) == pytest.approx(0.5)
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/profiles/test_table.py -k battery -v`
Expected: FAIL — `assert 'c47_a12' == 'battery'`

- [ ] **Step 4: Den Tabelleneintrag ergänzen**

In `src/loxmatter/profiles/clusters.yaml`, in aufsteigender Cluster-Reihenfolge einsortiert (also zwischen 8 und 59):

```yaml
  47:
    name: powersource
    attributes:
      # BatPercentRemaining. Matter zaehlt in halben Prozent (0-200) laut
      # Matter Application Cluster Specification - daher 0.5. Attribut-ID
      # gegen chip.clusters.Objects.PowerSource.Attributes belegt.
      #
      # Nur dieses eine von 37 Attributen ist benannt, und das ist die
      # Feinauswahl aus dem Entwurf (4.3): PowerSource fuehrt daneben
      # Ladezustaende, Batteriechemie, ANSI-Bezeichnungen und Fehlerlisten.
      # Wer eines davon braucht, schaltet es im Experten-Block frei.
      12: {slug: battery, unit: "%", scale: 0.5}
```

- [ ] **Step 5: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/profiles/test_table.py -k battery -v`
Expected: PASS

- [ ] **Step 6: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Batteriestand benennen und auf Prozent skalieren"
```

---

### Task 4: Namen aus dem SDK-Katalog

Ohne diesen Schritt heißt der Batteriestand eines Geräts, das die Profiltabelle
nicht kennt, für immer `c47_a12` — obwohl der Name in einer Abhängigkeit
liegt, die dieses Projekt ohnehin installiert.

**Files:**
- Modify: `src/loxmatter/profiles/table.py`
- Create: `src/loxmatter/profiles/catalog.py`
- Modify: `src/loxmatter/model/store.py` (Titel beim Anlegen)
- Test: `tests/profiles/test_catalog.py`, `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: `chip.clusters.Objects` (bereits Abhängigkeit über `python-matter-server`), `SignalRef`, `SignalKind`.
- Produces:
  - `element_name(ref: SignalRef) -> str | None` in `catalog.py`
  - `Profile` bekommt das Feld `title: str`; `slug` bleibt unverändert das Schlüsselmaterial

**Die Trennung, um die es hier geht:** `slug` bildet den Signalschlüssel und
ist damit unveränderlich (Hauptdokument 6.2). `title` ist reine Anzeige. Der
SDK-Katalog speist **nur den Titel**. Ein Gerät, das vor dieser Änderung
eingelernt wurde, behält `d1_0_c47_a12` und heißt fortan „BatPercentRemaining";
ein danach eingelerntes bekommt denselben Schlüssel. Der Katalog darf keinen
Schlüssel bewegen.

- [ ] **Step 1: Den Katalog erkunden**

```bash
uv run python -c "
import chip.clusters.Objects as O, inspect
cl = [c for _, c in inspect.getmembers(O, inspect.isclass) if hasattr(c, 'id') and hasattr(c, 'Attributes')]
print('Cluster:', len(cl))
ps = [c for c in cl if c.id == 47][0]
print(ps.__name__, [(n, a.attribute_id) for n, a in inspect.getmembers(ps.Attributes, inspect.isclass) if hasattr(a, 'attribute_id')][:5])
"
```

Erwartet: 140 Cluster; PowerSource mit Attributen samt `attribute_id`.
Prüfe außerdem, ob Ereignisse ebenso auffindbar sind (`ps.Events`), und
richte dich nach dem, was du siehst — nicht nach dieser Beschreibung.

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

```python
"""Attributnamen aus dem Cluster-Katalog des chip-SDK."""

from __future__ import annotations

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.catalog import element_name


def test_a_standard_attribute_gets_its_specification_name():
    """c47_a12 heisst im Standard BatPercentRemaining. Der Name liegt in
    einer Abhaengigkeit, die dieses Projekt ohnehin installiert - ihn von
    Hand zu pflegen waere Arbeit fuer nichts."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert element_name(ref) == "BatPercentRemaining"


def test_an_unknown_cluster_has_no_name():
    assert element_name(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is None


def test_an_unknown_attribute_of_a_known_cluster_has_no_name():
    assert element_name(SignalRef(1, 6, 9999, SignalKind.ATTRIBUTE)) is None


def test_the_catalog_is_read_once():
    """140 Cluster mit allen Attributen bei jedem Signal zu durchsuchen
    waere bei 159 Signalen je Geraet spuerbar. Der Aufbau gehoert hinter
    einen Cache."""
    first = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    second = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    assert first == second == "BatPercentRemaining"
```

Und in `tests/profiles/test_table.py`:

```python
def test_a_generic_signal_keeps_its_slug_but_gains_a_readable_title():
    """Der Schluessel bleibt generisch - er ist die Verdrahtung in Loxone
    und darf sich nie bewegen. Nur die Anzeige wird lesbar."""
    ref = SignalRef(0, 51, 1, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 3)
    assert profile.slug == "c51_a1"
    assert profile.title != "c51_a1"


def test_a_table_named_signal_uses_its_own_name_for_both():
    """Wo die eigene Tabelle etwas weiss, gewinnt sie: `onoff` ist
    sprechender als `OnOff`, und die Einheit kennt das SDK ohnehin nicht."""
    profile = lookup(SignalRef(1, 6, 0, SignalKind.ATTRIBUTE), True)
    assert profile.slug == "onoff"
    assert profile.title == "onoff"


def test_a_signal_the_catalog_does_not_know_falls_back_to_the_slug():
    ref = SignalRef(1, 4711, 3, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 1)
    assert profile.slug == "c4711_a3"
    assert profile.title == "c4711_a3"
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/profiles/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.profiles.catalog'`

- [ ] **Step 4: Den Katalog implementieren**

`src/loxmatter/profiles/catalog.py`: baut einmalig (`functools.cache`) eine
Abbildung `(cluster_id, element_id, kind) -> Name` aus
`chip.clusters.Objects` und liefert `element_name`. Import-Fehler und
unerwartete Formen dürfen keine Ausnahme auslösen — der Katalog ist eine
Verbesserung der Anzeige, kein Betriebsmittel: fällt er aus, bleiben die
generischen Namen, und das Werkzeug läuft weiter. Schreib das in den
Docstring.

`Profile` bekommt `title: str`. In `lookup`:
- Tabelleneintrag vorhanden → `slug` und `title` beide aus der Tabelle,
- sonst → `slug` wie bisher generisch, `title = element_name(ref) or slug`.

In `store.register_signals` wird die Titelspalte beim **Anlegen** aus
`profile.title` statt aus `profile.slug` befüllt. Der UPDATE-Zweig fasst
`title` weiterhin nicht an — sobald `set_title` es gesetzt hat, gehört es
dem Nutzer.

- [ ] **Step 5: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/profiles/ -v`
Expected: PASS

- [ ] **Step 6: Belegen, dass kein Schlüssel wandert**

```bash
uv run pytest tests/model/ -q
```

Expected: PASS. Kein bestehender Schlüsseltest darf sich ändern — wenn doch
einer bricht, ist die Trennung `slug`/`title` nicht sauber und **nicht der
Test anzupassen**.

- [ ] **Step 7: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Attributnamen aus dem SDK-Katalog fuer die Anzeige"
```

---

### Task 5: Zahlen aus Strukturen

Damit kommt der kWh-Zählerstand erstmals überhaupt in Loxone an.

**Files:**
- Modify: `src/loxmatter/profiles/table.py`
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/loxone/values.py`
- Test: `tests/profiles/test_table.py`, `tests/loxone/test_values.py`

**Interfaces:**
- Consumes: `classify(value)` (unverändert erhalten), `scale_factor(ref)`, `SignalRef`.
- Produces:
  - `struct_field(ref: SignalRef) -> int | None`
  - `struct_member(ref: SignalRef, raw: object) -> object` — der Wert, auf dem klassifiziert und gerechnet wird; ohne `field` unverändert `raw`
  - `lookup` gibt für ein Signal mit `field` die Exportierbarkeit des **Elements** zurück, nicht die der Struktur

- [ ] **Step 1: Die Form einer Struktur belegen**

```bash
uv run python -c "
import chip.clusters.Objects as O
S = O.ElectricalEnergyMeasurement.Structs.EnergyMeasurementStruct
for d in S.descriptor.Fields: print(d.Tag, d.Label)
print('CumulativeEnergyImported ->', O.ElectricalEnergyMeasurement.Attributes.CumulativeEnergyImported.attribute_id)
"
```

Erwartet: Tag 0 = `energy`, Attribut 1.

Und wie matter-server eine Struktur **serialisiert** — das ist der Punkt, an dem eine Implementierung mit `value["energy"]` scheitern würde:

```bash
uv run python -c "
import json
d = json.load(open('tests/fixtures/nodes/ikea_grillplats_plug.json'))
a = d.get('attributes') or d
print({k: v for k, v in a.items() if k.endswith('/29/0')})
"
```

Erwartet: `{'0/29/0': [{'0': 18, '1': 1}, ...]}` — **Feld-Tag als Zeichenkette**, nicht als Name.

Die Einheit von `EnergyMeasurementStruct.energy` steht nicht im SDK. Die Matter Application Cluster Specification gibt sie in mWh an; 1 kWh = 1e6 mWh, daher `scale: 1.0e-6`. Belege das und melde eine Abweichung.

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

In `tests/profiles/test_table.py`:

```python
_ENERGY = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)


def test_a_struct_member_becomes_an_analog_signal():
    """Matter liefert den Zaehlerstand als Struktur aus Wert und
    Zeitstempeln. Ohne das Herausziehen faellt er als 'nicht abbildbar'
    durch - und das ist der Wert, wegen dem man eine messende Steckdose
    kauft."""
    raw = {"0": 12_345_678, "1": 1_700_000_000, "2": 1_700_003_600}
    assert lookup(_ENERGY, raw).exportability is Exportability.ANALOG
    assert lookup(_ENERGY, raw).slug == "energy_imported"


def test_a_struct_without_the_named_member_stays_unexportable():
    """Nicht raten. Eine erfundene Zahl an einem echten Energiebaustein
    waere schlimmer als ein fehlender Wert."""
    assert lookup(_ENERGY, {"1": 1_700_000_000}).exportability is Exportability.NONE


def test_a_struct_member_that_is_not_a_number_stays_unexportable():
    assert lookup(_ENERGY, {"0": "viel"}).exportability is Exportability.NONE


def test_a_null_value_stays_unexportable_even_with_a_field():
    assert lookup(_ENERGY, None).exportability is Exportability.NONE


def test_an_integer_key_is_accepted_as_well_as_a_string_key():
    """Die Zeichenkette ist, was matter-server heute liefert; eine andere
    Serialisierung derselben Struktur waere mit Zahl genauso plausibel."""
    assert lookup(_ENERGY, {0: 5_000_000}).exportability is Exportability.ANALOG


def test_a_cluster_without_a_field_entry_still_sees_the_whole_value():
    """Nur ein Cluster, den die Tabelle kennt, darf ein Element benennen.
    Eine unbekannte Struktur bleibt unbekannt."""
    ref = SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)
    assert lookup(ref, {"0": 5}).exportability is Exportability.NONE
```

In `tests/loxone/test_values.py`:

```python
def test_the_energy_counter_arrives_in_kilowatt_hours():
    """Matter zaehlt in mWh, Loxone will kWh (Hauptdokument 7.3)."""
    ref = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)
    raw = {"0": 2_500_000_000, "1": 1_700_000_000}
    assert to_loxone_value(ref, raw) == pytest.approx(2500.0)


def test_a_struct_without_the_named_member_yields_none_at_runtime():
    """Laufzeit und Zerlegung muessen dieselbe Entscheidung treffen - sonst
    meldet die Oberflaeche einen Wert, den der Export nicht kennt."""
    ref = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)
    assert to_loxone_value(ref, {"1": 1_700_000_000}) is None
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/profiles/test_table.py tests/loxone/test_values.py -k "struct or energy" -v`
Expected: FAIL — die Exportierbarkeit ist `NONE` statt `ANALOG`

- [ ] **Step 4: `struct_member` implementieren und `lookup` anpassen**

In `src/loxmatter/profiles/table.py`:

```python
def struct_field(ref: SignalRef) -> int | None:
    """Die Feldnummer, die aus einer Struktur zu ziehen ist - oder None.

    Nur fuer Attribute eines Clusters, den die Tabelle kennt und bei dem
    der Eintrag ein `field` traegt.
    """
    if ref.kind is SignalKind.EVENT:
        return None
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return None
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry:
        return None
    field = entry.get("field")
    return int(field) if field is not None else None


def struct_member(ref: SignalRef, raw: object) -> object:
    """Der Wert, auf dem klassifiziert und gerechnet wird.

    Ohne `field`-Eintrag unveraendert `raw`. Mit `field` das benannte
    Element der Struktur - und `None`, wenn der Wert keine Struktur ist
    oder das Element fehlt. Dann bleibt das Signal nicht exportierbar; es
    wird NICHT geraten (Entwurf 2026-09-03, 5).

    matter-server liefert Strukturen als Woerterbuch mit dem Feld-Tag als
    Zeichenkette (`{"0": ...}`); die Zahl wird ebenso akzeptiert.

    Diese eine Funktion ist die gemeinsame Quelle fuer `lookup` (Einstufung
    beim Einlernen) und `loxone.values.to_loxone_value` (Laufzeit). Zwei
    Kopien wuerden auseinanderlaufen und die Oberflaeche einen Wert melden
    lassen, den der Export nicht kennt.
    """
    field = struct_field(ref)
    if field is None:
        return raw
    if not isinstance(raw, dict):
        return None
    return raw.get(str(field), raw.get(field))
```

`lookup` bekommt eine Zeile: die Einstufung läuft über `struct_member`.

```python
    if entry:
        return Profile(
            slug=entry["slug"],
            title=entry["slug"],
            unit=entry.get("unit", ""),
            exportability=classify(struct_member(ref, value)),
        )
```

Das Feld `title` stammt aus Task 4; übernimm die dort entstandene Fassung von
`lookup` und ändere daran nur die Exportierbarkeits-Zeile.

Der generische Zweig darunter bleibt `classify(value)` — ohne Tabelleneintrag gibt es kein `field`.

In `src/loxmatter/loxone/values.py`, in `to_loxone_value`, direkt am Anfang:

```python
    raw = struct_member(ref, raw)
```

Der Import ist um `struct_member` zu ergänzen.

- [ ] **Step 5: Den Tabelleneintrag ergänzen**

In `src/loxmatter/profiles/clusters.yaml`, Cluster 145:

```yaml
  145:
    name: energy
    attributes:
      # CumulativeEnergyImported/-Exported. Matter liefert beide als
      # EnergyMeasurementStruct - Wert plus Zeitstempel -, nicht als Zahl.
      # `field: 0` ist EnergyMeasurementStruct.energy, gegen
      # chip.clusters.Objects belegt; die ZEITSTEMPEL bleiben absichtlich
      # weg, sie haben in Loxone keine Verwendung.
      #
      # Matter zaehlt in mWh (Matter Application Cluster Specification),
      # Loxone will kWh (Hauptdokument 7.3): 1 kWh = 1e6 mWh.
      1: {slug: energy_imported, field: 0, unit: "kWh", scale: 1.0e-6}
      2: {slug: energy_exported, field: 0, unit: "kWh", scale: 1.0e-6}
```

Bestehende Einträge für 145 sind zu ersetzen, nicht zu verdoppeln — prüfe zuerst mit `grep -n "145:" -A 6 src/loxmatter/profiles/clusters.yaml`, was dort schon steht, und behalte vorhandene Slugs bei, damit kein Schlüssel wandert.

- [ ] **Step 6: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/profiles/test_table.py tests/loxone/test_values.py -v`
Expected: PASS

- [ ] **Step 7: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Zaehlerstand aus der Energie-Struktur ziehen"
```

---

### Task 6: Der Vorgabewert im Speicher

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `is_functional`, `device_types_by_endpoint` (Tasks 1–2), `is_exportable` (unverändert).
- Produces: `register_signals` setzt `exported` beim **Anlegen** auf `is_exportable(...) and is_functional(...)`. Signatur unverändert.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

```python
def test_a_freshly_registered_plug_exports_only_its_meaningful_values(tmp_path):
    """Das Ziel dieses ganzen Entwurfs, am echten Geraet: fuenf Werte, die
    etwas bedeuten, statt 109 technisch abbildbarer."""
    store = Store(tmp_path / "s.sqlite")
    snapshot = _fixture("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    exported = {s.key for s in store.signals(device_id) if s.exported}
    assert exported == {
        "d1_1_onoff",
        "d1_2_voltage",
        "d1_2_current",
        "d1_2_power",
        "d1_2_energy_imported",
    }


def test_a_freshly_registered_button_keeps_both_rockers_and_the_battery(tmp_path):
    """Der Fall, an dem sich zeigt, ob die Regel zu gierig ist: alle sechs
    Ereignisse beider Wippen muessen durchkommen, dazu der Batteriestand."""
    store = Store(tmp_path / "s.sqlite")
    snapshot = _fixture("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    exported = {s.key for s in store.signals(device_id) if s.exported}
    for endpoint in (1, 2):
        for slug in (
            "press",
            "longpress",
            "shortrelease",
            "longrelease",
            "multipress_ongoing",
            "multipress",
        ):
            assert f"d1_{endpoint}_{slug}" in exported
    assert "d1_0_battery" in exported
    assert len(exported) == 17


def test_a_thread_counter_is_stored_but_not_exported(tmp_path):
    """Nicht geloescht, nur abgewaehlt: der Experten-Block soll ihn
    freischalten koennen, ohne dass das Geraet neu eingelernt wird."""
    store = Store(tmp_path / "s.sqlite")
    snapshot = _fixture("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    counters = [s for s in store.signals(device_id) if s.ref.cluster_id == 53]
    assert counters, "Thread-Zaehler sollen weiterhin gespeichert werden"
    assert all(not s.exported for s in counters)
    assert all(s.exportability is Exportability.ANALOG for s in counters[:1])
```

Prüfe, wie bestehende Tests in dieser Datei ein Abbild laden (Hilfsfunktion `_fixture` oder Fixture); benutze denselben Weg statt einen zweiten einzuführen.

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/model/test_store.py -k freshly_registered -v`
Expected: FAIL — die Menge enthält 109 Schlüssel statt 5

- [ ] **Step 3: `register_signals` anpassen**

Vor der Schleife über `extract_signals(snapshot)`:

```python
        device_types = device_types_by_endpoint(snapshot)
```

Und die Zeile, die `exported` bestimmt:

```python
                # Zwei Fragen, zwei Antworten (Entwurf 2026-09-03, 3):
                # `is_exportable` sagt, ob der Wert ueberhaupt auf einen
                # Loxone-Eingang passt; `is_functional`, ob ihn jemand
                # standardmaessig will. Ein Thread-Funkzaehler ist das
                # erste und nicht das zweite.
                #
                # Nur beim ANLEGEN: der UPDATE-Zweig oben fasst `exported`
                # weiterhin nicht an, sobald ein Signal einmal bekannt ist -
                # ab dann gehoert der Wert dem Nutzer.
                exported = is_exportable(profile.exportability) and is_functional(
                    ref, device_types
                )
```

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/model/test_store.py -v`
Expected: PASS. Bestehende Tests dieser Datei, die von „alles exportierbare ist exportiert" ausgingen, sind **anzupassen, nicht zu löschen** — und die Anpassung ist im Commit zu begründen.

- [ ] **Step 5: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(store): nur relevante Signale sind ab Werk exportiert"
```

---

### Task 7: Migration auf Schema v3

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: `_MIGRATIONS`-Muster aus `_migrate_to_v1`/`_migrate_to_v2`; `lookup`, `is_exportable`, `is_functional`, `device_types_by_endpoint`.
- Produces: `_SCHEMA_VERSION = 3`, `_migrate_to_v3(db)`.

**Das Problem, das diese Aufgabe löst:** Titel, Einheit und Exportierbarkeit stehen **in der Zeile**. Die Tabellenerweiterungen aus Tasks 3 bis 5 erreichen ein bereits gespeichertes Signal deshalb nie — der Batteriestand hieße für immer `c47_a12`, der Zählerstand bliebe für immer „nicht abbildbar".

**Die Grenze:** der Schlüssel bleibt. Ein vor dem Update eingelerntes Gerät behält `d2_0_c47_a12` und heißt ab dann „battery"; ein danach eingelerntes bekommt `d2_0_battery`.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

```python
def test_the_migration_never_changes_a_key(tmp_path):
    """Die eiserne Regel (Hauptdokument 6.2). Ein umbenannter Schluessel
    waere ein stillschweigend toter Funktionsbaustein in einer fremden
    Config - kein Fehler, den irgendjemand von aussen sehen wuerde."""
    path = tmp_path / "s.sqlite"
    keys_before = _build_store_at_schema_v2(path)
    store = Store(path)  # oeffnet und migriert
    assert {s.key for s in store.signals(1)} == keys_before


def test_the_migration_refreshes_title_and_unit_from_the_table(tmp_path):
    """Ohne diesen Schritt erreichte eine Korrektur in clusters.yaml ein
    schon gespeichertes Signal nie."""
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path)
    store = Store(path)
    battery = next(s for s in store.signals(1) if s.ref.cluster_id == 47 and s.ref.element_id == 12)
    assert battery.key == "d1_0_c47_a12", "Schluessel bleibt der alte"
    assert battery.title == "battery"
    assert battery.unit == "%"


def test_the_migration_applies_the_new_default_to_existing_devices(tmp_path):
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path)
    store = Store(path)
    exported = {s.key for s in store.signals(1) if s.exported}
    assert len(exported) < 30, "die Signalflut muss auch rueckwirkend weg sein"


def test_a_signal_the_table_cannot_classify_survives_the_migration(tmp_path):
    """Scheitert die Neuableitung fuer eine Zeile, bleibt sie unveraendert -
    kein Abbruch, keine halb migrierte Datenbank (Entwurf 8)."""
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path, extra_row=("d1_9_kaputt", 9, 4711, 0, "attribute"))
    store = Store(path)
    assert any(s.key == "d1_9_kaputt" for s in store.signals(1))
```

`_build_store_at_schema_v2` ist eine Hilfsfunktion **in dieser Testdatei**: sie legt eine Datenbank nach dem alten Schema an (`PRAGMA user_version = 2`), schreibt ein Gerät und die Signale des Taster-Abbilds mit `exported = 1` für alles Exportierbare, und gibt die Schlüsselmenge zurück. Orientiere dich an den vorhandenen Hilfsfunktionen dieser Datei für v1 und v2 — sie existieren bereits und zeigen das Muster.

**Wichtig für den Migrationstest:** die Neuableitung braucht die Gerätetypen, die nur im Abbild stehen, nicht in der Datenbank. Entscheide, woher `_migrate_to_v3` sie nimmt, und begründe es:
- entweder die Gerätetypen bei `register_device`/`register_signals` mitspeichern (neue Spalte, dann ist die Migration autark),
- oder die Regel für Bestandszeilen ohne Abbild aus den gespeicherten Cluster-/Endpunkt-Nummern ableiten (dann braucht es eine Ersatzregel für „Verwaltungs-Endpunkt", und die ist zu begründen).

Der erste Weg ist der ehrlichere, wenn er ohne Verrenkung geht. Was du auch wählst: schreib es in den Docstring von `_migrate_to_v3`, und schreib dazu, was die Migration **nicht** kann.

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/model/test_store_migration.py -k migration -v`
Expected: FAIL — Titel bleibt `c47_a12`

- [ ] **Step 3: Migration implementieren**

`_SCHEMA_VERSION` auf `3` und ein Eintrag in `_MIGRATIONS`. Der Docstring von `_migrate_to_v3` muss benennen:
- warum rückwirkend und nicht nur für neue Geräte (zwei Regelsätze wären niemandem zu erklären, und der Unterschied hinge am Einlerndatum),
- dass der Schlüssel unangetastet bleibt und welche Folge das hat (zwei Schlüssel für denselben Wert bei alt/neu eingelernten Geräten),
- dass eine einzelne unableitbare Zeile unverändert bleibt statt die Migration abzubrechen.

Die Migration läuft wie ihre Vorgänger innerhalb der Transaktion, die `PRAGMA user_version` mitschreibt.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/model/test_store_migration.py -v`
Expected: PASS

- [ ] **Step 5: Gegen die echte Datenbank prüfen**

Es gibt eine echte Datenbank aus dem Betrieb auf dem Raspberry Pi (Schema v2, zwei Geräte). Sie ist **nicht** anzufassen und der Pi **nicht** zu kontaktieren. Stattdessen: baue mit `_build_store_at_schema_v2` eine Datenbank aus **beiden** eingecheckten Abbildern, migriere sie und gib das Ergebnis aus:

```bash
uv run python -c "
# ... Datenbank aus beiden Abbildern nach altem Schema bauen, dann:
# for d in store.devices(): print(d.label, len([s for s in store.signals(d.id) if s.exported]))
"
```

Erwartet: 5 und 17. Klebe die tatsächliche Ausgabe in die Commit-Nachricht oder den Bericht.

- [ ] **Step 6: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(store): Bestandsgeraete neu einstufen, ohne einen Schluessel zu aendern"
```

---

### Task 8: Relevanz in der API und der Oberfläche

**Files:**
- Modify: `src/loxmatter/api/models.py`, `src/loxmatter/api/devices.py`, `src/loxmatter/api/export.py`
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`
- Test: `tests/api/test_devices.py`, `tests/api/test_export_api.py`, `tests/api/test_web.py`

**Interfaces:**
- Consumes: `SignalOut` (Felder `key`, `path`, `kind`, `title`, `unit`, `value`, `exportable`, `reason`, `exported`), `_signal_out(signal, values)`.
- Produces: `SignalOut` bekommt das Feld `functional: bool`.

- [ ] **Step 1: Den fehlschlagenden API-Test schreiben**

```python
async def test_the_signal_payload_says_whether_a_signal_is_functional(api):
    """Die Oberflaeche muss die beiden Bloecke trennen koennen, ohne die
    Regel ein zweites Mal in JavaScript nachzubauen."""
    client, _, _ = api
    rows = (await client.get("/api/devices/1/signals")).json()
    onoff = next(r for r in rows if r["key"].endswith("_onoff"))
    counter = next(r for r in rows if "_c53_" in r["key"])
    assert onoff["functional"] is True
    assert counter["functional"] is False
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/api/test_devices.py -k functional -v`
Expected: FAIL — `KeyError: 'functional'`

- [ ] **Step 3: Das Feld ergänzen**

`SignalOut` bekommt `functional: bool`. In `_signal_out` wird es befüllt.

**Entscheide und begründe**, woher `_signal_out` die Gerätetypen nimmt: die Relevanz-Regel braucht sie, `_signal_out` sieht aber nur eine `StoredSignal`. Naheliegend ist, das Ergebnis in der Zeile zu speichern (dann fällt es in Task 7 ohnehin an) statt es bei jeder Anfrage neu zu berechnen. Was du auch wählst: **eine** Quelle, keine zweite Nachbildung der Regel.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/api/test_devices.py -v`
Expected: PASS

- [ ] **Step 5: Die Exportvorschau ergänzen**

`GET /api/export/preview` nennt zusätzlich je Gerät, wie viele Signale als Experte ausgeblendet sind. Test:

```python
async def test_the_preview_reports_how_many_signals_are_hidden(api):
    client, _, _ = api
    body = (await client.get("/api/export/preview?bridge_ip=10.0.0.1")).json()
    plug = next(d for d in body["devices"] if d["id"] == 1)
    assert plug["hidden_count"] > 100
```

Prüfe zuerst die tatsächliche Form der Antwort (`grep -n "preview" -A 30 src/loxmatter/api/export.py`) und füge das Feld dort ein, wo die übrigen Zählungen stehen.

- [ ] **Step 6: Die Oberfläche umbauen**

In `index.html` bekommt die Signalliste zwei Blöcke: **Funktional** (offen) und **Experte** (zugeklappt, mit Anzahl), plus einen Schalter „Experten-Signale anzeigen". Jedes Signal behält seinen Exportieren-Haken.

Die Gerätekachel zeigt statt `firstSignalsFor(...)` die funktionalen Signale — damit erledigt sich der offene Punkt aus dem Abschluss-Review von Phase 5 (heute stehen dort NetworkCommissioning und BasicInformation, also weder Ein/Aus noch Leistung). Beschriftung entsprechend von „Signale (Anfang der Liste)" zurück auf etwas, das wieder stimmt.

`style.css`: die Blöcke im vorhandenen Stil, keine neue Farbwelt.

Ein Test in `tests/api/test_web.py`, der das Markup prüft — orientiere dich an den vorhandenen Tests dort, die `/static/app.js` und `/` abrufen und im Text suchen. Die Testdocstring muss ehrlich sagen, was sie belegt und was nicht (es läuft keine Browser-Engine).

- [ ] **Step 7: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(web): funktionale Signale vorn, Experten-Signale zugeklappt"
```

---

### Task 9: Dokumentation und Abschluss

**Files:**
- Modify: `docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`
- Modify: `docs/superpowers/specs/2026-09-03-signalauswahl-design.md`
- Modify: `README.md`

- [ ] **Step 1: Das Hauptdokument nachziehen**

Abschnitt 3.5 (generische Zerlegung) bekommt einen Verweis auf den neuen Entwurf und den Satz, dass die Zerlegung unverändert alles behält — nur der Vorgabewert des Exports folgt jetzt der Relevanz. Abschnitt 5 (Datenmodell) und 6.6 (nicht exportierbare Werte) sind auf den Stand zu bringen: 6.6 nennt heute „109 von 159 abbildbar" als Ergebnis, ohne dass davon 5 exportiert werden.

- [ ] **Step 2: Offene Punkte im neuen Entwurf schließen**

Abschnitt 10 des Entwurfs hat vier offene Punkte. Punkt 1 (Gerätetyp-Nummern belegen) ist mit Task 1 erledigt — streiche ihn und trage ein, welche Quelle du benutzt hast. Punkte 2–4 bleiben, sofern nichts an ihnen entschieden wurde.

- [ ] **Step 3: README**

Der Abschnitt, der den Export beschreibt, muss sagen, dass standardmäßig die funktionalen Signale exportiert werden und wie man an die übrigen kommt.

- [ ] **Step 4: Vollständige Prüfung**

```bash
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
```

Expected: alles sauber, keine Testverluste gegenüber der Ausgangslage (477) außer bewusst angepassten.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: Signalauswahl in Hauptdokument und README nachziehen"
```

---

## Abschlusskriterien

Die Arbeit ist fertig, wenn:

1. `uv run pytest` ohne Hardware und ohne Netz durchläuft,
2. die Steckdose aus dem eingecheckten Abbild **namentlich** `onoff`, `voltage`, `current`, `power`, `energy_imported` exportiert — nicht nur „weniger als vorher",
3. der Taster beide Wippen vollständig samt `multipress` und den Batteriestand exportiert,
4. ein unbekannter Cluster auf einem Nutz-Endpunkt vollständig erhalten bleibt,
5. die Migration belegt kein einziger Schlüssel ändert sich,
6. ein Signal, das die Profiltabelle nicht kennt, seinen generischen **Schlüssel** behält, aber einen lesbaren Titel aus dem SDK-Katalog trägt,
7. die Gerätetyp-Nummern gegen die Matter Device Library belegt sind, nicht aus diesem Plan übernommen.

**Nicht Teil dieser Arbeit:** eine vom Anwender editierbare Sperrliste, die fehlenden Systemcheck-Prüfungen (mDNS, Dongle, OTBR, Thread-Netz) und der IPv6-Check, der globales IPv6 verlangt, wo Thread ULA nutzt — der ist ein eigener Fehler und gehört in eine eigene Runde.
