# Configuration Values a Device Brings Along

Design, 8 November 2026. A device's writable settings - a motion sensor's
keep time, a presence sensor's sensitivity, an LED indicator - become
visible in the web UI and settable from it and from Loxone. Solved for
every device that describes its settings, not for one model.

## 1. What Happened

On 8 November 2026 the maintainer asked why the keep time of his HOBEIAN
ZG-204Z motion sensors (three of them, now in loxmatter's own Zigbee
network after the takeover from Home Assistant) could not be found in
loxmatter, and asked for such values to be settable "via Loxone or via
loxmatter".

Read on the test Pi, without changing anything:

- The sensor reports `HOBEIAN` / `ZG-204Z` in its Basic cluster (attributes
  4 and 5). Neither zha-quirks 2.2.2 (loxmatter) nor 0.0.149 (Home
  Assistant 2025.11.3) has a quirk for that pair; only its siblings
  ZG-204ZL (`_TZE200_3towulqd` / `TS0601`) and ZG-204ZM are covered. Home
  Assistant never offered a keep-time entity for it either.
- Endpoint 1 carries Basic, PowerConfiguration, Identify, IAS Zone and the
  Tuya cluster 0xEF00. Occupancy and battery come through the standard
  clusters. Neither zigbee database holds a single value of 0xEF00.
- zigbee2mqtt's definition (`zigbee-herdsman-converters`,
  `src/devices/hobeian.ts`, `zg204_attr`) settles where the two settings
  live: **not** in a Tuya data point but on IAS Zone - keep time as
  attribute 0xF001 written as uint8 without a manufacturer code (0 = 30 s,
  1 = 60 s, 2 = 120 s), sensitivity as the standard
  `current_zone_sensitivity_level` 0x0013 (0 = low, 1 = medium, 2 = high).

## 2. One Value Scale

A configuration value has exactly one number that the web UI shows, Loxone
sends to the output and the input reports back (`device_config.py`):

- a number shows the raw value times the source's `multiplier`;
- an enum whose every choice names a distinct number (`_30_seconds`,
  `_60_seconds`, ...) is set and reported by that number, so a Loxone
  output sends 60 for a minute, not the enum's position 1;
- any other enum is set by its raw value (0 = low, 1 = medium, ...);
- a switch is 0 or 1.

`to_raw` and `from_raw` are the only crossings between the two scales. The
Zigbee translation writes the snapshot's value of an option's attribute on
this scale already (`translate._apply_config_options`), so the reading that
reaches Loxone and the value Loxone sends are the same number.

## 3. Where the Settings Come From

**Zigbee: the quirk.** A v2 quirk (`QuirkBuilder`) describes every value
Home Assistant offers as a number, select or switch entity in
`entity_metadata`: attribute, cluster, endpoint, range or enum, name and
`translation_key`. That is maintained upstream for hundreds of Tuya, Aqara
and other devices, so `zigbee/options.py` reads it instead of keeping a
second catalogue. Where it lives in zha-quirks 2.2.2 was measured: the
resolved zigpy device carries `_quirk_registry_entry`, whose
`zha_device_factory.quirk_definition.entity_metadata` is the tuple.

