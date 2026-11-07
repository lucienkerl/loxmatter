# First-run setup assistant and the migration from Home Assistant

Date: 2026-11-07. Status: implemented.

## 1. Goal

A new user should end up with a working bridge, and - if they used Home
Assistant before - with their old devices in it, without a terminal and
without re-pairing. Every setting that used to be a question in
`install.sh` (Thread stick, Bluetooth adapter) moves into the web interface,
into an assistant that appears on first start and stays until it is
finished.

The screen designs were agreed beforehand as a clickable prototype (three
paths: new, move with the Zigbee stick moved over, move onto loxmatter's own
stick), including every combination of sticks: both, only Thread, only
Zigbee, none.

## 2. The assistant

- **State.** `SetupStore` keeps `{completed, step, path}` as one JSON value
  under `setup` in the `setting` table - no schema bump. `step` is opaque to
  the bridge; the web UI owns the order of its screens.
- **Existing installations.** No record does not mean new: every bridge
  that predates the assistant has none. `GET /api/setup` answers
  "completed" when the store already has devices, and writes that answer
  down on the first ask, so removing the last device later does not bring
  the assistant back.
- **Routes.** `GET/PATCH /api/setup`, `POST /api/setup/complete`,
  `POST /api/setup/restart` (Settings, "Run the assistant again"; starts at
  the path choice, devices untouched). All behind the session guard: the
  password screen comes first, so a session exists by the time the
  assistant asks anything.
- **Steps.** welcome (language) → miniserver → path; then either
  sticks → networks → done, or ha_login → ha_devices → sticks → ha_thread →
  ha_matter → ha_zigbee → ha_cleanup → done. A step that does not apply is
  left out of the list (no Thread stick and no Thread devices: no Thread
  step; no ZHA or Zigbee skipped: no Zigbee step).
- **Deviation from the prototype.** The password comes before the language
  choice, not after it: changing the language is an `/api` route and needs a
  session. The password screen itself is unchanged.
- **The sticks step reuses the radios card's state and actions**
  (`radiosDraft`, `confirmApplyRadios`, `zigbeeDraft`, `applyZigbeeRadio`),
  so the assistant and Settings can never disagree about a stick. The
  Bluetooth adapter is not asked: the installer takes the first one, and
  Settings → Radios changes it.

## 3. Signing in to Home Assistant

Home Assistant is its own OAuth2 provider and accepts any web address as
`client_id` when `redirect_uri` has the same scheme and host (IndieAuth,
`homeassistant/components/auth/indieauth.py`; local addresses allowed).
Nothing has to be configured in Home Assistant.

1. The browser posts the Home Assistant address and its own origin to
   `POST /api/homeassistant/auth/start`; the bridge checks the address
   (`/auth/providers` answers without a sign-in), keeps a random `state`
   in memory and answers with `/auth/authorize?client_id=<origin>/&
   redirect_uri=<origin>/?ha_auth=1&state=…`.
2. The browser goes there (same window, not a pop-up) and the user signs in
   on Home Assistant's own page.
3. Home Assistant sends the browser back to `/?ha_auth=1&code=…&state=…`.
   The session cookie is `SameSite=Strict`, so the callback cannot be an
   `/api` route reached by that redirect; the page reads the code, removes
   it from the address bar and posts it to `/auth/finish`, which trades it
   at `/auth/token`.

The fallback is a long-lived access token, checked by one `get_config`
call before it is kept. Tokens live in `homeassistant.connection` in the
`setting` table until the user finishes; access tokens are refreshed two
minutes before their 30 minutes run out. A token never appears in a
response, a log line or an error message.

**A failed Home Assistant sign-in answers 403, never 401**: the web UI reads
any 401 from `/api` as "your session with this bridge has ended".

## 4. The device list

Read-only: device registry, area registry, `config_entries/get` for `zha`
and `matter`, `matter/node_diagnostics` per Matter device, `zha/devices`,
`thread/list_datasets`.

- One entry per Matter **node**: a bridge (Hue, DIRIGERA) is one node shown
  by Home Assistant as one registry device per bridged lamp; it joins this
  bridge's fabric once, so it is listed once with its members.
- Kinds: `matter_wifi`, `matter_thread`, `matter_bridge`, `zigbee`, `other`.
- Availability: `ready`, `needs_thread`, `needs_zigbee`, `later` (Zigbee
  devices that only serve housekeeping clusters - remotes, buttons - which
  send commands this bridge cannot pass on yet), `unsupported`, `done`
  (moved by an earlier run, from `homeassistant.results`).
- The boxes of Thread and Zigbee devices can be ticked before their stick
  exists: the sticks step comes after the list.

## 5. Thread and Matter

