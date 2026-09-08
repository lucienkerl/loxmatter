# Phase 4: Runtime Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Values flow. A measured value from the plug appears in the Miniserver, and a Loxone block switches it.

**Architecture:** Two directions that share only the store and the service process. Sensor direction: `profiles` supplies scaling factors, `loxone/values` calculates and formats, `loxone/sender` sends UDP, `loxone/runtime` connects Matter subscriptions to it and generates pulses, counters, online signals and heartbeat. Command direction: `commands/` translates a desired state into a Matter command, `loxone/server` receives the HTTP calls of the virtual outputs. In addition a `fake-miniserver`, which makes both directions testable without a real Miniserver.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, `mypy` (strict), `PyYAML`, `fastapi` + `uvicorn`, `sqlite3` and `asyncio` from the standard library.

## Global Constraints

- **Tests run without hardware and without network access.** A UDP socket on `127.0.0.1` does not count as network access — it never leaves the machine and is the only honest way to check a UDP sender. A test that needs a real device or a real Miniserver is skipped and rots (Spec 10.1).
- **German in prose, comments, docstrings and error messages**, English in identifiers and commit prefixes.
- **All data classes immutable** (`frozen=True`), unless there is a reason against it.
- **Keys are immutable** (Spec 6.2). This phase reads them and never rewrites them. Any change to a key would be a bug in this phase, not an adjustment.
- **The target unit is that of the Loxone block, not the SI unit** (Spec 7.3). Power in kW.
- **Number format: up to 6 decimal places, trailing zeros trimmed.** 300 mW must arrive as `0.0003`, not as `0`. This is not a detail: the reason for installing a metering plug is often precisely the small standby loads (Spec 7.3).
- **Datagram form:** `<key>:<value>`, matching the command detection `<key>:\v` of the exported template (Spec 6.1).
- `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy` must stay clean. ruff also formats Python blocks in Markdown.
- The unsanitized templates under `tests/fixtures/VirtualIn/` and `tests/fixtures/VirtualOut/` contain credentials of a real installation and are deliberately git-ignored. **Do not read.**

---

## Two gaps from Phase 3 that this phase closes

Found while designing this phase, not known beforehand:

**Commands are not persisted.** `extract_commands` runs at export time, the key `d1_1_on` ends up in the `VO_` template — but the store only knows `device` and `signal`. When the Miniserver later calls `/cmd/d1_1_on/1`, the bridge has no mapping back to node, endpoint, cluster and command ID. Phase 3 emits keys that it cannot resolve itself. Task 2 closes that.

**There are no scaling factors.** `clusters.yaml` carries `unit`, but no `scale`. The template contains `<v.6> kW`, and nothing converts milliwatts to kilowatts. Task 1 closes that.

## What this phase cannot complete

**The color-space conversion remains unvalidated.** Task 5 builds it and checks it against published reference values, but no Matter light is available. Of all the mappings in the project, this is the most error-prone — Loxone Lumitech against Matter Hue/Saturation or CIE xy respectively. The plan flags that in place; it is an open point of the phase, not a completed task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/loxmatter/profiles/clusters.yaml` | additionally `scale` per attribute |
| `src/loxmatter/loxone/values.py` | raw Matter value → Loxone value, and its text form |
| `src/loxmatter/loxone/sender.py` | UDP transmission: debouncing, rate limiting. Knows no Matter |
| `src/loxmatter/loxone/runtime.py` | connects subscriptions to the sender: pulses, counters, online, heartbeat, full resend |
| `src/loxmatter/model/store.py` | additionally `command` table and resolution |
| `src/loxmatter/commands/translate.py` | desired state → Matter command |
| `src/loxmatter/commands/color.py` | color-space conversion |
| `src/loxmatter/loxone/server.py` | HTTP endpoint for virtual outputs and `/resync` |
| `src/loxmatter/cli.py` | additionally `loxmatter run` |
| `src/loxmatter/devtools/` | test double for both directions (`FakeMiniserver`) |

---

### Task 1: Scaling and Number Format

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/profiles/table.py`
- Create: `src/loxmatter/loxone/__init__.py`
- Create: `src/loxmatter/loxone/values.py`
- Create: `tests/loxone/test_values.py`

**Interfaces:**
- Consumes: `SignalRef`, `SignalKind`, `lookup`, `Exportability`
- Produces:
  - `scale_factor(ref: SignalRef) -> float` in `profiles.table` — 1.0 if the table says nothing
  - `to_loxone_value(ref: SignalRef, raw: object) -> float | bool | None` in `loxone.values` — `None` if not mappable
  - `format_value(value: float | bool) -> str` in `loxone.values`
  - `datagram(key: str, value: float | bool) -> bytes` in `loxone.values`

- [x] **Step 1: Write the failing test**

`tests/loxone/test_values.py`:

```python
import pytest

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.loxone.values import datagram, format_value, to_loxone_value


def attr(cluster: int, element: int, endpoint: int = 1) -> SignalRef:
    return SignalRef(endpoint, cluster, element, SignalKind.ATTRIBUTE)


def test_temperature_is_hundredths_of_a_degree():
    """Spec 7.3: TemperatureMeasurement delivers 0.01 °C."""
    assert to_loxone_value(attr(1026, 0), 2150) == pytest.approx(21.5)


def test_power_goes_from_milliwatt_to_kilowatt():
    """Spec 7.3: Loxone computes power in kW, not in W."""
    assert to_loxone_value(attr(144, 8, endpoint=2), 5_000_000) == pytest.approx(5.0)


def test_small_power_survives_the_conversion():
    """300 mW is 0.0003 kW - exactly the standby load you want to see."""
    assert to_loxone_value(attr(144, 8, endpoint=2), 300) == pytest.approx(0.0003)


def test_level_is_scaled_from_254_to_percent():
    assert to_loxone_value(attr(8, 0), 254) == pytest.approx(100.0)
    assert to_loxone_value(attr(8, 0), 127) == pytest.approx(50.0, abs=0.2)


def test_boolean_passes_through_unscaled():
    assert to_loxone_value(attr(6, 0), True) is True


def test_unknown_cluster_passes_through_unscaled():
    """Spec 3.5: the table enriches, it does not filter."""
    assert to_loxone_value(attr(64999, 7), 42) == pytest.approx(42.0)


def test_unmappable_values_yield_none():
    """Spec 6.6: lists, structs, text and null never become a datagram."""
    assert to_loxone_value(attr(29, 1), [1, 2, 3]) is None
    assert to_loxone_value(attr(40, 1), "IKEA of Sweden") is None
    assert to_loxone_value(attr(49, 7), None) is None


def test_format_trims_trailing_zeros():
    assert format_value(21.5) == "21.5"
    assert format_value(21.0) == "21"
    assert format_value(0.0) == "0"


def test_format_keeps_six_decimals_for_small_values():
    """Without this, every load under 10 W disappears into zero."""
    assert format_value(0.0003) == "0.0003"
    assert format_value(0.000001) == "0.000001"


def test_format_renders_booleans_as_one_and_zero():
    assert format_value(True) == "1"
    assert format_value(False) == "0"


def test_datagram_matches_the_exported_check_pattern():
    """The template recognizes "<key>:\\v" - the datagram must match it (Spec 6.1)."""
    assert datagram("d1_2_power", 0.0003) == b"d1_2_power:0.0003"


def test_format_keeps_negative_values_intact():
    """A negative sign is not a rounding error and must not disappear."""
    assert format_value(-21.5) == "-21.5"
    assert format_value(-0.5) == "-0.5"
    assert format_value(-1234567.89) == "-1234567.89"


def test_format_rounds_negative_near_zero_to_plain_zero():
    """ "-0" is simply wrong in a Loxone visualization - no matter how it arises."""
    assert format_value(-1e-07) == "0"
    assert format_value(-0.0) == "0"


def test_negative_temperature_end_to_end():
    """TemperatureMeasurement in hundredths of a degree below zero - the everyday winter case."""
    ref = attr(1026, 0)
    value = to_loxone_value(ref, -1270)
    assert value == pytest.approx(-12.7)
    assert format_value(value) == "-12.7"


def test_format_never_renders_scientific_notation_for_negative_values():
    """Counterpart to test_no_value_formats_to_scientific_notation, with a negative sign."""
    assert "e" not in format_value(-0.000001).lower()
    assert "e" not in format_value(-1234567.89).lower()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_values.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.loxone'`

- [x] **Step 3: Scaling factors into the table**

Add `scale` to each of the relevant attributes in `src/loxmatter/profiles/clusters.yaml`.
The factors come from Spec 7.3:

```yaml
  8:
    attributes:
      # 0-254 to 0-100 %: 100/254
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

Watch out for the YAML trap from Phase 3: slugs like `on` and `off` must stay quoted.

- [x] **Step 4: `scale_factor` in `profiles/table.py`**

```python
def scale_factor(ref: SignalRef) -> float:
    """Factor by which a raw Matter value converts into the Loxone unit.

    1.0 if the table says nothing - unknown clusters are passed through
    raw, not discarded (Spec 3.5).
    """
    cluster = _table().get(ref.cluster_id, {})
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry:
        return 1.0
    return float(entry.get("scale", 1.0))
```

- [x] **Step 5: `loxone/values.py`**

```python
"""Converts raw Matter values into what the Miniserver expects.

Two rules from Spec 7.3 shape this module:

The target unit is that of the Loxone block, not the SI unit. The energy
manager expects kW, so we deliver kW - even though Matter measures in
milliwatts.

And from that follows the number format: from mW to kW is six orders of
magnitude. Rounding to two decimal places here would make every load under
10 W appear as 0 - and it is precisely the small standby loads that are
often the reason for installing a metering plug in the first place.
"""

from __future__ import annotations

from loxmatter.matter.models import SignalRef
from loxmatter.profiles.table import Exportability, classify, scale_factor

MAX_DECIMALS = 6


def to_loxone_value(ref: SignalRef, raw: object) -> float | bool | None:
    """Scaled value, or None if Loxone cannot accept it."""
    kind = classify(raw)
    if kind is Exportability.DIGITAL:
        return bool(raw)
    if kind is not Exportability.ANALOG:
        return None
    assert isinstance(raw, (int, float))
    return float(raw) * scale_factor(ref)


