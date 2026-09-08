# Devices Tab: Rooms, Categories, and a Multi-Column Tile Grid

Design, September 5, 2026. Continues
[the device dashboard design](2026-09-03-device-dashboard-and-export-design.md)
— its tile ("no more expanding", section 3) stays untouched in content
and is only rearranged here. Along the way, it resolves that design's open
point 1 (mapping device type → icon), because the category this
design needs anyway is exactly that mapping.

## 1. The Problem

The device view is a single-column list of always-open tiles
(`index.html:214-340`), sorted by `device.id`, i.e. by commissioning
order. That holds up to about eight devices. Beyond that, the
view has three weaknesses that reinforce each other:

1. **One column wastes two-thirds of the width.** A tile is as
   tall as its value and control blocks, but as wide as the window.
   Twelve devices are twelve screen heights.
2. **There is no ordering besides the commissioning date.** Someone
   looking for the plug in the bathroom scrolls and reads names — the view
   doesn't help.
3. **A device carries no location.** At best, the room sits in the
   self-assigned label ("Steckdose Bad"), which means it is not sortable,
   not filterable, and not correctable without rewriting the name,
   which ends up as `Title` in the Loxone export.

On top of that, there's a gap at commissioning time: a freshly commissioned
device is initially named whatever the manufacturer calls it, and sits at
the end of the list. The mapping "that's the lamp in the living room"
happens in the operator's head and nowhere else.

## 2. What Stays Unchanged

- **The tile's content.** Values, controls, export hint, export
  and remove button stay visible without a click. Doing away with the
  expand toggle from the dashboard design still stands; this design
  only rearranges.
- **The signal view** and the global export tab. Rooms do not appear
  there — the signal view is the complete view of *one* device, the
  export tab the view of *all*; neither needs location ordering.
- **The Loxone export.** Neither room nor category ends up in a
  template file. That is not frugality, but the justification for
  section 3.3: what is not exported must also not mark a device as
  "changed since export".
- **The status colors** (green unremarkable, amber changed, gray offline)
  and the copper accent color, along with the reasoning for why both stay
  separate (dashboard design, section 3).

## 3. Data Storage

### 3.1 Migration v7: Two Columns

`_SCHEMA_VERSION` goes from 6 to 7. `_migrate_to_v7` adds two columns to
`device` via the existing `_add_column_if_missing` — the same
pattern and the same reason as with `_migrate_to_v2`, which likewise
adds two columns in one step:

```sql
room         TEXT   -- NULL = "no room"
device_types TEXT   -- JSON, endpoint -> list of Matter type IDs; NULL = not yet backfilled
```

No backfill for `room`: `NULL` there means the same thing as for a freshly
commissioned device without a room choice, there is no existing value from
which a room could be derived. `device_types`, by contrast, is very much
backfilled, just not in the migration — see 3.4.

### 3.2 Rooms Are Not Their Own Object

There is no `room` table, no room IDs, and no room management. A
room exists for exactly as long as at least one active device carries its
name; the room list is the `DISTINCT` over `device.room`, which the
UI can derive from `GET /api/devices` anyway.

The price of this decision is that "rename room" is not an operation
on an object, but a bulk write across all devices of the
room — that's what the one route in 4.3 is for. The payoff: no orphaned
rooms, no cleanup rules, no second entity that has to be kept in
sync with the devices.

**Normalization.** Room names are trimmed on write; whatever is empty
after trimming becomes `NULL`. Comparison and sorting run
case-sensitively over the stored name — "Küche" and "küche" would be two
rooms. That is the simpler rule, and because the UI always offers
existing rooms as a selection list and hides free text behind "+ New
Room…", a spelling twin only arises if someone
actively types it in.

### 3.3 `set_room` Does Not Touch `updated_at`

`store.set_room(device_id, room)` writes exclusively `device.room`.
That is the one non-obvious point of the data storage and
deserves the justification:

`rename_device` also sets `updated_at`, and its docstring says why too
(`store.py:874-878`) — the label ends up in the next export as `Title` in
the template, so `GET /api/export/status` rightly leads the device
afterward as "changed since". The room ends up nowhere in the export. If
`set_room` also set `updated_at`, on the first cleanup of the
room assignment *every* device would get an amber "changed since export"
pill and the prompt for an export that produces byte-for-byte the same
files as the last one. The same reasoning applies to `backfill_device_types`.

### 3.4 `device_types`: the Source Is Stored, Not the Derivation

What goes into the database is the device's raw information — the output
of `relevance.device_types_by_endpoint(snapshot)`, serialized as JSON. The
category (section 5) is derived from it on every read and **not**
stored.

