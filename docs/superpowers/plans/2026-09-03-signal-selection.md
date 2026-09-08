# Signal Selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A plug exports five meaningful values to Loxone instead of 109 technically mappable ones, and the kWh meter reading arrives for the first time ever.

**Architecture:** A new concept `Relevance` decides exclusively about the *default value* of the existing column `exported`; the export mechanism remains untouched. The selection follows Matter's own structure — the descriptor cluster names a standardized device type per endpoint, Root Node and OTA Requestor are administrative. For clusters the profile table knows, only their named attributes additionally count. A new table field `field` pulls a number out of a Matter structure.

**Tech Stack:** Python 3.12, SQLite (`PRAGMA user_version` migrations), FastAPI, Pydantic v2, Alpine.js 3.17.1 (vendored, no build step), pytest, ruff, mypy strict.

**Design document:** [`docs/superpowers/specs/2026-09-03-signal-selection-design.md`](../specs/2026-09-03-signal-selection-design.md). In case of conflict between plan and design, the design takes precedence; report the conflict.

## Global Constraints

- **German** in prose, comments, docstrings, help texts, and error messages; **English** in all identifiers — including test names, JS variables, JSON field names, and YAML keys.
- All tests run **without hardware and without network access**.
- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` (strict over `src` and `scripts`) must be clean at the end of every task.
- Starting point: 477 tests green on `main`, HEAD `171a4b3`.
- **Keys are immutable** (main document 6.2). No task in this plan may change an existing signal key. A renamed key is a silently dead function block in someone else's Loxone config.
- **Check every external signature against the installed version**, rather than taking it from this plan (`uv run python -c "import inspect; ..."`). This plan has been wrong multiple times in earlier phases; the check caught it every time.
- Do **not read** `tests/fixtures/VirtualIn/` and `tests/fixtures/VirtualOut/` — unsanitized templates from a real installation, deliberately git-ignored.
- No connection to a host on the user's home network. A real matter-server with real devices runs at `10.0.1.56`.

## Files

| File | Responsibility |
|---|---|
| `src/loxmatter/profiles/relevance.py` | **new** — device type rule: which signals are wanted by default |
| `src/loxmatter/profiles/catalog.py` | **new** — attribute names from the chip SDK, display only |
| `src/loxmatter/profiles/table.py` | extended — fine selection for known clusters, struct field, title |
| `src/loxmatter/profiles/clusters.yaml` | extended — PowerSource, struct fields for energy |
| `src/loxmatter/loxone/values.py` | extended — pull a number out of a struct |
| `src/loxmatter/model/store.py` | extended — default value of `exported`, migration to schema v3 |
| `src/loxmatter/api/models.py` | extended — `relevance` in the signal payload |
| `src/loxmatter/api/devices.py` | extended — populate `relevance` |
| `src/loxmatter/api/export.py` | extended — preview names hidden signals |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | extended — functional/expert blocks |

---

### Task 1: Read device types from the descriptor

The foundation. Without this step, nothing knows the endpoint roles.

**Files:**
- Create: `src/loxmatter/profiles/relevance.py`
- Test: `tests/profiles/test_relevance.py`

**Interfaces:**
- Consumes: `loxmatter.matter.models.NodeSnapshot` (field `attributes: Mapping[str, Any]`, paths of the form `"<endpoint>/<cluster>/<attribute>"`), `loxmatter.matter.paths.parse_attribute_path`.
- Produces:
  - `DESCRIPTOR_CLUSTER_ID: int` (= 29), `DEVICE_TYPE_LIST_ID: int` (= 0)
  - `ROOT_NODE_DEVICE_TYPE: int`, `OTA_REQUESTOR_DEVICE_TYPE: int`, `POWER_SOURCE_DEVICE_TYPE: int`
  - `UTILITY_DEVICE_TYPES: frozenset[int]`
  - `device_types_by_endpoint(snapshot: NodeSnapshot) -> dict[int, frozenset[int]]`

- [ ] **Step 1: Establish the three device type numbers**

The installed SDK contains **no** device type table — only clusters. Check for yourself first:

```bash
uv run python -c "
import chip.clusters.Objects as O, os
print(os.path.dirname(O.__file__))
"
```

The numbers are in the CSA's Matter Device Library Specification. **Do not take them from this plan.** Establish them and write the source as a comment on the constant. Expected values for cross-checking — if your source differs, your source takes precedence, and you report the discrepancy:

- Root Node = 0x0016
- OTA Requestor = 0x0012
- Power Source = 0x0011

Cross-check against the checked-in snapshots (they must match your source):

```bash
uv run python -c "
import json
for f in ('ikea_grillplats_plug.json','ikea_bilresa_button.json'):
    d=json.load(open('tests/fixtures/nodes/'+f))
    a=d.get('attributes') or d
    print(f, {k: v for k,v in a.items() if k.endswith('/29/0')})
"
```

Expected output: the plug has types 18 and 22 on endpoint 0, the button additionally has 17 — and the button is the battery-powered device.

- [ ] **Step 2: Write the failing test**

```python
"""Device types per endpoint from the descriptor cluster."""

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
    """The battery level isn't coincidentally on endpoint 0 - the device
    declares the Power Source device type there. Task 2's exception rests
    exactly on that; without this guarantee it would be a guess."""
    types = device_types_by_endpoint(_snapshot("ikea_bilresa_button.json"))
    assert POWER_SOURCE_DEVICE_TYPE in types[0]


