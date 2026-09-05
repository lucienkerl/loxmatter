# Operations

[Back to the README](../README.md)

## Running the bridge

```bash
uv run loxmatter run --miniserver 192.168.1.10
```

Connects permanently to matter-server and to the Miniserver and starts an
HTTP service (default port 8080, `--listen`) that serves two things at the
same time:

- `/cmd` and `/resync` for the Miniserver (virtual outputs).
- `/` and `/api/*` for a browser interface: commission devices, view them,
  name them, switch them, export templates, diagnostics.

**The "System" view** shows three streams continuously, instead of only at
the push of a button: log lines, the UDP capture and the command log. The
log lines are the same ones
`docker logs` shows — only without shell access to the host, from level INFO
upwards. A click on "Pause" pauses the display without stopping the running
capture; "Hide heartbeat and full-resend" filters only the display, not what
arrives. Details:
[live feed design](superpowers/specs/2026-09-03-diagnose-livefeed-design.md).

## What a template contains

A device often delivers far more signals than anyone wants to have in
Loxone — a socket, for instance, more than a hundred of them, mostly Thread
radio counters, serial numbers and other administrative values. The export
therefore takes only the **functional** signals by default — the ones that
belong to the recognized device type (for a socket: on/off, voltage,
current, power, consumption). Everything else remains technically
exportable, but is not checked. In the WebUI's signal list these remaining
signals sit in the collapsed "Expert" section (with a count in its heading) —
each of them carries its own export checkbox and can be enabled there
individually, a Thread counter for troubleshooting, say. Rationale and
selection rule:
[signal selection design](superpowers/specs/2026-09-03-signalauswahl-design.md).

## Project file sync

This runs via `POST /api/export/project-sync`, in the WebUI at the top under
"Export" — by now the recommended route, ahead of the template files below
it that have to be imported one by one. Instead of importing templates one
at a time, an existing Loxone project file can be uploaded — the tool
reconciles it against the stored devices and returns a patched version for
download. Updates to virtual inputs/outputs that already exist, and new
signals within devices that already exist, are the default. **Completely new
device containers are experimental** and are only included via an explicit
checkbox in the WebUI: the ID scheme needed for new objects is derived from a
single real project file, is not officially documented and is **not
verified**. If the project has never had a virtual input or output of this
kind (no `VirtualInCaption`/`VirtualOutCaption` section), that same
experimental path now creates this section automatically along with it,
instead of locking the checkbox — no more manual preparation in Loxone
Config, but one more unverified object in the chain. Before trusting this
path for the first time: open a file patched this way in Loxone Config once
and check it for errors. Details:
[project file sync design](superpowers/specs/2026-09-03-projektdatei-sync-design.md).

## Updating when devices are already commissioned

**Take care when updating to this version if devices are already
commissioned.** The one-time migration of the database to this schema resets
the export checkbox of **every** already stored signal to the new default
value – even where it was flipped by hand beforehand. Anyone who, before
this update, deliberately enabled Thread counters or switched a signal off
loses that selection on the first start afterwards, without warning, and has
to set it again in the signal list. A template already imported into Loxone
is unaffected by this – the runtime path sends regardless of the checkbox
anyway; what is affected is only a **newly** generated template after the
update.

## Access control

The service binds to `0.0.0.0` by default (`--host`) so that the Miniserver
reaches it — the same reachability applies to the rest of the network.
**The `/api` routes therefore require a login.** On the first visit to
`http://<Host>:8080/` the interface shows an initial setup: pick a password,
done. After that you log in with this password, and the interface holds the
login in a session cookie (`loxmatter_session`, valid for 30 days, extended
on a sliding basis). **Until the password has been set, no `/api` route
delivers anything at all** — every request ends in 401, and the interface
shows nothing but the setup screen. That is a deliberate break with the
earlier behavior (a service without a token used to keep running openly,
with only a log warning): the open state no longer exists. The initial setup
demands no further proof — first come, first to set the password. That is a
deliberate trade-off (trust on first use), so that the service can be set up
without shell access to the host; the price is a window between the start of
the service and the first login in which anyone on the network can take over
the bridge — it should therefore last minutes, not days. A forgotten
password is reset in the reference deployment (see
[`deploy/testhost/`](../deploy/testhost/)) with `docker compose exec
loxmatter loxmatter set-password` **inside the running container**; for an
installation from source, correspondingly `uv run loxmatter set-password` on
the host. Both log out all open sessions in the process. **Important for a
containerized installation:** the database there typically lives in a named
Docker volume and is reachable via `LOXMATTER_STORE` only *inside* the
container — `set-password` on the host would hit a different, empty database
there and would falsely report success without unlocking the actual bridge;
since that finding, the command therefore aborts with a clear error instead
of creating a new database. Details and rationale:
[supplementary spec](superpowers/specs/2026-09-03-webui-login-design.md).

