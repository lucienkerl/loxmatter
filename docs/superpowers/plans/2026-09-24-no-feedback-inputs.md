# No Feedback Inputs by Default - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signal that only reports back a state the device accepts as a
command (a light's `onoff`, `level`, colour values) is no longer preselected
for export — for new devices, and once for every device already stored.

**Architecture:** The profile table marks such attributes `feedback: true`.
`profiles.table.marks_feedback` reads the mark; `Store.register_signals` leaves
a marked signal unexported when it creates it; a new store migration
`_migrate_to_v12` unticks the marked signals already stored.

**Tech Stack:** Python 3.12, SQLite (`sqlite3`), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-no-feedback-inputs-design.md`

**Worktree:** `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/no-feedback-inputs`
(branch `claude/no-feedback-inputs`). Every path below is relative to it.
Subagents: `cd` there first and use absolute paths; the main checkout is a
different branch.

## Global Constraints

- Everything in the repository is English: code, comments, docstrings, test
  names, commit messages. `scripts/check_language.py` enforces it.
- Commit messages: Conventional Commits, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The table mark is spelled exactly `feedback: true`, and it goes on exactly
  these eight attribute entries of `src/loxmatter/profiles/clusters.yaml`:
  6/0 `onoff`, 8/0 `level`, 768/0 `hue`, 768/1 `saturation`, 768/3
  `color_x`, 768/4 `color_y`, 768/7 `colortemp_mireds`, 768/8 `colormode`.
- `functional` does not change for any signal (the signal dialog keeps its
  order). Only the default of `exported` changes.
- Migrations are additive: no column is added or dropped.
  `_SCHEMA_VERSION` goes from 11 to 12.
- No user-facing string is added, so `strings.yaml` does not change.

## Running the tests

The suite has three parts with very different run times (measured
2026-09-24 in this worktree). Run each part in the foreground, not the whole
suite in one command:

```bash
uv run pytest -q -p no:cacheprovider tests --ignore=tests/api --ignore=tests/test_install_script.py --ignore=tests/test_update_script.py --ignore=tests/test_updater_script.py --ignore=tests/test_updater_radios_script.py --ignore=tests/test_updater_watchdog_once.py --ignore=tests/test_otbr_watchdog.py
```
(about 50 s)

```bash
uv run pytest -q -p no:cacheprovider tests/api
```
(about 2 min)

```bash
uv run pytest -q -p no:cacheprovider tests/test_install_script.py tests/test_update_script.py tests/test_updater_script.py tests/test_updater_radios_script.py tests/test_updater_watchdog_once.py tests/test_otbr_watchdog.py
```
(about 9.5 min, needs a Bash timeout of 600000 ms; this change does not touch
anything these tests cover, so only the final task runs it)

A trial run of the whole change found exactly these tests failing before
their updates, and no others: the two plug tests in `tests/model/test_store.py`,
the two plug tests in `tests/export/test_signals.py`,
`tests/test_export_cli.py::test_non_exportable_attributes_do_not_appear`,
`tests/api/test_devices.py::test_device_list_reports_how_many_inputs_the_next_export_would_produce`,
`tests/api/test_export_api.py::test_preview_reports_what_would_be_written`, and
every assertion of `user_version == 11` in `tests/model/test_store_migration.py`
and `tests/model/test_store_identity.py`, plus the `[5, 17]` count in
`test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures`.

---

### Task 1: The table marks feedback

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml` (header comment; entries 6/0, 8/0, 768/0, 768/1, 768/3, 768/4, 768/7, 768/8)
- Modify: `src/loxmatter/profiles/table.py` (after `marked_non_functional`, around line 219)
- Test: `tests/profiles/test_table.py`

**Interfaces:**
- Produces: `loxmatter.profiles.table.marks_feedback(ref: SignalRef) -> bool`
  and `loxmatter.profiles.table.feedback_elements() -> list[tuple[int, int]]`
  (sorted `(cluster_id, element_id)` pairs of every attribute entry marked
  `feedback: true`). Tasks 2 and 3 import both.

- [ ] **Step 1: Write the failing tests**

In `tests/profiles/test_table.py`, add `feedback_elements` and `marks_feedback`
to the existing `from loxmatter.profiles.table import (...)` block, keeping
its alphabetical order. Then append:

```python
FEEDBACK_ENTRIES = [
    (6, 0),  # onoff
    (8, 0),  # level
    (768, 0),  # hue
    (768, 1),  # saturation
    (768, 3),  # color_x
    (768, 4),  # color_y
    (768, 7),  # colortemp_mireds
    (768, 8),  # colormode
]


@pytest.mark.parametrize(("cluster_id", "element_id"), FEEDBACK_ENTRIES)
def test_a_state_behind_a_command_is_marked_as_feedback(cluster_id, element_id):
    """Design 2026-09-24, section 3: where Loxone is the only controller,
    these values only echo what Loxone just sent.

    Fault to prove it: drop `feedback: true` from any one of the entries."""
    assert marks_feedback(SignalRef(1, cluster_id, element_id, SignalKind.ATTRIBUTE)) is True


@pytest.mark.parametrize(
    ("cluster_id", "element_id", "what"),
    [
        (1026, 0, "a temperature reading"),
        (47, 12, "the battery level"),
        (144, 8, "a plug's active power"),
        (768, 16395, "a device constant"),
        (4242, 0, "a cluster the table does not know"),
    ],
)
def test_what_loxone_does_not_set_itself_is_not_feedback(cluster_id, element_id, what):
    """Sensor readings, energy values, the battery and device constants stay
    as they are (design 2026-09-24, section 2)."""
    assert marks_feedback(SignalRef(1, cluster_id, element_id, SignalKind.ATTRIBUTE)) is False, what


def test_an_event_is_never_feedback():
    """A button press is the input Loxone wants most. Event 1 of cluster 59
    shares its element ID with no feedback attribute, so this checks the
    kind, not an accident of numbering: 6/0 as an EVENT must not read the
    attribute entry 6/0."""
    assert marks_feedback(SignalRef(1, 59, 1, SignalKind.EVENT)) is False
    assert marks_feedback(SignalRef(1, 6, 0, SignalKind.EVENT)) is False


def test_feedback_elements_lists_exactly_the_marked_entries():
    """`_migrate_to_v12` unticks exactly these pairs; a pair too many would
    silence a sensor on every stored device."""
    assert feedback_elements() == sorted(FEEDBACK_ENTRIES)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/profiles/test_table.py`
Expected: collection error, `ImportError: cannot import name 'feedback_elements'`.

- [ ] **Step 3: Implement**

In `src/loxmatter/profiles/table.py`, directly after `marked_non_functional`, add:

```python
def marks_feedback(ref: SignalRef) -> bool:
    """Whether the table marks this attribute as feedback
    (`feedback: true`): the state behind a command the same cluster accepts.

    Where Loxone is the only system controlling a device, such a value only
    echoes what Loxone just sent, so it is not preselected for export
    (design 2026-09-24). It stays functional - the signal dialog keeps
    showing it at the top, only unticked.

    A mark in the table rather than a derivation from the device: a
    thermostat accepts commands and reports the measured room temperature
    in the same cluster, and `_migrate_to_v12` has no snapshot to derive
    anything from. Events are never feedback.
    """
    if ref.kind is SignalKind.EVENT:
        return False
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return False
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry:
        return False
    return entry.get("feedback") is True


def feedback_elements() -> list[tuple[int, int]]:
    """Every `(cluster_id, element_id)` attribute pair marked as feedback,
    sorted - for `model.store._migrate_to_v12`, which has no `SignalRef`
    to ask `marks_feedback` with."""
    return sorted(
        (cluster_id, element_id)
        for cluster_id, cluster in _table().items()
        for element_id, entry in (cluster.get("attributes") or {}).items()
        if entry and entry.get("feedback") is True
    )
```

In `src/loxmatter/profiles/clusters.yaml`, add `feedback: true` as the last key
of exactly these eight entries (leave everything else on each line as it is):

```yaml
      0: {slug: onoff, unit: "", feedback: true}
      0: {slug: level, unit: "%", scale: 0.3937007874015748, feedback: true}
      0: {slug: hue, unit: "°", scale: 1.4173228346456692, feedback: true}
      1: {slug: saturation, unit: "%", scale: 0.3937007874015748, feedback: true}
      7: {slug: colortemp_mireds, unit: mired, feedback: true}
      8: {slug: colormode, unit: "", feedback: true}
      3: {slug: color_x, unit: "", feedback: true}
      4: {slug: color_y, unit: "", feedback: true}
```

Then add this paragraph to the header comment of `clusters.yaml`, after the
paragraph that ends "not just a guess.":

```yaml
#
# `feedback: true` on an attribute entry marks the state behind a command
# the same cluster accepts - on/off, level, colour. Where Loxone is the only
# system controlling a device, such a value only echoes what Loxone just
# sent, so it is not preselected for export (design 2026-09-24). It is not
# the same as `functional: false`: a feedback value stays at the top of the
# signal dialog, only unticked. A cluster added to this table for its
# commands (window covering, thermostat, door lock) marks its own feedback
# attributes here when it is added; an unmarked attribute stays preselected.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q -p no:cacheprovider tests/profiles`
Expected: all pass. (`exported` defaults do not change yet, so nothing else is
affected.)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/profiles/clusters.yaml src/loxmatter/profiles/table.py tests/profiles/test_table.py
git commit -m "feat(profiles): mark the state behind a command as feedback" -m "A light's on/off, level and colour values only echo what Loxone just sent when Loxone is the only controller. The table now says which attributes those are, so the store can leave them unticked." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: New devices leave feedback unticked

**Files:**
- Modify: `src/loxmatter/model/store.py` (import block around line 75; `register_signals`, the line `exported = is_exportable(profile.exportability) and functional`, around line 2392)
- Test: `tests/model/test_store.py`, `tests/export/test_signals.py`, `tests/test_export_cli.py`, `tests/api/test_devices.py`, `tests/api/test_export_api.py`

**Interfaces:**
- Consumes: `marks_feedback(ref: SignalRef) -> bool` from Task 1.

- [ ] **Step 1: Write the failing tests and update the counts**

In `tests/model/test_store.py`, add `from loxmatter.profiles.table import marks_feedback`
to the imports (merge it into the existing
`from loxmatter.profiles.table import Exportability, Profile, lookup` line,
alphabetically). Then:

1. `test_new_signal_is_exported_exactly_when_it_is_exportable_and_functional`:
   rename to `test_new_signal_is_exported_exactly_when_it_is_exportable_functional_and_not_feedback`,
   change the `expected` line to

   ```python
        expected = (
            technically_exportable
            and is_functional(signal.ref, device_types)
            and not marks_feedback(signal.ref)
        )
   ```

   and append to its docstring:

   ```
    `marks_feedback` (design 2026-09-24) is reused for the same reason as
    `is_functional`: it is checked on its own against the table in
    `tests/profiles/test_table.py`.
   ```

2. `test_a_freshly_registered_plug_exports_only_its_meaningful_values`:
   remove `"d1_1_onoff",` from the expected set and replace the docstring with

   ```python
    """The goal of this whole design, on the real device: the values that
    mean something, instead of 110 technically mappable ones (draft section
    1 and 4.4). Four since 2026-09-24: `onoff` is feedback of the plug's own
    on/off command and no longer preselected - voltage, current, active
    power and the meter reading stay."""
   ```

3. Append two new tests:

   ```python
def test_a_freshly_registered_colour_light_exports_no_feedback(store):
    """Design 2026-09-24: Loxone switches the light, so none of its eight
    state values is preselected - `onoff`, `level` and six colour values.
    Before, exactly these eight were exported."""
    snap = load("ikea_kajplats_cws_lamp.json")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)

    assert [s.key for s in store.signals(device_id) if s.exported] == []


def test_feedback_stays_functional_so_the_dialog_keeps_its_order(store):
    """Only `exported` changes. `functional` decides whether the signal
    dialog shows a value at the top or folds it into the expert block
    (design 2026-09-24, section 3.2) - feedback belongs at the top."""
    snap = load("ikea_kajplats_cws_lamp.json")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)

    by_key = {s.key: s for s in store.signals(device_id)}
    for key in ("d1_1_onoff", "d1_1_level", "d1_1_hue", "d1_1_colortemp_mireds"):
        assert by_key[key].functional is True, key
        assert by_key[key].exported is False, key
   ```

In `tests/export/test_signals.py`:

1. Rename `test_plug_fixture_yields_6_inputs_with_the_relevance_default` to
   `test_plug_fixture_yields_5_inputs_with_the_relevance_default`, change
   `assert len(inputs) == 6` to `assert len(inputs) == 5`, and in its docstring
   replace the sentence starting "only the five remain" through "Plus the
   online signal, makes 6." with:

   ```
    only the four remain that mean something: voltage, current, active
    power, and the energy meter reading - `onoff` is feedback since
    2026-09-24 (see
    `tests/model/test_store.py::test_a_freshly_registered_plug_exports_only_its_meaningful_values`).
    Plus the online signal, makes 5.
   ```

2. `test_unchecking_one_signal_reduces_the_plug_fixtures_input_count_by_one`:
   change `assert len(inputs) == 5` to `assert len(inputs) == 4` and in its
   docstring replace "Base count since Task 6: 6 (see
   `test_plug_fixture_yields_6_inputs_with_the_relevance_default`), so 5
   after unchecking." with "Base count since 2026-09-24: 5 (see
   `test_plug_fixture_yields_5_inputs_with_the_relevance_default`), so 4
   after unchecking."

