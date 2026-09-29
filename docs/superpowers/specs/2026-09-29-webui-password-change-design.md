# Changing the password in the WebUI, and a way back for a forgotten one

Design, September 29, 2026. Supplements
[the login design](2026-09-03-webui-login-design.md), whose sections 4
(access model), 8 (routes), 9 (emergency exit), and 10 (UI) it builds on.

## 1. The problem

Once set, the WebUI password can only be changed from a shell:
`loxmatter set-password` inside the container (login design 9). That
command was built as an emergency exit, not as the everyday path — an
operator who simply wants a different password has to leave the UI, find
the host, and know the container invocation.

The opposite case has the opposite problem. An operator who has forgotten
the password stands in front of a login screen that says nothing about the
way out. The command exists; the screen does not name it. Today it appears
only in the 409 text of `POST /auth/setup` and in `docs/OPERATIONS.md`.

## 2. Decisions

Taken with Lucien on September 29, 2026:

- **No current password is required to change it.** Being logged in is the
  proof. The alternative — asking for the current password, run through
  the login throttle — was considered and discarded. What it would have
  protected against is stated in 7.
- **Existing sessions stay valid after a change.** The new password applies
  to future logins only. The alternative — signing out every other session
  as `set-password` does — was discarded.
- **"Forgot password?" shows a fixed command** that works from any
  directory: `docker exec -it loxmatter loxmatter set-password`. Showing
  `cd <host path> && docker compose exec …` with the real host path was
  discarded: the path would have to be delivered over a route that answers
  without a login, and the fixed command works without it.

## 3. Server: `PUT /api/auth/password`

A new router `build_password_router(store)` in `api/auth.py`, wired in
`loxone/server.build_app` **with** `dependencies=api_guard`, like every other
`/api` router. It does not sit next to the four `/auth` routes, which hang
outside the guard: this route must never be reachable while logged out, and
hanging it behind the guard means it needs no check of its own.

| Request | Response |
| --- | --- |
| Body `{"password": "<new>"}`, caller passes the guard, password set | 200 `{"status": "ok"}` |
| New password shorter than `MIN_PASSWORD_LENGTH` | 422, `api.auth.fail_password_too_short` — the same text as setup |
| No password set yet (only reachable with the bearer token) | 409, `api.auth.fail_no_password_set` |
| Neither session cookie nor token | 401 from the guard |

Details:

- **Both credentials may change the password** — the session cookie and the
  bearer token alike, because the guard does not tell them apart and this
  route does not either. The token is the stronger credential (login
  design 11), so this unlocks nothing it could not already reach.
- **The 409 keeps initial setup the only way to the first password.** With a
  token and no password, the guard lets the caller through; without this
  branch the route would become a second setup path that does not follow
  the rules of login design 5.
- **Hashing runs through `_PASSWORD_HASH_LIMITER`** via
  `anyio.to_thread.run_sync`, exactly like setup and login: scrypt blocks
  the event loop and uses 16 MiB per computation. The route is behind the
  guard, so the limiter protects memory and latency here, not a login
  boundary.
- **No `LoginThrottle`.** The route verifies no secret, so there is nothing
  to guess and nothing to count.
- **Storage: the existing `AuthStore.set_password_hash(value)`**, which
  overwrites the hash and leaves `session` untouched (decision 2). Until
  now only test fixtures called it; its docstring gains this route as its
  production caller. `reset_password`, which also deletes every session,
  remains the method of `set-password` and is not changed.
- As with the other auth routes, neither the response body nor the log
  ever contains the password or the hash.

## 4. UI: the "Password" card in Settings

A new card in the Settings view, after the language card:

- Two password fields, "New password" and "Repeat new password", and a
  "Change password" button.
- That the two fields match is checked in the browser only, as on the setup
  screen; the server receives one password. The minimum length is checked
  by the server, and its 422 text is shown as it comes.
- On success the fields are cleared and a short confirmation appears. On a
  401 the existing `UnauthorizedError` handling returns the UI to the login
  screen, as for every other call.

## 5. UI: "Forgot password?" on the login screen

A link below the login button that reveals a panel, without any request to
the server:

- The command `docker exec -it loxmatter loxmatter set-password`, with a
  copy button. It works from any directory because the reference
  deployment gives the container the fixed name `container_name: loxmatter`
  (`deploy/testhost/docker-compose.yml`). `-it` is needed: `set-password`
  prompts for the password without echoing it and needs a terminal.
- Below it, for an installation from source: `uv run loxmatter set-password`,
  also with a copy button.
- One sentence saying that the command signs out every session.

**The copy buttons cannot rely on `navigator.clipboard` alone.** The
Clipboard API exists only in a secure context — HTTPS or `localhost` — and
this service speaks plain HTTP on the LAN (login design 14.1), so on
`http://<pi>:8080/` the property is `undefined`. The button therefore falls
back to selecting the text in a temporary `<textarea>` and
`document.execCommand("copy")`, and the command itself is rendered with
`user-select: all`, so that a single click selects it for copying by hand
if both fail.

The panel is static content in `index.html`, its text from `strings.yaml`.
It reveals nothing about this bridge: the command is the same for every
installation.

## 6. One command everywhere

Three places already name the recovery command, in the form
`docker compose exec loxmatter loxmatter set-password` — which only works
from inside the compose directory:

- `api.auth.fail_already_set_up` in `strings.yaml`,
- `cli.set_password.fail_db_not_found` in `strings.yaml`,
- `docs/OPERATIONS.md`, the section on the forgotten password.

All three switch to `docker exec -it loxmatter loxmatter set-password`, so
that the login screen, the error texts, and the documentation name the same
command. The comment above `build_auth_router`, which insists that README,
release note, and this text must not drift apart, stays true that way.

The same section of `docs/OPERATIONS.md` also gains one sentence saying
that a known password is changed in the WebUI under Settings, and that
`set-password` remains the path for a forgotten one.

## 7. What decision 1 costs

Whoever holds a valid session can now replace the password — someone at an
unlocked browser, or someone who got hold of a session cookie. The rightful
operator then no longer gets in through the login and must use the
emergency exit from 5. Because sessions survive the change (decision 2),
the old session also keeps running alongside the attacker's; neither side
is thrown out.

What bounds this: the session cookie is `HttpOnly` and `SameSite=Strict`
(login design 7), so a foreign page can neither read it nor trigger this
route in a logged-in session. What remains is physical or network access to
a logged-in browser, and that already reaches the fabric backup, which is
worth more than the password.

## 8. Testing

`tests/api/test_auth.py`:

- Change with a session cookie → 200; afterward the old password fails at
  `POST /auth/login` and the new one succeeds.
- Change with the bearer token → 200.
- The session used for the change, and a second one opened before it, both
  remain valid afterward.
- Too short → 422 with the translated text; the stored hash is unchanged.
- No password set, caller with token → 409; still no password set
  afterward.
- The response body contains neither the password nor the hash.
- `AuthStore.set_password_hash` overwrites the hash and leaves the
  `session` rows in place (`tests/model/test_auth_store.py`).

`tests/api/test_security.py`: the new router joins the router-by-router
check — without cookie and token, `PUT /api/auth/password` → 401.

The WebUI bindings (card, "Forgot password?" panel, copy buttons) are
checked in a browser against a throwaway harness, because the web tests
only prove that the files are delivered.
