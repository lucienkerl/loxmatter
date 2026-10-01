# Lux and Kelvin Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Illuminance (1024/0) reaches Loxone in lux and colour temperature (768/7) in Kelvin, and the WebUI shows the raw device value beside every converted signal.

**Architecture:** A fixed registry of named conversion functions (`profiles/conversions.py`) is selected per attribute by a `convert:` key in `clusters.yaml` and applied in the one place raw values become Loxone values (`loxone/values.py`). `Runtime` additionally caches the raw value of every converted signal and hands it to the WebUI through `SignalOut.raw_value` and the live WebSocket's `raw` field. A store migration renames the default titles and units of already-registered signals.

**Tech Stack:** Python 3.12, PyYAML, SQLite (store), FastAPI + Pydantic, Alpine.js WebUI, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-01-lux-kelvin-conversion-design.md`

**Worktree (absolute, for every subagent):**
`/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/ikea-bewegungsmelder-illuminance-ea8c11`
Run every command from there. Never `cd` to the main checkout.

## Global Constraints

- Everything written in the repository is English - code, comments, docstrings, test names, commit messages. German only in `de:` values of `src/loxmatter/i18n/strings.yaml`.
- User-visible text goes through `strings.yaml` with an `en` and a `de` value and `t(...)` in JavaScript.
- Commit messages: Conventional Commits, English, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Existing signal keys are never renamed. New slugs: `illuminance_lux` (1024/0), `colortemp_kelvin` (768/7).
- New units: `lx`, `K`, both with 0 decimals in `_UNIT_DECIMALS`.
- 16395/16396 (physical colour temperature limits) stay in mired - do not touch them, nor `api/control.py` / `api/groups.py`.
- Store migrations are additive: update rows, never drop or rename columns.
- Comment density and style: match the surrounding code (this codebase explains *why* in docstrings and comments; a new function gets a docstring that says why it exists).
- Tests run in the foreground. The full suite takes more than ten minutes; never run it as one Bash call - see Task 6.

---

### Task 1: Lux and Kelvin reach Loxone

**Files:**
- Create: `src/loxmatter/profiles/conversions.py`
- Modify: `src/loxmatter/profiles/table.py` (`_table` at ~line 96, `_UNIT_DECIMALS` at ~line 356, `scale_factor` at ~line 423)
- Modify: `src/loxmatter/profiles/clusters.yaml` (768/7 at ~line 137-144, 1024/0 at ~line 279-287)
- Modify: `src/loxmatter/loxone/values.py:38-48`
- Create: `tests/profiles/test_conversions.py`
- Modify: `tests/profiles/test_table.py` (~line 470, ~line 500, new tests at the end)
- Modify: `tests/loxone/test_values.py` (new tests)
- Modify: `tests/model/test_store.py:422`, `tests/model/test_store_migration.py:1142`

**Interfaces:**
- Produces:
  - `loxmatter.profiles.conversions.CONVERSIONS: Mapping[str, Callable[[float], float | None]]`
  - `loxmatter.profiles.conversions.lux_from_log(value: float) -> float | None`
  - `loxmatter.profiles.conversions.kelvin_from_mired(value: float) -> float | None`
  - `loxmatter.profiles.table.converter(ref: SignalRef) -> Callable[[float], float | None] | None`
  - `loxmatter.profiles.table.is_converted(ref: SignalRef) -> bool`
  - `loxmatter.profiles.table.check_conversions(clusters: Mapping[int, Mapping[str, Any]]) -> None` (raises `ValueError`)

- [ ] **Step 1: Write the failing conversion tests**

Create `tests/profiles/test_conversions.py` (copy the GPL header from `tests/loxone/test_values.py` lines 1-15):

```python
import pytest

from loxmatter.profiles.conversions import CONVERSIONS, kelvin_from_mired, lux_from_log


def test_lux_from_log_inverts_the_zcl_encoding():
    """ZCL and Matter both encode 10000 * log10(lux) + 1."""
    assert lux_from_log(1) == pytest.approx(1.0)
    assert lux_from_log(10001) == pytest.approx(10.0)
    assert lux_from_log(20001) == pytest.approx(100.0)
    assert lux_from_log(30001) == pytest.approx(1000.0)


def test_zero_is_too_dark_to_measure_and_means_zero_lux():
    """Both specifications reserve 0 for "too low to be measured". The
    formula would give 0.99977 lx - a reading the sensor never made.

    Fault to prove it: drop the `value == 0` branch."""
    assert lux_from_log(0) == 0.0


def test_kelvin_from_mired_is_the_reciprocal():
    assert kelvin_from_mired(370) == pytest.approx(2702.7, abs=0.1)
    assert kelvin_from_mired(250) == pytest.approx(4000.0)


@pytest.mark.parametrize("mired", [0, -1])
def test_a_mired_outside_the_specified_range_is_not_sendable(mired):
    """1-65279 is the specified range. 0 would divide by zero.

    Fault to prove it: drop the `value <= 0` guard - this raises."""
    assert kelvin_from_mired(mired) is None


