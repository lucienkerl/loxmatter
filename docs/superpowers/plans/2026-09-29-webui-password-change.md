# Changing the Password in the WebUI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A logged-in operator can change the WebUI password under Settings, and the login screen tells an operator who forgot it which command resets it.

**Architecture:** A new router `build_password_router` in `api/auth.py` with one route, `PUT /api/auth/password`, mounted behind the existing `api_guard`. It hashes through the existing `_PASSWORD_HASH_LIMITER` and stores through the existing `AuthStore.set_password_hash`, which leaves sessions alone. The WebUI gets a "Password" card in Settings and a `<details>` disclosure "Forgot password?" on the login screen with the recovery command and copy buttons. Every place that names the recovery command switches to `docker exec -it loxmatter loxmatter set-password`.

**Tech Stack:** Python 3 / FastAPI / pytest (async, `httpx2`), Alpine.js (vendored, no build step), `strings.yaml` i18n.

**Spec:** `docs/superpowers/specs/2026-09-29-webui-password-change-design.md`

## Global Constraints

- Everything in English — code, comments, test names, commit messages (Conventional Commits). German only as `de:` values in `src/loxmatter/i18n/strings.yaml` and as quoted data in tests.
- Every user-visible string goes into `strings.yaml` with an `en` and a `de` value. The German auth texts address the reader with "du" (see `web.auth.password_hint`); keep that.
- No current password is required to change it; existing sessions stay valid after a change.
- The recovery command is exactly `docker exec -it loxmatter loxmatter set-password`; for source installs exactly `uv run loxmatter set-password`.
- No response body and no log line ever contains the password or its hash.
- Do not touch `AuthStore.reset_password` or the `set-password` CLI behaviour.
- Tests run in the foreground. The full suite takes over ten minutes — never run it in one Bash call; run the files named in each task, and in Task 5 run the suite in two halves.
- Checks before the final commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.

All paths below are relative to the worktree root
`/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/webui-password-change-b94c87`.
Subagents must use that absolute root, not the main checkout.

---

### Task 1: `PUT /api/auth/password`

**Files:**
- Modify: `src/loxmatter/api/auth.py` (move `_require_length` to module level; add `build_password_router`)
- Modify: `src/loxmatter/loxone/server.py:136` (import) and `:538-546` (comment) and after `:589` (wiring)
- Modify: `src/loxmatter/model/auth_store.py:81-89` (docstring of `set_password_hash`)
- Test: `tests/api/test_auth.py`, `tests/api/test_security.py`, `tests/model/test_auth_store.py`

**Interfaces:**
- Consumes: `AuthStore.password_hash() -> str | None`, `AuthStore.set_password_hash(value: str) -> None`, `hash_password(password: str) -> str`, `_PASSWORD_HASH_LIMITER`, `PasswordIn`, `StatusOut` (all existing).
- Produces: `build_password_router(store: Store) -> APIRouter` exposing `PUT /api/auth/password`, body `{"password": str}`, answers `{"status": "ok"}` / 409 / 422 / 401 (guard). Task 3 calls this route from the browser.

- [ ] **Step 1: Write the store test**

Append to `tests/model/test_auth_store.py`:

```python
def test_set_password_hash_leaves_the_sessions_in_place(tmp_path):
    """The WebUI password change (design 2026-09-29, decision 2) relies on
    this: unlike `reset_password`, a changed password does not sign
    anybody out."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.set_password_hash_if_unset("alt")
        store.auth.create_session("s1", created_at=1, expires_at=10**10)
        store.auth.set_password_hash("neu")
        assert store.auth.password_hash() == "neu"
        assert store.auth.session_expires_at("s1") == 10**10
    finally:
        store.close()
```

- [ ] **Step 2: Write the API tests**

In `tests/api/test_auth.py`, add to the imports:

```python
from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from loxmatter.auth.sessions import open_session, session_is_valid
```

(replace the existing `from loxmatter.auth.passwords import ...` line; `open_session`/`session_is_valid` are new). Add a second fixture right after `auth_client`:

```python
@pytest.fixture
async def token_client(
    tmp_path: Path, no_invoke: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    """Like `auth_client`, but with the bearer token `"secret"` configured -
    the path scripts take, and the only way to reach an `/api` route while
    no password is set."""
    store = Store(tmp_path / "t.sqlite")
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime, api_token="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store
    store.close()
```

Append these tests at the end of the file:

