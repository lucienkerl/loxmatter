# UI updates, stage 1: identity and delivery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The bridge knows and shows which version it is, CI publishes finished images to GHCR, and a console update pulls an image instead of building on the Pi.

**Architecture:** The identity comes from the image environment (`ENV`, set from CI build arguments), not from the host checkout — which may have moved elsewhere in the meantime. A new module `loxmatter.version` reads it, `GET /api/version` returns it, the WebUI shows it in the System tab. The Compose file switches to `image:` with a tag controllable via `.env`; `build:` remains alongside as a fallback.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, Alpine.js (vendort), Docker Compose, GitHub Actions, `docker/build-push-action` (buildx, multi-arch), pytest, ruff, mypy.

**Basis:** [`docs/superpowers/specs/2026-09-08-webui-updates-design.md`](../specs/2026-09-08-webui-updates-design.md), sections 4, 5, 15. Stage 2 (sidecar, `/api/update/*`, the update card) has its own plan and assumes this one.

## Global Constraints

- **Every new source file starts with the GPL header** in English FSF formulation, word-for-word matching existing files (e.g. `src/loxmatter/api/settings.py:1-15`). This is the one intentional exception to German prose — it is a legal reference to `LICENSE`, not translatable text.
- **Developer prose in English:** docstrings, comments, commit messages. Dense and reasoned — *why* a decision was made, not just what the code does.
- **Every user-facing text goes through `i18n.t()`** with `en`- **and** `de`-entry in `src/loxmatter/i18n/strings.yaml`. No hardcoded German in `cli.py`, `api/*`, or the WebUI.
- **Registry and repository:** `ghcr.io/lucienkerl/loxmatter`, `github.com/lucienkerl/loxmatter`.
- **The four build arguments are exactly named** `LOXMATTER_VERSION`, `LOXMATTER_COMMIT`, `LOXMATTER_BUILT_AT`, `LOXMATTER_SCHEMA_VERSION`.
- **The Compose tag variable is exactly** `LOXMATTER_IMAGE_TAG`, default `stable`.
- **The full test suite takes about three minutes.** It looks like a hang, but it's not — don't cancel it.
- **Before every commit, these must pass:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.
- **Every test must fail once as a trial.** A test that names a structure instead of checking it has happened in this project. The step "Run test to verify it fails" is not a formality.

## File Structure

| File | Responsibility |
|---|---|
| `src/loxmatter/version.py` (new) | Reads build identity from environment. Only place that touches `os.environ` for this. |
| `src/loxmatter/model/store.py` (modify) | Adds `schema_version()` as public access to the previously private `_SCHEMA_VERSION`. |
| `src/loxmatter/api/version.py` (new) | `GET /api/version`. One router, one model, no logic. |
| `src/loxmatter/loxone/server.py` (modify) | Includes the new router, behind the same guard as all `/api` routes. |
| `src/loxmatter/web/index.html`, `app.js` (modify) | "Version" card in System tab. |
| `src/loxmatter/i18n/strings.yaml` (modify) | The new `web.system.version.*` keys. |
| `Dockerfile` (modify) | Four `ARG`/`ENV` pairs. |
| `.github/workflows/ci.yml` (modify) | New `image` job: build multi-arch and push to GHCR. |
| `deploy/testhost/docker-compose.yml` (modify) | `image:` alongside `build:`. |
| `scripts/update.sh` (modify) | `compose pull` instead of `compose build`. |
| `CHANGELOG.md` (new), `docs/DEVELOPMENT.md` (modify), `README.md` (modify) | What constitutes a release, and how to update. |
| `tests/test_version.py`, `tests/api/test_version_api.py`, `tests/test_build_arguments.py`, `tests/test_update_script.py` (new) | see respective task. |

---

### Task 1: `loxmatter.version` — identity from environment

**Files:**
- Create: `src/loxmatter/version.py`
- Modify: `src/loxmatter/model/store.py` (new public function `schema_version()`, directly under `_SCHEMA_VERSION = 7`)
- Test: `tests/test_version.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `loxmatter.version.BuildInfo` (frozen dataclass with `version: str`, `commit: str | None`, `built_at: str | None`, `schema_version: int`) and `loxmatter.version.build_info() -> BuildInfo`. `loxmatter.model.store.schema_version() -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_version.py` (with GPL header, copied word-for-word from `src/loxmatter/api/settings.py:1-15`):

```python
"""Tests for build identity — spec "Ship updates through the UI"
(2026-09-08), section 4.

The four cases below cover exactly the four ways these values could be wrong:
not set at all (dev checkout), set empty (Docker Compose interpolates a
missing .env variable to an empty string), set correctly, and — the
important one — the schema version, which cannot be faked from the
environment."""

from __future__ import annotations

from loxmatter.model import store as store_module
from loxmatter.version import build_info


def test_without_environment_bridge_reports_dev_status(monkeypatch):
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None
    assert info.built_at is None


