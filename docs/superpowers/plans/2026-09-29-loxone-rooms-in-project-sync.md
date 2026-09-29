# Loxone Rooms in the Project Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A virtual input or output that the project sync creates lands in the Loxone room whose name matches its device's or group's room in loxmatter; a room missing in Loxone Config is created.

**Architecture:** The existing pipeline stays: `index` → `diff` → `patch`, one request, text surgery on the original bytes. `index.py` additionally records the room list (`PlaceCaption`/`Place`) and the rights group caption. A new pure module `rooms.py` matches loxmatter room names against Loxone rooms. `diff.py` stores the owner's room on new plan entries and the room assignments on the plan. `patch.py` writes new `Place`/`RightGroup` objects and sets `Pr` in the `IoData` of new cmds. The API and WebUI show a "Rooms" block and each new entry's target room.

**Tech Stack:** Python 3.12, FastAPI/pydantic, pytest, Alpine.js WebUI, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-29-loxone-rooms-in-project-sync-design.md`. Read it before starting any task.

## Global Constraints

- Everything in the repository is English: code, comments, docstrings, test names, commit messages (`CLAUDE.md`). German appears only as `de:` values in `src/loxmatter/i18n/strings.yaml` and as data in quoted test strings (fixture room names such as `"Küche"`).
- Commit messages: Conventional Commits, English, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every user-visible string goes through `strings.yaml` with `en` and `de`; the German tone of the project sync card is impersonal (`Bitte in Loxone Config manuell prüfen.`), never addressing the reader as `du` or `Sie`.
- Existing objects are never touched because of rooms: no `IoData` of an existing cmd is read for comparison or rewritten (spec section 2).
- Only `new_signal`/`new_device` entries get a room (spec section 5).
- Matching key: `name.strip().casefold()` (spec section 4).
- New objects carry `V="178"`, like every other object the sync creates.
- Created `Place`: `Type`, `V`, `U`, `Title`, `WF="16384"`, then `PType` copied from the first `Place` without `First="true"` (if any), then `RGR` (only if a rights group is written). `Icon` is never written (decided 2026-09-29). Never `PGroup`, `Rating`, `UseFav`, `First`.
- Created `RightGroup`: `Type="RightGroup" V="178" U=… Title=… Cl="0,0,0" WF="16384" GT="1" MG=""`.
- `NextObj` rises by the number of created objects (existing `_next_obj_edit`).
- **Running tests:** never with `run_in_background`, never via `Monitor`, never end a turn waiting for a notification. Run the named test files in the foreground. The full suite does not fit in one Bash call (10+ minutes); the final task splits it.
- Checks CI runs: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run python scripts/check_language.py`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/loxmatter/projectsync/index.py` | modify | find `PlaceCaption`, its `Place` children, and the rights group caption |
| `src/loxmatter/projectsync/rooms.py` | create | `RoomStatus`, `RoomAssignment`, `room_key`, `can_create_rooms`, `assign_rooms` - pure matching |
| `src/loxmatter/projectsync/diff.py` | modify | `PlanEntry.room`, `SyncPlan.rooms`, `SyncPlan.room_for`, wiring in `build_plan` |
| `src/loxmatter/projectsync/schema.py` | modify | `new_place_tag`, `new_right_group_tag`, `place_u` parameter of `new_cmd_children_xml` |
| `src/loxmatter/projectsync/patch.py` | modify | `_room_edits`; pass `place_u` into `_new_signal_edit`/`_new_device_edit` |
| `src/loxmatter/api/models.py` | modify | `ProjectSyncRoomOut`, `ProjectSyncEntryOut.target_room`, `ProjectSyncPlanOut.rooms` |
| `src/loxmatter/api/project_sync.py` | modify | fill the new fields |
| `src/loxmatter/i18n/strings.yaml` | modify | room block texts |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | modify | room block, target room line |
| `tests/projectsync/conftest.py` | modify | `rooms_project`, `places_only_project` fixtures |
| `tests/projectsync/test_index.py`, `test_rooms.py` (new), `test_diff.py`, `test_schema.py`, `test_patch_rooms.py` (new) | tests | |
| `tests/api/test_project_sync_api.py`, `tests/api/test_web.py` | tests | |
| `CHANGELOG.md` | modify | user-facing note |

---

### Task 1: The index reads rooms

**Files:**
- Modify: `src/loxmatter/projectsync/index.py`
- Create: `tests/projectsync/room_fixtures.py`
- Modify: `tests/projectsync/conftest.py`
- Test: `tests/projectsync/test_index.py`

**Interfaces:**
- Produces: `ProjectIndex.place_caption: Element | None`, `ProjectIndex.places: list[Element]` (the `Type="Place"` children of `place_caption`, document order), `ProjectIndex.right_group_caption: Element | None` (first `LoxCaption` with `CaptionType="13"` anywhere in the tree). Fixtures `rooms_project` and `places_only_project` (strings).

- [ ] **Step 1: Add the fixtures**

The XML snippets live in a plain module, not in the conftest: `tests/` has
no `__init__.py` files, so `from conftest import ...` would be ambiguous
between `tests/conftest.py` and `tests/projectsync/conftest.py`. pytest's
default `prepend` import mode puts `tests/projectsync/` on `sys.path`, so
`from room_fixtures import ...` works from every test file in that folder.

Create `tests/projectsync/room_fixtures.py` (GPL header copied from any
test file):

```python
"""Room list snippets for `projectsync` tests (design 2026-09-29)."""

from __future__ import annotations

# Rooms as Loxone Config writes them (design 2026-09-29, section 3): a
# `PlaceCaption` with the `Nicht zugeordnet` room (`First="true"`) and one
# ordinary room, and a rights group caption with one `RightGroup` per room
# (`Place.RGR` -> `RightGroup.U`). Inserted directly under `Document`, next
# to the `LoxLIVE` block, where real files carry them.
ROOMS_XML = (
    '\t\t<C Type="PlaceCaption" V="175" U="3000-0000-0000-aaaaaaaaaaaaaaaa" Title="Räume"'
    ' WF="16384">\r\n'
    '\t\t\t<C Type="Place" V="175" U="3000-0001-0000-aaaaaaaaaaaaaaaa" Title="Nicht zugeordnet"'
    ' WF="6307840" Icon="0000015c-00ff-0000-0000000000000000" First="true" PType="2"'
    ' RGR="3000-0011-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t<C Type="Place" V="175" U="3000-0002-0000-aaaaaaaaaaaaaaaa" Title="Küche"'
    ' WF="16384" Icon="0000005a-00ff-0000-0000000000000000" Rating="1" PGroup="4" PType="3"'
    ' RGR="3000-0012-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    "\t\t</C>\r\n"
    '\t\t<C Type="LoxCaption" V="175" U="3000-0010-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Berechtigungsgruppen" Cl="0,0,0" WF="20480" CaptionType="13" SubType="13">\r\n'
    '\t\t\t<C Type="RightGroup" V="175" U="3000-0011-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Nicht zugeordnet" Cl="0,0,0" WF="16384" GT="1" MG=""/>\r\n'
    '\t\t\t<C Type="RightGroup" V="175" U="3000-0012-0000-aaaaaaaaaaaaaaaa" Title="Küche"'
    ' Cl="0,0,0" WF="16384" GT="1" MG=""/>\r\n'
    "\t\t</C>\r\n"
)

