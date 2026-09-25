# The Miniserver's address is set in the web interface

Date: 25 September 2026. Extends "Device dashboard and export" (2026-09-03),
section 4, which moved the bridge's own address and ports into the `setting`
table, and leaves one address behind.

## 1. What is wrong

The card "Miniserver connection" under Settings holds three values: the
bridge's IP as the Miniserver sees it, the UDP port the Miniserver listens on,
and the HTTP port the bridge listens on. The one address the card is named
after is not among them. The Miniserver's IP only reaches the bridge as a
start argument:

```
install.sh asks  ->  .env MINISERVER_IP  ->  docker-compose.yml --miniserver  ->  cli.run  ->  UdpSender(miniserver, port)
```

Changing it therefore means editing `.env` over SSH and recreating the
container, which is exactly what "Updates only through the web UI" rules out
for every other setting.

A second defect sits in the same line. The sender's port is `--port`
(default 7000), not the UDP port saved in the card. That saved port only
reaches the export templates. Someone who changes it to 7001 gets templates
whose virtual inputs listen on 7001 while the bridge keeps sending to 7000:
the Miniserver never gets a value, and nothing reports an error.

## 2. Goal

- The Miniserver's IP is a field in the "Miniserver connection" card, saved
  in the `setting` table like the other three values.
- Saving it takes effect at once, without a restart. The Miniserver gets
  every current value again right afterwards.
- The bridge sends to the IP **and** UDP port saved in the card. The card is
  the one place both the sender and the export templates read from.
- Saving checks the address against the Miniserver and reports what it
  found. The address is saved whether or not a Miniserver answered.
- The installer no longer asks for the address.

## 3. Storage

`BridgeSettings` (`model/settings_store.py`) gains
`miniserver_ip: str | None`, stored under the new key `miniserver_ip`. It
is a new row in the generic `setting` table, so there is no schema change and
nothing for a rollback to undo (see the rule that store migrations stay
additive).

`BridgeSettingsStore.save` keeps its all-fields-in-one-transaction shape and
takes `miniserver_ip` as a fourth keyword. A separate
`seed_miniserver(ip, udp_port)` writes each of those two keys only where it
is not yet stored. It does not touch `saved_at`, because nobody saved
anything in the interface.

The port is seeded key by key, not together with the IP, because of the
defect in section 1: someone who saved 7001 in the card has a Loxone project
built from templates that say 7001. Overwriting that with `--port`'s 7000
would keep the bridge sending past it. Leaving it alone makes the sender
finally agree with the templates.

## 4. Start (`cli.run`)

`--miniserver` becomes optional (default `None`). An empty string counts as
not given: `docker-compose.yml` passes `${MINISERVER_IP}`, which is empty on
every installation made after this change.

At start, before the sender is built:

1. If `--miniserver` is given and no `miniserver_ip` is stored,
   `seed_miniserver(miniserver, port)` stores it, together with `--port`
   where no UDP port is stored yet, and an info line says so. This is how an existing installation carries its
   `.env` address over on the first start of the new version.
2. If `--miniserver` is given and differs from the stored value, a warning
   says the argument is ignored and names the stored value. It is the same
   shape as `cli.run.warn_zigbee_device_ignored`.
3. The sender is built from the stored `miniserver_ip` (possibly `None`) and
   the stored `udp_port`.

`--port` thereby changes meaning: it is only a starting value for the store,
as `--miniserver` is. Its help text says so.

## 5. The sender without a target

`UdpSender.__init__` accepts `host: str | None`. A new
`set_target(host: str | None, port: int)` replaces the target.

With no host, `send()` returns `False` and records nothing: neither in
`_last_sent` nor in the datagram log. Recording it in `_last_sent` would
suppress the same value once a target exists, and a light that does not
change would then never reach the Miniserver.

`target` returns `tuple[str, int] | None`.

## 6. Diagnostics

`_check_miniserver` (`api/diagnostics.py`) reports a failed check with the
new text `api.diagnostics.no_miniserver_address` when the sender has no
target. The text names the place to fix it, Settings → Miniserver
connection. It cannot link there: the diagnostics view renders every check's
detail as plain text (`x-text="check.detail"`), and one check with markup of
its own is not worth a second rendering path.

## 7. Saving (`PATCH /api/settings`)

`BridgeSettingsIn` gains `miniserver_ip: str | None`. The route:

1. Validates the Miniserver IP as an IPv4 address (`ipaddress.IPv4Address`).
   A bad value is a 422 with an i18n detail (`api.settings.invalid_ipv4`,
   naming the field), in the pattern of `api/devices.py`. An empty
   Miniserver IP, or `null`, is stored as `None`. A body **without** the
   field keeps the stored IP: a browser tab still running the previous
   version's `app.js` after an update saves the bridge's IP without knowing
   the new field, and must not erase the Miniserver's address by doing so.
   The bridge's IP keeps today's rule (not empty) and is not narrowed to
   IPv4 here; that would reject values existing installations saved.
2. Saves all four values.
3. Calls `sender.set_target(miniserver_ip, udp_port)`. If the target changed
   and is not `None`, it starts `runtime.resend_all()` as a background task,
   so the response does not wait for a rate-limited resend.
4. Probes the Miniserver (section 8) when an IP is set, and puts the result
   into the response.

