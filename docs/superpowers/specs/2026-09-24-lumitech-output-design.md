# One Output for the Loxone Lighting Controller

Design, 24 September 2026. Adds one virtual output per light, `lumitech`,
that takes whatever a Loxone lighting controller's colour actuator emits -
an RGB colour or a Lumitech white, each with its brightness - and gives the
light the part of it the light can carry. The single Matter commands
(`color`, `color_xy`, `colortemp`, `level`, `on`, ...) move into an expert
area and stop being exported by default.

It extends the mixed light groups design of 13 September 2026
(`2026-09-13-group-capability-fanout-design.md`) from group members to
single lights, and closes that design's open point on the colour-point
fallback for a single light (its Section 7, third bullet).

## 1. What Is Wrong Today

Every output the bridge exports is one Matter command, named after it. The
maintainer asked which output a Loxone lighting controller belongs on for a
warm-white lamp and for a dim-only lamp, and the answer depended on reading
the code:

- The Lumitech decoding (`commands/translate.py`, `decode_loxone_colour`)
  lives only behind `color` and `color_xy`. A tunable-white lamp such as the
  checked-in KAJPLATS WS has neither: its AcceptedCommandList names
  MoveToColor (XY), but `extract_commands`' capability gate drops it, and
  `extract_commands` on its fixture yields `off`, `on`, `toggle`, `level`,
  `level_onoff`, `colortemp` - no output of such a lamp understands a
  Lumitech value at all.
- The obvious output for a white, `colortemp`, expects a plain Kelvin
  number. A Lumitech value there is not rejected but sent wrong:
  `colour_temperature_payload(parse_kelvin("201002700"))` builds
  `colorTemperatureMireds: 0`.
- A dim-only lamp has only `level`/`level_onoff`. A Lumitech value there is
  read as a percentage, clamped to 254, and the lamp sits at full
  brightness.

Groups already do the right thing: `g{id}_color` decodes the value once and
`commands/adapt.py` gives each member what it can carry. A single light has
no such output.

## 2. Decisions Taken With the Maintainer

1. The new output takes **everything the lighting controller emits**: the
   packed RGB number and the Lumitech number, exactly the value range of
   `color` today. Not Lumitech alone.
2. On a light, **only the new output is exported by default**. Every single
   light command - `on`, `off`, `toggle`, `level`, `level_onoff`, `color`,
   `color_xy`, `colortemp` - goes into an expert area, deselected, and can
   be exported one by one from there.
3. The new default applies **retroactively** to lights commissioned before
   this version. Wiring in an existing Loxone project keeps working at
   runtime (Section 4.3); the project sync reports the old outputs as
   orphaned and the new one as new.
4. **Groups get the same output** with the same defaults, so a group reads
   the same in Loxone as a single light.
5. The output is called **`lumitech`**, titled "Lumitech / RGB" - the same
   in both languages, so a constant rather than an `i18n` entry. Keys:
   `d{id}_{endpoint}_lumitech`, `g{id}_lumitech`.
   "DMX" stays out of the name: it is the hardware interface in Loxone, not
   the actuator type.
6. The output is a **stored command row with a reserved pseudo pair**
   (Section 4.1), not a table of its own and not a key derived at runtime.

## 3. What the Output Does

### 3.1 Per Light

The value is decoded once by `decode_loxone_colour` into a colour (RGB) or
a white (Kelvin), always with a brightness. The endpoint's own stored light
commands then decide what is sent. The rules are those of
`commands/adapt.py` for a group member, reused rather than copied:

| The light carries ... | RGB value | Lumitech value |
|---|---|---|
| colour and colour temperature (KAJPLATS CWS) | colour + brightness | colour temperature + brightness |
| colour, no colour temperature | colour + brightness | the white as a colour point (Section 3.3 of the group design) + brightness |
| colour temperature only (KAJPLATS WS) | brightness only; the white stays | colour temperature + brightness |
| brightness only (TRADFRI WW) | brightness only | brightness only |
| on/off only | on if brightness > 0, else off | the same |

- **Brightness 0 is one off call**, with no colour call before it.
- **Order:** colour or colour temperature first, then brightness, with
  `ExecuteIfOff` - the order `to_device_calls` documents, so a lamp that was
  off does not flash up in its old colour.
- **XY before hue/saturation.** When a light carries both, the colour goes
  as MoveToColor (XY): XY is mandatory for a Matter Extended Color Light and
  hue/saturation optional, and ZHA sends colour only as XY. On a group, the
  output's own name decides between `color` and `color_xy`; `lumitech`
  names neither, so it needs this fixed rule.
- **One output per light endpoint.** An endpoint is a light when its Matter
  device type is one `profiles/categories.py` maps to the light category -
  the same table, not a second list. A dimmable plug is not a light and gets
  no `lumitech` output.
