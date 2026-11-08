# Two-way project sync

Date: 2026-11-08. Status: implemented.

## 1. Goal

Until now the project-file sync only wrote in one direction: loxmatter's
signals into the Loxone file. Changes made in Loxone Config, such as a room
or a name, never reached loxmatter, and objects of devices removed from
loxmatter stayed in the file. The user asked for the file to stay as close
to the loxmatter instance as possible, with differences visible in both
directions:

- When a room changes in Loxone because a device moved, a short dialog asks
  which value is right, so both systems agree again, or the device is
  skipped.
- When devices are removed in loxmatter but are still in Loxone, they should
  usually be deleted there, because they no longer work.

Screen designs on a canvas (overview, one dialog per device, clean-up,
summary) were approved, with three answers: the side that changed last is
preselected; objects still wired on program pages are offered for deletion
too, with a warning; Loxone changes are only seen when the file is read in.

## 2. What is compared

| Finding        | loxmatter                | Loxone file                               |
|----------------|--------------------------|-------------------------------------------|
| Room           | one room per device/group | one room per object: `IoData.Pr` → `Place.U` |
| Name           | the device or group label | the container's `Title`                   |
| Orphan         | key no longer exported   | `VirtualUdpInCmd`/`VirtualOutCmd` with a `d…`/`g…` key |

The managed attributes (`Check`, `CmdOn`, `Analog`, the container address)
keep working as before (design 2026-09-03); this design adds the three
findings above. `projectsync.reconcile` finds them, `projectsync.patch`
writes them.

## 3. Rooms and names

- **One room in Loxone.** When every existing object of an owner points at
  the same room, and that room differs from loxmatter's (by `room_key`), the
  owner is a room difference. The default room (`Place First="true"`, shown
  as "not assigned") counts as no room.
- **Several rooms in Loxone.** Objects spread over several rooms are a
  deliberate split made in Loxone Config. They are shown ("deliberately
  split") but never asked about and never changed.
- **New objects.** A new signal goes into the room the user chose; without a
  choice, into the room all its siblings share in Loxone, so it lands next
  to them; with no shared room, into loxmatter's room (design 2026-09-29).
- **Name.** The titles of an owner's containers that differ from the label.
  Exactly one such title is a name difference. A container renamed on one
  side only (say the output container) still counts; two different new
  titles are left alone, there is no single answer to offer.

Choosing **Loxone** stores the room (`set_room`/`set_group_room`) or the
name (`rename_device`/`rename_group`) in loxmatter, and renames the owner's
other containers to match. Choosing **loxmatter** points every existing
object's `IoData.Pr` at loxmatter's room (creating the room if needed,
inserting `Pr` or `IoData` where missing), or sets every container's
`Title` to the label. **Skip** leaves both sides as they are.

This lifts the non-goal of design 2026-09-29 that existing objects' rooms
are never written: they are now written, but only on the user's explicit
choice of loxmatter's room for that owner.

## 4. Which side moved: the baseline

A difference alone does not tell which side changed. After every sync that
was carried out, `SyncBaselineStore` (`setting` key `projectsync.baseline`)
records the room and name both sides agreed on per owner (`device:5`,
`group:2`). Next time, Loxone still showing the baseline means loxmatter
moved, so loxmatter is preselected; otherwise Loxone is. Without a baseline
Loxone is preselected when it has a value: a change made by hand in Loxone
Config is deliberate, while loxmatter's room and name are often still what
commissioning gave the device. A skipped owner keeps its old baseline, so it
is asked again.

## 5. Orphans and their wiring

The plan's `orphaned` entries are grouped by owner prefix (`d8`, `g2`), with
a reason:

- `removed` - loxmatter has no such device or group any more;
- `via_group` - a group member now switched through its group (design
  2026-11-07), e.g. Loxone still switches a single lamp instead of the group;
- `signal_dropped` - the owner exists, these signals are no longer exported.

Deletion is per owner and preselected, except for owners the user kept last
time (`kept` in the baseline).

How Loxone Config wires a virtual object, measured on the maintainer's file:

- A program page holds an `InputRef`/`OutputRef` whose `Ref` is the
  command's `U`. Nothing else refers to a command's own `U`.
- Links are `<In Input="…"/>` children of the consuming connector
  `<Co … Nc="n">`, pointing at the producing connector's `U`. `Nc` always
  equals the number of `In` children; a connector without links is written
  self-closing, without `Nc` (0 exceptions among 2,239 connectors). `In`
  carries `Input` and sometimes `FLG`.

Deleting an orphan therefore removes the command (or its whole container
when all commands go and no new command is added to it this sync), every
block whose `Ref` is a removed command's `U`, and every `In` that pointed at
a connector inside something removed. The connector keeps `Nc` in step, and
becomes self-closing without `Nc` when its last link goes. Function blocks
themselves stay; the overview names the pages an orphan is wired on, and the
dialog warns before deleting it.

## 6. Interface and API

`POST /api/export/project-sync` gains a `decisions` form field (JSON:
`{"rooms": {"device:5": "loxone"}, "names": {...}, "delete": ["d8"]}`) and
an `apply` query flag, and its response gains `room_differences`,
`name_differences`, `split_rooms` and `orphans`. The first upload sends
neither: nothing is decided, nothing deleted, nothing stored. The last step
of the dialog sends both; the bridge writes the file with every answer,
stores the "Loxone is right" ones and the baseline, and the browser
downloads the file and reads it straight back in, so the page shows the
state both sides are now in.

On the export screen the project-sync card shows four counts (the same on
both sides, only in loxmatter, differences, only in Loxone) and one list per
kind. "Start sync" opens a dialog in three steps: one question per
difference (Loxone, loxmatter, or skip, with "decide the same for all
further ones"), the clean-up list with link warnings, and a summary in two
columns (loxmatter → Loxone, Loxone → loxmatter). The former per-signal
plan stays below, folded away.

## 7. Non-goals

- Signals renamed in Loxone (`Title` of a command) stay the user's, as
  before: they are not a name difference.
- loxmatter does not watch the Miniserver for changes; Loxone-side changes
  are seen when the file is read in.
- A function block left without inputs after a deletion is not removed.