def test_an_endpoint_without_a_descriptor_is_absent_rather_than_empty():
    """If the descriptor is missing, the caller should be able to
    distinguish that from 'descriptor present but empty' - both lead to
    the same decision later, but for different reasons."""
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
    """A non-conformant device must not cause a crash. The endpoint then
    counts as typeless - and thus later (Task 2) as an application
    endpoint: when in doubt, one input too many, never a missing value."""
    snapshot = NodeSnapshot.from_raw({"node_id": 1, "attributes": {"0/29/0": raw}})
    assert device_types_by_endpoint(snapshot) == {0: frozenset()}
```

- [ ] **Step 3: Run the test, confirm the failure**

Run: `uv run pytest tests/profiles/test_relevance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.profiles.relevance'`

- [ ] **Step 4: Write the minimal implementation**

```python
"""Which signals a user wants by default (design 2026-09-03, 4.1).

Separate from `Exportability` and deliberately in its own module: the
question "can the value be mapped onto a UDP input" (table.py) and the
question "does anyone want it" are different questions with different
answers. A Thread radio counter is exportable, but not relevant.

The selection does not rely on a list of cluster numbers that someone
considered boring, but on Matter's own structure: the descriptor cluster
carries a standardized device type list on every endpoint. A device
without this information is not certified - the rule therefore carries
for every manufacturer and every device type, even those this tool has
never seen.
"""

from __future__ import annotations

from typing import Any

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import parse_attribute_path

DESCRIPTOR_CLUSTER_ID = 29
DEVICE_TYPE_LIST_ID = 0

# Source: Matter Device Library Specification (CSA). NOT derivable from
# the installed chip SDK - its catalog covers clusters, not device
# types (checked in Task 1).
ROOT_NODE_DEVICE_TYPE = 0x0016
OTA_REQUESTOR_DEVICE_TYPE = 0x0012
POWER_SOURCE_DEVICE_TYPE = 0x0011

UTILITY_DEVICE_TYPES: frozenset[int] = frozenset({ROOT_NODE_DEVICE_TYPE, OTA_REQUESTOR_DEVICE_TYPE})


def _device_type_ids(raw: object) -> frozenset[int]:
    """The device type numbers from a `DeviceTypeList` value.

    matter-server returns structures as a dictionary with the field tag
    as a STRING, not as the field name: a DeviceTypeStruct arrives as
    ``{"0": <Typ>, "1": <Revision>}``. Both - string and number - are
    accepted, because a different serialization of the same structure
    could just as plausibly deliver it as ``{0: ...}``.

    Anything unexpected yields an empty set instead of an exception: a
    non-conformant device should not halt the decomposition.
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
    """The declared device types per endpoint.

    An endpoint without a descriptor doesn't show up at all - the caller
    thereby distinguishes "not reported" from "reported, but empty",
    even though both lead to the same decision later.
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

- [ ] **Step 5: Run the test, confirm success**

Run: `uv run pytest tests/profiles/test_relevance.py -v`
Expected: PASS, 8 tests (4 cases from the parametrization plus the remaining four — recount: the parametrization has 5 cases, so 8 tests total)

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add src/loxmatter/profiles/relevance.py tests/profiles/test_relevance.py
git commit -m "feat(profiles): Geraetetypen je Endpunkt aus dem Descriptor lesen"
```

---

### Task 2: The relevance rule

**Files:**
- Modify: `src/loxmatter/profiles/relevance.py`
- Modify: `src/loxmatter/profiles/table.py` (new function `names_element`)
- Test: `tests/profiles/test_relevance.py`, `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: `device_types_by_endpoint` from Task 1; `loxmatter.matter.models.SignalRef` (fields `endpoint`, `cluster_id`, `element_id`, `kind`), `SignalKind`.
- Produces:
  - `BOILERPLATE_CLUSTERS: frozenset[int]`
  - `UTILITY_ENDPOINT_KEEP_CLUSTERS: frozenset[int]`
  - `is_functional(ref: SignalRef, device_types: dict[int, frozenset[int]]) -> bool`
  - in `table.py`: `names_element(ref: SignalRef) -> bool` — whether the table knows the cluster AND this element is named there

- [ ] **Step 1: Write the failing test for `names_element`**

Add to `tests/profiles/test_table.py`:

```python
def test_names_element_separates_named_from_generic_within_a_known_cluster():
    """The table knows cluster 6 and only names attribute 0 there. This
    exact distinction carries the fine selection: `onoff` is wanted,
    StartUpOnOff (0x4003) is not."""
    known = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    generic = SignalRef(1, 6, 0x4003, SignalKind.ATTRIBUTE)
    assert names_element(known) is True
    assert names_element(generic) is False


