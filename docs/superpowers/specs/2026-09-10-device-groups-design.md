# Device Groups: One Loxone Output for Many Devices

Design, 10 September 2026. Lets several devices of the same category be
addressed as one — a single virtual output in Loxone Config, a single
`/cmd` call, one fan-out inside the bridge.

Rationale: several lamps that are always switched together currently need
one virtual output per lamp in the Loxone project, and the Miniserver
fires them one after another. That is both more wiring than the intent
requires and more delay than the hardware requires.

## 1. Why This Is Being Built Now, and Why Not on 8 September

On 8 September 2026 group messaging was deliberately **deferred**: no
fan-out substitute, no server fork, no group model in the store. The
reasoning then was that no Matter server this bridge can use exposes
groups, that Matter 1.6 had just released the Groupcast cluster which
replaces Groups (0x0004) and GroupKeyManagement (0x003F), and that a
device with Groups revision 5 and adopted Groupcast **rejects** the legacy
`AddGroup` commands. The legacy path therefore had an expiry date, and
building it would have meant building something already obsolete.

That reasoning concerned **native** Matter groups. It is unchanged and
still holds. What changed is the requirement: a software group that keeps
sending to each device individually, and only collapses the *Loxone* side
to one command, is explicitly wanted.

The native path was re-checked before writing this, not assumed from the
earlier note. `matterjs-server`'s WebSocket API documents 41 commands —
`device_command`, `write_attribute`, `set_node_binding`, `set_acl_entry`
among them — and not one of them addresses a group, a groupcast or a
multicast. `Groups` appears in it only as a cluster ID, with no API
behind it. So the situation is exactly as recorded two days ago, and the
software group is not a shortcut past an available feature; it is the
only available way.

**What this means when native groups do arrive.** The group model below
is deliberately shaped so that it could later gain a second
implementation of its dispatch step without touching its data model, its
API or its export: everything except `resolve_group_command`'s fan-out
would stay as it is. Nothing in this design is written as though the
fan-out were the only conceivable dispatch.

## 2. What a Group Is

A group is a **named sender without a node of its own.** It has a label,
a room, a category and members. It has no signals, no live values, no
online state and no node ID.

The receiving side is untouched — that is the requirement, and it also
has a technical justification: a state per group would be an invention.
Six lamps have six brightnesses, and a single number claiming to be "the
group's brightness" would be the kind of quiet fiction this project
avoids everywhere else (Main Spec 8.1: a control that silently does
nothing is the failure this tool exists to surface, not to produce).

Three invariants:

1. **The category is fixed at creation** and derived from the first
   member via `profiles.categories.category_for()`. A member of a
   different category is refused with 400 — including when the group is
   currently empty. The category is therefore *stored*, not derived: an
   empty group must still know what it accepts. Grouping by category
   rather than by capability was chosen deliberately over the narrower
   alternatives (identical vendor/product would already be too tight for
   two IKEA lamps of different series).
2. **The command list is the intersection** of the members' commands,
   recomputed on every membership change (Section 4.3).
3. **A device may belong to any number of groups.** Nothing argues
   against it, and "Kitchen" and "Ground floor" must be allowed to
   overlap.

## 3. The Command Path

The key decides, nothing else:

```
/cmd/g3_on/1
  -> store.resolve_command("g3_on")        # no row in `command`
  -> store.resolve_group_command("g3_on")  # row in `group_command`
  -> members: (node 12, ep 1), (node 15, ep 1), (node 19, ep 1)
  -> per member: to_matter_calls(...) -> that member's calls
  -> asyncio.gather across the members
```

`resolve_command` is tried first and `resolve_group_command` second. The
two can never collide, because device keys begin with `d` and group keys
with `g` — but that is a convention, not an SQL guarantee, so a test
asserts the disjointness rather than trusting the naming (Section 10).

### 3.1 Parallel Across Members, Sequential Within a Member

This is the one detail of the fan-out that is not free to choose.

`to_matter_calls` already returns *several* calls for a single command:
the colour output carries colour and brightness, and their order is
deliberate. `commands/translate.py` records why — `_EXECUTE_IF_OFF` is
set so the colour is already in place before the light comes on;
measured on the checked-in KAJPLATS CWS on 8 September 2026, the value
`60100060` turned a switched-off lamp white at 100 % instead of green,
because the colour command had no effect while the lamp was off. The
alternative, sending brightness first, would make the lamp visibly come
up in the *old* colour and then change.

A flat `gather` over all calls of all members would destroy that
ordering. So: the calls of one member run sequentially, in the order
`to_matter_calls` returned them; the members run concurrently.

`BridgeMatterClient.send_command` awaits the server's reply per call
(`upstream.send_device_command`), so concurrent members genuinely
overlap on the wire rather than merely being scheduled together.

