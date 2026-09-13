# Device Dashboard: Room Sidebar, Denser Cards, and Per-Device Expert Settings

Design, September 13, 2026. Reworks the shell around the device grid
(`index.html:822` onward, `2026-09-05-devices-tab-rooms-and-tile-grid-design.md`)
and adds a new per-device "Expert Settings" modal.
The tile's own content — value rows, `value-fresh` highlighting, battery
row, kebab menu — is deliberately left alone; this design rearranges the
space around it and adds one new modal, worked out with the maintainer
through a set of comparison mockups (three layout directions plus the
expert modal, on one canvas).

## 1. The Problem

Two complaints from actually living with a growing install:

1. **The grid wastes width on a wide monitor.** `.device-grid` is
   `repeat(auto-fill, minmax(260px, 1fr))` (`style.css:1872`); on a wide
   desktop window that leaves either a half-empty last column or large
   inter-card gaps, and the only thing above the grid that could use that
   width — the room-chip bar (`index.html:822`) plus the search field
   (`index.html:886`) — is a single line, so most of the header row is
   empty too.
2. **A flat, filtered grid stops scaling once the room count grows.** The
   room bar filters to one room or "All"; there is no view that shows every
   room's devices at once with a sense of where one room ends and the next
   begins. At around 20-30 devices (the real install this design was
   discussed against) that already reads as one undifferentiated wall of
   cards once "All" is selected.

On top of the layout complaint, the maintainer wants a place to see
per-device technical detail — vendor, model, firmware, node address,
endpoint/cluster list — that today only exists as unlabeled rows buried in
the signals modal's "Expert" group (`web.signals.group_expert`,
`strings.yaml:1758`), mixed in with every other technical signal.

## 2. What Stays Unchanged

- **The tile's own content.** Value rows (ranked signals, up to the
  existing cap), the `+ N more` overflow row, `value-fresh` highlighting
  on change, the always-visible battery row, the offline pill, the color
  stripe, the kebab menu and everything in it (rename room, edit signals,
  export, remove) — all unchanged in behavior. This design only changes
  what surrounds the grid and narrows the card, not what a card shows.
- **The signals modal.** It stays the full, ungrouped-by-relevance view of
  *one* device's signals, "Expert" group included. The new expert modal
  (section 4) is a curated *summary* alongside it, not a replacement.
- **Room storage.** Rooms are still not their own object
  (`2026-09-05-devices-tab-rooms-and-tile-grid-design.md`, section 3.2) —
  a room is still exactly the `DISTINCT` over `device.room`. Renaming a
  room is still a bulk write across its devices.
- **The Loxone export**, and everything about what does or doesn't count
  as "changed since export."

## 3. Layout: A Room Sidebar Replaces the Room-Chip Bar

The room-chip bar and the search field currently sitting in one row above
the grid (`index.html:822-910`) move into a persistent left sidebar,
matching the pattern in Home Assistant's areas list and Linear's project
list rather than a row of filter chips:

- **Search** sits at the top of the sidebar, same behavior as today
  (filters by name/category/room), just relocated.
- **Below it, a room list**: "All Devices" first (with the total count,
  same number `roomChips()` computes today), then one row per room with
  its device count, in the same order the room bar uses today. The active
  selection gets the accent-tinted background and left accent bar the
  mockup shows; rename-room (today's pencil icon next to the active chip,
  `index.html:849`) becomes a hover-revealed action on the active row
  instead of a dedicated button, since the sidebar has no room for a
  second always-visible icon per row.
- **This is still a filter, not a router**: selecting a room shows only
  that room's devices, exactly like the chip bar today. Nothing here
  changes the URL or introduces routing.
- **The room bar's existing conditions carry over unchanged**: the
  sidebar's room list only appears once at least one device carries a
  room (`hasAnyRoom()`), and the empty-state text
  (`web.devices.empty`) is unaffected.

Rationale for choosing this over keeping the chip bar and just narrowing
cards (the alternative sketched as "Direction A" during the brainstorm):
the chip bar is a single line that cannot itself use the freed-up width,
so it does not address complaint 1. Docking it as a sidebar turns the
"wasted" width into navigation, and gives every room a permanently visible
row instead of a chip that can wrap or scroll once there are many rooms.

## 4. Content: Card Grid, Grouped by Room, Denser

**Cards stay cards.** A dense single-line-per-device table was the other
direction explored ("Direction B") and rejected as the *default* view: the
dashboard is a commissioning/debugging tool used to configure and diagnose,
not a daily control panel, and that is exactly when seeing several signals
per device at once — each independently able to flash via `value-fresh`
when its value changes — matters most. The concrete case that settled it:
commissioning several identical-looking motion sensors and walking around
triggering them one at a time to see, at a glance across the whole grid,
which card just lit up. A table row that shows only one value per device
cannot answer that; a card showing two or three can, without opening
anything.

Changes to the grid itself:

- **Narrower minimum card width.** `minmax(260px, 1fr)` becomes something
  around `minmax(200px, 1fr)` (the mockup used 200px; the exact number is
  a visual-testing detail for the implementation, not a design commitment)
  so a wide monitor fills with more columns instead of wasted gap.