The reason is already in this project's migration history:
`signal.functional` and `signal.title` are derived values that
were stored, and `_migrate_to_v3` had to recompute them for existing rows
afterward, when the derivation rule improved
(`store.py:78-83`). A mapping table from Matter type to category will
grow as soon as a device type shows up that nobody thought of when
writing it. If only the source is stored, such growth is a
code change without a migration.

`StoredDevice` accordingly gets `room: str | None` and
`device_types: dict[int, frozenset[int]] | None`; `_as_device` parses the
JSON, `None` means "not yet backfilled" (see 5.3).

**Populated in two places:**

1. `register_device(snapshot, room=None)` writes the types along with the
   registration — the snapshot is already there anyway.
2. `store.backfill_device_types(snapshots)`, called at bridge startup
   directly next to the existing
   `await runtime.seed_from_snapshot(await client.snapshots())`
   (`cli.py:606`). The snapshots of all known nodes are already
   fetched there; a second fetch would be pure waste. The call
   updates **exclusively rows with `device_types IS NULL`** — a
   device already backfilled is not rewritten on every start,
   and a device that is currently offline and therefore missing from
   `snapshots()` does not lose its types.

## 4. API

### 4.1 Models (`api/models.py`)

- `DeviceOut` gets `room: str | None`, `category: str` (identifier, see
  5.1), and `category_rank: int`. The rank comes from the backend instead of
  a second list in the frontend — the order of the categories is a
  property of the category table, not of the UI.
- `DeviceRename` becomes **`DevicePatch`** with `label: str | None = None`
  and `room: str | None = None`. `None` means "unchanged" (the same
  principle as with `SignalPatch`), the empty string `""` means "remove
  room" → `NULL`. The class name follows along, because "Rename" no longer
  fits; calls with only `label` remain valid.
- `CommissionRequest` gets `room: str | None = None`.
- New: `RoomRename` with `from_room: str` and `to_room: str` (field names
  with a suffix, because `from` is a Python keyword; exposed via
  `alias="from"`/`alias="to"`).

### 4.2 `PATCH /api/devices/{id}`

Extended, no new endpoint. Sets `label` via `rename_device` (with
`updated_at`) and `room` via `set_room` (without). A call that brings
both does both.

### 4.3 `POST /api/rooms/rename`

The one new route: an `UPDATE device SET room = ? WHERE room = ? AND
active = 1`, response `{"renamed": n}`.

Without it, renaming a room would mean typing "+ New Room…" on
each device individually — with five devices, five opportunities for a
typo that creates a sixth room. `active = 1` in the condition for
the same reason `store.devices()` filters on it afterward: a
removed device is, from the UI's perspective, no longer there and
should not silently tag along either.

**Merging is allowed.** A target name that already exists merges both
rooms — that is the obvious meaning of "rename Küche to
Essbereich now" when an Essbereich already exists. The UI asks for
confirmation beforehand in this case (section 6.3), because the operation
cannot be undone: after merging, nobody knows anymore which device was
previously in which of the two rooms.

## 5. Category

### 5.1 The Categories and Their Rank

A new module `profiles/categories.py`, next to `relevance.py`, because it
evaluates the same source:

| Rank | Identifier | German | English |
|---|---|---|---|
| 0 | `light` | Licht | Light |
| 1 | `socket` | Steckdose | Socket |
| 2 | `switch` | Taster | Switch |
| 3 | `covering` | Beschattung | Covering |
| 4 | `climate` | Klima | Climate |
| 5 | `sensor` | Sensor | Sensor |
| 6 | `lock` | Schloss | Lock |
| 7 | `other` | Sonstige | Other |

The rank is hardwired and **not** the alphabetical order of the
names: a language switch would otherwise reorder the groups, and a
view that is structured differently depending on the language has to be
explained twice.

The complete table of Matter type ID → category is a matter for the
implementation plan, not this spec — the same boundary as with the
icon point of the dashboard design. Confirmed here are the categories
themselves and their order; the mapping must be confirmed per type ID
against `matter_server.client.models.device_types`, not guessed.

### 5.2 The Primary Type

A Matter node declares types per endpoint. What counts:

1. Administrative endpoints fall away — the same set `is_functional`
   already knows (`UTILITY_DEVICE_TYPES` plus `POWER_SOURCE_DEVICE_TYPE`),
   and for the same reason: Root Node, OTA Requestor, and PowerSource say
   nothing about what the device does in the house.
2. Of the rest, the **lowest endpoint** counts — with Matter usually
   endpoint 1, the application endpoint.
3. If it declares multiple types, the one with the lowest
   category rank wins. This makes the result independent of the order in
   which the device lists its types.
4. Nothing mappable, or `device_types IS NULL` → `other`.

### 5.3 Until the Backfill Takes Effect

