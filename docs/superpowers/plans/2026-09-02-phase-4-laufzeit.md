# Phase 4: Laufzeit-Strecke — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Werte fließen. Ein Messwert der Steckdose erscheint im Miniserver, und ein Loxone-Baustein schaltet sie.

**Architecture:** Zwei Richtungen, die sich nur den Store und den Dienstprozess teilen. Sensorrichtung: `profiles` liefert Skalierungsfaktoren, `loxone/values` rechnet und formatiert, `loxone/sender` verschickt UDP, `loxone/runtime` verbindet Matter-Subscriptions damit und erzeugt Impulse, Zähler, Online-Signale und Heartbeat. Kommandorichtung: `commands/` übersetzt einen Wunschzustand in ein Matter-Kommando, `loxone/server` nimmt die HTTP-Aufrufe der virtuellen Ausgänge entgegen. Dazu ein `fake-miniserver`, der beide Richtungen ohne echten Miniserver prüfbar macht.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, `mypy` (strict), `PyYAML`, `fastapi` + `uvicorn`, `sqlite3` und `asyncio` aus der Standardbibliothek.

## Global Constraints

- **Tests laufen ohne Hardware und ohne Netzwerkzugriff.** Ein UDP-Socket auf `127.0.0.1` gilt nicht als Netzwerkzugriff — er verlässt die Maschine nicht und ist die einzige ehrliche Art, einen UDP-Sender zu prüfen. Ein Test, der ein echtes Gerät oder einen echten Miniserver braucht, wird übersprungen und verrottet (Spec 10.1).
- **Deutsch in Prosa, Kommentaren, Docstrings und Fehlermeldungen**, Englisch in Bezeichnern und Commit-Präfixen.
- **Alle Datenklassen unveränderlich** (`frozen=True`), solange kein Grund dagegen spricht.
- **Schlüssel sind unveränderlich** (Spec 6.2). Diese Phase liest sie und schreibt sie nie um. Jede Änderung an einem Schlüssel wäre ein Fehler dieser Phase, nicht eine Anpassung.
- **Zieleinheit ist die des Loxone-Bausteins, nicht die SI-Einheit** (Spec 7.3). Leistung in kW.
- **Zahlenformat: bis zu 6 Nachkommastellen, nachlaufende Nullen abgeschnitten.** 300 mW muss als `0.0003` ankommen, nicht als `0`. Das ist kein Detail: der Grund, eine messende Steckdose einzubauen, sind oft gerade die kleinen Dauerverbraucher (Spec 7.3).
- **Datagrammform:** `<key>:<wert>`, passend zur Befehlserkennung `<key>:\v` der exportierten Vorlage (Spec 6.1).
- `uv run ruff check .`, `uv run ruff format --check .` und `uv run mypy` müssen sauber bleiben. ruff formatiert auch Python-Blöcke in Markdown.
- Die unsanierten Vorlagen unter `tests/fixtures/VirtualIn/` und `tests/fixtures/VirtualOut/` enthalten Zugangsdaten einer echten Installation und sind bewusst git-ignoriert. **Nicht lesen.**

---

## Zwei Lücken aus Phase 3, die diese Phase schließt

Beim Entwurf dieser Phase gefunden, nicht vorher bekannt:

**Kommandos werden nicht persistiert.** `extract_commands` läuft beim Export, der Schlüssel `d1_1_on` landet in der `VO_`-Vorlage — aber der Store kennt nur `device` und `signal`. Ruft der Miniserver später `/cmd/d1_1_on/1`, hat die Bridge keine Zuordnung zurück auf Node, Endpoint, Cluster und Kommando-ID. Phase 3 gibt Schlüssel aus, die sie selbst nicht auflösen kann. Task 2 schließt das.

**Es gibt keine Skalierungsfaktoren.** `clusters.yaml` trägt `unit`, aber kein `scale`. In der Vorlage steht `<v.6> kW`, und nichts rechnet Milliwatt in Kilowatt. Task 1 schließt das.

## Was diese Phase nicht abschließen kann

**Die Farbraum-Umrechnung bleibt unvalidiert.** Task 5 baut sie und prüft sie gegen veröffentlichte Referenzwerte, aber es steht keine Matter-Leuchte zur Verfügung. Von allen Abbildungen im Projekt ist diese die fehleranfälligste — Loxone-Lumitech gegen Matter Hue/Saturation beziehungsweise CIE xy. Der Plan markiert das an Ort und Stelle; es ist ein offener Punkt der Phase, keine erledigte Aufgabe.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `src/loxmatter/profiles/clusters.yaml` | zusätzlich `scale` je Attribut |
| `src/loxmatter/loxone/values.py` | roher Matter-Wert → Loxone-Wert, und dessen Textform |
| `src/loxmatter/loxone/sender.py` | UDP-Versand: Entprellung, Rate-Limit. Kennt kein Matter |
| `src/loxmatter/loxone/runtime.py` | verbindet Subscriptions mit dem Sender: Impulse, Zähler, Online, Heartbeat, Full-Resend |
| `src/loxmatter/model/store.py` | zusätzlich `command`-Tabelle und Auflösung |
| `src/loxmatter/commands/translate.py` | Wunschzustand → Matter-Kommando |
| `src/loxmatter/commands/color.py` | Farbraum-Umrechnung |
| `src/loxmatter/loxone/server.py` | HTTP-Endpoint für virtuelle Ausgänge und `/resync` |
| `src/loxmatter/cli.py` | zusätzlich `loxmatter run` |
| `deploy/fake-miniserver/` | Testdoppel für beide Richtungen |

---

### Task 1: Skalierung und Zahlenformat

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/profiles/table.py`
- Create: `src/loxmatter/loxone/__init__.py`
- Create: `src/loxmatter/loxone/values.py`
- Create: `tests/loxone/test_values.py`

**Interfaces:**
- Consumes: `SignalRef`, `SignalKind`, `lookup`, `Exportability`
- Produces:
  - `scale_factor(ref: SignalRef) -> float` in `profiles.table` — 1.0, wenn die Tabelle nichts sagt
  - `to_loxone_value(ref: SignalRef, raw: object) -> float | bool | None` in `loxone.values` — `None`, wenn nicht abbildbar
  - `format_value(value: float | bool) -> str` in `loxone.values`
  - `datagram(key: str, value: float | bool) -> bytes` in `loxone.values`

- [ ] **Step 1: Write the failing test**

`tests/loxone/test_values.py`:

```python
import pytest

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.loxone.values import datagram, format_value, to_loxone_value


def attr(cluster: int, element: int, endpoint: int = 1) -> SignalRef:
    return SignalRef(endpoint, cluster, element, SignalKind.ATTRIBUTE)


def test_temperature_is_hundredths_of_a_degree():
    """Spec 7.3: TemperatureMeasurement liefert 0,01 °C."""
    assert to_loxone_value(attr(1026, 0), 2150) == pytest.approx(21.5)


def test_power_goes_from_milliwatt_to_kilowatt():
    """Spec 7.3: Loxone rechnet Leistung in kW, nicht in W."""
    assert to_loxone_value(attr(144, 8, endpoint=2), 5_000_000) == pytest.approx(5.0)


def test_small_power_survives_the_conversion():
    """300 mW sind 0,0003 kW - genau der Standby-Verbraucher, den man sehen will."""
    assert to_loxone_value(attr(144, 8, endpoint=2), 300) == pytest.approx(0.0003)


def test_level_is_scaled_from_254_to_percent():
    assert to_loxone_value(attr(8, 0), 254) == pytest.approx(100.0)
    assert to_loxone_value(attr(8, 0), 127) == pytest.approx(50.0, abs=0.2)


def test_boolean_passes_through_unscaled():
    assert to_loxone_value(attr(6, 0), True) is True


def test_unknown_cluster_passes_through_unscaled():
    """Spec 3.5: die Tabelle reichert an, sie filtert nicht."""
    assert to_loxone_value(attr(64999, 7), 42) == pytest.approx(42.0)