- **Grouped by room with section headings** when "All Devices" is
  selected: a heading row per room (name + device count, matching the
  sidebar's own count) above that room's slice of the grid, in the same
  room order as the sidebar. Selecting a single room in the sidebar shows
  its grid without a heading, same as selecting a room today shows a
  filtered, unheaded grid.
- **Devices with no room** keep behaving as they do today (grouped as
  their own "No Room" bucket, both in the sidebar list and, when "All
  Devices" is selected, as a heading like any other room).

### 4.1 Optional Secondary View: A Compact List

The mockups also carried a card/list toggle in the content header (grid
icon / list icon). The list mode reuses the dense, one-row-per-device
table from "Direction B" — full-width rows, a Room column (since a flat
list under "All Devices" no longer has room headings to carry that), and
the same `+ N more` overflow link for a row with more values than fit.
This is explicitly a *secondary* view for a quick, table-shaped scan or
export-style overview, not the default: it inherits Direction B's
trade-off of showing only one live value per device, which is why it is
not the primary way to work with the dashboard (section 4's rationale).
Cards remain the default on load; the toggle's own state does not need to
persist across reloads.

## 5. Expert Settings Modal

A new modal, opened from the existing kebab menu (`index.html:1521`) via a
new "Expert Settings…" entry, next to "Edit signals…". It shows what
is already knowable about a device today, split visibly into what exists
now and what does not yet:

**"Device" section** — vendor, model, firmware/software version, serial
number. These already arrive as ordinary signals today (the Basic
Information cluster's `VendorName`/`ProductName`/`SoftwareVersionString`/
`SerialNumber` attributes on Matter flow through `extract_signals`
un-filtered, `profiles/clusters.yaml:245-253`; the Zigbee equivalent needs
confirming against `zigbee/translate.py` during planning) — but sit
unlabeled among every other signal today. Field left blank (not "0" or
"unknown") when a device does not report it.

**"Address & Technology" section** — the technology (Matter/Zigbee) and
transport, and the address labeled for what it actually is per technology:
"Node-ID" for a Matter device (`DeviceOut.address` is already the node ID,
`api/devices.py:250`), "IEEE Address" for a Zigbee device (`address` is
already `facts.ieee`, `zigbee/translate.py:688` — no backend change needed
for Zigbee's address, only the UI label). Below that, the endpoint list:
one line per endpoint naming the clusters present on it, reusing the same
endpoint/cluster grouping the signals modal already computes for its own
layout — not a new grouping to invent.

**"Coming later" section** — signal strength (RSSI/LQI) and per-device IP
address. Visually set apart (the mockup uses a dashed border and a muted
"planned" badge) and each field shows an em dash, never a fabricated or
zero value. As established when this was discussed: neither exists in the
backend today (Zigbee never reads `device.lqi`/`device.rssi` from zigpy;
Matter's diagnostics clusters are referenced today only to block commands,
never parsed for their `Rssi`/`NetworkInterfaces` attributes; no per-device
IP is captured anywhere). Wiring either up is real integration work on
both device stacks and is explicitly **out of scope for this design** — a
separate future spec once that work is planned.

### 5.1 What This Needs on the Backend

Vendor/model/firmware/serial and the endpoint/cluster summary are not
currently exposed as their own fields — today a client would have to find
them by matching specific cluster/attribute paths inside the full signal
list, which is backend knowledge that does not belong duplicated in the
frontend. This design adds one new read endpoint,
`GET /api/devices/{id}/expert`, that returns them already resolved:

```json
{
  "technology": "matter",
  "transport": "thread",
  "address_label": "Node-ID",
  "address": "0x00000042",
  "vendor": "Danfoss",
  "model": "Ally Thermostat",
  "firmware": "1.4.2",
  "serial": "4471-2201-009",
  "endpoints": [
    { "endpoint": 0, "clusters": ["Basic Information", "Descriptor"] },
    { "endpoint": 1, "clusters": ["Thermostat", "Temperature Measurement"] }
  ]
}
```

Any field the device does not report is `null`, rendered as a blank, not
an em dash — the em dash is reserved for the "coming later" section, so
the two kinds of "nothing to show" (not reported vs. not yet built) stay
visually distinct. Exact field derivation (which cluster/attribute maps to
`vendor` etc. for both Matter and Zigbee) is implementation detail for the
plan, not this design.

## 6. i18n

Every new user-facing string goes through `strings.yaml` with both `en`
and `de` values, resolved with `i18n.t(...)` — same rule as the rest of
the UI (`CLAUDE.md`). New strings include, at minimum, the sidebar's "All
Devices" / "No Room" labels (already exist as `web.devices.room_all`
etc. — reused, not new), the view-toggle's two labels, the kebab menu's
new "Expert Settings…" entry, the expert modal's three section
titles, its field labels, and the "coming later" badge text. Exact key
names are chosen during implementation, following the existing
`web.devices.*` / `web.signals.*` naming.

## 7. Non-Goals

- **Signal strength (RSSI/LQI) and per-device IP address.** Backend
  integration work, deferred to a future spec (section 5).
- **A MAC address for Matter devices.** Would need parsing
  `GeneralDiagnostics.NetworkInterfaces` (a struct), which nothing in the
  codebase does today (`profiles/table.py:23` treats structs/lists as
  non-primitive). Same future spec as signal strength.
- **Changing how rooms are stored, or adding room management.** Section 2.
- **Changing the signals modal.** Section 2.
- **Persisting the card/list view toggle's state**, or making the list
  view support multi-value live highlighting — it deliberately doesn't
  (section 4.1).

## 8. Testing

Existing web tests (`tests/test_web.py`) cover the room bar, the grid, and
the kebab menu's z-index/stacking behavior — those need updating for the
sidebar and the narrower grid rather than being duplicated. The new
`GET /api/devices/{id}/expert` endpoint gets ordinary API tests: one
Matter device, one Zigbee device, and one device with fields missing
(unreported vendor/model), asserting `null` rather than a placeholder
string. The expert modal's "coming later" section needs a browser-level
check (per `webui-browser-verification` conventions for this project)
that it renders an em dash and never a value, since there is no live data
to assert against.
