# Loxone rooms in the project sync

Design, September 29, 2026. Extends
[the project file sync](2026-09-03-project-file-sync-design.md) (referred to
below as "the sync design") and builds on the room field from
[the devices tab design](2026-09-05-devices-tab-rooms-and-tile-grid-design.md).

## 1. The problem

A device or group in loxmatter carries a room (`device.room`,
`device_group.room`) - a free-text name the user picks in the WebUI. The
Loxone project file has rooms of its own, and every virtual input and
output belongs to one of them. The two never meet: a virtual input or
output the sync creates takes its room from whatever neighbouring object
it copies its `<IoData>` from (`sibling_iodata_attrs`,
`find_any_iodata_attrs` in `projectsync/schema.py`). In practice that is
`Nicht zugeordnet`, or at random the room of an unrelated neighbour. The
user then sorts every new object into its room by hand in Loxone Config,
although loxmatter already knew the answer.

Goal: an input or output the sync creates lands in the Loxone room that
has the same name as its device's or group's room in loxmatter. If Loxone
Config has no such room yet, the sync creates it.

## 2. Non-goals

- **No change to objects that already exist.** Once a virtual input or
  output exists in the project file, its room belongs to Loxone Config -
  the same rule the sync design (section 3.3, update 2026-09-24) applies
  to titles. The sync never reads or writes `<IoData>` on an existing
  object, so it never reports one as `updated` because of its room, and a
  user who moves an object to another room in Config is never overridden.
  The price: moving a device to another room in loxmatter does not reach
  objects that already exist; the user moves them in Config.
- **No Loxone rooms in loxmatter.** The rooms read from the project file
  are used for matching during one sync request and are not stored.
  loxmatter keeps having no room table (devices tab design, section 3.2),
  and the room picker in the WebUI does not offer Config's rooms.
- **No room mapping table in the WebUI.** Matching is by name only
  (section 4). A user who wants a different name renames the room on one
  side.
- **No deletion or renaming of Loxone rooms,** in line with the sync
  design's section 2.
- **No categories.** `<IoData Cr="…">` (the Loxone category) keeps the
  value it gets today.

## 3. How a Loxone project file stores rooms

Checked on 17 real project files from 2014 to 2026.

A room is a `Place` object under the single `PlaceCaption`, which sits
directly under `<C Type="Document">` (in the oldest checked file, from
2014, directly under `<ControlList>`) - never under a `LoxLIVE` block.
Rooms therefore belong to the whole project, whichever Miniserver the sync
has resolved (sync design, section 3.5), and the index searches for the
caption anywhere in the tree instead of assuming a depth:

```
<C Type="PlaceCaption" V="175" U="…" Title="Räume" WF="16384" u="…">
  <C Type="Place" V="175" U="15ea0aa5-0122-3bad-…" Title=`Nicht zugeordnet` WF="6307840" Icon="…" First="true" PType="2" RGR="…"/>
  <C Type="Place" V="175" U="15ea0aa5-0128-3bcb-…" Title="Bad" WF="16384" Icon="00000218-00ff-0000-0000000000000000" Rating="1" UseFav="true" PGroup="5" PType="2" RGR="15ea0aa5-0128-3bcd-…"/>
</C>
```

In every file since the rights system arrived, each room has a companion
`RightGroup` with the same title, referenced by the room's `RGR`. The
rights groups sit under a `LoxCaption` with `CaptionType="13"`
("Berechtigungsgruppen"), which is nested under another `LoxCaption` in
most files and directly under `Document` in one:

```
<C Type="LoxCaption" V="175" U="…" Title="Berechtigungsgruppen" Cl="0,0,0" WF="20480" CaptionType="13" SubType="13">
  <C Type="RightGroup" V="175" U="15ea0aa5-0128-3bcd-…" Title="Bad" Cl="0,0,0" WF="16384" GT="1" MG=""/>
</C>
```

In all 33 rooms of one file, the rights group's title equals the room's.
The two oldest files (Config versions from 2014/2015) have no
`CaptionType="13"` caption at all, and none of their rooms carries `RGR`.

An input or output names its room in its `<IoData>` child:
`<IoData Cr="<category U>" Pr="<place U>"/>`. In the user's own grown
project file, all 193 `VirtualUdpInCmd` and all 155 `VirtualOutCmd`
objects have one with a `Pr`. The
containers (`VirtualUdpIn`, `VirtualOut`) are left out of this design:
`VirtualUdpIn` never carries an `IoData`, and the sync does not write one
for a new `VirtualOut` today either.

`PType`, `PGroup`, `Icon`, `Rating` and `UseFav` are undocumented. No
title occurred twice among the rooms of any checked file.

## 4. Matching

A loxmatter room and a Loxone room match when their titles are equal
after `strip()` and `casefold()`. `küche` in loxmatter therefore finds
`Küche` in Config instead of creating a second room next to it. A
loxmatter room name is already stripped on the way into the store
(`_normalized_room`); stripping the Loxone side as well costs nothing.

If two Loxone rooms share a folded title (not observed, but Config does
not forbid it), the first in document order wins, and the plan says so
(section 7).

Rooms are matched once per sync request, not per entry: the plan holds
one room assignment per distinct loxmatter room name.

## 5. Which object gets which room

Applies only to objects this sync creates - plan entries with state
`new_signal` or `new_device`:

| Device/group room in loxmatter | Resulting `<IoData>` of the new cmd |
|---|---|
| set, matching Loxone room exists | `Pr` = that room's `U`; `Cr` copied as today |
| set, no matching Loxone room | `Pr` = `U` of the room this sync creates (section 6); `Cr` copied as today |
| set, room cannot be created (section 6.3) | as today (copied from the neighbour) |
| not set | as today (copied from the neighbour) |