def test_the_registry_names_both_functions():
    assert CONVERSIONS["lux_from_log"] is lux_from_log
    assert CONVERSIONS["kelvin_from_mired"] is kelvin_from_mired
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/profiles/test_conversions.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'loxmatter.profiles.conversions'`

- [ ] **Step 3: Write `conversions.py`**

Create `src/loxmatter/profiles/conversions.py` (GPL header as in `profiles/table.py`):

```python
"""Conversions that `scale` cannot express (design 2026-10-01, section 3).

`scale` in `clusters.yaml` multiplies. Two values need more than that: the
ZCL/Matter illuminance encoding is logarithmic, and Kelvin is the
reciprocal of mired. An attribute entry names one of the functions below
with `convert:`; nothing from the YAML file is ever evaluated, it only
picks an entry of `CONVERSIONS`.

Each function takes the raw device number and returns the Loxone value, or
`None` for "not sendable" - the same meaning `loxone.values.to_loxone_value`
gives `None`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final


def lux_from_log(value: float) -> float | None:
    """`10 ** ((value - 1) / 10000)`, the inverse of the encoding that ZCL
    and Matter share for `IlluminanceMeasurement.MeasuredValue`.

    0 means "too low to be measured" in both specifications and becomes
    exactly 0 lx; the formula would give 0.99977. The invalid marker
    (Matter `null`, ZCL 0xFFFF) never arrives here: `classify` rejects
    `None`, and the Zigbee edge suppresses 0xFFFF
    (`zigbee.translate`)."""
    if value == 0:
        return 0.0
    return float(10 ** ((value - 1) / 10000))


def kelvin_from_mired(value: float) -> float | None:
    """`1e6 / value`. Mired outside the specified 1-65279 cannot be a
    colour temperature; 0 would divide by zero. Such a value is not sent
    rather than replaced by an invented one."""
    if value <= 0:
        return None
    return 1_000_000 / value


CONVERSIONS: Final[Mapping[str, Callable[[float], float | None]]] = {
    "lux_from_log": lux_from_log,
    "kelvin_from_mired": kelvin_from_mired,
}
```

- [ ] **Step 4: Run the conversion tests**

Run: `uv run pytest tests/profiles/test_conversions.py -v`
Expected: 6 passed

- [ ] **Step 5: Write the failing table and value tests**

In `tests/profiles/test_table.py`, extend the import block from `loxmatter.profiles.table` (~line 24) with `check_conversions`, `converter`, `is_converted`, and add at the end of the file:

```python
def test_a_convert_entry_names_an_existing_function():
    """Every `convert:` in `clusters.yaml` must name an entry of
    `CONVERSIONS` - a typo fails here, not on a running bridge.

    Fault to prove it: rename `lux_from_log` in the YAML."""
    check_conversions(table._table())


def test_scale_and_convert_together_are_rejected():
    clusters = {1024: {"attributes": {0: {"slug": "x", "scale": 2, "convert": "lux_from_log"}}}}
    with pytest.raises(ValueError, match="1024/0"):
        check_conversions(clusters)


def test_an_unknown_conversion_is_rejected():
    clusters = {1024: {"attributes": {0: {"slug": "x", "convert": "lux_from_luck"}}}}
    with pytest.raises(ValueError, match="lux_from_luck"):
        check_conversions(clusters)


def test_illuminance_and_colour_temperature_carry_their_conversion():
    assert converter(SignalRef(1, 1024, 0, SignalKind.ATTRIBUTE)) is not None
    assert converter(SignalRef(1, 768, 7, SignalKind.ATTRIBUTE)) is not None
    assert converter(SignalRef(1, 8, 0, SignalKind.ATTRIBUTE)) is None


@pytest.mark.parametrize(
    ("cluster_id", "element_id", "expected"),
    [
        (1024, 0, True),  # convert
        (768, 7, True),  # convert
        (8, 0, True),  # scale 100/254
        (47, 12, True),  # scale 0.5
        (6, 0, False),  # onoff, plain
        (768, 16395, False),  # physical limit, stays raw mired
        (64999, 7, False),  # unknown cluster
    ],
)
def test_is_converted_follows_scale_and_convert(cluster_id, element_id, expected):
    """One rule for the WebUI's raw value line (design 2026-10-01,
    decision 2): what the bridge sends differs from what the device
    reported."""
    assert is_converted(SignalRef(1, cluster_id, element_id, SignalKind.ATTRIBUTE)) is expected


def test_lux_and_kelvin_are_whole_numbers_in_loxone():
    assert unit_format("lx") == "<v.0> lx"
    assert unit_format("K") == "<v.0> K"
```

If `unit_format` is not yet imported in that file, add it to the same import block.

Change the existing assertion at ~line 470:

```python
    assert lookup(SignalRef(1, 1024, 0, SignalKind.ATTRIBUTE), 5000).slug == "illuminance_lux"
```

and the comment at ~line 500 to `(768, 7),  # colortemp_kelvin`.

In `tests/loxone/test_values.py`, add after `test_level_is_scaled_from_254_to_percent`:

```python
def test_illuminance_reaches_loxone_in_lux():
    assert to_loxone_value(attr(1024, 0), 20001) == pytest.approx(100.0)


def test_illuminance_too_dark_to_measure_is_zero_lux():
    assert to_loxone_value(attr(1024, 0), 0) == 0.0


def test_colour_temperature_reaches_loxone_in_kelvin():
    """The output (768, 10) has always taken Kelvin; the feedback input now
    speaks the same unit."""
    assert to_loxone_value(attr(768, 7), 250) == pytest.approx(4000.0)


def test_a_colour_temperature_of_zero_mired_is_not_sent():
    assert to_loxone_value(attr(768, 7), 0) is None


def test_the_physical_colour_temperature_limits_stay_in_mired():
    """Design 2026-10-01, section 3.5."""
    assert to_loxone_value(attr(768, 16395), 153) == pytest.approx(153.0)
```

In `tests/model/test_store.py:422` replace `"d1_1_colortemp_mireds"` with `"d1_1_colortemp_kelvin"`. In `tests/model/test_store_migration.py:1142` replace `"colortemp_mireds",` with `"colortemp_kelvin",` - both register the fixture lamp freshly, so they get the new slug.

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/profiles/test_table.py tests/loxone/test_values.py -v`
Expected: ImportError for `check_conversions` (collection error of `test_table.py`); in `test_values.py` the lux/Kelvin tests FAIL with raw numbers (e.g. `20001.0 != 100.0`).

- [ ] **Step 7: Implement table support**

In `src/loxmatter/profiles/table.py`:

Add the import after `from loxmatter.profiles.catalog import element_name`:

```python
from loxmatter.profiles.conversions import CONVERSIONS
```

and `from collections.abc import Callable, Mapping` to the stdlib imports.

Replace `_table`:

```python
@functools.cache
def _table() -> dict[int, dict[str, Any]]:
    raw = yaml.safe_load(_TABLE_PATH.read_text(encoding="utf-8"))
    clusters = {int(k): v for k, v in (raw.get("clusters") or {}).items()}
    check_conversions(clusters)
    return clusters


def check_conversions(clusters: Mapping[int, Mapping[str, Any]]) -> None:
    """Rejects a `convert:` that names no function in `CONVERSIONS`, and an
    entry with both `scale` and `convert` - which of the two would apply
    is not obvious, so neither is guessed (design 2026-10-01, 3.1).

    Runs when the table is first loaded, so a broken entry stops the
    bridge at startup and a test, not a value on its way to Loxone."""
    for cluster_id, cluster in clusters.items():
        for element_id, entry in (cluster.get("attributes") or {}).items():
            name = entry.get("convert")
            if name is None:
                continue
            if "scale" in entry:
                raise ValueError(f"{cluster_id}/{element_id}: `scale` and `convert` together")
            if name not in CONVERSIONS:
                raise ValueError(f"{cluster_id}/{element_id}: unknown conversion {name!r}")
```

In `_UNIT_DECIMALS`, after `"mired": 0,` add:

```python
    # Kelvin and lux, converted since 2026-10-01: one Kelvin or one lux is
    # below what anyone can see, so whole numbers.
    "K": 0,
    "lx": 0,
```

Update the comment line above `"°": 0,` to read `# Hue in degrees, Kelvin, lux and the mired limits are meaningfully integers.` and drop the separate Kelvin/lux comment if it then repeats it.

After `scale_factor`, add:

```python
def converter(ref: SignalRef) -> Callable[[float], float | None] | None:
    """The named conversion of an attribute (`convert:` in
    `clusters.yaml`), or None if it has none. `check_conversions` has
    already ensured the name exists."""
    cluster = _table().get(ref.cluster_id, {})
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry or "convert" not in entry:
        return None
    return CONVERSIONS[entry["convert"]]


def is_converted(ref: SignalRef) -> bool:
    """Whether the value Loxone receives differs from the device's raw
    value - a `convert`, or a `scale` other than 1. The WebUI shows the
    raw value for exactly these signals (design 2026-10-01, decision 2);
    this is the one place that rule lives."""
    return converter(ref) is not None or scale_factor(ref) != 1.0
```

- [ ] **Step 8: Apply the conversion in `to_loxone_value`**

In `src/loxmatter/loxone/values.py`, change the import line to

```python
from loxmatter.profiles.table import (
    Exportability,
    classify,
    converter,
    scale_factor,
    struct_member,
)
```

and the end of `to_loxone_value` to:

```python
    assert isinstance(raw, (int, float))
    convert = converter(ref)
    if convert is not None:
        return convert(float(raw))
    return float(raw) * scale_factor(ref)
```

- [ ] **Step 9: Change the two YAML entries**

In `src/loxmatter/profiles/clusters.yaml`, replace the comment block and entry for 768/7 (~lines 137-144, from `# Color temperature (ColorTemperatureMireds, 7):` through the `7: {...}` line) with:

```yaml
      # Color temperature (ColorTemperatureMireds, 7): Matter reports
      # mired, Loxone and the output (768, 10) speak Kelvin. Kelvin =
      # 1e6 / mired is a reciprocal, so `convert`, not `scale` (design
      # 2026-10-01). The slug says Kelvin; a signal registered before that
      # keeps its key `..._colortemp_mireds` and carries Kelvin as well.
      7: {slug: colortemp_kelvin, unit: K, feedback: true, convert: kelvin_from_mired}
```

In the comment above 16395/16396, replace the sentence `Like 7 above, they stay in mired - Kelvin = 1e6 / mired is a reciprocal that `scale` cannot express.` with `They stay in mired: converting them would swap minimum and maximum, and the API already converts them for the slider (design 2026-10-01, section 3.5).` - keep `The API converts them for the UI (see api/control.py).` only if it does not repeat that.

Replace the 1024 attribute block (~lines 282-287):

```yaml
    attributes:
      # ZCL and Matter both encode 10000*log10(lux)+1. Turning that back
      # into lux is a power, so `convert`, not `scale` (design 2026-10-01).
      # A signal registered before that keeps its key `..._illuminance`
      # and carries lux as well.
      0: {slug: illuminance_lux, unit: lx, convert: lux_from_log}
```

- [ ] **Step 10: Run the tests of this task**

Run: `uv run pytest tests/profiles tests/loxone/test_values.py tests/loxone/test_values_real_device.py tests/model/test_store.py tests/model/test_store_migration.py -q`
Expected: all pass. If a test elsewhere in these files still expects `mired` for 768/7 or the slug `illuminance`, it is asserting the old behaviour - update it to the new one and say so in the report; do not change the code back.

- [ ] **Step 11: Find other readers of the old slugs**

Run: `grep -rn "colortemp_mireds\|_illuminance\b\|\"illuminance\"" src tests | grep -v phys_m`
Expected: only the clusters.yaml comments that mention the old key on purpose. Anything else: update it and mention it in the report.

- [ ] **Step 12: Lint, types, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: clean.

```bash
git add src/loxmatter/profiles/conversions.py src/loxmatter/profiles/table.py src/loxmatter/profiles/clusters.yaml src/loxmatter/loxone/values.py tests/profiles/test_conversions.py tests/profiles/test_table.py tests/loxone/test_values.py tests/model/test_store.py tests/model/test_store_migration.py
git commit -m "feat(profiles): send illuminance in lux and colour temperature in Kelvin

A named conversion in clusters.yaml covers what a scale factor cannot:
the logarithmic illuminance encoding and the mired reciprocal.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Existing signals get the new title and unit

**Files:**
- Modify: `src/loxmatter/model/store.py` (`_SCHEMA_VERSION` at line 180, new `_migrate_to_v16` after `_migrate_to_v15` at ~line 985, `_MIGRATIONS` at ~line 999)
- Modify: `tests/model/test_store_migration.py` (every `== 15` version assertion; new tests at the end)

**Interfaces:**
- Consumes: nothing from Task 1 at runtime (the migration freezes its own values).
- Produces: schema version 16.

Note: another branch may also claim version 16. If `origin/main` has a `_migrate_to_v16` by the time this merges, renumber this migration to the next free number.

- [ ] **Step 1: Write the failing migration tests**

Append to `tests/model/test_store_migration.py`:

```python
def _open_at_v15_with_old_titles(path: Path) -> tuple[int, int]:
    """A version-15 database as the previous release left it: the fixture
    lamp's colour temperature and a Zigbee motion sensor's illuminance
    registered with the titles and units of that release, one of them
    renamed by the user. Returns `(lamp_id, sensor_id)`."""
    store = Store(path)
    lamp = load("ikea_kajplats_cws_lamp.json")
    lamp_id = store.register_device(lamp)
    store.register_signals(lamp_id, lamp)
    sensor = NodeSnapshot(
        technology="zigbee",
        address="d0:cf:5e:ff:fe:71:a3:20",
        vendor_name="IKEA of Sweden",
        product_name="VALLHORN Wireless Motion Sensor",
        unique_id="d0:cf:5e:ff:fe:71:a3:20",
        attributes={
            "0/29/0": [{"0": 0x0016, "1": 1}],
            "1/29/0": [{"0": 0x0107, "1": 1}],
            "1/1030/0": 1,
            "1/1024/0": 23000,
        },
    )
    sensor_id = store.register_device(sensor)
    store.register_signals(sensor_id, sensor)
    store.mark_exported(lamp_id)
    store.mark_exported(sensor_id)
    store.close()
    db = sqlite3.connect(str(path))
    db.execute(
        "UPDATE signal SET title = 'colortemp_mireds', unit = 'mired'"
        " WHERE cluster_id = 768 AND element_id = 7"
    )
    db.execute(
        "UPDATE signal SET title = 'Brightness hall', unit = ''"
        " WHERE cluster_id = 1024 AND element_id = 0"
    )
    db.execute("PRAGMA user_version = 15")
    db.commit()
    db.close()
    return lamp_id, sensor_id


def _row(path: Path, cluster_id: int, element_id: int) -> tuple[str, str]:
    db = sqlite3.connect(str(path))
    try:
        title, unit = db.execute(
            "SELECT title, unit FROM signal WHERE cluster_id = ? AND element_id = ?",
            (cluster_id, element_id),
        ).fetchone()
        return title, unit
    finally:
        db.close()


def test_migration_to_v16_renames_the_default_title_and_unit(tmp_path):
    """A colour temperature signal registered before 2026-10-01 still read
    `colortemp_mireds` / `mired` next to a value that is Kelvin now.

    Fault to prove it: drop the migration's UPDATE - this fails."""
    path = tmp_path / "v15.sqlite"
    _open_at_v15_with_old_titles(path)

    Store(path).close()

    assert user_version(path) == 16
    assert _row(path, 768, 7) == ("colortemp_kelvin", "K")


