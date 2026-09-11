# Device Source Boundary: Matter Behind an Interface Zigbee Can Share

Design, 11 September 2026. The first of two specs that add native Zigbee
devices to loxmatter. This one draws the boundary and changes nothing a
user can see, except one small thing: every device tile gets a badge saying
whether the device is on Thread or on IP. The second spec plugs Zigbee in
behind the boundary drawn here.

Rationale: loxmatter reads Matter devices generically and turns them into
Loxone inputs and outputs. The same generic reading fits Zigbee almost
unchanged, because Matter's data model descends from the Zigbee Cluster
Library. What stands in the way is not the model but the wiring: the
Matter client is called by name from a dozen places, and a device's
identity is a Matter node ID. Pulling that apart first, with Matter as the
only source, lets the refactor be proven against the existing test suite
and the real devices on the test Pi before any Zigbee code exists.

## 1. The Zigbee Programme, and What This Spec Takes From It

Decided in the brainstorming session that produced this document, and
binding for both specs:

| Decision | Choice | Consequence |
|---|---|---|
| Integration route | Native, not through a Matter bridge (DIRIGERA, Hue) | A bridge appears as one node with many endpoints; Zigbee devices are to be managed one by one, exactly like Matter devices |
| Zigbee stack | `zigpy`, in-process | No MQTT, no broker container, no second protocol to maintain |
| Device breadth | Vendor deviations included (Tuya, Aqara) | `zha-quirks` from the start |
| Coordinator for development | Silicon Labs EZSP (`bellows`) | Other zigpy radios work through configuration but are unproven |
| Installations | Matter always, Zigbee optionally in addition | matter-server stays mandatory; no Zigbee-only mode |
| Commissioning UI | Tabs "Matter" and "Zigbee" in the existing card | Commissioning is not part of the shared interface (section 3.2) |
| Technology on the tile | Small badge on the category icon | Built here for Thread and IP; Spec 2 adds Zigbee |
| Test hardware | A dimmable/colour lamp and sensors | Lamp commands, reporting and quirks provable on hardware; button events fixture-only |
| Split | Two specs: this boundary, then Zigbee | This spec merges to `main` on its own |

**Rejected, with reasons.** A dedicated Zigbee container with its own
WebSocket API (mirroring matter-server) would keep bridge updates from
restarting the coordinator, but means writing and maintaining a second
protocol on both ends and teaching the updater a second container. A Zigbee
package without a shared interface, branching on `device.technology` at
each call site, would give the smallest first diff and spread the branch
across roughly ten places that every later feature would have to remember.
zigbee2mqtt was rejected for requiring MQTT.

The known price of the in-process choice: an update from the web UI
restarts the coordinator, and the Zigbee network runs without one for about
a minute. Devices stay joined; the Loxone side already restores state after
a restart.

### 1.1 Research Findings Recorded for Spec 2

Measured or read on 11 September 2026, so Spec 2 does not start from
memory:

- **Versions and licences (PyPI).** `zigpy` 2.2.0 (GPL-3.0),
  `bellows` 1.0.1 (GPL-3.0), `zigpy-znp` 1.1.1 (GPL-3.0), `zha` 2.2.2
  (Apache-2.0), `zha-quirks` 2.2.2 (Apache-2.0). All compatible with
  GPL-3.0-or-later.
- **`zha-quirks` now depends on `zha`.** Quirks without the ZHA library are
  no longer possible. The coupling is narrow, though: `zhaquirks.setup()`
  fills a registry in `zha/quirks.py` (306 lines), and zigpy's
  `ControllerApplication` accepts a `device_resolver` — ZHA's own gateway
  passes `DEVICE_REGISTRY.resolve` there. The gateway and its entity model
  (about 26,000 lines) are not needed for quirks to apply. Untested.
- **`zha` pins every radio library to an exact version** (`bellows==1.0.1`,
  `zigpy==2.2.0`, …) and releases on Home Assistant's schedule. That is a
  dependency-update policy question for Spec 2.
- **zigpy does not configure devices to report.** Binding and attribute
  reporting are set up by ZHA's cluster configuration
  (`zha/zigbee/cluster_config.py`), driven by its entities. Without ZHA's
  entities, loxmatter has to bind and configure reporting itself.
