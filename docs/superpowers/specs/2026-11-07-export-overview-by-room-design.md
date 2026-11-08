# Export overview by room, and group members exported through their group

Date: 2026-11-07. Status: implemented.

## 1. Goal

The export screen showed what goes to Loxone as two long tables (template
preview) and, after a project-file upload, as a long diff list. On a real
installation the bridge holds thousands of signals (one test bridge: 2,591, of
which 128 are marked useful; a single Matter window contact brings about 150),
and every lamp in a group was exported twice: once as the group, once on its
own. The user asked for two things:

1. Lamps in a group are not needed as separate Loxone objects by default, but
   exporting one on its own must stay possible with one click.
2. The list of exported signals has to become shorter and easier to scan.

Three screen designs were drawn on a canvas; the user chose variant A, "by
room, group first".

## 2. Export mode of a group member

A device that belongs to at least one group has an export mode:

| Mode     | Loxone gets                                        |
|----------|----------------------------------------------------|
| `group`  | nothing of its own; it is switched through the group |
| `status` | its inputs only (values to Loxone), no outputs     |
| `full`   | inputs and outputs, as before this change          |

A device in no group is always exported in full; the mode does not apply.

- **Storage.** A nullable column `device.group_export` (schema 18). `NULL`
  means "the default": `group` while the device is in a group. On the device
  row rather than on `device_group_member`, because `Store.set_group_members`
  replaces all membership rows on every edit and would lose the choice.
- **Existing installations keep their export.** `_migrate_to_v18` writes
  `full` for every device that is a group member at migration time. A
  project file that already holds a member's own inputs and outputs therefore
  does not lose them by updating; the user switches them to `group` when they
  want to. Only memberships created after the update start at `group`.
- **Route.** `PATCH /api/devices/{id}` takes `group_export`
  (`group | status | full`). Setting it bumps `updated_at`, because the next
  export changes.
- **Where it applies.** One function, `export.scope.scoped`, cuts a device's
  signals and commands down to its mode. The template ZIP (preview, download,
  status) and the project-file sync both go through it. `loxmatter export` on
  the command line exports the one device it was asked for and is unaffected.
- **Project sync.** A member switched to `group` produces no plan entries of
  its own; objects it left in an existing project file appear as "orphaned",
  exactly like a removed device. Nothing is deleted from the file.
- **Runtime.** Values keep flowing to Loxone by UDP whatever the mode; a
  member's commands still work if the project file has them.

## 3. The screen

`GET /api/export/overview` answers in one call with what the screen needs,
built from the same scoped lists as the export itself:

- rooms in name order, "no room" last; in each room the groups first, then
  the devices that are not group members;
- per group: its output capabilities, its members with their mode and
  capabilities, and whether it changed since the last export (see
  section 4 for what the screen compares with);
- per device: technology, transport, input and output capabilities, changed
  since export;
- totals: groups, devices exported on their own, inputs, outputs, members
  switched only through their group, entries changed since export.

**Capabilities instead of signals.** A chip names a capability, not a
signal: light control, on/off, brightness, colour temperature, colour,
temperature, humidity, motion, contact, light level, battery, power, energy,
button. They are derived from the cluster (`export.capabilities`), so `on`,
`off`, `toggle` and the on/off feedback all become one "on/off"; colour x,
colour y, hue, saturation and colour mode become one "colour". The lighting
controller (Lumitech), a lamp's only output by default, is "light control".
A signal or command outside the table keeps its own title. Inputs read
"→ to Loxone", outputs "← from Loxone". The web UI shows at most four chips
per direction and "+N" for the rest.

**Layout.** One card per room; a room without changes starts collapsed. A
group sits at the top of its room on a copper tint, with its members folded
away under "N lamps are switched through the group"; unfolded, each member
has a three-way choice (through the group / status only / complete). A
search field filters by device, group, room or capability; "Changed only"
hides what is current. A summary column on the right (below the list on a
phone) shows the totals and holds both actions: "Update Loxone project"
(the project-file sync, unchanged behind it) and "Templates as ZIP".

The template preview table moved into a folded "Templates in detail" section:
it still names the files the ZIP will contain, for whoever imports
templates by hand.

## 4. Compared with the project file, not with a download

Amended 2026-11-08, at the user's request. The first version marked an
entry "changed since the last export" by `updated_at` against
`exported_at`, and only a template ZIP download stamps `exported_at`. The
project-file sync - the way the user actually exports - never does, so
after updating the Loxone project everything still read as changed.

The screen now compares only with a project file read in on it:

- Before a file is read, nothing is marked: rooms are open, the
  "Differences only" filter is hidden, and the summary says no file has
  been compared yet.
- After the file is read, a group or device is marked "differs from file"
  when one of its plan entries is not `unchanged`. An orphaned entry has
  no device any more and is attributed by the owner in its key
  (`d{id}_...` / `g{id}_...`).
- The device tiles follow the same rule: "Matches the project file" or the
  "Differs from project file" pill, and neither before a file is read. The
  old "Last exported on ..." line is gone.

`GET /api/export/status` and its `changed_since_export` stay as they are:
the template ZIP's "only pending" download still uses them.
