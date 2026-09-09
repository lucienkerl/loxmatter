# Device tile: actions behind a kebab menu

Design, September 5, 2026. Changes the tile footer from
[the devices tab design](2026-09-05-devices-tab-rooms-and-tile-grid-design.md),
section 6.2 — and in doing so reverses the part of it that cost
the most rework.

## 1. The problem

Today the footer carries four things side by side: a select field for the
room, a text field for a new room shown as needed, the export note, and two
icon buttons. On a 300 px wide tile this is
the fullest row in the whole view, and the select field is the
loudest element in it — border, arrow, its own height — even though it is
the least-used function: you assign a room once, then never again.

There is a second, weightier point on top of that. This one select field took
six review rounds, all with the same root cause: `newRoomFor` is
mode state in JavaScript, and it controlled what a **native** `<select>`
displays. A native select list does not report that someone opened it and
left it again without a selection; and Alpine applies an element's
directives before it builds its children, which is why `:value` against a
`<select>` populated via `x-for` simply falls flat. Today's solution
(`x-model` against `roomSelectDrafts`, plus a `focusout` guard) is
correct, but it consists of counterweights to a coupling that should not
have to exist.

## 2. What stays unchanged

- **The tile's header, value grid, and control bar.** The whole upper
  part stays as it is.
- **Room bar, grouping, sorting, search.** The menu only changes how
  a room is *assigned*, not how it works.
- **The rename pencil** on the group heading and on the active chip.
- **The API.** `PATCH /api/devices/{id}` with `{room}` remains the only
  write path, `""` still means "remove room". No new route and no new
  field is created.

## 3. The footer becomes one line

On the left the export note (`exportHintFor`), on the right a ⋮. Nothing
else.

The room disappears from the tile as a result — deliberately: it is
redundant there anyway. Under "All", the group heading above the tiles
already names it; in the filtered state, the active chip in the room bar
does. Saying the same thing a third time costs the fullest row in the view.

## 4. The menu

**A native `<details>`** with the ⋮ as `<summary>`. The same primitive that
the project file sync view already uses four times (`index.html`,
`.projectsync-device`, and `.projectsync-unchanged-disclosure`) — not a new
pattern in the house.

The gain is not convenience but avoiding exactly the failure pattern from
section 1: **the open/closed state lives in the DOM, not in
Alpine.** There is no JavaScript state that would need to be kept in sync
with the visible state of a native control, and therefore no path on which
the two could drift apart.

**Contents, top to bottom:**

1. The rooms as entries: "No room", then every existing room
   alphabetically (`roomChips()` already returns the list in this
   order). The current room carries a checkmark — for a device without
   a room it sits at "No room", which is not a special case there but
   the normal state. A click assigns it (`saveRoom`) and closes the menu.
2. "+ New room …" — reveals a text field **inside** the menu.
   Enter saves and closes, Escape cancels. The menu stays open while
   typing, because the field is its child.
3. A separator.
4. "Export" (`exportDevice`) — disabled as long as no bridge IP
   is set, with the same note and link to settings
   as today.
5. "Remove" (`removeDevice`) in `--danger`, with the existing
   confirmation prompt.

**Closing.** Three ways, and the third is the one easily forgotten:

| Trigger | Implementation |
|---|---|
| Click on an entry | the entry sets `open = false` |
| Click elsewhere | `@click.outside` on the `<details>` — Alpine 3.17.1 in the `vendor/` folder brings the modifier along (verified) |
| Escape | `@keydown.escape` — **`<details>` does NOT close on its own** on Escape, unlike a `<dialog>` |

"Only ever one menu open" follows from this on its own: a click on the
⋮ of another tile falls outside the first `<details>` and closes
it via the same `@click.outside`.

## 5. What is removed without replacement

The actual payoff of this design. From `app.js`:

- `roomSelectDrafts` along with `syncRoomSelectDraft` — the assignable copy
  of `device.room` that only existed because `x-model` cannot
  point at an expression.
- `onRoomSelectChange` — the special handling of "+ New room …" so
  the select list never displays `__new__`.