- **Command and attribute IDs match Matter exactly** for everything
  `commands/translate.py` serves today, checked against zigpy 2.2.0:

  | Cluster | Command / attribute | Matter (loxmatter) | zigpy |
  |---|---|---|---|
  | OnOff `0x0006` | Off / On / Toggle | 0 / 1 / 2 | `0x00` / `0x01` / `0x02` |
  | LevelControl `0x0008` | MoveToLevel / MoveToLevelWithOnOff | 0 / 4 | `0x00` / `0x04` |
  | Color `0x0300` | MoveToHueAndSaturation / MoveToColorTemperature | 6 / 10 | `0x06` / `0x0A` |
  | Color `0x0300` | ColorTempPhysicalMin / Max | 16395 / 16396 | `0x400B` / `0x400C` |

  **Argument names differ**: `color_temp_mireds`, `transition_time`,
  `options_mask` in zigpy against the Matter SDK's `colorTemperatureMireds`,
  `transitionTime`, `optionsMask`. zigpy's `move_to_level_with_on_off` has
  no options fields at all.
- **Device type IDs conflict.** In the ZLL profile (`0xC05E`) that many IKEA
  and Hue lamps use, `0x0100` is a *dimmable* light; in Matter and in the
  ZHA profile (`0x0104`) `0x0100` is an on/off light. Device types cannot be
  passed through; they must be translated with the profile ID in hand.
- **Zigbee and Thread on one radio is not a foundation.** Home Assistant
  deprecated its Silicon Labs multiprotocol add-on in July 2025 and paused
  multi-PAN support as unstable. A second radio is required. Network
  coordinators (for example SMLIGHT SLZB-06M, EZSP over TCP) avoid a second
  USB device.
- **`install.sh` picks the first serial device** (`detect_radio_device`
  returns the first match of `/dev/ttyUSB*` or `/dev/ttyACM*`). With two
  sticks, boot order decides which one is taken as the Thread radio.

## 2. The Principle: Matter's Data Model Is loxmatter's Language

**Every source translates into the Matter data model at its own edge.**

The Zigbee source of Spec 2 delivers the same `NodeSnapshot` the Matter
client delivers today: attribute paths `endpoint/cluster/attribute`, device
types as Matter device type IDs, an `AcceptedCommandList` synthesised from
zigpy's cluster definitions. It receives the same call when a command goes
out, and renames the arguments.

As a result these stay **unchanged for both technologies**:
`matter/discovery.py`, `profiles/` (titles, units, scaling, functional
versus expert, categories, endpoint names), `commands/translate.py` apart
from the call type, the signal rows in the store, `loxone/runtime.py`, the
export, the control dialog and groups.

**The price** is that what Matter does not have must be put into Matter's
shape at the Zigbee edge: ZLL device types, Zigbee-only clusters such as
IAS Zone (`0x0500`) and Metering (`0x0702`, with multiplier and divisor).
They get profile table entries and names from zigpy rather than the Matter
SDK. All of that is Spec 2.

**The alternative**, a neutral data model of loxmatter's own, would be
semantically cleaner. It would also mean rewriting `discovery`, `profiles`
and `translate` and 38 test modules for a change no user would notice.

This is also why `NodeSnapshot` and `SignalRef` stay in
`matter/models.py` (section 3.3): a Zigbee module importing from there says
exactly what the design is.

## 3. The Interface

### 3.1 The Package `loxmatter/sources/`

The measure is what shared code calls on the Matter client today, not what
a source might conceivably offer.

```python
Technology = Literal["matter", "zigbee"]


@dataclass(frozen=True)
class DeviceCall:
    """Formerly `MatterCall`. `node_id` became `technology` + `address`."""

    technology: Technology
    address: str  # Matter: "23"; Zigbee: IEEE "00:12:4b:00:1c:a1:b2:c3"
    endpoint: int
    cluster_id: int
    command_id: int
    payload: dict[str, object] = field(default_factory=dict)


class DeviceSource(Protocol):
    technology: Technology

    @property
    def connected(self) -> bool: ...

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def wait_for_link_loss(self) -> None: ...
    async def snapshots(self) -> list[NodeSnapshot]: ...
    async def subscribe(
        self,
        resolve_device_id: Callable[[str], int | None],
        handler: RuntimeEventHandler,
    ) -> None: ...
    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None: ...
    async def send(self, call: DeviceCall) -> None: ...
    async def remove(self, address: str) -> None: ...


class SourceNotConfiguredError(LookupError):
    """A stored device belongs to a technology no running source serves."""


class Sources:
    """The only place that dispatches by technology."""

    def __init__(self, sources: Iterable[DeviceSource]) -> None: ...
    def get(self, technology: Technology) -> DeviceSource: ...  # raises SourceNotConfiguredError
    def all(self) -> list[DeviceSource]: ...
    def all_connected(self) -> bool: ...
    async def send(self, call: DeviceCall) -> None: ...  # this is the invoker
```