def format_value(value: float | bool) -> str:
    """Text form for the datagram: up to six decimal places, no trailing zeros.

    A value that rounds to zero is always emitted as "0" - regardless of
    sign. Otherwise a negative rounding remainder like -1e-07 would let a
    "-0" through, which would simply be wrong in the Loxone visualization.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    text = f"{value:.{MAX_DECIMALS}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def datagram(key: str, value: float | bool) -> bytes:
    """A UDP datagram in the form the exported template recognizes."""
    return f"{key}:{format_value(value)}".encode()
```

- [x] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_values.py -v`
Expected: PASS, 15 tests

- [x] **Step 7: Check against the real device**

`tests/loxone/test_values_real_device.py`:

```python
"""Checks the scaling against the recorded plug."""

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
    """2/144/4 is RMSVoltage in mV - the plug was connected to 230 V."""
    snap = plug()
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 4)
    assert to_loxone_value(ref, snap.attributes[ref.path]) == pytest.approx(230.0)


def test_exactly_109_signals_yield_a_value():
    """Spec 6.6: of 159 attribute signals, 109 reach a UDP input."""
    snap = plug()
    werte = [to_loxone_value(s, snap.attributes.get(s.path)) for s in extract_signals(snap)]
    assert sum(1 for w in werte if w is not None) == 109


def test_no_value_formats_to_scientific_notation():
    """Loxone cannot read "1e-05" - that would be a silent failure."""
    snap = plug()
    for ref in extract_signals(snap):
        wert = to_loxone_value(ref, snap.attributes.get(ref.path))
        if wert is not None:
            assert "e" not in format_value(wert).lower()
```

- [x] **Step 8: Commit**

```bash
git add src/loxmatter/loxone src/loxmatter/profiles tests/loxone
git commit -m "feat(loxone): Skalierung und Zahlenformat nach Spec 7.3"
```

---

### Task 2: Make commands resolvable in the store

Closes the gap from Phase 3: the exporter writes `/cmd/d1_1_on/<v>` into the template,
but nothing can later map this key back onto a Matter command.

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Modify: `src/loxmatter/cli.py` (export persists the commands)
- Create: `tests/model/test_store_commands.py`

**Interfaces:**
- Consumes: `DeviceCommand` from `export.commands`, `NodeSnapshot`
- Produces:
  - `class StoredCommand` — frozen: `key`, `slug`, `node_id`, `endpoint`, `cluster_id`, `command_id`, `takes_value`
  - `class UnknownCommandError(KeyError)` — its own `__str__`, so that `str(exc)` does not
    wrap `repr()` quotes around the message (Task 6 turns this into an HTTP body)
  - `Store.register_commands(device_id: int, commands: Sequence[DeviceCommand], node_id: int) -> list[StoredCommand]`
    — reports a genuine key collision instead of silently discarding it, and
    updates `takes_value`/`slug` of an already known command on every call
  - `Store.resolve_command(key: str) -> StoredCommand` — raises `UnknownCommandError` with
    a German message
  - `Store.commands(device_id: int) -> list[StoredCommand]`

- [x] **Step 1: Write the failing test**

`tests/model/test_store_commands.py`:

```python
import json
from pathlib import Path

import pytest

from loxmatter.export.commands import DeviceCommand, extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.profiles import table

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
    with pytest.raises(KeyError, match="unbekannter Kommando-Schluessel") as excinfo:
        store.resolve_command("d1_1_gibtsnicht")
    # str(KeyError(...)) would otherwise wrap repr() quotes around the whole
    # message — UnknownCommandError returns it unchanged.
    assert str(excinfo.value) == "unbekannter Kommando-Schluessel 'd1_1_gibtsnicht'"


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


def test_command_key_collision_raises_instead_of_dropping_silently(store, monkeypatch):
    """Two commands from different clusters on the same endpoint can end up
    with the same slug — a future entry in `clusters.yaml` for a second
    cluster on an endpoint that already shares a slug with `onoff`/`level`
    is a perfectly ordinary Matter arrangement. `command_slug` is
    deliberately forced to a fixed value here to reproduce exactly that.
    `register_commands` must not resolve that silently with `INSERT OR
    IGNORE` — it must fail loudly, and the device must not contain any
    commands from this failed call afterward."""
    real_command_slug = table.command_slug

    def fake_command_slug(cluster_id: int, command_id: int) -> str | None:
        if cluster_id == 3 and command_id == 0:
            return "on"
        return real_command_slug(cluster_id, command_id)

    monkeypatch.setattr("loxmatter.export.commands.command_slug", fake_command_slug)

    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    commands = extract_commands(snap)

    with pytest.raises(ValueError, match="Schluessel-Kollision"):
        store.register_commands(device_id, commands, snap.node_id)

    assert store.commands(device_id) == []


def test_takes_value_change_is_picked_up_on_reregistration(store):
    """Unlike with signals, `register_commands` used to freeze `takes_value`
    forever at first commissioning. A correction in `clusters.yaml` must
    reach an already stored command without changing its key (Spec 6.2)."""
    device_id, snap, first = registered(store, "ikea_grillplats_plug.json")
    on_before = next(c for c in first if c.slug == "on")
    assert on_before.takes_value is False

    updated = [
        DeviceCommand(
            endpoint=on_before.endpoint,
            cluster_id=on_before.cluster_id,
            command_id=on_before.command_id,
            slug=on_before.slug,
            takes_value=True,
        )
    ]
    again = store.register_commands(device_id, updated, snap.node_id)

    on_after = next(c for c in again if c.key == on_before.key)
    assert on_after.takes_value is True
    assert on_after.key == on_before.key
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/model/test_store_commands.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'register_commands'`

- [x] **Step 3: Add schema and methods**

Add to `_SCHEMA` in `src/loxmatter/model/store.py`:

```sql
CREATE TABLE IF NOT EXISTS command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device(id),
    node_id     INTEGER NOT NULL,
    endpoint    INTEGER NOT NULL,
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, command_id)
);
```

Plus the data class, `UnknownCommandError` and the methods:

```python
@dataclass(frozen=True)
class StoredCommand:
    key: str
    slug: str
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    takes_value: bool


class UnknownCommandError(KeyError):
    """`KeyError.__str__` wraps the message in `repr()`, which puts extra
    quotes around the German text — Task 6 turns this into an HTTP error
    body. The subclass returns the message unchanged; `pytest.raises(KeyError,
    ...)` still catches it, since it inherits from `KeyError`."""

    def __str__(self) -> str:
        return str(self.args[0])


def _existing_command_keys(self, device_id: int) -> set[str]:
    rows = self._db.execute("SELECT key FROM command WHERE device_id = ?", (device_id,)).fetchall()
    return {str(r["key"]) for r in rows}


def register_commands(
    self, device_id: int, commands: Sequence[DeviceCommand], node_id: int
) -> list[StoredCommand]:
    """Makes the exported command keys resolvable at runtime.

    Without this, the exporter writes keys into the template that nobody
    can later map back onto a Matter command.

    An already known command (same device_id/endpoint/cluster_id/
    command_id) keeps its key, but `takes_value` and `slug` are picked up
    freshly on every call — exactly as `register_signals` redetermines
    `unit` and `exportability` instead of freezing them forever at first
    commissioning.

    Runs as a single transaction with rollback on failure. Deliberately no
    `INSERT OR IGNORE` — that would not report a genuine key collision, but
    would silently discard the second command (see `register_signals`).
    Unlike with signals, there is no fallback strategy here: two commands
    from different clusters on the same endpoint with the same slug are a
    bug in `clusters.yaml`.
    """
    taken = self._existing_command_keys(device_id)
    try:
        for command in commands:
            existing = self._db.execute(
                "SELECT key FROM command WHERE device_id = ? AND endpoint = ?"
                " AND cluster_id = ? AND command_id = ?",
                (device_id, command.endpoint, command.cluster_id, command.command_id),
            ).fetchone()
            if existing is not None:
                self._db.execute(
                    "UPDATE command SET takes_value = ?, slug = ? WHERE key = ?",
                    (int(command.takes_value), command.slug, existing["key"]),
                )
                continue

            key = f"d{device_id}_{command.endpoint}_{command.slug}"
            if key in taken:
                collision = self._db.execute(
                    "SELECT cluster_id, command_id FROM command WHERE device_id = ? AND key = ?",
                    (device_id, key),
                ).fetchone()
                raise ValueError(
                    f"Schluessel-Kollision fuer Geraet {device_id}: Kommando "
                    f"(cluster_id={command.cluster_id}, command_id={command.command_id}) "
                    f"und (cluster_id={collision['cluster_id']}, "
                    f"command_id={collision['command_id']}) teilen sich den "
                    f"Schluessel {key!r}"
                )
            taken.add(key)
            self._db.execute(
                "INSERT INTO command "
                "(device_id, node_id, endpoint, cluster_id, command_id, key, slug,"
                " takes_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device_id,
                    node_id,
                    command.endpoint,
                    command.cluster_id,
                    command.command_id,
                    key,
                    command.slug,
                    int(command.takes_value),
                ),
            )
    except (ValueError, sqlite3.Error):
        self._db.rollback()
        raise
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
        raise UnknownCommandError(f"unbekannter Kommando-Schluessel {key!r}")
    return self._as_command(row)


@staticmethod
def _as_command(row: sqlite3.Row) -> StoredCommand:
    return StoredCommand(
        key=row["key"],
        slug=row["slug"],
        node_id=int(row["node_id"]),
        endpoint=int(row["endpoint"]),
        cluster_id=int(row["cluster_id"]),
        command_id=int(row["command_id"]),
        takes_value=bool(row["takes_value"]),
    )
```

- [x] **Step 4: Export persists the commands**

In `src/loxmatter/cli.py`, in the `export` command, directly after `store.register_signals(...)`
and inside the same `try`, add:

```python
        stored_commands = store.register_commands(
            device_id, extract_commands(snapshot, raw=raw_commands), snapshot.node_id
        )
```

And build the `LoxoneCommand` list from `stored_commands` instead of from `device_commands`,
so that the key in the template and the key in the database come from **one**
source. Two places that assemble the same key independently drift apart —
and that would only be noticed once a Loxone block stops doing anything. The title
comes from `c.slug` — `StoredCommand` now carries the slug in its own column, instead
of parsing it back out of the key (`c.key.split("_", 2)[-1]`). Two places that
have to know the same composition separately are the same drift bug
as above, just one level deeper.

- [x] **Step 5: Run tests**

Run: `uv run pytest tests/model tests/test_export_cli.py -v`
Expected: PASS, 8 tests in `test_store_commands.py`; the existing export tests must
run through unchanged.

- [x] **Step 6: Commit**

```bash
git add src/loxmatter/model src/loxmatter/cli.py tests/model
git commit -m "feat(model): exportierte Kommandos sind zur Laufzeit aufloesbar"
```

---

### Task 3: UDP sender

**Files:**
- Create: `src/loxmatter/loxone/sender.py`
- Create: `tests/loxone/test_sender.py`

**Interfaces:**
- Consumes: `datagram` from `loxone.values`
- Produces:
  - `class UdpSender` with `__init__(self, host: str, port: int, *, rate_limit: float = 50.0)`
  - `async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool` — `True` if it was actually sent
  - `async def close(self) -> None`
  - `RATE_LIMIT_PER_SECOND: float`

- [x] **Step 1: Write the failing test**

`tests/loxone/test_sender.py`:

```python
import asyncio
import socket

import pytest

from loxmatter.loxone.sender import UdpSender


@pytest.fixture
def receiver():
    """A UDP socket on 127.0.0.1 - never leaves the machine."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    yield sock
    sock.close()