def test_empty_variables_count_as_missing(monkeypatch):
    # Docker Compose interpolates a variable missing from .env to an empty
    # string, not "not set" — the same trap that caught LOXMATTER_API_TOKEN
    # once before (see Compose file).
    monkeypatch.setenv("LOXMATTER_VERSION", "")
    monkeypatch.setenv("LOXMATTER_COMMIT", "   ")
    monkeypatch.delenv("LOXMATTER_BUILT_AT", raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None


def test_set_variables_pass_unchanged(monkeypatch):
    monkeypatch.setenv("LOXMATTER_VERSION", "0.3.0")
    monkeypatch.setenv("LOXMATTER_COMMIT", "a3f91c2")
    monkeypatch.setenv("LOXMATTER_BUILT_AT", "2026-09-08T10:00:00Z")
    info = build_info()
    assert info.version == "0.3.0"
    assert info.commit == "a3f91c2"
    assert info.built_at == "2026-09-08T10:00:00Z"


def test_schema_version_cannot_be_faked_from_environment(monkeypatch):
    """The only value that does NOT come from the environment.

    In the image it appears as ENV too — but for the updater from stage 2,
    which reads it with `docker inspect` from an image not yet started.
    The running process knows it already, and a second source would be a
    source that claims something different at some point."""
    monkeypatch.setenv("LOXMATTER_SCHEMA_VERSION", "999")
    assert build_info().schema_version == store_module._SCHEMA_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_version.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Add `schema_version()` to the store**

In `src/loxmatter/model/store.py`, directly under the line `_SCHEMA_VERSION = 7`:

```python
def schema_version() -> int:
    """The schema version of this module, publicly readable.

    `_SCHEMA_VERSION` stays private: anyone changing it should see the long
    comment block above it, explaining every step. This function exports it
    so that `loxmatter.version` and CI don't have to access a private name —
    and so there is exactly one source for this number.
    """
    return _SCHEMA_VERSION
```

- [ ] **Step 4: Write the module**

`src/loxmatter/version.py` (GPL header, then):

```python
"""How the running version knows its identity — spec "Ship updates through
the UI" (2026-09-08), section 4.

The values come from the ENVIRONMENT, not from the host checkout. `Dockerfile`
lays them down as `ENV` during build, fed from build arguments that CI sets.
The reason for this direction: a checkout on the host may have moved
elsewhere, wandered on, or changed location without ever being shipped — the
image, by contrast, IS what is running.

Outside an image — in the dev checkout, where `uv run loxmatter` starts
directly — the variables are missing. That is not an error case, but the
normal case when developing: `version` is then "dev", `commit`/`built_at`
are None. If someone made an exception here, the bridge couldn't start
outside Docker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loxmatter.model.store import schema_version


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str | None
    built_at: str | None
    schema_version: int


def _clean(name: str) -> str | None:
    """Treat empty environment variables as missing.

    Docker Compose interpolates a variable missing from `.env` to an empty
    string, not "not set". This exact trap caught `LOXMATTER_API_TOKEN` once
    before (see the detailed explanation in
    deploy/testhost/docker-compose.yml); without this function, the version
    on a host with no value set would be "" instead of "dev".
    """
    value = os.environ.get(name, "").strip()
    return value or None


def build_info() -> BuildInfo:
    return BuildInfo(
        version=_clean("LOXMATTER_VERSION") or "dev",
        commit=_clean("LOXMATTER_COMMIT"),
        built_at=_clean("LOXMATTER_BUILT_AT"),
        # Deliberately NOT from the environment: see the docstring of
        # `test_schema_version_cannot_be_faked_from_environment`.
        schema_version=schema_version(),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_version.py -v`
Expected: 4 passed

- [ ] **Step 6: Prove the last test can fail**

Temporarily change `version.py` to `schema_version=int(os.environ.get("LOXMATTER_SCHEMA_VERSION", schema_version()))`, run `uv run pytest tests/test_version.py -v`.
Expected: `test_schema_version_cannot_be_faked_from_environment` FAILS (`999 != 7`). Then revert the change and run again: 4 passed.

- [ ] **Step 7: Lint, types, full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all green (full suite takes about three minutes)

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/version.py src/loxmatter/model/store.py tests/test_version.py
git commit -m "feat(version): read build identity from environment

The bridge couldn't say which version it was: pyproject.toml has been at
0.1.0 for 628 commits, and the git state lives in the checkout on the host,
which the running process doesn't know. But an update requires that 'before'
and 'after' are nameable.

The values come from the image itself, therefore. Empty values count as
missing — Docker Compose interpolates a variable missing from .env to an
empty string, the same trap that caught LOXMATTER_API_TOKEN once.

The schema version takes the other road deliberately and comes from
model.store, not from the environment: the running process knows it anyway,
and a second source would be one that claims something different at some
point. A test proves that a set LOXMATTER_SCHEMA_VERSION cannot change it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `GET /api/version`

**Files:**
- Create: `src/loxmatter/api/version.py`
- Modify: `src/loxmatter/loxone/server.py` (import and an `include_router` call next to `build_language_router`, around line 494)
- Test: `tests/api/test_version_api.py`

**Interfaces:**
- Consumes: `loxmatter.version.build_info()` from Task 1.
- Produces: `loxmatter.api.version.build_version_router() -> APIRouter` with prefix `/api`; response model `VersionOut(version: str, commit: str | None, built_at: str | None, schema_version: int)`. The WebUI in Task 4 reads exactly these four fields.

- [ ] **Step 1: Write the failing test**

`tests/api/test_version_api.py` (GPL header, then):

```python
"""Tests for GET /api/version.

The `api` fixture follows the same pattern as in `test_language.py`: a local,
already-authenticated fixture. `unauthenticated_api` proves that this route
is NOT one of the three intentional exceptions to the login requirement
(`/cmd`, `/resync`, `GET /api/i18n`)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store, schema_version


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[httpx.AsyncClient]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client
    store.close()


@pytest.fixture
async def unauthenticated_api(
    tmp_path, no_invoke, fake_runtime
) -> AsyncIterator[httpx.AsyncClient]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    store.close()


async def test_route_returns_four_values(api, monkeypatch):
    monkeypatch.setenv("LOXMATTER_VERSION", "0.3.0")
    monkeypatch.setenv("LOXMATTER_COMMIT", "a3f91c2")
    monkeypatch.setenv("LOXMATTER_BUILT_AT", "2026-09-08T10:00:00Z")
    response = await api.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {
        "version": "0.3.0",
        "commit": "a3f91c2",
        "built_at": "2026-09-08T10:00:00Z",
        "schema_version": schema_version(),
    }


async def test_responds_even_in_dev_checkout(api, monkeypatch):
    """No 500 if variables are missing — otherwise the UI would be unusable
    outside Docker."""
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    response = await api.get("/api/version")
    assert response.status_code == 200
    assert response.json()["version"] == "dev"
    assert response.json()["commit"] is None


async def test_no_access_without_session(unauthenticated_api):
    response = await unauthenticated_api.get("/api/version")
    assert response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_version_api.py -v`
Expected: FAIL — first two with `404`, because the route doesn't exist yet

- [ ] **Step 3: Write the router**

`src/loxmatter/api/version.py` (GPL header, then):

```python
"""Build identity through the API — spec "Ship updates through the UI"
(2026-09-08), section 4.

`build_version_router` builds an `APIRouter` with prefix `/api`, just like
`api.settings.build_settings_router` — included in `loxone.server.build_app`
behind the same `api_guard`.

Unlike `GET /api/i18n`, this route is NOT exempt from login requirement:
the login page doesn't need it to display. Anyone who wants to know the
version should be logged in — a version number is, for someone already in
the network, a useful hint about which known gaps this installation still
has.

No caching: `build_info()` reads `os.environ`, which is immutable in the
running process — but tests set the variables with `monkeypatch` per test,
and a cache would make exactly these tests depend on each other."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from loxmatter.version import build_info


class VersionOut(BaseModel):
    version: str
    commit: str | None
    built_at: str | None
    schema_version: int


def build_version_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/version")
    async def get_version() -> VersionOut:
        info = build_info()
        return VersionOut(
            version=info.version,
            commit=info.commit,
            built_at=info.built_at,
            schema_version=info.schema_version,
        )

    return router
```

- [ ] **Step 4: Wire it into the app**

In `src/loxmatter/loxone/server.py`, at the import block with the other routers:

```python
from loxmatter.api.version import build_version_router
```

And directly after the line `app.include_router(build_language_router(store), dependencies=api_guard)`:

```python
    app.include_router(build_version_router(), dependencies=api_guard)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_version_api.py -v`
Expected: 3 passed

- [ ] **Step 6: Prove the guard test can fail**

Temporarily remove `dependencies=api_guard` from the new `include_router` call, run `uv run pytest tests/api/test_version_api.py -v`.
Expected: `test_no_access_without_session` FAILS (`200 != 401`). Then restore it, run again: 3 passed.

- [ ] **Step 7: Lint, types, full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all green

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/api/version.py src/loxmatter/loxone/server.py tests/api/test_version_api.py
git commit -m "feat(api): GET /api/version

Returns the four values from Task 1, behind the same guard as every other
/api route. Deliberately NOT a fourth exception to the login requirement:
the login page doesn't need the version to display, and a version number is,
for someone in the same network, a useful hint about which known gaps this
installation still has.

No caching, even though os.environ is immutable in the running process: a
cache would make the tests depend on each other, and they set the variables
per test with monkeypatch.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `Dockerfile` — the four build arguments

**Files:**
- Modify: `Dockerfile` (after the `ENV PATH=` block, before `EXPOSE 8080`)

**Interfaces:**
- Consumes: nothing.
- Produces: four `ARG` declarations with exactly the names `LOXMATTER_VERSION`, `LOXMATTER_COMMIT`, `LOXMATTER_BUILT_AT`, `LOXMATTER_SCHEMA_VERSION`, each set as an equally-named `ENV`. Task 4 verifies that CI passes exactly these four, and reads `LOXMATTER_SCHEMA_VERSION` later (stage 2) via `docker inspect`.

- [ ] **Step 1: Add the arguments**

In the `Dockerfile`, directly before the line `EXPOSE 8080`:

```dockerfile
# Build identity (spec "Ship updates through the UI", 2026-09-08, section 4).
# Set by CI, read by `loxmatter/version.py` and — for LOXMATTER_SCHEMA_VERSION
# — by the updater from stage 2, which reads it with `docker inspect` from
# an image not yet started. That's exactly why it appears here as ENV and not
# just in code: `docker inspect` sees no Python constant.
#
# The defaults below make a manual build (`docker compose build`) possible
# without anyone needing to know four arguments — it produces an image that
# honestly identifies as "dev" instead of claiming a version it doesn't have.
ARG LOXMATTER_VERSION=dev
ARG LOXMATTER_COMMIT=""
ARG LOXMATTER_BUILT_AT=""
ARG LOXMATTER_SCHEMA_VERSION=""
ENV LOXMATTER_VERSION=${LOXMATTER_VERSION} \
    LOXMATTER_COMMIT=${LOXMATTER_COMMIT} \
    LOXMATTER_BUILT_AT=${LOXMATTER_BUILT_AT} \
    LOXMATTER_SCHEMA_VERSION=${LOXMATTER_SCHEMA_VERSION}
```

- [ ] **Step 2: Verify the file parses as a Dockerfile**

Run: `docker build --check . 2>&1 | tail -5` (if Docker is available; otherwise skip — Task 4 verifies consistency without Docker)
Expected: no errors for `ARG`/`ENV`

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "build: four build arguments for build identity

LOXMATTER_SCHEMA_VERSION intentionally appears as ENV in the image even
though the running process knows it from model.store: the updater from
stage 2 reads it with \`docker inspect\` from an image not yet started —
and docker inspect sees no Python constant.

Defaults for all four so a manual build works without argument knowledge,
producing an image that honestly identifies as 'dev'.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The WebUI shows the version

**Files:**
- Modify: `src/loxmatter/web/index.html` (new card as the **first** card in the `view === 'system'` section, before the "Resync all" card)
- Modify: `src/loxmatter/web/app.js` (`versionInfo` state, loading in `loadSystem()`)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_version_api.py` (an addition, see Step 1)

**Interfaces:**
- Consumes: `GET /api/version` from Task 2, `this.request(...)` and `t(...)` from `app.js`.
- Produces: Alpine state `versionInfo` (`{version, commit, built_at, schema_version}` or `null`), display in System tab. Stage 2 attaches its update card right at this spot.

- [ ] **Step 1: Write the failing test**

At the end of `tests/api/test_version_api.py`:

```python
async def test_ui_knows_all_version_card_strings():
    """A missing key only shows up in the browser later — as an empty field,
    not an error. This list is the connection between index.html and
    strings.yaml that no one else checks."""
    from loxmatter import i18n

    for key in (
        "web.system.version_heading",
        "web.system.version_running",
        "web.system.version_commit",
        "web.system.version_built_at",
        "web.system.version_dev_hint",
    ):
        assert i18n.raw_template(key)
        assert key in i18n.strings_with_prefix("web.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_version_api.py::test_ui_knows_all_version_card_strings -v`
Expected: FAIL — `KeyError: 'web.system.version_heading'`

- [ ] **Step 3: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, unmittelbar before `web.system.resync_heading:`:

```yaml
web.system.version_heading:
  en: "Version"
  de: "Version"
web.system.version_running:
  en: "Running: {version}"
  de: "Läuft: {version}"
web.system.version_commit:
  en: "Commit {commit}"
  de: "Commit {commit}"
web.system.version_built_at:
  en: "built {built_at}"
  de: "gebaut {built_at}"
web.system.version_dev_hint:
  en: "This bridge was built from a working copy, not from a published version."
  de: "Diese Brücke wurde aus einer Arbeitskopie gebaut, nicht aus einer veröffentlichten Version."
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_version_api.py -v`
Expected: 4 passed

- [ ] **Step 5: Add the state and the load call in `app.js`**

In the Alpine state object, right next to `systemChecks`:

```javascript
    // Build identity (GET /api/version). `null` as long as the System tab
    // hasn't been opened — the card shows nothing instead of "undefined".
    versionInfo: null,
```

In `loadSystem()`, within the `try` block, BEFORE the existing `this.systemChecks = ...`:

```javascript
        // Before the checks, not after: the version is the first card in
        // the tab, and it shouldn't appear only after the checks (which do
        // real network work) are done.
        this.versionInfo = await this.request("GET", "/api/version");
```

- [ ] **Step 6: Add the card in `index.html`**

As the first card inside `<section x-show="view === 'system'">`, before the existing card with `t('web.system.resync_heading')`:

```html
        <div class="card">
          <h2 x-text="t('web.system.version_heading')"></h2>
          <template x-if="versionInfo">
            <div>
              <p x-text="t('web.system.version_running', { version: versionInfo.version })"></p>
              <p class="hint">
                <span x-show="versionInfo.commit" x-cloak
                      x-text="t('web.system.version_commit', { commit: versionInfo.commit })"></span>
                <span x-show="versionInfo.built_at" x-cloak
                      x-text="t('web.system.version_built_at', { built_at: versionInfo.built_at })"></span>
              </p>
              <p class="hint" x-show="versionInfo.version === 'dev'" x-cloak
                 x-text="t('web.system.version_dev_hint')"></p>
            </div>
          </template>
        </div>
```

- [ ] **Step 7: Check the Alpine bindings in a throwaway harness**

A browser test only proves that files are delivered — the bindings must run. Start `uv run python scripts/dev_web_server.py`, open the System tab and verify:

- The "Version" card appears **at the top**, before "Resync all values".
- It shows `Running: dev` (in dev checkout) and below a note about the working copy.
- The browser console shows **no** Alpine errors and no `t()` warning about a missing key.

Then in the same session start `LOXMATTER_VERSION=0.3.0 LOXMATTER_COMMIT=a3f91c2 uv run python scripts/dev_web_server.py`:

- The card shows `Running: 0.3.0` and `Commit a3f91c2`, and the working-copy note is **gone**.

- [ ] **Step 8: Lint, types, full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js src/loxmatter/i18n/strings.yaml tests/api/test_version_api.py
git commit -m "feat(web): version card in System tab

The UI never showed which version was running. The card appears first in
the tab and loads before the checks — the checks do real network work, and
the version shouldn't wait for them.

In the dev checkout it says 'Running: dev' plus a note that this bridge was
built from a working copy. That's not an error state but the normal case
when developing — and the only wording that doesn't pretend there's a
version here.

The test verifies translation keys, not markup: a missing key only shows up
in the browser later, and then as an empty field instead of an error.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: CI builds and publishes multi-arch to GHCR

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_build_arguments.py` (new)

**Interfaces:**
- Consumes: the four `ARG` names from Task 3, `schema_version()` from Task 1.
- Produces: images `ghcr.io/lucienkerl/loxmatter:dev`, `:sha-<short>` (push to `main`) and `:<version>`, `:stable` (tag `v*`). Tasks 7 and 8 require that `:stable` exists.

- [ ] **Step 1: Write the failing test**

`tests/test_build_arguments.py` (GPL header, then):

```python
"""CI and Dockerfile must agree on the same four arguments.

This is deliberately NOT a test that just claims four lines exist in the
Dockerfile — such a test would be true the moment someone types the names,
and stay true if CI passes different ones after. What's checked is the
agreement between both files — exactly the error that only shows up at the
image: a `--build-arg` the Dockerfile doesn't know gets SILENTLY discarded
by Docker (just a warning), and the image then has an empty version.

The third test covers the third source: CI reads the schema version with
grep from store.py. If the line format changes there, grep returns nothing
— and the test fails here, not on the next update on someone else's Pi."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from loxmatter.model.store import schema_version

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# Same format the grep call in CI uses. Both are deliberately side by side:
# the test is only worth anything if it checks the exact pattern CI really
# applies.
SCHEMA_PATTERN = r"^_SCHEMA_VERSION = ([0-9]+)$"


def _build_push_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["image"]["steps"]:
        if str(step.get("uses", "")).startswith("docker/build-push-action"):
            return step
    raise AssertionError("No docker/build-push-action step in job 'image'")


def test_ci_passes_exactly_the_args_dockerfile_knows() -> None:
    declared = set(
        re.findall(
            r"^ARG\s+([A-Z_][A-Z0-9_]*)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE
        )
    )
    passed = {
        line.split("=", 1)[0].strip()
        for line in _build_push_step()["with"]["build-args"].strip().splitlines()
        if line.strip()
    }
    assert passed == declared


def test_both_architectures_are_built() -> None:
    # Pi is the normal case for this project, not the exception. If arm64
    # is missing, no one notices until a user reads "no matching manifest".
    platforms = _build_push_step()["with"]["platforms"]
    assert "linux/arm64" in platforms
    assert "linux/amd64" in platforms


def test_ci_grep_finds_schema_version() -> None:
    store_source = (ROOT / "src" / "loxmatter" / "model" / "store.py").read_text(encoding="utf-8")
    found = re.findall(SCHEMA_PATTERN, store_source, re.MULTILINE)
    assert len(found) == 1, "exactly one line must match, else grep misses it"
    assert int(found[0]) == schema_version()


def test_ci_uses_exactly_this_pattern() -> None:
    workflow_source = WORKFLOW.read_text(encoding="utf-8")
    assert SCHEMA_PATTERN.strip("^$") in workflow_source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_arguments.py -v`
Expected: FAIL — `KeyError: 'image'`, the job doesn't exist yet

- [ ] **Step 3: Add the `image` job**

At the end of `.github/workflows/ci.yml`:

```yaml

  # Builds and publishes the image (spec "Ship updates through the UI",
  # 2026-09-08, section 5). `needs: test` is not just cosmetic: an image
  # that hasn't passed the test suite must not appear under a tag someone
  # can install.
  image:
    needs: test
    if: github.event_name == 'push' && (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v'))
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - id: meta
        run: |
          set -eu
          # Schema version has exactly one source: store.py. If grep fails,
          # build aborts instead of publishing an image with empty
          # LOXMATTER_SCHEMA_VERSION — the stage 2 updater couldn't do its
          # pre-check and would have to guess. tests/test_build_arguments.py
          # checks the same pattern so a store.py change doesn't first show
          # up in CI.
          schema="$(sed -n -E 's/^_SCHEMA_VERSION = ([0-9]+)$/\1/p' src/loxmatter/model/store.py)"
          [ -n "$schema" ] || { echo "No _SCHEMA_VERSION found in store.py"; exit 1; }
          case "$GITHUB_REF" in
            refs/tags/v*) version="${GITHUB_REF#refs/tags/v}" ;;
            *)            version="dev" ;;
          esac
          {
            echo "schema=$schema"
            echo "version=$version"
            echo "commit=$(git rev-parse --short HEAD)"
            echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
          } >> "$GITHUB_OUTPUT"
      - id: tags
        run: |
          set -eu
          image="ghcr.io/lucienkerl/loxmatter"
          if [ "${{ steps.meta.outputs.version }}" = "dev" ]; then
            echo "list=$image:dev,$image:sha-${{ steps.meta.outputs.commit }}" >> "$GITHUB_OUTPUT"
          else
            echo "list=$image:${{ steps.meta.outputs.version }},$image:stable" >> "$GITHUB_OUTPUT"
          fi
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.tags.outputs.list }}
          build-args: |
            LOXMATTER_VERSION=${{ steps.meta.outputs.version }}
            LOXMATTER_COMMIT=${{ steps.meta.outputs.commit }}
            LOXMATTER_BUILT_AT=${{ steps.meta.outputs.built_at }}
            LOXMATTER_SCHEMA_VERSION=${{ steps.meta.outputs.schema }}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_arguments.py -v`
Expected: 4 passed

- [ ] **Step 5: Prove the consistency test can fail**

Temporarily remove the line `LOXMATTER_BUILT_AT=...` from `build-args` and run `uv run pytest tests/test_build_arguments.py -v`.
Expected: `test_ci_passes_exactly_the_args_dockerfile_knows` FAILS. Then restore it: 4 passed.

- [ ] **Step 6: Commit and push, then watch the run**

```bash
git add .github/workflows/ci.yml tests/test_build_arguments.py
git commit -m "ci: publish multi-arch image to GHCR

Push to main yields :dev and :sha-<short>, a v* tag yields :<version> and
:stable. \`needs: test\` is not just cosmetic — an image that hasn't passed
the test suite must not appear under a tag someone can install.

Schema version comes via sed from store.py and aborts the build if not found
there. An empty LOXMATTER_SCHEMA_VERSION in the image would be an image
whose schema jump the stage 2 updater couldn't pre-check — it would have to
guess, and that's exactly the case the pre-check is meant to eliminate.

The test compares Dockerfile and workflow against each other instead of just
claiming four lines exist: a --build-arg the Dockerfile doesn't know gets
SILENTLY discarded by Docker.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push
```

Then: `gh run watch` until the `image` job is done, and
`gh api /users/lucienkerl/packages/container/loxmatter/versions --jq '.[0].metadata.container.tags'`
Expected: contains `dev`

- [ ] **Step 7: Verify the published image actually carries its identity**

```bash
docker pull ghcr.io/lucienkerl/loxmatter:dev
docker inspect ghcr.io/lucienkerl/loxmatter:dev --format '{{json .Config.Env}}' | tr ',' '\n' | grep LOXMATTER
```
Expected: four lines, `LOXMATTER_VERSION=dev`, a seven-character commit, a timestamp, and `LOXMATTER_SCHEMA_VERSION=7`. **If any of these is empty, stop here** — stage 2 depends on exactly these values.

---

### Task 6: What constitutes a release

**Files:**
- Create: `CHANGELOG.md`
- Modify: `docs/DEVELOPMENT.md` (new section at end)

**Interfaces:**
- Consumes: nothing.
- Produces: the written rule that Task 7 follows.

- [ ] **Step 1: Write `CHANGELOG.md`**

```markdown
# Changes

Starting with 0.2.0, this project assigns version numbers following [Semantic
Versioning](https://semver.org/). Every published version has a section here,
and the UI shows its text as release notes before anyone installs an update
— so it's read by people who don't know the code.

## [Unreleased]

## [0.2.0] — 2026-09-08

### Added

- The UI shows in the System tab which version is running.
- Finished images are available at `ghcr.io/lucienkerl/loxmatter`
  (`arm64` and `amd64`). An update pulls them instead of building on the
  Raspberry Pi — taking about one minute instead of five to ten.

### Changed

- `scripts/update.sh` pulls the image instead of building locally. `--build`
  restores the old behavior.
- The stack runs from a published image. **This one change needs the
  console once:** `git pull && ./scripts/update.sh` on the machine
  running the bridge.
```

- [ ] **Step 2: Add the release rule to `docs/DEVELOPMENT.md`**

At the end of the file:

```markdown
## Publishing a release

Starting with 0.2.0, installations elsewhere rely on version numbers: the UI
compares the running version against the latest release, and the updater
installs exactly what was published here. This chain is not carried by code
but by discipline — that's why it's written here.

1. `CHANGELOG.md`: rewrite the `[Unreleased]` section with the new number
   and date. **Write for people who don't know the code** — this text
   appears in the confirmation dialog before an update.
2. Choose a number: `PATCH` for bug fixes, `MINOR` for new features, `MAJOR`
   for anything an existing installation must handle by hand.
3. **If `_SCHEMA_VERSION` in `model/store.py` rises, put that in the notes.**
   A schema jump is the only case where a fallback to the previous version
   is not consequence-free (see spec section 8).
4. Commit, then `git tag -a v0.3.0 -m "0.3.0"` and `git push --tags`.
5. CI builds `:0.3.0` and `:stable` from this. **Only when both are in the
   registry** is the version published — before that, the UI shows it, but
   installing would go nowhere.
6. Create a GitHub release whose text is the changelog section. The UI reads
   exactly this text.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/DEVELOPMENT.md
git commit -m "docs: changelog and the rule that makes a release

Starting with 0.2.0, installations elsewhere rely on version numbers — the
UI compares against them, the updater installs exactly what was published.
This chain is not carried by code but by discipline, so it's written here
with the rule to write the changelog for people who don't know the code:
it appears in the confirmation dialog before an update.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The first published version

**Files:**
- Modify: `pyproject.toml:3` (`version = "0.1.0"` → `version = "0.2.0"`)

**Interfaces:**
- Consumes: the CI job from Task 5, the rule from Task 6.
- Produces: tags `ghcr.io/lucienkerl/loxmatter:0.2.0` and `:stable`. **Tasks 8 and 9 require that `:stable` exists** — without this task the Compose file afterward points to an image that doesn't exist.

- [ ] **Step 1: Raise the version**

In `pyproject.toml`, line 3: `version = "0.2.0"`

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`
Expected: all green

- [ ] **Step 3: Commit, tag, push**

```bash
git add pyproject.toml
git commit -m "release: 0.2.0

The first version that an installation elsewhere can rely on.
pyproject.toml has been at 0.1.0 for 628 commits.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git tag -a v0.2.0 -m "0.2.0"
git push && git push --tags
```

- [ ] **Step 4: Wait for the images and verify them**

```bash
gh run watch
docker pull ghcr.io/lucienkerl/loxmatter:stable
docker inspect ghcr.io/lucienkerl/loxmatter:stable --format '{{index .Config.Env}}' | tr ' ' '\n' | grep LOXMATTER_VERSION
```
Expected: `LOXMATTER_VERSION=0.2.0`

**Without this result, Task 8 must not begin.**

- [ ] **Step 5: Create the GitHub release**

```bash
gh release create v0.2.0 --title "0.2.0" --notes-file - <<'EOF'
### Added

- The UI shows in the System tab which version is running.
- Finished images are available at `ghcr.io/lucienkerl/loxmatter` (arm64 and amd64). An update pulls them instead of building on the Raspberry Pi — taking about one minute instead of five to ten.

### Changed

- `scripts/update.sh` pulls the image instead of building locally. `--build` restores the old behavior.
- The stack runs from a published image. **This one change needs the console once:** `git pull && ./scripts/update.sh` on the machine running the bridge.
EOF
```

---

### Task 8: Compose runs from the image

**Files:**
- Modify: `deploy/testhost/docker-compose.yml` (service `loxmatter`)
- Test: `tests/test_compose_profiles.py` (three additions)

**Interfaces:**
- Consumes: `ghcr.io/lucienkerl/loxmatter:stable` from Task 7.
- Produces: the environment variable `LOXMATTER_IMAGE_TAG` as the one place where the running version is set. **Stage 2 rewrites exactly this value in `.env` to fall back.**

- [ ] **Step 1: Write the failing tests**

At the end of `tests/test_compose_profiles.py`:

```python
def test_bridge_runs_from_published_image() -> None:
    # Before 0.2.0, Compose built the image on the Pi — five to ten minutes,
    # with PyPI and storage as error sources in the middle of an update.
    image = _stack()["services"]["loxmatter"]["image"]
    assert image.startswith("ghcr.io/lucienkerl/loxmatter:")
    assert "${LOXMATTER_IMAGE_TAG:-stable}" in image


def test_build_path_remains_alongside() -> None:
    # `image:` and `build:` on the same service: `compose pull` pulls,
    # `compose build` builds, and `up` builds only if no image exists locally.
    # On a host without GHCR access, that's the fallback tier. A profile
    # wouldn't work here — profiles apply to services, not individual keys.
    assert _stack()["services"]["loxmatter"]["build"]["context"] == "../.."


def test_running_version_appears_at_exactly_one_place() -> None:
    # Stage 2 relies on this: the fallback rewrites ONE line in .env. If
    # the tag appears at a second place, only the one falls back and the
    # other doesn't.
    source = COMPOSE.read_text(encoding="utf-8")
    assert source.count("LOXMATTER_IMAGE_TAG") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: FAIL — `KeyError: 'image'`

- [ ] **Step 3: Change the compose file**

In the service `loxmatter`, **before** the existing `build:` block:

```yaml
    # Since 0.2.0 built from a published image instead of on the Pi (spec
    # "Ship updates through the UI", 2026-09-08, section 5). Building took
    # five to ten minutes on the test Pi and could fail on a PyPI outage or
    # storage — in the middle of an update someone started through the
    # browser, that's the wrong kind of surprise.
    #
    # LOXMATTER_IMAGE_TAG is in .env and is the ONE place where the running
    # version is set. Exactly for this reason: the updater (stage 2) falls
    # back by rewriting this one line. A second mention of the tag would be
    # one that stays behind.
    #
    # `build:` remains below. A Compose profile wouldn't work here (profiles
    # apply to services, not individual keys), and none is needed: `compose
    # pull` pulls, `compose build` builds explicitly, and `up` builds only
    # if no image exists locally. On a host without GHCR access, that's
    # exactly the desired fallback tier.
    image: ghcr.io/lucienkerl/loxmatter:${LOXMATTER_IMAGE_TAG:-stable}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: 6 passed

- [ ] **Step 5: Prove the single-mention test can fail**

Temporarily add a comment `# LOXMATTER_IMAGE_TAG` somewhere in the Compose file, run the tests.
Expected: `test_running_version_appears_at_exactly_one_place` FAILS (`2 != 1`). Then remove it: 6 passed.

- [ ] **Step 6: Verify against the real registry**

```bash
cd deploy/testhost && docker compose pull loxmatter && docker compose config | grep 'image: ghcr'
```
Expected: pull succeeds, and `config` shows `ghcr.io/lucienkerl/loxmatter:stable`

- [ ] **Step 7: Commit**

```bash
git add deploy/testhost/docker-compose.yml tests/test_compose_profiles.py
git commit -m "build(compose): build from published image instead of on Pi

LOXMATTER_IMAGE_TAG is the one place where the running version is set — the
stage 2 updater falls back by rewriting exactly this one line in .env. A
test holds that it stays unique: a second mention would be one that persists
during fallback.

build: remains alongside, without a profile — profiles apply to services,
not individual keys, and none is needed: up builds only if no image exists
locally. On a host without GHCR access, that's the desired fallback tier.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: `update.sh` pulls instead of building

**Files:**
- Modify: `scripts/update.sh` (argument parsing around lines 36-46, build step around lines 95-102)
- Modify: `README.md` (new "Updating" section, currently missing)
- Test: `tests/test_update_script.py` (new)

**Interfaces:**
- Consumes: the Compose file from Task 8.
- Produces: `./scripts/update.sh` with switches `--no-pull`, `--build`, `--no-cache`, `--help`. Stage 2 builds the same flow in the sidecar afterward.

- [ ] **Step 1: Write the failing test**

`tests/test_update_script.py` (GPL header, then):

```python
"""Behavior tests for scripts/update.sh.

Same procedure as in `test_install_script.py`: the script runs against a
sealed PATH of fake binaries, and we check WHICH commands it chooses —
not what they do. A real `docker compose pull` would be unreasonable
both in CI and on a dev machine.

The key test below is `test_without_build_never_built`: before 0.2.0 the
script always built, and the whole point of this change is that an update
on a Pi no longer takes five to ten minutes. A backslidden `compose build`
would go unnoticed otherwise — it works, just slowly."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update.sh"

SYSTEM_TOOLS = (
    "bash",
    "sh",
    "cat",
    "grep",
    "sed",
    "awk",
    "tr",
    "printf",
    "mkdir",
    "rm",
    "sleep",
    "date",
    "ls",
    "xargs",
    "tail",
    "seq",
    "hostname",
)


@pytest.fixture
def sealed(tmp_path):
    """A PATH from two directories: fake tools and the real ones the script
    legitimately needs. Each stub logs its call to $STUB_LOG and exits
    success — `curl` also outputs a health response so the wait loop
    proceeds immediately instead of waiting 120 seconds."""
    bindir = tmp_path / "bin"
    sysdir = tmp_path / "sys"
    bindir.mkdir()
    sysdir.mkdir()
    log = tmp_path / "stub.log"

    def stub(name: str, body: str = "") -> None:
        path = bindir / name
        path.write_text(
            f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8"
        )
        path.chmod(0o755)

    stub("docker")
    stub("git")
    stub("curl", 'echo \'{"status":"ok"}\'')

    for tool in SYSTEM_TOOLS:
        real = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(*args: str):
        result = subprocess.run(
            [str(SCRIPT), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{bindir}:{sysdir}",
                "STUB_LOG": str(log),
                "HOME": str(tmp_path),
            },
        )
        return result, log.read_text(encoding="utf-8") if log.exists() else ""

    return run


def test_pulls_image_instead_of_building(sealed):
    _, calls = sealed("--no-pull")
    assert "compose pull loxmatter" in calls


def test_without_build_never_built(sealed):
    _, calls = sealed("--no-pull")
    assert "compose build" not in calls


def test_with_build_builds_not_pulls(sealed):
    _, calls = sealed("--no-pull", "--build")
    assert "compose build loxmatter" in calls
    assert "compose pull loxmatter" not in calls


def test_pull_comes_before_restart(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("compose pull") < calls.index("compose up")


def test_restart_leaves_neighbor_services_alone(sealed):
    # --no-deps: OTBR's thread state depends on a volume, and restarting
    # the thread network is not part of an update.
    _, calls = sealed("--no-pull")
    up_line = next(line for line in calls.splitlines() if "compose up" in line)
    assert "--no-deps" in up_line


def test_database_backed_up_before_everything(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("volume inspect") < calls.index("compose pull")


def test_no_cache_without_build_rejected(sealed):
    result, _ = sealed("--no-pull", "--no-cache")
    assert result.returncode != 0
    assert "--build" in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_script.py -v`
Expected: FAIL — `test_pulls_image_instead_of_building`, `test_without_build_never_built`, `test_with_build_builds_not_pulls`, `test_pull_comes_before_restart`, `test_no_cache_without_build_rejected` fail; the two about `--no-deps` and the backup should already **pass** (the script does that already)

- [ ] **Step 3: Rewrite the argument parsing**

In `scripts/update.sh` replace the `for arg in "$@"` block with:

```bash
PULL=1
BUILD=0
NO_CACHE=""
for arg in "$@"; do
  case "$arg" in
    --no-pull)  PULL=0 ;;
    --build)    BUILD=1 ;;
    --no-cache) NO_CACHE=1 ;;
    -h|--help)  sed -n '18,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          printf 'Unknown argument: %s (allowed: --no-pull, --build, --no-cache, --help)\n' "$arg" >&2; exit 2 ;;
  esac
