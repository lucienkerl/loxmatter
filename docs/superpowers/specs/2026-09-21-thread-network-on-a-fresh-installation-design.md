# A fresh installation forms its own Thread network

Date: 21 September 2026. Follows "Thread setup without handwork" (2026-09-14)
and "Radios in the Web UI" (2026-09-11).

## 1. What happened

On 21 September a second Raspberry Pi (`pi3-andi`) was installed from scratch
with `COMPOSE_PROFILES=thread` and a SONOFF Dongle Plus MG24. The radio worked
from the start — `ot-ctl rcp version` answered
`SL-OPENTHREAD/2.4.4.0_GitHub-7074a43e4; EFR32` — but Thread never came up,
and the `otbr` container was restarted about every ninety seconds:

- `ot-ctl state` answered `disabled`, `ot-ctl dataset active` answered
  `Error 23: NotFound`, `ot-ctl ifconfig` answered `down`. Nothing in the
  product had ever created a Thread network. On the first test Pi that had
  been done by hand in September and never again since.
- The watchdog (`scripts/otbr-watchdog.sh`, run every minute by the updater
  since 2026-09-14) found no `leader`/`router`/`child` state, cleared the pid
  file, restarted `otbr`, waited 60 s, logged "Still no Thread interface after
  60 s. Is the radio module stuck?", and repeated after its 90 s grace period.
  The restarts themselves produced the symptoms that looked like a broken stick:
  `Init() at spinel_driver.cpp:87: Failure` when the stick was cut off mid-boot,
  and "thread border agent already started; not starting" when the pid file
  could not be cleared.
- Four `ot-ctl` commands by hand (`dataset init new`, `dataset commit active`,
  `ifconfig up`, `thread start`) made the node `leader` within six seconds, and
  the watchdog logged "Thread network is back". The first Thread device was
  commissioned into that network the same evening.

A third problem sits next to these two. `verify_thread` in
`deploy/updater/radios-once.sh` accepts only `leader`, `router` or `child`. A
user who switches Thread on through the radios card of a fresh installation
therefore gets a verification that cannot succeed, a rollback, and Thread off
again — the situation "The radios card says when a rollback left Thread off"
(2026-09-14) describes, with no way out through the card.

## 2. Goal

- A fresh installation with a working radio stick reaches a formed Thread
  network without a single command, whether Thread was chosen during
  installation or switched on later on the radios card.
- A Thread network that devices already live in is never replaced by a new
  one. When the border router has lost its network while matter-server still
  has Thread devices, the bridge forms nothing and says so on the radios card.
- Neither the watchdog nor the radios card treats "no network configured yet"
  as a fault.

## 3. One rule: running but not configured

The border router is **running but not configured** when both hold:

- `ot-ctl state` answers `disabled`;
- `ot-ctl dataset active` answers `Error 23: NotFound`.

Through the REST API the bridge sees the same state as `GET /node/state` →
`"disabled"` and `GET /node/dataset/active` → `204 No Content` (ot-br-posix
`src/rest/rest_web_server.cpp` at the pinned commit, `GetDataset`).

Restarting the border router does not change this state; only a dataset does.
An agent that has a dataset and is still `disabled` or `detached` is a
different case and stays a fault.

### 3.1 The watchdog

`scripts/otbr-watchdog.sh`, after `thread_is_up` failed and before the age
check: if the agent is running but not configured, the script exits 0 without
restarting and without writing a line. The log keeps its rule of containing
incidents only, and this is not one — section 4 forms the network.

An `ot-ctl` that does not answer at all is not "not configured"; the
existing restart path handles it as before.

### 3.2 The radios card's verification

`verify_thread` in `deploy/updater/radios-once.sh` treats running but not
configured as success, the same as `leader`/`router`/`child`. The agent is
healthy; the network is the bridge's to form. A request that switches Thread on
is therefore no longer rolled back on a fresh installation.

Both changes ship in the updater image. An existing installation receives them
through the updater refresh that System → Version offers when that image
changes.

## 4. The bridge forms the network

A new module `src/loxmatter/matter/thread_network.py` owns one job: make sure a
Thread network exists when a border router is present, and hand it to
matter-server.

### 4.1 When it runs

As a background loop of `loxmatter run`, next to the existing runtime loops:
once at start and then every 60 s. After it has seen an active dataset *and*
handed it to matter-server, it stops (the guarded case in section 4.3 keeps it
running). It starts again only with the next bridge start. A border router that later loses its dataset is not something
this loop repairs (section 4.3).

### 4.2 One pass

1. `GET /node/dataset/active` on `$LOXMATTER_OTBR_URL` (default
   `http://127.0.0.1:8081`, the same base URL `matter/otbr.py` already uses).
   - Unreachable, or any answer other than 200/204: nothing to do in this
     pass. Thread may be off or `otbr` may be starting.
   - 200: a network exists. Once per bridge start the bridge hands it to
     matter-server, whatever matter-server reports - an older installation's
     credentials there would otherwise survive (they are kept in its data
     directory); afterwards only while matter-server reports none. Either
     way the dataset passes through `validated_dataset` before
     `set_thread_dataset`. The loop is done.
   - 204: continue.
2. `GET /node/state`. Anything other than `"disabled"` means the agent is
   busy (attaching, or already part of a network whose dataset read raced):
   nothing to do in this pass.