`RuntimeEventHandler` moves from `matter/client.py` to `sources/` with it;
it was already technology-neutral (`device_id`, `path`, `raw`).

### 3.2 Deliberately Not in the Interface

**Commissioning.** Matter takes a code and returns one device. Zigbee opens
the network for a while, and devices arrive later, possibly several. One
method for both would bend one of them into the shape of the other. Each
commissioning tab calls its source directly.

**Matter-only operations** stay on `BridgeMatterClient` and are reached
through it by name: `commission_with_code`, `set_thread_dataset`,
`thread_dataset_set`, and `snapshot(node_id)` for `loxmatter inspect` and
`loxmatter export --node`.

### 3.3 Three Settlements

1. **No adapter layer.** `BridgeMatterClient` satisfies `DeviceSource`
   itself. `send_command`, `remove_node` and `follow_node` become `send`,
   `remove` and `follow`. Internally the client keeps computing with integer
   node IDs and converts at exactly these methods and in the snapshots it
   hands out. One class per technology, nothing wrapped around it.
2. **`NodeSnapshot` stays in `matter/models.py`**, see section 2. Its
   `node_id: int` becomes `technology: Technology` and `address: str`.
   `NodeSnapshot.from_raw(node_id, raw)` keeps its signature as the Matter
   factory and fills both fields.
3. **The supervisor moves** from `matter/supervisor.py` to
   `sources/supervisor.py` and is typed on `DeviceSource`: one supervising
   loop per source. Its log lines name `source.technology` instead of
   matter-server.

## 4. Identity in the Store

### 4.1 Migration 9

`_SCHEMA_VERSION` goes from 8 to 9, following the existing pattern: one
transaction, idempotent for a freshly created database (which already has
the new columns from `_SCHEMA` and still runs the chain from
`user_version = 0`).

| Table | Change |
|---|---|
| `device` | **add** `technology TEXT NOT NULL DEFAULT 'matter'` |
| `device` | **add** `address TEXT NOT NULL DEFAULT ''`, filled from `CAST(node_id AS TEXT)` |
| `device` | **add** `network_features INTEGER`, the raw FeatureMap of NetworkCommissioning (`0/49/65532`), `NULL` at first |
| `device` | **keep** `node_id`, still written: `int(address)` for Matter, `0` for any other technology; never read by version-9 code |
| `command` | **keep** `node_id`, still written as a copy of the owning device's; `StoredCommand` gets `technology` and `address` through a join and has no `node_id` field |

**Why `node_id` stays.** This section first said to drop it. The task
review of the implementation (11 September 2026) found what that would
break: the web UI updater (`deploy/updater/update-once.sh`, rollback
section) rolls a failed update back to the previous image **without**
restoring the database, on the stated invariant that every migration only
adds columns, so the older version keeps running on the newer schema.
Version 8 reads `device.node_id` and writes `command.node_id`. Dropping the
columns would leave a rolled-back bridge unable to subscribe or list its
devices, while `/health` — which does not touch Matter — still answered.
Fixing the updater instead would not help existing installations: the
updater sidecar does not update itself. So migration 9 is additive, and
the price named above moves into Spec 2: a Zigbee row writes `0` into
`node_id`, which version-8 code would treat as an unreachable Matter node
— degraded, not dead.

**Rows written by version 8 after a rollback.** A device commissioned
while the older version runs against a version-9 database gets
`technology = 'matter'` by default but `address = ''`, and migration 9
never runs again when rolling forward. Every start of the store therefore
repairs such rows (`address = CAST(node_id AS TEXT) WHERE address = ''
AND technology = 'matter'`). Commands written by version 8 carry a
`node_id` the new code ignores; they reach their device through the join.