done
# --no-cache controls a build. Without --build it controls nothing, and a
# switch that silently does nothing is worse than one that's missing: it
# makes someone think they built fresh.
if [ -n "$NO_CACHE" ] && [ "$BUILD" -eq 0 ]; then
  printf 'Abort: --no-cache only works together with --build.\n' >&2
  exit 2
fi
```

And rewrite the header comment (lines 19-33) for the new switches:

```bash
# Brings the running bridge to the state of the published version.
#
#   ./scripts/update.sh              # fetch, pull image, restart
#   ./scripts/update.sh --no-pull    # only pull and restart
#   ./scripts/update.sh --build      # build from source instead of pulling
#   ./scripts/update.sh --build --no-cache   # build without layer cache
#
# Run on the machine where the bridge runs. The stack is in the repository
# itself (deploy/testhost/), the script finds it via its own path — no
# configuration step.
#
# Since 0.2.0 it pulls instead of building: building took five to ten
# minutes on the test Pi and could fail on a PyPI outage or storage.
# --build restores the old way, for development and for hosts without
# registry access.
#
# The service starts with `--no-deps`: matter-server and OTBR are untouched.
# Without it, Compose recreates them whenever the project config changes —
# and OTBR's thread state depends on a volume, which a rebuild survives, but
# restarting the thread network without reason is not part of an update.
```

- [ ] **Step 4: Replace the build step**

Replace the `say "Building the image"` block and its comment with:

```bash
# Pull instead of build (0.2.0). The long comment from 2026-09-03 about
# using `docker compose build` and not a standalone `docker build` stays
# unchanged — it now only applies to the --build branch below. The reason
# then stays the same: the service has a `build:` block in the Compose file
# and builds its own image; nobody uses a separately-built `loxmatter:local`.
if [ "$BUILD" -eq 1 ]; then
  say "Building the image"
  (cd "$STACK" && docker compose build ${NO_CACHE:+--no-cache} "$SERVICE") \
    || die "Build failed — the running service remains unchanged."
