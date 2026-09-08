# Phase 1: Matter Adapter and Signal Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI that, for a real Matter device, lists every attribute and every event and reports what it *could not* decompose — so the base assumption from Spec 3.5 is confirmed or refuted.

**Architecture:** A package `loxmatter.matter` with four modules with no cycles between them: `paths` (path parsing, pure functions), `models` (immutable data classes), `discovery` (decomposing a node snapshot into signals, pure), `client` (the only part with I/O, a thin shell around `matter_server.client.MatterClient`). Decomposition is deliberately separated from the connection: it works on a JSON snapshot and is thus testable against checked-in fixtures of real devices, without hardware and without a network.

**Tech Stack:** Python 3.12, `uv` as package manager, `python-matter-server>=8.1.2`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `typer` for the CLI.

## Global Constraints

From the spec, apply to every task:

- **Tests run without hardware and without network access.** A test that needs a real device is skipped and rots (Spec 10.1).
- **Generic, not curated.** Every readable attribute and every event becomes a signal. Unknown clusters are passed through raw, never discarded (Spec 3.5).
- **German in error messages and logs**, English in identifiers and commit prefixes.
- **All data classes immutable** (`frozen=True`), unless there is a reason against it.
- Target units and formatting (kW, 6 decimal places) are **Phase 3**, not here. This phase delivers raw values.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Project, dependencies, tool configuration |
| `src/loxmatter/matter/paths.py` | Parse attribute paths, know global attribute IDs. Pure functions |
| `src/loxmatter/matter/models.py` | `SignalKind`, `SignalRef`, `NodeSnapshot`. Data only |
| `src/loxmatter/matter/discovery.py` | `extract_signals`, `find_unreported_attributes`. Pure, no I/O |
| `src/loxmatter/matter/client.py` | Connection to matter-server, returns `NodeSnapshot` |
| `src/loxmatter/cli.py` | `loxmatter inspect` |
| `scripts/record_node.py` | Save a node snapshot from real hardware as a fixture |
| `tests/fixtures/nodes/*.json` | Checked-in snapshots of real devices |
| `deploy/testhost/` | Compose file and log of the test environment (Task 6, originally `deploy/testvm/` — moved to the Raspberry Pi because the VM lacked Bluetooth, see the README there) |

---

### Task 1: Project Scaffolding and Path Parsing

The scaffolding is set up here because `paths.py` is the first module that needs it.

**Files:**
- Create: `pyproject.toml`
- Create: `src/loxmatter/__init__.py`
- Create: `src/loxmatter/matter/__init__.py`
- Create: `src/loxmatter/matter/paths.py`
- Test: `tests/matter/test_paths.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `parse_attribute_path(path: str) -> tuple[int, int, int]` — returns `(endpoint, cluster_id, attribute_id)`, raises `ValueError` for anything else
  - `GLOBAL_ATTRIBUTE_IDS: frozenset[int]`
  - `ATTRIBUTE_LIST_ID: int` (`0xFFFB`), `EVENT_LIST_ID: int` (`0xFFFA`)

- [ ] **Step 1: Set up project scaffolding**

`pyproject.toml`:

```toml
[project]
name = "loxmatter"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "python-matter-server>=8.1.2",
    "typer>=0.12",
]

[project.scripts]
loxmatter = "loxmatter.cli:app"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "mypy>=1.11"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true
files = ["src"]
```

Then:

```bash
mkdir -p src/loxmatter/matter tests/matter tests/fixtures/nodes scripts
touch src/loxmatter/__init__.py src/loxmatter/matter/__init__.py
uv sync
```

- [ ] **Step 2: Write the failing test**

`tests/matter/test_paths.py`:

```python
import pytest

from loxmatter.matter.paths import (
    ATTRIBUTE_LIST_ID,
    EVENT_LIST_ID,
    GLOBAL_ATTRIBUTE_IDS,
    parse_attribute_path,
)


def test_parses_endpoint_cluster_attribute():
    assert parse_attribute_path("1/6/0") == (1, 6, 0)


def test_parses_multi_digit_values():
    assert parse_attribute_path("2/1030/65531") == (2, 1030, 65531)


@pytest.mark.parametrize("bad", ["1/6", "1/6/0/9", "", "a/6/0", "1//0"])
def test_rejects_malformed_paths(bad):
    with pytest.raises(ValueError, match="Attributpfad"):
        parse_attribute_path(bad)


def test_global_attribute_ids_cover_the_matter_reserved_range():
    assert ATTRIBUTE_LIST_ID == 0xFFFB
    assert EVENT_LIST_ID == 0xFFFA
    assert GLOBAL_ATTRIBUTE_IDS == {0xFFF8, 0xFFF9, 0xFFFA, 0xFFFB, 0xFFFC, 0xFFFD}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.matter.paths'`

- [ ] **Step 4: Write minimal implementation**

`src/loxmatter/matter/paths.py`:

```python
"""Parse attribute paths from matter-server.

matter-server addresses attributes as "<endpoint>/<cluster>/<attribute>",
e.g. "1/6/0" for OnOff.OnOff on endpoint 1.
"""

from __future__ import annotations

# Global attributes per the Matter specification. They describe the device
# instead of carrying a measured value, and do not become Loxone signals.
GENERATED_COMMAND_LIST_ID = 0xFFF8
ACCEPTED_COMMAND_LIST_ID = 0xFFF9
EVENT_LIST_ID = 0xFFFA
ATTRIBUTE_LIST_ID = 0xFFFB
FEATURE_MAP_ID = 0xFFFC
CLUSTER_REVISION_ID = 0xFFFD

GLOBAL_ATTRIBUTE_IDS: frozenset[int] = frozenset(
    {
        GENERATED_COMMAND_LIST_ID,
        ACCEPTED_COMMAND_LIST_ID,
        EVENT_LIST_ID,
        ATTRIBUTE_LIST_ID,
        FEATURE_MAP_ID,
        CLUSTER_REVISION_ID,
    }
)


def parse_attribute_path(path: str) -> tuple[int, int, int]:
    """Decompose "1/6/0" into (endpoint, cluster_id, attribute_id)."""
    parts = path.split("/")
    if len(parts) != 3:
        raise ValueError(f"unerwarteter Attributpfad: {path!r}")
    try:
        endpoint, cluster_id, attribute_id = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"unerwarteter Attributpfad: {path!r}") from exc
    return endpoint, cluster_id, attribute_id
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_paths.py -v`
Expected: PASS, 8 tests

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/loxmatter tests/matter/test_paths.py
git commit -m "feat(matter): Projektgerüst und Attributpfad-Parsing"
```

---

### Task 2: Data Model for Signals

**Files:**
- Create: `src/loxmatter/matter/models.py`
- Test: `tests/matter/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `SignalKind` — `str` enum with `ATTRIBUTE = "attribute"`, `EVENT = "event"`
  - `SignalRef(endpoint: int, cluster_id: int, element_id: int, kind: SignalKind)` — frozen, sortable, with `.path -> str`
  - `NodeSnapshot(node_id: int, vendor_name: str, product_name: str, unique_id: str, attributes: dict[str, object])` — frozen, with `.from_raw(node_id: int, raw: Mapping[str, object]) -> NodeSnapshot`

- [ ] **Step 1: Write the failing test**

`tests/matter/test_models.py`:

```python
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef


def test_signal_ref_renders_matter_path():
    ref = SignalRef(endpoint=1, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE)
    assert ref.path == "1/6/0"


def test_signal_refs_sort_by_endpoint_then_cluster_then_element():
    unsorted = [
        SignalRef(2, 6, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 1030, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 6, 16, SignalKind.ATTRIBUTE),
        SignalRef(1, 6, 0, SignalKind.ATTRIBUTE),
    ]
    assert [r.path for r in sorted(unsorted)] == ["1/6/0", "1/6/16", "1/1030/0", "2/6/0"]


def test_signal_ref_is_hashable_and_frozen():
    ref = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    assert len({ref, SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)}) == 1


def test_attribute_and_event_on_same_path_are_distinct():
    attribute = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    event = SignalRef(1, 6, 0, SignalKind.EVENT)
    assert attribute != event
    assert len({attribute, event}) == 2


def test_node_snapshot_reads_basic_information_cluster():
    raw = {
        "attributes": {
            "0/40/1": "IKEA of Sweden",
            "0/40/3": "TRADFRI bulb",
            "0/40/18": "ABC123",
            "1/6/0": True,
        }
    }
    snapshot = NodeSnapshot.from_raw(node_id=12, raw=raw)
    assert snapshot.node_id == 12
    assert snapshot.vendor_name == "IKEA of Sweden"
    assert snapshot.product_name == "TRADFRI bulb"
    assert snapshot.unique_id == "ABC123"
    assert snapshot.attributes["1/6/0"] is True


def test_node_snapshot_tolerates_missing_basic_information():
    snapshot = NodeSnapshot.from_raw(node_id=3, raw={"attributes": {"1/6/0": False}})
    assert snapshot.vendor_name == ""
    assert snapshot.product_name == ""
    assert snapshot.unique_id == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.matter.models'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/matter/models.py`:

```python
"""Immutable snapshot of what matter-server knows about a device."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# BasicInformation cluster on endpoint 0.
_VENDOR_NAME_PATH = "0/40/1"
_PRODUCT_NAME_PATH = "0/40/3"
_UNIQUE_ID_PATH = "0/40/18"


class SignalKind(str, Enum):
    ATTRIBUTE = "attribute"
    EVENT = "event"


@dataclass(frozen=True, order=True)
class SignalRef:
    """Reference to exactly one data source of a device.

    An attribute and an event can carry the same numbers and still be
    different things — `kind` therefore belongs to the identity.
    """

    endpoint: int
    cluster_id: int
    element_id: int
    kind: SignalKind

    @property
    def path(self) -> str:
        return f"{self.endpoint}/{self.cluster_id}/{self.element_id}"


@dataclass(frozen=True)
class NodeSnapshot:
    node_id: int
    vendor_name: str
    product_name: str
    unique_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, node_id: int, raw: Mapping[str, Any]) -> NodeSnapshot:
        attributes: Mapping[str, Any] = raw.get("attributes") or {}

        def text(path: str) -> str:
            value = attributes.get(path)
            return value if isinstance(value, str) else ""

        return cls(
            node_id=node_id,
            vendor_name=text(_VENDOR_NAME_PATH),
            product_name=text(_PRODUCT_NAME_PATH),
            unique_id=text(_UNIQUE_ID_PATH),
            attributes=dict(attributes),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_models.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/matter/models.py tests/matter/test_models.py
git commit -m "feat(matter): Datenmodell für Signale und Node-Abbilder"
```

---

### Task 3: Decomposing a Node Snapshot into Signals

The centerpiece of the phase. `extract_signals` answers "what values does this device have",
`find_unreported_attributes` answers "and which ones did we miss" — the second
function is the actual touchstone for Spec 3.5.

**Files:**
- Create: `src/loxmatter/matter/discovery.py`
- Test: `tests/matter/test_discovery.py`

**Interfaces:**
- Consumes: `parse_attribute_path`, `GLOBAL_ATTRIBUTE_IDS`, `EVENT_LIST_ID`, `ATTRIBUTE_LIST_ID`, `FEATURE_MAP_ID` from Task 1; `SignalRef`, `SignalKind`, `NodeSnapshot` from Task 2
- Produces:
  - `extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]` — sorted, attributes and events
  - `find_unreported_attributes(snapshot: NodeSnapshot) -> list[SignalRef]` — attributes the device names in its `AttributeList` but for which no value is present
  - `find_unparsable_paths(snapshot: NodeSnapshot) -> list[str]` — paths where parsing failed
  - `FEATURE_MAP_EVENTS: dict[int, tuple[_FeatureEventRule, ...]]` — cluster-specific knowledge of which events a FeatureMap implies

> **Addendum, see Task 7:** The code below already shows the corrected state.
> Task 3 was originally implemented without `FEATURE_MAP_EVENTS` — pure
> EventList derivation, 10 tests. Validation against real devices in Task 7
> (2026-09-01) showed that neither of the two IKEA devices checked carries
> the EventList; a button demonstrably sending button presses delivered
> zero events through it. The fix — a second, FeatureMap-based
> event source — therefore only landed in `discovery.py` afterward (commit
> `6af2de7`, see Task 7 and Spec 3.5/6.3). This section was subsequently
> updated to the delivered state so it does not present the long-superseded
> EventList-only version as current.

- [ ] **Step 1: Write the failing test**

`tests/matter/test_discovery.py`:

```python
from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef


def snapshot(attributes: dict[str, object]) -> NodeSnapshot:
    return NodeSnapshot.from_raw(node_id=1, raw={"attributes": attributes})


def test_every_non_global_attribute_becomes_a_signal():
    signals = extract_signals(snapshot({"1/6/0": True, "1/8/0": 254}))
    assert signals == [
        SignalRef(1, 6, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 8, 0, SignalKind.ATTRIBUTE),
    ]


def test_global_attributes_are_not_signals():
    signals = extract_signals(
        snapshot({"1/6/0": True, "1/6/65533": 6, "1/6/65532": 0, "1/6/65531": [0]})
    )
    assert signals == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]


def test_event_list_produces_event_signals():
    signals = extract_signals(snapshot({"1/59/65530": [0, 1, 2]}))
    assert signals == [
        SignalRef(1, 59, 0, SignalKind.EVENT),
        SignalRef(1, 59, 1, SignalKind.EVENT),
        SignalRef(1, 59, 2, SignalKind.EVENT),
    ]


def test_empty_or_absent_event_list_produces_nothing():
    assert extract_signals(snapshot({"1/59/65530": []})) == []
    assert extract_signals(snapshot({"1/59/65530": None})) == []


def test_unknown_cluster_is_still_extracted():
    """Spec 3.5: profiles/ is enrichment, not a gatekeeper."""
    signals = extract_signals(snapshot({"1/64999/7": 42}))
    assert signals == [SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)]


def test_signals_are_sorted_deterministically():
    signals = extract_signals(snapshot({"2/6/0": True, "1/1030/0": 1, "1/6/0": False}))
    assert [s.path for s in signals] == ["1/6/0", "1/1030/0", "2/6/0"]


def test_finds_attributes_the_device_claims_but_did_not_report():
    # AttributeList (65531) names 0 and 16, only 0 was delivered.
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 16], "1/6/0": True}))
    assert missing == [SignalRef(1, 6, 16, SignalKind.ATTRIBUTE)]


def test_reports_nothing_missing_when_device_is_complete():
    assert find_unreported_attributes(snapshot({"1/6/65531": [0], "1/6/0": True})) == []


def test_global_attributes_are_not_counted_as_missing():
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 65533], "1/6/0": True}))
    assert missing == []


def test_unparsable_paths_are_collected_not_raised():
    snap = snapshot({"kaputt": 1, "1/6/0": True})
    assert find_unparsable_paths(snap) == ["kaputt"]
    assert extract_signals(snap) == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]


# Second event source: FeatureMap of the Switch cluster (0x003B / 59).
#
# EventList (65530) is optional and, per validation against real IKEA
# devices (see tests/matter/test_real_devices.py), not implemented in
# practice — a button without this derivation delivers zero event signals.
# The conditions below are taken from data_model/1.4/clusters/Switch.xml
# (project-chip/connectedhomeip, machine-readable transcription of the
# Matter Application Cluster Specification): one mandatoryConform per
# event, via feature bits.


def test_feature_map_ms_only_yields_initial_press_only():
    # MS (bit 1) = 0b10 = 2
    signals = extract_signals(snapshot({"1/59/65532": 2}))
    assert signals == [SignalRef(1, 59, 1, SignalKind.EVENT)]  # InitialPress


def test_feature_map_ms_msr_yields_initial_press_and_short_release():
    # MS + MSR = 0b110 = 6
    signals = extract_signals(snapshot({"1/59/65532": 6}))
    assert signals == [
        SignalRef(1, 59, 1, SignalKind.EVENT),  # InitialPress
        SignalRef(1, 59, 3, SignalKind.EVENT),  # ShortRelease
    ]


def test_feature_map_ls_only_yields_switch_latched_only():
    # LS (bit 0) = 1
    signals = extract_signals(snapshot({"1/59/65532": 1}))
    assert signals == [SignalRef(1, 59, 0, SignalKind.EVENT)]  # SwitchLatched


def test_feature_map_30_matches_ikea_bilresa_button():
    # MS + MSR + MSL + MSM = 2 + 4 + 8 + 16 = 30, the real FeatureMap of the
    # IKEA BILRESA button (node 4, endpoints 1 and 2). AS is not set, so
    # MultiPressOngoing fires in addition to MultiPressComplete; LS is not
    # set, so SwitchLatched is absent accordingly.
    signals = extract_signals(snapshot({"1/59/65532": 30}))
    assert signals == [
        SignalRef(1, 59, 1, SignalKind.EVENT),  # InitialPress
        SignalRef(1, 59, 2, SignalKind.EVENT),  # LongPress
        SignalRef(1, 59, 3, SignalKind.EVENT),  # ShortRelease
        SignalRef(1, 59, 4, SignalKind.EVENT),  # LongRelease
        SignalRef(1, 59, 5, SignalKind.EVENT),  # MultiPressOngoing
        SignalRef(1, 59, 6, SignalKind.EVENT),  # MultiPressComplete
    ]


def test_feature_map_msm_with_action_switch_excludes_multi_press_ongoing():
    # MSM + AS = 16 + 32 = 48. MultiPressOngoing requires MSM AND NOT AS.
    signals = extract_signals(snapshot({"1/59/65532": 48}))
    assert signals == [SignalRef(1, 59, 6, SignalKind.EVENT)]  # MultiPressComplete


def test_feature_map_zero_yields_no_events():
    assert extract_signals(snapshot({"1/59/65532": 0})) == []


def test_feature_map_is_ignored_for_clusters_without_a_table_entry():
    """The FeatureMap derivation is cluster-specific knowledge — for clusters
    without an entry in FEATURE_MAP_EVENTS it must not invent anything."""
    assert extract_signals(snapshot({"1/6/65532": 30})) == []


def test_event_list_and_feature_map_are_unioned_and_deduplicated():
    signals = extract_signals(snapshot({"1/59/65530": [1, 3], "1/59/65532": 6}))
    # EventList names {1, 3}, FeatureMap (MS+MSR) also {1, 3} — no duplicate.
    assert signals == [
        SignalRef(1, 59, 1, SignalKind.EVENT),
        SignalRef(1, 59, 3, SignalKind.EVENT),
    ]


def test_feature_map_attribute_itself_is_not_an_attribute_signal():
    signals = extract_signals(snapshot({"1/59/65532": 30}))
    assert all(s.kind is SignalKind.EVENT for s in signals)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.matter.discovery'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/matter/discovery.py`:

```python
"""Decomposes a node snapshot into individual signals.

Purely functional and without I/O — works on a NodeSnapshot and is thus
testable against checked-in fixtures of real devices.

Principle from Spec 3.5: nothing is discarded for attributes. Unknown
clusters become signals just like known ones; enrichment with names
and scaling happens later, in profiles/.

For events, this principle **no longer holds without qualification** — that
is the fix from validation against real devices (Phase 1, 2026-09-01, see
Spec 3.5 and 6.3). The EventList (0xFFFA) is optional in the Matter standard
and, in practice, not implemented by the IKEA devices checked: a button
that demonstrably sends button presses delivered zero events through the
EventList. As a second, cluster-specific source, the events a cluster *can*
generate per the Matter specification are therefore derived from the
FeatureMap (0xFFFC) — the device does not need to list the events itself for
this. This knowledge lives in `FEATURE_MAP_EVENTS`, a table, not in
branching code, so further clusters can be added without touching the
algorithm here. Both sources are unioned and deduplicated (SignalRef
is hashable, the result set handles that automatically).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.matter.paths import (
    ATTRIBUTE_LIST_ID,
    EVENT_LIST_ID,
    FEATURE_MAP_ID,
    GLOBAL_ATTRIBUTE_IDS,
    parse_attribute_path,
)

# Switch cluster (0x003B / 59) — feature bits of the FeatureMap per the
# Matter Application Cluster Specification.
_SWITCH_CLUSTER_ID = 59
_LATCHING_SWITCH = 0x01
_MOMENTARY_SWITCH = 0x02
_MOMENTARY_SWITCH_RELEASE = 0x04
_MOMENTARY_SWITCH_LONG_PRESS = 0x08
_MOMENTARY_SWITCH_MULTI_PRESS = 0x10
_ACTION_SWITCH = 0x20


@dataclass(frozen=True)
class _FeatureEventRule:
    """An event a cluster generates when certain FeatureMap bits are
    set and others are not."""

    event_id: int
    requires: int
    excludes: int = 0

    def applies(self, feature_map: int) -> bool:
        return (feature_map & self.requires) == self.requires and (feature_map & self.excludes) == 0


# Which events a cluster can generate per the specification, depending on
# its FeatureMap. Source checked against
# data_model/1.4/clusters/Switch.xml from project-chip/connectedhomeip
# (machine-readable transcription of the Matter Application Cluster
# Specification) — one mandatoryConform per event, via feature bits:
#
#   SwitchLatched (0)        ← LS
#   InitialPress (1)         ← MS
#   LongPress (2)            ← MSL
#   ShortRelease (3)         ← MSR
#   LongRelease (4)          ← MSL
#   MultiPressOngoing (5)    ← MSM AND NOT AS
#   MultiPressComplete (6)   ← MSM
#
# Further clusters with events lacking EventList support are added here as
# additional entries — the algorithm in extract_signals does not change for
# that.
FEATURE_MAP_EVENTS: dict[int, tuple[_FeatureEventRule, ...]] = {
    _SWITCH_CLUSTER_ID: (
        _FeatureEventRule(event_id=0, requires=_LATCHING_SWITCH),
        _FeatureEventRule(event_id=1, requires=_MOMENTARY_SWITCH),
        _FeatureEventRule(event_id=2, requires=_MOMENTARY_SWITCH_LONG_PRESS),
        _FeatureEventRule(event_id=3, requires=_MOMENTARY_SWITCH_RELEASE),
        _FeatureEventRule(event_id=4, requires=_MOMENTARY_SWITCH_LONG_PRESS),
        _FeatureEventRule(
            event_id=5,
            requires=_MOMENTARY_SWITCH_MULTI_PRESS,
            excludes=_ACTION_SWITCH,
        ),
        _FeatureEventRule(event_id=6, requires=_MOMENTARY_SWITCH_MULTI_PRESS),
    ),
}


def _parsed_paths(snapshot: NodeSnapshot) -> Iterable[tuple[int, int, int, object]]:
    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        yield endpoint, cluster_id, attribute_id, value


def _as_id_list(value: object) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    return [int(item) for item in value if isinstance(item, (int, float))]


def _feature_map_event_ids(cluster_id: int, value: object) -> list[int]:
    """Event IDs that follow from a cluster's FeatureMap per FEATURE_MAP_EVENTS.

    Empty for clusters without a table entry, or a FeatureMap that satisfies
    none of the bit conditions recorded there.
    """
    rules = FEATURE_MAP_EVENTS.get(cluster_id)
    if not rules or not isinstance(value, (int, float)):
        return []
    feature_map = int(value)
    return [rule.event_id for rule in rules if rule.applies(feature_map)]


def extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Every non-global attribute becomes a signal. Events come from two
    unioned sources: the EventList (if the device carries it) and, for
    clusters with an entry in FEATURE_MAP_EVENTS, the FeatureMap."""
    signals: set[SignalRef] = set()

    for endpoint, cluster_id, attribute_id, value in _parsed_paths(snapshot):
        if attribute_id == EVENT_LIST_ID:
            for event_id in _as_id_list(value):
                signals.add(SignalRef(endpoint, cluster_id, event_id, SignalKind.EVENT))
            continue
        if attribute_id == FEATURE_MAP_ID:
            for event_id in _feature_map_event_ids(cluster_id, value):
                signals.add(SignalRef(endpoint, cluster_id, event_id, SignalKind.EVENT))
            continue
        if attribute_id in GLOBAL_ATTRIBUTE_IDS:
            continue
        signals.add(SignalRef(endpoint, cluster_id, attribute_id, SignalKind.ATTRIBUTE))

    return sorted(signals)


def find_unreported_attributes(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Attributes the device names in its AttributeList but did not deliver.

    This is the touchstone for Spec 3.5: a non-empty list means the generic
    decomposition misses values the device actually offers.
    """
    present: set[tuple[int, int, int]] = set()
    claimed: set[tuple[int, int, int]] = set()

    for endpoint, cluster_id, attribute_id, value in _parsed_paths(snapshot):
        present.add((endpoint, cluster_id, attribute_id))
        if attribute_id == ATTRIBUTE_LIST_ID:
            for claimed_id in _as_id_list(value):
                if claimed_id not in GLOBAL_ATTRIBUTE_IDS:
                    claimed.add((endpoint, cluster_id, claimed_id))

    return sorted(
        SignalRef(endpoint, cluster_id, attribute_id, SignalKind.ATTRIBUTE)
        for endpoint, cluster_id, attribute_id in claimed - present
    )


def find_unparsable_paths(snapshot: NodeSnapshot) -> list[str]:
    """Paths that did not match the expected format. Should be empty."""
    broken: list[str] = []
    for path in snapshot.attributes:
        try:
            parse_attribute_path(path)
        except ValueError:
            broken.append(path)
    return sorted(broken)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_discovery.py -v`