def received(sock: socket.socket) -> list[bytes]:
    packets = []
    while True:
        try:
            packets.append(sock.recv(4096))
        except BlockingIOError:
            return packets


async def test_sends_the_expected_datagram(receiver):
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_2_power", 0.0003)
    await asyncio.sleep(0.05)
    assert received(receiver) == [b"d1_2_power:0.0003"]
    await sender.close()


async def test_unchanged_value_is_not_resent(receiver):
    """Debouncing: a sensor that reports the same value every second does not flood."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    assert await sender.send("d1_1_temp", 21.5) is True
    assert await sender.send("d1_1_temp", 21.5) is False
    await asyncio.sleep(0.05)
    assert len(received(receiver)) == 1
    await sender.close()


async def test_changed_value_is_sent(receiver):
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.6) is True
    await asyncio.sleep(0.05)
    assert len(received(receiver)) == 2
    await sender.close()


async def test_force_resends_an_unchanged_value(receiver):
    """The full resend after a Miniserver restart must bypass debouncing."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.5, force=True) is True
    await sender.close()


async def test_rate_limit_staggers_a_burst(receiver):
    """Spec 6.4: staggered to about 50 datagrams per second."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port, rate_limit=100.0)
    start = asyncio.get_running_loop().time()
    for i in range(10):
        await sender.send(f"d1_1_a{i}", i)
    duration = asyncio.get_running_loop().time() - start
    assert duration >= 0.09
    await sender.close()


async def test_send_after_close_raises():
    sender = UdpSender("127.0.0.1", 7000)
    await sender.close()
    with pytest.raises(RuntimeError, match="geschlossen"):
        await sender.send("d1_1_temp", 21.5)


async def test_close_during_in_flight_send_does_not_crash(receiver):
    """A close() while a send is parked in the rate-limit sleep must never
    trigger an AttributeError from an already-closed socket - either the
    send completes cleanly, or it sees the documented RuntimeError."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port, rate_limit=10.0)
    await sender.send("d1_1_a", 1)

    async def delayed_send() -> bool | RuntimeError:
        try:
            return await sender.send("d1_1_b", 2)
        except RuntimeError as error:
            return error

    send_task = asyncio.create_task(delayed_send())
    await asyncio.sleep(0.02)
    close_task = asyncio.create_task(sender.close())

    result = await send_task
    await close_task

    assert result is True or isinstance(result, RuntimeError)


async def test_close_is_idempotent():
    sender = UdpSender("127.0.0.1", 7000)
    await sender.close()
    await sender.close()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_sender.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.loxone.sender'`

- [x] **Step 3: Write minimal implementation**

`src/loxmatter/loxone/sender.py`:

```python
"""Sends values as UDP datagrams to the Miniserver.

Knows no Matter. It receives finished keys and finished values.

Two properties are not optional:

Debouncing - a Matter device likes to report a measured value once a
second, even when it does not change. Resending unchanged values only
costs load, and the Miniserver does not like a UDP storm.

Rate limit - during the full resend after a Miniserver restart, hundreds
of datagrams are pending at once. They should arrive staggered, not in a
burst (Spec 6.4).
"""

from __future__ import annotations

import asyncio
import socket

from loxmatter.loxone.values import datagram

RATE_LIMIT_PER_SECOND = 50.0


class UdpSender:
    def __init__(self, host: str, port: int, *, rate_limit: float = RATE_LIMIT_PER_SECOND) -> None:
        """Sets up the UDP socket. A rate_limit of 0 or below means: no rate limit."""
        self._target = (host, port)
        self._interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._socket: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._last_sent: dict[str, str] = {}
        self._next_send_time = 0.0
        self._lock = asyncio.Lock()

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        """Sends if the value has changed or force is set."""
        if self._socket is None:
            raise RuntimeError("UdpSender ist geschlossen")

        packet = datagram(key, value)
        text = packet.decode()
        if not force and self._last_sent.get(key) == text:
            return False

        async with self._lock:
            if self._socket is None:
                raise RuntimeError("UdpSender ist geschlossen")
            loop = asyncio.get_running_loop()
            wait_time = self._next_send_time - loop.time()
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._socket.sendto(packet, self._target)
            self._next_send_time = loop.time() + self._interval

        self._last_sent[key] = text
        return True

    async def close(self) -> None:
        """Closes the socket. Takes the same lock as send(), so that a send
        currently parked in the rate-limit sleep does not hit an already
        closed socket. Calling it more than once stays harmless.
        """
        async with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_sender.py -v`
Expected: PASS, 8 tests

- [x] **Step 5: Commit**

```bash
git add src/loxmatter/loxone/sender.py tests/loxone/test_sender.py
git commit -m "feat(loxone): UDP-Sender mit Entprellung und Rate-Limit"
```

---

### Task 4: Runtime of the sensor direction

The centerpiece: Matter subscriptions become datagrams. Plus the three things
a virtual UDP input cannot do on its own — events, reachability and
state recovery.

**Files:**
- Create: `src/loxmatter/loxone/runtime.py`
- Create: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Store`, `StoredSignal`, `UdpSender`, `to_loxone_value`
- Produces:
  - `class Runtime` with `__init__(self, store: Store, sender: UdpSender, *, heartbeat_seconds: float = 30.0, resend_seconds: float = 300.0)`
  - `async def on_attribute(self, device_id: int, path: str, raw: object) -> None`
  - `async def on_event(self, device_id: int, path: str) -> None`
  - `async def set_online(self, device_id: int, online: bool) -> None`
  - `async def resend_all(self) -> int` — number of datagrams sent
  - `async def seed_from_snapshot(self, snapshots: Sequence[NodeSnapshot]) -> int` — **Addendum,
    live run 2026-09-02:** fills `_last_values` from the current device state
    (`BridgeMatterClient.snapshots()`), without sending itself — a resend right after
    startup (see Task 8) would otherwise have nothing to send, because `_last_values` is
    empty at startup and a value otherwise only lands there via a changing subscription. See
    Spec 6.4 and the corresponding report.
  - `async def start(self) -> None`, `async def stop(self) -> None`
  - `PULSE_MILLISECONDS: int`

- [x] **Step 1: Write the failing test**

`tests/loxone/test_runtime.py`:

```python
import asyncio
import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    """Remembers what was sent instead of actually sending it."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, object, bool]] = []

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self.sent.append((key, value, force))
        return True

    async def close(self) -> None:
        return None

    def keys(self) -> list[str]:
        return [k for k, _, _ in self.sent]


class FlakySender(FakeSender):
    """Like FakeSender, but raises a RuntimeError on the nth call - for
    tests that want to reproduce a failed send attempt."""

    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self._fail_on_call = fail_on_call
        self._calls = 0

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise RuntimeError("Sender kaputt")
        return await super().send(key, value, force=force)


@pytest.fixture
def environment(tmp_path):
    """Two devices in one store: the plug supplies the attribute for
    the scaling tests (2/144/4), the button supplies the event for the
    pulse tests (1/59/1) — the plug has no switch cluster and cannot
    supply an event."""
    store = Store(tmp_path / "t.sqlite")

    plug_raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    plug_snap = NodeSnapshot.from_raw(plug_raw["node_id"], plug_raw)
    device_id = store.register_device(plug_snap)
    store.register_signals(device_id, plug_snap)
    store.register_commands(device_id, extract_commands(plug_snap), plug_snap.node_id)

    button_raw = json.loads((FIXTURES / "ikea_bilresa_button.json").read_text(encoding="utf-8"))
    button_snap = NodeSnapshot.from_raw(button_raw["node_id"], button_raw)
    button_device_id = store.register_device(button_snap)
    store.register_signals(button_device_id, button_snap)

    sender = FakeSender()
    runtime = Runtime(store, sender)
    yield runtime, sender, store, device_id, button_device_id
    store.close()


async def test_attribute_change_becomes_a_scaled_datagram(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sender.sent == [(f"d{device_id}_2_voltage", pytest.approx(230.0), False)]


async def test_unmappable_attribute_is_not_sent(environment):
    """Spec 6.6: lists never become a datagram."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "0/29/1", [29, 31, 40])
    assert sender.sent == []