If no neighbouring `IoData` exists to copy `Cr` from, the new `IoData`
carries `Pr` only.

**A new signal in an existing container follows loxmatter, not its
siblings.** If the user has moved the siblings to another room in Config,
the new signal still lands in the loxmatter room and has to be moved once
by hand. Following the siblings instead would mean a device's room in
loxmatter only ever counts for its very first sync, which is harder to
explain than one object to move.

Groups (`owner_kind == "group"`) are handled like devices, with the
group's own room.

## 6. Creating a room

### 6.1 What is written

For every loxmatter room name without a match (section 4) that at least
one new entry of this plan needs, the sync creates exactly one room - no
matter how many devices share it. A room nobody new needs is not created:
a device whose inputs and outputs all exist already never causes a new
Loxone room.

A new room is two objects:

- a `Place`, appended at the end of the `PlaceCaption`'s content:
  `V="178"`, a new `U`, `Title` = the loxmatter room name exactly as
  stored, `WF="16384"`, `RGR` = the new rights group's `U`, and `PType`
  and `Icon` copied from the first room in the file that does not carry
  `First="true"` (the `Nicht zugeordnet` room, whose `WF` and `First`
  must not be copied). If there is no such room, `PType` and `Icon` are
  left out. `PGroup`, `Rating` and `UseFav` are never written.
- a `RightGroup`, appended at the end of the rights group caption's
  content: `V="178"`, a new `U`, `Title` = the same name, `Cl="0,0,0"`,
  `WF="16384"`, `GT="1"`, `MG=""`.

IDs come from `new_unique_id` and count against `all_u_values` like every
other new object; `NextObj` rises by two per created room (sync design,
section 6).

### 6.2 Old projects without rights groups

If the file has no `LoxCaption` with `CaptionType="13"` but a
`PlaceCaption`, the room is created as a `Place` without `RGR`, and no
rights group is written - the shape the rooms of those files already
have. `NextObj` then rises by one.

### 6.3 When no room can be created

If the file has no `PlaceCaption`, no room is created. The affected new
entries fall back to today's behaviour, and the plan reports the room as
"could not be created". The sync as a whole does not fail over a room.

### 6.4 The unverified part

Whether Loxone Config accepts a `Place` and `RightGroup` created this
way is known only after a real import - exactly as the new device
containers were in the sync design's section 3.4. Unlike there, room
creation is not hidden behind a checkbox: the user decided on
2026-09-29 that rooms missing in Config should be created. Acceptance of
this feature therefore includes one import of a patched file with a
newly created room into Loxone Config, including a check that the room
appears in the room list and in the user rights dialog.

## 7. Plan and WebUI

`SyncPlan` gains a list of room assignments, one per distinct loxmatter
room name that a new entry needs:

| State | Meaning |
|---|---|
| `found` | a Loxone room with a matching title exists |
| `created` | no match; the patched file creates the room |
| `ambiguous` | several Loxone rooms match; the first is used |
| `not_creatable` | no match, and the file has no `PlaceCaption` |

Each new plan entry (`new_signal`, `new_device`) additionally carries
the title of its target Loxone room, or none if the entry falls back to
today's behaviour.

The WebUI's project sync view shows a "Rooms" block above the device
list with one row per assignment and a state badge, and shows the target
room in the rows of new entries. Every text goes through
`i18n/strings.yaml` with an `en` and a `de` value. A plan in which every
signal is `unchanged` has no room assignments, so the "Everything up to
date" message (sync design, section 5) is unaffected.

## 8. Architecture

The change stays within the existing pipeline - one request, text
surgery, no server-side state (sync design, sections 3.2 and 4).

- **`projectsync/index.py`** additionally records, for the whole
  document: the `PlaceCaption` element, every `Place` (title, `U`,
  attributes), and the rights group caption (`LoxCaption` with
  `CaptionType="13"`), both searched anywhere in the tree.
- **A new module `projectsync/rooms.py`** owns sections 4 and 6.1's
  decisions: from the index and the set of loxmatter room names needed by
  new entries, it returns the room assignments (section 7). Pure
  functions, no text editing, no IDs: the `U` values of a created room
  are generated in `patch.py`, like every other new object's.
- **`projectsync/diff.py`** passes each new entry's device or group room
  to `rooms.py` and stores the result in the plan.
- **`projectsync/schema.py`** gains `new_place_tag` and
  `new_right_group_tag`, and `new_cmd_children_xml` accepts a room `U`
  that replaces or adds `Pr` in the copied `IoData` attributes.
- **`projectsync/patch.py`** writes the two insertions per created room
  and passes the target room `U` into `_new_signal_edit` and
  `_new_device_edit`.

The store is not touched; no migration.

## 9. Testing

- `rooms.py`: matching by folded title, first-wins on duplicates, one
  assignment per name however many entries share it, no creation for
  rooms only existing entries need, `not_creatable` without a
  `PlaceCaption`.
- Patch tests against small synthetic project files: a new cmd's `Pr`
  points to the matching room; a created room consists of one `Place`
  and one `RightGroup` that reference each other and are unique in
  `U`; the old-project variant writes a `Place` without `RGR`;
  `NextObj` rises by the number of objects created; every byte outside
  the edits stays identical (as in the existing patch tests).
- An existing object whose `IoData` points to another room than its
  device's loxmatter room stays `unchanged` and byte-identical.
- The API/WebUI tests show the room block and the target room; the i18n
  keys exist in both languages.
- Acceptance on real software: section 6.4.