```python
# ---------------------------------------------------------------------------
# PUT /api/auth/password - changing a known password (design 2026-09-29).
# ---------------------------------------------------------------------------

NEUES_PASSWORT = "ein-neues-passwort"


async def test_a_logged_in_client_changes_the_password(auth_client):
    client, store = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})

    response = await client.put("/api/auth/password", json={"password": NEUES_PASSWORT})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    stored = store.auth.password_hash()
    assert stored is not None
    assert verify_password(NEUES_PASSWORT, stored)
    assert not verify_password(PASSWORT, stored)


async def test_after_a_change_only_the_new_password_logs_in(auth_client):
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    await client.put("/api/auth/password", json={"password": NEUES_PASSWORT})
    await client.post("/auth/logout")

    assert (await client.post("/auth/login", json={"password": PASSWORT})).status_code == 401
    assert (await client.post("/auth/login", json={"password": NEUES_PASSWORT})).status_code == 200


async def test_a_change_leaves_every_session_valid(auth_client):
    """Decision 2 of the design: the new password applies to future logins
    only - neither the session that made the change nor one opened
    elsewhere before it is signed out."""
    client, store = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    own_session = client.cookies.get("loxmatter_session")
    other_session = open_session(store.auth)

    await client.put("/api/auth/password", json={"password": NEUES_PASSWORT})

    assert own_session is not None
    assert session_is_valid(store.auth, own_session)
    assert session_is_valid(store.auth, other_session)
    assert (await client.get("/api/devices")).status_code == 200


async def test_the_bearer_token_may_change_the_password(token_client):
    client, store = token_client
    store.auth.set_password_hash(hash_password(PASSWORT))

    response = await client.put(
        "/api/auth/password",
        json={"password": NEUES_PASSWORT},
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    stored = store.auth.password_hash()
    assert stored is not None
    assert verify_password(NEUES_PASSWORT, stored)


async def test_changing_needs_a_login(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    before = store.auth.password_hash()

    response = await client.put("/api/auth/password", json={"password": NEUES_PASSWORT})

    assert response.status_code == 401
    assert store.auth.password_hash() == before


async def test_changing_rejects_a_short_password(auth_client):
    client, store = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    before = store.auth.password_hash()

    response = await client.put("/api/auth/password", json={"password": "kurz"})

    assert response.status_code == 422
    assert response.json()["detail"] == (
        f"The password must be at least {MIN_PASSWORD_LENGTH} characters long."
    )
    assert store.auth.password_hash() == before


async def test_changing_rejects_a_short_password_in_german(auth_client):
    """German companion test to test_changing_rejects_a_short_password."""
    client, store = auth_client
    store.locale.set_language("de")
    await client.post("/auth/setup", json={"password": PASSWORT})

    response = await client.put("/api/auth/password", json={"password": "kurz"})

    assert response.status_code == 422
    assert response.json()["detail"] == (
        f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
    )


async def test_changing_is_no_second_way_to_the_first_password(token_client):
    """Without a password, the token still passes the guard - the route must
    not turn into a setup path that skips the rules of `/auth/setup`."""
    client, store = token_client

    response = await client.put(
        "/api/auth/password",
        json={"password": NEUES_PASSWORT},
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 409
    assert store.auth.password_hash() is None


async def test_a_password_change_never_echoes_the_password_or_its_hash(auth_client):
    client, store = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    responses = [
        await client.put("/api/auth/password", json={"password": "kurz"}),
        await client.put("/api/auth/password", json={"password": NEUES_PASSWORT}),
    ]
    stored = store.auth.password_hash()
    assert stored is not None
    for response in responses:
        assert NEUES_PASSWORT not in response.text
        assert "kurz" not in response.text
        assert stored not in response.text
```

In `tests/api/test_security.py`, add `"/api/auth/password"` coverage. Append after `test_without_a_password_every_api_route_is_closed`:

```python
async def test_the_password_change_route_is_behind_the_guard(secured_client):
    """`PUT /api/auth/password` sits next to the four open `/auth` routes by
    name only - it must never be reachable while logged out."""
    client, _app, _device_id, store = secured_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    before = store.auth.password_hash()

    response = await client.put("/api/auth/password", json={"password": "ein-neues-passwort"})

    assert response.status_code == 401
    assert store.auth.password_hash() == before
```