Expected: PASS, 19 tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/matter/discovery.py tests/matter/test_discovery.py
git commit -m "feat(matter): generische Zerlegung eines Node-Abbilds in Signale"
```

Task 3 was originally committed with pure EventList derivation (10 tests, no
`FEATURE_MAP_EVENTS`). Validation against real devices in Task 7 showed that this
derivation does not hold for events — see the addendum box at the start of this task and
Task 7 for the full finding. The code and the 19 tests above already reflect the
corrected state from commit `6af2de7`.

---

### Task 4: Connection to matter-server

The only place with I/O. Deliberately thin: it fetches raw node data and
turns it into a `NodeSnapshot`, nothing more. Everything interesting is already tested in Task 3.

**Files:**
- Create: `src/loxmatter/matter/client.py`
- Test: `tests/matter/test_client.py`

**Interfaces:**
- Consumes: `NodeSnapshot` from Task 2
- Produces:
  - `class BridgeMatterClient` with `async def connect(self) -> None`, `async def disconnect(self) -> None`, `async def snapshots(self) -> list[NodeSnapshot]`, `async def snapshot(self, node_id: int) -> NodeSnapshot`
  - `MatterUnavailableError(RuntimeError)` — raised when there is no connection
  - Constructor: `BridgeMatterClient(url: str, session_factory: Callable[[Any], Any] | None = None, http_session_factory: Callable[[], Any] | None = None)` — `http_session_factory` builds the aiohttp `ClientSession`, `session_factory` receives that session and builds the upstream `MatterClient` from it. `BridgeMatterClient` creates the session itself and also closes it itself again (in `disconnect()` and on a failed `connect()`) — `MatterClientConnection.disconnect()` from python-matter-server only closes the websocket, not the session passed to it.

- [ ] **Step 1: Write the failing test**

The tests run against stand-ins for the upstream client and the aiohttp
session — no network, no server. The session stand-in counts its `close()`
calls, so the tests really do catch the leak seen in practice (the session
is never closed).

`tests/matter/test_client.py`:

```python
import asyncio
from types import SimpleNamespace

import pytest

from loxmatter.matter import client as client_module
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError


class FakeNode:
    """Stands in for matter_server.client.models.node.MatterNode.

    The real MatterNode does not carry its raw attributes directly but
    under node_data.attributes — node_id, however, remains an attribute
    directly on the node (there a property on node_data.node_id). This
    stand-in reproduces exactly that shape instead of flattening it for
    simplicity.
    """

    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.node_data = SimpleNamespace(attributes=attributes)


class FakeUpstream:
    """Stands in for matter_server.client.MatterClient.

    start_listening() reproduces the real contract: it fills the node
    cache, sets init_ready (if requested), and then blocks until it is
    cancelled — exactly like MatterClient.start_listening().
    get_nodes() deliberately returns nothing until start_listening() has
    run: a test that never starts the listener must be able to reproduce
    the original defect (empty node cache).
    """

    def __init__(
        self,
        nodes: list[FakeNode] | None = None,
        fail_connect: bool = False,
        fail_disconnect: bool = False,
        signal_ready: bool = True,
    ):
        self._configured_nodes = nodes or []
        self._nodes: list[FakeNode] = []
        self.disconnect_calls = 0
        self.start_listening_calls = 0
        self.cancelled = False
        self._fail_connect = fail_connect
        self._fail_disconnect = fail_disconnect
        self._signal_ready = signal_ready

    async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
        self.start_listening_calls += 1
        if self._fail_connect:
            raise RuntimeError("Verbindung fehlgeschlagen")
        self._nodes = self._configured_nodes
        if self._signal_ready and init_ready is not None:
            init_ready.set()
        try:
            await asyncio.Event().wait()  # blocks until cancelled
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self._fail_disconnect:
            raise RuntimeError("Trennung fehlgeschlagen")

    def get_nodes(self) -> list[FakeNode]:
        return self._nodes


class FakeSession:
    """Stands in for aiohttp.ClientSession — counts how often close() ran."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def make_client(
    nodes: list[FakeNode] | None = None,
    *,
    fail_connect: bool = False,
    fail_disconnect: bool = False,
    signal_ready: bool = True,
) -> tuple[BridgeMatterClient, FakeSession]:
    """Builds a BridgeMatterClient with stand-ins for the HTTP session and upstream."""
    session = FakeSession()
    upstream = FakeUpstream(
        nodes or [],
        fail_connect=fail_connect,
        fail_disconnect=fail_disconnect,
        signal_ready=signal_ready,
    )
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: session,
    )
    return bridge, session


@pytest.fixture
def client() -> BridgeMatterClient:
    bridge, _ = make_client(
        [
            FakeNode(12, {"0/40/1": "IKEA of Sweden", "1/6/0": True}),
            FakeNode(13, {"0/40/1": "IKEA of Sweden", "1/1026/0": 2150}),
        ]
    )
    return bridge


async def test_snapshots_requires_a_connection(client):
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await client.snapshots()


async def test_snapshots_maps_every_node(client):
    await client.connect()
    snapshots = await client.snapshots()
    assert [s.node_id for s in snapshots] == [12, 13]
    assert snapshots[0].vendor_name == "IKEA of Sweden"
    assert snapshots[1].attributes["1/1026/0"] == 2150


async def test_snapshot_selects_by_node_id(client):
    await client.connect()
    assert (await client.snapshot(13)).attributes["1/1026/0"] == 2150


async def test_snapshot_raises_for_unknown_node(client):
    await client.connect()
    with pytest.raises(MatterUnavailableError, match="unbekannter Node 99"):
        await client.snapshot(99)


async def test_disconnect_is_idempotent(client):
    await client.connect()
    await client.disconnect()
    await client.disconnect()
    with pytest.raises(MatterUnavailableError):
        await client.snapshots()


async def test_connect_disconnect_closes_session_exactly_once():
    """BridgeMatterClient creates the session itself and must close it again."""
    bridge, session = make_client([FakeNode(1, {})])
    await bridge.connect()
    assert session.close_calls == 0
    await bridge.disconnect()
    assert session.close_calls == 1


async def test_disconnect_twice_closes_session_once_and_does_not_raise():
    bridge, session = make_client([FakeNode(1, {})])
    await bridge.connect()
    await bridge.disconnect()
    await bridge.disconnect()
    assert session.close_calls == 1


async def test_failed_connect_closes_session_and_allows_retry():
    """A failing connect() must not leak the session and must allow a
    later, successful connect()."""
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    attempts = {"n": 0}

    def session_factory(_session: FakeSession) -> FakeUpstream:
        attempts["n"] += 1
        return FakeUpstream([FakeNode(1, {})], fail_connect=attempts["n"] == 1)

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    with pytest.raises(RuntimeError, match="Verbindung fehlgeschlagen"):
        await bridge.connect()

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()

    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert [s.node_id for s in snapshots] == [1]
    assert sessions[1].close_calls == 0


async def test_connect_twice_closes_previous_session_and_does_not_leak():
    """A second connect() without an intervening disconnect() must not
    leave the first session unreachable — it must be closed before the
    second session is created."""
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    def session_factory(_session: FakeSession) -> FakeUpstream:
        return FakeUpstream([FakeNode(1, {})])

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    await bridge.connect()
    await bridge.connect()

    assert len(sessions) == 2
    assert sessions[0].close_calls == 1
    assert sessions[1].close_calls == 0
    snapshots = await bridge.snapshots()
    assert [s.node_id for s in snapshots] == [1]


async def test_disconnect_closes_session_even_if_upstream_disconnect_raises():
    """If the upstream raises in disconnect(), the session must still be
    closed and the client afterward recognizable as not connected."""
    bridge, session = make_client([FakeNode(1, {})], fail_disconnect=True)
    await bridge.connect()

    with pytest.raises(RuntimeError, match="Trennung fehlgeschlagen"):
        await bridge.disconnect()

    assert session.close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()


async def test_connect_cancelled_closes_session_and_propagates_cancellation():
    """asyncio.CancelledError inherits from BaseException, not Exception — a
    connect() cancelled during connection setup must still not leak the
    session and must propagate the cancellation."""
    session = FakeSession()

    class CancellingUpstream:
        async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
            raise asyncio.CancelledError()

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: CancellingUpstream(),
        http_session_factory=lambda: session,
    )

    with pytest.raises(asyncio.CancelledError):
        await bridge.connect()

    assert session.close_calls == 1


async def test_connect_times_out_when_listener_never_signals_readiness(monkeypatch):
    """The defect this test prevents: without a time limit, connect() would
    either wait forever for an event that never comes or — worse — falsely
    report itself as connected without the node cache ever having been
    filled. A listener that never sets init_ready must make connect()
    fail within the time limit."""
    monkeypatch.setattr(client_module, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    bridge, _session = make_client([FakeNode(1, {})], signal_ready=False)

    with pytest.raises(MatterUnavailableError, match="keine Bereitschaft"):
        await bridge.connect()


async def test_connect_timeout_closes_session_and_allows_a_later_successful_connect(
    monkeypatch,
):
    """After a readiness timeout, the client's own session must be closed,
    the client must count as not connected, and a later connect() with a
    working upstream must still succeed."""
    monkeypatch.setattr(client_module, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    attempts = {"n": 0}

    def session_factory(_session: FakeSession) -> FakeUpstream:
        attempts["n"] += 1
        return FakeUpstream([FakeNode(1, {})], signal_ready=attempts["n"] != 1)

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    with pytest.raises(MatterUnavailableError, match="keine Bereitschaft"):
        await bridge.connect()

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()

    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert [s.node_id for s in snapshots] == [1]
    assert sessions[1].close_calls == 0


async def test_disconnect_cancels_the_listener_task():
    """disconnect() must cancel the listener task instead of just letting
    it keep running — otherwise a coroutine stays active, waiting on a
    connection that has since been closed."""
    session = FakeSession()
    upstream = FakeUpstream([FakeNode(1, {})])
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: session,
    )
    await bridge.connect()
    assert upstream.cancelled is False

    await bridge.disconnect()

    assert upstream.cancelled is True


async def test_snapshots_reflect_nodes_populated_by_the_listener():
    """Regression test for the actual defect: the old connect() never
    called upstream.start_listening(), which left the upstream's node
    cache empty forever — every real device appeared unknown, no matter
    how many were commissioned. get_nodes() here — like the real
    MatterClient — deliberately returns nothing until start_listening()
    has run; against the old code (no call to start_listening()), this
    test fails."""
    bridge, _session = make_client([FakeNode(3, {"0/40/1": "Aqara", "1/6/0": True})])

    await bridge.connect()
    snapshots = await bridge.snapshots()

    assert [s.node_id for s in snapshots] == [3]
    assert snapshots[0].vendor_name == "Aqara"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.matter.client'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/matter/client.py`:

```python
"""Connection to python-matter-server.