`build_settings_router(store)` becomes
`build_settings_router(store, sender, runtime)`. `build_app` already holds
both. A test that builds the app without them gets a router that saves but
does not retarget, which is the case `sender is None` in diagnostics
already covers.

`BridgeSettingsOut` gains `miniserver_ip` and
`miniserver_check: MiniserverCheckOut | None`. `MiniserverCheckOut` carries
`found: bool`, `serial: str | None`, `firmware: str | None` and a translated
`message`. `GET` never probes, so it returns `miniserver_check = None`.

## 8. The probe

A new module, `loxone/probe.py`, with
`async def probe_miniserver(ip: str, timeout: float = 3.0) -> MiniserverProbe`.
It asks `http://<ip>/jdev/cfg/api`, which a Miniserver answers without
signing in. It uses `aiohttp`, which is already a dependency.

It parses the same shape as `install.sh`'s `check_miniserver`: the `value`
string of the answer contains `'snr': '…'` and `'version': '…'`. The fixture
`tests/fixtures/miniserver/jdev_cfg_api.json` is the reference for both.

The outcomes, each with its own i18n text:

| Outcome | `found` | Message |
|---|---|---|
| Answer with a serial or a version | yes | Miniserver found (serial …, firmware …) |
| Timeout | no | No Miniserver answers at … (timeout) |
| Connection refused / no route | no | No Miniserver answers at … (could not connect) |
| HTTP error status | no | No Miniserver answers at … (HTTP …) |
| Answer without either field | no | Something answers at …, but it is not a Miniserver |

The probe never raises: every failure becomes `found = False`. The bridge
sends to the address even when it was not found, because a Miniserver that
is switched off right now is a normal state.

## 9. Web interface

**Settings → Miniserver connection.**
- A new first field: "IP of the Miniserver" /
  "IP des Miniservers" (`web.settings.miniserver_ip_label`), with its own
  placeholder.
- The existing field keeps `web.bridge_ip_label`. The explanation
  (`web.settings.connection_explanation`) is rewritten so it describes both
  addresses. Today it warns against entering the Miniserver's IP into the
  bridge field; the new text says which address goes where, now that there
  is a correct place for the Miniserver's.
- After saving, the probe result shows under the save button: in green
  when found, as a warning otherwise.

**Export.** The read-only block next to the templates shows the Miniserver's
IP as well, so it lists every value the card holds.

**Project sync.** When the uploaded file configures more than one
Miniserver, no `miniserver_ip` was given, and the stored IP is among the
candidates, the endpoint uses the stored IP instead of asking. If the stored
IP is not among them, it asks as today. A file with a single Miniserver
behaves as before: `build_index` rejects a `miniserver_ip` that does not
match a single block, so the stored IP is only offered where the choice is
actually open.

## 10. Installer and deployment

- `install.sh` drops `decide_miniserver`, `check_miniserver`,
  `add_miniserver_finding`, the `MINISERVER_IP` question in the preview, and
  the "no terminal to ask on" failure. A variable passed in the environment
  (`MINISERVER_IP=… sh`) is still written into `.env`, so a scripted
  installation keeps working and seeds the store on first start. The closing
  summary tells the user to enter the Miniserver's address in the web
  interface under Settings → Miniserver connection.
- `docker-compose.yml` keeps `--miniserver ${MINISERVER_IP}`. It is how an
  existing installation's address reaches the store. It also keeps a
  rollback working: an older version still requires the argument, and the
  updater rolls back without restoring the database.
- `.env.example`: `MINISERVER_IP` is described as an optional starting value
  that the web interface overrides.
- README, `deploy/testhost/README.md` and CHANGELOG say where the address is
  set now.

## 11. What does not change

- `loxmatter export` and `fake-miniserver` keep their own options.
- The HTTP port (`listen_port`) still only feeds the templates. The bridge
  listens where uvicorn was started (`--listen`), and changing that at
  runtime would mean moving the server under its own feet. This spec does
  not change it.
- No new dependency.

## 12. Tests

- **Store:** the round trip of `miniserver_ip`. `seed_miniserver` writes into
  an empty store, does not overwrite a stored IP or a stored UDP port, and
  leaves `saved_at` alone.
- **CLI:** the seed on first start, the stored value winning over the
  argument (with the warning), an empty `--miniserver` counting as absent,
  and the sender built with the stored UDP port rather than `--port`.
- **Sender:** without a target nothing is sent and nothing is recorded.
  After `set_target` the next value goes to the new address. A value
  suppressed while there was no target goes out once a target exists.
- **Settings API:** saving retargets the sender and starts one full resend.
  An invalid IP is a 422 in both languages. The probe result appears in the
  response against a local HTTP server that answers with the fixture, and
  against a closed port.
- **Probe:** each row of the table in section 8.
- **Diagnostics:** no target produces the new failed check.
- **Project sync:** the stored IP resolves a file with several Miniservers.
  A stored IP not among them still asks. A single-Miniserver file ignores
  it.
- **Web:** `test_web.py` confirms the field is served. The Alpine binding
  (draft → PATCH → probe message) runs in a throwaway harness in the browser
  as well, because a test of the served HTML only proves delivery.
- **Installer:** the existing shell tests lose the question and gain a check
  that an environment `MINISERVER_IP` still lands in `.env`.