SQLite in `python:3.12-slim` and in the running Pi container is 3.46.1
(measured 11 September 2026); the migration needs nothing newer than
`ADD COLUMN`.

### 4.2 Store API

- `device_id_for_node(node_id)` becomes `device_id_for(technology, address)`.
- `register_commands(device_id, commands, node_id)` loses its third
  parameter.
- `backfill_device_types(snapshots)` matches snapshots by
  `(technology, address)`.
- **New** `backfill_network_features(snapshots)`, the same shape as
  `backfill_device_types`: fills only `NULL`, never overwrites, leaves a
  device missing from the snapshots (offline) alone, does not touch
  `updated_at`. Called in `attach()` next to `backfill_device_types`, where
  the snapshots are already loaded.
- `register_device` writes `technology` and `address` from the snapshot,
  and `network_features` from its attribute `0/49/65532` (endpoint 0,
  NetworkCommissioning, FeatureMap). Discovery never sees that value as a
  signal — FeatureMap is one of the global attributes `extract_signals`
  skips — so it is read here, the same way `device_types_by_endpoint` reads
  the Descriptor cluster.
- `unique_id` is unchanged for Matter, including its fallback
  `node:<address>`, so existing rows keep matching on re-registration. Spec 2
  uses `zigbee:<ieee>`.

`network_features` is stored raw for the reason `device_types` is: a rule
derived from it will improve, and an improved rule over a stored source is
a code change, not a migration. Section 5 shows that devices already bend
this value.

## 5. Transport: Thread or IP

### 5.1 The Evidence

Read from the live matter-server on the test Pi on 11 September 2026:

| Nodes | Devices | Actual link | `0/49/65532` |
|---|---|---|---|
| 4, 8, 11, 12, 13, 14, 15, 16, 21, 22 | IKEA BILRESA, GRILLPLATS, MYGGBETT, ALPSTUGA, MYGGSPRAY, KAJPLATS (×3), TIMMERFLOTTE, KLIPPBOK | Thread | `2` |
| 23 | Tasmota-Plug-4 | Wi-Fi | `4` (Ethernet bit) |
| 24 | Tasmota-Plug-6 | Wi-Fi | `5` (Wi-Fi and Ethernet bits) |

NetworkCommissioning's FeatureMap has bit 0 for Wi-Fi, bit 1 for Thread,
bit 2 for Ethernet. Both Tasmota plugs are on Wi-Fi and neither reports it
correctly. **Wi-Fi and Ethernet cannot be told apart reliably; Thread and IP
can**, and Thread versus IP is the distinction wanted.

### 5.2 The Rule

A new module `profiles/transport.py`, next to `categories.py` and for the
same reason: derived when read, from a stored source.

```python
Transport = Literal["thread", "ip", "zigbee"]

THREAD_BIT = 0x2
IP_BITS = 0x1 | 0x4  # Wi-Fi, Ethernet - not distinguishable in practice


def transport_for(technology: str, network_features: int | None) -> Transport | None:
    if technology == "zigbee":
        return "zigbee"
    if network_features is None:
        return None
    if network_features & THREAD_BIT:
        return "thread"
    if network_features & IP_BITS:
        return "ip"
    return None
```

Thread wins when a device reports both. That is a judgement, not a
measurement: no device reporting the Thread bit and an IP bit together has
been seen, but the Tasmota plugs show IP bits set without meaning, while no
false Thread bit has been observed. The docstring carries the table from
5.1, so the rule can be revisited when a counterexample turns up.

### 5.3 API and Tile

`DeviceOut` replaces `node_id` with `technology`, `address` and
`transport` (`"thread" | "ip" | "zigbee" | null`). The web UI never read
`node_id`.

The tile puts a small round badge on the bottom-right corner of
`.type-badge` (which gains `position: relative`), only when `transport` is
not `null`:

- Two new sprite symbols, `i-transport-thread` and `i-transport-ip`, drawn
  in the existing sprite's style (24 viewBox, stroke 1.8, `currentColor`).
  Spec 2 adds `i-transport-zigbee`.