(`hash_password` is already imported in `test_security.py`; check with `grep -n "import hash_password\|hash_password" tests/api/test_security.py | head -2` and add `from loxmatter.auth.passwords import hash_password` if not.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_auth.py tests/api/test_security.py tests/model/test_auth_store.py -v -k "password or session" 2>&1 | tail -30`
Expected: the new API tests FAIL (404/405 from the missing route; `test_changing_needs_a_login` and `test_the_password_change_route_is_behind_the_guard` may already pass with 404≠401 — they must fail on `assert 404 == 401`). The store test PASSES already (it pins existing behaviour).

- [ ] **Step 4: Move `_require_length` to module level**

In `src/loxmatter/api/auth.py`, delete the nested `_require_length` inside `build_auth_router` and add it at module level, directly above the comment that precedes `build_auth_router` (the one starting `# The 409 text in \`api.auth.fail_already_set_up\``):

```python
def _require_length(password: str) -> None:
    """A dedicated check instead of `Field(min_length=...)` on the model:
    the message ends up in the UI and should be shown there in the
    configured language and say what to do - not as a pydantic error list.
    Shared by `/auth/setup` and `PUT /api/auth/password`."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=i18n.t("api.auth.fail_password_too_short", min_length=MIN_PASSWORD_LENGTH),
        )
```

The call `_require_length(body.password)` inside `setup` stays as it is.

- [ ] **Step 5: Add `build_password_router`**

At the end of `src/loxmatter/api/auth.py`:

```python
def build_password_router(store: Store) -> APIRouter:
    """`PUT /api/auth/password` - changing a known password from the WebUI
    (design "Changing the password in the WebUI", 2026-09-29).

    Unlike the four routes in `build_auth_router`, this one hangs BEHIND
    `build_api_guard` (`loxone.server.build_app` wires it with
    `dependencies=api_guard`): being logged in - or holding the bearer
    token - IS the proof, and the route has no check of its own. No current
    password is asked for (design decision 1), and no session is signed
    out (decision 2): `set_password_hash` overwrites the hash only, unlike
    `reset_password`, which stays the emergency exit's method.

    No `LoginThrottle`: nothing secret is verified here, so there is
    nothing to guess. The hashing still runs through
    `_PASSWORD_HASH_LIMITER`, for the same reason as in setup and login -
    scrypt blocks the event loop and takes 16 MiB per computation."""
    router = APIRouter(prefix="/api")

    @router.put("/auth/password")
    async def change_password(body: PasswordIn) -> StatusOut:
        _require_length(body.password)
        if store.auth.password_hash() is None:
            # Only reachable with the bearer token: without a password the
            # guard lets nobody else through. Initial setup stays the only
            # way to the FIRST password, with its own rules (login design 5).
            raise HTTPException(status_code=409, detail=i18n.t("api.auth.fail_no_password_set"))
        hashed = await anyio.to_thread.run_sync(
            hash_password, body.password, limiter=_PASSWORD_HASH_LIMITER
        )
        store.auth.set_password_hash(hashed)
        return StatusOut(status="ok")

    return router
```

Also update the module docstring's first line from "The four access routes" to mention the fifth: replace

```
"""The four access routes: `/auth-info`, `/auth/setup`, `/auth/login`,
`/auth/logout` (Spec 8).
```

with

```
"""The four access routes: `/auth-info`, `/auth/setup`, `/auth/login`,
`/auth/logout` (Spec 8) - plus `PUT /api/auth/password`, which is NOT one of
them: it lives in its own router behind the guard (`build_password_router`
at the end of this module).
```

- [ ] **Step 6: Wire the router in `server.py`**

`src/loxmatter/loxone/server.py:136`:

```python
from loxmatter.api.auth import build_auth_router, build_password_router
```

After `app.include_router(build_version_router(), dependencies=api_guard)` add:

```python
    app.include_router(build_password_router(store), dependencies=api_guard)
```

And bring the comment at `:538-546` up to date:

```python
    # `dependencies=api_guard` on each of the twelve `/api` routers (see
    # `build_api_guard` above; the eighth was `POST
    # /api/export/project-sync`, the ninth `build_language_router`, the
    # tenth `build_update_router`, the eleventh `build_groups_router`, the
    # twelfth `build_password_router`): this protects without exception
    # every route of these twelve routers, including the WebSocket routes
    # `/api/live` and `/api/diagnostics/live` - and explicitly NOT `/cmd`,
    # `/resync`, `/health`, `/` and `/static`, which are mounted further
    # below without `dependencies`.
```

- [ ] **Step 7: Update the `set_password_hash` docstring**

`src/loxmatter/model/auth_store.py`, replace the docstring of `set_password_hash` with:

```python
        """Sets the hash, overwriting any existing one, committing on its
        own - and leaving every session in place.

        The path of `PUT /api/auth/password` (WebUI password change, design
        2026-09-29), which deliberately signs nobody out. NOT the path for
        `loxmatter set-password` (see `reset_password` below, which folds
        this statement together with signing out every session into ONE
        transaction). Test code also uses it to preset a password in a
        fixture without touching any session."""
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_auth.py tests/api/test_security.py tests/model/test_auth_store.py tests/loxone/test_server.py -v 2>&1 | tail -15`
Expected: all PASS.

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/loxmatter/api/auth.py src/loxmatter/loxone/server.py src/loxmatter/model/auth_store.py tests/api/test_auth.py tests/api/test_security.py tests/model/test_auth_store.py
git commit -m "feat(auth): let a logged-in client change the password via PUT /api/auth/password

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: One recovery command everywhere

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml:208-210` (`cli.set_password.fail_db_not_found`), `:717-719` (`api.auth.fail_already_set_up`)
- Modify: `docs/OPERATIONS.md:243-248`
- Test: `tests/api/test_auth.py:88-116`, `tests/test_cli.py` (check)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: the command string `docker exec -it loxmatter loxmatter set-password`, which Task 4 shows on the login screen.

- [ ] **Step 1: Update the expected texts in the tests**

In `tests/api/test_auth.py`, `test_setup_is_closed_for_good_once_a_password_is_set`:

```python
    assert second.json()["detail"] == (
        "A password has already been set for this service – initial setup is "
        "therefore permanently complete. Forgot the password? In the reference "
        "deployment, `docker exec -it loxmatter loxmatter set-password` resets "
        "it; for a source install, `uv run loxmatter set-password`."
    )
```

and in `..._in_german`:

```python
    assert second.json()["detail"] == (
        "Für diesen Dienst ist bereits ein Passwort vergeben – die Ersteinrichtung "
        "ist damit dauerhaft abgeschlossen. Passwort vergessen? Im Referenz-"
        "Deployment setzt `docker exec -it loxmatter loxmatter set-password` "
        "es neu; bei einer Installation aus dem Quellcode `uv run loxmatter "
        "set-password`."
    )