`POST /api/homeassistant/thread` fetches the preferred dataset
(`thread/get_dataset_tlv`) and calls `ThreadNetworkKeeper.adopt`:
same Extended PAN ID → only hand it to matter-server; otherwise the border
router's own network is replaced (`PUT /node/state disable`, `PUT
/node/dataset/active`, enable) - but never while that network holds Thread
devices (`ThreadNetworkInUseError`, the rule of the fresh-installation
design, section 4.3).

`POST /api/homeassistant/matter` starts a background job (polled through
`GET /api/homeassistant/job`). Per device: `matter/open_commissioning_window`
→ `commission()` with `setup_manual_code` and Home Assistant's room →
`rename_device` with Home Assistant's name. Home Assistant keeps the device
(multi-admin) until the clean-up.

## 6. Zigbee from ZHA

ZHA and this bridge both stand on zigpy (`zigbee/migration.py`):

1. **Pause** (`POST …/zigbee/pause`, the first change in Home Assistant,
   confirmed in the UI): `zha/network/backups/create`, kept as
   `homeassistant.zigbee_backup` (it carries the network key and never
   leaves the bridge), then `config_entries/disable`. A repeat keeps the
   first backup.
2. **Stick.** Moved over: the network is in the stick's own tokens, nothing
   is written. Own stick: `POST …/zigbee/restore` writes the backup with
   `app.backups.restore_backup`, optionally with the EZSP key that
   overwrites the coordinator's EUI64 (most sticks allow that once; without
   it, bindings and IAS enrolments point at the old address), then the
   supervisor reconnects.
3. **Enter** (`POST …/zigbee/import`), only once the stick's Extended PAN ID
   equals the backup's: each device is rebuilt from ZHA's signature (node
   descriptor, endpoints, clusters, manufacturer and model - also into the
   Basic cluster cache so a restart keeps them), announced with
   `app.device_initialized` - which saves it to zigpy's database, applies
   quirks and runs configure-on-join - and adopted into the store with
   Home Assistant's name and room (`zigbee/adopt.py`, shared with the
   pairing tab).

## 7. Clean-up

Optional, and it only touches devices `homeassistant.results` records as
arrived. Matter: `config/device_registry/remove_config_entry` with the
Matter entry - Home Assistant's Matter integration then takes its own fabric
off the device; this bridge's fabric stays. ZHA: deleting its config entry
(`DELETE /api/config/config_entries/entry/{id}`), on request. Home
Assistant's Thread network is left alone: the devices use it, now through
both border routers. Finishing the assistant forgets the tokens and the
Zigbee backup.

## 8. Installer

`install.sh` no longer asks: without `LOXMATTER_MODE`/`RADIO_DEVICE` it
installs without Thread, says how many USB sticks it found, and leaves them
to the assistant. Several Bluetooth adapters: the first is taken. A script
can still choose the Thread stick with `LOXMATTER_MODE=thread` or
`RADIO_DEVICE=…`.

## 9. Home Assistant on the same host

Found on the first real target (a Raspberry Pi running Home Assistant
Container, 7 November 2026): one host cannot run two Matter servers (both
want 5580) or two border routers on one stick, and Home Assistant's border
router page holds 8080 while ioBroker's admin page holds 8081.

- **Ports.** The installer checks 8080 (web interface) and 8081 (border
  router REST) before it installs anything and moves a taken one to the
  next free port (`LOXMATTER_PORT`, `OTBR_REST_PORT` in `.env`). The otbr
  image's entrypoint has no option for the REST port; the value rides in
  its `--trel-url` slot, which it passes to otbr-agent as it stands. Unset,
  the otbr command renders exactly as before.
- **Take-over.** `LOXMATTER_TAKE_OVER=home-assistant` runs
  `scripts/home-assistant-handover.sh take` in place of `compose up`. It
  reads Home Assistant's Thread dataset before it stops anything, stops and
  renames Home Assistant's Matter server and border router, starts
  loxmatter's Matter server on a copy of the Matter data (owned by UID
  1000) and writes the dataset into loxmatter's border router before the
  bridge starts. Home Assistant's Matter integration reconnects to
  `ws://localhost:5580/ws` by itself, so Home Assistant keeps its Matter
  devices. `give-back` undoes it from the record `take` leaves.
- **The Matter step** then pairs nothing: a node whose Home Assistant
  identifier carries this bridge's compressed fabric id (`in_fabric`) is
  entered into the store from its snapshot with Home Assistant's name and
  room (`register_node`, shared with commissioning).

## 10. Not yet

Zigbee2MQTT (the assistant says so on the path screen); remotes and buttons
(listed as "comes later"). The Home Assistant calls are built from Home
Assistant's source and tested against a fake; they have not yet run against
a real Home Assistant - a test migration needs the user's go-ahead, because
pausing ZHA and the clean-up change it.
