# Login instead of token box: password-protected access to the UI

Design, September 3, 2026. Supplements
[the main document](2026-09-01-matter-loxone-bridge-design.md), specifically
its sections 8 (WebUI) and 9 (hardening), and replaces the token entry
introduced in Phase 5, Task 8.

## 1. The problem

On first access, the UI demands an API token that the operator has
previously generated themselves (`openssl rand -hex 32`), entered into
`.env`, and then entered by hand once more in the browser. The reason for
this is structural, not cosmetic: the WebUI is a static page with no login
of its own. In `loxone/server.py`, `/` and `/static/*` deliberately hang
without `dependencies=api_guard`; only `/api/*` is protected. There is no
login, no session, no account — **the token is the only credential this
service knows**.

It follows that the server cannot "simply hand the token over
automatically." If it sat in the delivered `index.html` or behind a
bootstrap endpoint, anyone who opens `http://<host>:8080/` would get it —
and with it `GET /api/diagnostics/fabric-backup` too, i.e. the
irreplaceable access data of the Matter fabric (main document 4.1). The
token would then no longer be one.

The way out is not to distribute the token more conveniently, but to give
the browser a real credential of its own alongside it: a login with a
password, the kind one knows from any other self-hosted service.

## 2. What this design does not touch

- **`/cmd` and `/resync` remain reachable without any protection.** The
  Miniserver calls virtual outputs without a header and without a cookie;
  any check there would switch off the Loxone integration. Unchanged from
  Phase 5, Task 8.
- **The bearer token stays.** Going forward it is no longer the browser's
  path but that of scripts and `curl`. `LOXMATTER_API_TOKEN`,
  `--api-token`, `normalize_api_token`, and the WebSocket subprotocol path
  `bearer, <token>` remain fully intact on the server side.
- **No TLS.** The service continues to speak HTTP on port 8080. See 14.1.
- **No username.** One service, one operator, one password. A name field
  would be an input with no decision behind it.

## 3. Discarded alternatives

**Embedding the token in the page, or delivering it via a bootstrap
endpoint — discarded.** See 1: `/` is unauthenticated, so the token would
be too.

**One-time link `http://host:8080/?token=<token>`, which the page picks up
into `localStorage` and strips from the URL — discarded.** This works and
costs little code, but the token then sits in the browser history and
possibly the access log; and it still leaves a secret that the operator
must generate and transport themselves. A login solves the same problem
without a secret ever passing through a URL.

**Trusted networks (`--trusted-net 192.168.1.0/24`) from which `/api` is
reachable without proof — discarded.** This was agreed on for a while and
was then withdrawn in favor of the login. The reason against the network
exception: it makes every device on the same network an administrator,
including TVs, robot vacuums, and guest devices on the Wi-Fi. A login is
the stronger credential — and because it is stronger, it is also allowed to
unlock more (see 11).

**Password from an environment variable (`LOXMATTER_PASSWORD` or
`LOXMATTER_PASSWORD_HASH`) — discarded.** The goal is an installation that
can be set up headlessly and configured entirely through the UI. A
password that has to sit in a file before the first start is the opposite
of that.

## 4. The access model

`build_api_guard` in `loxone/server.py` will now decide based on two
credentials instead of one. Order per request:

1. **Valid session cookie** → through.
2. **Valid bearer token** (header or WebSocket subprotocol, both as
   before) → through.
3. **Neither** → 401.

**Point 3 no longer knows an exception, and that is the actual hardening
this design provides.** Today a service without a configured token runs
with fully open `/api` routes and only a warning in the log — anyone who
overlooks the warning is running an open bridge without noticing. Going
forward this state no longer exists: as long as no password is set,
**every** `/api` route responds with 401, and the UI can show nothing but
the setup screen. Setting a password thereby becomes a precondition of
operation, not a recommendation one can ignore.

This applies explicitly to **existing installations after the update**
too: a bridge that previously ran without a token no longer delivers any
device data after the update, until a password has been set. A configured
`LOXMATTER_API_TOKEN` still passes through unchanged via point 2 — so
scripts and automations do not break because of the update, not even
during the time before the password is set.