```

Then check whether any test pins `fail_db_not_found`'s text:
Run: `grep -rn "docker compose exec\|fail_db_not_found" tests`
Expected: after the edit above, no `docker compose exec` left. If a CLI test asserts the full `fail_db_not_found` text, update its expected string the same way (`docker compose exec loxmatter` → `docker exec -it loxmatter`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/api/test_auth.py -v -k closed_for_good 2>&1 | tail -5`
Expected: 2 FAIL (text mismatch).

- [ ] **Step 3: Change the strings**

In `src/loxmatter/i18n/strings.yaml`, in both the `en:` and `de:` values of `api.auth.fail_already_set_up` and of `cli.set_password.fail_db_not_found`, replace `` `docker compose exec loxmatter loxmatter set-password` `` with `` `docker exec -it loxmatter loxmatter set-password` ``. Nothing else in those values changes.

- [ ] **Step 4: Update `docs/OPERATIONS.md`**

Replace the sentence at `:243-248`:

```
password is reset in the reference deployment (see
[`deploy/testhost/`](../deploy/testhost/)) with `docker compose exec
loxmatter loxmatter set-password` **inside the running container**; for an
installation from source, correspondingly `uv run loxmatter set-password` on
the host. Both log out all open sessions in the process.
```

with

```
password is reset in the reference deployment (see
[`deploy/testhost/`](../deploy/testhost/)) with `docker exec -it loxmatter
loxmatter set-password`, which runs **inside the running container** and
works from any directory; for an installation from source, correspondingly
`uv run loxmatter set-password` on the host. Both log out all open sessions
in the process. The login screen shows both commands under "Forgot
password?". A password you still know is changed in the WebUI instead,
under Settings → Password; that change leaves every open session signed
in.
```