# The room list of a project from before the rights system (the 2014/2015
# files in design 2026-09-29, section 3): rooms without `RGR`, no rights
# group caption at all.
PLACES_ONLY_XML = (
    '\t\t<C Type="PlaceCaption" V="70" U="3000-0000-0000-aaaaaaaaaaaaaaaa" Title="Räume">\r\n'
    '\t\t\t<C Type="Place" V="70" U="3000-0002-0000-aaaaaaaaaaaaaaaa" Title="Küche"'
    ' Icon="00000000-0000-0002-2100000000000000"/>\r\n'
    "\t\t</C>\r\n"
)

_DOCUMENT_OPEN = 'Title="Testprojekt">\r\n'


def with_rooms(project: str, rooms_xml: str = ROOMS_XML) -> str:
    """`project` with `rooms_xml` inserted as the first children of its
    `Document`."""
    assert _DOCUMENT_OPEN in project
    return project.replace(_DOCUMENT_OPEN, _DOCUMENT_OPEN + rooms_xml, 1)
```

Append to `tests/projectsync/conftest.py`:

```python
from room_fixtures import PLACES_ONLY_XML, with_rooms


@pytest.fixture
def rooms_project(sample_project: str) -> str:
    return with_rooms(sample_project)


@pytest.fixture
def places_only_project(sample_project: str) -> str:
    return with_rooms(sample_project, PLACES_ONLY_XML)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/projectsync/test_index.py`:

```python
def test_index_finds_the_rooms_and_the_rights_group_caption(rooms_project):
    index = build_index(rooms_project)
    assert index.place_caption is not None
    assert [p.attrs["Title"] for p in index.places] == ["Nicht zugeordnet", "Küche"]
    assert index.right_group_caption is not None
    assert index.right_group_caption.attrs["CaptionType"] == "13"


def test_index_without_rooms_has_none(sample_project):
    index = build_index(sample_project)
    assert index.place_caption is None
    assert index.places == []
    assert index.right_group_caption is None


def test_old_project_has_rooms_but_no_rights_group_caption(places_only_project):
    index = build_index(places_only_project)
    assert [p.attrs["Title"] for p in index.places] == ["Küche"]
    assert index.right_group_caption is None


def test_rights_group_caption_is_found_when_nested(sample_project):
    """In most real files the rights groups sit under another `LoxCaption`,
    not directly under `Document` (design 2026-09-29, section 3)."""
    nested = (
        '\t\t<C Type="LoxCaption" V="175" U="3000-0020-0000-aaaaaaaaaaaaaaaa" Title="Benutzer">\r\n'
        '\t\t\t<C Type="LoxCaption" V="175" U="3000-0010-0000-aaaaaaaaaaaaaaaa"'
        ' Title="Berechtigungsgruppen" CaptionType="13" SubType="13"></C>\r\n'
        "\t\t</C>\r\n"
    )
    index = build_index(with_rooms(sample_project, nested))
    assert index.right_group_caption is not None
    assert index.right_group_caption.attrs["U"] == "3000-0010-0000-aaaaaaaaaaaaaaaa"


def test_place_caption_directly_under_control_list_is_found(sample_project):
    """The oldest checked file (2014) has its `PlaceCaption` directly under
    `<ControlList>`, not under `Document`."""
    anchor = '<ControlList Version="275" NextObj="100">\r\n'
    project = sample_project.replace(anchor, anchor + PLACES_ONLY_XML, 1)
    index = build_index(project)
    assert [p.attrs["Title"] for p in index.places] == ["Küche"]
```

At the top of `tests/projectsync/test_index.py`, add:

```python
from room_fixtures import PLACES_ONLY_XML, with_rooms
```

Imports belong at the top of a module: place the new `from room_fixtures
import ...` line in the conftest's import block, and let `uv run ruff check
--fix tests/projectsync` settle the order.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/projectsync/test_index.py -v`
Expected: the five new tests FAIL with `AttributeError: 'ProjectIndex' object has no attribute 'place_caption'`.

- [ ] **Step 4: Implement**

In `src/loxmatter/projectsync/index.py`:

Change `from dataclasses import dataclass` to `from dataclasses import dataclass, field` and add `from collections.abc import Callable, Sequence` (replacing the existing `Sequence`-only import).

Append three fields at the END of `ProjectIndex` (with defaults, so the dataclass stays valid after the fields without defaults):

```python
    # Rooms (design 2026-09-29, section 3). They belong to the whole
    # project, not to a `LoxLIVE` block, so they are searched in the whole
    # tree - under `Document` in every file since 2015, directly under
    # `<ControlList>` in the oldest one. `None`/empty for a file without a
    # room list; `projectsync.rooms` then creates no room.
    place_caption: Element | None = None
    places: list[Element] = field(default_factory=list)
    # The `LoxCaption` with `CaptionType="13"` ("Berechtigungsgruppen")
    # that holds one `RightGroup` per room. `None` in projects from before
    # the rights system; a room created there gets no `RGR`.
    right_group_caption: Element | None = None
```

Add the helper next to `_find_all_loxlive`:

```python
def _find_first(elements: list[Element], matches: Callable[[Element], bool]) -> Element | None:
    """The first element in document order, at any depth, for which
    `matches` holds - like `_find_all_loxlive`, without assuming a fixed
    nesting depth."""
    for element in elements:
        if matches(element):
            return element
        found = _find_first(element.children, matches)
        if found is not None:
            return found
    return None
```

In `build_index`, before `return ProjectIndex(`:

```python
    place_caption = _find_first(top_level, lambda e: e.type == "PlaceCaption")
    right_group_caption = _find_first(
        top_level, lambda e: e.type == "LoxCaption" and e.attrs.get("CaptionType") == "13"
    )
```

and add to the `ProjectIndex(...)` call:

```python
        place_caption=place_caption,
        places=[]
        if place_caption is None
        else [e for e in place_caption.children if e.type == "Place"],
        right_group_caption=right_group_caption,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/projectsync -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/projectsync/index.py tests/projectsync/
git commit -m "feat(projectsync): read the project file's rooms and rights groups

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Matching loxmatter rooms against Loxone rooms

**Files:**
- Create: `src/loxmatter/projectsync/rooms.py`
- Test: `tests/projectsync/test_rooms.py`

**Interfaces:**
- Consumes: `ProjectIndex.place_caption`, `ProjectIndex.places` (Task 1).
- Produces:
  - `class RoomStatus(StrEnum)`: `FOUND = "found"`, `CREATED = "created"`, `AMBIGUOUS = "ambiguous"`, `NOT_CREATABLE = "not_creatable"`
  - `@dataclass(frozen=True) class RoomAssignment`: `name: str` (loxmatter's spelling), `status: RoomStatus`, `loxone_title: str | None` (title of the target Loxone room; the loxmatter name, stripped, for `CREATED`; `None` for `NOT_CREATABLE`), `place_u: str | None` (`U` of the existing room for `FOUND`/`AMBIGUOUS`, else `None`); property `targets_a_room -> bool` (`status is not NOT_CREATABLE`)
  - `room_key(name: str) -> str`
  - `can_create_rooms(index: ProjectIndex) -> bool`
  - `assign_rooms(index: ProjectIndex, names: Iterable[str]) -> list[RoomAssignment]` - one assignment per distinct `room_key`, in first-seen order, blank names skipped

- [ ] **Step 1: Write the failing tests**

Create `tests/projectsync/test_rooms.py` (with the repository's GPL header, copied from any other test file):

```python
"""Tests for `projectsync.rooms` - design 2026-09-29, sections 4 and 6."""