async def test_unknown_path_is_ignored_not_raised(environment):
    """A device can report attributes that were not there at export time."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "9/9999/9", 1)
    assert sender.sent == []


async def test_event_sends_a_pulse_and_a_counter(environment):
    """Spec 6.3: the pulse produces the edge, the counter survives a lost packet."""
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    keys = sender.keys()
    assert f"d{button_device_id}_1_press" in keys
    assert f"d{button_device_id}_1_press_n" in keys


async def test_pulse_falls_back_to_zero(environment):
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    await asyncio.sleep(Runtime.PULSE_MILLISECONDS / 1000 + 0.1)
    pulses = [(k, v) for k, v, _ in sender.sent if k == f"d{button_device_id}_1_press"]
    assert pulses == [
        (f"d{button_device_id}_1_press", True),
        (f"d{button_device_id}_1_press", False),
    ]


async def test_counter_increases_monotonically(environment):
    runtime, sender, _, _, button_device_id = environment
    for _ in range(3):
        await runtime.on_event(button_device_id, "1/59/1")
    counters = [v for k, v, _ in sender.sent if k == f"d{button_device_id}_1_press_n"]
    assert counters == [1, 2, 3]


async def test_online_signal_is_sent(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.set_online(device_id, False)
    assert (f"d{device_id}_online", False, False) in sender.sent


async def test_resend_forces_every_known_value(environment):
    """Spec 6.4: after a Miniserver restart, debouncing must be bypassed."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()
    count = await runtime.resend_all()
    assert count == 1
    assert sender.sent[0][2] is True


async def test_resend_of_an_empty_runtime_sends_nothing(environment):
    runtime, _, _, _, _ = environment
    assert await runtime.resend_all() == 0


async def test_heartbeat_toggles(environment):
    """Spec 6.5: bridge_alive covers "container dead" and "network gone" alike."""
    _, sender, store, _, _ = environment
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.16)
    await runtime.stop()
    values = [v for k, v, _ in sender.sent if k == "bridge_alive"]
    assert len(values) >= 2
    assert values[0] != values[1]


async def test_heartbeat_survives_a_failed_send(environment):
    """Review-Fix Important #1: per the module docstring, the heartbeat
    covers "container dead" and "network gone" alike - a single failed
    send attempt must therefore not end the watchdog loop, otherwise the
    Loxone watchdog freezes on the last value while the bridge has long
    gone silent."""
    _, _, store, _, _ = environment
    sender = FlakySender(fail_on_call=2)
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.22)
    await runtime.stop()
    values = [v for k, v, _ in sender.sent if k == "bridge_alive"]
    # The second call fails (see FlakySender) - without the fix the loop
    # would die there and no further values would ever arrive.
    assert len(values) >= 3


async def test_stop_completes_even_if_a_task_already_died(environment):
    """Review-Fix Important #1, companion bug: contextlib.suppress(CancelledError)
    only suppresses a cancellation, not some other exception that a
    task already died from before `stop()`. The old implementation let
    `stop()` abort on exactly that exception, skipping the clearing of
    the task list in the process."""
    runtime, _, _, _, _ = environment

    async def boom() -> None:
        raise RuntimeError("Task ist schon vor stop() gestorben")

    dead_task = asyncio.create_task(boom())
    await asyncio.sleep(0)  # actually let the task die
    assert dead_task.done()
    runtime._tasks.append(dead_task)

    await runtime.start()
    await runtime.stop()  # must not fail on the already-dead task

    assert runtime._tasks == []
    assert runtime._pulse_tasks == set()


async def test_stop_lowers_an_in_flight_pulse(environment):
    """Review-Fix Important #2: a cancellation during the pulse sleep
    otherwise skips the `send(key, False)` - the digital signal would stay
    stuck at 1 until the next event on this key."""
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    await runtime.stop()
    key = f"d{button_device_id}_1_press"
    values = [v for k, v, _ in sender.sent if k == key]
    assert values[-1] is False


async def test_invalidate_index_lets_a_newly_registered_signal_through(environment, monkeypatch):
    """Review-Fix Important #3: `Store.register_signals` can add a new
    signal to an already indexed device at any time (e.g. after a firmware
    update). Without `invalidate_index` this signal stays invisible to the
    runtime, because `_signal_for` reads from the database only once per
    device."""
    runtime, sender, store, device_id, _ = environment
    plug_raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    plug_snap = NodeSnapshot.from_raw(plug_raw["node_id"], plug_raw)

    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # Initial indexing by the runtime - the path does not exist yet.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)
    store.register_signals(device_id, plug_snap)

    # The runtime's cache does not know about the new signal yet.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    runtime.invalidate_index(device_id)
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.keys() == [key]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.loxone.runtime'`

- [x] **Step 3: Write minimal implementation**

`src/loxmatter/loxone/runtime.py`:

```python
"""Connects Matter subscriptions to the UDP sender.

Here are the three things a virtual UDP input cannot do on its own:

Events (Spec 6.3) - an input carries values, not a "something happened".
Every event becomes a pulse, which produces an edge, and a monotonic
counter, which survives a lost UDP packet.

Reachability (Spec 6.5) - one digital signal per device, plus a global
heartbeat that serves as a watchdog in Loxone and covers "container dead"
and "network gone" alike. A heartbeat that dies on the first send failure
would be useless for exactly this purpose - see `_heartbeat_loop`.

State recovery (Spec 6.4) - UDP is stateless. After a Miniserver restart,
all inputs sit at their default value until the next update arrives; for a
temperature sensor that can be hours.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from loxmatter.loxone.values import to_loxone_value
from loxmatter.matter.models import SignalKind
from loxmatter.model.store import Store, StoredSignal

PULSE_MILLISECONDS = 200
HEARTBEAT_KEY = "bridge_alive"

logger = logging.getLogger(__name__)


class Sender(Protocol):
    """What the runtime needs from the sender - so tests can replace it."""

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
        self._last_values: dict[str, float | bool] = {}
        self._counters: dict[str, int] = {}
        self._heartbeat_on = False
        # Long-lived background tasks (heartbeat and resend loop).
        self._tasks: list[asyncio.Task[None]] = []
        # Short-lived pulse tasks, one per `on_event` call. A done_callback
        # immediately discards each finished task, otherwise the set would
        # grow without bound with every event (Review-Fix Minor #1) - only
        # `stop()` would ever have cleared it otherwise.
        self._pulse_tasks: set[asyncio.Task[None]] = set()
        # Keys whose pulse currently sits at True. `stop()` lowers them
        # explicitly, because a cancellation during the pulse sleep
        # otherwise skips the `send(key, False)` in `_release_pulse` and
        # the digital signal stays stuck at 1 until the next event on this
        # key (Review-Fix Important #2).
        self._pulses_high: set[str] = set()
        # Index (device_id, path, kind) -> StoredSignal, loaded from the
        # database once per device. `on_attribute` and `on_event` run on
        # every value reported by a device - without this cache that would
        # be a fresh query over ~160 rows per call, and the original
        # design even queried twice: once for the key, a second time for
        # the SignalRef. Here it is read exactly once per device; every
        # further path of the same device is a dict lookup. Whoever calls
        # `Store.register_signals` again for the same device after the
        # first indexing must call `invalidate_index` afterward -
        # otherwise a newly added signal stays invisible to this runtime
        # (Review-Fix Important #3).
        self._signals: dict[tuple[int, str, str], StoredSignal] = {}
        self._indexed: set[int] = set()

    def _signal_for(self, device_id: int, path: str, kind: SignalKind) -> StoredSignal | None:
        """Finds the stored signal for a Matter path, without querying the
        database again on every call."""
        if device_id not in self._indexed:
            for stored in self._store.signals(device_id):
                self._signals[(device_id, stored.ref.path, stored.ref.kind.value)] = stored
            self._indexed.add(device_id)
        signal = self._signals.get((device_id, path, kind.value))
        if signal is None:
            logger.debug(
                "Kein Signal fuer Geraet %s, Pfad %s, Art %s - Update wird verworfen",
                device_id,
                path,
                kind.value,
            )
        return signal

    def invalidate_index(self, device_id: int | None = None) -> None:
        """Discards the signal cache of one device, or - if not specified - of all devices.

        Whoever calls `Store.register_signals` again at runtime for an
        already running device (e.g. after a firmware update that unlocks
        a new cluster) MUST call this method for the affected device
        afterward. Without that, `_signal_for` stays at its once-loaded
        state: the new signal exists in the database, but updates to it
        run into the void for the rest of the process - with no error, no
        log entry other than the `debug` entry in `_signal_for`.
        """
        if device_id is None:
            self._signals.clear()
            self._indexed.clear()
            return
        self._indexed.discard(device_id)
        for cache_key in [k for k in self._signals if k[0] == device_id]:
            del self._signals[cache_key]

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        signal = self._signal_for(device_id, path, SignalKind.ATTRIBUTE)
        if signal is None:
            return
        value = to_loxone_value(signal.ref, raw)
        if value is None:
            return
        self._last_values[signal.key] = value
        await self._sender.send(signal.key, value)

    async def on_event(self, device_id: int, path: str) -> None:
        signal = self._signal_for(device_id, path, SignalKind.EVENT)
        if signal is None:
            return
        key = signal.key
        # The counter serves to detect packet loss, not an exact protocol -
        # it therefore deliberately counts up before sending. A counter
        # that got stuck on a failed send() would be no gain for this
        # purpose (Review-Fix Minor #2).
        self._counters[key] = self._counters.get(key, 0) + 1
        await self._sender.send(key, True)
        self._pulses_high.add(key)
        await self._sender.send(f"{key}_n", self._counters[key])
        self._last_values[f"{key}_n"] = self._counters[key]
        task = asyncio.create_task(self._release_pulse(key))
        task.add_done_callback(self._pulse_tasks.discard)
        self._pulse_tasks.add(task)

    async def _release_pulse(self, key: str) -> None:
        await asyncio.sleep(PULSE_MILLISECONDS / 1000)
        await self._sender.send(key, False)
        self._pulses_high.discard(key)

    async def set_online(self, device_id: int, online: bool) -> None:
        key = f"d{device_id}_online"
        self._last_values[key] = online
        await self._sender.send(key, online)

    async def resend_all(self) -> int:
        """Resends every known value, bypassing debouncing."""
        count = 0
        for key, value in list(self._last_values.items()):
            await self._sender.send(key, value, force=True)
            count += 1
        return count

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._resend_loop()))

    async def stop(self) -> None:
        # Lower every pulse currently high BEFORE the associated tasks are
        # cancelled - otherwise the cancellation skips the
        # `send(key, False)` in `_release_pulse` and the signal stays
        # stuck at 1 until the next event (Review-Fix Important #2).
        for key in list(self._pulses_high):
            await self._sender.send(key, False)
        self._pulses_high.clear()

        tasks: list[asyncio.Task[None]] = [*self._tasks, *self._pulse_tasks]
        for task in tasks:
            task.cancel()
        # gather(..., return_exceptions=True) instead of a
        # contextlib.suppress(CancelledError) per task: the latter only
        # suppresses a cancellation, not an exception that a task already
        # died from before `stop()` - that would be raised again, abort
        # the loop over the tasks, and skip `clear()` (Review-Fix
        # Important #1, companion bug).
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pulse_tasks.clear()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                self._heartbeat_on = not self._heartbeat_on
                await self._sender.send(HEARTBEAT_KEY, self._heartbeat_on, force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Exactly the failure case the heartbeat is meant to report
                # must not silence it - otherwise the Loxone watchdog
                # freezes on the last value while nothing is running
                # anymore (Review-Fix Important #1).
                logger.exception("Heartbeat konnte nicht gesendet werden - Schleife laeuft weiter")
            await asyncio.sleep(self._heartbeat_seconds)

    async def _resend_loop(self) -> None:
        while True:
            await asyncio.sleep(self._resend_seconds)
            try:
                await self.resend_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Full-Resend fehlgeschlagen - Schleife laeuft weiter")
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_runtime.py -v`
Expected: PASS, 14 tests