(Adjust the preceding "A forgotten" line break only as needed to keep the paragraph intact; do not touch the rest of the paragraph.)

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/api/test_auth.py tests/test_cli.py tests/test_i18n.py -v 2>&1 | tail -8`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
uv run python scripts/check_language.py
git add src/loxmatter/i18n/strings.yaml docs/OPERATIONS.md tests/api/test_auth.py tests/test_cli.py
git commit -m "fix(auth): name a recovery command that works from any directory

docker compose exec only works inside the compose directory; the fixed
container name makes docker exec -it loxmatter work from anywhere, and the
login screen is about to show the same command.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The "Password" card in Settings

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (after the `web.settings.language_error` entry)
- Modify: `src/loxmatter/web/index.html` (new card after the language card, before the `resend_heading` card, around `:3058`)
- Modify: `src/loxmatter/web/app.js` (state next to `resendInterval*` around `:1229`; method after `saveResendInterval` around `:6571`)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `PUT /api/auth/password` from Task 1; `this.request(method, path, body)`, `this.showToast(text, isError)`, `t(key)` (existing); `web.auth.password_mismatch`, `web.auth.password_hint` (existing keys).
- Produces: component fields `newPasswordDraft`, `newPasswordRepeatDraft`, `passwordChangeBusy`, `passwordChangeError`; method `changePassword()`.

- [ ] **Step 1: Write the web tests**

In `tests/api/test_web.py`, `test_the_interface_offers_setup_and_login_instead_of_a_token_field`: the page now carries five password fields. Change

```python
    assert page.count('type="password"') == 3
```

to

```python
    # Two for setup, one for login, two for the password card in Settings.
    assert page.count('type="password"') == 5
```

and extend its docstring's "Checked here: three password fields" sentence to "five password fields (two for setup, one for login, two for changing it in Settings)".

Append:

```python
async def test_the_settings_view_offers_a_password_change(api):
    """Design 2026-09-29, section 4: two fields, one button, sent to the
    guarded route - and no field for the current password (decision 1)."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    script = (await client.get("/static/app.js")).text
    assert 'x-model="newPasswordDraft"' in page
    assert 'x-model="newPasswordRepeatDraft"' in page
    assert "changePassword()" in page
    assert "current-password" not in _label_around(page, 'x-model="newPasswordDraft"')
    assert '"PUT", "/api/auth/password"' in script


async def test_the_password_card_never_keeps_the_password_in_memory(api):
    """Like `submitPassword`: whatever the outcome, both drafts are cleared
    in a `finally`, so the password does not stay in the page."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async changePassword()")
    body = script[start : script.index("\n    },", start)]
    finally_part = body[body.index("finally") :]
    assert 'this.newPasswordDraft = ""' in finally_part
    assert 'this.newPasswordRepeatDraft = ""' in finally_part
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/api/test_web.py -v -k "password or setup_and_login" 2>&1 | tail -8`
Expected: 3 FAIL.

- [ ] **Step 3: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, directly after the `web.settings.language_error` entry:

```yaml
web.settings.password_heading:
  en: "Password"
  de: "Passwort"
web.settings.password_explanation:
  en: "Sets a new password for this WebUI. Browsers that are signed in right now stay signed in; the new password applies to the next login."
  de: "Vergibt ein neues Passwort für diese WebUI. Browser, die gerade angemeldet sind, bleiben angemeldet; das neue Passwort gilt ab der nächsten Anmeldung."
web.settings.password_new_label:
  en: "New password"
  de: "Neues Passwort"
web.settings.password_repeat_label:
  en: "Repeat new password"
  de: "Neues Passwort wiederholen"
web.settings.password_submit:
  en: "Change password"
  de: "Passwort ändern"
web.settings.password_changed_toast:
  en: "Password changed."
  de: "Passwort geändert."
```

- [ ] **Step 4: Add the card to `index.html`**

Directly after the closing `</div>` of the language card (the card whose `<h2>` is `web.settings.language_heading`) and before the card with `web.settings.resend_heading`:

```html
        <div class="card">
          <h2 x-text="t('web.settings.password_heading')"></h2>
          <p class="hint" x-text="t('web.settings.password_explanation')"></p>
          <!-- No field for the current password: being logged in is the proof
               (design 2026-09-29, decision 1). -->
          <div class="row">
            <label
              ><span x-text="t('web.settings.password_new_label')"></span>
              <input type="password" autocomplete="new-password" x-model="newPasswordDraft"
                     @keydown.enter="changePassword()" />
            </label>
            <label
              ><span x-text="t('web.settings.password_repeat_label')"></span>
              <input type="password" autocomplete="new-password" x-model="newPasswordRepeatDraft"
                     @keydown.enter="changePassword()" />
            </label>
            <button
              class="primary"
              @click="changePassword()"
              :disabled="passwordChangeBusy"
              x-text="t('web.settings.password_submit')"
            ></button>
          </div>
          <p class="hint" x-text="t('web.auth.password_hint')"></p>
          <p x-show="passwordChangeError" x-cloak class="banner danger" x-text="passwordChangeError"></p>
        </div>
