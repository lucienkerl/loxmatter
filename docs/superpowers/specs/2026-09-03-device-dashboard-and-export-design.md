# Device dashboard: values without expanding, export per device

Design, September 3, 2026. Extends
[the main document](2026-09-01-matter-loxone-bridge-design.md), section 8
(UI), as well as [the login design](2026-09-03-webui-login-design.md),
section 14.2 (Remaining configuration in the UI) — whose `setting` table
was intended for exactly this purpose, but so far only holds the password
hash.

## 1. The problem

Two separate complaints, one shared trigger: the device tile in the
"Devices" view shows, in its collapsed state, only the status dot, the
name, and a hint text about the export count. Anyone who wants to know
whether a plug is currently on, or how warm a sensor reads, has to click
"Details" on each device.

Second: an export today is exclusively an all-or-pending operation in its
own tab, with its own input fields for the bridge address and the ports.
Exporting a single, just-finished-commissioning device means: switch tabs,
re-enter the address (it is stored nowhere), preview or download directly.

## 2. What stays unchanged

The "Signals" view (full tree, functional/expert, individually togglable
exportability) stays exactly as it is — it is the place for the complete
view of a device, not the device tile. The global Export tab also stays;
it remains the only way to export several or all devices at once.

Explicitly **not** part of this design: the remaining configuration from
section 14.2 of the login design (Miniserver address, matter-server
address, data directory) — that continues to come from
`docker-compose.yml`/CLI options and, as announced there, gets its own
spec. This design fills the `setting` table with exactly three more keys:
the fields currently in the Export tab (bridge IP, UDP port, HTTP port).

## 3. Device tile: always open

**No more expanding.** The "Details"/"Collapse" toggle
(`toggleExpanded`, `expandedDeviceId`, `index.html:206-291`) is dropped.
Every tile loads and shows, immediately on entering the view, what today
is only there after a click: the functional signals (`firstSignalsFor`,
`FUNCTIONAL_PREVIEW_LIMIT` stays at 6) and the controls (`commandsFor`).

**Structure of a tile, top to bottom:**

1. **Header row** — type icon in a tinted circle, editable name, a status
   pill on the right.
2. **Values** — the functional signals as chips (label above/next to the
   value, value in monospace numbers).
3. **Controls** — the known commands as buttons, as before.
4. **Footer** — export hint (last exported / changed since export) on the
   left, "Export" and "Remove" buttons on the right.

**Status pill, three states, consistently color-coded:**

| State | Condition | Color | Icon |
|---|---|---|---|
| Unremarkable | online, `!changed_since_export` | no pill text, just a green edge stripe on the tile | — |
| Changed since export | online, `changed_since_export` (already exists, `ExportStatusOut`, see section 5) | Amber | Warning triangle |
| Offline | `!isOnline(device)` | Gray | "wifi-off" |

The edge stripe (4 px, left edge) carries the same meaning in addition to
the pill — deliberately redundant, so that a tile's state stands out even
when scrolling quickly over many devices, without having to read the pill.

`changed_since_export` currently only arrives via `GET /api/export/status`,
called by `loadExportStatus()` (`app.js:896-908`), so far exclusively from
within the Export tab. The Devices view must in future trigger this call
itself too (analogous, no new route), otherwise the status pill there has
no data to work from.

**Type icon.** A small, dedicated set of stroke icons (no icon font, no
external library — the UI runs offline, `vendor/alpine.min.js` is checked
in instead of loaded from a CDN for exactly that reason). The mapping from
device type to icon comes from the same source that already evaluates
`device_types` for the relevance rule today
([signal-selection design](2026-09-03-signal-selection-design.md) section
4.1): plug/relay → plug symbol, sensor with a motion cluster → motion
symbol, window/shading → louver symbol, anything unmapped → a neutral
placeholder symbol. A complete mapping table is a matter for the
implementation plan, not this spec.

**Color.** The accent color switches from green to copper/amber (`#a15a2c`
light, `#e2915c` dark) — for primary buttons, brand, type-icon background.
The status colors (green = unremarkable, amber = changed, gray = offline)
stay separate from that and do not change: an amber primary button next to
an amber status pill would otherwise be confusing, so "amber" stays
reserved exclusively for the "changed since export" status, and the
primary button's accent color is copper, a visibly different tone. The
rest of the palette (background, surface, border, text) stays unchanged
(`style.css:27-61`).

These three points (layout, status pills, copper/amber) were walked
through and approved with the client on an interactive HTML mockup.

## 4. New "Settings" tab