**Addendum, live run 2026-09-02:** 5 more tests for `seed_from_snapshot` were
added (fill the cache without sending, a resend after that sends them, an attribute
without a stored signal is skipped, seeding twice does not double anything, a node
without a known device does not abort the seeding) — making 19 tests in
`tests/loxone/test_runtime.py`. See Task 8 for the corresponding change in `_run()`.

- [x] **Step 5: Commit**

```bash
git add src/loxmatter/loxone/runtime.py tests/loxone/test_runtime.py
git commit -m "feat(loxone): Laufzeit mit Impulsen, Zaehlern, Online und Full-Resend"
```

---

### Task 5: Desired state → Matter command

**Files:**
- Create: `src/loxmatter/commands/__init__.py`
- Create: `src/loxmatter/commands/translate.py`
- Create: `src/loxmatter/commands/color.py`
- Create: `tests/commands/test_translate.py`
- Create: `tests/commands/test_color.py`

**Interfaces:**
- Consumes: `StoredCommand` from `model.store`
- Produces:
  - `class MatterCall` — frozen: `node_id`, `endpoint`, `cluster_id`, `command_id`, `payload: dict[str, object]`
  - `to_matter_call(command: StoredCommand, value: str) -> MatterCall`
  - `UnsupportedValueError(ValueError)` — German text
  - in `color.py`: `kelvin_to_mireds(kelvin: float) -> int`, `rgb_to_hue_saturation(r: int, g: int, b: int) -> tuple[int, int]`

- [x] **Step 1: Clarify the Loxone color encoding before writing code**

**This step needs research, not guesswork.** For OnOff and LevelControl the mapping is
unambiguous. For color it is not: Loxone transmits color as **a single number**,
which, depending on operating mode, encodes RGB or Lumitech (brightness plus color
temperature). Which number carries which meaning is described in the Loxone
documentation for the lighting block.

Determine the format from the Loxone documentation and **write it down with a source in
`color.py` as a module docstring**. Do not guess it from example values — a wrongly
guessed encoding produces lights that take on the wrong color, and that looks like a
device bug, not a conversion bug.

If you find no reliable source, that is a finding: implement color temperature and
brightness, leave out RGB, and enter the open point in Spec 7.3.

**Regardless of the above:** no Matter light is available. What is built here
is checked against reference values, not against hardware. This is the only
part of this phase that closes that way — note it in the module docstring.

The Matter side, on the other hand, is documented and needs no research:

| Purpose | Cluster | Command | Payload |
|---|---|---|---|
| On / off / toggle | 6 | 0 / 1 / 2 | none |
| Brightness | 8 | 4 (`MoveToLevelWithOnOff`) | `level` 0–254, `transitionTime` |
| Hue and saturation | 768 | 6 (`MoveToHueAndSaturation`) | `hue` 0–254, `saturation` 0–254 |
| Color temperature | 768 | 10 (`MoveToColorTemperature`) | `colorTemperatureMireds` |

Mireds are `1_000_000 / Kelvin`.

- [x] **Step 2: Write the failing test**

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
    """Loxone can send 100.4 due to rounding - that must not become 255."""
    assert to_matter_call(cmd(8, 4, takes_value=True), "100.4").payload["level"] == 254
    assert to_matter_call(cmd(8, 4, takes_value=True), "-3").payload["level"] == 0


def test_non_numeric_value_raises_in_german():
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_call(cmd(8, 4, takes_value=True), "hell")


def test_color_temperature_converts_kelvin_to_mireds():
    call = to_matter_call(cmd(768, 10, takes_value=True), "2700")
    assert call.payload["colorTemperatureMireds"] == 370


def test_unknown_cluster_command_raises_rather_than_guessing():
    """Better a clear error than a command with a made-up payload."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(64999, 3, takes_value=True), "1")


def test_onoff_cluster_with_unknown_command_raises():
    """Cluster 6 (OnOff) is known, but only commands 0/1/2 are. The
    dispatch must not stop already at the cluster - otherwise an unknown
    OnOff command would get a made-up empty payload instead of an
    error."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(6, 99, takes_value=True), "1")


def test_level_cluster_with_unknown_command_raises():
    """Cluster 8 (LevelControl) is known, but only commands 0/4 are
    handled here. Move/Step/Stop (command IDs among others 1, 2, 3, 5, 6, 7)
    are real LevelControl commands that can appear e.g. in a raw export
    (`raw`) without an entry in `clusters.yaml` - wrongly slipping them a
    MoveToLevelWithOnOff payload would be exactly the bug this module is
    meant to prevent."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(8, 1, takes_value=True), "50")
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
    """Reference values from the HSV definition, not from a device."""
    h, s = rgb_to_hue_saturation(*rgb)
    assert h == pytest.approx(hue, abs=1)
    assert s == pytest.approx(saturation, abs=1)
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/commands -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.commands'`

- [x] **Step 4: Write minimal implementation**

`src/loxmatter/commands/color.py`:

```python
"""Color-space conversion between Loxone and Matter.

WARNING - this part is NOT validated against hardware. No Matter light was
available during construction; it is checked exclusively against reference
values from the HSV definition. Of all the mappings in the project this is
the most error-prone, and a bug here looks like a device bug, not a
conversion bug. Cross-check against a real light before first use.

The Loxone side of the encoding is to be researched in step 1 of this task
and documented here with a source.
"""

from __future__ import annotations

import colorsys


def kelvin_to_mireds(kelvin: float) -> int:
    """Matter measures color temperature in mired, the reciprocal of Kelvin."""
    if kelvin <= 0:
        raise ValueError(f"Kelvin muss groesser als 0 sein, war {kelvin}")
    return round(1_000_000 / kelvin)


def rgb_to_hue_saturation(r: int, g: int, b: int) -> tuple[int, int]:
    """RGB (0-255) to Matter hue and saturation (both 0-254)."""
    h, _, s = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return round(h * 254), round(s * 254)
```

`src/loxmatter/commands/translate.py`:

```python
"""Translates a desired state into a Matter command.

This module later has two callers: the HTTP endpoint for the virtual
outputs (Task 6) and the WebUI (Phase 5). If the logic lived in either
one, the conversion would exist twice - with guaranteed drifting
behavior (Spec 4.2).

Whatever is not in the table raises. Sending a command with a made-up
payload to a real device is worse than a clear error.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from loxmatter.commands.color import kelvin_to_mireds
from loxmatter.model.store import StoredCommand

LEVEL_MAX = 254

_CLUSTER_ONOFF = 6
_CLUSTER_LEVEL = 8
_CLUSTER_COLOR = 768

_COMMAND_OFF = 0
_COMMAND_ON = 1
_COMMAND_TOGGLE = 2
_COMMAND_MOVE_TO_LEVEL = 0
_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF = 4
_COMMAND_COLOR_TEMPERATURE = 10


class UnsupportedValueError(ValueError):
    """The value does not fit this command."""


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


def _keine_nutzlast(_value: str) -> dict[str, object]:
    return {}


def _stufe_nutzlast(value: str) -> dict[str, object]:
    return {"level": _level(value), "transitionTime": 0}


def _farbtemperatur_nutzlast(value: str) -> dict[str, object]:
    return {"colorTemperatureMireds": kelvin_to_mireds(_als_zahl(value))}


# Dispatch on the pair (cluster ID, command ID), not just on the
# cluster ID - otherwise e.g. LevelControl-Stop (command 3) would wrongly
# get a MoveToLevelWithOnOff payload just because cluster 8 is known.
_NUTZLAST_BAUER: dict[tuple[int, int], Callable[[str], dict[str, object]]] = {
    (_CLUSTER_ONOFF, _COMMAND_OFF): _keine_nutzlast,
    (_CLUSTER_ONOFF, _COMMAND_ON): _keine_nutzlast,
    (_CLUSTER_ONOFF, _COMMAND_TOGGLE): _keine_nutzlast,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL): _stufe_nutzlast,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF): _stufe_nutzlast,
    (_CLUSTER_COLOR, _COMMAND_COLOR_TEMPERATURE): _farbtemperatur_nutzlast,
}