def test_names_element_is_false_for_a_cluster_the_table_does_not_know():
    """An unknown cluster names nothing. The caller (relevance) must NOT
    conclude 'everything off' from that - see there."""
    assert names_element(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is False


def test_names_element_covers_events_too():
    """Cluster 59 names its events; the fine selection must not discard a
    button press as unnamed."""
    assert names_element(SignalRef(1, 59, 1, SignalKind.EVENT)) is True
```

Add `names_element` to the import at the top of the file.

- [ ] **Step 2: Run the test, confirm the failure**

Run: `uv run pytest tests/profiles/test_table.py -k names_element -v`
Expected: FAIL — `ImportError: cannot import name 'names_element'`

- [ ] **Step 3: Implement `names_element`**

In `src/loxmatter/profiles/table.py`, directly after `lookup`:

```python
def knows_cluster(cluster_id: int) -> bool:
    """Whether the profile table carries this cluster at all."""
    return cluster_id in _table()


def names_element(ref: SignalRef) -> bool:
    """Whether the profile table names exactly this element.

    Separate from `lookup`, because `lookup` invents a generic name
    (`c6_a16387`) for an unnamed element and thereby loses the
    distinction. The fine selection in `profiles.relevance` needs it,
    though: within a known cluster, "named" is the marker for "wanted".
    """
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return False
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    return ref.element_id in (cluster.get(section) or {})
```

Check whether `SignalKind` is already imported in `table.py`; `lookup` uses it, so yes.

- [ ] **Step 4: Run the test, confirm success**

Run: `uv run pytest tests/profiles/test_table.py -k names_element -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Write the failing test for `is_functional`**

Add to `tests/profiles/test_relevance.py`:

```python
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.relevance import is_functional

_PLUG_TYPES = {0: frozenset({18, 22}), 1: frozenset({266}), 2: frozenset({1296})}
_BUTTON_TYPES = {0: frozenset({17, 18, 22}), 1: frozenset({15}), 2: frozenset({15})}


def test_a_thread_diagnostics_counter_on_the_root_endpoint_is_not_functional():
    ref = SignalRef(0, 53, 4, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_the_battery_level_on_a_root_endpoint_is_functional():
    """The exceptional case the descriptor itself justifies: the button
    additionally declares Power Source on endpoint 0."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _BUTTON_TYPES) is True


def test_the_battery_cluster_is_not_functional_where_no_power_source_is_declared():
    """The same cluster number on an endpoint without a Power Source type
    remains administrative. The rule hinges on the declared device type,
    not on the cluster number - otherwise it would again just be a
    list."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_onoff_on_an_application_endpoint_is_functional():
    assert is_functional(SignalRef(1, 6, 0, _KIND), _PLUG_TYPES) is True


def test_a_generic_attribute_of_a_known_cluster_is_not_functional():
    """StartUpOnOff (0x4003) legitimately sits with OnOff, but nobody
    wants it in Loxone. The table knows cluster 6 and only names
    attribute 0 there."""
    assert is_functional(SignalRef(1, 6, 0x4003, _KIND), _PLUG_TYPES) is False


def test_every_attribute_of_an_unknown_cluster_stays_functional():
    """The project's basic wager (main document 3.5): a device type this
    tool has never seen still works. If this were wrong, a foreign
    device would sit silent - without anyone noticing that something is
    missing."""
    assert is_functional(SignalRef(1, 4711, 99, _KIND), _PLUG_TYPES) is True


def test_identify_groups_and_descriptor_are_never_functional():
    for cluster_id in (3, 4, 29):
        assert is_functional(SignalRef(1, cluster_id, 0, _KIND), _PLUG_TYPES) is False


def test_an_endpoint_without_a_declared_type_counts_as_an_application_endpoint():
    """When in doubt, one input too many, never a missing value."""
    assert is_functional(SignalRef(9, 4711, 0, _KIND), _PLUG_TYPES) is True


def test_events_of_a_known_cluster_stay_functional():
    """A discarded event would be a button press that never arrives in
    Loxone - the very first requirement of this project."""
    for event_id in (1, 2, 3, 4, 5, 6):
        ref = SignalRef(1, 59, event_id, SignalKind.EVENT)
        assert is_functional(ref, _BUTTON_TYPES) is True
```

Add `_KIND = SignalKind.ATTRIBUTE` at the top of the file.

- [ ] **Step 6: Run the test, confirm the failure**

Run: `uv run pytest tests/profiles/test_relevance.py -k is_functional -v`
Expected: FAIL — `ImportError: cannot import name 'is_functional'`

- [ ] **Step 7: Implement `is_functional`**

Add to `src/loxmatter/profiles/relevance.py`:

```python
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.table import knows_cluster, names_element

# Administrative on every endpoint, regardless of device type: Identify
# (blinking for identification), Groups (Matter group management), and
# the descriptor itself. None of these have any meaning for home
# automation.
BOILERPLATE_CLUSTERS: frozenset[int] = frozenset({3, 4, DESCRIPTOR_CLUSTER_ID})

# Clusters that are still wanted on an administrative endpoint - but
# only if the device also declares the corresponding application device
# type there. The battery level is the case that makes this necessary.
UTILITY_ENDPOINT_KEEP_CLUSTERS: dict[int, int] = {
    47: POWER_SOURCE_DEVICE_TYPE,  # PowerSource
}


def is_functional(ref: SignalRef, device_types: dict[int, frozenset[int]]) -> bool:
    """Whether this signal is wanted by default (design 2026-09-03, 4).

    Three layers, in this order:

    1. Boilerplate clusters are never wanted, on any endpoint.
    2. On an administrative endpoint (Root Node or OTA Requestor), only
       what belongs to an application device type also declared there
       is wanted.
    3. On an application endpoint, everything is wanted - except for a
       cluster the profile table knows: there, only the named elements.
       An unknown cluster remains fully wanted (main document 3.5).

    Events are not subject to layer 3: they are named in the table
    anyway, and a discarded event would be a button press that never
    arrives in Loxone.
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

- [ ] **Step 8: Run the test, confirm success**

Run: `uv run pytest tests/profiles/test_relevance.py -v`
Expected: PASS

- [ ] **Step 9: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Relevanz aus dem Geraetetyp je Endpunkt ableiten"
```

---

### Task 3: PowerSource into the profile table

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Test: `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: `lookup` from `table.py`, unchanged.
- Produces: no new signature — table data only.

- [ ] **Step 1: Establish the attribute number and unit**

```bash
uv run python -c "
import chip.clusters.Objects as O
print('BatPercentRemaining ->', O.PowerSource.Attributes.BatPercentRemaining.attribute_id)
"
```

Expected: `12`.

The **unit** is not in the SDK. The Matter Application Cluster Specification states `BatPercentRemaining` in half-percent (value range 0–200). Establish this and write the source as a comment on the entry. If your source differs, your source takes precedence — report the discrepancy; a wrong factor permanently shows double or half the charge level in Loxone.

- [ ] **Step 2: Write the failing test**

```python
def test_the_battery_level_is_named_and_scaled_to_percent():
    """Matter counts BatPercentRemaining in half-percent (0-200). Without
    the factor, Loxone would show 200 % at a full battery."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 190)
    assert profile.slug == "battery"
    assert profile.unit == "%"
    assert scale_factor(ref) == pytest.approx(0.5)
```

- [ ] **Step 3: Run the test, confirm the failure**

Run: `uv run pytest tests/profiles/test_table.py -k battery -v`
Expected: FAIL — `assert 'c47_a12' == 'battery'`

- [ ] **Step 4: Add the table entry**

In `src/loxmatter/profiles/clusters.yaml`, inserted in ascending cluster order (i.e. between 8 and 59):

```yaml
  47:
    name: powersource
    attributes:
      # BatPercentRemaining. Matter counts in half-percent (0-200) per
      # the Matter Application Cluster Specification - hence 0.5.
      # Attribute ID established against
      # chip.clusters.Objects.PowerSource.Attributes.
      #
      # Only this one of 37 attributes is named, and that is the fine
      # selection from the design (4.3): PowerSource additionally
      # carries charge states, battery chemistry, ANSI designations, and
      # error lists. Anyone who needs one of those enables it in the
      # expert block.
      12: {slug: battery, unit: "%", scale: 0.5}
```

- [ ] **Step 5: Run the test, confirm success**

Run: `uv run pytest tests/profiles/test_table.py -k battery -v`
Expected: PASS

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Batteriestand benennen und auf Prozent skalieren"
```

---

### Task 4: Names from the SDK catalog

Without this step, the battery level of a device the profile table
doesn't know is called `c47_a12` forever — even though the name lives
in a dependency this project installs anyway.

**Files:**
- Modify: `src/loxmatter/profiles/table.py`
- Create: `src/loxmatter/profiles/catalog.py`
- Modify: `src/loxmatter/model/store.py` (title at creation time)
- Test: `tests/profiles/test_catalog.py`, `tests/profiles/test_table.py`

**Interfaces:**
- Consumes: `chip.clusters.Objects` (already a dependency via `python-matter-server`), `SignalRef`, `SignalKind`.
- Produces:
  - `element_name(ref: SignalRef) -> str | None` in `catalog.py`
  - `Profile` gains the field `title: str`; `slug` remains unchanged as the key material

**The separation this is about:** `slug` forms the signal key and is
therefore immutable (main document 6.2). `title` is pure display. The
SDK catalog feeds **only the title**. A device commissioned before this
change keeps `d1_0_c47_a12` and is henceforth called "BatPercentRemaining";
one commissioned afterward gets the same key. The catalog must not move
any key.

- [ ] **Step 1: Explore the catalog**

```bash
uv run python -c "
import chip.clusters.Objects as O, inspect
cl = [c for _, c in inspect.getmembers(O, inspect.isclass) if hasattr(c, 'id') and hasattr(c, 'Attributes')]
print('Cluster:', len(cl))
ps = [c for c in cl if c.id == 47][0]
print(ps.__name__, [(n, a.attribute_id) for n, a in inspect.getmembers(ps.Attributes, inspect.isclass) if hasattr(a, 'attribute_id')][:5])
"
```

Expected: 140 clusters; PowerSource with attributes including
`attribute_id`. Also check whether events are equally discoverable
(`ps.Events`), and go by what you see — not by this description.

- [ ] **Step 2: Write the failing test**

```python
"""Attribute names from the chip SDK's cluster catalog."""

from __future__ import annotations

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.catalog import element_name


def test_a_standard_attribute_gets_its_specification_name():
    """c47_a12 is called BatPercentRemaining in the standard. The name
    lives in a dependency this project installs anyway - maintaining it
    by hand would be work for nothing."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert element_name(ref) == "BatPercentRemaining"


def test_an_unknown_cluster_has_no_name():
    assert element_name(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is None


def test_an_unknown_attribute_of_a_known_cluster_has_no_name():
    assert element_name(SignalRef(1, 6, 9999, SignalKind.ATTRIBUTE)) is None


def test_the_catalog_is_read_once():
    """Searching 140 clusters with all attributes for every signal would
    be noticeable at 159 signals per device. The build belongs behind a
    cache."""
    first = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    second = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    assert first == second == "BatPercentRemaining"
```

And in `tests/profiles/test_table.py`:

```python
def test_a_generic_signal_keeps_its_slug_but_gains_a_readable_title():
    """The key stays generic - it is the wiring in Loxone and must never
    move. Only the display becomes readable."""
    ref = SignalRef(0, 51, 1, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 3)
    assert profile.slug == "c51_a1"
    assert profile.title != "c51_a1"


def test_a_table_named_signal_uses_its_own_name_for_both():
    """Where the project's own table knows something, it wins: `onoff`
    is more descriptive than `OnOff`, and the SDK doesn't know the unit
    anyway."""
    profile = lookup(SignalRef(1, 6, 0, SignalKind.ATTRIBUTE), True)
    assert profile.slug == "onoff"
    assert profile.title == "onoff"


def test_a_signal_the_catalog_does_not_know_falls_back_to_the_slug():
    ref = SignalRef(1, 4711, 3, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 1)
    assert profile.slug == "c4711_a3"
    assert profile.title == "c4711_a3"
```

- [ ] **Step 3: Run the test, confirm the failure**

Run: `uv run pytest tests/profiles/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.profiles.catalog'`

- [ ] **Step 4: Implement the catalog**

`src/loxmatter/profiles/catalog.py`: builds a mapping
`(cluster_id, element_id, kind) -> name` once (`functools.cache`) from
`chip.clusters.Objects` and provides `element_name`. Import errors and
unexpected shapes must not raise an exception — the catalog is a
display improvement, not an operational resource: if it fails, the
generic names remain, and the tool keeps running. Write that into the
docstring.

`Profile` gains `title: str`. In `lookup`:
- table entry present → `slug` and `title` both from the table,
- otherwise → `slug` generic as before, `title = element_name(ref) or slug`.

In `store.register_signals`, the title column is populated at
**creation time** from `profile.title` instead of `profile.slug`. The
UPDATE branch continues to leave `title` untouched — once `set_title`
has set it, it belongs to the user.

- [ ] **Step 5: Run the test, confirm success**

Run: `uv run pytest tests/profiles/ -v`
Expected: PASS

- [ ] **Step 6: Prove that no key moves**

```bash
uv run pytest tests/model/ -q
```

Expected: PASS. No existing key test may change — if one does break,
the `slug`/`title` separation is not clean, and **the test is not to be
adjusted**.

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Attributnamen aus dem SDK-Katalog fuer die Anzeige"
```

---

### Task 5: Numbers from structures

With this, the kWh meter reading arrives in Loxone for the first time ever.

**Files:**
- Modify: `src/loxmatter/profiles/table.py`
- Modify: `src/loxmatter/profiles/clusters.yaml`
- Modify: `src/loxmatter/loxone/values.py`
- Test: `tests/profiles/test_table.py`, `tests/loxone/test_values.py`

**Interfaces:**
- Consumes: `classify(value)` (kept unchanged), `scale_factor(ref)`, `SignalRef`.
- Produces:
  - `struct_field(ref: SignalRef) -> int | None`
  - `struct_member(ref: SignalRef, raw: object) -> object` — the value that is classified and computed on; without `field`, unchanged `raw`
  - `lookup` returns the exportability of the **element** for a signal with `field`, not that of the struct

- [ ] **Step 1: Establish the shape of a struct**

```bash
uv run python -c "
import chip.clusters.Objects as O
S = O.ElectricalEnergyMeasurement.Structs.EnergyMeasurementStruct
for d in S.descriptor.Fields: print(d.Tag, d.Label)
print('CumulativeEnergyImported ->', O.ElectricalEnergyMeasurement.Attributes.CumulativeEnergyImported.attribute_id)
"
```

Expected: tag 0 = `energy`, attribute 1.

And how matter-server **serializes** a struct — this is the point where an implementation using `value["energy"]` would fail:

```bash
uv run python -c "
import json
d = json.load(open('tests/fixtures/nodes/ikea_grillplats_plug.json'))
a = d.get('attributes') or d
print({k: v for k, v in a.items() if k.endswith('/29/0')})
"
```

Expected: `{'0/29/0': [{'0': 18, '1': 1}, ...]}` — **field tag as a string**, not as a name.

The unit of `EnergyMeasurementStruct.energy` is not in the SDK. The Matter Application Cluster Specification states it in mWh; 1 kWh = 1e6 mWh, hence `scale: 1.0e-6`. Establish this and report any discrepancy.

- [ ] **Step 2: Write the failing test**

In `tests/profiles/test_table.py`:

```python
_ENERGY = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)


def test_a_struct_member_becomes_an_analog_signal():
    """Matter delivers the meter reading as a struct of value and
    timestamps. Without pulling it out, it falls through as 'not
    mappable' - and that's the value someone buys a metering plug
    for."""
    raw = {"0": 12_345_678, "1": 1_700_000_000, "2": 1_700_003_600}
    assert lookup(_ENERGY, raw).exportability is Exportability.ANALOG
    assert lookup(_ENERGY, raw).slug == "energy_imported"


def test_a_struct_without_the_named_member_stays_unexportable():
    """Do not guess. A made-up number on a real energy component would
    be worse than a missing value."""
    assert lookup(_ENERGY, {"1": 1_700_000_000}).exportability is Exportability.NONE


def test_a_struct_member_that_is_not_a_number_stays_unexportable():
    assert lookup(_ENERGY, {"0": "viel"}).exportability is Exportability.NONE


def test_a_null_value_stays_unexportable_even_with_a_field():
    assert lookup(_ENERGY, None).exportability is Exportability.NONE


def test_an_integer_key_is_accepted_as_well_as_a_string_key():
    """The string is what matter-server delivers today; a different
    serialization of the same struct would be just as plausible with a
    number."""
    assert lookup(_ENERGY, {0: 5_000_000}).exportability is Exportability.ANALOG


def test_a_cluster_without_a_field_entry_still_sees_the_whole_value():
    """Only a cluster the table knows may name an element. An unknown
    struct stays unknown."""
    ref = SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)
    assert lookup(ref, {"0": 5}).exportability is Exportability.NONE
```

In `tests/loxone/test_values.py`:

```python
def test_the_energy_counter_arrives_in_kilowatt_hours():
    """Matter counts in mWh, Loxone wants kWh (main document 7.3)."""
    ref = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)
    raw = {"0": 2_500_000_000, "1": 1_700_000_000}
    assert to_loxone_value(ref, raw) == pytest.approx(2500.0)


def test_a_struct_without_the_named_member_yields_none_at_runtime():
    """Runtime and decomposition must make the same decision - otherwise
    the UI reports a value the export doesn't know."""
    ref = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)
    assert to_loxone_value(ref, {"1": 1_700_000_000}) is None