The guard applies unchanged to all five `/api` routers, including the
WebSocket route `/api/live`. The caller's peer address plays no role in
any of these decisions.

`cli._warn_if_missing_api_token` will now warn as long as **no password is
set**, and is renamed accordingly to `_warn_if_no_password`. A configured
token no longer silences the warning: it is the path for scripts, not a
substitute for initial setup.

## 5. First start: trust on first use

If no password is stored in the store, the UI shows a setup screen on
which the password is set — without any further proof. Whoever arrives
first does the setup.

**This applies to every installation without a password, including an
existing one after the update.** There is no special case for an already
configured `LOXMATTER_API_TOKEN`: one screen, one flow, the same rules.
Two states, two behaviors:

| Password | `POST /auth/setup` |
| --- | --- |
| not set | open (trust on first use) |
| set | 409, permanently |

**This is a deliberate trade-off, not an oversight.** For the fresh
install, it was decided on September 3, 2026 against three alternatives:
a setup code in the startup log, a 15-minute window after startup, and an
initial password via the CLI. The deciding factor was that setup should be
possible without looking at a log and without a shell on the host. For the
existing system, the same day decided against the variant of querying the
existing token once there — in favor of a single flow with no special case
in code and documentation.

The price paid for this is the same in both cases and must be stated
clearly: **between the start without a password and the setting of the
password, anyone who reaches the service can take it over.** The rightful
operator only learns of this when their own password is not accepted.
Anyone who sets up the bridge and only continues configuring it days later
leaves this window open for days.

**"Whoever reaches the service" here should be read more broadly than
"whoever is on the same network"** (addendum from September 3, 2026, from
the closing review). A foreign website whose name switches, after a short
TTL, to the bridge's LAN address — DNS rebinding — counts as the same
origin as far as the operator's browser is concerned. That eliminates both
the CORS preflight, which would otherwise block a foreign
`Content-Type: application/json`, and the effect of `SameSite=Strict`:
`POST /auth/setup` is reachable from the internet as soon as the operator
opens any page while their bridge is still running without a password.
Once the password is set, the effect of such an attack remains limited to
`/cmd` and `/resync`, which are deliberately open anyway.

A check on `Sec-Fetch-Site` (allowing only `same-origin` and `none`) on the
two `/auth` routes would establish exactly this boundary and would be a
few lines. It was **deliberately not** implemented on September 3, 2026:
the operator sets up the bridge immediately after rolling it out, so the
window is minutes long, and one more check on the single way in is one
more place where you can lock yourself out. Anyone who leaves the bridge
unconfigured for longer should decide this differently.

For the existing system this weighs more heavily than for the fresh
install, and that belongs here too: the installation was already secured,
the operator has no reason to expect a takeover window after an update,
and they may not notice the update until days later. Anyone who rolls out
this version should log in immediately afterward. That belongs **in the
release notes and in the README**, not only in this spec.

What limits the window, and what falls within this decision:

- As long as no password is set, all `/api` routes are locked
  (section 4). Anyone who reaches the service in this state but does
  *not* complete setup sees no device data, no diagnostics, and no
  fabric backup. Only the takeover itself is exposed, not the existing
  data.
- As long as no password is set, the service writes a clear warning line
  to the log on **every** start — not only the first.
- The setup screen states the situation openly: this bridge is currently
  takeable by anyone on the network, and setup should be completed now.
- Once a password is set, the setup path is closed **permanently**
  (`POST /auth/setup` responds with 409 from then on, see 8).
- An already configured token remains valid after setup; setting the
  password does not invalidate it.

## 6. Storage

The store (`model/store.py`) already has a versioned schema with
migrations (`_SCHEMA_VERSION`, `_migrate`) and lives in the persistent
volume under `LOXMATTER_STORE`. `_SCHEMA_VERSION` stands at 3 after
Phase 6; newly added is `_migrate_to_v4`, and `_SCHEMA_VERSION` moves to 4:

```sql
CREATE TABLE IF NOT EXISTS setting (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
  id         TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
```