def to_matter_call(command: StoredCommand, value: str) -> MatterCall:
    """Builds the Matter call for an exported command key."""

    nutzlast_bauen = _NUTZLAST_BAUER.get((command.cluster_id, command.command_id))
    if nutzlast_bauen is None:
        raise UnsupportedValueError(
            f"Cluster {command.cluster_id} Kommando {command.command_id} wird nicht unterstuetzt"
        )

    return MatterCall(
        node_id=command.node_id,
        endpoint=command.endpoint,
        cluster_id=command.cluster_id,
        command_id=command.command_id,
        payload=nutzlast_bauen(value),
    )
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/commands -v`
Expected: PASS, 15 tests

- [x] **Step 6: Record the finding on color encoding**

Enter the result from step 1 into Spec 7.3: which Loxone encoding you found
and from which source, or that no reliable source could be located. Also note
there that the conversion is not checked against hardware for lack of a light.

- [x] **Step 7: Commit**

```bash
git add src/loxmatter/commands tests/commands docs/
git commit -m "feat(commands): Wunschzustand in Matter-Kommando uebersetzen"
```

---

### Task 6: HTTP endpoint for the virtual outputs

**Files:**
- Create: `src/loxmatter/loxone/server.py`
- Create: `tests/loxone/test_server.py`
- Modify: `pyproject.toml` (`fastapi`, `uvicorn`)

**Interfaces:**
- Consumes: `Store`, `to_matter_call`, `Runtime`
- Produces:
  - `build_app(store: Store, invoke: Callable[[MatterCall], Awaitable[None]], runtime: Runtime) -> FastAPI`
  - Routes: `GET /cmd/{key}/{value}`, `GET /resync`, `GET /health`

- [x] **Step 1: Add dependencies**

In `pyproject.toml` under `dependencies`: `"fastapi>=0.115"`, `"uvicorn>=0.30"`. Then
`uv sync`. FastAPI is added already at this point because Spec 3.3 plans to use it for
the WebUI in Phase 5 — an intermediate step through a different server would be work
that would just be discarded again.

- [x] **Step 2: Write the failing test**

`tests/loxone/test_server.py`:

```python
import json
from pathlib import Path

import httpx2
import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object, bool]] = []

    async def send(self, key, value, *, force: bool = False) -> bool:
        self.sent.append((key, value, force))
        return True

    async def close(self) -> None:
        return None


class BrokenResendSender(FakeSender):
    """Sends normal updates without complaint, but refuses every call -
    simulates a `UdpSender` whose socket is already closed (see
    `UdpSender.send`, which then unconditionally raises `RuntimeError`)."""

    async def send(self, key, value, *, force: bool = False) -> bool:
        raise RuntimeError("UdpSender ist geschlossen")


@pytest.fixture
async def client(tmp_path):
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    calls = []

    async def invoke(call):
        calls.append(call)

    runtime = Runtime(store, FakeSender())
    app = build_app(store, invoke, runtime)
    # httpx2.AsyncClient instead of Starlette's TestClient: TestClient runs
    # the request in an anyio portal thread that is not the thread in
    # which this fixture created the Store - but sqlite3 connections are
    # bound to their creating thread (see store.py). AsyncClient with
    # ASGITransport calls the app directly in this test's event loop,
    # without opening a second thread.
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, calls, device_id
    store.close()


async def test_command_reaches_matter(client):
    c, calls, device_id = client
    response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].cluster_id == 6
    assert calls[0].command_id == 1


async def test_unknown_key_yields_404_not_500(client):
    c, calls, _ = client
    response = await c.get("/cmd/d1_1_gibtsnicht/1")
    assert response.status_code == 404
    assert calls == []


async def test_unsupported_value_yields_400(client):
    c, _, device_id = client
    response = await c.get(f"/cmd/d{device_id}_1_on/../etc/passwd")
    assert response.status_code in (400, 404)


async def test_resync_forces_a_full_resend(client):
    c, _, _ = client
    response = await c.get("/resync")
    assert response.status_code == 200
    assert "gesendet" in response.text.lower() or response.json()["gesendet"] >= 0


async def test_health_answers_without_touching_matter(client):
    c, calls, _ = client
    assert (await c.get("/health")).status_code == 200
    assert calls == []


async def test_a_failing_matter_call_yields_502_not_a_traceback(tmp_path):
    """A device that is currently not responding must not produce a traceback."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    async def invoke(call):
        raise TimeoutError("Geraet antwortet nicht")

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 502
    assert "Traceback" not in response.text
    store.close()


async def test_a_failing_resend_yields_502_not_a_traceback(tmp_path):
    """Review-Fix Minor #3: /resync must not pass a broken sender (e.g. an
    already closed UdpSender) through as a bare 500 - the same safeguard
    as with a failing /cmd."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    runtime = Runtime(store, BrokenResendSender())
    # `on_attribute` enters the value into `_last_values` BEFORE it calls
    # the sender (see runtime.py) - so the first call fails to send, but
    # leaves `_last_values` populated as desired, so that `resend_all`
    # below has anything to try to send at all.
    with pytest.raises(RuntimeError):
        await runtime.on_attribute(device_id, "2/144/4", 230000)

    async def invoke(call):
        return None

    app = build_app(store, invoke, runtime)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/resync")
    assert response.status_code == 502
    assert "Traceback" not in response.text
    store.close()
```

- [x] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/loxone/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.loxone.server'`

- [x] **Step 4: Write minimal implementation**

`src/loxmatter/loxone/server.py`:

```python
"""Receives the HTTP calls of the virtual outputs.

The Miniserver does not evaluate the response of a virtual output - it
sends and forgets. So the status codes here are not for Loxone, but for
the human who checks the log to see why a block is not doing anything.
Accordingly they must be distinguishable: 404 for an unknown key, 400 for
a value that does not fit, 502 for a device that is not responding.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException

from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.loxone.runtime import Runtime
from loxmatter.model.store import Store

Invoker = Callable[[MatterCall], Awaitable[None]]

logger = logging.getLogger(__name__)


def build_app(store: Store, invoke: Invoker, runtime: Runtime) -> FastAPI:
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/resync")
    async def resync() -> dict[str, int]:
        """Spec 6.4: hangs off the system-start block in the config project."""
        try:
            count = await runtime.resend_all()
        except Exception as exc:  # e.g. a UdpSender whose socket is already closed
            logger.exception("Full-Resend ueber /resync fehlgeschlagen")
            raise HTTPException(
                status_code=502, detail=f"Full-Resend fehlgeschlagen: {exc}"
            ) from exc
        return {"gesendet": count}

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
        except Exception as exc:  # noqa: BLE001 - every device problem becomes a 502
            raise HTTPException(status_code=502, detail=f"Geraet nicht erreichbar: {exc}") from exc

        return {"status": "ok", "key": key}

    return app
```

- [x] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/loxone/test_server.py -v`
Expected: PASS, 7 tests

- [x] **Step 6: Commit**

```bash
git add src/loxmatter/loxone/server.py tests/loxone/test_server.py pyproject.toml uv.lock
git commit -m "feat(loxone): HTTP-Endpoint fuer virtuelle Ausgaenge und resync"
```

---

### Task 7: System template

`bridge_alive` and `/resync` belong to no device and therefore need their own
template pair (Spec 6.2, 6.4, 6.5).

**Files:**
- Modify: `src/loxmatter/export/documents.py`
- Modify: `src/loxmatter/cli.py`
- Create: `tests/export/test_system_template.py`

**Interfaces:**
- Produces: `render_system_templates(bridge_ip: str, port: int) -> tuple[bytes, bytes]`, plus CLI flag `--system`

- [x] **Step 1: Write the failing test**

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
    """They belong to no device - a d<id>_ would be wrong."""
    viu, vo = render_system_templates("192.168.1.50", 7000)
    assert "d1_" not in text(viu)
    assert "d1_" not in text(vo)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_system_template.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_system_templates'`

- [x] **Step 3: Write minimal implementation**

Add to `src/loxmatter/export/documents.py`:

```python
def render_system_templates(bridge_ip: str, port: int) -> tuple[bytes, bytes]:
    """The two templates that belong to no device.

    bridge_alive is the watchdog (Spec 6.5): it toggles as long as the
    bridge is running, and covers "container dead" and "network gone" alike.

    /resync belongs, in the config project, on the system-start block
    (Spec 6.4). UDP is stateless - without this call, all inputs sit at
    their default value after a Miniserver restart, possibly for hours in
    the case of a temperature sensor.
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

Plus the flag in the `export` command. The system templates need no device, so
`--system` is allowed to run without `--node` and without `--fixture` — the way the
command is structured otherwise checks the source first:

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

And in the body, **before** loading the snapshot. Both `write_bytes` calls sit in
try/except like the three device-template write operations further below — an OSError
here (full disk, read-only volume in the future container deployment) must show no
traceback, same as there (Review-Fix Important #1, 2026-09-02).
`out.mkdir` no longer necessarily runs right at the start, but only here and once more
before the device templates (`_ensure_out_dir`, `mkdir(exist_ok=True)` tolerates the
second call) — a call with none of `--system`, `--node` or `--fixture` fails at
parameter validation in `_load_snapshot`, before any directory is created
(Review-Fix Minor #3, 2026-09-02):

```python
    if system:
        _ensure_out_dir(out)
        viu_sys, vo_sys = render_system_templates(bridge_ip, port)
        viu_sys_path = out / "VIU_Matter_System.xml"
        vo_sys_path = out / "VO_Matter_System.xml"
        try:
            viu_sys_path.write_bytes(viu_sys)
        except OSError as exc:
            _fail(
                f"{viu_sys_path} konnte nicht geschrieben werden: {exc}. "
                "Es wurde noch keine Datei angelegt."
            )
        try:
            vo_sys_path.write_bytes(vo_sys)
        except OSError as exc:
            _fail(
                f"{vo_sys_path} konnte nicht geschrieben werden: {exc}. "
                f"Geschrieben wurde bereits {viu_sys_path.name}, es fehlt {vo_sys_path.name}."
            )
        typer.echo("VIU_Matter_System.xml, VO_Matter_System.xml: Heartbeat und /resync")
        if fixture is None and node is None:
            return
```

This makes three kinds of invocation possible: only a device, only the system templates, or both.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_system_template.py -v`
Expected: PASS, 5 tests

Plus the regression tests from the review fix (2026-09-02): pinning the system
templates against the reference files in `test_reference.py` (2 tests) and the
safeguarding of both `write_bytes` calls plus the mkdir order in `test_export_cli.py`
(2 tests).

Run: `uv run pytest tests/export/test_reference.py tests/test_export_cli.py -v`
Expected: PASS, 15 tests in `test_reference.py`, 13 tests in `test_export_cli.py`

- [x] **Step 5: Commit**

```bash
git add src/loxmatter/export/documents.py src/loxmatter/cli.py tests/export/test_system_template.py
git commit -m "feat(export): Systemvorlage mit Heartbeat und resync"
```

---

### Task 8: `loxmatter run`, `fake-miniserver` and the through-connection

**Files:**
- Create: `src/loxmatter/devtools/__init__.py`
- Create: `src/loxmatter/devtools/fake_miniserver.py`
- Modify: `src/loxmatter/cli.py`
- Create: `tests/devtools/test_fake_miniserver.py`
- Modify: `deploy/testhost/docker-compose.yml`