else
  say "Pulling the image"
  (cd "$STACK" && docker compose pull "$SERVICE") \
    || die "No image pulled — the running service remains unchanged. Without registry access, --build helps."
fi
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_script.py -v`
Expected: 7 passed

- [ ] **Step 6: Prove the central test can fail**

Temporarily change the `else` branch to `docker compose build "$SERVICE"`, run the tests.
Expected: `test_without_build_never_built` and `test_pulls_image_instead_of_building` FAIL. Then restore: 7 passed.

- [ ] **Step 7: Add the missing README section**

In `README.md` after the installation section:

```markdown
## Updating

```bash
cd ~/loxmatter && git pull && ./scripts/update.sh
```

The script backs up the signal database first, pulls the published image,
restarts only the bridge — matter-server and the Thread border router are
left alone — and waits until the bridge reports healthy again. On a
Raspberry Pi this takes about a minute.

`--build` builds from source instead of pulling, for development or for a
host that cannot reach `ghcr.io`.

The System tab shows which version is running.
```

- [ ] **Step 8: Run shellcheck, lint, types, full suite**

Run: `shellcheck scripts/update.sh && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all green

- [ ] **Step 9: Run it for real on the test host**

```bash
./scripts/update.sh
```
Expected: "Pulling the image", then a healthy service and device status output. In the browser, the System tab now shows `Running: 0.2.0`.

- [ ] **Step 10: Commit**

```bash
git add scripts/update.sh README.md tests/test_update_script.py
git commit -m "feat(update): pull image instead of building on Pi

Building took five to ten minutes on the test Pi and could fail on a PyPI
outage or storage. For the console it was a long bar; for the button in the
UI (stage 2) it would have been unreasonable.

--build restores the old way, for development and for hosts without registry
access. --no-cache without --build is now rejected instead of silently doing
nothing: a switch that does nothing makes someone think they built fresh.

The tests run like install.sh's against a sealed PATH and check WHICH
commands the script chooses. The key one verifies that without --build it
never builds — a backslidden compose build would go unnoticed otherwise,
it works, just slowly.

The README has so far said nothing about updating.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Stage 1 complete

After Task 9:

- The UI shows which version is running.
- There is a published version `0.2.0`, a `:stable` image for `arm64` and `amd64`, and a changelog.
- A console update takes about one minute instead of ten.

This stands on its own. **Stage 2** — sidecar, `/api/update/*`, the update card with its four states and automatic rollback — has its own plan: `docs/superpowers/plans/2026-09-08-webui-updates-stage-2.md`.