```

- [ ] **Step 5: Add state and method to `app.js`**

After `resendIntervalError: null,` (around `:1232`):

```js

    // --- Password change (Settings) -------------------------------------
    // Separate from `passwordDraft`/`passwordRepeatDraft` of the setup and
    // login screens: those are cleared on every login attempt, and the two
    // forms must not share a half-typed value.
    newPasswordDraft: "",
    newPasswordRepeatDraft: "",
    passwordChangeBusy: false,
    passwordChangeError: null,
```

After `saveResendInterval()` (after its closing `},` around `:6571`):

```js

    /**
     * Sends a new password to `PUT /api/auth/password` (design 2026-09-29).
     * Only the match of the two fields is checked here; the minimum length
     * is the server's, and its 422 text is shown as it comes. Via
     * `this.request`, not `requestJson`: a 401 here means the session
     * expired, and that must lead back to the login screen.
     */
    async changePassword() {
      this.passwordChangeError = null;
      if (this.newPasswordDraft !== this.newPasswordRepeatDraft) {
        this.passwordChangeError = t("web.auth.password_mismatch");
        return;
      }
      this.passwordChangeBusy = true;
      try {
        await this.request("PUT", "/api/auth/password", { password: this.newPasswordDraft });
        this.showToast(t("web.settings.password_changed_toast"));
      } catch (error) {
        this.passwordChangeError = error.message;
      } finally {
        this.passwordChangeBusy = false;
        // In every case, as in `submitPassword`: a password does not stay in
        // the page's memory, not even after a failed attempt.
        this.newPasswordDraft = "";
        this.newPasswordRepeatDraft = "";
      }
    },
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/api/test_web.py tests/test_i18n.py -v 2>&1 | tail -8`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
uv run python scripts/check_language.py
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/index.html src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "feat(web): change the password under Settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: "Forgot password?" on the login screen

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (after `web.auth.password_mismatch`)
- Modify: `src/loxmatter/web/index.html` (login template, around `:241-254`)
- Modify: `src/loxmatter/web/app.js` (module-level `copyToClipboard` after `requestJson`; component fields/method next to the auth state around `:812-816`)
- Modify: `src/loxmatter/web/style.css` (after the `.auth-screen input` rule, around `:1244`)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: the command string from Task 2; `this.showToast(text, isError)`; CSS classes `.commission-disclosure` (chevron `<details>`) and `.disclosure-body` (existing).
- Produces: `copyToClipboard(text) -> Promise<boolean>` (module level), component fields `resetCommandDocker`, `resetCommandSource`, method `copyCommand(text)`.

- [ ] **Step 1: Write the web tests**

Append to `tests/api/test_web.py`:

```python
async def test_the_login_screen_names_the_recovery_commands(api):
    """Design 2026-09-29, section 5: the forgotten-password path is on the
    screen that needs it - the same command the 409 text and OPERATIONS.md
    name (section 6)."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    script = (await client.get("/static/app.js")).text
    login = page[page.index("web.auth.login_submit") :]
    login = login[: login.index("</template>")]
    assert "web.auth.forgot_summary" in login
    assert 'x-text="resetCommandDocker"' in login
    assert 'x-text="resetCommandSource"' in login
    assert 'resetCommandDocker: "docker exec -it loxmatter loxmatter set-password"' in script
    assert 'resetCommandSource: "uv run loxmatter set-password"' in script


async def test_the_copy_button_works_without_a_secure_context(api):
    """`navigator.clipboard` exists only over HTTPS or on localhost; this
    service speaks HTTP on the LAN. The fallback must be there."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async function copyToClipboard(")
    body = script[start : script.index("\n}\n", start)]
    assert "window.isSecureContext" in body
    assert 'document.execCommand("copy")' in body


async def test_the_recovery_command_can_be_selected_with_one_click(api):
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css[css.index(".command-copy code") :]
    rule = rule[: rule.index("}")]
    assert "user-select: all" in rule
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/api/test_web.py -v -k "recovery or copy_button" 2>&1 | tail -6`
Expected: 3 FAIL.

- [ ] **Step 3: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, directly after `web.auth.password_mismatch`:

```yaml
web.auth.forgot_summary:
  en: "Forgot password?"
  de: "Passwort vergessen?"
web.auth.forgot_explanation:
  en: "Run this on the host the bridge runs on. It asks for a new password and signs out every browser."
  de: "Führe das auf dem Rechner aus, auf dem die Brücke läuft. Es fragt nach einem neuen Passwort und meldet alle Browser ab."