3. **The guard.** If matter-server has at least one node that is a Thread
   device, the bridge forms nothing (section 4.3). A node is a Thread device
   when its endpoint 0 serves the Thread Network Diagnostics cluster (`0x0035`)
   — the commissioned IKEA switch on `pi3-andi` lists 53 in its root
   `serverList` — OR when `profiles/transport.py`'s hardware-verified
   classifier reads a Thread bit from the mandatory Network Commissioning
   FeatureMap (`0/49/65532`). The OR matters because Thread Network
   Diagnostics is optional, and not every Thread device serves it. Wi-Fi and Ethernet nodes do not block: a user who ran
   without Thread and adds a stick later gets a network.

   Thread credentials that matter-server holds *without* any Thread node do not
   block either. They strand nothing and are replaced in step 5. `pi3-andi`
   was exactly this case: matter-server kept a dataset from an earlier
   installation in its `./data` directory and had no nodes.
4. `PUT /node/dataset/active` with the header `If-None-Match: *` and the JSON
   body `{}`. That an empty JSON object is accepted is read from the source,
   not yet measured; the plan's first hardware step confirms it on the Pi. The
   border router creates a new network with random values only
   if it still holds no active dataset, atomically within its main loop.
   - 201: created.
   - 412 (a dataset appeared in the meantime) or 409 (the agent left
     `disabled`): someone else was faster. Not an error; the next pass sees the
     network in step 1.
   - Anything else: logged at warning level, retried in the next pass.
5. `PUT /node/state` with the body `"enable"`, then `GET /node/dataset/active`
   again and hand the dataset to matter-server with `set_thread_dataset`.
6. One log line at info level: "formed Thread network {name} on channel
   {channel}". The channel comes from `thread_channel_from_dataset` in
   `matter/otbr.py`; the name from the Network Name TLV (type 3), read by a
   small helper next to it.

matter-server unreachable during step 3 or step 5 means no decision can be
made safely: the pass ends and the next one tries again. The bridge never
forms a network without having asked matter-server first.

### 4.3 The guarded case

When step 3 blocks, the bridge keeps the finding and forms nothing. The loop
keeps running, so a dataset restored by hand shows up in step 1 of a later pass
and the state turns to `formed`. The bridge does not try to restore anything
itself: the full dataset of the lost network sits only in
matter-server's storage and in the fabric backup, and restoring it into a border
router is a deliberate act with its own risks. The radios card says what
happened (section 5), and `deploy/testhost/README.md` gets a short section on
restoring a dataset with `ot-ctl dataset set active <hex>` from a fabric
backup.

## 5. The radios card shows the network

`GET /api/radios` gains a `thread_network` object, filled from the loop's last
pass:

| `state`     | Meaning                                                   |
|-------------|-----------------------------------------------------------|
| `unknown`   | no pass yet, or the border router was unreachable         |
| `formed`    | a network exists; `name` and `channel` are set            |
| `forming`   | step 4 or 5 is in progress                                |
| `missing`   | the guard blocked: Thread devices exist, the network does not |

The card shows, below the Thread select and only while Thread is on:

- `formed`: "Thread network {name}, channel {channel}".
- `forming`: "Creating the Thread network…".
- `missing`: "The border router has no Thread network, but {count} Thread
  devices were commissioned into one. The bridge does not create a new network,
  because it would cut those devices off. Restore the network from a fabric
  backup." with a link to the README section.
- `unknown`: nothing.

Every text lives in `src/loxmatter/i18n/strings.yaml` with an `en` and a `de`
value.

## 6. Tests

- `tests/test_thread_network.py` (new), against a fake OTBR REST server and a
  fake matter client:
  - 204 + `disabled` + no Thread node: `PUT` with `If-None-Match: *` and `{}`,
    then `PUT /node/state` `"enable"`, then `set_thread_dataset` with the new
    dataset; the loop stops.
  - 200 with `thread_credentials_set: false`: only `set_thread_dataset`, no
    `PUT`.
  - 200 with `thread_credentials_set: true`: nothing is written.
  - 412 and 409 on the `PUT`: no error state, the next pass reads the network.
  - A Thread node present: no `PUT`, state `missing` with the count.
  - Only Wi-Fi nodes present: the network is formed.
  - OTBR unreachable, and matter-server unreachable during the guard: nothing
    is written, state `unknown`, the next pass tries again.
- `tests/test_otbr_watchdog.py`, with the existing fake `docker`: `disabled`
  plus `Error 23: NotFound` exits 0 with no restart and no log line;
  `disabled` with a dataset still restarts.
- `tests/test_updater_radios_script.py`: `verify_thread` succeeds for running but not
  configured, and still fails for an agent that does not answer.
- `tests/api/test_radios_api.py`: `thread_network` in `GET /api/radios` for each
  state.

## 7. On hardware

On `pi3-andi`, with a **new, empty** volume under a different name for `otbr`,
so that the network the IKEA switch lives in stays untouched, and only with
the owner's go-ahead:

1. With no Thread devices known to matter-server: the network forms within a
   minute of the bridge start, the watchdog log stays empty, the card shows
   name and channel.
2. With the switch known to matter-server: nothing is formed, the card shows
   `missing`.
3. Thread switched off and on through the radios card on the empty volume: no
   rollback.

Afterwards the original volume goes back in place and `ot-ctl state` answers
`leader` again.

## 8. Not part of this

- Choosing a network name or channel. The border router's defaults
  (`OpenThread-xxxx`, a random channel) stay.
- A button or form to restore a lost dataset.
- Better feedback during commissioning in the web UI. That is the next design,
  and it depends on this one only in that a Thread device can now be
  commissioned on a fresh installation at all.
