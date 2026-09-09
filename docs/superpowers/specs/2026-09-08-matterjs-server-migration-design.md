# Migration to matterjs-server: the client library and the server image

Design, 8 September 2026. Frees loxmatter from `python-matter-server`, which
has been archived since version 8.1.2, and moves it to its successor
`matterjs-server` (matter.js, TypeScript, Matter 1.6.0) together with its
Python client.

Trigger: research on Matter groups on the same day (see section 7).
It was meant to clarify how to implement native groups, but instead revealed
that the library this project stands on receives no more updates.

## 1. The problem

`python-matter-server` is **archived**. 8.1.2 is the last version that
will ever be released. Home Assistant switched to
[`matterjs-server`](https://github.com/matter-js/matterjs-server) —
a re-implementation based on matter.js that speaks the same
WebSocket API and is intended as a drop-in replacement.

loxmatter depends on the frozen software in two places:

- `pyproject.toml` requires `python-matter-server>=8.1.2` — from there comes the
  client library `matter_server.client`, which `matter/client.py` uses,
  and transitively with it the `chip` package, from which `matter/client.py` pulls
  the command classes.
- `deploy/testhost/docker-compose.yml` pulls
  `ghcr.io/home-assistant-libs/python-matter-server:stable`.

The `deploy/testhost/README.md` already knows about part of this (section "3.
matter-server image path"), but names
`ghcr.io/matter-js/python-matter-server` as the successor. That's only the **mirror of the old
repository** under the new organization, not the successor. Following
that hint lands back at 8.1.2.

Practical consequence: every future Matter capability — Matter 1.6, new clusters,
Groupcast — is unreachable for this project as long as it is stuck on 8.1.2.
And none of these capabilities will be backported.

## 2. What was verified

The successor delivers its own Python package under `python_client/`:
**`matter-python-client`** (PyPI, currently 1.4.0, Apache-2.0). It provides the
packages `matter_server*` **and** `chip*` — **the same module paths** that
loxmatter imports today. The library migration is thus a
dependency swap, not a code rebuild.

Every single touchpoint was verified against the source code of the new
package (status `main`, 8 September 2026), not against its documentation:

### 2.1 Imports

| Import location | Symbol | in new package |
| --- | --- | --- |
| `matter/client.py:249` | `matter_server.client.client.MatterClient` | present |
| `matter/client.py:426`, `cli.py:33` | `matter_server.client.exceptions.{CannotConnect, ConnectionClosed, NotConnected}` | present |
| `matter/client.py:555,612` | `matter_server.common.models.EventType` | present |
| `matter/client.py:518` | `chip.clusters.ClusterObjects` | present |
| `tests/matter/test_client.py:22` | `matter_server.common.models.MatterNodeEvent` | present |
| `tests/matter/test_client_commissioning.py:21` | `matter_server.common.errors.NodeCommissionFailed` | present |
| `tests/profiles/test_{categories,endpoints}.py` | `matter_server.client.models.device_types.ALL_TYPES` | present |

`EventType` carries all four values used by loxmatter (`NODE_ADDED`,
`NODE_UPDATED`, `NODE_REMOVED`, `NODE_EVENT`) plus `ATTRIBUTE_UPDATED`, and
additionally new ones that don't bother us.

### 2.2 Method signatures

> **Correction (final review, 8 September 2026).** This section first said
> "exactly six methods" and listed `start_listening`, `disconnect`,
> `get_nodes`, `subscribe_events`, `send_device_command`, `commission_with_code`.
> That was wrong: it's **eight methods and additionally one read
> property**. Overlooked were `remove_node`, `set_thread_operational_dataset`
> and access to `upstream.server_info`. The omission is not harmless —
> it hits exactly the one call whose payload changes (see
> below). A design that hides its own correction has less value as evidence;
> therefore the wrong version stands here and is not deleted.
> Commit `beee42f` repeats the number six in its message — that is
> history and remains.

`matter/client.py` calls eight methods of the upstream client and reads
additionally one property. `connect` does **not** belong here —
`BridgeMatterClient.connect()` instead starts `start_listening` as
a background task and waits for its readiness event, see
module docstring.

| Touchpoint | Call | in new package |
| --- | --- | --- |
| `matter/client.py:282` | `start_listening(ready)` | present |
| `matter/client.py:378` | `disconnect()` | present |
| `matter/client.py:413,676,777` | `get_nodes()` | present, still returns `MatterNode` |
| `matter/client.py:444` | `commission_with_code(code)` | present, see below |
| `matter/client.py:461` | `remove_node(node_id)` | present, identical |
| `matter/client.py:489` | `server_info` (read) | present as `property -> ServerInfoMessage \| None` |
| `matter/client.py:522` | `set_thread_operational_dataset(dataset)` | present, **payload changed**, see below |
| `matter/client.py:564` | `send_device_command(...)` | present, see below |
| `matter/client.py:604,667` | `subscribe_events(...)` | present, see below |

The line numbers are the status **after** the comment corrections of the
final review (finding B6). The numbers in section 2.1 are from before
and therefore are 15 resp. 33 lines smaller than today's — the
additions are docstring prose, not instructions.

Neither `remove_node` nor `set_thread_operational_dataset` nor
`upstream.server_info` are covered by the test suite — the tests consistently use
a mock — and neither by mypy, because `_upstream` is treated as `Any`.
For these three, the source code comparison below is the only
safeguard there is.

The three critical ones are character-identical:

- `subscribe_events(callback, event_filter, node_filter, attr_path_filter)` —
  identical signature, identical key matching via
  `f"{event}/{node}/{path}"` with wildcard. The entire rationale in the
  module docstring of `matter/client.py` (why one subscription per
  (node, path) pair is needed, because `data` at `ATTRIBUTE_UPDATED` is only the
  value) remains unchanged and valid.
- `send_device_command(node_id, endpoint_id, command, ...)` — identical,
  and still derives `command_name` from `command.__class__.__name__`.
- `commission_with_code(...) -> MatterNodeData` — still returns the
  **flat** `MatterNodeData`, not `MatterNode`. The trap that
  Phase 5 stumbled over (`node_data.attributes` vs. `node.node_data.
  attributes`) remains the same trap, and the code that avoids it
  remains correct.

`MatterNode` still carries `node_data` as an attribute, `get_nodes()` still returns
`MatterNode`. The difference between the two types, which
`matter/client.py` records in the docstring, persists unchanged.

#### `set_thread_operational_dataset` — the only call with changed payload

This is the place the first version of this section missed,
and at the same time the only place where something changes:

| | Signature | over the wire |
| --- | --- | --- |
| `python-matter-server` 8.1.2 | `set_thread_operational_dataset(dataset)` | `dataset` |
| `matter-python-client` 1.4.0 | `set_thread_operational_dataset(dataset, entry_id="default")` | `dataset`, **`id`** |

loxmatter doesn't provide `entry_id` (`matter/client.py:522`), so gets
`"default"` — and only for `entry_id != "default"` does the new client
require a higher schema version (`require_schema=12`, otherwise `None`).
The call thus doesn't fall under the schema check from section 2.5.

**Why it still works even against an old 8.1.2 server:** its
argument resolution runs with `strict=False`
(`matter_server/common/helpers/api.py:51,57`) and silently discards unknown
keys rather than rejecting the call. The extra `id`
arrives and is ignored.

**Why it still stands as a finding even though it's harmless:** the
entire safety of the split of this migration — swap the library first,
then the server image — rests on wire compatibility in both
directions. The design justified this split and didn't check
exactly the one call where it could have failed.
That it holds is now proven; before it was unproven and presented as proven.
The same fact stands in the docstring of
`BridgeMatterClient.set_thread_dataset`.

### 2.3 Command classes

`matter/client.py` builds commands via
`chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS[cluster_id][command_id]`
and calls the found class with the field names from `commands/translate.py`.
Both hold:

- `ALL_ACCEPTED_COMMANDS` exists and is filled via the same
  `__init_subclass__` mechanism — the explicit import of
  `chip.clusters.Objects` solely for its side effect therefore remains
  necessary and correct.
- The classes have the same names and carry the same fields:
  `MoveToLevelWithOnOff(level, transitionTime, optionsMask, optionsOverride)`,
  `MoveToColorTemperature(colorTemperatureMireds)`,
  `MoveToHueAndSaturation(hue, saturation)`.

`commands/translate.py` and `commands/color.py` remain untouched.

### 2.4 `attribute_subscriptions`

The successor's compatibility docs note the difference that
`MatterNode.attribute_subscriptions` on matter.js is **always empty**, because
the server subscribes to all attributes automatically.

For loxmatter it's inconsequential: a full-text search for `attribute_subscriptions`
across `src/` and `tests/` yields no hits. loxmatter never registers
attributes with the server, it only registers **callbacks** via
`subscribe_events` with `attr_path_filter`. The server subscribing to everything anyway
makes this route even more robust: no path can go missing
that `follow_node()` would have to catch up.

### 2.5 Schema version — the two changes are independent

The new client closes the connection if the server reports a lower
`schema_version` than the client carries, or demands a higher
`min_supported_schema_version`.

- new client: `SCHEMA_VERSION = 11`
- python-matter-server 8.1.2: `SCHEMA_VERSION = 11`,
  `MIN_SCHEMA_VERSION = 9`

Both conditions are met (11 >= 11 and 9 <= 11). **The new client
connects to the old server.** That's why this design
breaks into two separate commits, not one: after commit 1
the test host runs unchanged, even if commit 2 hasn't arrived there yet.

The methods used by loxmatter require no higher schema:
`commission_with_code` only requires `require_schema=12` if
`wifi_credentials_id`/`thread_dataset_id` are provided — loxmatter doesn't.

## 3. Commit 1 — the dependency

Affected files: `pyproject.toml`, `uv.lock`, `docs/LICENSING.md`,
`README.md`.

- `python-matter-server>=8.1.2` → `matter-python-client>=1.4.0`.
- The comment block in `pyproject.toml` for `chip` (line 90) names the package
  as "part of the CHIP SDK that python-matter-server uses for cluster commands"
  — the source changes, the statement must be updated:
  `chip` now comes from the same package as `matter_server` and is generated from
  matter.js models.
- `docs/LICENSING.md` line 20: the entry names `python-matter-server` and
  `chip SDK` as Apache-2.0. The successor is also Apache-2.0, the
  name changes.
- `README.md` line 250 links the old repository.

**No code changes in `src/`.** If `uv sync` shows otherwise, that's
a finding that refutes this design and should be reported, not
silently patched away.

**Verification:** `uv sync`, then the full test suite (`uv run pytest`, about
three minutes). It covers the imports directly and via the fake upstream in
`tests/matter/` also the path through `ALL_ACCEPTED_COMMANDS`. Thus
commit 1 is fully verified without hardware.

Additionally to the test suite: `uv run python -c "import chip.clusters.Objects;
from chip.clusters import ClusterObjects; print(len(ClusterObjects.
ALL_ACCEPTED_COMMANDS))"` — proves that the registration table in the new
package is actually filled and not just existing.

## 4. Commit 2 — server image and Compose

Affected files: `deploy/testhost/docker-compose.yml`,
`deploy/testhost/README.md`.

Four changes, all verified from the successor's documentation:

1. **Image** → `ghcr.io/matter-js/matterjs-server:stable`.

2. **`--paa-root-cert-dir` must be removed.** The successor's CLI docs
   list the option under "Deprecated Options (were used in Python Matter
   Server) … **not supported**" with the rationale "Handled internally by
   matter.js DCL client". `--storage-path /data` remains unchanged.

3. **BLE needs `NOBLE_BINDINGS=dbus`.** The new container runs
   **unprivileged**, and the standard Bluetooth backend (`NOBLE_BINDINGS=
   hci`) opens a raw HCI socket, for which the container user lacks
   permissions. The D-Bus path instead talks to the host's BlueZ daemon;
   the required `/run/dbus:ro` mount is already in the Compose.
   `--bluetooth-adapter ${BLUETOOTH_ADAPTER}` remains as an argument.

   The alternative that the docs mention — run container as root
   (`user: 0:0`) — is **not** chosen here: it lifts
   container isolation, and the service runs with
   `network_mode: host` anyway.

4. **`chown -R 1000:1000` on `deploy/testhost/data`** before first start.
   The directory has been written to so far by a container running as root;
   the new user (UID 1000) otherwise couldn't access it. Without
   this step the container won't start.

The Fabric backup path remains untouched: `GET /api/diagnostics/
fabric-backup` (`api/diagnostics.py:703`) zips the mounted directory
recursively via `rglob("*")` and reads no file format. A changed
storage format doesn't affect it.

Likewise untouched: the diagnostics endpoint `thread-credentials`
(`_check_thread_credentials`) reads no files, just the
in-memory flag `BridgeMatterClient.thread_dataset_set`.

The README section "3. matter-server image path" is corrected: not
`ghcr.io/matter-js/python-matter-server` (the mirror of the old repo),
but `ghcr.io/matter-js/matterjs-server`.

## 5. What commit 2 cannot prove

Two points remain open until the migration actually runs on the test Pi.
Both belong in the README as such, not as resolved.

### 5.1 The data migration is one-time and one-way

The first start of the new image migrates `./data` to its own format.
The docs promise to be able to continue using the same data directory and
the same arguments; a path back to the old image is not promised, however.

If the migration fails, the Fabric is lost and **every commissioned
device must be reset and re-paired** — for a sensor behind a cabinet door,
not a small thing (the same consideration already justifies the `otbr-state`
volume in the Compose).

Therefore: **before the first start, pull `GET /api/diagnostics/fabric-backup`
and store the archive outside the Pi.** That's exactly what this route is for.

### 5.2 Capitalization of command names

The successor's WebSocket docs show command names in camelCase
(`"command_name": "moveToLevelWithOnOff"`). The included Python client,
however, sends `command.__class__.__name__` and thus PascalCase
(`MoveToLevelWithOnOff`) — the same thing loxmatter sends today.

The server must therefore accept both spellings, otherwise its own
Python client would be broken. That's a conclusion, not a measurement: proof comes
only through a light that actually responds to a click on the Pi.

## 6. Not part of this design

- **Matter groups / Groupcast.** Deliberately deferred (section 7).
- **New capabilities of the successor.** `get_network_topology`, the
  ICD commands, OTA upload and Thread diagnostics are present and
  could loxmatter use later. Taking them here would mix swapping the foundation
  with expanding functionality — and thus undo exactly the separation
  that section 2.5 enables.
- **The `--fabricid` default.** Python set 1, matter.js chooses randomly.
  This only affects creating a **new** Fabric; the migrated one keeps its.
  Nothing to do here, and a precautionary `--fabricid 1` would
  be interference in existing state without cause.

## 7. Origin

Noticed while researching Matter groups on 8 September 2026. The
question was how to implement native group messaging (a multicast command for
multiple lights instead of N individual commands). Answer: not at all, currently —
neither `python-matter-server` nor `matterjs-server` expose groups over
their WebSocket API, and `send_device_command` checks the
node cache in both cases, so a group node ID (`0xFFFF_FFFF_FFFF_0000 | groupId`)
doesn't get through.

The group function therefore waits until `matterjs-server` offers it natively.
matter.js itself already brings the controller side
(`packages/protocol/src/groups/FabricGroups.ts`, `KeySets.ts`,
`GroupSession.ts`); only the WS layer is missing. Additionally, Matter
1.6 (17 June 2026) released the **Groupcast cluster**, which replaces Groups
(`0x0004`) and GroupKeyManagement (`0x003F`) for group management — in matter.js
still provisional and gated with hard throws.

This design is the prerequisite for that: as long as loxmatter is stuck on 8.1.2,
it can't get Groupcast when it comes.