def test_unmappable_values_yield_none():
    """Spec 6.6: Listen, Structs, Text und null werden nie zu einem Datagramm."""
    assert to_loxone_value(attr(29, 1), [1, 2, 3]) is None
    assert to_loxone_value(attr(40, 1), "IKEA of Sweden") is None
    assert to_loxone_value(attr(49, 7), None) is None


def test_format_trims_trailing_zeros():
    assert format_value(21.5) == "21.5"
    assert format_value(21.0) == "21"
    assert format_value(0.0) == "0"


def test_format_keeps_six_decimals_for_small_values():
    """Ohne das verschwindet jeder Verbraucher unter 10 W in der Null."""
    assert format_value(0.0003) == "0.0003"
    assert format_value(0.000001) == "0.000001"


def test_format_renders_booleans_as_one_and_zero():
    assert format_value(True) == "1"
    assert format_value(False) == "0"


def test_datagram_matches_the_exported_check_pattern():
    """Die Vorlage erkennt "<key>:\\v" - das Datagramm muss dazu passen (Spec 6.1)."""
    assert datagram("d1_2_power", 0.0003) == b"d1_2_power:0.0003"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_values.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.loxone'`

- [ ] **Step 3: Skalierungsfaktoren in die Tabelle**

In `src/loxmatter/profiles/clusters.yaml` bei den Attributen jeweils `scale` ergänzen.
Die Faktoren stammen aus Spec 7.3:

```yaml
  8:
    attributes:
      # 0-254 auf 0-100 %: 100/254
      0: {slug: level, unit: "%", scale: 0.39370078740157477}
  1026:
    attributes:
      0: {slug: temp, unit: "°C", scale: 0.01}
  1029:
    attributes:
      0: {slug: humidity, unit: "%", scale: 0.01}
  144:
    attributes:
      4: {slug: voltage, unit: "V", scale: 0.001}
      5: {slug: current, unit: "A", scale: 0.001}
      8: {slug: power, unit: "kW", scale: 0.000001}
  145:
    attributes:
      1: {slug: energy_imported, unit: "kWh", scale: 0.000001}
      2: {slug: energy_exported, unit: "kWh", scale: 0.000001}
```

Achte auf die YAML-Falle aus Phase 3: Slugs wie `on` und `off` müssen quotiert bleiben.

- [ ] **Step 4: `scale_factor` in `profiles/table.py`**

```python
def scale_factor(ref: SignalRef) -> float:
    """Faktor, mit dem ein roher Matter-Wert in die Loxone-Einheit uebergeht.

    1.0, wenn die Tabelle nichts sagt - unbekannte Cluster werden roh
    durchgereicht, nicht verworfen (Spec 3.5).
    """
    cluster = _table().get(ref.cluster_id, {})
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry:
        return 1.0
    return float(entry.get("scale", 1.0))
```

- [ ] **Step 5: `loxone/values.py`**

```python
"""Rechnet rohe Matter-Werte in das um, was der Miniserver erwartet.

Zwei Regeln aus Spec 7.3 pragen dieses Modul:

Zieleinheit ist die des Loxone-Bausteins, nicht die SI-Einheit. Der
Energiemanager erwartet kW, also liefern wir kW - auch wenn Matter in
Milliwatt misst.

Und daraus folgt das Zahlenformat: von mW nach kW sind sechs
Groessenordnungen. Wer hier auf zwei Nachkommastellen rundet, laesst jeden
Verbraucher unter 10 W als 0 erscheinen - und gerade die kleinen
Dauerverbraucher sind oft der Grund, eine messende Steckdose einzubauen.
"""

from __future__ import annotations

from loxmatter.matter.models import SignalRef
from loxmatter.profiles.table import Exportability, classify, scale_factor

MAX_DECIMALS = 6


def to_loxone_value(ref: SignalRef, raw: object) -> float | bool | None:
    """Skalierter Wert, oder None wenn Loxone ihn nicht aufnehmen kann."""
    kind = classify(raw)
    if kind is Exportability.DIGITAL:
        return bool(raw)
    if kind is not Exportability.ANALOG:
        return None
    assert isinstance(raw, (int, float))
    return float(raw) * scale_factor(ref)


def format_value(value: float | bool) -> str:
    """Textform fuer das Datagramm: bis zu sechs Nachkommastellen, ohne Nullen am Ende."""
    if isinstance(value, bool):
        return "1" if value else "0"
    text = f"{value:.{MAX_DECIMALS}f}".rstrip("0").rstrip(".")
    return text or "0"


def datagram(key: str, value: float | bool) -> bytes:
    """Ein UDP-Datagramm in der Form, die die exportierte Vorlage erkennt."""
    return f"{key}:{format_value(value)}".encode()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_values.py -v`
Expected: PASS, 11 Tests

- [ ] **Step 7: Gegen das echte Gerät halten**

`tests/loxone/test_values_real_device.py`:

```python
"""Prueft die Skalierung an der aufgezeichneten Steckdose."""

import json
from pathlib import Path

import pytest

from loxmatter.loxone.values import format_value, to_loxone_value
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def plug() -> NodeSnapshot:
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_mains_voltage_lands_near_230_volt():
    """2/144/4 ist RMSVoltage in mV - die Steckdose hing an 230 V."""
    snap = plug()
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 4)
    assert to_loxone_value(ref, snap.attributes[ref.path]) == pytest.approx(230.0)


def test_exactly_109_signals_yield_a_value():
    """Spec 6.6: von 159 Attributsignalen erreichen 109 einen UDP-Eingang."""
    snap = plug()
    werte = [to_loxone_value(s, snap.attributes.get(s.path)) for s in extract_signals(snap)]
    assert sum(1 for w in werte if w is not None) == 109


def test_no_value_formats_to_scientific_notation():
    """Loxone kann "1e-05" nicht lesen - das waere ein stiller Ausfall."""
    snap = plug()
    for ref in extract_signals(snap):
        wert = to_loxone_value(ref, snap.attributes.get(ref.path))
        if wert is not None:
            assert "e" not in format_value(wert).lower()
```

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/loxone src/loxmatter/profiles tests/loxone
git commit -m "feat(loxone): Skalierung und Zahlenformat nach Spec 7.3"
```

---

### Task 2: Kommandos im Store auflösbar machen

Schließt die Lücke aus Phase 3: der Exporter schreibt `/cmd/d1_1_on/<v>` in die Vorlage,
aber nichts kann diesen Schlüssel später zurück auf ein Matter-Kommando abbilden.

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Modify: `src/loxmatter/cli.py` (Export persistiert die Kommandos)
- Create: `tests/model/test_store_commands.py`

**Interfaces:**
- Consumes: `DeviceCommand` aus `export.commands`, `NodeSnapshot`
- Produces:
  - `class StoredCommand` — frozen: `key`, `node_id`, `endpoint`, `cluster_id`, `command_id`, `takes_value`
  - `Store.register_commands(device_id: int, commands: Sequence[DeviceCommand], node_id: int) -> list[StoredCommand]`
  - `Store.resolve_command(key: str) -> StoredCommand` — wirft `KeyError` mit deutscher Meldung
  - `Store.commands(device_id: int) -> list[StoredCommand]`

- [ ] **Step 1: Write the failing test**

`tests/model/test_store_commands.py`:

```python
import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    yield s
    s.close()


def registered(store: Store, name: str):
    snap = load(name)
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    commands = store.register_commands(device_id, extract_commands(snap), snap.node_id)
    return device_id, snap, commands


def test_plug_commands_are_resolvable_by_their_exported_key(store):
    device_id, snap, _ = registered(store, "ikea_grillplats_plug.json")
    resolved = store.resolve_command(f"d{device_id}_1_on")
    assert resolved.cluster_id == 6
    assert resolved.command_id == 1
    assert resolved.endpoint == 1
    assert resolved.node_id == snap.node_id


def test_unknown_key_raises_with_a_german_message(store):
    registered(store, "ikea_grillplats_plug.json")
    with pytest.raises(KeyError, match="unbekannter Kommando-Schluessel"):
        store.resolve_command("d1_1_gibtsnicht")