`setting` initially carries exactly one key, `password_hash`. The table is
nonetheless laid out generically because the remaining configuration is
meant to take the same path later (14.2).

**Password hash: `hashlib.scrypt` from the standard library**, stored as
`scrypt$<n>$<r>$<p>$<salt-hex>$<hash-hex>` with n = 2^14, r = 8, p = 1,
16-byte salt, 32-byte output. The memory requirement of 128·n·r = 16 MiB
falls under the default of `hashlib.scrypt`, so `maxmem` need not be
touched. Chosen over Argon2 (`argon2-cffi`) and bcrypt (`passlib`) because
both would have meant a new runtime dependency for exactly one hash;
scrypt is sufficient for this purpose and already present. The format
carries its own parameters, so that a later change of the cost factors can
still verify old hashes.

Sessions live in the database and not in memory, because the service runs
with `restart: unless-stopped`: a restart must not log everyone out every
time. Expired rows are deleted along with every access to the table — no
background job for a table with a handful of rows.

## 7. The session

Cookie `loxmatter_session`, value from `secrets.token_hex(32)` — 32 bytes
of randomness, hexadecimal.
Attributes:

- **`HttpOnly`** — no script in the origin can get at the value. This
  removes the XSS trade-off that the `localStorage` comment in
  `web/app.js` has to carry today.
- **`SameSite=Strict`** — at the same time the CSRF protection: a foreign
  page cannot trigger a state-changing request within a logged-in session,
  because the browser does not send the cookie along at all for a foreign
  origin. A dedicated CSRF token is therefore not introduced. `Strict`
  instead of `Lax`, because there is no use case in which one links into
  this UI from a foreign page.
- **`Path=/`**, so that the WebSocket handshake on `/api/live` carries the
  cookie too.
- **Explicitly without `Secure`.** The service still speaks HTTP; with
  `Secure` the browser would discard the cookie and no one would get in.
  This line is deliberately set and must not be added later "for the sake
  of security" while 14.1 remains open.

Lifetime of 30 days, extended on a sliding basis with every successful
access.

## 8. New routes

All four hang **outside** the guard, next to `/health` — they must be
reachable while logged out.

| Route | Behavior |
| --- | --- |
| `GET /auth-info` | `{"password_set": bool, "authenticated": bool}`. Tells the UI whether to show setup, login, or the app. Not a secret: `password_set` can also be read off from how `POST /auth/login` responds (409 before initial setup, 401 afterward), and `authenticated` from whether `/api/devices` delivers data. **No longer** solely from `/api/devices` — since section 4, that route responds with 401 in both states (corrected on September 3, 2026). Practical consequence: a network scan finds not-yet-set-up bridges via `GET /auth-info` without leaving a trace in any error counter. |
| `POST /auth/setup` | Accepts the new password **as long as none is set** — without any further proof, even if a token is configured (5). If one is already set: 409, without exception. On success it immediately creates a session, so the operator does not have to type it in again right afterward. |
| `POST /auth/login` | Checks the password, and on success creates a session. |
| `POST /auth/logout` | Deletes the session row **server-side** and clears the cookie. A logout that only deletes the cookie lets a stolen identifier live on. |

**Lockout against brute-forcing.** A password is guessable, a 256-bit
token is not — without a brake, the new path would be weaker than the one
it replaces. After five failed attempts from the same peer address,
`POST /auth/login` is throttled for that address to one attempt per 30
seconds; a successful login resets the counter. The counter lives in
memory, not in the database: it is transient state that does not justify
a write access per failed attempt, and while a restart does clear it, an
attacker cannot trigger one. The password comparison runs in constant
time (`secrets.compare_digest` over the hashes).

None of the three `POST` routes' response bodies **ever** contain the
password, the hash, or parts of it, and under no circumstances do the
routes log the plaintext.

## 9. Emergency exit