def test_migration_to_v16_keeps_a_title_the_user_chose(tmp_path):
    """`title` belongs to the user once set (`set_title`). Only the unit,
    which is never the user's, moves.

    Fault to prove it: drop `AND title = ?` from the title UPDATE."""
    path = tmp_path / "v15.sqlite"
    _open_at_v15_with_old_titles(path)

    Store(path).close()

    assert _row(path, 1024, 0) == ("Brightness hall", "lx")


def test_migration_to_v16_marks_both_devices_changed(tmp_path):
    """Title and unit go into the export and the project sync, so a device
    whose rows this migration touched reads "changed since export" - the
    same rule as `_migrate_to_v12`.

    Fault to prove it: drop the `updated_at` stamp - this fails."""
    path = tmp_path / "v15.sqlite"
    lamp_id, sensor_id = _open_at_v15_with_old_titles(path)

    store = Store(path)
    try:
        for device_id in (lamp_id, sensor_id):
            device = store.device(device_id)
            assert changed_since_export(device.exported_at, device.updated_at), device_id
    finally:
        store.close()
```

Then replace every `user_version(path) == 15` in this file with `user_version(path) == 16`, and the `fetchone()[0] == 15` at ~line 942 with `== 16`:

```bash
sed -i '' 's/user_version(path) == 15/user_version(path) == 16/; s/fetchone()\[0\] == 15$/fetchone()[0] == 16/' tests/model/test_store_migration.py
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/model/test_store_migration.py -q -k "v16 or user_version or v14"`
Expected: the three new tests and the version assertions FAIL (version is 15, title unchanged).

If `register_signals` for the motion-sensor snapshot does not create a 1024/0 signal (for instance because the Zigbee path needs more attributes), compare with `_open_at_v13_with_zigbee_management_signals` (~line 1263) and add what is missing; the test needs exactly one 768/7 row and one 1024/0 row in the database.

- [ ] **Step 3: Write the migration**

In `src/loxmatter/model/store.py` set `_SCHEMA_VERSION = 16`. After `_migrate_to_v15` add:

```python
# What `register_signals` named and measured these two signals before they
# were converted (design 2026-10-01, section 4): (cluster, element, old
# title, new title, new unit). Frozen here, like
# `_ZIGBEE_MANAGEMENT_CLUSTERS_V14`, rather than read from `clusters.yaml`,
# whose values a later change may move again.
_CONVERTED_SIGNALS_V16: tuple[tuple[int, int, str, str, str], ...] = (
    (768, 7, "colortemp_mireds", "colortemp_kelvin", "K"),
    (1024, 0, "illuminance", "illuminance_lux", "lx"),
)