from __future__ import annotations

from loxmatter.projectsync.index import build_index
from loxmatter.projectsync.rooms import RoomAssignment, RoomStatus, assign_rooms, room_key


def test_room_key_ignores_case_and_surrounding_whitespace():
    assert room_key("  Küche ") == room_key("küche") == room_key("KÜCHE")


def test_an_existing_room_is_found_whatever_the_case(rooms_project):
    index = build_index(rooms_project)
    assert assign_rooms(index, ["küche"]) == [
        RoomAssignment(
            "küche", RoomStatus.FOUND, "Küche", "3000-0002-0000-aaaaaaaaaaaaaaaa"
        )
    ]


def test_a_missing_room_is_created_under_its_loxmatter_name(rooms_project):
    index = build_index(rooms_project)
    [assignment] = assign_rooms(index, ["Werkstatt"])
    assert assignment == RoomAssignment("Werkstatt", RoomStatus.CREATED, "Werkstatt", None)
    assert assignment.targets_a_room


def test_one_assignment_per_room_however_many_names_share_it(rooms_project):
    index = build_index(rooms_project)
    assignments = assign_rooms(index, ["Werkstatt", "Küche", "werkstatt", "Küche"])
    assert [a.name for a in assignments] == ["Werkstatt", "Küche"]


def test_blank_names_get_no_assignment(rooms_project):
    index = build_index(rooms_project)
    assert assign_rooms(index, ["", "   "]) == []


def test_two_loxone_rooms_with_one_title_use_the_first(rooms_project):
    duplicate = (
        '<C Type="Place" V="175" U="3000-0003-0000-aaaaaaaaaaaaaaaa" Title="KÜCHE"'
        ' WF="16384"/>'
    )
    anchor = "\t\t</C>\r\n\t\t<C Type=\"LoxCaption\""
    assert anchor in rooms_project
    project = rooms_project.replace(anchor, duplicate + anchor, 1)
    index = build_index(project)
    assert assign_rooms(index, ["Küche"]) == [
        RoomAssignment(
            "Küche", RoomStatus.AMBIGUOUS, "Küche", "3000-0002-0000-aaaaaaaaaaaaaaaa"
        )
    ]


def test_without_a_room_list_nothing_can_be_created(sample_project):
    index = build_index(sample_project)
    [assignment] = assign_rooms(index, ["Küche"])
    assert assignment == RoomAssignment("Küche", RoomStatus.NOT_CREATABLE, None, None)
    assert not assignment.targets_a_room


def test_an_old_project_without_rights_groups_can_still_get_rooms(places_only_project):
    index = build_index(places_only_project)
    assert assign_rooms(index, ["Werkstatt"])[0].status is RoomStatus.CREATED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/projectsync/test_rooms.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.projectsync.rooms'`.

- [ ] **Step 3: Implement**

Create `src/loxmatter/projectsync/rooms.py` (GPL header copied from `projectsync/diff.py`):

```python
"""Matches loxmatter's room names against the rooms of a Loxone project
file (design `docs/superpowers/specs/
2026-09-29-loxone-rooms-in-project-sync-design.md`, sections 4 and 6).

Pure decisions, no text editing and no IDs: which Loxone room a name
means, and whether a missing one can be created. `patch.py` generates
the `U` values of created rooms, like every other new object's."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.scan import Element

__all__ = ["RoomAssignment", "RoomStatus", "assign_rooms", "can_create_rooms", "room_key"]


class RoomStatus(StrEnum):
    FOUND = "found"
    CREATED = "created"
    # Several Loxone rooms share the folded title; the first in document
    # order is used (design section 4).
    AMBIGUOUS = "ambiguous"
    # No match, and the file has no room list to add one to (section 6.3).
    NOT_CREATABLE = "not_creatable"


@dataclass(frozen=True)
class RoomAssignment:
    # The room name as loxmatter stores it - the first spelling met, when
    # several devices spell the same room differently.
    name: str
    status: RoomStatus
    # The title of the Loxone room new objects go into: the existing
    # room's for FOUND/AMBIGUOUS, the stripped loxmatter name for CREATED.
    loxone_title: str | None
    # `U` of the existing Loxone room; `None` for CREATED (generated at
    # patch time) and NOT_CREATABLE.
    place_u: str | None

    @property
    def targets_a_room(self) -> bool:
        return self.status is not RoomStatus.NOT_CREATABLE


def room_key(name: str) -> str:
    """The form in which two room names are compared: `küche` in loxmatter
    finds `Küche` in Loxone Config instead of creating a second room."""
    return name.strip().casefold()


def can_create_rooms(index: ProjectIndex) -> bool:
    """Whether a new `Place` has somewhere to go. A self-closing
    `PlaceCaption` has no content range to append to - Loxone Config never
    writes one, since the `Nicht zugeordnet` room always exists."""
    caption = index.place_caption
    return caption is not None and not caption.self_closing and caption.inner_end is not None


def assign_rooms(index: ProjectIndex, names: Iterable[str]) -> list[RoomAssignment]:
    """One assignment per distinct room (by `room_key`), in the order the
    names first occur; blank names are skipped."""
    places_by_key: dict[str, list[Element]] = {}
    for place in index.places:
        places_by_key.setdefault(room_key(place.attrs.get("Title", "")), []).append(place)
    creatable = can_create_rooms(index)

    assignments: list[RoomAssignment] = []
    seen: set[str] = set()
    for name in names:
        key = room_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        matches = places_by_key.get(key, [])
        if matches:
            first = matches[0]
            status = RoomStatus.AMBIGUOUS if len(matches) > 1 else RoomStatus.FOUND
            assignments.append(
                RoomAssignment(name, status, first.attrs.get("Title", name), first.attrs.get("U"))
            )
        elif creatable:
            assignments.append(RoomAssignment(name, RoomStatus.CREATED, name.strip(), None))
        else:
            assignments.append(RoomAssignment(name, RoomStatus.NOT_CREATABLE, None, None))
    return assignments
```

Add `"rooms"` handling to `src/loxmatter/projectsync/__init__.py` only if that file re-exports submodules today (read it first; if it only holds a docstring, leave it).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/projectsync/test_rooms.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/rooms.py tests/projectsync/test_rooms.py
git commit -m "feat(projectsync): match loxmatter room names against Loxone rooms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The plan carries rooms

**Files:**
- Modify: `src/loxmatter/projectsync/diff.py`
- Test: `tests/projectsync/test_diff.py`