Fifth tab, on par with Devices/Signals/Export/System (`nav.tabs`,
`index.html`). A card "Miniserver connection" (not "Miniserver", to avoid
the same mix-up the existing hint text in the Export tab already warns
against — this means the address of **this bridge**, as seen by the
Miniserver, not the Miniserver's own address):

- IP of this bridge
- UDP port (virtual input)
- HTTP port (receiving commands)
- "Save" button, "Last saved … ago" hint

**Storage.** The three values go into the existing generic `setting` table
(`store.py:128-131`, created in `_migrate_to_v5` for the password hash,
intended per its own docstring in `auth_store.py:42-45` exactly for this
extension). New keys `bridge_ip`, `bridge_udp_port`,
`bridge_listen_port`, read/written via the same upsert access
(`INSERT … ON CONFLICT DO UPDATE`) that `AuthStore` already demonstrates
for `password_hash` (`auth_store.py:55-90`) — its own small class or an
extension of `AuthStore` is a matter for the implementation plan. Two new
endpoints following the pattern of `GET`/`PATCH /devices/{id}`
(`api/devices.py:191-206`): `GET /api/settings` and `PATCH /api/settings`.

Server-side instead of `localStorage`, because the bridge address is a
property of the installation, not of the browser — several people or
devices opening the same dashboard should see the same address without
retyping it.

A second card "More settings" with a placeholder sentence visibly marks
that more will be added here in future (cf. section 2 — not part of this
design).

## 5. Export tab: fields become read-only, preview/download stay

The three input fields in the existing Export tab
(`exportBridgeIp`/`exportPort`/`exportListenPort`, `app.js:249-251`)
become `readonly`, pre-filled from `GET /api/settings`, with a reference
"Managed in Settings → Miniserver connection". Checkboxes, "View preview",
"Download ZIP" stay unchanged (`index.html:456-479`, `app.js:914-999`) —
this tab remains the way to export "all" or "all pending" devices.

## 6. Export per device (new button in the tile footer)

**No preview step.** A click on "Export" on the tile immediately downloads
the ZIP for exactly this one device — the values are already visible on
the tile, so an additional preview would be duplicate information.

**Backend change, no new route.** `GET /api/export/download`
(`api/export.py:238-257`) gets an optional parameter `device_id`. If it is
set, the selection (currently `for device in store.devices()`,
`export.py:305`) iterates over only this one device, regardless of the
`only_pending` checkbox, and after successfully building the ZIP marks
only this one device as exported (the same deferred `mark_exported` logic
as today, see comments in `export.py`). `GET /api/export/preview` stays
unchanged — it is not called from the tile (see "No preview step" above).

Frontend: `downloadUrl()`/`downloadExport()` (`app.js:956-999`) get a
second, tile-specific variant that uses the same `download()` path
(error responses go through the UI, not as raw text — the same reason the
global export is already not a plain `<a href>`, `index.html:472-477`),
but passes `device_id` instead of the checkbox parameters.

## 7. Error handling

| Case | Behavior |
|---|---|
| Settings still empty (no `bridge_ip` stored) | "Export" button on every tile disabled, hint text "Please set the bridge IP in Settings → Miniserver connection first", with a link to the tab |
| Device offline | Values show the last known state (tile dimmed, as in the mockup), command buttons disabled, "Export" stays active — the last known configuration is still a valid export |
| `device_id` in `/api/export/download` unknown (device removed in the meantime) | 404, as `GET /devices/{id}` already returns today for the same case |
| `PATCH /api/settings` with an empty bridge IP | 422, analogous to the existing required-field check for `bridge_ip` in `export.py` |

## 8. Verification

- A device with no stored settings: the "Export" button on the tile is
  disabled, no call against `/api/export/download` is possible.
- `GET /api/export/download?device_id=…` delivers a ZIP with exactly the
  files of this one device and marks exclusively this one device as
  exported — a second, existing device stays untouched (`exported_at`
  unchanged).
- `only_pending=true` set together with `device_id`: `device_id` wins, the
  device is exported even if it would not be pending per
  `changed_since_export` (section 6).
- `GET`/`PATCH /api/settings` — values survive a process restart
  (migration/table already present, no schema update needed).
- Existing tests for `toggleExpanded`/`expandedDeviceId` are dropped along
  with the function; new tests cover that values and controls are visible
  without a click.

## 9. Open points

1. The complete device-type-to-icon mapping table (section 3) is a matter
   for the implementation plan, not this spec — the three types shown in
   the mockup (plug, motion, louver) are sufficiently covered; further
   Matter device types need a placeholder symbol until they are added
   individually.

   **Resolved** by the [devices-tab design from September 5, 2026](2026-09-05-devices-tab-rooms-and-tile-grid-design.md):
   the mapping is `profiles/categories.py`, and it delivers not only the
   icon but also the sort order within a room and the search term.
2. Whether an HTTP-port conflict (e.g. two bridges on the same host)
   should be checked when saving the settings is open — until someone asks
   for it: no, as with the existing Export tab already.
3. Migration of existing installations: whoever has already entered a
   bridge IP/ports in the Export tab today loses that entry on the first
   start of this version (it was never in the database, see section 1)
   and has to re-enter it once in Settings. No automatic carry-over
   possible, because the previous value was never stored anywhere.