Only what can be written becomes an option: `NumberMetadata`,
`SwitchMetadata`, and `ZCLEnumMetadata` on the select platform. An enum on
the sensor platform (the ZG-204ZM's `human_motion_state`) is a reading; a
write-attribute button is an action. Both stay out. The classes are told
apart by name, so the module imports nothing from zhaquirks; a test pins
the names against the installed package.

**Quirks loxmatter ships itself.** `zigbee/local_quirks.py` holds quirks
for devices in use that zha-quirks lacks, written with zha-quirks' own
`QuirkBuilder` so the same reader covers them and each could go upstream
unchanged. They register after `zhaquirks.setup()`; the registry keeps the
first match per manufacturer and model, so an upstream quirk for the same
device would win. The first one is the ZG-204Z: it replaces IAS Zone by a
subclass that declares 0xF001 (`zcl_type=uint8`, not manufacturer
specific) and describes both settings as enums.

**Matter: not yet.** loxmatter cannot write a Matter attribute today
(`api/control.py`, "Finding on the writability of an attribute"), and the
installed Matter package carries no table of writable attributes. Matter
devices therefore have no options; `NodeSnapshot.config_options` is empty
for them. Adding them later means a write path in `BridgeMatterClient` and
a curated list, for example `OccupancySensing.HoldTime` (1030/3) with its
limits from `HoldTimeLimits` (1030/4).

## 4. An Option Is a Command Row

Writing an option is what Loxone does to it, so each option is stored in
the `command` table and gets a key, an `exported` flag, a virtual output
and `/cmd/{key}/{value}` like every command - nothing in the export, the
project sync or the command gate needed a second path.

- **Command ID.** Negative and encoding the attribute:
  `option_command_id(id) = -1 - id`. No Matter or Zigbee command has a
  negative ID, and the cluster stays the real one, so the row is unique per
  endpoint, cluster and attribute. `LUMITECH` is `(-1, 0)`, negative
  cluster, and is not an option.
- **The option travels with the row** as JSON in the new column
  `command.option`, re-adopted on every `register_commands` like the slug.
  A value is therefore checked before it is queued, even with the radio
  away: an unsuitable value is 400 at `/cmd` and at
  `POST /api/commands/{key}`.
- **Slug** `set_<name>` for the output, `<name>` for the input signal of the
  same attribute, so the two read as a pair in Loxone (`set_keep_time`,
  `keep_time`).
- **Offered, never preselected.** A setting is changed once, not operated:
  the output and the input start unticked (`functional = false`) and the
  dialog has one checkbox for both.
- **Not in groups.** A group row carries no option to check a value
  against, and setting three sensors' keep time is three clicks.
- **Not in the control dialog**, which operates a device; the option has
  its own dialog (section 6).

A rolled-back image sees the rows as outputs whose `/cmd` answers 400 - it
has no payload builder for them.

## 5. Writing to a Device That Sleeps

A battery sensor sleeps between detections. A write sent into that sleep
costs up to ~28 s and fails, and waiting for it would hold the web UI's
request and the Miniserver's `/cmd` for nothing. So a value is never written
from the request that set it:

1. `ZigbeeSource.send` sees a negative command ID, writes the raw value to
   `zigbee_pending_write` (one row per attribute; a newer value replaces an
   older one) and returns.
2. It installs the device's wake-up watcher (`configure.py`) and asks it
   for a pass at once - all a listening, mains-powered device needs.
3. The watcher writes what the table holds whenever the device is heard
   (`options.write_pending`). A device that does not answer keeps its rows
   for the next wake-up; one that refuses a value has it dropped with a
   warning, since it would refuse it forever. A row is deleted only if its
   value is still the one that was sent, so a value set meanwhile is not
   lost.
4. The same watcher reads values the bridge has never seen - the ZG-204Z
   never reports its keep time on its own - at most every 15 minutes per
   device, and right after a device joins. Tuya data points are not read;
   those devices report them when they wake.

The table survives a restart (`addresses_with_pending` covers it, so the
watcher is reinstalled), and removing a device drops its rows.

## 6. The Dialog

A device with at least one option gets "Configuration…" in its tile menu
(`DeviceOut.config_options`). `GET /api/devices/{id}/options` lists each
option with its title, kind, unit, range or choices, the value the device
last reported, the value still waiting for it, and its export state.

- An enum is a drop-down, a switch a checkbox, a number a field with "Set".
  A change goes to `POST /api/commands/{key}` at once.
- A waiting value shows as "Waiting for the device: 60 s"; the dialog asks
  again every 5 s while one is waiting.
- Names: `config.option.<translation_key>` (or the attribute name) in
  `strings.yaml` when there is one, else the source's English
  `fallback_name`, the way a device's model name is shown. A choice reads as
  its number and unit when it names one (`_30_seconds` - "30 s"), else
  through `config.choice.<name>`, else as its name without underscores.

## 7. Schema

Version 19: the nullable column `command.option` and the table
`zigbee_pending_write (address, endpoint, cluster_id, attribute_id, value)`.
Both additive. No backfill: `backfill_commands` registers every device's
options on the next start.

## 8. Not Verified on Hardware Yet

The write format for the ZG-204Z follows zigbee2mqtt's converter, and the
tests prove that exactly that goes on the wire (uint8, attribute 0xF001,
no manufacturer code). Whether the three sensors accept it, and whether
they answer the read of 0xF001, is open until it runs on the Pi.