web.auth.forgot_source_label:
  en: "Installed from source instead of Docker:"
  de: "Aus dem Quellcode statt mit Docker installiert:"
web.auth.copy:
  en: "Copy"
  de: "Kopieren"
web.auth.copied_toast:
  en: "Command copied."
  de: "Befehl kopiert."
web.auth.copy_failed_toast:
  en: "Could not copy – click the command to select it and copy it by hand."
  de: "Kopieren hat nicht geklappt – klick auf den Befehl, um ihn zu markieren, und kopiere ihn von Hand."
```

- [ ] **Step 4: Add the disclosure to the login screen**

In `src/loxmatter/web/index.html`, inside the login `<main class="auth-screen">` (the template with `!authenticated && passwordSet`), directly after the `web.auth.login_submit` button and before `</main>`:

```html
        <!-- A native <details>, no Alpine state: nothing is fetched, the
             commands are the same for every installation (design 2026-09-29,
             section 5). The classes are the ones of the commissioning
             disclosures, so the same gesture looks the same everywhere. -->
        <details class="commission-disclosure forgot-password">
          <summary x-text="t('web.auth.forgot_summary')"></summary>
          <div class="disclosure-body">
            <p class="hint" x-text="t('web.auth.forgot_explanation')"></p>
            <div class="command-copy">
              <code x-text="resetCommandDocker"></code>
              <button type="button" @click="copyCommand(resetCommandDocker)"
                      x-text="t('web.auth.copy')"></button>
            </div>
            <p class="hint" x-text="t('web.auth.forgot_source_label')"></p>
            <div class="command-copy">
              <code x-text="resetCommandSource"></code>
              <button type="button" @click="copyCommand(resetCommandSource)"
                      x-text="t('web.auth.copy')"></button>
            </div>
          </div>
        </details>
```

- [ ] **Step 5: Add `copyToClipboard` and the component pieces to `app.js`**

Module level, directly after the closing `}` of `async function requestJson(...)`:

```js

/**
 * Copies `text` to the clipboard and says whether that worked.
 *
 * `navigator.clipboard` exists only in a secure context - HTTPS or
 * `localhost` - and this service speaks plain HTTP on the LAN, so on
 * `http://<pi>:8080/` the property is `undefined`. The fallback is the old
 * way: a temporary, invisible `<textarea>`, selected, and
 * `document.execCommand("copy")`. Deprecated, but it is the only path a
 * page on plain HTTP has. If both fail, the caller says so, and the
 * command stays selectable with one click (`user-select: all`).
 */
async function copyToClipboard(text) {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Permission denied or similar - fall through to the old path.
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  return copied;
}
```

In the component, after `authError: null,` (around `:816`):

```js
    // The two recovery commands on the login screen ("Forgot password?").
    // Not in strings.yaml: they are commands, not prose, and the same in
    // every language. The same text as in `api.auth.fail_already_set_up`
    // and docs/OPERATIONS.md - keep the three in step.
    resetCommandDocker: "docker exec -it loxmatter loxmatter set-password",
    resetCommandSource: "uv run loxmatter set-password",
```

Directly after the `logout()` method:

```js

    async copyCommand(text) {
      const copied = await copyToClipboard(text);
      this.showToast(copied ? t("web.auth.copied_toast") : t("web.auth.copy_failed_toast"), !copied);
    },
```

- [ ] **Step 6: Add the styles**

In `src/loxmatter/web/style.css`, after the `.auth-screen input { ... }` rule:

```css

/* "Forgot password?" on the login screen. The command sits in a box of its
   own with the copy button beside it; `user-select: all` makes one click
   select all of it, the manual way out if copying fails (plain HTTP has
   no Clipboard API, see `copyToClipboard` in app.js). `overflow-wrap:
   anywhere` lets the long docker line break on a phone instead of pushing
   the button off the screen. */