In `tests/test_export_cli.py`:

1. `test_non_exportable_attributes_do_not_appear`: change
   `assert commands == 5 + 1` to `assert commands == 4 + 1`, and in the
   docstring replace "for this plug, 5 of those remain (see
   `tests/export/test_signals.py::test_plug_fixture_yields_6_inputs_with_the_relevance_default`)."
   with "for this plug, 4 of those remain, `onoff` being feedback since
   2026-09-24 (see
   `tests/export/test_signals.py::test_plug_fixture_yields_5_inputs_with_the_relevance_default`)."

2. Append:

   ```python
def test_a_colour_light_template_has_only_the_online_input(tmp_path):
    """Design 2026-09-24: Loxone controls the light, so its template carries
    no feedback input - only the device's own online signal - while every
    output stays."""
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_kajplats_cws_lamp.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    inputs = next(tmp_path.glob("VIU_*.xml")).read_text(encoding="utf-8-sig")
    outputs = next(tmp_path.glob("VO_*.xml")).read_text(encoding="utf-8-sig")
    assert inputs.count("<VirtualInUdpCmd ") == 1
    assert "_online:\\v" in inputs
    assert outputs.count("<VirtualOutCmd ") > 0
   ```

In `tests/api/test_devices.py`,
`test_device_list_reports_how_many_inputs_the_next_export_would_produce`:
change `== 6` to `== 5` and in the docstring "5 functional signals plus the
online signal" to "4 functional signals (`onoff` is feedback since
2026-09-24) plus the online signal".