- The coupling of `newRoomFor` to a `<select>`. The field itself stays,
  but only as a visibility switch for the text field in the menu now — that is,
  in exactly the role it was originally meant for.
- The `syncRoomSelectDraft` call in `loadDevices` and in
  `commissionDevice`.

From `index.html`: the `.room-picker` wrapper with its `focusout` guard,
the `<select class="room-select">`, the text field `.room-new` at this
spot — and the 50-line comment before it that tells the history of the
coupling. From `style.css`: `.room-select`, `.room-new`, `.room-picker`.

What stays: `saveRoom`, `beginNewRoom`, `commitNewRoom`, `roomKeyOf`,
`roomChips`, `reconcileRoomFilter` — all unchanged in what they do,
`beginNewRoom`/`commitNewRoom` just without the detour through the select
list.

**Five tests in `tests/api/test_web.py` check constructs that no longer
exist afterward** (`test_the_page_offers_the_room_bar_and_the_room_picker`
partially, `…room_select_uses_a_synced_draft…`,
`…new_room_option_resets_the_draft…`,
`…room_select_leaves_new_room_mode_when_a_normal_room_is_picked`,
`…room_picker_closes_new_room_mode_when_focus_leaves_it_entirely`). They
are **deleted, not rewritten**: their assertions concern a
mechanism that vanishes without replacement. What remains of their intent —
"the tile never shows a room the device doesn't have" — is no longer
a checkable claim afterward, because the tile no longer shows the room
at all.

## 6. Language

Reused: `web.devices.room_none`, `room_new`,
`room_new_placeholder`, `export`, `remove`, `export_hint_prefix`/`_suffix`.

New, with `en` and `de` entries:

| Key | en | de |
|---|---|---|
| `web.devices.menu` | Actions | Aktionen |
| `web.devices.menu_room_heading` | Room | Raum |

`web.devices.menu` is the accessible name of the ⋮ button — it carries no
text, so it needs a name. `menu_room_heading` labels the
room section in the menu, so the list of room names doesn't sit without
context above the two actions.

## 7. Error handling

| Case | Behavior |
|---|---|
| `saveRoom` fails (network, 4xx) | as today: `deviceActionError` as a banner, the menu is already closed by this point — the error sits above the list, not in a vanished menu |
| "+ New room …" confirmed with an empty field | nothing is saved, menu closes; identical to today's `commitNewRoom` |
| No bridge IP set | "Export" disabled, the note linking to settings stays below the footer, not in the menu — it applies to all tiles, not to this one |
| Device is removed while its menu is open | the tile disappears along with the `<details>`; no state is left behind, because none lived outside the DOM |

## 8. Verification

The WebUI tests only prove that a construct is shipped — for
behavior it takes the browser (see the experience from the
predecessor design). So both:

**Shipped** (`tests/api/test_web.py`): the `<details class="tile-menu">`
with `@click.outside` and `@keydown.escape`; the five menu entries; **no**
`room-select`, `roomSelectDrafts`, or `onRoomSelectChange` left in the
shipped `app.js`.

**Behavior** (throwaway harness against the vendored Alpine, results in the
implementation report):

- Menu opens and closes via the ⋮; a click elsewhere closes it; Escape
  closes it.
- A click on the ⋮ of a second tile closes the first menu.
- The current room carries the checkmark; a click on another one assigns it,
  the tile moves into the right group, the menu is closed.
- "+ New room …": text field appears in the menu, menu stays
  open while typing, Enter saves and closes, Escape cancels.
- After assigning the last device of a filtered room, `reconcileRoomFilter`
  still kicks in (the case from the re-review).

## 9. Open points

1. Whether the menu entries should additionally get arrow-key keyboard
   navigation remains open. `<details>` provides tab order and
   activation with Enter/space on its own; arrow keys would be their
   own keyboard loop and therefore state of its own again — exactly
   what this design wants to move away from. Add it later only if someone
   misses it.
2. With very many rooms the menu gets long. At what count it needs its
   own scroll area or a submenu is not decidable today — the
   order of magnitude seen so far is a handful of rooms.