`/cmd` and `/resync` remain *always* untouched by this — the Miniserver
cannot send a header or a cookie, and that is a deliberate limit: whoever
reaches the port can still switch a device, but can no longer commission
one, remove one, or download the fabric backup. "Whoever reaches the port"
is to be understood more broadly than it sounds: `/cmd/{key}/{value}` is a
GET without an origin check, and any web page that someone on this network
opens in their browser can trigger it too
(`<img src="http://…/cmd/…">`) — a foot in the LAN is not needed for that.

**`LOXMATTER_API_TOKEN` still exists — but only for scripts and `curl`, no
longer for the browser.** Set via `--api-token` or the environment variable,
`build_api_guard` still accepts it as `Authorization: Bearer <token>` and,
for the WebSocket handshake of `/api/live`, as a subprotocol
(`Sec-WebSocket-Protocol: bearer, <token>`) — from which follows the same
requirement for the token as before: **no spaces, no comma, no non-ASCII**;
`openssl rand -hex 32` yields only `[0-9a-f]` and is the recommended way to
get one. A token that consists only of whitespace (an accidental line break
in a `.env`) counts as "not set". The browser interface itself no longer
sets an `Authorization` header and no longer puts a secret in
`localStorage` — the session cookie takes over that role. Existing
automation against `LOXMATTER_API_TOKEN` does not break with this update,
not even before the password has been set: the token path in the guard
exists independently of the password status.

**No TLS.** The service still speaks HTTP without encryption; both the token
and the password travel across the network in the clear on every
transmission. Use a password that is used nowhere else — and a **randomly
generated** one at that, not one you made up. Since the 403 branch was
dropped, the fabric backup lies behind the login too, and eight characters
carry its protection only as long as they cannot be guessed (see
[section 11 of the design](superpowers/specs/2026-09-03-webui-login-design.md)).

The fabric backup (`GET /api/diagnostics/fabric-backup`) is no longer an
exception today — it used to be one: without a configured token, this one
route answered with 403, while all the other `/api` routes stayed open
without a token. That special case is gone, because the rule it was an
exception to has itself gone away: **all** `/api` routes — the fabric backup
included — now equally require a valid session or a valid token, otherwise
401.

A working example is in
[`deploy/testhost/docker-compose.yml`](../deploy/testhost/docker-compose.yml);
`deploy/testhost/README.md` lists `LOXMATTER_API_TOKEN` among the variables
that can optionally be set during setup.

## Language

The interface is in English by default, but can be switched to German at any
time — the same, shared setting applies to the CLI, the WebUI and the texts
in newly generated export templates alike:

- **In the web interface:** the "Settings" tab → two buttons, English/German;
  the page then reloads itself automatically.
- **Via the CLI:** `uv run loxmatter set-language de` (or `en`) — like
  `set-password` it requires an already existing database and otherwise
  aborts with a clear error; the same restriction applies accordingly for a
  containerized installation, see [Access control](#access-control) above.
- **For a single invocation, without changing the stored setting:** the
  environment variable `LOXMATTER_LANG` (e.g. `LOXMATTER_LANG=de uv run
  loxmatter run --miniserver 192.168.1.10`) — takes precedence over the
  stored setting, for this one process only.

**Careful:** a language change affects only **newly** generated export
templates — the same property as the update note on signal selection above.
A template already imported into Loxone Config stays unchanged, in the
language it was originally exported in.