### 3.2 Errors Are Collected, Not Raised at the First One

`asyncio.gather(..., return_exceptions=True)`. If at least one member
fails, the response is 502 and its detail names the affected devices —
"3 of 5 reached; Lamp Kitchen, Lamp Hallway are not responding". The
reachable lamps stay switched.

The three rejected alternatives, and why:

- **Sequential with an abort at the first failure** (what `/cmd` does
  today for a single device's multiple calls) loses the entire point of
  the feature and additionally does the wrong thing: a dead lamp in
  second position would leave the remaining four dark.
- **Best effort with 200 as long as anything succeeded** hides the broken
  lamp. The Miniserver does not evaluate the response at all — the status
  code exists for the human reading the log (`loxone/server.py` module
  docstring), and for that reader "five of six" is the only useful
  answer.
- **Reporting only the first exception** would name one lamp when three
  are unreachable, which reads like a single device fault instead of a
  network fault.

**Known consequence:** the HTTP response waits for the *slowest* member.
An unreachable Thread device can push the response time into seconds.
This has no effect on the delay at the lamps — the reachable ones have
long since received their command — and it is harmless for a Miniserver
that fires and forgets. It is recorded here so that nobody later reads a
slow `/cmd` response as a bug in the fan-out.

## 4. Store

### 4.1 Schema

```sql
CREATE TABLE device_group (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    room        TEXT,
    category    TEXT NOT NULL,
    exported_at TEXT,
    updated_at  TEXT
);
CREATE TABLE device_group_member (
    group_id  INTEGER NOT NULL REFERENCES device_group(id),
    device_id INTEGER NOT NULL REFERENCES device(id),
    UNIQUE (group_id, device_id)
);
CREATE TABLE group_command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL REFERENCES device_group(id),
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (group_id, cluster_id, command_id)
);
```

`group_command` stores **no endpoint.** Each member has its own, and two
lamps of different make can carry the same cluster on different
endpoints. The endpoint therefore belongs to the member, not to the group
command, and is looked up per member at dispatch time.

The key is `g{group_id}_{slug}` — without the endpoint segment that the
device key `d{device_id}_{endpoint}_{slug}` carries, for the same reason.

`room` follows the same encoding as `device.room`: a free-text name,
`NULL` meaning "no room", and no room table — a room exists for exactly as
long as something carries its name.

### 4.2 Migration 8

`_SCHEMA_VERSION` goes to 8; `_migrate_to_v8` creates the three tables
with `CREATE TABLE IF NOT EXISTS`. That is the `_migrate_to_v5` pattern
and it is needed for the same reason: a freshly created database already
has the tables via `_SCHEMA` and is nevertheless at `PRAGMA user_version
= 0`, so it runs through the whole migration chain. Plain `CREATE TABLE`
would fail there.

No backfill. There is nothing to carry over: no existing database has
groups.

### 4.3 The Intersection, and What Happens When Members Change

`register_group_commands(group_id)` recomputes the command list from the
current members and writes it, on every membership change:

- The intersection is taken over `(cluster_id, command_id)` pairs across
  all members' stored commands. `slug` and `takes_value` are re-adopted
  from the member rows on every run.
- **A member that carries the pair on several endpoints receives the
  command on all of them.** A two-channel dimmer has two `on` commands
  today, one per endpoint, and each has its own device key; a group has
  one `on`, and the only reading of it that does not surprise is "the
  whole member". The alternative — lowest endpoint only — would leave
  half of such a device dark on a group command and give no indication
  why. These calls run sequentially within that member, like every other
  call of one member (Section 3.1).
- A command that survives **keeps its key.** A command that drops out of
  the intersection loses its row, and its key answers 404 from then on.
- The group itself survives, down to one member and down to zero.
- `updated_at` is touched, so the export tab can answer "changed since the
  last export" for a group exactly as it does for a device.

Removing a device (`DELETE /api/devices/{id}`, `forget_device`) is a
membership change like any other: its rows in `device_group_member` go
with it, and every group it belonged to recomputes its intersection. It
does not delete a group, even one thereby emptied — for the reason given
just below.

This mirrors `register_commands`, which re-adopts `takes_value` and
`slug` on every call precisely so that a correction in `clusters.yaml`
reaches an already stored command. A frozen command list would be the
opposite of that and would let a group claim a capability no member has
left.

**A 404 is the better outcome than a silent disappearance.** When a
command drops out, a virtual output in the Loxone project points at a key
that no longer resolves. That shows up in the log and points at the one
line that needs attention. Deleting an emptied group instead would take
its keys with it, leaving an output in the project whose origin nobody
can reconstruct.

**An offline member does not shrink the intersection.** Commands come
from the stored `AcceptedCommandList` of the last interview, not from a
live query. A lamp that happens not to answer therefore does not change
the group; it only appears in the 502 detail.

## 5. API

All routes sit under the existing `api_guard`.

| Route | Purpose |
| --- | --- |
| `GET /api/groups` | list with members, commands, room, category |
| `POST /api/groups` | `{label, room?, member_ids[]}`; at least one member, which fixes the category |
| `PATCH /api/groups/{id}` | label and room |
| `PUT /api/groups/{id}/members` | the complete member list |
| `DELETE /api/groups/{id}` | group and its keys |
| `GET /api/groups/{id}/controls` | as `GET /api/devices/{id}/controls` |

**No new control endpoint.** `POST /api/commands/{key}` drives groups
too — this is where the shared key namespace pays off. A group tile in
the WebUI makes exactly the same call as a device tile, and the
404/400/502 semantics carry over unchanged. A second control path would
be the drift that `commands/translate.py` exists to prevent (Main Spec
4.2).

**`PUT` on the whole member list**, not `POST`/`DELETE` per member. The
intersection is recomputed after every change anyway; removing two
members individually would recompute it twice and pass through an
intermediate state nobody asked for — including keys that briefly vanish
and come back.

`GET /api/groups/{id}/controls` reports value ranges as an
**intersection**: for colour temperature the maximum of the members'
minima and the minimum of their maxima. Otherwise the slider would offer
a Kelvin range that half the group silently clamps — the same fault the
lamp-controls design set out to remove for a single device.

Two members whose ranges do not overlap at all yield an empty
intersection. That control is then **not offered** and is counted like a
command without a usable range, rather than shown with a degenerate span
that no value satisfies. The command itself stays in the group and stays
exported — it is the *slider* that has nothing to offer, and `/cmd` with
an explicit value still reaches every member that accepts it.

**The sliders' initial value** is the one place where a group tile states
something it cannot know: six lamps have six brightnesses. The
lamp-controls design explicitly rejected the alternative — a slider with
no initial value — because the first push then moves the lamp somewhere
arbitrary and proves nothing about the state it changed. The initial
value is therefore taken from the member with the lowest `device_id`, and
the tile names that device underneath, so the number is attributed rather
than presented as the group's.

## 6. WebUI

A **"New group"** button joins the search field on the devices tab. The
dialog asks for a name, then members: the first device selected fixes the
category, after which devices of other categories are greyed out — with
the reason on the entry, not silently. The room is prefilled when the
members agree on one, and remains the user's decision afterwards.

The group tile is the device tile: same grid, same control bar, same room
chips, same search (matching label, category and room). The differences
are deliberately small — a "Group" badge, the member count, and a
"Edit members" entry in the kebab menu alongside rename, room and delete.

What the group tile deliberately **lacks**: no online dot, no signal
list, no "last heard". Those are properties of a node, and a group is not
one. Showing them would mean inventing an aggregate, which Section 2
rules out.

The room comes from the group's own `room` column rather than being
derived from the members. A derived room would move a group on its own:
"Kitchen ceiling light" would slip to "no room" the moment one of its
lamps is moved to "Dining room", and nobody would learn why.

## 7. Export

The group gets its own template file, `VO_g{id}_{label}.xml`.

`filename_for` needs one small change for it: the `d` in
`f"{prefix}_d{device_id}"` is hard-wired today and becomes a parameter.
The reasoning in its docstring carries over to groups unchanged — the ID
guarantees uniqueness because the label normalisation is lossy, the label
keeps the file recognisable to a human.

`to_outputs` pairs `on`/`off` over `(endpoint, cluster_id)` today. Group
commands have no endpoint, so the group variant pairs over `cluster_id`
alone. Rather than a second copy, the pairing takes its grouping key as a
parameter. Two copies would drift, and they would drift **silently**: an
unpaired `on` does not raise, it simply passes through as its own output,
and the missing off-path would only show up as a switch in Loxone that
never turns anything off.

Title and caption container read `Matter — Group: <name>` (`de`: `Matter
— Gruppe: <name>`), with an `en`/`de` pair in `strings.yaml`.

**Renaming a group leaves an already patched project file working.**
The container in the project keeps its old title, and the project sync
keeps finding it, because containers are matched by key and the keys are
built from the ID (Section 8). The stale title is cosmetic — the same
thing a device rename does today, and for the same reason. Re-importing
the template refreshes it.

## 8. Project Sync

A group container is a sibling of the device containers, not nested
inside one.

`build_index` recognises group keys through `key_from_output_cmd`
unchanged, because a group output has the same `/cmd/{key}/{value}` shape
as a device output. That is a direct consequence of the shared key
namespace: had groups received their own route, the index would have
needed a second URL pattern, and the Loxone project would carry two kinds
of virtual output that do the same thing.

**Containers are matched by key, not by title.** `index.build_index`
indexes `output_containers` by the loxmatter key it reads back out of
each `<VirtualOutCmd>` URL (`key_from_output_cmd`), and `_plan_outputs`
asks whether any indexed key starts with `d{device.id}_`. The group
variant asks the same question with `g{group.id}_`. Nothing here reads a
title, so nothing here breaks when one changes.

The two titles should nevertheless agree, and today they do so by hand:
`documents.render_virtual_out` writes `Matter — {label}` and
`schema.new_output_container_open_tag` writes the same string from a
second format literal. The group path adds a third and a fourth
occurrence of that pattern, which is where it stops being maintainable —
so both group titles come from one shared helper. This is a
readability requirement, not a correctness one; getting it wrong costs a
confusing name in Loxone Config, not a failed sync.

`MissingCaptionError` plays no part in any of this. Its own docstring
records it as historical: `_new_device_edit` creates a missing
`VirtualOutCaption` section itself, and the error is no longer raised.
It is named here only because it would otherwise look like the obvious
thing to guard against.

`exported_at`/`updated_at` on the group behave as on a device.

## 9. Internationalisation

Every new user-visible string gets an `en` and a `de` value in
`strings.yaml` and is resolved through `i18n.t(...)` at call time. This
covers the group title prefix (Section 7), the WebUI labels of Section 6,
and the new error details:

- the category refusal on adding a member (400),
- the "N of M reached" text in the 502 detail (Section 3.2), which needs
  a plural-aware phrasing rather than a concatenation,
- the group tile's attribution of its initial value (Section 5).

## 10. Testing

Beyond the obvious per-unit coverage:

- **Key disjointness.** A test asserts that no group key can equal a
  device key, rather than trusting the `d`/`g` prefixes. This is the
  guarantee `resolve_command`'s two-step lookup rests on.
- **Ordering within a member.** A fan-out test with a recording invoker
  asserts that a colour command's two calls reach one member in the
  documented order, while two members' calls interleave. Testing only
  that all calls arrive would pass for the flat `gather` that Section 3.1
  rejects.
- **Partial failure.** One failing member out of three yields 502, the
  other two are still invoked, and the detail names the failing device.
  The point is the second half: a test that only checks the status code
  would pass for an implementation that aborts.
- **Intersection changes.** Adding a member that lacks `level` removes the
  `level` row, its key then answers 404, and the surviving commands keep
  their keys. An offline member changes nothing.
- **Migration.** A version-7 database gains the three tables and keeps its
  rows; a fresh database ends at version 8 with the same schema. Both
  directions matter — the `_migrate_to_v5` pitfall is that a fresh
  database also runs the whole chain.
- **Export and sync round trip.** A group template imports into a project
  file, and a subsequent sync recognises its keys instead of reporting
  them as new — the check that Section 8's shared string requirement
  actually holds.

Fixtures: the two checked-in IKEA lamps
(`tests/fixtures/nodes/`) already provide a mixed-capability pair, which
is exactly the intersection case — both are `LIGHT`, only one has
`MoveToHueAndSaturation`. No synthetic group fixture is needed.

## 11. Explicitly Not Built

- **Native Matter groups.** Section 1. Nothing in the WebUI or the export
  suggests that a group exists on the devices themselves; it exists in
  this bridge only.
- **The receiving side.** No group signals, no aggregated state, no
  virtual inputs for a group. This is the requirement and Section 2's
  reasoning.
- **Scenes, favourites, schedules.** The lamp-controls design ruled these
  out for devices; a group does not reintroduce them.
- **Nested groups.** A group containing a group. No use case, and it
  would make the intersection recursive and force cycle detection into the
  fan-out.
- **Cross-category groups.** By requirement.
- **Automatic groups** derived from a room or a category. A group is
  created deliberately; a derived group would move on its own, which
  Section 6 rejects for the room already.

## 12. Open Points

1. **Thread congestion at scale.** Concurrent unicasts to many members
   have not been measured against a real Thread network. With the handful
   of lamps in the test setup this is not a concern; if a group ever grows
   to a size where it is, a small stagger between members would be the
   remedy, and it would go in `resolve_group_command`'s dispatch without
   touching anything else in this design.
2. **The initial-value attribution** (Section 5) is a judgement call about
   how talkative the tile should be. If it reads as clutter in practice,
   dropping the attribution line — keeping the value — is a UI change with
   no consequences elsewhere.
3. **Groups in the diagnostics live feed.** A group command currently
   appears there as its individual Matter calls, which is accurate but
   loses the fact that they belonged to one group call. Whether that is
   worth a group marker in the feed is left until the feature has been
   used.