def _migrate_to_v16(db: sqlite3.Connection) -> None:
    """Colour temperature and illuminance reach Loxone in Kelvin and lux
    since 2026-10-01; their keys stay (the Loxone input recognises the
    value by it), but a stored row still carried the old unit and, unless
    the user renamed it, the old default title.

    The unit moves unconditionally - it is never the user's. The title only
    where it still equals the old default: a title the user chose stays
    theirs (`set_title`). `register_signals` would move the unit too, but
    only for a device that reports in; the migration also reaches one that
    is offline.

    Stamps `device.updated_at` for every device it touches, BEFORE the
    update, because title and unit go into the export and the project sync
    - the same reasoning as `_migrate_to_v12`.

    Additive: rows only, no column. A rolled-back image shows the new title
    over a mired value until it re-registers the unit - harmless."""
    for cluster_id, element_id, old_title, new_title, new_unit in _CONVERTED_SIGNALS_V16:
        match = "kind = 'attribute' AND cluster_id = ? AND element_id = ?"
        db.execute(
            "UPDATE device SET updated_at = ? WHERE id IN ("
            f"SELECT device_id FROM signal WHERE {match}"
            " AND (unit != ? OR title = ?))",
            (now_iso(), cluster_id, element_id, new_unit, old_title),
        )
        db.execute(
            f"UPDATE signal SET title = ? WHERE {match} AND title = ?",
            (new_title, cluster_id, element_id, old_title),
        )
        db.execute(
            f"UPDATE signal SET unit = ? WHERE {match}",
            (new_unit, cluster_id, element_id),
        )
```

Add `16: _migrate_to_v16,` to `_MIGRATIONS` after `15: _migrate_to_v15,`.

- [ ] **Step 4: Run the migration tests**

Run: `uv run pytest tests/model/test_store_migration.py -q`
Expected: all pass.

- [ ] **Step 5: Other places that pin the schema version**

Run: `grep -rn "schema rises\|== 15\b" tests CHANGELOG.md | grep -iv "channel\|otbr"`
Expected: `CHANGELOG.md` says "rises from 14 to 15" - change it to "rises from 14 to 16". Any test that pins 15 as the current schema: bump it and mention it in the report.

- [ ] **Step 6: Lint, types, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`