- **No official logos.** "Matter" and "Zigbee" are trademarks of the
  Connectivity Standards Alliance; the badge uses neutral pictograms.
- `title` and `aria-label` from new i18n keys
  `web.devices.transport_thread` ("Matter over Thread") and
  `web.devices.transport_ip` ("Matter over IP"), each with `en` and `de`.

Chosen over two alternatives shown as mockups: an icon at the right edge of
the header competes with the offline pill and truncates long names earlier;
an icon with text in the footer sits in the line reserved for export state.
The category icon says *what* the device is, the badge on it *how* it is
connected.

## 6. Wiring

### 6.1 Startup

```python
sender = UdpSender(miniserver, port)
client = _build_client(url)
sources = Sources([client])  # Spec 2 appends the Zigbee source here, if configured
runtime = Runtime(store, sender, link_ok=sources.all_connected)
invoke = sources.send
```

`client.connect()` failing still ends startup, as today; matter-server is
mandatory. `attach` and `supervise` run once per entry of `sources.all()`.
Shutdown disconnects every source in its own `try`, the pattern `_run`
already uses for each resource.

### 6.2 Call Sites

| Place | Today | After |
|---|---|---|
| `build_app` | `client: BridgeMatterClient \| None` | `client` stays for Matter's own routes (commissioning, Thread, diagnostics, fabric backup); **new** `sources: Sources \| None` |
| `DELETE /api/devices/{id}` | `client.remove_node(device.node_id)` | `sources.get(device.technology).remove(device.address)` |
| Follow after commissioning | `client.follow_node(snapshot.node_id, …)` | `client.follow(snapshot.address, …)` — the route is Matter's anyway |
| `Invoker` in `loxone/server.py`, `api/control.py`, `commands/fanout.py` | `Callable[[MatterCall], Awaitable[None]]` | `Callable[[DeviceCall], Awaitable[None]]` |
| `commands/translate.py` | `to_matter_calls`, reads `command.node_id` | `to_device_calls`, reads `command.technology` and `command.address` |
| `Runtime.on_node_snapshot` | `store.device_id_for_node(snapshot.node_id)` | `store.device_id_for(snapshot.technology, snapshot.address)` |
| `supervisor.attach` | `client.subscribe(store.device_id_for_node, runtime)` | `source.subscribe(partial(store.device_id_for, source.technology), runtime)` |
| `loxmatter inspect`, `export --node` | Matter client | unchanged, Matter-only |

### 6.3 Error Behaviour