**Interfaces:**
- Consumes: `assign_rooms`, `room_key`, `RoomAssignment` (Task 2).
- Produces:
  - `PlanEntry.room: str | None = None` - the owner's loxmatter room, set only on `NEW_SIGNAL`/`NEW_DEVICE` entries whose owner has a room.
  - `SyncPlan.rooms: list[RoomAssignment]` (default empty) - `assign_rooms` over the rooms of those entries.
  - `SyncPlan.room_for(entry: PlanEntry) -> RoomAssignment | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/projectsync/test_diff.py` (add `import dataclasses` and `from loxmatter.projectsync.rooms import RoomStatus` to its imports):

```python
def test_new_entries_carry_their_devices_room(rooms_project):
    """`d1_1_temp` is new in an existing container, device 2 is new
    altogether; `d1_1_onoff` exists and gets no room (design 2026-09-29,
    section 5)."""
    index = build_index(rooms_project)
    device1 = dataclasses.replace(_device(1, "Altes Geraet"), room="küche")
    device2 = dataclasses.replace(_device(2, "Neues Geraet"), room="Werkstatt")
    signals = {
        1: [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, endpoint=2)],
        2: [_signal("d2_1_onoff", 2)],
    }
    plan = build_plan(index, [device1, device2], signals, {1: [], 2: []})

    by_key = {entry.key: entry for entry in plan.entries}
    assert by_key["d1_1_onoff"].room is None
    assert by_key["d1_1_temp"].room == "küche"
    assert by_key["d2_1_onoff"].room == "Werkstatt"
    assert [(a.name, a.status) for a in plan.rooms] == [
        ("küche", RoomStatus.FOUND),
        ("Werkstatt", RoomStatus.CREATED),
    ]
    assignment = plan.room_for(by_key["d1_1_temp"])
    assert assignment is not None and assignment.loxone_title == "Küche"
    assert plan.room_for(by_key["d1_1_onoff"]) is None


def test_a_room_only_existing_objects_need_is_not_assigned(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    plan = build_plan(index, [device], {1: [_signal("d1_1_onoff", 1)]}, {1: []})
    assert plan.rooms == []


def test_a_device_without_a_room_gets_no_assignment(rooms_project):
    index = build_index(rooms_project)
    plan = build_plan(index, [_device(2, "Neues Geraet")], {2: [_signal("d2_1_onoff", 2)]}, {2: []})
    assert plan.rooms == []
    assert all(entry.room is None for entry in plan.entries)
```

Groups are covered in `tests/projectsync/test_group_sync.py`, whose
`group_store` fixture builds a real store with two lamps and one group.
Append there (add `from loxmatter.projectsync.rooms import RoomStatus` to
its imports):

```python
def test_a_new_group_output_carries_the_groups_room(rooms_project, group_store):
    store, group = group_store
    store.set_group_room(group.id, "Küche")
    index = build_index(rooms_project)
    plan = build_plan(
        index,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    new = [e for e in plan.entries if e.owner_kind == "group"]
    assert new and all(e.room == "Küche" for e in new)
    assert [a.status for a in plan.rooms] == [RoomStatus.FOUND]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/projectsync/test_diff.py tests/projectsync/test_group_sync.py -v`
Expected: the new tests FAIL (`AttributeError: 'PlanEntry' object has no attribute 'room'`).

- [ ] **Step 3: Implement**

In `src/loxmatter/projectsync/diff.py`:

Imports: `from dataclasses import dataclass, field, replace` and
`from loxmatter.projectsync.rooms import RoomAssignment, assign_rooms, room_key`.

Add to `PlanEntry` after `owner_kind`:

```python
    # The owner's room in loxmatter (design 2026-09-29, section 5) - only
    # on NEW_SIGNAL/NEW_DEVICE entries: an object that already exists keeps
    # whatever room Loxone Config gave it, so its room is never looked at.
    room: str | None = None
```

Replace `SyncPlan` with:

```python
@dataclass(frozen=True)
class SyncPlan:
    entries: list[PlanEntry]
    # One per distinct room a new entry needs (design 2026-09-29, section 7).
    rooms: list[RoomAssignment] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(
            entry.status in (PlanStatus.UPDATED, PlanStatus.NEW_SIGNAL, PlanStatus.NEW_DEVICE)
            for entry in self.entries
        )

    def room_for(self, entry: PlanEntry) -> RoomAssignment | None:
        """The room assignment `entry` goes into, if it has a room."""
        if entry.room is None:
            return None
        key = room_key(entry.room)
        return next((a for a in self.rooms if room_key(a.name) == key), None)
```

Add above `build_plan`:

```python
_NEW_STATUSES = (PlanStatus.NEW_SIGNAL, PlanStatus.NEW_DEVICE)


def _with_owner_room(entry: PlanEntry, room: str | None) -> PlanEntry:
    if room is None or entry.status not in _NEW_STATUSES:
        return entry
    return replace(entry, room=room)
```

In `build_plan`, after the group loop and BEFORE `entries += _orphaned_entries(...)`:

```python
    owner_rooms: dict[tuple[str, int], str | None] = {
        ("device", device.id): device.room for device in devices
    }
    owner_rooms.update({("group", group.id): group.room for group in groups})
    entries = [
        _with_owner_room(entry, owner_rooms.get((entry.owner_kind, entry.device_id)))
        for entry in entries
    ]
```

and replace `return SyncPlan(entries)` with:

```python
    rooms = assign_rooms(index, [entry.room for entry in entries if entry.room is not None])
    return SyncPlan(entries, rooms)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/projectsync -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/diff.py tests/projectsync/
git commit -m "feat(projectsync): new plan entries carry their owner's room

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Tags for rooms and a room in `IoData`

**Files:**
- Modify: `src/loxmatter/projectsync/schema.py`
- Test: `tests/projectsync/test_schema.py`

**Interfaces:**
- Produces:
  - `new_place_tag(title: str, u: str, rgr_u: str | None, template: Mapping[str, str] | None) -> str` - self-closing `<C Type="Place" .../>`
  - `new_right_group_tag(title: str, u: str) -> str` - self-closing `<C Type="RightGroup" .../>`
  - `new_cmd_children_xml(..., place_u: str | None = None)` - when set, the written `IoData` carries `Pr=place_u` (replacing a copied `Pr` in place, or added after the copied attributes; an `IoData` with only `Pr` if nothing was copied)

- [ ] **Step 1: Write the failing tests**

Append to `tests/projectsync/test_schema.py` (import `new_place_tag`, `new_right_group_tag`, `new_cmd_children_xml` from `loxmatter.projectsync.schema` if not imported yet):

```python
def test_new_place_tag_copies_icon_and_ptype_from_the_template():
    template = {
        "Title": "Küche",
        "WF": "16384",
        "Icon": "0000005a-00ff-0000-0000000000000000",
        "PType": "3",
        "PGroup": "4",
        "Rating": "1",
    }
    tag = new_place_tag("Werkstatt", "u-place", "u-rights", template)
    assert tag == (
        '<C Type="Place" V="178" U="u-place" Title="Werkstatt" WF="16384"'
        ' Icon="0000005a-00ff-0000-0000000000000000" PType="3" RGR="u-rights"/>'
    )


def test_new_place_tag_without_template_or_rights_group():
    assert new_place_tag("Werkstatt", "u-place", None, None) == (
        '<C Type="Place" V="178" U="u-place" Title="Werkstatt" WF="16384"/>'
    )