An existing device sits in "Other" until the next bridge startup —
visible, controllable, complete, just unsorted. That is the more honest
transitional solution compared to a migration that guesses a category
from stored signals: such a heuristic would be a second
classification rule alongside the Matter device types, i.e. exactly the
double source that `relevance.py` warns against in its comment on
`UTILITY_ENDPOINT_KEEP_CLUSTERS`.

## 6. UI

### 6.1 Grid

`grid-template-columns: repeat(auto-fill, minmax(260px, 1fr))` — four
columns at usual desktop width, two on tablet, one on
phone, with no custom breakpoints. The 260 px is the lower bound above
which the header row with its primary value and the value column stop
wrapping.

### 6.2 The Tile

Approved after an interactive design ("Mix 2"), top to bottom:

1. **Header row** — category icon in a tinted field · name (editable as
   today) · the **primary value** in monospace digits on the right. Below
   it, small, the primary value's label ("Zustand", "Temperatur").
   **If a status pill is due (offline / changed since export), it
   displaces this label.** The tile's state weighs more than
   the caption of a number sitting two centimeters away.
2. **Value grid** — the remaining functional signals as label/value
   rows, values right-aligned in monospace. The existing
   `FUNCTIONAL_PREVIEW_LIMIT = 6` stays and **counts the primary value**:
   primary value plus up to five rows. The previous hint "*n* more in
   the signal view" becomes the last grid row ("+ 7 more", linked
   to the signal view) instead of its own paragraph.
3. **Control bar** — the commands as text buttons, unchanged in function.
   The hint about unnamed raw commands attaches itself as a dimmed "+3
   unnamed" at the end of the button row instead of its own line.
4. **Footer** — on the left the **room selector** ("🏠 Wohnzimmer ▾", opens
   the existing rooms plus "+ Neuer Raum …" plus "Ohne Raum"), next to it
   the export hint, on the right the icon buttons Export and Remove.

**Primary-value rule:** the first functional signal in the order that
`firstSignalsFor` already delivers today — i.e. the order of the
profile table. Socket → state, climate sensor → temperature, blind →
position. No new column, no endpoint, no configuration; a device
without functional signals simply shows no primary value and the header
stays single-line.

The room deliberately sits in the footer and not under the name: the
header already carries the name, the primary-value label, and in a
worst case the status pill — a fourth element would have displaced one of
those.

### 6.3 Room Bar and Grouping

Above the list, a chip row: "Alle · Wohnzimmer · Küche · … · Ohne Raum",
each with its device count. With **Alle** selected, group headings appear per
room; with a single room selected, they disappear because there's only one.

**The chip row does not appear at all as long as not a single device
carries a room** (`hasAnyRoom()` in `app.js`). With three devices and no
room, it would be a line of noise above a list that fits in one glance
anyway.

The search field from 6.6 is unaffected by this and is present even when
not a single device carries a room — it is part of the same row, but tied
to no condition (correction, 2026-09-05: an earlier version of
this section required hiding the whole bar including the search field;
the implementation deliberately decided against that, and the
review confirmed that as the right decision). The reason: per 6.6, search
reaches across name, category name, **and** room name — the
first two criteria are entirely independent of whether any device
carries a room. A search field that only appeared after the first room
assignment would be missing exactly at the moment a growing, still
roomless device list would need it most — being able to
search by name or category is independent of rooms and must not be tied
to them.

On every group heading, a pencil that renames the room
(`POST /api/rooms/rename`). If the target name already carries devices,
the UI asks for confirmation beforehand and names the merge by
name (see 4.3). If a single room is selected, there is no heading that
could carry the pencil — it then sits on the active chip of the room bar.
"Ohne Raum" is not a room and carries no pencil in either case: it is the
set of devices with no assignment, and a name one could change is
exactly what these devices lack.

**The filter state is not saved** — no `localStorage`, no
endpoint. After a reload, the view is back on "Alle". A
remembered filter otherwise creates the moment, two weeks later, when three
of twelve devices are shown and nobody remembers why.

### 6.4 Sorting

Rooms alphabetically, "Ohne Raum" always last. Within a room: by
`category_rank`, within that alphabetically by label via `localeCompare`
(so that "Ä" sorts next to "A" and not after "Z"). All plugs in a room
thus sit together, then the switches, then the rest.

**No second heading level.** The categories get no separate
sub-headings below the room heading: the order plus the category-specific
icons make the blocks visible, and two heading levels above
four-column tiles would be more structure than content.

### 6.5 Icons

Eight `<symbol>` entries in the existing inline SVG block
(`index.html:64-86`), one per category, `#i-device` stays as `other`.
Still inline and without an icon library, for the same reason as the
checked-in `vendor/alpine.min.js`: the UI runs offline. This
resolves open point 1 of the dashboard design.

### 6.6 Search