.command-copy {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.command-copy code {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
  user-select: all;
  font-family: var(--mono);
  font-size: 0.9em;
  padding: 0.4rem 0.5rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
}

.command-copy button {
  flex: 0 0 auto;
}
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/api/test_web.py tests/test_i18n.py -v 2>&1 | tail -8`
Expected: all PASS (including `test_the_stylesheet_has_balanced_braces`).

- [ ] **Step 8: Commit**

```bash
uv run python scripts/check_language.py
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/index.html src/loxmatter/web/app.js src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "feat(web): show the reset command under \"Forgot password?\" on the login screen

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Browser verification and full checks

The web tests prove only that the markup is delivered. This task runs the real app in Chromium and reads the DOM.

**Files:**
- Create (scratchpad only, not committed): `<scratchpad>/verify_password_ui.py`

- [ ] **Step 1: Start the dev server**

Run in the background: `uv run python scripts/dev_web_server.py --demo --port 8431`
(`--demo` presets the password `loxmatter-demo`, `DEMO_PASSWORD` in the script.)

- [ ] **Step 2: Write and run the Playwright check**

```python
"""Throwaway check for the password card and the forgot-password disclosure."""

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8431"

# Simulates plain HTTP on the LAN: no Clipboard API. Records what
# execCommand("copy") would have copied.
NO_CLIPBOARD = """
Object.defineProperty(window, 'isSecureContext', { get: () => false });
Object.defineProperty(navigator, 'clipboard', { get: () => undefined });
window.__copied = [];
document.execCommand = (cmd) => {
  if (cmd === 'copy') { window.__copied.push(document.getSelection().toString()
    || document.activeElement.value); return true; }
  return false;
};
"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.add_init_script(NO_CLIPBOARD)
    page.goto(BASE)
    page.wait_for_selector("text=Log in")

    # 1. Disclosure is closed, opens, shows the command, copy falls back.
    assert not page.is_visible("text=docker exec -it loxmatter loxmatter set-password")
    page.click("summary:has-text('Forgot password?')")
    assert page.is_visible("text=docker exec -it loxmatter loxmatter set-password")
    page.click(".command-copy >> nth=0 >> button")
    copied = page.evaluate("window.__copied")
    assert copied == ["docker exec -it loxmatter loxmatter set-password"], copied
    assert page.is_visible("text=Command copied.")

    # 2. Log in, change the password, mismatch first.
    page.fill("input[autocomplete=current-password]", "loxmatter-demo")
    page.click("button:has-text('Log in')")
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("text=Change password")
    page.fill("input[x-model=newPasswordDraft]", "neues-passwort-1")
    page.fill("input[x-model=newPasswordRepeatDraft]", "neues-passwort-2")
    page.click("button:has-text('Change password')")
    assert page.is_visible("text=The two entries do not match.")

    page.fill("input[x-model=newPasswordDraft]", "kurz")
    page.fill("input[x-model=newPasswordRepeatDraft]", "kurz")
    page.click("button:has-text('Change password')")
    page.wait_for_selector("text=at least 8 characters")
    assert page.input_value("input[x-model=newPasswordDraft]") == ""

    page.fill("input[x-model=newPasswordDraft]", "neues-passwort-1")
    page.fill("input[x-model=newPasswordRepeatDraft]", "neues-passwort-1")
    page.click("button:has-text('Change password')")
    page.wait_for_selector("text=Password changed.")
    assert page.input_value("input[x-model=newPasswordDraft]") == ""

    # 3. Still signed in; the new password works after logout.
    page.reload()
    page.wait_for_selector("text=Change password")
    page.click("button.logout")
    page.wait_for_selector("text=Log in")
    page.fill("input[autocomplete=current-password]", "neues-passwort-1")
    page.click("button:has-text('Log in')")
    page.wait_for_selector("button.logout")

    # 4. Phone width: the command box must not push the page wider.
    page.click("button.logout")
    page.wait_for_selector("text=Log in")
    page.set_viewport_size({"width": 360, "height": 740})
    page.click("summary:has-text('Forgot password?')")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, overflow
    page.screenshot(path="login-forgot-360.png", full_page=True)
    browser.close()
print("all checks passed")
```

Run: `uv run --with playwright python <scratchpad>/verify_password_ui.py`
Expected: `all checks passed`. Look at `login-forgot-360.png` yourself. If a selector does not match, fix the selector, not the assertion — and if an assertion fails, it is a finding: go back to the task that owns it.

Stop the dev server afterward.

- [ ] **Step 3: Full checks**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_language.py
uv run pytest --collect-only -q 2>&1 | tail -3
uv run pytest tests/api tests/auth tests/model tests/loxone -q 2>&1 | tail -3
```

Then the rest of the suite in a separate call (the full suite does not fit one Bash timeout):

```bash
uv run pytest tests --ignore=tests/api --ignore=tests/auth --ignore=tests/model --ignore=tests/loxone -q 2>&1 | tail -3
```

Expected: everything green; `--collect-only` reports no errors.