def test_new_place_tag_escapes_the_title():
    assert 'Title="Bad &amp; WC"' in new_place_tag("Bad & WC", "u", None, None)


def test_new_right_group_tag():
    assert new_right_group_tag("Werkstatt", "u-rights") == (
        '<C Type="RightGroup" V="178" U="u-rights" Title="Werkstatt" Cl="0,0,0"'
        ' WF="16384" GT="1" MG=""/>'
    )


def test_place_u_replaces_the_copied_room():
    xml = new_cmd_children_xml(
        kind="output",
        existing_u={"1000-0000-0000-aaaaaaaaaaaaaaaa"},
        iodata_attrs={"Cr": "cat", "Pr": "old-room"},
        place_u="new-room",
    )
    assert '<IoData Cr="cat" Pr="new-room"/>' in xml


def test_place_u_without_a_copied_iodata_writes_pr_only():
    xml = new_cmd_children_xml(
        kind="output",
        existing_u={"1000-0000-0000-aaaaaaaaaaaaaaaa"},
        iodata_attrs=None,
        place_u="new-room",
    )
    assert '<IoData Pr="new-room"/>' in xml


def test_without_place_u_the_copied_iodata_is_unchanged():
    xml = new_cmd_children_xml(
        kind="output",
        existing_u={"1000-0000-0000-aaaaaaaaaaaaaaaa"},
        iodata_attrs={"Cr": "cat", "Pr": "old-room"},
    )
    assert '<IoData Cr="cat" Pr="old-room"/>' in xml
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/projectsync/test_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'new_place_tag'`.

- [ ] **Step 3: Implement**

In `src/loxmatter/projectsync/schema.py` add `from collections.abc import Mapping`, and after `new_caption_open_tag`:

```python
# Room attributes worth copying from an existing room (design 2026-09-29,
# section 6.1). Both are undocumented; copying them from a room Loxone
# Config wrote itself beats inventing values. `PGroup`, `Rating` and
# `UseFav` are the user's own choices for that one room; `First` and its
# `WF` mark the `Nicht zugeordnet` room, which is why `patch._room_edits`
# never picks that one as the template.
_PLACE_TEMPLATE_ATTRS = ("Icon", "PType")


def new_place_tag(
    title: str, u: str, rgr_u: str | None, template: Mapping[str, str] | None
) -> str:
    """A new room (`<C Type="Place" .../>`, self-closing like every room in
    a real file). `rgr_u` is the `U` of its companion `RightGroup`, or
    `None` in a project from before the rights system, whose rooms carry
    no `RGR` (design 2026-09-29, section 6.2)."""
    attrs = [("Type", "Place"), ("V", "178"), ("U", u), ("Title", title), ("WF", "16384")]
    if template:
        attrs += [(name, template[name]) for name in _PLACE_TEMPLATE_ATTRS if name in template]
    if rgr_u is not None:
        attrs.append(("RGR", rgr_u))
    return f"<C {render_attrs(attrs)}/>"


def new_right_group_tag(title: str, u: str) -> str:
    """The user rights group Loxone Config keeps next to every room, under
    the same title (design 2026-09-29, section 3). The attribute values are
    the ones every room's rights group carries in the checked files."""
    attrs = [
        ("Type", "RightGroup"),
        ("V", "178"),
        ("U", u),
        ("Title", title),
        ("Cl", "0,0,0"),
        ("WF", "16384"),
        ("GT", "1"),
        ("MG", ""),
    ]
    return f"<C {render_attrs(attrs)}/>"