```bash
git add src/loxmatter/model/store.py tests/model/test_store_migration.py CHANGELOG.md
git commit -m "feat(store): give converted signals their Kelvin and lux title and unit

Keys stay so Loxone keeps recognising the inputs; a title the user chose
is left alone.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The runtime keeps the raw value and streams it

**Files:**
- Modify: `src/loxmatter/loxone/runtime.py` (`__init__` ~line 116 and ~line 146, `add_observer`/`remove_observer`/`_notify_observers` ~lines 148-195, `_cache_attribute` ~line 236, `on_attribute` ~line 256, new `last_raw_values_for` after `last_values_for` ~line 495)
- Modify: `src/loxmatter/api/live.py` (`Observer` alias ~line 69, `observer` ~line 93)
- Modify: `tests/loxone/test_runtime.py` (~line 869), `tests/api/test_live.py` (~lines 38, 60, 70, 130)

**Interfaces:**
- Consumes: `loxmatter.profiles.table.is_converted(ref) -> bool`, `struct_member(ref, raw) -> object` (Task 1).
- Produces:
  - `Runtime.last_raw_values_for(device_id: int) -> dict[str, int | float]`
  - Observer callables: `Callable[[str, object, object], None]` called as `observer(key, value, raw)`; `raw` is `None` except for an attribute of a converted signal.
  - Live WebSocket message: `{"key": str, "value": ..., "raw": int | float | None}`

- [ ] **Step 1: Write the failing runtime tests**

In `tests/loxone/test_runtime.py`, add after the `environment` fixture (~line 99-122; it shows the loading pattern this file uses):

```python
def _lamp_snapshot() -> NodeSnapshot:
    raw = json.loads((FIXTURES / "ikea_kajplats_cws_lamp.json").read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def lamp_store(tmp_path):
    """The colour lamp, whose 768/7 is converted (mired to Kelvin) and
    whose 6/0 is not. Returns `(store, device_id)`."""
    store = Store(tmp_path / "lamp.sqlite")
    snap = _lamp_snapshot()
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    yield store, device_id
    store.close()
```

Then add:

```python
async def test_the_raw_value_of_a_converted_signal_is_kept(lamp_store):
    """The WebUI shows the device's own number beside the converted one
    (design 2026-10-01, section 5.1)."""
    store, device_id = lamp_store
    runtime = Runtime(store, FakeSender())
    await runtime.on_attribute(device_id, "1/768/7", 250)

    key = f"d{device_id}_1_colortemp_kelvin"
    assert runtime.last_values_for(device_id)[key] == pytest.approx(4000.0)
    assert runtime.last_raw_values_for(device_id) == {key: 250}


async def test_an_unconverted_signal_keeps_no_raw_value(lamp_store):
    """On/off arrives unchanged - a second copy of it would be noise.

    Fault to prove it: drop the `is_converted` check - this fails."""
    store, device_id = lamp_store
    runtime = Runtime(store, FakeSender())
    await runtime.on_attribute(device_id, "1/6/0", True)

    assert runtime.last_raw_values_for(device_id) == {}


async def test_the_observer_receives_the_raw_value(lamp_store):
    store, device_id = lamp_store
    seen: list[tuple[str, object, object]] = []
    runtime = Runtime(store, FakeSender())
    runtime.add_observer(lambda key, value, raw: seen.append((key, value, raw)))

    await runtime.on_attribute(device_id, "1/768/7", 250)
    await runtime.on_attribute(device_id, "1/6/0", True)

    assert seen == [
        (f"d{device_id}_1_colortemp_kelvin", pytest.approx(4000.0), 250),
        (f"d{device_id}_1_onoff", True, None),
    ]


async def test_seeding_keeps_the_raw_value_too(lamp_store):
    """`seed_from_snapshot` runs through `_cache_attribute` like a live
    update; the raw value must not be missing until the first change."""
    store, device_id = lamp_store
    runtime = Runtime(store, FakeSender())
    await runtime.seed_from_snapshot([_lamp_snapshot()])

    # The fixture lamp reports 370 mired (tests/fixtures/nodes/
    # ikea_kajplats_cws_lamp.json, "1/768/7").
    assert runtime.last_raw_values_for(device_id)[f"d{device_id}_1_colortemp_kelvin"] == 370
```

If `seed_from_snapshot` matches snapshots to devices in a way this test does not satisfy (read its docstring), follow what the existing seed tests in this file do and say so in the report.

Change the existing observer lambda at ~line 869 to `lambda key, value, raw: seen.append((key, value))`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/loxone/test_runtime.py -q`
Expected: the four new tests fail (`AttributeError: ... last_raw_values_for`, or a `TypeError` from the three-argument lambda).

- [ ] **Step 3: Implement it in `Runtime`**

In `src/loxmatter/loxone/runtime.py`:

Import `is_converted` and `struct_member` from `loxmatter.profiles.table`.

Next to `self._last_values` (~line 116):

```python
        # The device's own number for every CONVERTED signal, before
        # `scale` or `convert` (design 2026-10-01, section 5.1) - only for
        # the WebUI, never sent. Unconverted signals have nothing here:
        # their raw value IS `_last_values`.
        self._last_raw: dict[str, int | float] = {}
```

Change the observer type in `__init__`, `add_observer` and `remove_observer` from `Callable[[str, object], None]` to `Callable[[str, object, object], None]`, and add to `add_observer`'s docstring: `The third argument is the raw device value of a converted signal (see `_last_raw`), otherwise None.`

`_notify_observers`:

```python
    def _notify_observers(self, key: str, value: object, raw: object = None) -> None:
```

and inside the loop `observer(key, value, raw)`.

In `_cache_attribute`, after `self._last_values[signal.key] = value`:

```python
        if is_converted(signal.ref):
            member = struct_member(signal.ref, raw)
            if isinstance(member, (int, float)) and not isinstance(member, bool):
                self._last_raw[signal.key] = member
```

In `on_attribute`, change the notify line to:

```python
        self._notify_observers(key, value, self._last_raw.get(key))
```

All other `_notify_observers(...)` calls stay as they are (raw defaults to None).

After `last_values_for`:

```python
    def last_raw_values_for(self, device_id: int) -> dict[str, int | float]:
        """The raw device values of a device's converted signals, by key -
        the companion of `last_values_for` for the WebUI's raw value line
        (design 2026-10-01, section 5.2). Same prefix rule as there."""
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._last_raw.items() if k.startswith(prefix)}
```

- [ ] **Step 4: Stream it over the live socket**

In `src/loxmatter/api/live.py`:

```python
Observer = Callable[[str, object, object], None]
```

and

```python
        def observer(key: str, value: object, raw: object) -> None:
            queue.put({"key": key, "value": value, "raw": raw})
```

Update the module docstring sentence that says the socket carries "literally the same key/value" to mention that it also carries the raw value of a converted signal.

In `tests/api/test_live.py`, give every observer lambda/function a third parameter `raw` (~lines 38, 60, 70, 130), keeping the assertions as they are. Then add, after `test_websocket_delivers_a_value` (~line 76):

```python
async def test_the_live_message_carries_the_raw_value(api_with_runtime):
    """Design 2026-10-01, section 5.2: voltage is scaled (mV to V), so the
    message carries the raw millivolts as well.

    Fault to prove it: drop `"raw": raw` from the queued dict in
    `api/live.py`."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live") as ws:
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(ws.receive_json(), timeout=2)
    assert message["key"] == f"d{device_id}_2_voltage"
    assert message["raw"] == 230000
```

If `tests/api/test_live_smoke.py` compares a whole received message with a `{"key": ..., "value": ...}` dict, extend the expected dict with `"raw"` and say so in the report.

- [ ] **Step 5: Run the tests of this task**

Run: `uv run pytest tests/loxone/test_runtime.py tests/api/test_live.py tests/api/test_live_smoke.py -q`
Expected: all pass.

Run: `grep -rn "add_observer(lambda key, value)" tests src`
Expected: no output.

- [ ] **Step 6: Lint, types, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`

```bash
git add src/loxmatter/loxone/runtime.py src/loxmatter/api/live.py tests/loxone/test_runtime.py tests/api/test_live.py tests/api/test_live_smoke.py
git commit -m "feat(runtime): keep the raw value of converted signals for the WebUI

The live socket carries it beside the converted value; Loxone still only
gets the converted one.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The signal API reports `converted` and `raw_value`

**Files:**
- Modify: `src/loxmatter/api/models.py` (`SignalOut`, ~line 52-75)
- Modify: `src/loxmatter/api/devices.py` (`RuntimeValues` ~line 162, `_signal_out` ~line 179, callers ~lines 374-378 and ~480-482)
- Modify: `tests/api/conftest.py` (`FakeRuntime` ~line 212), `tests/api/test_zigbee_api.py` (~line 256)
- Modify: `tests/api/test_devices.py` (new tests)

**Interfaces:**
- Consumes: `is_converted(ref)` (Task 1), `Runtime.last_raw_values_for(device_id) -> dict[str, int | float]` (Task 3).
- Produces: JSON fields `converted: bool` and `raw_value: int | float | null` on every signal of `GET /api/devices/{id}/signals` and `PATCH /api/signals/{key}` (whatever path the existing patch route uses).

- [ ] **Step 1: Write the failing API tests**

The two runtime doubles must satisfy the widened `RuntimeValues` protocol. Add to `FakeRuntime` in `tests/api/conftest.py`, after `last_values_for` (~line 212):

```python
    def last_raw_values_for(self, device_id: int) -> dict[str, int | float]:
        """No converted value has arrived at this double; tests of the raw
        value use `api_with_runtime`, which has the real `Runtime`."""
        return {}
```

and the same method, returning `{}`, to the double in `tests/api/test_zigbee_api.py` (~line 256).

Add to `tests/api/test_devices.py` (the `api_with_runtime` fixture comes from `tests/api/conftest.py`: a real `Runtime` with the IKEA plug, yielding `(client, runtime, device_id)`; import `pytest` if the file does not yet):

```python
async def test_a_converted_signal_reports_its_raw_value(api_with_runtime):
    """Design 2026-10-01, section 5.2: voltage is scaled (mV to V).

    Fault to prove it: pass `{}` instead of `raw_values` in `get_signals`."""
    client, runtime, device_id = api_with_runtime
    await runtime.on_attribute(device_id, "2/144/4", 230000)

    response = await client.get(f"/api/devices/{device_id}/signals")
    signal = {s["key"]: s for s in response.json()}[f"d{device_id}_2_voltage"]

    assert signal["value"] == pytest.approx(230.0)
    assert signal["converted"] is True
    assert signal["raw_value"] == 230000


async def test_an_unconverted_signal_reports_no_raw_value(api_with_runtime):
    """On/off arrives unchanged; `converted` is false and there is no raw
    value to show."""
    client, runtime, device_id = api_with_runtime
    await runtime.on_attribute(device_id, "1/6/0", True)

    response = await client.get(f"/api/devices/{device_id}/signals")
    signal = {s["key"]: s for s in response.json()}[f"d{device_id}_1_onoff"]

    assert signal["converted"] is False
    assert signal["raw_value"] is None
```

If the plug's on/off key is not `d{device_id}_1_onoff` (check `store.signals(device_id)`), use the one it has.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_devices.py -q -k raw_value`
Expected: FAIL with `KeyError: 'converted'` (or a 500 from the missing `last_raw_values_for` before Task 4's code exists).

- [ ] **Step 3: Implement**

In `src/loxmatter/api/models.py`, add after `resend: bool` in `SignalOut`:

```python
    # Design 2026-10-01, section 5.2: whether `value` is a conversion of
    # the device's number (`profiles.table.is_converted`), and that number
    # itself - None for an unconverted signal, and for a converted one
    # whose value has not arrived yet.
    converted: bool
    raw_value: int | float | None
```

and mention both fields in the class docstring with one sentence.

In `src/loxmatter/api/devices.py`:

- Add to `RuntimeValues`: `def last_raw_values_for(self, device_id: int) -> dict[str, int | float]: ...`
- Import `is_converted` from `loxmatter.profiles.table`.
- `_signal_out` gains a parameter `raw_values: dict[str, int | float]` after `values`, and builds:

```python
    converted = is_converted(signal.ref)
```

passing `converted=converted, raw_value=raw_values.get(signal.key) if converted else None` to `SignalOut`.

- In `get_signals`: `raw_values = runtime.last_raw_values_for(device_id)` and pass it.
- In the patch handler (~line 480): `raw_values = runtime.last_raw_values_for(updated.device_id)` and pass it.

Run: `grep -rn "_signal_out(" src` - every call must pass `raw_values`.

- [ ] **Step 4: Run the API tests**

Run: `uv run pytest tests/api/test_devices.py tests/api/test_zigbee_api.py tests/api/test_live.py -q`
Expected: all pass.

- [ ] **Step 5: Lint, types, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
(`mypy` checks that `Runtime` and `loxone/server.py`'s `_RuntimeDependency` still satisfy `RuntimeValues`.)

```bash
git add src/loxmatter/api/models.py src/loxmatter/api/devices.py tests/api/conftest.py tests/api/test_zigbee_api.py tests/api/test_devices.py
git commit -m "feat(api): report the raw value beside every converted signal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The WebUI shows the raw value

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (new key near the other `web.signals.*` keys)
- Modify: `src/loxmatter/web/app.js` (`liveValues: {}` ~line 1466, `liveValueOf` ~line 2994, `signalAgeTitle` ~line 3537, the comment above `signalValueByPath` ~line 5518, `readStartValues` ~line 5544, the socket `message` handler ~line 8302)
- Modify: `src/loxmatter/web/index.html` (signals dialog value cell ~line 3495-3500)
- Modify: `src/loxmatter/web/style.css` (after `.value-fresh` ~line 1315)
- Modify: `tests/api/test_web.py` (new delivery tests)

**Interfaces:**
- Consumes: `signal.converted`, `signal.raw_value` (Task 4), live message field `raw` (Task 3).
- Produces: `liveRaw` state, `liveRawOf(signal)`, `rawValueText(signal)` in the Alpine app.

- [ ] **Step 1: Write the failing delivery tests**

Append to `tests/api/test_web.py`, following `test_the_relative_time_and_header_helpers_are_translated` (~line 2856) for how the script is fetched:

```python
async def test_the_live_socket_keeps_the_raw_value(api):
    """Design 2026-10-01, section 5.3. Delivery only - the binding itself
    is checked in a browser harness."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    handler_start = script.index('socket.addEventListener("message"')
    handler = script[handler_start : script.index("\n      });", handler_start)]
    assert "this.liveRaw[message.key] = message.raw;" in handler
    assert "liveRawOf(signal) {" in script
    assert "rawValueText(signal) {" in script


async def test_the_raw_value_is_translated_and_shown_in_the_dialog_and_tooltip(api):
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    page = (await client.get("/")).text
    body_start = script.index("rawValueText(signal) {")
    body = script[body_start : script.index("\n    },", body_start)]
    assert 't("web.signals.raw_value"' in body
    title_start = script.index("signalAgeTitle(signal) {")
    title = script[title_start : script.index("\n    },", title_start)]
    assert "this.rawValueText(signal)" in title
    assert 'x-text="rawValueText(signal)"' in page


async def test_the_kelvin_slider_starts_from_the_kelvin_signal(api):
    """768/7 is Kelvin since 2026-10-01; dividing 1e6 by it again would put
    the slider at about 250 K.

    Fault to prove it: restore `1000000 / mireds`."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("readStartValues(deviceId) {")
    body = script[start : script.index("\n    },", start)]
    assert "1000000 /" not in body
    assert "this.signalValueByPath(deviceId, 768, 7)" in body
```

If the root page is not served at `/` in these tests, use the path the neighbouring HTML tests use. If `api` unpacks differently there, follow the neighbours.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_web.py -q -k "raw_value or kelvin_slider"`
Expected: 3 failed.

- [ ] **Step 3: Add the string**

In `src/loxmatter/i18n/strings.yaml`, next to the other `web.signals.*` keys:

```yaml
# The device's own number under a converted value (design 2026-10-01,
# section 5.3) - "23000" under "200 lx", "250" under "4000 K".
web.signals.raw_value:
  en: "raw value: {value}"
  de: "Rohwert: {value}"
```

- [ ] **Step 4: Keep the raw value in the app**

In `src/loxmatter/web/app.js`:

After `liveValues: {},` (~line 1466):

```js
    // The raw device value of a converted signal, by key, from the live
    // socket (design 2026-10-01, section 5.3) - the companion of
    // `liveValues`, read only through `liveRawOf`.
    liveRaw: {},
```

In the socket `message` handler, directly after `this.liveValues[message.key] = message.value;`:

```js
        this.liveRaw[message.key] = message.raw;
```

After `liveValueOf`:

```js
    // The device's own number behind a converted value, or `undefined` -
    // for an unconverted signal there is nothing to show beside the value
    // itself. Same order as `liveValueOf`: the live socket first, then
    // what the signal list was loaded with.
    liveRawOf(signal) {
      if (!signal || !signal.converted) {
        return undefined;
      }
      const live = this.liveRaw[signal.key];
      if (live !== undefined && live !== null) {
        return live;
      }
      return signal.raw_value ?? undefined;
    },

    // "raw value: 23000", or "" when there is none - an empty string lets
    // `x-show` hide the line and `signalAgeTitle` leave the tooltip alone.
    rawValueText(signal) {
      const raw = this.liveRawOf(signal);
      return raw === undefined ? "" : t("web.signals.raw_value", { value: this.formatValue(raw) });
    },
```

(If `formatValue` is a free function in this file rather than a method, call it the way `index.html` does - `formatValue(raw)` in a template means it is reachable from the component; check with `grep -n "formatValue(" src/loxmatter/web/app.js | head`.)

Replace the end of `signalAgeTitle` (the `const text = ...; return text ? ... : ...;` part) with:

```js
      const text = this.signalSeenText(signal);
      const age = text
        ? t("web.header.last_updated", { text })
        : t("web.header.unchanged_since_load");
      // The tile has no room for a second line (design 2026-10-01, 5.3),
      // so the raw value rides in the tooltip there - and here in the
      // dialog too, where it costs nothing.
      const raw = this.rawValueText(signal);
      return raw ? `${age} · ${raw}` : age;
```

The two `t(...)` literals must keep exactly this form - `test_the_relative_time_and_header_helpers_are_translated` looks for `? t("web.header.last_updated", { text })` and `: t("web.header.unchanged_since_load");`.

In the comment above `signalValueByPath`, replace `- level and saturation in percent, hue in degrees. Only the color temperature stays in mired, because Kelvin is a reciprocal that `scale` cannot do.` with `- level and saturation in percent, hue in degrees, colour temperature in Kelvin (since 2026-10-01).`

In `readStartValues`:

```js
    readStartValues(deviceId) {
      // Cluster 8 attribute 0 = CurrentLevel; cluster 768: 0 = CurrentHue,
      // 1 = CurrentSaturation, 7 = ColorTemperatureMireds - which the
      // bridge converts to Kelvin before anything reads it (design
      // 2026-10-01) - and 8 = ColorMode.
      // All checked against the installed SDK (see design, section 4).
      const kelvin = this.signalValueByPath(deviceId, 768, 7);
      return {
        percent: this.signalValueByPath(deviceId, 8, 0),
        kelvin: kelvin > 0 ? Math.round(kelvin) : undefined,
        hue: this.signalValueByPath(deviceId, 768, 0),
        saturation: this.signalValueByPath(deviceId, 768, 1),
        colormode: this.signalValueByPath(deviceId, 768, 8),
      };
    },
```

- [ ] **Step 5: Show it in the signals dialog**

In `src/loxmatter/web/index.html`, replace the value `<span>` of the signals dialog (~lines 3495-3500, the one with `class="value col-right"`) with:

```html
                        <span class="col-right value-stack">
                          <span
                            class="value"
                            :class="{ 'value-fresh': signalIsFresh(signal) }"
                            :title="signalAgeTitle(signal)"
                            x-text="formatValue(liveValueOf(signal)) + (signal.unit ? ' ' + signal.unit : '')"
                          ></span>
                          <!-- The device's own number under a converted
                               value (design 2026-10-01, section 5.3):
                               small and muted, because the converted
                               value above is the one that matters. -->
                          <span
                            class="raw-value"
                            x-show="rawValueText(signal)"
                            x-text="rawValueText(signal)"
                          ></span>
                        </span>
```

Keep the HTML comment that sits directly above the old span. Leave the device tile (~line 1369) unchanged - it gets the raw value through `signalAgeTitle`.

- [ ] **Step 6: Style it**

In `src/loxmatter/web/style.css`, after the `.value-fresh` rule block:

```css
/* Converted value with the device's raw number underneath (design
   2026-10-01, section 5.3). One grid cell, two lines, right-aligned like
   the value always was. */
.value-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.raw-value {
  font-family: var(--mono);
  font-size: 0.72rem;
  color: var(--text-muted);
}
```

If `.col-right` sets `justify-self` or `text-align`, the wrapper inherits it as a grid cell; check that the column still lines up (Step 8).

- [ ] **Step 7: Run the web tests**

Run: `uv run pytest tests/api/test_web.py tests/test_i18n.py -q`
Expected: all pass. Then `uv run python scripts/check_language.py` - expected `No German found.`

- [ ] **Step 8: Check the bindings in a browser**

Delivery tests do not run Alpine. Build a throwaway harness in the scratchpad (not in the repository): an HTML page that loads the real `src/loxmatter/web/app.js` and `style.css`, stubs `fetch` and `WebSocket` so that `/api/devices` returns one lamp and `/api/devices/1/signals` returns a `colortemp_kelvin` signal (`value: 4000, unit: "K", converted: true, raw_value: 250`) and an `onoff` signal (`converted: false, raw_value: null`), and then delivers one live message `{"key": "d1_1_colortemp_kelvin", "value": 2702.7, "raw": 370}`. Open it with the Browser pane and check:

1. The dialog shows `4000 K` with `raw value: 250` underneath; `onoff` has no second line.
2. After the live message: `2703 K` (or as `formatValue` renders it) and `raw value: 370`.
3. The tile tooltip (`title` attribute) of the colour temperature ends in `· raw value: 370`.
4. At 375 px width the dialog row does not overflow.
5. Switching the language to German shows `Rohwert: 370`.

Report what you saw for each; a screenshot of 1 and 4.

- [ ] **Step 9: Lint, commit**

Run: `uv run ruff check . && uv run ruff format --check .`

```bash
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "feat(web): show the raw value under every converted signal

The converted value stays where it was; the dialog adds the device's own
number underneath, the tile's tooltip carries it, and the Kelvin slider
reads the Kelvin signal directly.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Changelog, open point, full checks

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`)
- Modify: `docs/superpowers/specs/2026-09-03-signal-selection-design.md` (section 10, the open point on the missing reciprocal conversion)

- [ ] **Step 1: Changelog**

Under `## [Unreleased]`, in `### Before you update`, below the schema line (which Task 2 changed to "14 to 16"):

```markdown
- **Colour temperature and brightness inputs change unit.** The colour
  temperature of a lamp now reaches Loxone in Kelvin instead of mired, and
  the brightness a motion sensor measures in lux instead of the sensor's
  raw number. The inputs keep their names, so nothing needs re-importing -
  but a threshold or formula on them in Loxone Config has to be adjusted:
  "dark" below 15000 raw is about 30 lx now.
```

Under `### Changed`:

```markdown
- **The signal list shows the device's raw value under every converted
  value** - "raw value: 23000" under "200 lx" - and the tile's tooltip
  shows it too.
```

- [ ] **Step 2: Close the open point**

In `docs/superpowers/specs/2026-09-03-signal-selection-design.md`, find the item in section 10 about the colour temperature staying in mired / the missing reciprocal conversion (`grep -n "mired" docs/superpowers/specs/2026-09-03-signal-selection-design.md`). Strike it through the way the other done items there are (`~~...~~`) and add: `**Done (2026-10-01).** `convert: kelvin_from_mired` in `clusters.yaml`, see docs/superpowers/specs/2026-10-01-lux-kelvin-conversion-design.md.`

- [ ] **Step 3: Full checks**

Run each, in the foreground:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_language.py
uv run pytest --collect-only -q
```

Expected: clean; collection reports no errors (duplicate test file basenames break CI).

The suite takes more than ten minutes, more than one Bash call allows. Run it in two halves, each with the maximum timeout (600000 ms):

```bash
uv run pytest tests/api -q -p no:cacheprovider
```

```bash
uv run pytest tests --ignore=tests/api -q -p no:cacheprovider
```

Expected: both all passed. If a half times out, split it further by directory rather than skipping tests.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/superpowers/specs/2026-09-03-signal-selection-design.md
git commit -m "docs(changelog): announce lux and Kelvin and the raw value line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