A field on the right of the room bar, purely client-side over the
already-loaded device list — no endpoint, no query. It matches
case-insensitively across **name, category name, and room name**. Because
the category name is the translated one, "Steckdose" in German and
"socket" in English find the same devices.

**Search acts within the selected room**, chip and field apply
together (AND). With "Alle" selected, it searches everything and stays
grouped by room.

This creates a case the view has to handle: no match in the
selected room, even though the sought device sits next door. The empty
state therefore doesn't just show "no match", but also counts the matches
outside — "3 more matches in other rooms — show all", the link
switches to "Alle" and keeps the search term.

### 6.7 Commissioning Card

A third field next to pairing code and Thread dataset: a selection field
with the existing rooms, default "Ohne Raum", last entry "+ Neuer
Raum …" reveals a text field for the name.

**The selected room stays after successful commissioning** — unlike
code and Thread dataset, which continue to be cleared (`app.js:1093`).
Someone commissioning four devices in the kitchen selects the room once; a
pairing code, by contrast, is worthless after use, and one left
behind would be a source of error.

## 7. Language

Every new text goes through `i18n.t()` with an `en`/`de` pair in
`strings.yaml` — the WebUI has been translated throughout since i18n
Phase B, hardcoded German text would be a step backward. New keys in
the namespace `web.devices.*` (room bar, room selection, search, empty
states), `web.devices.category.*` (the eight category names from 5.1), and
`api.devices.*` for the error messages from section 8.

## 8. Error Handling

| Case | Behavior |
|---|---|
| `PATCH /api/devices/{id}` with `room: ""` | room is removed (`NULL`), 200 — that is the documented meaning, not an error |
| `PATCH` with a room name made of pure whitespace | is trimmed and thus becomes `NULL`, as above — no 422 for something that has an unambiguous meaning |
| `POST /api/rooms/rename`, source room carries no active device | 404, with the name in the message — analogous to `GET /devices/{id}` for a removed device |
| `POST /api/rooms/rename` with an empty target name | 422 — "rename room" is not the way to dissolve a room; the room selector on the tile exists for that |
| `POST /api/devices/commission` with `room` | room is written along with it; if commissioning fails, no device and thus no room is created |
| Device offline | unchanged from before: values show the last state, tile dimmed, commands disabled. Room selection and export remain usable — neither needs the device |
| `device_types IS NULL` (not yet backfilled) | category `other`, icon `#i-device`, sorted to the end of the room. No hint, no warning — the state resolves itself on the next start |

## 9. Verification

**Store and Migration**

- A database at version 6 gets both columns and is at 7 afterward;
  a freshly created one already has them via `_SCHEMA` and survives
  `_migrate_to_v7` without a "duplicate column".
- `set_room` does **not** change `updated_at`: a device that per
  `GET /api/export/status` is not pending is still not pending after a
  room assignment. The same for `backfill_device_types`.
- `rename_device` continues to set `updated_at` — the existing test for
  that must stay green unchanged.
- `backfill_device_types` does not overwrite an already-filled row,
  and a device missing from the passed snapshots keeps its types.
- `rename_room` only touches active devices: a device removed via
  `forget_device` in the same room keeps its old room name in its
  row.

**Category**

- A node with Root Node on endpoint 0 and On/Off Plug-in Unit on
  endpoint 1 gives `socket` — endpoint 0 is skipped.
- An endpoint with two mappable types gives the one with the lower
  rank, independent of the order in the declaration.
- An unknown type ID and `device_types = NULL` both give `other`.

**API**

- `PATCH /api/devices/{id}` with only `room` leaves the label untouched, with
  only `label` leaves the room untouched.
- `POST /api/rooms/rename` onto an existing target name merges
  and reports the total number of changed devices.
- `POST /api/devices/commission` with `room` returns a `DeviceOut` whose
  `room` is set.

**UI** (in the style of the existing `tests/api/test_web.py`)

- Room bar, search field, and room selector on the tile are present in
  the served HTML.
- All new visible text goes through `t(...)` and has both `en` and `de`
  in `strings.yaml` — `tests/test_i18n.py` already has the
  completeness check for that.

## 10. Open Points

1. The complete table of Matter type ID → category (5.1) belongs in the
   implementation plan and must be confirmed per entry against
   `matter_server.client.models.device_types`.
2. Whether a device whose types change on re-interview (e.g. after
   a firmware update) should get its `device_types` updated
   is deliberately left open: `backfill_device_types` only writes
   `NULL` rows. This case has never been observed so far and gets
   no mechanism on spec.
3. Search only reaches into what `GET /api/devices` delivers — name,
   category, room. Whether it should later also search signal titles is
   its own question; it would need the signals of all devices in the frontend and
   thus a different loading path.