```

Change `new_cmd_children_xml`: add the keyword parameter `place_u: str | None = None` after `unit_format`, extend the docstring with one paragraph:

```
    **`place_u` (design 2026-09-29, section 5):** the `U` of the Loxone
    room this object belongs in. It replaces the `Pr` copied from a
    neighbour, keeping the copied `Cr` (the category is not this
    feature's business); without a neighbour to copy from, the `IoData`
    carries `Pr` alone.
```

and replace the block

```python
    parts = list(connectors)
    if iodata_attrs:
```

with

```python
    if place_u is not None:
        iodata_attrs = {**(iodata_attrs or {}), "Pr": place_u}

    parts = list(connectors)
    if iodata_attrs:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/projectsync/test_schema.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/projectsync/schema.py tests/projectsync/test_schema.py
git commit -m "feat(projectsync): tags for new rooms and a room in a new cmd's IoData

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The patch writes rooms

**Files:**
- Modify: `src/loxmatter/projectsync/patch.py`
- Test: `tests/projectsync/test_patch_rooms.py` (create)

**Interfaces:**
- Consumes: `SyncPlan.rooms`, `SyncPlan.room_for`, `RoomStatus`, `room_key` (Tasks 2-3); `new_place_tag`, `new_right_group_tag`, `new_cmd_children_xml(place_u=...)` (Task 4); `ProjectIndex.place_caption/places/right_group_caption` (Task 1).
- Produces: `apply_plan` output with created rooms and `Pr` set on new cmds. No signature change.

- [ ] **Step 1: Write the failing tests**

Create `tests/projectsync/test_patch_rooms.py` (GPL header). The helpers `_signal`/`_device`/`_patch` are the ones in `tests/projectsync/test_patch.py`; copy them into this file unchanged (tests do not import each other here):

```python
"""Rooms in the patched file - design 2026-09-29, sections 5 and 6."""

from __future__ import annotations

import dataclasses
import re

from loxmatter.export.signals import SignalKind
from loxmatter.model.store import SignalRef, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import build_plan
from loxmatter.projectsync.index import build_index
from loxmatter.projectsync.patch import apply_plan

KITCHEN_U = "3000-0002-0000-aaaaaaaaaaaaaaaa"


# `_signal`, `_device`, `_patch_bytes`, `_patch`: copied from test_patch.py.


def _cmd_xml(patched: str, key: str) -> str:
    """The whole `<C ...>...</C>` text of the cmd whose Check/CmdOn names `key`."""
    match = re.search(rf'<C Type="Virtual\w+Cmd"[^>]*{re.escape(key)}[^>]*>.*?</C>', patched, re.S)
    assert match is not None, key
    return match.group(0)


def _places(patched: str) -> list[str]:
    return re.findall(r'<C Type="Place"[^>]*/>', patched)


def _right_groups(patched: str) -> list[str]:
    return re.findall(r'<C Type="RightGroup"[^>]*/>', patched)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    assert match is not None, (tag, name)
    return match.group(1)


def test_a_new_signal_goes_into_the_matching_room(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="küche")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    assert f'Pr="{KITCHEN_U}"' in _cmd_xml(patched, "d1_1_temp")
    # The copied category stays.
    assert 'Cr="1000-0005-0000-aaaaaaaaaaaaaaaa"' in _cmd_xml(patched, "d1_1_temp")
    assert len(_places(patched)) == 2  # no room created


def test_an_existing_object_keeps_its_room(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Küche")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    assert 'Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"' in _cmd_xml(patched, "d1_1_onoff")


def test_a_missing_room_is_created_with_its_rights_group(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(2, "Neues Geraet"), room="Werkstatt")
    patched = _patch(index, device, [_signal("d2_1_onoff", 2)])

    places = _places(patched)
    rights = _right_groups(patched)
    assert len(places) == 3 and len(rights) == 3
    new_place, new_rights = places[-1], rights[-1]
    assert _attr(new_place, "Title") == "Werkstatt"
    assert _attr(new_rights, "Title") == "Werkstatt"
    assert _attr(new_place, "RGR") == _attr(new_rights, "U")
    # Icon/PType from `Küche`, not from `Nicht zugeordnet`.
    assert _attr(new_place, "PType") == "3"
    assert 'First="true"' not in new_place
    # Every cmd of the new device points at the new room.
    for key in ("d2_1_onoff", "d2_online"):
        assert f'Pr="{_attr(new_place, "U")}"' in _cmd_xml(patched, key)


def test_a_room_shared_by_two_devices_is_created_once(rooms_project):
    index = build_index(rooms_project)
    devices = [
        dataclasses.replace(_device(2, "Zwei"), room="Werkstatt"),
        dataclasses.replace(_device(3, "Drei"), room="werkstatt"),
    ]
    signals = {2: [_signal("d2_1_onoff", 2)], 3: [_signal("d3_1_onoff", 3)]}
    commands = {2: [], 3: []}
    plan = build_plan(index, devices, signals, commands)
    patched = apply_plan(
        index, plan, devices, signals, commands, bridge_ip="10.0.0.5", port=7000, listen=8080
    ).decode("utf-8-sig")
    assert [_attr(p, "Title") for p in _places(patched)].count("Werkstatt") == 1


def test_next_obj_counts_the_room_objects(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    # One new cmd + one Place + one RightGroup.
    assert 'NextObj="103"' in patched


def test_an_old_project_gets_a_room_without_rights_group(places_only_project):
    index = build_index(places_only_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    new_place = _places(patched)[-1]
    assert _attr(new_place, "Title") == "Werkstatt"
    assert "RGR=" not in new_place
    assert _right_groups(patched) == []
    assert 'NextObj="102"' in patched


def test_without_a_room_list_new_objects_keep_the_neighbours_room(sample_project):
    index = build_index(sample_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    assert 'Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"' in _cmd_xml(patched, "d1_1_temp")
    assert _places(patched) == []


def test_everything_outside_the_edits_is_byte_identical(rooms_project):
    """Removing exactly the inserted objects and the changed counter gives
    back the input (the same guarantee `test_patch.py` checks for the other
    edits)."""
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    stripped = patched
    stripped = stripped.replace(_places(patched)[-1], "")
    stripped = stripped.replace(_right_groups(patched)[-1], "")
    stripped = stripped.replace(_cmd_xml(patched, "d1_1_temp"), "")
    stripped = stripped.replace('NextObj="103"', 'NextObj="100"')
    assert stripped == rooms_project
```

Read `test_patch.py`'s existing byte-identity test before running: if `_patch` prepends a BOM that the input lacks, or if the sample project's first input container changes in another way, adjust `test_everything_outside_the_edits_is_byte_identical` to the same normalisation that test uses - do not weaken the assertion to a substring check.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/projectsync/test_patch_rooms.py -v`
Expected: the room tests FAIL (no `Pr` change, no new `Place`); `test_without_a_room_list_new_objects_keep_the_neighbours_room` and `test_an_existing_object_keeps_its_room` may already PASS.

- [ ] **Step 3: Implement**

In `src/loxmatter/projectsync/patch.py`:

Imports - add:

```python
from loxmatter.projectsync.rooms import RoomStatus, room_key
```

and add `new_place_tag`, `new_right_group_tag` to the `projectsync.schema` import list.

Add after `_display_format`:

```python
def _room_edits(index: ProjectIndex, plan: SyncPlan) -> tuple[dict[str, str], list[_Edit], int]:
    """Where every room of the plan lives in the patched file (design
    2026-09-29, section 6): returns `room_key` -> `Place.U` for every room
    a new object can point at, the insertions for rooms this sync creates,
    and the number of objects created (for `NextObj`).

    All new rooms go in ONE insertion at the end of the `PlaceCaption`,
    and all new rights groups in one at the end of their caption: two
    insertions at the same position would come out in reverse order
    (`_apply_edits` writes back to front)."""
    place_us: dict[str, str] = {
        room_key(a.name): a.place_u
        for a in plan.rooms
        if a.status in (RoomStatus.FOUND, RoomStatus.AMBIGUOUS) and a.place_u
    }
    to_create = [a for a in plan.rooms if a.status is RoomStatus.CREATED]
    if not to_create:
        return place_us, [], 0

    caption = index.place_caption
    # `rooms.can_create_rooms` is why an assignment is CREATED at all.
    assert caption is not None and caption.inner_end is not None
    rights = index.right_group_caption
    rights_end = None if rights is None else rights.inner_end
    # `Nicht zugeordnet` (`First="true"`) carries its own `WF` and marks
    # itself as the default room - never a template for an ordinary one.
    template = next((p.attrs for p in index.places if p.attrs.get("First") != "true"), None)

    places_xml: list[str] = []
    rights_xml: list[str] = []
    for assignment in to_create:
        title = assignment.loxone_title or assignment.name.strip()
        place_u = new_unique_id(index.all_u_values)
        rights_u = None if rights_end is None else new_unique_id(index.all_u_values)
        places_xml.append(new_place_tag(title, place_u, rights_u, template))
        if rights_u is not None:
            rights_xml.append(new_right_group_tag(title, rights_u))
        place_us[room_key(assignment.name)] = place_u

    edits = [_Edit(caption.inner_end, caption.inner_end, "".join(places_xml))]
    if rights_end is not None and rights_xml:
        edits.append(_Edit(rights_end, rights_end, "".join(rights_xml)))
    return place_us, edits, len(places_xml) + len(rights_xml)


def _place_u_for(place_us: Mapping[str, str], entry: PlanEntry) -> str | None:
    return None if entry.room is None else place_us.get(room_key(entry.room))
```

`_new_signal_edit`: add the parameter `place_u: str | None` after `entries_by_key`, and pass `place_u=place_u` into its `new_cmd_children_xml(...)` call.

`_new_device_edit`: add the parameter `place_u: str | None` after `listen`, and pass `place_u=place_u` into its `new_cmd_children_xml(...)` call. All entries of one call share one owner, so one room.

In `apply_plan`, replace

```python
    edits: list[_Edit] = []
    created_count = 0
```

with

```python
    place_us, edits, created_count = _room_edits(index, plan)
```

change `edits.append(_new_signal_edit(index, entry, source))` to

```python
            edits.append(_new_signal_edit(index, entry, source, _place_u_for(place_us, entry)))
```

and the `_new_device_edit(...)` call to

```python
        edit, group_created_count = _new_device_edit(
            index,
            group_entries,
            source,
            bridge_ip,
            port,
            listen,
            _place_u_for(place_us, group_entries[0]),
        )
```

Extend the `apply_plan` docstring's first paragraph with: "and, for new objects whose owner has a room, the Loxone rooms they go into (design 2026-09-29)."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/projectsync -v`
Expected: all PASS, including the untouched `test_patch.py` and `test_group_sync.py`.

- [ ] **Step 5: Type-check**

Run: `uv run mypy`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/projectsync/patch.py tests/projectsync/test_patch_rooms.py
git commit -m "feat(projectsync): new inputs and outputs land in their Loxone room

A room Loxone Config does not have yet is created as a Place with its
RightGroup; objects that already exist keep the room Config gave them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The API returns rooms

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/api/project_sync.py`
- Test: `tests/api/test_project_sync_api.py`

**Interfaces:**
- Consumes: `SyncPlan.rooms`, `SyncPlan.room_for`, `RoomAssignment.targets_a_room` (Tasks 2-3).
- Produces: JSON `rooms: [{"name": str, "status": "found"|"created"|"ambiguous"|"not_creatable", "loxone_title": str|null}]` on the plan; `target_room: str|null` on each entry. The WebUI (Task 7) reads exactly these names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_project_sync_api.py`:

```python
ROOMS_XML = (
    '\t\t<C Type="PlaceCaption" V="175" U="3000-0000-0000-aaaaaaaaaaaaaaaa" Title="Räume">\r\n'
    '\t\t\t<C Type="Place" V="175" U="3000-0002-0000-aaaaaaaaaaaaaaaa" Title="Küche"'
    ' WF="16384" PType="3"/>\r\n'
    "\t\t</C>\r\n"
)


def _with_rooms(project: str) -> bytes:
    anchor = 'Title="Testprojekt">\r\n'
    return project.replace(anchor, anchor + ROOMS_XML, 1).encode("utf-8")


async def test_project_sync_reports_a_found_room_and_the_entries_target(api):
    client, store = api
    [device] = store.devices()
    store.set_room(device.id, "küche")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("projekt.Loxone", _with_rooms(SAMPLE_PROJECT), "application/xml")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rooms"] == [{"name": "küche", "status": "found", "loxone_title": "Küche"}]
    assert {entry["target_room"] for entry in body["entries"]} == {"Küche"}
    patched = base64.b64decode(body["patched_base64"])
    assert b'Pr="3000-0002-0000-aaaaaaaaaaaaaaaa"' in patched


async def test_project_sync_reports_a_room_it_creates(api):
    client, store = api
    [device] = store.devices()
    store.set_room(device.id, "Werkstatt")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("projekt.Loxone", _with_rooms(SAMPLE_PROJECT), "application/xml")},
    )
    body = response.json()
    assert body["rooms"] == [
        {"name": "Werkstatt", "status": "created", "loxone_title": "Werkstatt"}
    ]
    assert b'Title="Werkstatt"' in base64.b64decode(body["patched_base64"])


async def test_project_sync_without_a_room_list_names_no_target(api):
    client, store = api
    [device] = store.devices()
    store.set_room(device.id, "Werkstatt")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("projekt.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
    )
    body = response.json()
    assert body["rooms"] == [
        {"name": "Werkstatt", "status": "not_creatable", "loxone_title": None}
    ]
    assert all(entry["target_room"] is None for entry in body["entries"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_project_sync_api.py -v`
Expected: the three new tests FAIL with `KeyError: 'rooms'`.

- [ ] **Step 3: Implement**

In `src/loxmatter/api/models.py`, add `target_room: str | None = None` as the last field of `ProjectSyncEntryOut`, with this sentence appended to its docstring: "`target_room` (design 2026-09-29, section 7) is the title of the Loxone room a new entry goes into, `None` when it keeps its neighbour's room."

Add before `ProjectSyncPlanOut`:

```python
class ProjectSyncRoomOut(BaseModel):
    """One loxmatter room a new entry needs, and what the sync does with it
    (`projectsync.rooms.RoomAssignment`, design 2026-09-29, section 7)."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: str
    loxone_title: str | None
```

and in `ProjectSyncPlanOut` after `entries`:

```python
    rooms: list[ProjectSyncRoomOut] = Field(default_factory=list)
```

In `src/loxmatter/api/project_sync.py`, import `ProjectSyncRoomOut` next to the other models, and in `_entries_out` add as the last keyword of `ProjectSyncEntryOut(...)`:

```python
            target_room=_target_room(plan, entry),
```

with, above `_entries_out`:

```python
def _target_room(plan: SyncPlan, entry: PlanEntry) -> str | None:
    assignment = plan.room_for(entry)
    if assignment is None or not assignment.targets_a_room:
        return None
    return assignment.loxone_title
```

(import `PlanEntry` from `loxmatter.projectsync.diff`). In the final `return ProjectSyncPlanOut(...)` add:

```python
            rooms=[
                ProjectSyncRoomOut(
                    name=a.name, status=a.status.value, loxone_title=a.loxone_title
                )
                for a in result.plan.rooms
            ],
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_project_sync_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/ tests/api/test_project_sync_api.py
git commit -m "feat(api): project sync returns the rooms new objects go into

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The WebUI shows rooms

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/web/style.css`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `projectSync.plan.rooms`, `entry.target_room` (Task 6).
- Produces: app methods `projectSyncRoomStatusLabel(status)`, `projectSyncRoomBadgeClass(status)`, `projectSyncRoomNote(room)`.

- [ ] **Step 1: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, directly after the `web.export.projectsync_note_possible_duplicate` entry:

```yaml
web.export.projectsync_rooms_heading:
  en: "Rooms in Loxone Config"
  de: "Räume in Loxone Config"
web.export.projectsync_room_status_found:
  en: "Exists"
  de: "Vorhanden"
web.export.projectsync_room_status_created:
  en: "Will be created"
  de: "Wird angelegt"
web.export.projectsync_room_status_ambiguous:
  en: "Exists twice"
  de: "Doppelt vorhanden"
web.export.projectsync_room_status_not_creatable:
  en: "Cannot be created"
  de: "Nicht anlegbar"
web.export.projectsync_room_note_created:
  en: "Created together with its user rights group. This has not yet been tested on every Loxone Config version – please check the room after opening the file."
  de: "Wird zusammen mit seiner Berechtigungsgruppe angelegt. Noch nicht mit jeder Version von Loxone Config erprobt – bitte den Raum nach dem Öffnen der Datei prüfen."
web.export.projectsync_room_note_ambiguous:
  en: "Loxone Config has several rooms with this name; new objects go into the first."
  de: "Loxone Config hat mehrere Räume mit diesem Namen; neue Objekte kommen in den ersten."
web.export.projectsync_room_note_not_creatable:
  en: "The project file has no room list, so new objects keep the room of their neighbours."
  de: "Die Projektdatei hat keine Raumliste; neue Objekte übernehmen den Raum ihrer Nachbarn."
web.export.projectsync_target_room:
  en: "Room: {room}"
  de: "Raum: {room}"
```

- [ ] **Step 2: Write the failing markup test**

Append to `tests/api/test_web.py`, next to `test_the_projectsync_card_static_text_is_translated` (reuse that test's way of fetching the markup - read it first; it fetches `/` through the `api` fixture):

```python
async def test_the_projectsync_plan_shows_rooms(api):
    """Design 2026-09-29, section 7: a room block above the device cards and
    each new entry's target room. Markup only - the bindings are checked in
    the browser (see the plan's Task 7, step 6)."""
    client, _store = api
    markup = (await client.get("/")).text
    assert "projectSync.plan.rooms" in markup
    assert "x-text=\"t('web.export.projectsync_rooms_heading')\"" in markup
    assert "projectSyncRoomStatusLabel(room.status)" in markup
    assert "entry.target_room" in markup
    script = (await client.get("/static/app.js")).text
    for key in (
        "projectsync_room_status_found",
        "projectsync_room_status_created",
        "projectsync_room_status_ambiguous",
        "projectsync_room_status_not_creatable",
        "projectsync_room_note_created",
        "projectsync_room_note_ambiguous",
        "projectsync_room_note_not_creatable",
        "projectsync_target_room",
    ):
        assert f"web.export.{key}" in script
```

The `api` fixture and the `/static/app.js` path are the ones the neighbouring tests in `test_web.py` use.

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k projectsync -v`
Expected: `test_the_projectsync_plan_shows_rooms` FAILS on the first assertion.

- [ ] **Step 4: Implement the markup**

In `src/loxmatter/web/index.html`, inside `<div class="projectsync-plan" ...>`, directly after the closing `</div>` of `<div class="projectsync-summary" ...>` and before the `<template x-for="group in projectSyncGroupedEntries(...)">`:

```html
                <!-- Rooms new objects go into (design 2026-09-29, section 7):
                     one row per loxmatter room a new entry needs. Empty when
                     nothing new is created, so a plan with only updates shows
                     no block. -->
                <div class="projectsync-rooms" x-show="(projectSync.plan.rooms || []).length > 0">
                  <div class="projectsync-io-label" x-text="t('web.export.projectsync_rooms_heading')"></div>
                  <template x-for="room in projectSync.plan.rooms || []" :key="room.name">
                    <div class="projectsync-entry">
                      <div class="projectsync-entry-head">
                        <span class="projectsync-entry-title" x-text="room.loxone_title || room.name"></span>
                        <span
                          class="badge"
                          :class="projectSyncRoomBadgeClass(room.status)"
                          x-text="projectSyncRoomStatusLabel(room.status)"
                        ></span>
                      </div>
                      <p
                        class="hint projectsync-entry-note"
                        x-show="projectSyncRoomNote(room)"
                        x-text="projectSyncRoomNote(room)"
                      ></p>
                    </div>
                  </template>
                </div>
```

In the same file, inside the attention-entry template, directly after the `<p class="hint projectsync-entry-note" ...>` of `projectSyncEntryNote(entry)`:

```html
                              <p
                                class="hint projectsync-entry-note"
                                x-show="entry.target_room"
                                x-text="t('web.export.projectsync_target_room', { room: entry.target_room })"
                              ></p>
```

- [ ] **Step 5: Implement the helpers and style**

In `src/loxmatter/web/app.js`, after `projectSyncEntryNote(entry) { ... },`:

```js
    /** Label for a room assignment's status (`projectsync/rooms.py`,
     * `RoomStatus`). */
    projectSyncRoomStatusLabel(status) {
      const labels = {
        found: t("web.export.projectsync_room_status_found"),
        created: t("web.export.projectsync_room_status_created"),
        ambiguous: t("web.export.projectsync_room_status_ambiguous"),
        not_creatable: t("web.export.projectsync_room_status_not_creatable"),
      };
      return labels[status] || status;
    },

    /** Badge colour for a room: a created room is new, a room that could
     * not be created or exists twice wants a look, an existing one is fine
     * - the same colours `projectSyncStatusBadgeClass` uses for entries. */
    projectSyncRoomBadgeClass(status) {
      if (status === "created") {
        return this.projectSyncStatusBadgeClass("new_device");
      }
      if (status === "ambiguous" || status === "not_creatable") {
        return this.projectSyncStatusBadgeClass("possible_duplicate");
      }
      return this.projectSyncStatusBadgeClass("unchanged");
    },

    /** Explanation under a room row; empty for a room that simply exists. */
    projectSyncRoomNote(room) {
      if (room.status === "created") {
        return t("web.export.projectsync_room_note_created");
      }
      if (room.status === "ambiguous") {
        return t("web.export.projectsync_room_note_ambiguous");
      }
      if (room.status === "not_creatable") {
        return t("web.export.projectsync_room_note_not_creatable");
      }
      return "";
    },
```

In `src/loxmatter/web/style.css`, after the `.projectsync-summary { ... }` rule:

```css
/* Rooms block of the project sync plan (design 2026-09-29, section 7):
   sits between the summary and the device cards, spaced like a card. */
.projectsync-rooms {
  margin-bottom: 0.75rem;
}
```

- [ ] **Step 6: Run the tests and check in the browser**

Run: `uv run pytest tests/api/test_web.py tests/test_i18n.py -v`
Expected: all PASS.

Markup tests only prove the text is delivered. Run the bindings once for real: start the app locally (there is no `.claude/launch.json` yet: read `docs/DEVELOPMENT.md` for the dev command, create `.claude/launch.json` with it, and start it with the preview tool), log in, give a device a room in the device view, open the export tab and upload a project file built from `tests/api/test_project_sync_api.py`'s `SAMPLE_PROJECT` plus `ROOMS_XML` (write it to the scratchpad, never into the repo). Check: the "Rooms in Loxone Config" block lists the room with the right badge; a new entry shows "Room: …"; switching the language to German shows the `de` texts; a file without `ROOMS_XML` shows "Cannot be created". Take a screenshot of the room block for the task report.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/ tests/api/test_web.py
git commit -m "feat(web): show the Loxone rooms new objects go into

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Changelog and full checks

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Changelog**

In `CHANGELOG.md`, under `## [Unreleased]` → `### Added`, as the first bullet (the file is read by users before an update; no code names):

```markdown
- **The project sync puts new inputs and outputs into their room.** A
  virtual input or output the sync creates now lands in the Loxone Config
  room with the same name as its device's or group's room. A room Loxone
  Config does not have yet is created, together with its user rights group.
  Inputs and outputs that already exist keep the room you gave them in
  Loxone Config.
```

- [ ] **Step 2: Lint, format, types, language**

Run each, in the foreground:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_language.py
```

Expected: no findings; `check_language.py` prints `No German found.` If it flags a quoted German test string, that is an exemption the checker should already know (`CLAUDE.md`, "The three places German is still correct") - fix the word list or add a narrow commented exemption, never exempt a whole file.

- [ ] **Step 3: Full test suite, split**

The suite does not fit into one Bash call. Run these two in the foreground, one after the other, each with a 600000 ms timeout:

```bash
uv run pytest tests/api tests/auth tests/commands tests/devtools tests/diagnostics tests/export tests/loxone tests/matter tests/model tests/profiles tests/projectsync tests/radios tests/sources tests/zigbee -q
```

```bash
uv run pytest tests/test_*.py -q
```

The first command lists every test subfolder as of 2026-09-29; if `ls -d tests/*/` shows a new one (ignore `fixtures/` and `__pycache__/`), add it. The second takes about eight and a half minutes. Expected: both PASS.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): new inputs and outputs land in their Loxone room

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Acceptance on real software (user)**

Not automatable (spec section 6.4). Hand this to Lucien: sync a real project with one device in a room Loxone Config does not have, open the patched file in Loxone Config, and check that (1) the file opens without complaint, (2) the new room appears in the room list, (3) it appears in the user rights dialog, (4) the new inputs/outputs sit in it, (5) the device's existing objects kept their rooms.