**A technology without a source.** `Sources.get` raises
`SourceNotConfiguredError` when the store holds a device of a technology no
source is running for. It cannot happen in this spec; it can in Spec 2, when
someone removes the Zigbee radio from the configuration. `/cmd` and device
removal then answer **503** with a new i18n detail
`api.errors.source_not_configured` ("{technology} is not set up in this
installation"), not 502. 502 means "the device did not answer", and that
would be false here.

**Everything else is unchanged.** A device that does not answer still
yields 502 with `api.errors.device_unreachable`. Heartbeat, offline state
and reconnect behave with a single source exactly as they do today.

## 7. Testing

### 7.1 The Existing Suite Is the Main Evidence

It must pass with **mechanical changes only**: the renames of `node_id`,
`MatterCall`, `to_matter_calls` and the three client methods, and the
changed signature of `register_commands`. `node_id` appears 245 times in 47
test modules. A test whose *expectation* has to change is a finding, and the
implementation plan names each one with its reason.

### 7.2 New Tests

Every new test that names a protection is shown to catch it: the fault is
introduced, the test is seen failing, the fault is reverted.

| Test | Protects | Fault introduced to prove it |
|---|---|---|
| Migration 8→9 on a version-8 database built in the test | `address` equals the old `node_id`; `node_id` still present in `device` **and** `command`; signal keys and command keys still resolve | Comment out the `address` backfill |
| Version-8 SQL on a version-9 database | The literal statements version 8 runs (lookup by `node_id`, inserts with `node_id`) still work | Write `0` into `node_id` for Matter too |
| A device inserted by version-8 code | Addressable by `(technology, address)` after reopening the store | Remove the startup repair |
| Migration on a fresh database | Idempotent: no "duplicate column", no "no such column" | Replace `_add_column_if_missing` with a bare `ALTER` |
| Migration rollback | A failure inside migration 9 leaves version 8 intact | Fail **after** the first write, not before — otherwise the test only exercises a precheck |
| `transport_for` | 2→thread, 4→ip, 5→ip, 1→ip, 3→thread, 0→`None`, `None`→`None`, zigbee→zigbee | Swap the Thread bit and the IP bits |
| `Sources.send` | The call reaches the source **of the call's technology** | With **two** fake sources: make `get` always return the first |
| `Sources.get` for an unknown technology | `SourceNotConfiguredError`, and 503 at `/cmd` | Map the error to 502 |
| Supervisor | Runs with a **non-Matter** fake source | Proves no Matter type leaks through `attach`/`supervise` |
| `backfill_network_features` | Fills only `NULL`; leaves set values and offline devices alone | Drop the `IS NULL` condition |
| `DeviceOut` | Carries `technology`, `address`, `transport`; no `node_id` | Leave `node_id` in the model |

### 7.3 Web UI

`tests/api/test_web.py` can prove only that markup is delivered. The badge's
behaviour is checked in a throwaway harness: the tile block cut out of
`index.html` by script (not retyped), `style.css` and the vendored
`vendor/alpine.min.js` beside it, served over http. In it: the badge appears
for `thread` and `ip` and is absent for `null`. Layout is checked on the
multi-column grid at the narrowest real tile width, **260 px**, not on one
wide tile.

### 7.4 Hardware on the Test Pi

With a separate instance, never touching the production one:

1. Migrate a **copy** of `/data/loxmatter.sqlite`. Every signal key and
   command key is identical before and after; every command resolves.
2. Switch and dim a KAJPLATS through that instance, then restore its prior
   state. Read values through the running instance's `signals` route, never
   through a fresh `snapshots()` call, which returns matter-server's cache.
3. Look at the device tab: ten tiles with a Thread badge, two with an IP
   badge.

### 7.5 Checks

The CI set from `docs/DEVELOPMENT.md`: `ruff check`, `ruff format --check`,
`mypy`, `pytest` (about eight minutes for the full run), and
`scripts/check_language.py`.

## 8. Explicitly Not Built Here

- No Zigbee dependency in `pyproject.toml`, no zigpy code.
- No change to `deploy/testhost/docker-compose.yml` or `install.sh`.
- No Zigbee commissioning tab; the commissioning card is untouched, including
  the "MATTER" label in its sticker illustration.
- No `i-transport-zigbee` symbol.
- No distinction between Wi-Fi and Ethernet (section 5.1).

## 9. Open Points for Spec 2

Recorded here so they are not rediscovered:

1. **Heartbeat semantics with two sources.** `all_connected` would silence
   the heartbeat when only the Zigbee radio is gone, and Loxone would report
   the whole bridge dead while Matter devices keep working. Options range
   from per-technology heartbeat keys to a heartbeat that means "Matter is
   up". Depends on how zigpy reports and recovers from a lost radio.
2. **Does zigpy reconnect by itself?** If it does, `wait_for_link_loss` and
   the supervisor loop may mean something different for Zigbee than
   "rebuild the connection".
3. **Binding and reporting** (section 1.1): which attributes, with which
   intervals, configured by loxmatter when a device joins.
4. **Translation at the edge:** ZLL and ZHA device types to Matter device
   types; names for Zigbee-only clusters from zigpy; profile entries and
   scaling for IAS Zone and Metering; `AcceptedCommandList` synthesis.
5. **Events from buttons.** Zigbee remotes send commands to the coordinator
   rather than report attributes; how they become `SignalKind.EVENT` paths.
   Fixture-only for now.
6. **Commissioning tab details:** join window length, countdown, stopping
   early, several devices in one window, which room applies to each.
7. **Radio configuration and deployment:** a `devices:` entry for a missing
   radio fails the *whole* service at `docker compose up` (the `otbr`
   service learned this, hence its profile) — for the `loxmatter` service
   itself that would take the bridge down with the stick. Network
   coordinators via `socket://`. Installer detection by
   `/dev/serial/by-id/` so two sticks cannot be confused.
8. **Coordinator backup** next to the Matter fabric backup in the System tab.
9. **Dependency policy** for `zha`'s exact pins and release pace.