def test_button_registers_no_commands(store):
    _, _, commands = registered(store, "ikea_bilresa_button.json")
    assert commands == []


def test_reregistering_is_idempotent(store):
    device_id, snap, first = registered(store, "ikea_grillplats_plug.json")
    again = store.register_commands(device_id, extract_commands(snap), snap.node_id)
    assert [c.key for c in again] == [c.key for c in first]


def test_command_keys_match_the_exported_scheme(store):
    device_id, _, commands = registered(store, "ikea_grillplats_plug.json")
    assert sorted(c.key for c in commands) == [
        f"d{device_id}_1_off",
        f"d{device_id}_1_on",
        f"d{device_id}_1_toggle",
    ]


def test_node_id_is_stored_so_the_runtime_can_address_the_device(store):
    _, snap, commands = registered(store, "ikea_grillplats_plug.json")
    assert {c.node_id for c in commands} == {snap.node_id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/model/test_store_commands.py -v`
Expected: FAIL mit `AttributeError: 'Store' object has no attribute 'register_commands'`

- [ ] **Step 3: Schema und Methoden ergänzen**

In `_SCHEMA` von `src/loxmatter/model/store.py` ergänzen:

```sql
CREATE TABLE IF NOT EXISTS command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device(id),
    node_id     INTEGER NOT NULL,
    endpoint    INTEGER NOT NULL,
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    takes_value INTEGER NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, command_id)
);
```

Dazu die Datenklasse und die drei Methoden:

```python
@dataclass(frozen=True)
class StoredCommand:
    key: str
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    takes_value: bool


def register_commands(
    self, device_id: int, commands: Sequence[DeviceCommand], node_id: int
) -> list[StoredCommand]:
    """Macht die exportierten Kommando-Schluessel zur Laufzeit aufloesbar.

    Ohne das schreibt der Exporter Schluessel in die Vorlage, die spaeter
    niemand zurueck auf ein Matter-Kommando abbilden kann.
    """
    for command in commands:
        self._db.execute(
            "INSERT OR IGNORE INTO command "
            "(device_id, node_id, endpoint, cluster_id, command_id, key, takes_value) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                node_id,
                command.endpoint,
                command.cluster_id,
                command.command_id,
                f"d{device_id}_{command.endpoint}_{command.slug}",
                int(command.takes_value),
            ),
        )
    self._db.commit()
    return self.commands(device_id)


def commands(self, device_id: int) -> list[StoredCommand]:
    rows = self._db.execute(
        "SELECT * FROM command WHERE device_id = ? ORDER BY endpoint, cluster_id, command_id",
        (device_id,),
    ).fetchall()
    return [self._as_command(r) for r in rows]