In `tests/api/test_export_api.py`, `test_preview_reports_what_would_be_written`:
change `assert device["inputs"] == 6` to `== 5` and in the docstring "5
relevant signals of the plug" to "4 relevant signals of the plug (`onoff` is
feedback since 2026-09-24)" and "makes 6" to "makes 5".

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/model/test_store.py tests/export/test_signals.py tests/test_export_cli.py tests/api/test_devices.py tests/api/test_export_api.py`
Expected: FAIL — the plug still exports `d1_1_onoff` (counts one too high),
the colour light exports eight signals, the CLI template has 9
`VirtualInUdpCmd`.

- [ ] **Step 3: Implement**

In `src/loxmatter/model/store.py`, add `marks_feedback,` to the
`from loxmatter.profiles.table import (...)` block (after `lookup,`). In
`register_signals`, replace

```python
                exported = is_exportable(profile.exportability) and functional
```

with

```python
                # Third question (design 2026-09-24): whether the value is
                # only feedback of a command - then Loxone sent it itself,
                # and it stays unticked. `functional` stays true, so the
                # signal dialog still shows it at the top.
                exported = (
                    is_exportable(profile.exportability)
                    and functional
                    and not marks_feedback(ref)
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command as Step 2.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/test_store.py tests/export/test_signals.py tests/test_export_cli.py tests/api/test_devices.py tests/api/test_export_api.py
git commit -m "feat(store): leave feedback unticked on newly commissioned devices" -m "A light Loxone controls no longer brings onoff, level and its colour values as inputs; a plug keeps its energy readings. The values stay functional, so the signal dialog still shows them at the top." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Stored devices lose their feedback inputs once

**Files:**
- Modify: `src/loxmatter/model/store.py` (the schema-version comment block ending in `_SCHEMA_VERSION = 11`, around line 140-156; a new function after `_migrate_to_v11`, around line 818; `_MIGRATIONS`; the import block)
- Test: `tests/model/test_store_migration.py`, `tests/model/test_store_identity.py`

**Interfaces:**
- Consumes: `feedback_elements() -> list[tuple[int, int]]` from Task 1.

- [ ] **Step 1: Write the failing tests and move the version**

In `tests/model/test_store_migration.py` and `tests/model/test_store_identity.py`,
every `user_version(path) == 11`, `_user_version(path) == 11` and
`fetchone()[0] == 11` becomes `== 12`:

```bash
sed -i '' -E 's/(user_version\(path\) == |fetchone\(\)\[0\] == )11$/\112/' tests/model/test_store_migration.py tests/model/test_store_identity.py
grep -n "== 11$" tests/model/test_store_migration.py tests/model/test_store_identity.py
```

The `grep` must print nothing.

In `test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures`,
change `assert sorted(counts.values()) == [5, 17]` to `== [4, 17]`, and in its
docstring change "`test_a_freshly_registered_plug_exports_only_its_meaningful_values` = 5,"
to "`test_a_freshly_registered_plug_exports_only_its_meaningful_values` = 4
since 2026-09-24, `onoff` being feedback,".

Append to `tests/model/test_store_migration.py`:

```python
def _open_at_v11_with_feedback_ticked(path: Path) -> dict[str, int]:
    """A version-11 database as the previous release left it: a colour
    light and a plug, every feedback signal ticked (the old default), plus
    two hand-made choices the migration must not touch - the plug's
    voltage unticked and the light's physical minimum colour temperature
    ticked. Returns the device IDs by fixture."""
    store = Store(path)
    ids: dict[str, int] = {}
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_grillplats_plug.json"):
        snap = load(name)
        device_id = store.register_device(snap)
        store.register_signals(device_id, snap)
        ids[name] = device_id
    store.set_exported(f"d{ids['ikea_grillplats_plug.json']}_2_voltage", False)
    store.set_exported(
        f"d{ids['ikea_kajplats_cws_lamp.json']}_1_colortemp_phys_min_mireds", True
    )
    store.close()

    db = sqlite3.connect(str(path))
    db.executescript(
        "UPDATE signal SET exported = 1 WHERE kind = 'attribute' AND ("
        " (cluster_id = 6 AND element_id = 0) OR (cluster_id = 8 AND element_id = 0)"
        " OR (cluster_id = 768 AND element_id IN (0, 1, 3, 4, 7, 8)));"
        " PRAGMA user_version = 11;"
    )
    db.commit()
    db.close()
    return ids


def test_migration_to_v12_unticks_feedback_on_stored_devices(tmp_path):
    """Design 2026-09-24, section 5: a light commissioned before the update
    loses its feedback inputs on the first start after it, as a freshly
    commissioned one never has them."""
    path = tmp_path / "v11.sqlite"
    ids = _open_at_v11_with_feedback_ticked(path)

    store = Store(path)
    try:
        assert user_version(path) == 12
        light = {s.key: s for s in store.signals(ids["ikea_kajplats_cws_lamp.json"])}
        plug = {s.key: s for s in store.signals(ids["ikea_grillplats_plug.json"])}
    finally:
        store.close()

    light_id = ids["ikea_kajplats_cws_lamp.json"]
    for slug in (
        "onoff",
        "level",
        "hue",
        "saturation",
        "color_x",
        "color_y",
        "colortemp_mireds",
        "colormode",
    ):
        assert light[f"d{light_id}_1_{slug}"].exported is False, slug
    assert plug[f"d{ids['ikea_grillplats_plug.json']}_1_onoff"].exported is False


def test_migration_to_v12_leaves_every_other_choice_alone(tmp_path):
    """Only the eight marked attributes change. A sensor value the user
    unticked stays unticked, a device constant they ticked stays ticked,
    and the plug's energy readings stay exported."""
    path = tmp_path / "v11.sqlite"
    ids = _open_at_v11_with_feedback_ticked(path)
    plug_id = ids["ikea_grillplats_plug.json"]
    light_id = ids["ikea_kajplats_cws_lamp.json"]

    store = Store(path)
    try:
        plug = {s.key: s for s in store.signals(plug_id)}
        light = {s.key: s for s in store.signals(light_id)}
    finally:
        store.close()

    assert plug[f"d{plug_id}_2_voltage"].exported is False
    assert light[f"d{light_id}_1_colortemp_phys_min_mireds"].exported is True
    for slug in ("current", "power", "energy_imported"):
        assert plug[f"d{plug_id}_2_{slug}"].exported is True, slug
```

The module already has everything these tests use: `load` and `FIXTURES`
(lines 46-51), `user_version` (line ~212), and the `json`, `sqlite3`, `Path`,
`NodeSnapshot` and `Store` imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/model/test_store_migration.py tests/model/test_store_identity.py`
Expected: FAIL — every version assertion reads 11, the `[4, 17]` count reads
`[5, 17]`, and the new migration tests find the feedback still ticked.

- [ ] **Step 3: Implement**

In `src/loxmatter/model/store.py`:

1. Add `feedback_elements,` to the `from loxmatter.profiles.table import (...)`
   block (after `element_rank_for,`).

2. Directly after `_migrate_to_v11`, add:

```python
def _migrate_to_v12(db: sqlite3.Connection) -> None:
    """Unticks every stored feedback signal (design 2026-09-24, section 5).

    Feedback is the state behind a command the same cluster accepts -
    `profiles.table.feedback_elements` names the pairs. From version 12 on
    `register_signals` leaves them unexported on a new device; this brings
    every device commissioned earlier to the same state, so the next export
    no longer carries their inputs.

    It cannot tell a preselected feedback signal from one somebody ticked by
    hand - the store keeps no record of which is which - and unticks both.
    Decided consciously: leaving stored devices alone would have kept every
    existing light's feedback inputs. Additive like every migration here: it
    writes one column of the matching rows and adds or drops nothing, so a
    rolled-back image finds a valid state."""
    for cluster_id, element_id in feedback_elements():
        db.execute(
            "UPDATE signal SET exported = 0"
            " WHERE cluster_id = ? AND element_id = ? AND kind = 'attribute'",
            (cluster_id, element_id),
        )
```

3. Append `12: _migrate_to_v12,` to `_MIGRATIONS`.

4. Change `_SCHEMA_VERSION = 11` to `_SCHEMA_VERSION = 12`, and add to the
   comment block right above it:

```python
# Version 12 (no feedback inputs, design 2026-09-24) adds no column: it sets
# `signal.exported = 0` for every attribute the profile table marks as
# feedback, see `_migrate_to_v12`. Like `_migrate_to_v3` it re-decides a
# default for existing rows; unlike there, it only ever unticks.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q -p no:cacheprovider tests/model`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/test_store_migration.py tests/model/test_store_identity.py
git commit -m "feat(store): untick feedback on devices commissioned before the update" -m "Schema 12 sets exported = 0 for every stored attribute the profile table marks as feedback, so an existing light loses its onoff, level and colour inputs on the next export, as a new one never has them. Nothing else is touched." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Change notes and the full check

**Files:**
- Modify: `CHANGELOG.md` (the `### Changed` section of `## [Unreleased]`, around line 31)

- [ ] **Step 1: Write the change note**

Add as the first bullet under `### Changed` in the `## [Unreleased]` block
(the CHANGELOG is shown to users before an update, so it is written for people
who don't know the code):

```markdown
- **A light no longer reports its state back to Loxone by default.** When
  Loxone is the only system that switches a device, its on/off, brightness
  and colour inputs only repeat what Loxone just sent. They are now unticked
  for every device — also for those already set up, once, with this update.
  Sensors, buttons, energy readings, the battery and the online input stay.
  If you do switch a light from elsewhere, tick its values again in the
  signal dialog. Inputs already in your project file are listed as orphaned
  by the sync; remove them in Loxone Config.
```

- [ ] **Step 2: Run every check CI runs**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_language.py
```

Expected: all clean. Then the three test parts from "Running the tests"
above, each in the foreground (the third with a 600000 ms timeout).
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): feedback inputs are no longer preselected" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