- **A value that means nothing** answers 400 with the translated messages
  `color` already uses: a channel above 100 percent, a malformed Lumitech
  number, a fraction.

### 3.2 Per Group

`g{id}_lumitech` gives every member what Section 3.1 gives it as a single
light, in one fan-out, with the fan-out's ordering and counting rules
unchanged. For the same light and the same value, the calls a member
receives through `g{id}_lumitech` are byte-identical to the calls
`d{id}_{endpoint}_lumitech` produces.

### 3.3 `colortemp` Rejects a Lumitech Value

`colortemp` keeps taking a plain Kelvin number. A value that
`is_lumitech` recognises now answers 400 with a translated message instead
of reaching `kelvin_to_mireds` and sending 0 mired. This applies on the
device path and the group path alike. It is the one behaviour change to an
existing output in this design.

## 4. Where It Lives

### 4.1 The Output

- **`profiles/light_commands.py`:** `LUMITECH: Final = (-1, 0)`, added to
  `LIGHT_COMMAND_PAIRS`. A negative cluster id exists in neither Matter nor
  Zigbee, so the pair can never coincide with a real command.
- **`export/commands.py`, `extract_commands`:** appends
  `DeviceCommand(endpoint, -1, 0, "lumitech", takes_value=True)` for every
  light endpoint. Keys and storage go through `register_commands` as for
  every other command, and the existing `backfill_commands` creates the row
  for every existing light on the first start.
- **`model/store.py`, `register_group_commands`:** no change of rule. With
  `LUMITECH` in `LIGHT_COMMAND_PAIRS`, the union rule offers
  `g{id}_lumitech` as soon as any member carries it.
- **`commands/adapt.py`, new `adapt_device_command(command, device_rows,
  value)`:** for `LUMITECH`, builds the calls through `adapt_group_command`
  over the light rows of the command's own endpoint; for every other pair it
  is `to_device_calls` unchanged. Both device routes (`/cmd/{key}/{value}` in
  `loxone/server.py`, `POST /api/commands/{key}` in `api/control.py`) call it
  in place of `to_device_calls`. It lives in `adapt.py`, not in
  `translate.py`, because `adapt.py` already imports `translate.py` and the
  reverse would be a cycle.
- **`commands/adapt.py`:** treats `LUMITECH` like the two colour pairs when
  decoding, and applies the XY-before-HS rule of Section 3.1 when the named
  pair is `LUMITECH`.
- **Every place that reads a pair as a real Matter command skips it:** the
  device controls route in `api/control.py` and the group controls route in
  `api/groups.py` (no slider, no "+N more" count). Raw mode cannot render
  it as `c{cluster}_cmd{command}`: the row is appended with its slug, never
  derived from the AcceptedCommandList.

### 4.2 Export Selection

- **Schema v13** (v12 is the feedback-input migration of the same day):
  `ALTER TABLE command ADD COLUMN exported INTEGER` and the same for
  `group_command`. Nothing else. The migration is additive; a
  rollback by the updater to an older version does not read the column.
- **`NULL` means "follows the default rule"; `0` or `1` is an explicit
  choice**, written only by the checkbox in the web UI. Every row existing
  at migration time is `NULL` and therefore follows the new rule at once -
  decision 3, with no data rewritten.
- **The default rule, computed on read and never stored:** a command row is
  *expert* and deselected when its pair is a light pair other than
  `LUMITECH` **and** its endpoint has a `lumitech` row. Every other row is
  *functional* and selected - `lumitech` itself, and every command of a plug
  or any other non-light. For a group the same rule applies without the
  endpoint: when the group has `g{id}_lumitech`, its other light pairs are
  expert. Whether an endpoint is a light is thereby read from the rows
  themselves, not from a second source.
- **One place decides it.** The store resolves the rule when it reads a row
  and hands out `StoredCommand.exported` / `.functional` (and the same on
  `StoredGroupCommand`) as plain booleans; `to_outputs` /
  `to_group_outputs` filter on `exported`, and every export path - the
  template, the project sync, the CLI - goes through those two, so the
  template and the sync cannot disagree about which keys exist.
- **Recomputing a group keeps the choice.** `register_group_commands`
  updates surviving rows in place (it deletes only pairs that drop out and
  inserts only new ones), so `exported` survives; this is pinned by a test,
  not assumed. New rows start as `NULL`.
- **`register_commands` keeps the choice** for the same reason: it updates
  an existing row's `slug` and `takes_value` only.

### 4.3 Runtime Is Untouched

`resolve_command`, `/cmd/{key}/{value}` and `POST /api/commands/{key}` do
not look at `exported`. A deselected `d5_1_color` keeps working until the
Loxone project is rewired, the same as an unexported signal keeps its UDP
wiring today.

### 4.4 Export and Project Sync

- The template (`VO_*.xml`) and the project sync take only exported
  commands: a filter before `to_outputs` / `to_group_outputs`, through the
  helper of Section 4.2.