def resolve_command(self, key: str) -> StoredCommand:
    row = self._db.execute("SELECT * FROM command WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise KeyError(f"unbekannter Kommando-Schluessel {key!r}")
    return self._as_command(row)


@staticmethod
def _as_command(row: sqlite3.Row) -> StoredCommand:
    return StoredCommand(
        key=row["key"],
        node_id=int(row["node_id"]),
        endpoint=int(row["endpoint"]),
        cluster_id=int(row["cluster_id"]),
        command_id=int(row["command_id"]),
        takes_value=bool(row["takes_value"]),
    )
```

- [ ] **Step 4: Der Export persistiert die Kommandos**

In `src/loxmatter/cli.py` im `export`-Kommando, direkt nach `store.register_signals(...)`
und innerhalb desselben `try`, ergänzen:

```python
        stored_commands = store.register_commands(
            device_id, extract_commands(snapshot, raw=raw_commands), snapshot.node_id
        )
```

Und die `LoxoneCommand`-Liste aus `stored_commands` statt aus `device_commands` bauen,
damit der Schlüssel in der Vorlage und der Schlüssel in der Datenbank aus **einer**
Quelle stammen. Zwei Stellen, die denselben Schlüssel unabhängig zusammensetzen, driften
auseinander — und das fiele erst auf, wenn ein Loxone-Baustein nichts mehr tut.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/model tests/test_export_cli.py -v`
Expected: PASS; die bestehenden Export-Tests müssen unverändert durchlaufen.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/model src/loxmatter/cli.py tests/model
git commit -m "feat(model): exportierte Kommandos sind zur Laufzeit aufloesbar"
```

---

### Task 3: UDP-Sender

**Files:**
- Create: `src/loxmatter/loxone/sender.py`
- Create: `tests/loxone/test_sender.py`

**Interfaces:**
- Consumes: `datagram` aus `loxone.values`
- Produces:
  - `class UdpSender` mit `__init__(self, host: str, port: int, *, rate_limit: float = 50.0)`
  - `async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool` — `True`, wenn tatsächlich gesendet wurde
  - `async def close(self) -> None`
  - `RATE_LIMIT_PER_SECOND: float`

- [ ] **Step 1: Write the failing test**

`tests/loxone/test_sender.py`:

```python
import asyncio
import socket

import pytest

from loxmatter.loxone.sender import UdpSender


@pytest.fixture
def empfaenger():
    """Ein UDP-Socket auf 127.0.0.1 - verlaesst die Maschine nicht."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    yield sock
    sock.close()


def empfangen(sock: socket.socket) -> list[bytes]:
    pakete = []
    while True:
        try:
            pakete.append(sock.recv(4096))
        except BlockingIOError:
            return pakete


async def test_sends_the_expected_datagram(empfaenger):
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_2_power", 0.0003)
    await asyncio.sleep(0.05)
    assert empfangen(empfaenger) == [b"d1_2_power:0.0003"]
    await sender.close()


async def test_unchanged_value_is_not_resent(empfaenger):
    """Entprellung: ein Sensor, der jede Sekunde denselben Wert meldet, flutet nicht."""
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    assert await sender.send("d1_1_temp", 21.5) is True
    assert await sender.send("d1_1_temp", 21.5) is False
    await asyncio.sleep(0.05)
    assert len(empfangen(empfaenger)) == 1
    await sender.close()


async def test_changed_value_is_sent(empfaenger):
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.6) is True
    await asyncio.sleep(0.05)
    assert len(empfangen(empfaenger)) == 2
    await sender.close()


async def test_force_resends_an_unchanged_value(empfaenger):
    """Der Full-Resend nach einem Miniserver-Neustart muss die Entprellung umgehen."""
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.5, force=True) is True
    await sender.close()


async def test_rate_limit_staggers_a_burst(empfaenger):
    """Spec 6.4: gestaffelt auf etwa 50 Datagramme pro Sekunde."""
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port, rate_limit=100.0)
    start = asyncio.get_running_loop().time()
    for i in range(10):
        await sender.send(f"d1_1_a{i}", i)
    dauer = asyncio.get_running_loop().time() - start
    assert dauer >= 0.09
    await sender.close()


async def test_send_after_close_raises():
    sender = UdpSender("127.0.0.1", 7000)
    await sender.close()
    with pytest.raises(RuntimeError, match="geschlossen"):
        await sender.send("d1_1_temp", 21.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_sender.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.loxone.sender'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/loxone/sender.py`:

```python
"""Verschickt Werte als UDP-Datagramme an den Miniserver.

Kennt kein Matter. Er bekommt fertige Schluessel und fertige Werte.

Zwei Eigenschaften sind nicht optional:

Entprellung - ein Matter-Geraet meldet einen Messwert gerne im Sekundentakt,
auch wenn er sich nicht aendert. Unveraenderte Werte erneut zu schicken kostet
nur Last, und der Miniserver mag keinen UDP-Sturm.

Rate-Limit - beim Full-Resend nach einem Miniserver-Neustart stehen hunderte
Datagramme gleichzeitig an. Gestaffelt kommen sie an, im Schwall nicht
(Spec 6.4).
"""

from __future__ import annotations

import asyncio
import socket

from loxmatter.loxone.values import datagram

RATE_LIMIT_PER_SECOND = 50.0


class UdpSender:
    def __init__(self, host: str, port: int, *, rate_limit: float = RATE_LIMIT_PER_SECOND) -> None:
        self._target = (host, port)
        self._interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._socket: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._letzte: dict[str, str] = {}
        self._naechster_sendezeitpunkt = 0.0
        self._sperre = asyncio.Lock()

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        """Sendet, wenn sich der Wert geaendert hat oder force gesetzt ist."""
        if self._socket is None:
            raise RuntimeError("UdpSender ist geschlossen")

        paket = datagram(key, value)
        text = paket.decode()
        if not force and self._letzte.get(key) == text:
            return False

        async with self._sperre:
            loop = asyncio.get_running_loop()
            wartezeit = self._naechster_sendezeitpunkt - loop.time()
            if wartezeit > 0:
                await asyncio.sleep(wartezeit)
            self._socket.sendto(paket, self._target)
            self._naechster_sendezeitpunkt = loop.time() + self._interval

        self._letzte[key] = text
        return True

    async def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_sender.py -v`
Expected: PASS, 6 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/loxone/sender.py tests/loxone/test_sender.py
git commit -m "feat(loxone): UDP-Sender mit Entprellung und Rate-Limit"
```

---

### Task 4: Laufzeit der Sensorrichtung

Das Herzstück: Matter-Subscriptions werden zu Datagrammen. Dazu die drei Dinge, die
ein virtueller UDP-Eingang von sich aus nicht kann — Events, Erreichbarkeit und
Zustands-Wiederherstellung.

**Files:**
- Create: `src/loxmatter/loxone/runtime.py`
- Create: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Store`, `StoredSignal`, `UdpSender`, `to_loxone_value`
- Produces:
  - `class Runtime` mit `__init__(self, store: Store, sender: UdpSender, *, heartbeat_seconds: float = 30.0, resend_seconds: float = 300.0)`
  - `async def on_attribute(self, device_id: int, path: str, raw: object) -> None`
  - `async def on_event(self, device_id: int, path: str) -> None`
  - `async def set_online(self, device_id: int, online: bool) -> None`
  - `async def resend_all(self) -> int` — Anzahl gesendeter Datagramme
  - `async def start(self) -> None`, `async def stop(self) -> None`
  - `PULSE_MILLISECONDS: int`

- [ ] **Step 1: Write the failing test**

`tests/loxone/test_runtime.py`:

```python
import asyncio
import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    """Merkt sich, was gesendet wurde, statt es zu verschicken."""

    def __init__(self) -> None:
        self.gesendet: list[tuple[str, object, bool]] = []

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self.gesendet.append((key, value, force))
        return True

    async def close(self) -> None:
        return None

    def keys(self) -> list[str]:
        return [k for k, _, _ in self.gesendet]


@pytest.fixture
def umgebung(tmp_path):
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)
    sender = FakeSender()
    runtime = Runtime(store, sender)
    yield runtime, sender, store, device_id, snap
    store.close()


async def test_attribute_change_becomes_a_scaled_datagram(umgebung):
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sender.gesendet == [(f"d{device_id}_2_voltage", pytest.approx(230.0), False)]


async def test_unmappable_attribute_is_not_sent(umgebung):
    """Spec 6.6: Listen werden nie zu einem Datagramm."""
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "0/29/1", [29, 31, 40])
    assert sender.gesendet == []


async def test_unknown_path_is_ignored_not_raised(umgebung):
    """Ein Gerät kann Attribute melden, die beim Export nicht dabei waren."""
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "9/9999/9", 1)
    assert sender.gesendet == []


async def test_event_sends_a_pulse_and_a_counter(umgebung):
    """Spec 6.3: der Impuls erzeugt die Flanke, der Zaehler ueberlebt ein verlorenes Paket."""
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_event(device_id, "1/59/1")
    keys = sender.keys()
    assert f"d{device_id}_1_press" in keys
    assert f"d{device_id}_1_press_n" in keys


async def test_pulse_falls_back_to_zero(umgebung):
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_event(device_id, "1/59/1")
    await asyncio.sleep(Runtime.PULSE_MILLISECONDS / 1000 + 0.1)
    impulse = [(k, v) for k, v, _ in sender.gesendet if k == f"d{device_id}_1_press"]
    assert impulse == [(f"d{device_id}_1_press", True), (f"d{device_id}_1_press", False)]


async def test_counter_increases_monotonically(umgebung):
    runtime, sender, _, device_id, _ = umgebung
    for _ in range(3):
        await runtime.on_event(device_id, "1/59/1")
    zaehler = [v for k, v, _ in sender.gesendet if k == f"d{device_id}_1_press_n"]
    assert zaehler == [1, 2, 3]


async def test_online_signal_is_sent(umgebung):
    runtime, sender, _, device_id, _ = umgebung
    await runtime.set_online(device_id, False)
    assert (f"d{device_id}_online", False, False) in sender.gesendet


async def test_resend_forces_every_known_value(umgebung):
    """Spec 6.4: nach einem Miniserver-Neustart muss die Entprellung umgangen werden."""
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.gesendet.clear()
    anzahl = await runtime.resend_all()
    assert anzahl == 1
    assert sender.gesendet[0][2] is True


async def test_resend_of_an_empty_runtime_sends_nothing(umgebung):
    runtime, _, _, _, _ = umgebung
    assert await runtime.resend_all() == 0


async def test_heartbeat_toggles(umgebung):
    """Spec 6.5: bridge_alive deckt "Container tot" und "Netz weg" gleichermassen ab."""
    _, sender, store, _, _ = umgebung
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.16)
    await runtime.stop()
    werte = [v for k, v, _ in sender.gesendet if k == "bridge_alive"]
    assert len(werte) >= 2
    assert werte[0] != werte[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_runtime.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.loxone.runtime'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/loxone/runtime.py`:

```python
"""Verbindet Matter-Subscriptions mit dem UDP-Sender.

Hier stehen die drei Dinge, die ein virtueller UDP-Eingang von sich aus nicht
kann:

Events (Spec 6.3) - ein Eingang traegt Werte, kein "etwas ist passiert". Jedes
Event wird zu einem Impuls, der eine Flanke erzeugt, und einem monotonen
Zaehler, der ein verlorenes UDP-Paket ueberlebt.

Erreichbarkeit (Spec 6.5) - je Geraet ein digitales Signal, dazu ein globaler
Heartbeat, der in Loxone als Watchdog dient und "Container tot" wie "Netz weg"
gleichermassen abdeckt.

Zustands-Wiederherstellung (Spec 6.4) - UDP ist zustandslos. Nach einem
Neustart des Miniservers stehen alle Eingaenge auf ihrem Defaultwert, bis das
naechste Update kommt; bei einem Temperatursensor koennen das Stunden sein.
"""

from __future__ import annotations

import asyncio
import contextlib

from typing import Protocol

from loxmatter.loxone.values import to_loxone_value
from loxmatter.matter.models import SignalKind
from loxmatter.model.store import Store

PULSE_MILLISECONDS = 200
HEARTBEAT_KEY = "bridge_alive"


class Sender(Protocol):
    """Was die Laufzeit vom Sender braucht - damit Tests ihn ersetzen koennen."""

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool: ...

    async def close(self) -> None: ...


class Runtime:
    PULSE_MILLISECONDS = PULSE_MILLISECONDS

    def __init__(
        self,
        store: Store,
        sender: Sender,
        *,
        heartbeat_seconds: float = 30.0,
        resend_seconds: float = 300.0,
    ) -> None:
        self._store = store
        self._sender = sender
        self._heartbeat_seconds = heartbeat_seconds
        self._resend_seconds = resend_seconds
        self._letzte_werte: dict[str, float | bool] = {}
        self._zaehler: dict[str, int] = {}
        self._heartbeat_an = False
        self._aufgaben: list[asyncio.Task[None]] = []
        self._signale: dict[tuple[int, str, str], str] = {}

    def _schluessel(self, device_id: int, path: str, kind: SignalKind) -> str | None:
        """Findet den unveraenderlichen Schluessel zu einem Matter-Pfad."""
        cache_key = (device_id, path, kind.value)
        if cache_key not in self._signale:
            treffer = [
                s
                for s in self._store.signals(device_id)
                if s.ref.path == path and s.ref.kind is kind
            ]
            self._signale[cache_key] = treffer[0].key if treffer else ""
        return self._signale[cache_key] or None

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        key = self._schluessel(device_id, path, SignalKind.ATTRIBUTE)
        if key is None:
            return
        treffer = [s for s in self._store.signals(device_id) if s.key == key]
        wert = to_loxone_value(treffer[0].ref, raw)
        if wert is None:
            return
        self._letzte_werte[key] = wert
        await self._sender.send(key, wert)

    async def on_event(self, device_id: int, path: str) -> None:
        key = self._schluessel(device_id, path, SignalKind.EVENT)
        if key is None:
            return
        self._zaehler[key] = self._zaehler.get(key, 0) + 1
        await self._sender.send(key, True)
        await self._sender.send(f"{key}_n", self._zaehler[key])
        self._letzte_werte[f"{key}_n"] = self._zaehler[key]
        self._aufgaben.append(asyncio.create_task(self._impuls_zuruecknehmen(key)))

    async def _impuls_zuruecknehmen(self, key: str) -> None:
        await asyncio.sleep(PULSE_MILLISECONDS / 1000)
        await self._sender.send(key, False)

    async def set_online(self, device_id: int, online: bool) -> None:
        key = f"d{device_id}_online"
        self._letzte_werte[key] = online
        await self._sender.send(key, online)

    async def resend_all(self) -> int:
        """Schickt jeden bekannten Wert erneut, an der Entprellung vorbei."""
        anzahl = 0
        for key, wert in list(self._letzte_werte.items()):
            await self._sender.send(key, wert, force=True)
            anzahl += 1
        return anzahl

    async def start(self) -> None:
        self._aufgaben.append(asyncio.create_task(self._heartbeat_schleife()))
        self._aufgaben.append(asyncio.create_task(self._resend_schleife()))

    async def stop(self) -> None:
        for aufgabe in self._aufgaben:
            aufgabe.cancel()
        for aufgabe in self._aufgaben:
            with contextlib.suppress(asyncio.CancelledError):
                await aufgabe
        self._aufgaben.clear()

    async def _heartbeat_schleife(self) -> None:
        while True:
            self._heartbeat_an = not self._heartbeat_an
            await self._sender.send(HEARTBEAT_KEY, self._heartbeat_an, force=True)
            await asyncio.sleep(self._heartbeat_seconds)

    async def _resend_schleife(self) -> None:
        while True:
            await asyncio.sleep(self._resend_seconds)
            await self.resend_all()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_runtime.py -v`
Expected: PASS, 10 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/loxone/runtime.py tests/loxone/test_runtime.py
git commit -m "feat(loxone): Laufzeit mit Impulsen, Zaehlern, Online und Full-Resend"
```

---

### Task 5: Wunschzustand → Matter-Kommando

**Files:**
- Create: `src/loxmatter/commands/__init__.py`
- Create: `src/loxmatter/commands/translate.py`
- Create: `src/loxmatter/commands/color.py`
- Create: `tests/commands/test_translate.py`
- Create: `tests/commands/test_color.py`

**Interfaces:**
- Consumes: `StoredCommand` aus `model.store`
- Produces:
  - `class MatterCall` — frozen: `node_id`, `endpoint`, `cluster_id`, `command_id`, `payload: dict[str, object]`
  - `to_matter_call(command: StoredCommand, value: str) -> MatterCall`
  - `UnsupportedValueError(ValueError)` — deutscher Text
  - in `color.py`: `kelvin_to_mireds(kelvin: float) -> int`, `rgb_to_hue_saturation(r: int, g: int, b: int) -> tuple[int, int]`

- [ ] **Step 1: Die Loxone-Farbcodierung klären, bevor Code entsteht**

**Dieser Schritt braucht Recherche, keine Vermutung.** Für OnOff und LevelControl ist die
Abbildung eindeutig. Für Farbe ist sie es nicht: Loxone überträgt Farbe als **eine Zahl**,
die je nach Betriebsart RGB oder Lumitech (Helligkeit plus Farbtemperatur) codiert. Welche
Zahl welche Bedeutung trägt, ist in der Loxone-Dokumentation zum Beleuchtungsbaustein
beschrieben.

Ermittle das Format aus der Loxone-Dokumentation und **schreibe es mit Quelle in
`color.py` als Modul-Docstring**. Rate es nicht aus Beispielwerten — eine falsch geratene
Codierung erzeugt Leuchten, die die falsche Farbe annehmen, und das sieht nach einem
Gerätefehler aus, nicht nach einem Umrechnungsfehler.

Findest du keine belastbare Quelle, ist das ein Befund: implementiere Farbtemperatur und
Helligkeit, lass RGB weg, und trag den offenen Punkt in Spec 7.3 ein.

**Unabhängig davon gilt:** es steht **keine Matter-Leuchte** zur Verfügung. Was hier
entsteht, ist gegen Referenzwerte geprüft, nicht gegen Hardware. Das ist der einzige
Teil dieser Phase, der so abschließt — vermerke es im Modul-Docstring.

Die Matter-Seite ist dagegen belegt und nicht zu recherchieren:

| Zweck | Cluster | Kommando | Nutzlast |
|---|---|---|---|
| Ein / Aus / Umschalten | 6 | 0 / 1 / 2 | keine |
| Helligkeit | 8 | 4 (`MoveToLevelWithOnOff`) | `level` 0–254, `transitionTime` |
| Farbton und Sättigung | 768 | 6 (`MoveToHueAndSaturation`) | `hue` 0–254, `saturation` 0–254 |
| Farbtemperatur | 768 | 10 (`MoveToColorTemperature`) | `colorTemperatureMireds` |

Mireds sind `1_000_000 / Kelvin`.

- [ ] **Step 2: Write the failing test**

`tests/commands/test_translate.py`:

```python
import pytest

from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.model.store import StoredCommand


def cmd(cluster: int, command: int, takes_value: bool = False) -> StoredCommand:
    return StoredCommand(
        key="d1_1_test",
        node_id=3,
        endpoint=1,
        cluster_id=cluster,
        command_id=command,
        takes_value=takes_value,
    )


def test_onoff_needs_no_payload():
    call = to_matter_call(cmd(6, 1), "1")
    assert call == MatterCall(node_id=3, endpoint=1, cluster_id=6, command_id=1, payload={})


def test_level_is_scaled_from_percent_to_254():
    call = to_matter_call(cmd(8, 4, takes_value=True), "50")
    assert call.payload["level"] == 127


def test_level_hundred_percent_is_full():
    assert to_matter_call(cmd(8, 4, takes_value=True), "100").payload["level"] == 254


def test_level_is_clamped_not_wrapped():
    """Loxone kann durch Rundung 100.4 schicken - das darf nicht zu 255 werden."""
    assert to_matter_call(cmd(8, 4, takes_value=True), "100.4").payload["level"] == 254
    assert to_matter_call(cmd(8, 4, takes_value=True), "-3").payload["level"] == 0


def test_non_numeric_value_raises_in_german():
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_call(cmd(8, 4, takes_value=True), "hell")


def test_color_temperature_converts_kelvin_to_mireds():
    call = to_matter_call(cmd(768, 10, takes_value=True), "2700")
    assert call.payload["colorTemperatureMireds"] == 370


def test_unknown_cluster_command_raises_rather_than_guessing():
    """Lieber ein klarer Fehler als ein Kommando mit erfundener Nutzlast."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(64999, 3, takes_value=True), "1")
```

`tests/commands/test_color.py`:

```python
import pytest

from loxmatter.commands.color import kelvin_to_mireds, rgb_to_hue_saturation


def test_mireds_are_the_reciprocal_of_kelvin():
    assert kelvin_to_mireds(2700) == 370
    assert kelvin_to_mireds(6500) == 153


def test_mireds_reject_zero_kelvin():
    with pytest.raises(ValueError, match="Kelvin"):
        kelvin_to_mireds(0)


@pytest.mark.parametrize(
    ("rgb", "hue", "saturation"),
    [
        ((255, 0, 0), 0, 254),
        ((0, 255, 0), 85, 254),
        ((0, 0, 255), 169, 254),
        ((255, 255, 255), 0, 0),
        ((0, 0, 0), 0, 0),
    ],
)
def test_primary_colours_map_to_known_hues(rgb, hue, saturation):
    """Referenzwerte aus der HSV-Definition, nicht aus einem Geraet."""
    h, s = rgb_to_hue_saturation(*rgb)
    assert h == pytest.approx(hue, abs=1)
    assert s == pytest.approx(saturation, abs=1)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/commands -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.commands'`

- [ ] **Step 4: Write minimal implementation**

`src/loxmatter/commands/color.py`:

```python
"""Farbraum-Umrechnung zwischen Loxone und Matter.

ACHTUNG - dieser Teil ist NICHT an Hardware validiert. Beim Bau stand keine
Matter-Leuchte zur Verfuegung; geprueft ist er ausschliesslich gegen
Referenzwerte der HSV-Definition. Von allen Abbildungen im Projekt ist diese
die fehleranfaelligste, und ein Fehler sieht hier nach einem Geraetefehler aus,
nicht nach einem Umrechnungsfehler. Vor dem ersten Einsatz an einer echten
Leuchte gegenpruefen.

Die Loxone-Seite der Codierung ist in Schritt 1 dieser Task zu recherchieren
und hier mit Quelle zu dokumentieren.
"""

from __future__ import annotations

import colorsys


def kelvin_to_mireds(kelvin: float) -> int:
    """Matter misst Farbtemperatur in Mired, dem Kehrwert von Kelvin."""
    if kelvin <= 0:
        raise ValueError(f"Kelvin muss groesser als 0 sein, war {kelvin}")
    return round(1_000_000 / kelvin)


def rgb_to_hue_saturation(r: int, g: int, b: int) -> tuple[int, int]:
    """RGB (0-255) nach Matter-Hue und -Saturation (beide 0-254)."""
    h, _, s = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return round(h * 254), round(s * 254)
```

`src/loxmatter/commands/translate.py`:

```python
"""Uebersetzt einen Wunschzustand in ein Matter-Kommando.

Dieses Modul hat spaeter zwei Aufrufer: den HTTP-Endpoint fuer die virtuellen
Ausgaenge (Task 6) und die WebUI (Phase 5). Laege die Logik in einem von
beiden, gaebe es die Umrechnung zweimal - mit garantiert auseinanderdriftendem
Verhalten (Spec 4.2).

Was nicht in der Tabelle steht, wirft. Ein Kommando mit erfundener Nutzlast an
ein echtes Geraet zu schicken ist schlechter als ein klarer Fehler.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loxmatter.commands.color import kelvin_to_mireds
from loxmatter.model.store import StoredCommand

LEVEL_MAX = 254


class UnsupportedValueError(ValueError):
    """Der Wert passt nicht zu diesem Kommando."""


@dataclass(frozen=True)
class MatterCall:
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    payload: dict[str, object] = field(default_factory=dict)


def _als_zahl(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise UnsupportedValueError(f"Wert {value!r} ist keine Zahl") from exc


def _level(value: str) -> int:
    prozent = _als_zahl(value)
    return max(0, min(LEVEL_MAX, round(prozent * LEVEL_MAX / 100)))


def to_matter_call(command: StoredCommand, value: str) -> MatterCall:
    """Baut den Matter-Aufruf zu einem exportierten Kommando-Schluessel."""

    def bauen(payload: dict[str, object]) -> MatterCall:
        return MatterCall(
            node_id=command.node_id,
            endpoint=command.endpoint,
            cluster_id=command.cluster_id,
            command_id=command.command_id,
            payload=payload,
        )

    if command.cluster_id == 6:
        return bauen({})
    if command.cluster_id == 8:
        return bauen({"level": _level(value), "transitionTime": 0})
    if command.cluster_id == 768 and command.command_id == 10:
        return bauen({"colorTemperatureMireds": kelvin_to_mireds(_als_zahl(value))})

    raise UnsupportedValueError(
        f"Cluster {command.cluster_id} Kommando {command.command_id} wird nicht unterstuetzt"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/commands -v`
Expected: PASS, 13 Tests

- [ ] **Step 6: Befund zur Farbcodierung eintragen**

Trage das Ergebnis aus Schritt 1 in Spec 7.3 ein: welche Loxone-Codierung du gefunden
hast und aus welcher Quelle, oder dass keine belastbare Quelle auffindbar war. Vermerke
dort ebenfalls, dass die Umrechnung mangels Leuchte nicht an Hardware geprüft ist.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/commands tests/commands docs/
git commit -m "feat(commands): Wunschzustand in Matter-Kommando uebersetzen"
```

---

### Task 6: HTTP-Endpoint für die virtuellen Ausgänge

**Files:**
- Create: `src/loxmatter/loxone/server.py`
- Create: `tests/loxone/test_server.py`
- Modify: `pyproject.toml` (`fastapi`, `uvicorn`)

**Interfaces:**
- Consumes: `Store`, `to_matter_call`, `Runtime`
- Produces:
  - `build_app(store: Store, invoke: Callable[[MatterCall], Awaitable[None]], runtime: Runtime) -> FastAPI`
  - Routen: `GET /cmd/{key}/{value}`, `GET /resync`, `GET /health`

- [ ] **Step 1: Abhängigkeiten ergänzen**

In `pyproject.toml` unter `dependencies`: `"fastapi>=0.115"`, `"uvicorn>=0.30"`. Dann
`uv sync`. FastAPI kommt jetzt schon dazu, weil Spec 3.3 es für die WebUI in Phase 5
vorsieht — ein Zwischenschritt über einen anderen Server wäre Arbeit, die wieder
wegfällt.

- [ ] **Step 2: Write the failing test**

`tests/loxone/test_server.py`:

```python
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    def __init__(self) -> None:
        self.gesendet: list[tuple[str, object, bool]] = []

    async def send(self, key, value, *, force: bool = False) -> bool:
        self.gesendet.append((key, value, force))
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def client(tmp_path):
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    aufrufe = []

    async def invoke(call):
        aufrufe.append(call)

    runtime = Runtime(store, FakeSender())
    app = build_app(store, invoke, runtime)
    with TestClient(app) as c:
        yield c, aufrufe, device_id
    store.close()


def test_command_reaches_matter(client):
    c, aufrufe, device_id = client
    antwort = c.get(f"/cmd/d{device_id}_1_on/1")
    assert antwort.status_code == 200
    assert len(aufrufe) == 1
    assert aufrufe[0].cluster_id == 6
    assert aufrufe[0].command_id == 1


def test_unknown_key_yields_404_not_500(client):
    c, aufrufe, _ = client
    antwort = c.get("/cmd/d1_1_gibtsnicht/1")
    assert antwort.status_code == 404
    assert aufrufe == []


def test_unsupported_value_yields_400(client):
    c, _, device_id = client
    antwort = c.get(f"/cmd/d{device_id}_1_on/../etc/passwd")
    assert antwort.status_code in (400, 404)


def test_resync_forces_a_full_resend(client):
    c, _, _ = client
    antwort = c.get("/resync")
    assert antwort.status_code == 200
    assert "gesendet" in antwort.text.lower() or antwort.json()["gesendet"] >= 0


def test_health_answers_without_touching_matter(client):
    c, aufrufe, _ = client
    assert c.get("/health").status_code == 200
    assert aufrufe == []


def test_a_failing_matter_call_yields_502_not_a_traceback(tmp_path):
    """Ein Geraet, das gerade nicht antwortet, darf keinen Traceback erzeugen."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    async def invoke(call):
        raise TimeoutError("Geraet antwortet nicht")

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    with TestClient(app) as c:
        antwort = c.get(f"/cmd/d{device_id}_1_on/1")
    assert antwort.status_code == 502
    assert "Traceback" not in antwort.text
    store.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_server.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.loxone.server'`

- [ ] **Step 4: Write minimal implementation**

`src/loxmatter/loxone/server.py`:

```python
"""Nimmt die HTTP-Aufrufe der virtuellen Ausgaenge entgegen.

Der Miniserver wertet die Antwort eines virtuellen Ausgangs nicht aus - er
schickt und vergisst. Die Statuscodes hier sind also nicht fuer Loxone da,
sondern fuer den Menschen, der im Log nachsieht, warum ein Baustein nichts
bewirkt. Entsprechend muessen sie unterscheidbar sein: 404 fuer einen
unbekannten Schluessel, 400 fuer einen unpassenden Wert, 502 fuer ein Geraet,
das nicht antwortet.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException

from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.loxone.runtime import Runtime
from loxmatter.model.store import Store

Invoker = Callable[[MatterCall], Awaitable[None]]


def build_app(store: Store, invoke: Invoker, runtime: Runtime) -> FastAPI:
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/resync")
    async def resync() -> dict[str, int]:
        """Spec 6.4: haengt im Config-Projekt am Systemstart-Baustein."""
        return {"gesendet": await runtime.resend_all()}

    @app.get("/cmd/{key}/{value}")
    async def command(key: str, value: str) -> dict[str, str]:
        try:
            stored = store.resolve_command(key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            call = to_matter_call(stored, value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            await invoke(call)
        except Exception as exc:  # noqa: BLE001 - jedes Geraeteproblem wird zu 502
            raise HTTPException(status_code=502, detail=f"Geraet nicht erreichbar: {exc}") from exc

        return {"status": "ok", "key": key}

    return app
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_server.py -v`
Expected: PASS, 6 Tests

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/loxone/server.py tests/loxone/test_server.py pyproject.toml uv.lock
git commit -m "feat(loxone): HTTP-Endpoint fuer virtuelle Ausgaenge und resync"
```

---

### Task 7: Systemvorlage

`bridge_alive` und `/resync` gehören zu keinem Gerät und brauchen deshalb ein eigenes
Vorlagenpaar (Spec 6.2, 6.4, 6.5).

**Files:**
- Modify: `src/loxmatter/export/documents.py`
- Modify: `src/loxmatter/cli.py`
- Create: `tests/export/test_system_template.py`

**Interfaces:**
- Produces: `render_system_templates(bridge_ip: str, port: int) -> tuple[bytes, bytes]`, plus CLI-Flag `--system`

- [ ] **Step 1: Write the failing test**

`tests/export/test_system_template.py`:

```python
from loxmatter.export.documents import render_system_templates


def text(raw: bytes) -> str:
    return raw.decode("utf-8-sig")


def test_input_template_carries_the_heartbeat():
    viu, _ = render_system_templates("192.168.1.50", 7000)
    assert 'Check="bridge_alive:\\v"' in text(viu)
    assert 'Analog="false"' in text(viu)


def test_output_template_carries_resync():
    _, vo = render_system_templates("192.168.1.50", 7000)
    assert 'CmdOn="/resync"' in text(vo)


def test_both_templates_have_the_info_element_first():
    for raw in render_system_templates("192.168.1.50", 7000):
        assert text(raw).split(">", 2)[2].lstrip().startswith("<Info ")


def test_both_are_utf8_with_bom_and_crlf():
    for raw in render_system_templates("192.168.1.50", 7000):
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\n" not in raw.replace(b"\r\n", b"")


def test_system_templates_carry_no_device_prefix():
    """Sie gehoeren zu keinem Geraet - ein d<id>_ waere falsch."""
    viu, vo = render_system_templates("192.168.1.50", 7000)
    assert "d1_" not in text(viu)
    assert "d1_" not in text(vo)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_system_template.py -v`
Expected: FAIL mit `ImportError: cannot import name 'render_system_templates'`

- [ ] **Step 3: Write minimal implementation**

In `src/loxmatter/export/documents.py` ergänzen:

```python
def render_system_templates(bridge_ip: str, port: int) -> tuple[bytes, bytes]:
    """Die beiden Vorlagen, die zu keinem Geraet gehoeren.

    bridge_alive ist der Watchdog (Spec 6.5): er toggelt, solange die Bridge
    laeuft, und deckt "Container tot" wie "Netz weg" gleichermassen ab.

    /resync gehoert im Config-Projekt an den Systemstart-Baustein (Spec 6.4).
    UDP ist zustandslos - ohne diesen Aufruf stehen nach einem Neustart des
    Miniservers alle Eingaenge auf ihrem Defaultwert, bei einem Temperatursensor
    womoeglich stundenlang.
    """
    viu = render_virtual_in_udp(
        "System",
        bridge_ip,
        port,
        [
            LoxoneInput(
                key="bridge_alive",
                title="Bridge erreichbar",
                comment="Watchdog: toggelt, solange die Bridge laeuft",
                analog=False,
                unit_format="",
            )
        ],
    )
    vo = render_virtual_out(
        "System",
        f"http://{bridge_ip}:8080",
        [
            LoxoneCommand(
                key="resync",
                title="Alle Werte neu senden",
                path="/resync",
                analog=False,
            )
        ],
    )
    return viu, vo
```

Dazu im `export`-Kommando das Flag. Die Systemvorlagen brauchen kein Gerät, also
darf `--system` ohne `--node` und ohne `--fixture` laufen — der Aufbau des Kommandos
prüft die Quelle sonst zuerst:

```python
system: bool = (
    typer.Option(
        False,
        "--system",
        help="Erzeugt zusätzlich die geräteunabhängigen Vorlagen "
        "(bridge_alive, /resync). Einmalig zu importieren.",
    ),
)
```

Und im Rumpf, **vor** dem Laden des Snapshots:

```python
    out.mkdir(parents=True, exist_ok=True)
    if system:
        viu_sys, vo_sys = render_system_templates(bridge_ip, port)
        (out / "VIU_Matter_System.xml").write_bytes(viu_sys)
        (out / "VO_Matter_System.xml").write_bytes(vo_sys)
        typer.echo("VIU_Matter_System.xml, VO_Matter_System.xml: Heartbeat und /resync")
        if fixture is None and node is None:
            return
```

Damit sind drei Aufrufe möglich: nur ein Gerät, nur die Systemvorlagen, oder beides.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_system_template.py -v`
Expected: PASS, 5 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/export/documents.py src/loxmatter/cli.py tests/export/test_system_template.py
git commit -m "feat(export): Systemvorlage mit Heartbeat und resync"
```

---

### Task 8: `loxmatter run`, `fake-miniserver` und der Durchstich

**Files:**
- Create: `src/loxmatter/devtools/__init__.py`
- Create: `src/loxmatter/devtools/fake_miniserver.py`
- Modify: `src/loxmatter/cli.py`
- Create: `tests/devtools/test_fake_miniserver.py`
- Modify: `deploy/testhost/docker-compose.yml`

**Interfaces:**
- Produces: CLI-Kommandos `loxmatter run` und `loxmatter fake-miniserver`
- `class FakeMiniserver` mit `async def start()`, `async def stop()`, `received: list[tuple[str, str]]`, `def silent_keys(template: Path) -> list[str]`

- [ ] **Step 1: Write the failing test**

`tests/devtools/test_fake_miniserver.py`:

```python
import asyncio
import socket
from pathlib import Path

import pytest

from loxmatter.devtools.fake_miniserver import FakeMiniserver

REFERENZ = Path(__file__).parents[1] / "fixtures" / "loxone" / "VIU_Referenz.xml"


async def test_records_incoming_datagrams():
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"d1_1_temp:21.5", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert fake.received == [("d1_1_temp", "21.5")]


async def test_malformed_datagram_is_recorded_not_dropped():
    """Ein Datagramm ohne Doppelpunkt ist ein Fehler, den man sehen will."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"kaputt", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert fake.malformed == [b"kaputt"]


async def test_silent_keys_names_signals_that_never_arrived():
    """Der eigentliche Nutzen: exportierte Signale finden, die nie feuern."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"d1_1_beispiel:1", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    stumm = fake.silent_keys(REFERENZ)
    await fake.stop()
    sock.close()
    assert "d1_1_beispiel" not in stumm
    assert stumm  # die Referenz traegt mehr als einen Befehl


def test_silent_keys_reads_the_check_attribute():
    fake = FakeMiniserver(port=0)
    assert all(not k.endswith(":\\v") for k in fake.silent_keys(REFERENZ))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/devtools -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.devtools'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/devtools/fake_miniserver.py`:

```python
"""Ersetzt den Loxone Miniserver beim Entwickeln.

Der dritte Punkt unten ist der eigentliche Gewinn: er vergleicht, welche
Signale eine erzeugte Vorlage ankuendigt, mit denen, die tatsaechlich ein
Datagramm geschickt haben. Ein exportiertes Signal, das nie feuert, ist ein
Mapping-Fehler - und ohne diesen Abgleich faellt er erst in Loxone auf, wo er
wie ein Geraetefehler aussieht.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

_CHECK = re.compile(r'Check="([^:"]+):\\v"')


class _Protokoll(asyncio.DatagramProtocol):
    def __init__(self, server: FakeMiniserver) -> None:
        self._server = server

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        text = data.decode(errors="replace")
        key, sep, value = text.partition(":")
        if not sep:
            self._server.malformed.append(data)
            return
        self._server.received.append((key, value))


class FakeMiniserver:
    def __init__(self, port: int = 7000, host: str = "127.0.0.1") -> None:
        self._host, self._port = host, port
        self.received: list[tuple[str, str]] = []
        self.malformed: list[bytes] = []
        self._transport: asyncio.DatagramTransport | None = None

    @property
    def port(self) -> int:
        if self._transport is None:
            return self._port
        return int(self._transport.get_extra_info("sockname")[1])

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protokoll(self), local_addr=(self._host, self._port)
        )
        self._transport = transport

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def silent_keys(self, template: Path) -> list[str]:
        """Signale, die die Vorlage ankuendigt, die aber nie ein Datagramm schickten."""
        angekuendigt = set(_CHECK.findall(template.read_text(encoding="utf-8-sig")))
        gesehen = {key for key, _ in self.received}
        return sorted(angekuendigt - gesehen)
```

- [ ] **Step 4: `loxmatter run` schreiben**

In `src/loxmatter/cli.py`:

```python
@app.command()
def run(
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
    miniserver: str = typer.Option(..., help="IP des Miniservers"),
    port: int = typer.Option(7000, help="UDP-Port, auf dem der Miniserver lauscht"),
    listen: int = typer.Option(8080, help="Port für die HTTP-Kommandos aus Loxone"),
    store_path: Path | None = typer.Option(None, help="Datenbank mit den Schlüsseln"),  # noqa: B008
) -> None:
    """Verbindet Matter und Loxone dauerhaft: Werte raus, Kommandos rein."""
    asyncio.run(_run(url, miniserver, port, listen, _resolve_store_path(store_path)))


async def _run(url: str, miniserver: str, port: int, listen: int, store_path: Path) -> None:
    store = Store(store_path)
    sender = UdpSender(miniserver, port)
    runtime = Runtime(store, sender)
    client = _build_client(url)

    async def invoke(call: MatterCall) -> None:
        await client.send_command(call)

    try:
        await client.connect()
        await runtime.start()
        # Ein Neustart der Bridge soll wirken wie /resync (Spec 6.4).
        await runtime.resend_all()

        config = uvicorn.Config(
            build_app(store, invoke, runtime), host="0.0.0.0", port=listen, log_level="info"
        )
        await uvicorn.Server(config).serve()
    finally:
        await runtime.stop()
        await sender.close()
        await client.disconnect()
        store.close()
```

Die Anbindung an matter-server fehlt in `BridgeMatterClient` noch in zwei Punkten und
gehört zu dieser Task:

- **`subscribe(handler)`** — meldet Attribut- und Event-Änderungen. `python-matter-server`
  liefert sie über `client.subscribe_events`; die Node-ID musst du auf die `device_id`
  des Stores abbilden, denn die Schlüssel hängen an der `device_id`, nicht an der Node-ID.
  Eine Node-ID kann sich ändern, die `device_id` nie — genau dafür existiert sie.
- **`send_command(call)`** — führt einen `MatterCall` aus, über
  `client.send_device_command(node_id, endpoint, cluster, command, payload)`.
- Erreichbarkeit: `node.available` auf `Runtime.set_online(device_id, verfügbar)`.

Schreibe für beide Tests gegen die vorhandene Fake-Upstream-Attrappe in
`tests/matter/test_client.py`, nicht gegen einen echten Server.

Dazu das Kommando für das Testdoppel:

```python
@app.command(name="fake-miniserver")
def fake_miniserver_cmd(
    port: int = typer.Option(7000, help="UDP-Port, auf dem gelauscht wird"),
    template: Path | None = typer.Option(  # noqa: B008
        None, help="Erzeugte VIU_-Vorlage: nennt am Ende die Signale, die nie feuerten"
    ),
) -> None:
    """Ersetzt den Miniserver: schreibt jedes Datagramm mit."""
    asyncio.run(_fake_miniserver(port, template))
```

Es druckt jedes Datagramm mit Zeitstempel und bei Strg-C, sofern `--template` gesetzt
ist, die stummen Signale.

- [ ] **Step 5: Vollständige Prüfung**

```bash
uv run pytest -v && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 6: Durchstich ohne Miniserver**

```bash
uv run loxmatter fake-miniserver --port 7000 --template ./export/VIU_d1_*.xml
```

In einer zweiten Sitzung:

```bash
uv run loxmatter run --url ws://10.0.1.56:5580/ws --miniserver 127.0.0.1 --port 7000
```

Erwartet: Datagramme der Steckdose erscheinen; ein Druck auf den Taster erzeugt Impuls
und Zähler; `bridge_alive` toggelt. Am Ende nennt der `fake-miniserver` die stummen
Signale — bei einer Steckdose ohne Last sind das viele, das ist kein Fehler.

- [ ] **Step 7: Durchstich mit echtem Miniserver**

**Dieser Schritt braucht einen Menschen mit Loxone Config.** Er ist der Zweck der Phase.

Vorlagen erzeugen und importieren (Gerät und System), `loxmatter run` gegen die echte
Miniserver-IP starten, und in der Loxone-Visualisierung prüfen:

1. Die Leistung der Steckdose erscheint und ändert sich, wenn du einen Verbraucher
   ansteckst.
2. Ein Druck auf den Taster löst den Impuls-Eingang aus.
3. Ein virtueller Ausgang auf `d<id>_1_toggle` schaltet die Steckdose.
4. `bridge_alive` toggelt.
5. Nach einem Neustart des Miniservers füllt der Systemstart-Baustein über `/resync`
   alle Werte sofort wieder.

Was abweicht, geht in die Spec — **nicht** in eine Anpassung der Tests.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/devtools src/loxmatter/cli.py tests/devtools deploy/
git commit -m "feat(cli): loxmatter run und fake-miniserver"
```

---

## Abschluss der Phase

Die Phase ist fertig, wenn:

1. `uv run pytest` ohne Hardware und ohne Netz durchläuft,
2. der Durchstich ohne Miniserver (Task 8 Schritt 6) läuft,
3. **die fünf Punkte aus Task 8 Schritt 7 an echter Hardware bestätigt sind**,
4. Abweichungen in der Spec stehen.

**Bleibt offen:** die Farbraum-Umrechnung ist gegen Referenzwerte geprüft, aber nicht
gegen eine Leuchte. Das ist der einzige Teil, den diese Phase nicht abschließen kann,
und er gehört als offener Punkt in die Roadmap, nicht in ein stilles Vergessen.