**Interfaces:**
- Produces: CLI commands `loxmatter run` and `loxmatter fake-miniserver`
- `class FakeMiniserver` with `async def start()`, `async def stop()`,
  `received: list[tuple[str, str]]`, `malformed: list[bytes]`,
  `port: int` (property — with `port=0`, the port actually bound),
  `on_received: Callable[[str, str], None] | None`,
  `on_malformed: Callable[[bytes], None] | None` (both constructor kwargs, for
  `loxmatter fake-miniserver`'s real-time output with timestamp — see
  `_fake_miniserver` in `cli.py`), `announced_keys(template: Path) -> set[str]`,
  `silent_keys(template: Path) -> list[str]`
  **(Correction, Review-Fix I6, 2026-09-02: extended relative to the original
  design below by `malformed`, `on_received`, `on_malformed` and
  `announced_keys` — `port` is a property, not an attribute,
  see Step 3/6.)**

- [x] **Step 1: Write the failing test**

`tests/devtools/test_fake_miniserver.py`:

```python
import asyncio
import socket
from pathlib import Path

import pytest

from loxmatter.devtools.fake_miniserver import FakeMiniserver

REFERENZ = Path(__file__).parents[1] / "fixtures" / "loxone" / "VIU_reference.xml"


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
    """A datagram without a colon is a bug you want to see."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"kaputt", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert fake.malformed == [b"kaputt"]


async def test_silent_keys_names_signals_that_never_arrived():
    """The actual benefit: finding exported signals that never fire."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"d1_1_beispiel:1", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    stumm = fake.silent_keys(REFERENZ)
    await fake.stop()
    sock.close()
    assert "d1_1_beispiel" not in stumm
    assert stumm  # the reference carries more than one command


def test_silent_keys_reads_the_check_attribute():
    fake = FakeMiniserver(port=0)
    assert all(not k.endswith(":\\v") for k in fake.silent_keys(REFERENZ))
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/devtools -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.devtools'`

- [x] **Step 3: Write minimal implementation**

`src/loxmatter/devtools/fake_miniserver.py`:

```python
"""Replaces the Loxone Miniserver during development.

The third point below is the actual benefit: it compares which signals a
generated template announces with those that actually sent a datagram. An
exported signal that never fires is a mapping bug - and without this
comparison it only surfaces in Loxone, where it looks like a device bug.
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
        """Signals the template announces, but that never sent a datagram."""
        angekuendigt = set(_CHECK.findall(template.read_text(encoding="utf-8-sig")))
        gesehen = {key for key, _ in self.received}
        return sorted(angekuendigt - gesehen)
```

**Correction, Review-Fix I6 (2026-09-02):** the design above is the original state before implementation, left here for traceability of the plan, but no longer binding. What was actually shipped is an extended version — `on_received`/`on_malformed` as optional constructor callbacks (for `loxmatter fake-miniserver`'s real-time output, see `_fake_miniserver` below), `announced_keys()` split out separately from `silent_keys()` (for `_silent_keys_report`'s distinction between "nothing to check" and "everything seen", see Step 4/6), and named `_DatagramProtocol` instead of `_Protokoll`. The actual source code is in `src/loxmatter/devtools/fake_miniserver.py`:

```python
"""Replaces the Loxone Miniserver during development.

The third point below is the actual benefit: it compares which signals a
generated template announces with those that actually sent a datagram. An
exported signal that never fires is a mapping bug - and without this
comparison it only surfaces in Loxone, where it looks like a device bug.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

# Reads exactly the attribute that render_virtual_in_udp writes (see
# export/documents.py): Check="<key>:\v".
_CHECK = re.compile(r'Check="([^:"]+):\\v"')


class _DatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: FakeMiniserver) -> None:
        self._server = server

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        text = data.decode(errors="replace")
        key, sep, value = text.partition(":")
        if not sep:
            self._server.malformed.append(data)
            if self._server.on_malformed is not None:
                self._server.on_malformed(data)
            return
        self._server.received.append((key, value))
        if self._server.on_received is not None:
            self._server.on_received(key, value)


class FakeMiniserver:
    """Receives UDP datagrams like the real Miniserver - without it.

    `on_received`/`on_malformed` are meant for `loxmatter fake-miniserver`
    (real-time output with timestamp) - `received`/`malformed` remain the
    primary source queried in the test and always grow, regardless of
    whether a callback is set.
    """

    def __init__(
        self,
        port: int = 7000,
        host: str = "127.0.0.1",
        *,
        on_received: Callable[[str, str], None] | None = None,
        on_malformed: Callable[[bytes], None] | None = None,
    ) -> None:
        self._host, self._port = host, port
        self.received: list[tuple[str, str]] = []
        self.malformed: list[bytes] = []
        self.on_received = on_received
        self.on_malformed = on_malformed
        self._transport: asyncio.DatagramTransport | None = None

    @property
    def port(self) -> int:
        if self._transport is None:
            return self._port
        return int(self._transport.get_extra_info("sockname")[1])

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DatagramProtocol(self), local_addr=(self._host, self._port)
        )
        self._transport = transport

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def announced_keys(self, template: Path) -> set[str]:
        """Signals the template announces via the `Check` attribute.

        Kept separate from `silent_keys` so that a caller (see
        `loxmatter fake-miniserver`) can distinguish whether a template
        simply carries NO Check attribute (e.g. a VO_ file or an empty
        template) - then there is nothing to check - instead of confusing
        that with the case where all announced signals were seen.
        """
        return set(_CHECK.findall(template.read_text(encoding="utf-8-sig")))

    def silent_keys(self, template: Path) -> list[str]:
        """Signals the template announces, but that never sent a datagram."""
        seen = {key for key, _ in self.received}
        return sorted(self.announced_keys(template) - seen)
```

- [x] **Step 4: Write `loxmatter run`**

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
    """Connects Matter and Loxone permanently: values out, commands in."""
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
        # Without this call the bridge connects but never listens to
        # anything: subscribe() is what forwards attribute/event changes
        # and reachability to `runtime` in the first place (see below).
        # resolve_device_id maps the node ID onto the stable device_id
        # that the keys hang off of.
        await client.subscribe(store.device_id_for_node, runtime)
        await runtime.start()
        # Addendum, live run 2026-09-02 (Spec 6.4): seed from the current
        # device state FIRST, only THEN the full resend - otherwise it
        # would find an empty cache and send nothing. See Runtime.seed_from_snapshot.
        await runtime.seed_from_snapshot(await client.snapshots())
        # A restart of the bridge is meant to act like /resync (Spec 6.4).
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

**Correction, Review-Fix I6 (2026-09-02):** the design above is the original state before implementation, left here for traceability of the plan, but no longer binding. What was actually shipped is a significantly extended version — `run` resolves the store path itself, prints it out (Review-Fix M10, 2026-09-02), and opens the database SYNCHRONOUSLY before `asyncio.run(...)`, so that an unwritable path ends as a clear CLI error instead of as a traceback from inside `asyncio.run`; `_run` therefore takes the already-opened `store` as its first parameter (not `store_path`) and cleans up each of the four resources (runtime, sender, matter client, database) in `finally` inside its OWN try/except, so that an error cleaning up one resource does not drag down the others. The actual source code is in `src/loxmatter/cli.py`:

```python
@app.command()
def run(
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
    miniserver: str = typer.Option(..., help="IP des Miniservers"),
    port: int = typer.Option(7000, help="UDP-Port, auf dem der Miniserver lauscht"),
    listen: int = typer.Option(8080, help="Port für die HTTP-Kommandos aus Loxone"),
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help="Datenbank mit den Signalschlüsseln. Siehe --store-path bei `export`."
    ),
) -> None:
    """Connects Matter and Loxone permanently: values out, commands in.

    Opens the database here already, synchronously — an unwritable path
    is meant to end as a clear CLI error (as with `export`), not as a
    traceback from inside `asyncio.run`.
    """
    resolved_store_path = _resolve_store_path(store_path)
    # Printed out as with `export` (Review-Fix M10, 2026-09-02): the most
    # likely misconfiguration is an `export` database and a `run`
    # database that drift apart — exported with `--store-path`, started
    # without (or the other way around). Without this line that only
    # surfaces as a 404 in a log nobody reads, because `run` never used
    # to name the path it was using.
    typer.echo(f"Datenbank: {resolved_store_path.resolve()}")
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(
            f"Verzeichnis {resolved_store_path.parent} konnte nicht angelegt werden: {exc}. "
            "Ist der Pfad beschreibbar?"
        )
    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(f"Datenbank {resolved_store_path} konnte nicht geöffnet werden: {exc}")

    asyncio.run(_run(store, url, miniserver, port, listen))
```