- The on/off pairing in `_to_outputs` (`on` with `off_path`) works on the
  exported rows only. With only `on` selected, the output is a plain one
  without an off path.
- The `lumitech` output's title is "Lumitech / RGB" where every other
  output's title is its slug (`export/outputs.py`); its `Comment` carries
  the key, as for every output. There is no description field on a virtual
  output command to say more.
- The first sync after the update reports the previously wired outputs
  (`d5_1_color`, `g1_color`, ...) as orphaned and `..._lumitech` as new.
  Nothing is deleted.
- Devices and groups whose command list changed read "changed since the
  last export", through the existing `updated_at`.

### 4.5 Web UI

- **Signal dialog of a device** (tile menu, Signals): below the inputs, a
  second part "Outputs" with the same functional/expert split, the same
  grid and the same export checkbox column. `lumitech` stands open at the
  top; the single commands sit in the collapsed expert block. The split
  comes from the API (`functional`), not from a copy of the rule in the
  browser.
- **Group dialog:** the same "Outputs" part. A group has no signals and so
  no signal dialog.
- **API:** `GET /api/devices/{id}/outputs` and `GET /api/groups/{id}/outputs`
  list the outputs with `key`, `slug`, `title`, `exported` and `functional`.
  `PATCH /api/commands/{key}` with `{"exported": true|false}` sets the
  choice, for a device key and a group key alike - the key namespace is
  shared, the way `POST /api/commands/{key}` already serves both. `POST`
  stays the send route.
- **Export tab:** "Commands" counts exported commands only. The "withheld
  in the expert area" column counts signals and commands, and its
  explanation says so.
- **Controls are unchanged.** The sliders and the colour area keep driving
  the single commands, selected or not - `exported` concerns Loxone only.
- Every new string goes into `strings.yaml` with `en` and `de`.

## 5. Testing

- **The adapter, one test per row of the table in Section 3.1**, on the
  command sets of real devices: `ikea_kajplats_cws_lamp.json`,
  `ikea_kajplats_ws_lamp.json`, and the TRADFRI WW rows captured from the
  maintainer's Pi on 13 September 2026 that the group tests already carry.
  The colour-without-temperature and on/off-only sets are written out and
  named as constructed. Each test asserts the exact calls and their order;
  brightness 0 yields one off call; XY wins over hue/saturation.
- **Both paths agree:** for each light and value, `d..._lumitech` and a
  group member through `g..._lumitech` produce byte-identical calls.
- **`extract_commands`:** both KAJPLATS fixtures get exactly one
  `lumitech` row on endpoint 1; `ikea_grillplats_plug.json` gets none.
- **`colortemp`:** a Lumitech value is a 400 with a translated message, on
  the device path and the group path.
- **Store:** migration v12 to v13 on a real v12 database (column present,
  every row `NULL`); the default rule for light and non-light endpoints and
  for groups; an explicit choice survives `register_commands` and a group
  recompute; `g{id}_lumitech` appears once a member carries `lumitech`.
- **Export and sync:** a deselected command is missing from the template;
  the on/off pairing with only half selected; the sync reports the old keys
  as orphaned and `lumitech` as new; template and sync see the same key set.
- **Runtime:** a deselected command still sends through `/cmd/...`.
- **Web UI:** the Alpine bindings of both "Outputs" parts are run in a
  throwaway harness, not only checked for being shipped.
- **Fault injection** for every protective test, as on the branches before.
- The suite runs in its measured three parts, each in the foreground.

**Hardware**, on the maintainer's test Pi, read back through the running
bridge's `signals` route, never through a fresh `snapshots()` call:

1. Lumitech 2700 K at 30 % on the KAJPLATS WS's `lumitech`: 370 mired,
   about 30 %.
2. The same value on the KAJPLATS CWS: colour temperature, ColorMode 2.
3. Blue at 60 % on the CWS: colour as XY, ColorMode 1.
4. The same Lumitech value on `g1_lumitech`: the CWS lamps warm white at
   30 %, the TRADFRI WW at 30 %.
5. A project sync lists the old outputs as orphaned.

## 6. Documentation

- CHANGELOG `[Unreleased]`: the new output, the expert area for the single
  commands, the one-time change to exported outputs, the `colortemp` fix.
- A dated note at the top of `2026-09-13-group-capability-fanout-design.md`
  pointing here for its Section 7 open point; its body is not rewritten.

## 7. Explicitly Not Built

- **The dimmer actuator (0-100 %) as a format of its own.** It happens to
  work on a dim-only lamp through the RGB reading, but turns a colour lamp
  red. `level` in the expert area stays the output for it.
- **A white temperature derived from an RGB colour** for tunable-white
  lamps. Decision 2 of the group design, unchanged.
- **Feedback** of the light's state as one Lumitech input to Loxone. The
  opposite direction, and a subject of its own.