Deliberately kept thin: fetches raw data and turns it into NodeSnapshots.
Decomposition into signals happens in discovery.py and is tested there
without a network.

BridgeMatterClient creates the aiohttp ClientSession itself and thus remains
its sole owner: MatterClientConnection.disconnect() from python-matter-server
only closes the websocket, not the session passed to it — per aiohttp
convention, whoever created the session must do that. This class therefore
holds the session reference itself and closes it in disconnect() or on a
failed connect().

The upstream `MatterClient` fills its node cache exclusively in
`start_listening()` — a long-running coroutine that fetches the initial
node dump, sets an `init_ready` event, and then keeps running to receive
push updates. `connect()` therefore starts it as a background task and
waits for the readiness event before the client reports itself as
connected; `disconnect()` cancels this task again before the connection
is closed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any, Final

from loxmatter.matter.models import NodeSnapshot

# How long connect() waits for the listener's readiness event before
# giving up. matter-server usually sends the initial node dump within a
# few seconds; the multiple serves as a safety margin against a slow or
# hanging server.
LISTENER_READY_TIMEOUT_SECONDS: Final = 10.0


class MatterUnavailableError(RuntimeError):
    """matter-server is not connected, or does not know the requested node."""


async def _cancel_and_await(task: asyncio.Task[Any]) -> None:
    """Cancels a task and awaits its end.

    Purely intended for cleanup: exceptions from the cancelled task
    (typically CancelledError, but also others, if the task had already
    ended with an error beforehand) are swallowed here so they do not
    mask the actual, already-running error path — the caller has already
    seen the relevant exception at its actual source, or will still see
    it there.
    """
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