New CLI command `loxmatter set-password`, which prompts for the password
without echoing it and writes the hash directly into the store — with
`--store-path` like the other commands. Without it, a headlessly set-up
installation with a forgotten password would be lost for good; the
operator already has access to the database file on the host anyway, and
the command merely turns that into a usable path. In doing so it deletes
all existing sessions: whoever resets the password does not want an old
session to keep running.

## 10. UI

`web/index.html` gets two screens placed in front of the actual app,
controlled by `GET /auth-info` at startup:

- **Setup** — enter the password twice, with the note from 5. No further
  field, regardless of whether a token is configured.
- **Login** — one field, one button, a comprehensible error message on a
  wrong password and while the lockout is active ("too many attempts,
  possible again in X seconds").

**The token box is removed with nothing replacing it.** It was the reason
for this design. This removes from `web/app.js`: `TOKEN_STORAGE_KEY`,
`readStoredToken`, `authHeaders`, the entire `localStorage` behavior along
with its XSS trade-off, `saveToken`/`clearToken`/`startTokenEdit`/
`cancelTokenEdit`, and the subprotocol construction
`new WebSocket(url, ["bearer", token])`. `fetch` now runs with
`credentials: "same-origin"`, and the WebSocket picks up the cookie on its
own during the handshake. On the server side, the subprotocol path for
scripts remains intact (see 2).

`UnauthorizedError` stays as its own error class, but its effect changes:
a 401 from a running session (expired, or logged out elsewhere) throws the
UI back to the login screen, instead of showing an error message that
points to a field that no longer exists.

## 11. Effect on the fabric backup

`GET /api/diagnostics/fabric-backup` today refuses delivery as long as no
token is configured (403, review fix "Fix 3" from 2026-09-03) — the route
is not meant to stand unprotected on the LAN, because behind it hangs the
takeover of the fabric.

**The state this lockout was directed against can no longer occur.** It
defended the case "service is running with no access mechanism at all, so
all `/api` routes are open" — section 4 abolishes exactly this case:
without a password, every `/api` route is locked, and anyone who gets
through has presented a cookie or a token. The parameter
`api_token_configured` and the 403 branch are therefore removed with
nothing replacing them.

This is deliberately a removal and not an oversight. An unreachable branch
whose detailed docstring describes a situation that no longer exists
misleads the next reader — they read a condition there that they rely on,
and that no longer checks anything. What carries the protection going
forward is the guard itself, and the fact that it hangs on **every** one
of the five routers is already checked by `tests/api/test_security.py`
router by router individually rather than via the shared prefix.

After login, the download is therefore free — no token, no extra step. An
exception that asks the operator for a second secret once more after a
successful login protects nothing that was not already protected.

**The credential guarding this route has, in the process, become weaker,
and that belongs here too** (addendum from September 3, 2026, from the
closing review). An earlier paragraph called the login the "stronger
credential" — that holds for its *availability* (there is now always one),
not for its *strength*. Previously, `GET /api/diagnostics/fabric-backup`
was guarded by a secret with 256 bits of entropy that could not be
guessed. Now it is guarded by a password of at least eight characters,
transmitted in plaintext over HTTP (14.1), slowed by a throttle of roughly
two attempts per minute — that is, about 2,900 attempts a day against the
single irrevocable state of this installation (main document 4.1). A
dictionary password falls to that within days.

Removing the 403 branch remains correct nonetheless: the state it stood
against no longer exists. And the minimum length deliberately stays at
eight characters as of September 3, 2026 — the alternative (twelve) was
considered and discarded. What follows from this belongs in the
documentation and not in a constant: **this bridge's password should be
random, not memorable.** Eight random characters support the arithmetic
above; eight chosen ones do not.

Unchanged is that `build_diagnostics_router` **never gets to see the
password, the hash, or the token**: what it does not know, it cannot
accidentally write into a response or into the log.

## 12. Testing

New file `tests/api/test_auth.py`:

- Hashing and verification: the correct password matches, a wrong one
  does not, two identical passwords produce different hashes because of
  the salt, a hash with foreign parameters in the prefix is still checked
  correctly.
- Sessions: creating, checking, sliding extension, expiry, deletion on
  logout, cleanup of expired rows.
- Lockout: five failed attempts, then throttling; a successful login
  resets it; a second peer address is not affected by the first one's
  lockout.
- Migration: a database at schema version 3 is raised to 4 without
  touching existing rows in `device`/`signal`/`command`.

`tests/api/test_security.py` grows to include:

- The session cookie gets through every one of the five `/api` router
  groups.
- The bearer token still gets through unchanged.
- Neither → 401, **without exception**. In particular the state "no
  password, no token": every one of the five router groups responds with
  401, not with data. This is the test that pins down the hardening from
  section 4 — until now, exactly this state was open.
- No password, but a configured token: `/api` remains reachable with the
  token. This is the existing-install case immediately after the update;
  it proves that scripts survive the update.
- `POST /auth/setup` with no password set → password set, even with a
  configured token and without sending it along (5).
- `POST /auth/setup` after a password is set → 409, even with a valid
  token.
- A token configured before setup remains valid after the password is
  set.
- The renamed startup warning `cli._warn_if_no_password`: warns with no
  password set — even with a configured token — and stays silent with
  one set.
- Logout makes the cookie worthless immediately (the same value
  afterward → 401).
- `/api/live` connects with the cookie and without a subprotocol.
- `/cmd` and `/resync` remain open in **every** one of these states.
- `GET /api/diagnostics/fabric-backup` after login without a token → 200.

## 13. Documentation

For existing installations, this change is a breaking one — a bridge that
previously ran without a token delivers nothing after the update until a
password has been set (4). No one should first notice this from a silent
service. What needs updating:

- **Release notes:** what happens, what to do (open the UI, set a
  password), and the note from 5 that this should be done immediately
  after rolling out, not deferred to later.
- **README:** a new section on hardening — login instead of token entry,
  setting the password on first access, `loxmatter set-password` as the
  emergency exit, and the advice from 14.1 on a password that is not used
  anywhere else.
- **`deploy/testhost/.env.example`:** `LOXMATTER_API_TOKEN` loses its role
  as access to the UI and keeps only the one for scripts. The long
  comment that today guides entering it into the UI becomes wrong and
  must be replaced.
- **`deploy/testhost/docker-compose.yml`:** the same comment on
  `LOXMATTER_API_TOKEN` and on the volume line for `/matter-data` —
  today it states that mounting and the token belong together. Going
  forward, the password carries that role.
- **Module docstrings** of `loxone/server.py` and `api/diagnostics.py`:
  both describe the token as the sole credential. Both explain, at
  length, states that no longer exist after 4 and 11.

## 14. Open points

**14.1 No TLS.** During login, the password goes over the network in
plaintext. This is not a regression — the `Authorization` header already
does that today — but a password gets reused by humans, whereas a
service-specific token does not. The documentation must therefore
explicitly advise a password that is not used anywhere else. TLS
(certificate, reverse proxy, `Secure` flag on the cookie) is its own
design. As soon as a reverse proxy is placed in front of it: `_client_id`
in `api/auth.py` reads the connection's peer address, and that would then
look the same for every caller — the proxy's address. Five failed
attempts by any caller would then lock out EVERY operator together, with
one request per 30 seconds, permanently. A Unix socket — the usual
companion of exactly this kind of reverse proxy — reaches the same
outcome by a shorter path: there, `request.client` is `None`, and
`_client_id` falls back to the fixed return value `"unknown"`, which all
callers then likewise share. This future design must therefore switch
`LoginThrottle` over to a trustworthily evaluated `X-Forwarded-For`, or
the throttling turns into a global lockout.

**14.2 Remaining configuration in the UI.** Miniserver address,
matter-server address, ports, and data directory continue to come from
`docker-compose.yml` and the CLI options. Bringing them into the UI —
along with the question of which values are changeable during operation
and which require a restart — is the actual path to a headlessly set-up
installation and gets its own spec. This design lays the groundwork for
it with `setting` and the setup screen.

**14.3 Multiple operators.** One password, no username, no roles. Should
this ever be needed, the schema can carry it (`setting` becomes a `user`
table), but it is not a goal today.