```python
async def _run(store: Store, url: str, miniserver: str, port: int, listen: int) -> None:
    """Builds sender, runtime and client on top of `store` and keeps them running.

    `store` comes in already opened (see `run` above). `UdpSender`,
    `Runtime` and `_build_client` perform no I/O in their constructors
    that could fail — unlike `Store(...)` itself. So from here on, all
    four resources are guaranteed to exist by the time `finally` closes
    them: no leak from a failed constructor anywhere between `try` and
    the first `await`.

    Every cleanup step in `finally` sits in its own `try`/`except`: if one
    fails (e.g. `runtime.stop()`, because the last full resend was stuck
    mid-send-failure), the following ones must still run — otherwise,
    depending on where the failure happened, the UDP socket would stay
    open or the matter-server connection would hang. `asyncio.CancelledError`
    flows past all of this uncaught: a Ctrl-C is meant to propagate the
    cancellation, not be swallowed as a cleanup error.

    On the actual cancellation behavior: `uvicorn.Server.serve()` catches
    SIGINT/SIGTERM itself (`Server.capture_signals`) and returns cleanly on
    a first Ctrl-C instead of raising an exception — the `finally` block
    below runs in that case just as with any other regular end. `asyncio.run()`
    itself, since Python 3.11, additionally installs its own SIGINT handler
    that cancels the entire `_run` task on a Ctrl-C outside of `serve()`
    (e.g. during `client.connect()`) — that too reaches `finally` as a
    normal cancellation exception.
    """
    sender = UdpSender(miniserver, port)
    runtime = Runtime(store, sender)
    client = _build_client(url)

    async def invoke(call: MatterCall) -> None:
        await client.send_command(call)

    try:
        try:
            await client.connect()
        except CannotConnect:
            _fail(f"matter-server unter {url} nicht erreichbar — läuft der Dienst?")
        except MatterUnavailableError as exc:
            _fail(
                f"matter-server unter {url} hat sich verbunden, aber keine "
                f"Bereitschaft gemeldet: {exc}"
            )
        await client.subscribe(store.device_id_for_node, runtime)
        await runtime.start()
        # Load initial values from the current device state BEFORE the
        # resend below sends them out (Spec 6.4, live run of 2026-09-02):
        # without this `resend_all()` would find an empty cache, because a
        # value only lands there via a changing subscription - see
        # `Runtime.seed_from_snapshot`.
        await runtime.seed_from_snapshot(await client.snapshots())
        # A restart of the bridge is meant to act like /resync (Spec 6.4).
        await runtime.resend_all()

        config = uvicorn.Config(
            build_app(store, invoke, runtime), host="0.0.0.0", port=listen, log_level="info"
        )
        await uvicorn.Server(config).serve()
    finally:
        try:
            await runtime.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Laufzeit konnte beim Beenden nicht sauber gestoppt werden")
        try:
            await sender.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("UDP-Sender konnte beim Beenden nicht sauber geschlossen werden")
        try:
            await client.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Verbindung zu matter-server konnte beim Beenden nicht sauber getrennt werden"
            )
        store.close()
```

The connection to matter-server is still missing two things in `BridgeMatterClient`,
which belong to this task:

- **`subscribe(handler)`** — reports attribute and event changes. `python-matter-server`
  delivers them via `client.subscribe_events`; you have to map the node ID onto the
  store's `device_id`, because the keys hang off the `device_id`, not the node ID.
  A node ID can change, the `device_id` never — that is exactly why it exists.
- **`send_command(call)`** — executes a `MatterCall`, via
  `client.send_device_command(node_id, endpoint, cluster, command, payload)`.
- Reachability: `node.available` onto `Runtime.set_online(device_id, available)`.

Write tests for both against the existing fake upstream double in
`tests/matter/test_client.py`, not against a real server.

`_run()`'s own setup/teardown behavior (connection, subscription, start, full resend,
HTTP server, and teardown in every failure case — including an abort in the middle of
startup, BEFORE `serve()`, since exactly the call to `subscribe()` above sits there too)
belongs in its own tests against a `_FakeUpstream` double in `tests/test_cli.py`, not
against a real matter-server.

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 6 tests for `_run()`'s setup/teardown (1 of them for the abort during
startup, before `serve()`).

**Addendum, live run 2026-09-02:** a seventh test was added, which checks that `_run()`
calls `runtime.seed_from_snapshot(...)` with the current snapshots, and specifically
BEFORE the first `resend_all()` — making 7 tests for `_run()`'s setup/teardown in
`tests/test_cli.py`.

Plus the command for the test double:

```python
@app.command(name="fake-miniserver")
def fake_miniserver_cmd(
    port: int = typer.Option(7000, help="UDP-Port, auf dem gelauscht wird"),
    template: Path | None = typer.Option(  # noqa: B008
        None, help="Erzeugte VIU_-Vorlage: nennt am Ende die Signale, die nie feuerten"
    ),
) -> None:
    """Replaces the Miniserver: logs every datagram."""
    asyncio.run(_fake_miniserver(port, template))
```

It prints every datagram with a timestamp and, on Ctrl-C, provided `--template` is set,
the silent signals.

**Correction, Review-Fix I6 (2026-09-02):** the design above was missing two things that were actually shipped. First, `fake_miniserver_cmd` checks `--template` already BEFORE `asyncio.run(_fake_miniserver(...))`, not only afterward — otherwise the path is only read in `_fake_miniserver`'s `finally`, i.e. only AFTER waiting for Ctrl-C, and a wrong path would only surprise the user after the abort (Review-Fix Minor #5). Second, there is `_silent_keys_report` — a dedicated function for the closing message, which distinguishes THREE cases, not two: `announced` empty (the template carries no `Check` attribute at all, e.g. a VO_ file — nothing to check), `announced` not empty and `silent` empty (everything actually seen), and `announced`/`silent` both not empty (Review-Fix Minor #4). The actual source code is in `src/loxmatter/cli.py`:

```python
@app.command(name="fake-miniserver")
def fake_miniserver_cmd(
    port: int = typer.Option(7000, help="UDP-Port, auf dem gelauscht wird"),
    template: Path | None = typer.Option(  # noqa: B008
        None, help="Erzeugte VIU_-Vorlage: nennt am Ende die Signale, die nie feuerten"
    ),
) -> None:
    """Replaces the Miniserver: logs every datagram.

    `--template` is already checked here, instead of surprising the user
    with an error only after waiting for Ctrl-C (the path is only read in
    `_fake_miniserver`'s `finally`) — as with the other commands of this
    module, a wrong path is meant to end immediately as a CLI error
    (Review-Fix Minor #5).
    """
    if template is not None and not template.is_file():
        _fail(f"Vorlage {template} wurde nicht gefunden.")
    asyncio.run(_fake_miniserver(port, template))


def _silent_keys_report(template_name: str, announced: set[str], silent: list[str]) -> str:
    """Formulates the closing message of `fake-miniserver --template`.

    Three cases to distinguish: `announced` empty means the template
    carries no `Check` attribute at all (e.g. a VO_ file or an empty
    template) — then there is nothing to check, and that is different
    from "everything was seen". Only if `announced` is not empty and
    `silent` is empty was the check actually successful (Review-Fix Minor #4).
    """
    if not announced:
        return f"{template_name} enthält keine Check-Signale — nichts zu prüfen."
    if not silent:
        return (
            f"Alle {len(announced)} Signale aus {template_name} wurden mindestens einmal gesehen."
        )
    lines = [f"{len(silent)} Signale aus {template_name} nie gesehen:"]
    lines += [f"  {key}" for key in silent]
    return "\n".join(lines)


async def _fake_miniserver(port: int, template: Path | None) -> None:
    # datetime.now() without tz is deliberate here: this is local time for a
    # human watching the terminal - not a stored or compared time.
    def announce(key: str, value: str) -> None:
        typer.echo(f"{datetime.now():%H:%M:%S} {key} = {value}")  # noqa: DTZ005

    def announce_malformed(data: bytes) -> None:
        typer.echo(
            f"{datetime.now():%H:%M:%S} KAPUTT (kein Doppelpunkt): {data!r}",  # noqa: DTZ005
            err=True,
        )

    fake = FakeMiniserver(port=port, on_received=announce, on_malformed=announce_malformed)
    await fake.start()
    typer.echo(f"fake-miniserver lauscht auf UDP-Port {fake.port} — Strg-C zum Beenden")
    try:
        await asyncio.Event().wait()  # blocks until Ctrl-C cancels the task
    finally:
        await fake.stop()
        if template is not None:
            announced = fake.announced_keys(template)
            silent = fake.silent_keys(template)
            typer.echo(f"\n{_silent_keys_report(template.name, announced, silent)}")
```



- [x] **Step 5: Full check**

```bash
uv run pytest -v && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [x] **Step 6: Through-connection without a Miniserver**

```bash
uv run loxmatter fake-miniserver --port 7000 --template ./export/VIU_d1_*.xml
```

In a second session:

```bash
uv run loxmatter run --url ws://10.0.1.56:5580/ws --miniserver 127.0.0.1 --port 7000
```

Expected: datagrams from the plug appear; a press on the button produces a pulse
and a counter; `bridge_alive` toggles. At the end, `fake-miniserver` names the silent
signals — for a plug without a load that is many, and that is not a bug.

**Correction, live run 2026-09-02:** the sentence above applied to signals that never
change during the run — not to them never arriving at startup at all. The first run
showed exactly this mix-up: over 40 s only three datagrams arrived (heartbeat, one
HTTP-triggered switch command), none of the 109 exportable attribute signals, because
`resend_all()` found an empty cache at startup (see Spec 6.4 and Task 4). After the fix
here, all 109 signals of the plug appear right after the connection is established,
regardless of whether anything ever changes afterward — "silent" from now on may only
refer to signals that never get sent a second time after startup, not to a completely
missing first time.

- [ ] **Step 7: Through-connection with a real Miniserver — 🔴 THE ONLY STEP OF THE ENTIRE PHASE STILL OPEN (Review-Fix I6, 2026-09-02)**

> **Everything else in this plan is done and checked off.** This is the last
> unchecked checkbox among all ~50 in this document. It cannot be done by an
> agent — it needs a human with access to Loxone Config and the real
> Miniserver (see "This step needs a human..." below). Before this phase
> counts as complete, exactly this step must be made up — see also
> "Completion of the phase" at the very bottom of this document.

**This step needs a human with Loxone Config.** It is the purpose of the phase.

Generate and import templates (device and system), start `loxmatter run` against the
real Miniserver IP, and check in the Loxone visualization:

1. The plug's power appears and changes when you plug in a load.
2. A press on the button triggers the pulse input.
3. A virtual output on `d<id>_1_toggle` switches the plug.
4. `bridge_alive` toggles.
5. After a Miniserver restart, the system-start block refills all values immediately
   via `/resync`.

Whatever deviates goes into the spec — **not** into an adjustment of the tests.

- [x] **Step 8: Commit**

```bash
git add src/loxmatter/devtools src/loxmatter/cli.py tests/devtools deploy/
git commit -m "feat(cli): loxmatter run und fake-miniserver"
```

---

## Completion of the phase

The phase is done when:

1. [x] `uv run pytest` runs through without hardware and without network,
2. [x] the through-connection without a Miniserver (Task 8 Step 6) runs,
3. [ ] **the five points from Task 8 Step 7 are confirmed on real hardware —
   THE ONLY OPEN POINT (Review-Fix I6, 2026-09-02), see there**,
4. [x] deviations are recorded in the spec.

**Remains open:** the color-space conversion is checked against reference values, but
not against a light. This is the only part this phase cannot complete, and it belongs
as an open point in the roadmap, not in a silent forgetting.