class BridgeMatterClient:
    def __init__(
        self,
        url: str,
        session_factory: Callable[[Any], Any] | None = None,
        http_session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._url = url
        self._session_factory = session_factory or self._default_session_factory
        self._http_session_factory = http_session_factory or self._default_http_session_factory
        self._upstream: Any | None = None
        self._http_session: Any | None = None
        self._listener_task: asyncio.Task[Any] | None = None

    def _default_session_factory(self, session: Any) -> Any:
        # Lazily imported so tests never have to load matter_server.
        from matter_server.client.client import MatterClient

        return MatterClient(self._url, session)

    @staticmethod
    def _default_http_session_factory() -> Any:
        # Lazily imported so tests never have to load aiohttp.
        import aiohttp

        return aiohttp.ClientSession()

    async def _start_listener(self, upstream: Any) -> asyncio.Task[Any]:
        """Starts upstream.start_listening() as a background task and waits
        until it has filled the node cache and signaled readiness. If the
        listener fails or does not respond in time, this method fully
        cleans up the task and raises, instead of returning a half-connected
        task."""
        ready = asyncio.Event()
        listener_task: asyncio.Task[Any] = asyncio.ensure_future(upstream.start_listening(ready))
        ready_task = asyncio.ensure_future(ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {listener_task, ready_task},
                timeout=LISTENER_READY_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_task in done:
                # Readiness reported — the listener now keeps running in
                # the background to receive push updates.
                return listener_task

            await _cancel_and_await(ready_task)

            if listener_task in done:
                # The listener ended before reporting readiness.
                # .result() re-raises its original exception unchanged
                # (e.g. CannotConnect) — callers like the CLI can thus
                # continue to handle it specifically.
                listener_task.result()
                msg = "Listener wurde beendet, bevor er Bereitschaft meldete"
                raise MatterUnavailableError(msg)

            msg = (
                f"matter-server hat nach {LISTENER_READY_TIMEOUT_SECONDS:.0f}s "
                "keine Bereitschaft gemeldet"
            )
            raise MatterUnavailableError(msg)
        except BaseException:
            await _cancel_and_await(listener_task)
            raise

    async def connect(self) -> None:
        # An already-connected client is cleanly disconnected on a renewed
        # connect() before reconnecting — otherwise the old, still-open
        # session would become unreachable and never closed when
        # self._upstream/self._http_session are overwritten.
        if self._upstream is not None:
            await self.disconnect()
        http_session = self._http_session_factory()
        try:
            upstream = self._session_factory(http_session)
            listener_task = await self._start_listener(upstream)
        except BaseException:
            # BaseException instead of Exception: asyncio.CancelledError
            # inherits from BaseException, not from Exception. A connect()
            # cancelled during connection setup (e.g. via asyncio.wait_for)
            # must still close the session and propagate the cancellation.
            await http_session.close()
            raise
        self._http_session = http_session
        self._upstream = upstream
        self._listener_task = listener_task

    async def disconnect(self) -> None:
        if self._upstream is None:
            return
        upstream = self._upstream
        http_session = self._http_session
        listener_task = self._listener_task
        # Set fields to None before the await: this way the client is
        # immediately recognizable as not connected, even if one of the
        # steps below raises an exception — disconnect() stays idempotent
        # and object state stays clean, no matter how the disconnection
        # turns out.
        self._upstream = None
        self._http_session = None
        self._listener_task = None
        if http_session is None:
            # Invariant: if _upstream is set, _http_session is also set
            # (both are only ever set together in connect()). An explicit
            # error instead of assert, so the check also takes effect
            # under `python -O`.
            msg = "interner Fehler: _http_session fehlt trotz aktivem _upstream"
            raise RuntimeError(msg)
        try:
            if listener_task is not None:
                await _cancel_and_await(listener_task)
        finally:
            try:
                await upstream.disconnect()
            finally:
                await http_session.close()

    def _require_upstream(self) -> Any:
        if self._upstream is None:
            raise MatterUnavailableError("nicht verbunden mit matter-server")
        return self._upstream

    async def snapshots(self) -> list[NodeSnapshot]:
        upstream = self._require_upstream()
        return [
            # The raw attributes on matter_server.MatterNode do not sit
            # directly on the node but on node.node_data.attributes — this
            # was previously unobservable because the node cache was
            # always empty before the listener was wired up (see the
            # module docstring).
            NodeSnapshot.from_raw(node.node_id, {"attributes": node.node_data.attributes})
            for node in upstream.get_nodes()
        ]

    async def snapshot(self, node_id: int) -> NodeSnapshot:
        for candidate in await self.snapshots():
            if candidate.node_id == node_id:
                return candidate
        raise MatterUnavailableError(f"unbekannter Node {node_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_client.py -v`
Expected: PASS, 15 tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/matter/client.py tests/matter/test_client.py
git commit -m "feat(matter): Client für matter-server mit Snapshot-Abbildung"
```

---

### Task 5: CLI `loxmatter inspect`

The phase's visible payoff. Works either against a running matter-server or
against a fixture file — the latter makes it usable network-free in tests.

**Files:**
- Create: `src/loxmatter/cli.py`
- Test: `tests/test_cli.py`
- Create: `tests/fixtures/nodes/example_light.json`

**Interfaces:**
- Consumes: `BridgeMatterClient`, `NodeSnapshot`, `extract_signals`, `find_unreported_attributes`, `find_unparsable_paths`
- Produces: `app: typer.Typer` with command `inspect`; `render_report(snapshot: NodeSnapshot) -> str`

- [ ] **Step 1: Create the fixture**

`tests/fixtures/nodes/example_light.json`:

```json
{
  "node_id": 12,
  "attributes": {
    "0/40/1": "IKEA of Sweden",
    "0/40/3": "TRADFRI bulb",
    "0/40/18": "ABC123",
    "1/6/65531": [0, 16],
    "1/6/0": true,
    "1/8/0": 254,
    "1/59/65530": [0, 1]
  }
}
```

- [ ] **Step 2: Write the failing test**

`tests/test_cli.py`:

```python
import json
from pathlib import Path
from typing import Any

from matter_server.client.exceptions import CannotConnect
from typer.testing import CliRunner

from loxmatter import cli
from loxmatter.cli import app, render_report
from loxmatter.matter.client import BridgeMatterClient
from loxmatter.matter.models import NodeSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "nodes" / "example_light.json"


def load() -> NodeSnapshot:
    raw = json.loads(FIXTURE.read_text())
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_report_names_the_device():
    report = render_report(load())
    assert "IKEA of Sweden" in report
    assert "TRADFRI bulb" in report


def test_report_lists_attribute_and_event_signals():
    report = render_report(load())
    assert "1/6/0" in report
    assert "1/8/0" in report
    assert "1/59/0" in report  # event from the EventList
    assert "1/59/1" in report


def test_report_hides_global_attributes():
    assert "65531" not in render_report(load())


def test_report_flags_attributes_the_device_claimed_but_did_not_report():
    # AttributeList names 0 and 16, only 0 was delivered.
    report = render_report(load())
    assert "NICHT GELIEFERT" in report
    assert "1/6/16" in report


def test_cli_reads_a_fixture_without_network():
    result = CliRunner().invoke(app, ["inspect", "--fixture", str(FIXTURE)])
    assert result.exit_code == 0
    assert "TRADFRI bulb" in result.stdout


class _FakeUpstream:
    """Stand-in for matter_server.client.MatterClient — offline, no socket."""

    def __init__(
        self,
        nodes: list[Any] | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self._nodes = nodes or []
        self._connect_error = connect_error

    async def connect(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    async def disconnect(self) -> None:
        pass

    def get_nodes(self) -> list[Any]:
        return self._nodes


class _FakeHttpSession:
    async def close(self) -> None:
        pass


def _fake_client(
    *,
    nodes: list[Any] | None = None,
    connect_error: BaseException | None = None,
) -> BridgeMatterClient:
    upstream = _FakeUpstream(nodes=nodes, connect_error=connect_error)
    return BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=_FakeHttpSession,
    )


def test_cli_reports_malformed_fixture_missing_node_id(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"attributes": {}}), encoding="utf-8")

    result = CliRunner().invoke(app, ["inspect", "--fixture", str(broken)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "node_id" in result.stderr


def test_cli_reports_fixture_that_is_not_valid_json(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not valid json", encoding="utf-8")

    result = CliRunner().invoke(app, ["inspect", "--fixture", str(broken)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "JSON" in result.stderr


def test_cli_reports_node_not_found(monkeypatch):
    monkeypatch.setattr(cli, "_build_client", lambda url: _fake_client(nodes=[]))

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://test/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "1" in result.stderr


def test_cli_reports_unreachable_server(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_build_client",
        lambda url: _fake_client(connect_error=CannotConnect("boom")),
    )

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://10.0.1.215:5580/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "nicht erreichbar" in result.stderr
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.cli'`

- [ ] **Step 4: Write minimal implementation**

`src/loxmatter/cli.py`:

```python
"""Command line for the bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import NoReturn

import typer
from matter_server.client.exceptions import CannotConnect

from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind

app = typer.Typer(help="Matter → Loxone Bridge")


@app.callback()
def main() -> None:
    """Without this callback, Typer turns `loxmatter inspect ...` into
    `loxmatter ...` when there is exactly one command — the subcommand disappears."""


def render_report(snapshot: NodeSnapshot) -> str:
    lines = [
        f"Node {snapshot.node_id}: {snapshot.vendor_name} {snapshot.product_name}".rstrip(),
        f"Unique ID: {snapshot.unique_id or '—'}",
        "",
    ]

    signals = extract_signals(snapshot)
    attributes = [s for s in signals if s.kind is SignalKind.ATTRIBUTE]
    events = [s for s in signals if s.kind is SignalKind.EVENT]

    lines.append(f"Attribute ({len(attributes)}):")
    for ref in attributes:
        lines.append(f"  {ref.path:<16} = {snapshot.attributes.get(ref.path)!r}")

    lines.append("")
    lines.append(f"Events ({len(events)}):")
    for ref in events:
        lines.append(f"  {ref.path}")

    missing = find_unreported_attributes(snapshot)
    if missing:
        lines += [
            "",
            f"NICHT GELIEFERT ({len(missing)}) — vom Gerät gelistet, aber ohne Wert:",
        ]
        lines += [f"  {ref.path}" for ref in missing]

    broken = find_unparsable_paths(snapshot)
    if broken:
        lines += ["", f"NICHT LESBAR ({len(broken)}):"] + [f"  {p}" for p in broken]

    return "\n".join(lines)


def _fail(message: str) -> NoReturn:
    """Reports an expected CLI error: one line to stderr, then program
    exit with exit code ≠ 0 — instead of a traceback."""
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _load_fixture(path: Path) -> NodeSnapshot:
    """Loads a fixture file; reports broken content as a CLI error instead
    of aborting with a raw KeyError/JSONDecodeError."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"Fixture {path} enthält kein gültiges JSON: {exc}")
    try:
        node_id = raw["node_id"]
    except (KeyError, TypeError):
        _fail(f"Fixture {path} hat kein Feld 'node_id'.")
    return NodeSnapshot.from_raw(node_id, raw)


def _build_client(url: str) -> BridgeMatterClient:
    """A dedicated construction step, so tests can monkeypatch the client
    with an instance built from fake factories — without touching the
    network (see BridgeMatterClient.session_factory)."""
    return BridgeMatterClient(url)


@app.command()
def inspect(
    node: int | None = typer.Option(None, help="Node-ID am laufenden matter-server"),
    fixture: Path | None = typer.Option(  # noqa: B008 — typer idiom, Ruff does not consider `Path` immutable
        None, help="Statt matter-server ein gespeichertes Abbild"
    ),
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
) -> None:
    """Lists all attributes and events of a device."""
    if fixture is not None:
        typer.echo(render_report(_load_fixture(fixture)))
        return

    if node is None:
        raise typer.BadParameter("entweder --node oder --fixture angeben")

    async def run() -> str:
        client = _build_client(url)
        try:
            await client.connect()
        except CannotConnect:
            _fail(f"matter-server unter {url} nicht erreichbar — läuft der Dienst?")
        try:
            snapshot = await client.snapshot(node)
        except MatterUnavailableError:
            _fail(f"Node {node} ist am matter-server ({url}) nicht bekannt — kommissioniert?")
        finally:
            await client.disconnect()
        return render_report(snapshot)

    typer.echo(asyncio.run(run()))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 9 tests

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/cli.py tests/test_cli.py tests/fixtures/nodes/example_light.json
git commit -m "feat(cli): loxmatter inspect listet Signale eines Geräts"
```

A later bug fix added three error paths — broken fixture (invalid
JSON or missing `node_id`), unknown node, unreachable matter-server —, which
previously produced raw tracebacks instead of clean German messages. The code above and
the four additional tests already reflect this state.

---

### Task 6: matter-server and OTBR on the Test Host (Raspberry Pi)

This task was originally in Phase 6. It had to be pulled forward because Task 7
cannot run without a running controller — the assumption from Spec 3.5 can only
be checked against real devices, and real devices are only reachable through a controller.

The goal is explicitly **not** the finished production stack from Spec 4.1. It is the
test environment that can complete Phase 1. Phase 6 builds on it.

**Files:**
- Create: `deploy/testhost/docker-compose.yml`
- Create: `deploy/testhost/.env.example`
- Create: `deploy/testhost/README.md`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: a reachable WebSocket URL `ws://10.0.1.56:5580/ws`, which Task 7 uses as `--url`
  (see `deploy/testhost/README.md`)

#### The Environment, Already Surveyed

Do not determine again — these values were looked up on the Pi:

| | Value |
|---|---|
| Host | `pi@10.0.1.56`, SSH key set up |
| OS | Debian 13 "trixie" (Raspberry Pi OS), aarch64 |
| Model | Raspberry Pi 4 Model B Rev 1.5, 8 GB RAM |
| Backbone interface | `wlan0` (no Ethernet cable plugged in) |
| Radio module | SONOFF Dongle Plus MG24 (Silicon Labs CP210x), Thread firmware already flashed |
| Device node | `/dev/ttyUSB0`, group `dialout` |
| Bluetooth | built-in adapter `hci0` |
| IPv6 | link-local only, `forwarding=0` |
| Docker | not installed |

**Why a Pi and not a VM:** This environment first ran on an Ubuntu VM
(`lucienkerl@10.0.1.215`, backbone interface `ens18`). It had to move to the Pi
because the VM **had no Bluetooth adapter** — Matter commissioning runs over BLE,
and without an adapter no device can ever be commissioned. The Pi has a built-in
adapter in `hci0`. All other findings from the VM (baud rate, OTBR image variant,
NAT64/firewall workaround) apply unchanged to the Pi — the same dongle, the same
firmware, the same image — and are recorded in `deploy/testhost/README.md` under
"History: the VM".

**On the missing global IPv6:** not critical for Thread devices. The OTBR sets up
its own ULA prefix on `wpan0`, and matter-server runs alongside it with
`network_mode: host` and reaches the devices via the route there. Only Matter-over-WLAN
would need global IPv6 on the LAN. All that's necessary is `forwarding=1`.

#### Step 0: Root Steps (to be Run by a Human)

`sudo` on this Pi requires a password that the agent must neither ask for nor use.
These commands are run by the operator themselves, after which the agent takes over:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker,dialout "$USER"
printf 'net.ipv6.conf.all.forwarding=1\nnet.ipv4.ip_forward=1\n' | sudo tee /etc/sysctl.d/99-matter.conf
sudo sysctl --system
```

Afterward, **log in again** (group memberships only take effect in a new session).

- [ ] **Step 1: Confirm prerequisites**

```bash
ssh pi@10.0.1.56 'id -nG; docker ps >/dev/null && echo docker-ok; ls -l /dev/ttyUSB0; sysctl net.ipv6.conf.all.forwarding'
```

Expected: `docker` and `dialout` in the groups, `docker-ok`, `forwarding = 1`.
If anything is missing, Step 0 is incomplete — report that instead of working around it with `sudo`.

- [ ] **Step 2: Write the compose files**

`deploy/testhost/.env.example`:

```
RADIO_DEVICE=/dev/ttyUSB0
RADIO_BAUDRATE=460800
BACKBONE_IF=wlan0
# id of the Bluetooth adapter (from `hci0` -> 0) for BLE commissioning by
# matter-server. The Pi only has hci0, hence 0; see README "Enable BLE".
BLUETOOTH_ADAPTER=0
```

`deploy/testhost/docker-compose.yml`:

```yaml
# Test environment for Phase 1 - NOT the production stack from Spec 4.1.
services:
  otbr:
    image: openthread/otbr:latest
    container_name: otbr
    network_mode: host
    privileged: true
    restart: unless-stopped
    devices:
      - ${RADIO_DEVICE}:${RADIO_DEVICE}
    environment:
      RADIO_URL: spinel+hdlc+uart://${RADIO_DEVICE}?uart-baudrate=${RADIO_BAUDRATE}
      # Without modprobe in the image, legacy iptables tables cannot be
      # loaded — NAT64/NAT44 and the legacy firewall would crash the
      # container. Not critical for the test environment (no global IPv6 needed).
      NAT64: "0"
      FIREWALL: "0"
    command: --backbone-interface ${BACKBONE_IF}

  matter-server:
    image: ghcr.io/home-assistant-libs/python-matter-server:stable
    container_name: matter-server
    network_mode: host
    restart: unless-stopped
    security_opt:
      - apparmor=unconfined
    volumes:
      - ./data:/data
      - /run/dbus:/run/dbus:ro
    # --bluetooth-adapter enables BLE commissioning via the adapter named
    # in BLUETOOTH_ADAPTER — the reason this test environment moved to
    # the Pi at all (see above).
    command:
      - --storage-path
      - /data
      - --paa-root-cert-dir
      - /data/credentials
      - --bluetooth-adapter
      - "${BLUETOOTH_ADAPTER:-0}"
    depends_on:
      - otbr
```

Copy `.env.example` on the Pi to `.env`. The compose syntax of
`openthread/otbr` changes between versions — check it against the image's
documentation before hunting for bugs that aren't, and record deviations in the README.

**Baud rate:** 460800 is the most likely value for the SONOFF MG24. If the
RCP does not connect, 115200 is the next candidate. Do not guess more than twice —
after that, read the dongle's firmware documentation.

- [ ] **Step 3: Start and Form the Thread Network**

```bash
cd ~/loxmatter-testhost && docker compose up -d
docker compose logs -f otbr        # until the RCP is connected
```

```bash
docker exec -it otbr ot-ctl dataset init new
docker exec -it otbr ot-ctl dataset commit active
docker exec -it otbr ot-ctl ifconfig up
docker exec -it otbr ot-ctl thread start
docker exec -it otbr ot-ctl state          # expected: leader
docker exec -it otbr ot-ctl dataset active -x
```

Save the printed active dataset — it is needed to commission Thread devices.
**Do not commit it to the repository**, it is a network credential.

- [ ] **Step 4: Check Reachability from the Development Machine**

On the Mac, in the project directory:

```bash
uv run loxmatter inspect --node 1 --url ws://10.0.1.56:5580/ws
```

Expected: a report, or `unbekannter Node 1`. Either proves that the connection
holds. A connection error means port 5580 is not reachable — then
check the firewall and `network_mode: host`.

- [ ] **Step 5: Write the README**

`deploy/testhost/README.md` records what actually worked: the concrete
baud rate, every deviation from Step 2, the command to back up the fabric volume under
`./data`, and the note that the Thread dataset does not belong in the repository.
This document is the raw material for the deployment guide in Phase 6 — write down
what went wrong, not just what ended up working.

- [ ] **Step 6: Commit**

```bash
git add deploy/testhost
git commit -m "feat(deploy): Testumgebung mit matter-server und OTBR"
```

---

### Task 7: Record Fixtures of Real Devices and Check the Assumption

The purpose of the whole phase. This is where Spec 3.5 is confirmed or refuted.

**Files:**
- Create: `scripts/record_node.py`
- Create: `tests/fixtures/nodes/<manufacturer>_<product>.json` (per device)
- Create: `tests/matter/test_real_devices.py`
- Modify: `docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md` (only if the assumption breaks)

**Interfaces:**
- Consumes: `BridgeMatterClient`, `extract_signals`, `find_unreported_attributes`, `find_unparsable_paths`
- Produces: fixture files in the format from Task 5 (`{"node_id": int, "attributes": {...}}`)

- [ ] **Step 1: Write the Recording Tool**

`scripts/record_node.py`:

```python
"""Saves the snapshot of a real device as a fixture.

Usage: uv run python scripts/record_node.py 12 tests/fixtures/nodes/ikea_bulb.json
With a different matter-server:
       uv run python scripts/record_node.py 3 tests/fixtures/nodes/ikea_plug.json \\
           --url ws://10.0.1.56:5580/ws
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loxmatter.matter.client import BridgeMatterClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_id", type=int, help="Node-ID am matter-server")
    parser.add_argument("target", type=Path, help="Zieldatei für die Fixture")
    parser.add_argument(
        "--url",
        default="ws://localhost:5580/ws",
        help="Adresse von matter-server (Default: ws://localhost:5580/ws)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    client = BridgeMatterClient(args.url)
    await client.connect()
    try:
        snapshot = await client.snapshot(args.node_id)
    finally:
        await client.disconnect()

    args.target.write_text(
        json.dumps(
            {"node_id": snapshot.node_id, "attributes": dict(snapshot.attributes)},
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{args.target} geschrieben, {len(snapshot.attributes)} Attribute")


if __name__ == "__main__":
    asyncio.run(main())
```

`--url` instead of a second `sys.argv` field, because the test host (`deploy/testhost/`,
Task 6) runs at a fixed IP on the LAN, not on localhost — see the usage example
above.

- [ ] **Step 2: Record Real Devices**

Run once per device, with matter-server running and IKEA devices commissioned.
Goal: at least one device per class from Spec 3.5, otherwise the phase only checks half
the assumption.

**The node IDs below are placeholders** — the real ones are in the matter-server UI,
or come from `uv run loxmatter inspect --node <id>`, tried until one matches. For the
file names, all that matters is that `test_real_devices.py` finds them via `*.json` and
excludes `example_*` — not their exact wording:

```bash
uv run python scripts/record_node.py 12 tests/fixtures/nodes/ikea_bulb_color.json
uv run python scripts/record_node.py 13 tests/fixtures/nodes/ikea_plug_energy.json
uv run python scripts/record_node.py 14 tests/fixtures/nodes/ikea_button.json
uv run python scripts/record_node.py 15 tests/fixtures/nodes/ikea_sensor.json
```

**Result:** Only two IKEA devices were available, not four — the bulb (with
ColorControl) and the sensor simply weren't present in the apartment. Recorded were
`tests/fixtures/nodes/ikea_grillplats_plug.json` (node 3, metering plug) and
`tests/fixtures/nodes/ikea_bilresa_button.json` (node 4, two-channel button) — named
after the device model rather than the device class, for the same reason as above: the
exact wording was never the requirement. What this means for the assumption's coverage
is in the finding at the end of this task.

- [ ] **Step 3: Write the failing test**

`tests/matter/test_real_devices.py`:

```python
"""Checks Spec 3.5 against snapshots of real devices.

If one of these tests fails, the test is not wrong — then the generic
decomposition does not hold, and the spec must be changed.
"""

import json
from pathlib import Path

import pytest

from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "nodes"
REAL_DEVICES = sorted(p for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("example_"))


def load(path: Path) -> NodeSnapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_real_device_fixtures_exist():
    assert REAL_DEVICES, "Task 7 Schritt 2 wurde nicht ausgeführt — keine echten Abbilder da"


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_every_path_is_parsable(path):
    assert find_unparsable_paths(load(path)) == []


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_no_claimed_attribute_is_missing(path):
    assert find_unreported_attributes(load(path)) == []


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_device_yields_at_least_one_signal(path):
    assert extract_signals(load(path))


def test_at_least_one_fixture_carries_events():
    """Buttons are the special case from Spec 6.3 — without them the assumption is only half-checked."""
    with_events = [
        p for p in REAL_DEVICES if any(s.kind is SignalKind.EVENT for s in extract_signals(load(p)))
    ]
    assert with_events, "kein aufgenommenes Gerät liefert Events — Taster fehlt"


def test_at_least_one_fixture_carries_energy_measurement():
    """Spec 7.3: metering plug, cluster 144 ElectricalPowerMeasurement."""
    with_energy = [
        p for p in REAL_DEVICES if any(s.cluster_id == 144 for s in extract_signals(load(p)))
    ]
    assert with_energy, "kein aufgenommenes Gerät misst Leistung"
```

- [ ] **Step 4: Run tests and read the result carefully**

Run: `uv run pytest tests/matter/test_real_devices.py -v`

Expectation: PASS. These tests are the phase's experiment, not a formality.

On failure, **do not adjust the test**, record the finding instead:

- `test_every_path_is_parsable` fails → matter-server uses path forms that
  `parse_attribute_path` does not know. Extend `paths.py`, revise Task 1.
- `test_no_claimed_attribute_is_missing` fails → the device offers attributes that
  do not end up in the snapshot. Clarify the cause: does matter-server not read them at
  all, or is a subscription missing? **This is the case that endangers Spec 3.5.**
- `test_at_least_one_fixture_carries_events` fails → events are not in the
  EventList and only come via subscriptions. Then `discovery` needs a
  second source and the spec needs an addition in 6.3.

- [ ] **Step 5: Record the Finding in the Spec**

Add a paragraph with the result under 3.5 in the spec — even if it is positive:

```markdown
**Validierung (Phase 1, <Datum>).** Geprüft an <n> realen IKEA-Geräten
(<Liste>). Alle Attributpfade parsebar, keine vom Gerät gelisteten Attribute
fehlten, Events über die EventList auffindbar. Die generische Zerlegung trägt.
```

On a negative finding, describe instead what does not hold and how 3.5 changes.

**Addendum on the coverage bar from Step 2:** "at least one device per class, otherwise
the phase only checks half the assumption" was **not met**. Only two classes were
available and checked — metering plug (`ikea_grillplats_plug.json`) and button
(`ikea_bilresa_button.json`). A lamp with a ColorControl cluster and a sensor were
not available and stayed unchecked. The spec is honest about this (n=2), but it has
so far not recorded that the bar itself was missed. Consequence:
ColorControl goes unchecked into Phase 4, where the color-space conversion lives — that
must be made up there before the generic decomposition counts as confirmed for that
cluster.

- [ ] **Step 6: Commit**

```bash
git add scripts/record_node.py tests/fixtures/nodes tests/matter/test_real_devices.py docs/
git commit -m "test(matter): Spec 3.5 an echten IKEA-Geräten validiert"
```

---

### Task 8: CI and Quality Gate

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `README.md`

**Interfaces:**
- Consumes: all previous tasks
- Produces: nothing for later tasks

- [ ] **Step 1: Set Up CI**

`.github/workflows/ci.yml`:

```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run pytest -v
```

- [ ] **Step 2: README schreiben**

`README.md`:

```markdown
# loxmatter

Bindet Matter-Geräte (Thread und WiFi) an einen Loxone Miniserver an.

Design: [`docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md)

## Stand

Phase 1 von 6: Matter-Adapter und Signal-Extraktion.

## Entwickeln

```bash
uv sync
uv run pytest
```

Die Testsuite läuft ohne Hardware und ohne Netzwerkzugriff.

## Ein Gerät ansehen

```bash
uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json
uv run loxmatter inspect --node 12          # against a running matter-server
```
```

- [ ] **Step 3: Get Everything Green Locally**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest -v
```

Expected: all checks with no findings, all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add .github README.md
git commit -m "ci: Lint, Typprüfung und Tests bei jedem Push"
```

---

## Completion of the Phase

The phase is done when:

1. `uv run pytest` passes without hardware and without a network,
2. `uv run loxmatter inspect --node <id>` prints a complete signal list for
   every real IKEA device,
3. the finding on Spec 3.5 is in the spec — positive or negative.

Only then is the plan for Phase 2 written. If the finding turns out negative, the
spec is revised first: it remains the authoritative document, not this plan.