```

- [ ] **Step 3: Run the test, confirm the failure**

Run: `uv run pytest tests/profiles/test_table.py tests/loxone/test_values.py -k "struct or energy" -v`
Expected: FAIL — the exportability is `NONE` instead of `ANALOG`

- [ ] **Step 4: Implement `struct_member` and adjust `lookup`**

In `src/loxmatter/profiles/table.py`:

```python
def struct_field(ref: SignalRef) -> int | None:
    """The field number to pull out of a struct - or None.

    Only for attributes of a cluster the table knows and whose entry
    carries a `field`.
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
    """The value that is classified and computed on.

    Without a `field` entry, unchanged `raw`. With `field`, the named
    element of the struct - and `None` if the value is not a struct or
    the element is missing. Then the signal stays unexportable; it is
    NOT guessed (design 2026-09-03, 5).

    matter-server delivers structs as a dictionary with the field tag as
    a string (`{"0": ...}`); the number is accepted as well.

    This one function is the shared source for `lookup` (classification
    during commissioning) and `loxone.values.to_loxone_value` (runtime).
    Two copies would drift apart and let the UI report a value the
    export doesn't know.
    """
    field = struct_field(ref)
    if field is None:
        return raw
    if not isinstance(raw, dict):
        return None
    return raw.get(str(field), raw.get(field))
```

`lookup` gains one line: the classification goes through `struct_member`.

```python
    if entry:
        return Profile(
            slug=entry["slug"],
            title=entry["slug"],
            unit=entry.get("unit", ""),
            exportability=classify(struct_member(ref, value)),
        )
```

The field `title` comes from Task 4; take the version of `lookup` that
resulted there and change only the exportability line.

The generic branch below stays `classify(value)` — without a table entry there is no `field`.

In `src/loxmatter/loxone/values.py`, in `to_loxone_value`, right at the start:

```python
    raw = struct_member(ref, raw)
```

Add `struct_member` to the import.

- [ ] **Step 5: Add the table entry**

In `src/loxmatter/profiles/clusters.yaml`, cluster 145:

```yaml
  145:
    name: energy
    attributes:
      # CumulativeEnergyImported/-Exported. Matter delivers both as
      # EnergyMeasurementStruct - value plus timestamps -, not as a
      # number. `field: 0` is EnergyMeasurementStruct.energy,
      # established against chip.clusters.Objects; the TIMESTAMPS are
      # deliberately left out, they have no use in Loxone.
      #
      # Matter counts in mWh (Matter Application Cluster Specification),
      # Loxone wants kWh (main document 7.3): 1 kWh = 1e6 mWh.
      1: {slug: energy_imported, field: 0, unit: "kWh", scale: 1.0e-6}
      2: {slug: energy_exported, field: 0, unit: "kWh", scale: 1.0e-6}
```

Existing entries for 145 are to be replaced, not duplicated — first check with `grep -n "145:" -A 6 src/loxmatter/profiles/clusters.yaml` what's already there, and keep existing slugs so that no key moves.

- [ ] **Step 6: Run the test, confirm success**

Run: `uv run pytest tests/profiles/test_table.py tests/loxone/test_values.py -v`
Expected: PASS

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(profiles): Zaehlerstand aus der Energie-Struktur ziehen"
```

---

### Task 6: The default value in storage

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `is_functional`, `device_types_by_endpoint` (Tasks 1–2), `is_exportable` (unchanged).
- Produces: `register_signals` sets `exported` at **creation time** to `is_exportable(...) and is_functional(...)`. Signature unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_a_freshly_registered_plug_exports_only_its_meaningful_values(tmp_path):
    """The goal of this entire design, on the real device: five values
    that mean something, instead of 109 that are technically
    mappable."""
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
    """The case that shows whether the rule is too greedy: all six
    events of both rockers must get through, plus the battery level."""
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
    """Not deleted, only deselected: the expert block should be able to
    enable it, without the device having to be recommissioned."""
    store = Store(tmp_path / "s.sqlite")
    snapshot = _fixture("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    counters = [s for s in store.signals(device_id) if s.ref.cluster_id == 53]
    assert counters, "Thread-Zaehler sollen weiterhin gespeichert werden"
    assert all(not s.exported for s in counters)
    assert all(s.exportability is Exportability.ANALOG for s in counters[:1])
```

Check how existing tests in this file load a snapshot (helper function `_fixture` or fixture); use the same approach instead of introducing a second one.

- [ ] **Step 2: Run the test, confirm the failure**

Run: `uv run pytest tests/model/test_store.py -k freshly_registered -v`
Expected: FAIL — the set contains 109 keys instead of 5

- [ ] **Step 3: Adjust `register_signals`**

Before the loop over `extract_signals(snapshot)`:

```python
        device_types = device_types_by_endpoint(snapshot)
```

And the line that determines `exported`:

```python
# Two questions, two answers (design 2026-09-03, 3):
# `is_exportable` says whether the value fits onto a
# Loxone input at all; `is_functional`, whether anyone
# wants it by default. A Thread radio counter is the
# first and not the second.
#
# Only at CREATION TIME: the UPDATE branch above continues
# to leave `exported` untouched once a signal is known -
# from then on the value belongs to the user.
exported = is_exportable(profile.exportability) and is_functional(ref, device_types)
```

- [ ] **Step 4: Run the test, confirm success**

Run: `uv run pytest tests/model/test_store.py -v`
Expected: PASS. Existing tests in this file that assumed "everything exportable is exported" are **to be adjusted, not deleted** — and the adjustment is to be justified in the commit.

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(store): nur relevante Signale sind ab Werk exportiert"
```

---

### Task 7: Migration to schema v3

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: the `_MIGRATIONS` pattern from `_migrate_to_v1`/`_migrate_to_v2`; `lookup`, `is_exportable`, `is_functional`, `device_types_by_endpoint`.
- Produces: `_SCHEMA_VERSION = 3`, `_migrate_to_v3(db)`.

**The problem this task solves:** title, unit, and exportability live **in the row**. The table extensions from Tasks 3 through 5 therefore never reach an already-stored signal — the battery level would be called `c47_a12` forever, the meter reading would stay "not mappable" forever.

**The boundary:** the key stays. A device commissioned before the update keeps `d2_0_c47_a12` and is called "battery" from then on; one commissioned afterward gets `d2_0_battery`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_migration_never_changes_a_key(tmp_path):
    """The iron rule (main document 6.2). A renamed key would be a
    silently dead function block in someone else's config - not an
    error anyone would see from outside."""
    path = tmp_path / "s.sqlite"
    keys_before = _build_store_at_schema_v2(path)
    store = Store(path)  # opens and migrates
    assert {s.key for s in store.signals(1)} == keys_before


def test_the_migration_refreshes_title_and_unit_from_the_table(tmp_path):
    """Without this step, a correction in clusters.yaml would never
    reach an already-stored signal."""
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
    """If the re-derivation fails for a row, it stays unchanged - no
    abort, no half-migrated database (design 8)."""
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path, extra_row=("d1_9_kaputt", 9, 4711, 0, "attribute"))
    store = Store(path)
    assert any(s.key == "d1_9_kaputt" for s in store.signals(1))
```

`_build_store_at_schema_v2` is a helper function **in this test file**: it creates a database following the old schema (`PRAGMA user_version = 2`), writes a device and the signals of the button snapshot with `exported = 1` for everything exportable, and returns the key set. Follow the existing helper functions in this file for v1 and v2 — they already exist and show the pattern.

**Important for the migration test:** the re-derivation needs the device types, which live only in the snapshot, not in the database. Decide where `_migrate_to_v3` gets them from, and justify it:
- either store the device types alongside `register_device`/`register_signals` (a new column, then the migration is self-sufficient),
- or derive the rule for existing rows without a snapshot from the stored cluster/endpoint numbers (then a substitute rule for "administrative endpoint" is needed, and it must be justified).

The first path is the more honest one, if it can be done without contortion. Whichever you choose: write it into the docstring of `_migrate_to_v3`, and write down what the migration **cannot** do.

- [ ] **Step 2: Run the test, confirm the failure**

Run: `uv run pytest tests/model/test_store_migration.py -k migration -v`
Expected: FAIL — title stays `c47_a12`

- [ ] **Step 3: Implement the migration**

`_SCHEMA_VERSION` to `3` and an entry in `_MIGRATIONS`. The docstring of `_migrate_to_v3` must state:
- why retroactively and not just for new devices (two rule sets would be unexplainable to anyone, and the difference would hinge on the commissioning date),
- that the key stays untouched and what consequence that has (two keys for the same value on devices commissioned before/after),
- that a single row that cannot be re-derived stays unchanged instead of aborting the migration.

The migration runs, like its predecessors, inside the transaction that also writes `PRAGMA user_version`.

- [ ] **Step 4: Run the test, confirm success**

Run: `uv run pytest tests/model/test_store_migration.py -v`
Expected: PASS

- [ ] **Step 5: Check against the real database**

There is a real database from operation on the Raspberry Pi (schema v2, two devices). It is **not** to be touched, and the Pi is **not** to be contacted. Instead: use `_build_store_at_schema_v2` to build a database from **both** checked-in snapshots, migrate it, and print the result:

```bash
uv run python -c "
# ... build a database from both snapshots following the old schema, then:
# for d in store.devices(): print(d.label, len([s for s in store.signals(d.id) if s.exported]))
"
```

Expected: 5 and 17. Paste the actual output into the commit message or the report.

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(store): Bestandsgeraete neu einstufen, ohne einen Schluessel zu aendern"
```

---

### Task 8: Relevance in the API and the UI

**Files:**
- Modify: `src/loxmatter/api/models.py`, `src/loxmatter/api/devices.py`, `src/loxmatter/api/export.py`
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`
- Test: `tests/api/test_devices.py`, `tests/api/test_export_api.py`, `tests/api/test_web.py`

**Interfaces:**
- Consumes: `SignalOut` (fields `key`, `path`, `kind`, `title`, `unit`, `value`, `exportable`, `reason`, `exported`), `_signal_out(signal, values)`.
- Produces: `SignalOut` gains the field `functional: bool`.

- [ ] **Step 1: Write the failing API test**

```python
async def test_the_signal_payload_says_whether_a_signal_is_functional(api):
    """The UI must be able to separate the two blocks, without
    rebuilding the rule a second time in JavaScript."""
    client, _, _ = api
    rows = (await client.get("/api/devices/1/signals")).json()
    onoff = next(r for r in rows if r["key"].endswith("_onoff"))
    counter = next(r for r in rows if "_c53_" in r["key"])
    assert onoff["functional"] is True
    assert counter["functional"] is False
```

- [ ] **Step 2: Run the test, confirm the failure**

Run: `uv run pytest tests/api/test_devices.py -k functional -v`
Expected: FAIL — `KeyError: 'functional'`

- [ ] **Step 3: Add the field**

`SignalOut` gains `functional: bool`. It is populated in `_signal_out`.

**Decide and justify** where `_signal_out` gets the device types from: the relevance rule needs them, but `_signal_out` only sees a `StoredSignal`. It's natural to store the result in the row (then it falls out of Task 7 anyway) instead of recomputing it on every request. Whichever you choose: **one** source, no second reconstruction of the rule.

- [ ] **Step 4: Run the test, confirm success**

Run: `uv run pytest tests/api/test_devices.py -v`
Expected: PASS

- [ ] **Step 5: Extend the export preview**

`GET /api/export/preview` additionally states, per device, how many signals are hidden as expert. Test:

```python
async def test_the_preview_reports_how_many_signals_are_hidden(api):
    client, _, _ = api
    body = (await client.get("/api/export/preview?bridge_ip=10.0.0.1")).json()
    plug = next(d for d in body["devices"] if d["id"] == 1)
    assert plug["hidden_count"] > 100
```

First check the actual shape of the response (`grep -n "preview" -A 30 src/loxmatter/api/export.py`) and add the field where the other counts live.

- [ ] **Step 6: Rebuild the UI**

In `index.html`, the signal list gains two blocks: **Functional** (expanded) and **Expert** (collapsed, with a count), plus a toggle "Show expert signals". Each signal keeps its export checkbox.

The device tile shows the functional signals instead of `firstSignalsFor(...)` — this resolves the open item from Phase 5's closing review (today it shows NetworkCommissioning and BasicInformation there, so neither on/off nor power). Adjust the label accordingly from "Signals (start of list)" back to something that is accurate again.

`style.css`: the blocks in the existing style, no new color scheme.

A test in `tests/api/test_web.py` that checks the markup — follow the existing tests there that fetch `/static/app.js` and `/` and search the text. The test's docstring must honestly say what it proves and what it doesn't (no browser engine runs).

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(web): funktionale Signale vorn, Experten-Signale zugeklappt"
```

---

### Task 9: Documentation and wrap-up

**Files:**
- Modify: `docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`
- Modify: `docs/superpowers/specs/2026-09-03-signal-selection-design.md`
- Modify: `README.md`

- [ ] **Step 1: Bring the main document up to date**

Section 3.5 (generic decomposition) gains a reference to the new design and the sentence that decomposition unchanged keeps everything — only the export's default value now follows relevance. Section 5 (data model) and 6.6 (non-exportable values) are to be brought up to date: 6.6 currently states "109 of 159 mappable" as the result, without noting that 5 of those are exported.

- [ ] **Step 2: Close open items in the new design**

Section 10 of the design has four open items. Item 1 (establish device type numbers) is done with Task 1 — strike it and note which source you used. Items 2–4 remain, unless something has been decided about them.

- [ ] **Step 3: README**

The section describing the export must say that the functional signals are exported by default, and how to get at the rest.

- [ ] **Step 4: Full check**

```bash
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
```

Expected: everything clean, no test losses compared to the starting point (477) except deliberately adjusted ones.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: Signalauswahl in Hauptdokument und README nachziehen"
```

---

## Completion criteria

The work is done when:

1. `uv run pytest` passes without hardware and without network,
2. the plug from the checked-in snapshot exports `onoff`, `voltage`, `current`, `power`, `energy_imported` **by name** — not just "fewer than before",
3. the button exports both rockers completely, including `multipress` and the battery level,
4. an unknown cluster on an application endpoint remains fully intact,
5. the migration proves not a single key changes,
6. a signal the profile table doesn't know keeps its generic **key**, but carries a readable title from the SDK catalog,
7. the device type numbers are established against the Matter Device Library, not taken from this plan.

**Not part of this work:** a user-editable blocklist, the missing system-check checks (mDNS, dongle, OTBR, Thread network), and the IPv6 check that requires global IPv6 where Thread uses ULA — that is a separate bug and belongs in its own round.
