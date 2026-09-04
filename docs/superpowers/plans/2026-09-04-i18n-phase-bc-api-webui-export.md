# i18n Phase B+C: API + WebUI + Export-Vorlagen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Phase A's `i18n.t()`/`strings.yaml` mechanism to the API (error messages), the WebUI (all static and dynamic text), and the exported Loxone template files — using the one shared language setting from Phase A everywhere.

**Architecture:** Three new dotted namespaces in the existing `src/loxmatter/i18n/strings.yaml` (`api.*`, `web.*`, `export.*`). A new per-request middleware keeps the server's process-global `i18n` state in sync with the stored setting on every request (the long-running server's equivalent of Phase A's per-process CLI bootstrap). Two new routes (`GET /api/i18n` unauthenticated, `PATCH /api/language` authenticated) let the WebUI fetch its own translations and change the setting. The WebUI itself gets a small hand-written client-side `t()` (no new JS dependency, no build step) that reads a string table fetched once at page load.

**Tech Stack:** Python 3.12, FastAPI/Starlette, PyYAML (already present), Alpine.js (already vendored, no build step), pytest.

## Global Constraints

- Default language is `en`; only `en`/`de` supported — unchanged from Phase A.
- One shared setting for the whole installation — unchanged from Phase A. No per-browser/per-user language.
- No new Python dependency (no Jinja2 — `index.html` stays a literal static file, per spec §3) and no new JS dependency/build step for the client-side translation helper.
- API error `detail=` text is a SHARED surface with the WebUI: `app.js`'s `readErrorDetail` displays `detail` verbatim (Phase A finding, unchanged). Do not build a second, separate `web.*` translation of the same error text — the WebUI shows exactly what the API returns.
- Export-template text (title/comment fields) follows the shared setting at export time, for both the CLI and the WebUI export paths — confirmed design decision, spec §8. Only newly generated templates are affected; already-imported ones in Loxone Config are untouched.
- `GET /api/i18n` is deliberately exempt from the login guard (`build_api_guard`) — a third, explicit exception alongside `/cmd`/`/resync`, needed because the login/setup screen itself must render translated before anyone can be authenticated. `PATCH /api/language` is NOT exempt — it requires the same session/token as every other `/api` route.
- The `sync_language` middleware must be registered so it runs BEFORE every other request-handling code in `build_app` (including the existing `_record_command` middleware and the `build_api_guard` dependency) — see Task 1 for the exact placement and why middleware registration order matters here.
- Source comments and docstrings stay German throughout — only user-facing text (API `detail=`, WebUI text, export template fields) becomes bilingual, exactly as in Phase A.
- Internal invariant-violation exceptions that a normal user cannot trigger through ordinary use (e.g. `export/signals.py`'s `ValueError`s for a key collision or a signal belonging to the wrong device — programmer-error-only conditions, never expected user-facing runtime states) are explicitly OUT of scope for translation, matching how Phase A left similarly internal error paths in `cli.py` untouched.
- `docs/superpowers/specs/2026-09-04-i18n-phase-bc-api-webui-export-design.md` is the approved spec this plan implements.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/loxmatter/i18n/strings.yaml` | Extended with `api.*`, `web.*`, `export.*` namespaces — same file as Phase A, no restructuring. |
| `src/loxmatter/api/language.py` | New. `build_i18n_router(store)` → `GET /api/i18n` (unauthenticated). `build_language_router(store)` → `PATCH /api/language` (authenticated, included with the guard like every other `/api` router). |
| `src/loxmatter/loxone/server.py` | Modified: new `sync_language` middleware registered first in `build_app`; the two new routers wired in (one with, one without `dependencies=api_guard`); `build_api_guard`'s own 401 `detail` and the `/cmd`/`/resync` handlers' `detail=` strings migrated to `t()`. |
| `src/loxmatter/api/control.py`, `devices.py`, `auth.py`, `diagnostics.py`, `export.py` | Modified: every literal `detail=`/`SystemCheckOut(detail=...)` German string migrated to `i18n.t("api.*", ...)`. |
| `src/loxmatter/model/store.py` | Modified: `UnknownDeviceError`/`UnknownCommandError` messages (and any other exception text found to reach an HTTP response) migrated to `t()`. |
| `src/loxmatter/matter/client.py`, `src/loxmatter/commands/translate.py` | Modified: exception messages that reach `HTTPException(detail=str(exc))` migrated to `t()`. |
| `src/loxmatter/export/documents.py`, `src/loxmatter/export/signals.py` | Modified: template `title`/`comment` field text migrated to `i18n.t("export.*", ...)`. |
| `src/loxmatter/web/vendor/i18n.js` (or similar — exact name decided in Task 9) | New. The client-side `t()` helper + Alpine store, no build step, plain `<script>` include. |
| `src/loxmatter/web/index.html` | Modified: static text nodes become `x-text="t('web.xyz')"` (or `:attr="t(...)"`); `x-cloak` added to the app shell; the empty "Weitere Einstellungen" settings card becomes the EN/DE language toggle. |
| `src/loxmatter/web/app.js` | Modified: `init()` fetches `GET /api/i18n` first; every dynamic user-facing string routes through the same `t()`. |
| `tests/...` | Modified/new throughout, mirroring Phase A's pattern: existing assertions on literal German text move to English, German companions added via `i18n.set_language("de")` (server-side) or a fetched-strings fixture (client-side, exact approach decided per task). |

---

## Task 1: `sync_language` middleware + `GET /api/i18n` + `PATCH /api/language`

**Files:**
- Create: `src/loxmatter/api/language.py`
- Modify: `src/loxmatter/loxone/server.py`
- Create: `tests/api/test_language.py`

**Interfaces:**
- Consumes: `loxmatter.i18n.{set_language, current_language, t, SUPPORTED_LANGUAGES}` (Phase A), `store.locale.{get_language, set_language}` (Phase A `LocaleStore`).
- Produces: `build_i18n_router(store: Store) -> APIRouter` (`GET /api/i18n`), `build_language_router(store: Store) -> APIRouter` (`PATCH /api/language`) — both importable from `loxmatter.api.language`. `build_app` wires the `sync_language` middleware and both routers; every later task's route handlers can rely on `i18n.current_language()` already reflecting the stored setting for the current request.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_language.py`. First check the existing test fixture setup other `tests/api/*.py` files use (look at `tests/api/conftest.py` and e.g. `tests/api/test_web.py` for how a test builds an app/client against a `Store` — copy that exact setup pattern, do not invent a new one). Using that pattern:

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Tests fuer GET /api/i18n (ungeschuetzt) und PATCH /api/language
(geschuetzt) sowie die sync_language-Middleware, die die gespeicherte
Spracheinstellung bei jeder Anfrage frisch liest."""

from __future__ import annotations

from loxmatter import i18n

# The `client`/`authenticated_client`/`store` fixtures below come from
# `tests/api/conftest.py` — import them exactly as `tests/api/test_web.py`
# already does; the import line is intentionally left for the implementer
# to copy verbatim from that file rather than guessed here.


def test_get_i18n_works_without_a_session(client):
    """Die dritte, bewusste Ausnahme von der Anmeldepflicht (Spec-Abschnitt
    5) - ohne Cookie, ohne Token, trotzdem 200."""
    response = client.get("/api/i18n")
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert isinstance(body["strings"], dict)


def test_get_i18n_only_returns_the_web_namespace(client):
    response = client.get("/api/i18n")
    body = response.json()
    assert all(key.startswith("web.") for key in body["strings"])


def test_patch_language_requires_a_session(client):
    response = client.patch("/api/language", json={"language": "de"})
    assert response.status_code == 401


def test_patch_language_persists_and_is_reflected_by_the_next_request(
    authenticated_client, store
):
    """Beweist die Middleware, nicht nur die Route: eine ZWEITE, unabhaengige
    Anfrage (hier /api/i18n, das keine Anmeldung braucht) muss die neue
    Sprache sehen - nicht nur store.locale direkt."""
    response = authenticated_client.patch("/api/language", json={"language": "de"})
    assert response.status_code == 200
    assert store.locale.get_language() == "de"

    follow_up = authenticated_client.get("/api/i18n")
    assert follow_up.json()["language"] == "de"


def test_patch_language_rejects_an_unsupported_value(authenticated_client):
    response = authenticated_client.patch("/api/language", json={"language": "fr"})
    assert response.status_code == 400


def test_sync_language_middleware_sees_a_change_made_directly_through_the_store(
    client, store
):
    """Die Luecke aus Spec-Abschnitt 4: eine Aenderung, die NICHT ueber
    PATCH /api/language lief (hier direkt ueber store.locale, wie es
    `loxmatter set-language` in einem anderen Prozess taete), muss die
    NAECHSTE Anfrage trotzdem sehen."""
    store.locale.set_language("de")
    response = client.get("/api/i18n")
    assert response.json()["language"] == "de"


def test_a_request_does_not_leak_language_state_to_i18n_t_outside_the_request():
    """Nach jeder Anfrage soll die globale i18n-Sprache wieder auf den von
    tests/conftest.pys reset_language-Fixture gesetzten Wert stehen - dieser
    Test dokumentiert nur die Erwartung; reset_language selbst erledigt die
    eigentliche Absicherung."""
    assert i18n.current_language() == i18n.DEFAULT_LANGUAGE
```

(the `client`/`authenticated_client`/`store` fixture names above are placeholders for whatever `tests/api/conftest.py` actually calls them — **read that file first** and use its real fixture names; if no `authenticated_client`-style fixture already exists, look at how an existing test in `tests/api/test_settings.py` or similar authenticates before a `PATCH`, and copy that exact pattern instead of inventing a new one)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_language.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.api.language'` (or a fixture-not-found error if the placeholder fixture names above don't match reality — fix the test file's imports/fixture names to match `tests/api/conftest.py` BEFORE treating this as the expected failure)

- [ ] **Step 3: Implement `api/language.py`**

Create `src/loxmatter/api/language.py`:

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Die gemeinsame Spracheinstellung ueber die API - Phase B+C, Spec-Abschnitt 5.

Zwei getrennte Router, weil sie unterschiedlich geschuetzt werden muessen
(`loxone.server.build_app` bindet sie deshalb mit unterschiedlichem
`dependencies=`-Argument ein, siehe dort):

- `build_i18n_router`: `GET /api/i18n` - UNGESCHUETZT. Die Ersteinrichtungs-
  und Anmeldeseite braucht diese Texte, um sich ueberhaupt anzuzeigen, bevor
  jemand angemeldet sein kann - dieselbe Notwendigkeit wie bei `/auth-info`
  (siehe `api/auth.py`), nur fuer Uebersetzungen statt Zugangsstatus.
- `build_language_router`: `PATCH /api/language` - geschuetzt wie jede
  andere `/api`-Route, die den Zustand der Installation aendert.

Liest `i18n`s eigene, bereits durch die Namensraum-Konvention (`web.*`)
gefilterte Teilmenge - kein zweiter, eigener Satz Uebersetzungen fuer den
Client, dieselbe `strings.yaml` wie ueberall sonst."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loxmatter import i18n
from loxmatter.model.store import Store


class I18nOut(BaseModel):
    language: str
    strings: dict[str, str]


class LanguageIn(BaseModel):
    language: str


class LanguageOut(BaseModel):
    language: str


def _web_strings() -> dict[str, str]:
    """Alle `web.*`-Schluessel, aufgeloest in der aktuellen Sprache.

    `i18n._STRINGS` traegt jeden Schluessel des gesamten Projekts
    (`cli.*`, `api.*`, `web.*`, `export.*`, `test.*`) - diese Funktion
    filtert auf genau den Namensraum, den der Client braucht, und laesst
    `i18n.t()` selbst die Aufloesung (inkl. Ruecksicherungsfall) erledigen,
    statt die Tabelle hier ein zweites Mal auszulesen."""
    return {key: i18n.t(key) for key in i18n.strings_with_prefix("web.")}


def build_i18n_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/i18n")
    async def get_i18n() -> I18nOut:
        return I18nOut(language=i18n.current_language(), strings=_web_strings())

    return router


def build_language_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.patch("/language")
    async def set_language(body: LanguageIn) -> LanguageOut:
        if body.language not in i18n.SUPPORTED_LANGUAGES:
            raise HTTPException(
                status_code=400,
                detail=i18n.t(
                    "api.language.fail_unsupported",
                    language=body.language,
                    supported=", ".join(sorted(i18n.SUPPORTED_LANGUAGES)),
                ),
            )
        store.locale.set_language(body.language)
        return LanguageOut(language=body.language)

    return router
```

**This step also requires a small addition to `src/loxmatter/i18n/__init__.py`**: a `strings_with_prefix(prefix: str) -> list[str]` helper, since nothing in Phase A needed to enumerate keys by namespace. Add it next to `t()`:

```python
def strings_with_prefix(prefix: str) -> list[str]:
    """Alle Schluessel, die mit `prefix` beginnen - fuer `api/language.py`s
    `GET /api/i18n`, das nur den `web.*`-Namensraum an den Client
    ausliefert, nicht die gesamte Tabelle (CLI-Hilfetexte, API-
    Fehlermeldungen etc. gehen den Browser nichts an)."""
    return [key for key in _STRINGS if key.startswith(prefix)]
```

Add a corresponding test to `tests/test_i18n.py`:

```python
def test_strings_with_prefix_returns_only_matching_keys():
    keys = i18n.strings_with_prefix("test.")
    assert "test.greeting" in keys
    assert "test.english_only" in keys
    assert not any(not k.startswith("test.") for k in keys)
```

Also add the two new keys this task's route handler needs to `src/loxmatter/i18n/strings.yaml` (under a new `# api.language` comment near the top of the `api.*` section this and later tasks build up):

```yaml
api.language.fail_unsupported:
  en: "Unsupported language '{language}' — expected: {supported}."
  de: "Nicht unterstützte Sprache '{language}' — erwartet: {supported}."
```

- [ ] **Step 4: Wire the middleware and both routers into `build_app`**

Edit `src/loxmatter/loxone/server.py`. Add the import:

```python
from loxmatter.api.language import build_i18n_router, build_language_router
from loxmatter import i18n
```

(add these two lines next to the existing `from loxmatter.api.*` imports)

**Correction (Whole-Branch-Review, 2026-09-04):** the snippet below — and its
docstring's claim that registering FIRST makes a middleware the outermost
layer — reflects Task 1's original (incorrect) understanding of Starlette's
middleware semantics. The real semantics are the opposite: the LAST
`@app.middleware("http")` registered ends up outermost (see the corrected
"Middleware-Registrierungsreihenfolge" reference section below). The shipped
code in `server.py` now registers `_sync_language` AFTER `_record_command`,
not before, and its docstring there states the corrected semantics. This
snippet is left as historical record of what Task 1 actually executed at the
time; do not copy its ordering or its docstring's derivation.

Inside `build_app`, insert the middleware as the FIRST thing registered — before `_append_command_log`/`_record_command` are even defined, right after the `api_guard = [...]` line:

```python
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)
    command_log: RingBuffer[CommandLogEntry] = RingBuffer(maxlen=COMMAND_LOG_SIZE)
    api_guard = [Depends(build_api_guard(api_token, store))]

    @app.middleware("http")
    async def _sync_language(
        request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        """Liest die gespeicherte Spracheinstellung bei JEDER Anfrage frisch
        (Spec-Abschnitt 4) - registriert als ALLERERSTE Middleware, damit sie
        vor `_record_command` und vor jeder Route (einschliesslich des
        Anmelde-Waechters, dessen 401-Text ebenfalls uebersetzt ist) laeuft.
        Middleware-Registrierungsreihenfolge in Starlette: die zuerst per
        `@app.middleware("http")` registrierte Funktion wird zur AEUSSEREN
        Schicht und sieht eine Anfrage deshalb zuerst - siehe die
        ausfuehrliche Herleitung im Implementierungsplan dieser Aufgabe,
        Abschnitt "Middleware-Registrierungsreihenfolge".

        `store.locale.get_language()` wirft nie (Phase A) - kein try/except
        noetig, anders als `_append_command_log` weiter unten, das einen
        echten Fehlschlag beim Schreiben in einen fremden Ringpuffer
        abfaengt."""
        i18n.set_language(store.locale.get_language())
        return await call_next(request)

    def _append_command_log(*, method: str, path: str, status: int) -> None:
```

(the `def _append_command_log(...)` line above is the EXISTING line already in the file — this step inserts the new middleware immediately before it, changing nothing about `_append_command_log`'s own body)

Then, near the other `app.include_router(...)` calls, add both new routers — `i18n` WITHOUT the guard (next to `build_auth_router`), `language` WITH the guard (next to the other five guarded routers):

```python
    app.include_router(build_device_router(store, client, runtime), dependencies=api_guard)
    app.include_router(build_export_router(store), dependencies=api_guard)
    app.include_router(build_settings_router(store), dependencies=api_guard)
    app.include_router(build_language_router(store), dependencies=api_guard)
    app.include_router(build_live_router(runtime), dependencies=api_guard)
```

(insert the `build_language_router` line among the other guarded routers — exact position among them doesn't matter, FastAPI doesn't care about router registration order for routes with distinct paths)

```python
    # OHNE `dependencies=api_guard` - genau wie `/health`, `/cmd` und
    # `/resync` weiter unten, UND wie `build_auth_router` direkt darueber.
    # Siehe api/language.py-Moduldocstring: die Ersteinrichtungs-/
    # Anmeldeseite braucht diese Uebersetzungen, um sich ueberhaupt
    # anzuzeigen, bevor jemand angemeldet sein kann.
    app.include_router(build_auth_router(store))
    app.include_router(build_i18n_router(store))
```

(the `app.include_router(build_auth_router(store))` line is EXISTING — this step adds the `build_i18n_router` line directly after it, inside the same "no guard" comment block)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_language.py tests/test_i18n.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`
Expected: all pass — this task changes `build_app`'s middleware stack and route table, both exercised by every existing `tests/api/*.py` test, so a full run matters more than usual here.

Run: `uv run ruff check src/loxmatter/api/language.py src/loxmatter/i18n/ src/loxmatter/loxone/server.py`
Run: `uv run mypy src/loxmatter/api/language.py src/loxmatter/i18n/ src/loxmatter/loxone/server.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/language.py src/loxmatter/i18n/__init__.py src/loxmatter/i18n/strings.yaml src/loxmatter/loxone/server.py tests/api/test_language.py tests/test_i18n.py
git commit -m "$(cat <<'EOF'
feat(api): sync_language-Middleware, GET /api/i18n, PATCH /api/language

Erste Aufgabe von Phase B+C: die WebUI kann ab jetzt ihre eigenen
Uebersetzungen abrufen (ungeschuetzt, fuer die Anmeldeseite) und die
gemeinsame Spracheinstellung aendern (geschuetzt). Die Middleware
liest die gespeicherte Einstellung bei jeder Anfrage frisch, damit ein
per CLI waehrend des Laufs geaenderter Wert ohne Neustart ankommt.
Noch keine der bestehenden API-Fehlermeldungen ist migriert - das
folgt in den naechsten Aufgaben.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Middleware-Registrierungsreihenfolge (Referenz fuer Task 1)

**Korrektur (Whole-Branch-Review, 2026-09-04):** Der urspruengliche Text
dieses Abschnitts behauptete "Zuerst registriert = laeuft zuerst" und liess
Task 1 `_sync_language` deshalb VOR `_record_command` registrieren. Das ist
falsch herum - siehe unten fuer die tatsaechlichen Starlette-Semantiken und
den Review-Fix (`_sync_language` wird in `server.py` inzwischen ALS LETZTE
der beiden Middlewares registriert, nicht als erste). Der Fehler in der
urspruenglichen Herleitung: `@app.middleware("http")` ruft intern
`add_middleware` auf, und `Starlette.add_middleware` fuegt jede neue
Middleware mit `self.user_middleware.insert(0, ...)` VORNE in die Liste ein
- `user_middleware` ist bei zwei Registrierungen `A` (zuerst), `B` (danach)
also `[B, A]`, NICHT `[A, B]` wie unten angenommen.

Starlette baut den Middleware-Stapel in `Starlette.build_middleware_stack()`
so auf: `app = router`, dann fuer jedes Element von
`reversed([ServerError] + user_middleware + [ExceptionMiddleware])` wird die
jeweilige Middleware um `app` HERUM gelegt (`app = cls(app=app, ...)`). Mit
`user_middleware = [B, A]` (siehe oben) ergibt `reversed(...)` die
Wickel-Reihenfolge `ServerError, A, B, ExceptionMiddleware` - die Schicht-
Reihenfolge von aussen nach innen ist damit `ServerError → B → A →
ExceptionMiddleware → router`. Eine eingehende Anfrage durchlaeuft die
Schichten von aussen nach innen - `B`s Code vor seinem `await
call_next(...)` laeuft deshalb VOR `A`s entsprechendem Code, obwohl `A`
ZUERST registriert wurde. **Zuletzt registriert = aeusserste Schicht = laeuft
zuerst.** Verifiziert per `TestClient`-Probe (zwei Middlewares, Aufrufreihen-
folge geloggt: `second-in, first-in, handler, first-out, second-out` fuer
zwei nacheinander per `@app.middleware("http")` registrierte Funktionen
`first`, `second`) sowie per `app.user_middleware[0]`, das nach zwei
Registrierungen die ZULETZT registrierte Funktion enthaelt. `_sync_language`
muss deshalb NACH `_record_command` registriert werden, nicht davor - so ist
es in `server.py` inzwischen umgesetzt, mit einem Test
(`test_sync_language_is_the_outermost_middleware` in
`tests/loxone/test_server.py`), der `app.user_middleware[0]` genau darauf
prueft.

(Randbemerkung, nicht sicherheitsrelevant fuer diese Aufgabe: FastAPIs
`Depends(...)`-Abhaengigkeiten, also auch `build_api_guard`, loesen erst
WAEHREND der Routenbehandlung auf, die selbst innerhalb JEDER Middleware
liegt - der Waechter saehe die richtige Sprache also auch, wenn die
Registrierungsreihenfolge der beiden Middlewares vertauscht waere. Die
Reihenfolge oben ist trotzdem wichtig und sollte nicht aus Bequemlichkeit
vertauscht werden: `_record_command`s Ringpuffer-Eintraege selbst tragen
keinen uebersetzten Text, aber ein kuenftiger Diagnose-Text dort sollte
sich auf eine bereits aufgeloeste Sprache verlassen koennen, ohne dass
jemand die Registrierungsreihenfolge erneut nachvollziehen muss.)

---

## Task 2: Exception-Texte, die ueber `detail=str(exc)` in HTTP-Antworten landen

**Warum diese Aufgabe vor den einzelnen API-Routern kommt:** mehrere
`HTTPException(detail=str(exc))`-Stellen in `api/control.py`,
`api/devices.py`, `api/export.py` geben nur eine Ausnahme weiter, deren
deutscher Text an einer ANDEREN Stelle entsteht - `model/store.py`
(`UnknownDeviceError`, `UnknownCommandError`), `commands/translate.py`
(`UnsupportedValueError`, dreimal), `matter/client.py`
(`MatterUnavailableError`, sechsmal, `CommissioningError`, einmal). Diese
Aufgabe migriert die Texte AN IHRER QUELLE - die `api/*.py`-Aufrufstellen,
die nur `str(exc)` weiterreichen, brauchen dadurch KEINE eigene Aenderung:
sobald die Ausnahme selbst einen uebersetzten Text traegt, liefert
`str(exc)` ihn automatisch weiter (`UnknownDeviceError.__str__`/
`UnknownCommandError.__str__` geben `str(self.args[0])` unveraendert
zurueck, siehe `model/store.py`).

**Bewusst NICHT migriert** (siehe Global Constraints, "Internal
invariant-violation exceptions"): `model/store.py`s beide
`ValueError`s in `_assign_key`/`register_commands` (Schluessel-Kollision)
werden aktuell in KEINEM `api/*.py`-Aufrufer abgefangen - ein Aufruf
propagiert bis zu FastAPIs Standard-500-Handler, der `detail` gar nicht
uebersetzt ausliefert. Ebenfalls nicht migriert: `Store.udp_port()`s
`KeyError` (kein Aufrufer in `api/*.py`, nicht HTTP-erreichbar). Beide sind
ein vorbestehender Befund (unbehandelte Ausnahme bei einer Schluessel-
Kollision landet als nackter 500 statt eines aussagekraeftigen Fehlers),
kein Uebersetzungsproblem - der Umsetzer soll das NICHT im Rahmen dieser
Aufgabe beheben, nur nicht faelschlich fuer bereits erledigt halten.

**Files:**
- Modify: `src/loxmatter/model/store.py:836` (`UnknownDeviceError`), `src/loxmatter/model/store.py:1178` (`UnknownCommandError`)
- Modify: `src/loxmatter/commands/translate.py:84,91,132-134`
- Modify: `src/loxmatter/matter/client.py:242,244-248,330,355,387,438-440,470` (`MatterUnavailableError`) und `:389` (`CommissioningError`)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Create: `tests/model/test_store_error_messages.py`, `tests/commands/test_translate_error_messages.py`, `tests/matter/test_client_error_messages.py` (drei kleine, fokussierte Dateien statt eine grosse — jede prueft nur die Uebersetzung an ihrer eigenen Quelle, unabhaengig von den API-Routern, die diese Ausnahmen spaeter fangen)

**Interfaces:**
- Consumes: `loxmatter.i18n.t` (Phase A).
- Produces: keine neue oeffentliche Schnittstelle — jede betroffene Ausnahme traegt ab dieser Aufgabe einen `i18n.t(...)`-Text statt eines hartkodierten deutschen. Spaetere Aufgaben (3-6), die dieselben Ausnahmen in `api/*.py` fangen, brauchen an den reinen `detail=str(exc)`-Stellen NICHTS zu aendern.

- [ ] **Step 1: Write the failing tests**

Create `tests/model/test_store_error_messages.py` (read `tests/model/test_store.py` first for the existing fixture/import pattern used there — mirror it, do not invent a new setup):

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Tests fuer die uebersetzten Texte von UnknownDeviceError/UnknownCommandError -
str(exc) reicht diesen Text unveraendert in eine HTTP-Antwort weiter
(siehe api/control.py, api/devices.py, api/export.py), diese Tests pruefen
aber nur die Ausnahme selbst, unabhaengig von der API."""

from __future__ import annotations

from loxmatter import i18n
from loxmatter.model.store import Store, UnknownDeviceError, UnknownCommandError


def test_unknown_device_error_is_english_by_default(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        try:
            store.device(999)
        except UnknownDeviceError as exc:
            assert str(exc) == "unknown device 999"
        else:
            raise AssertionError("expected UnknownDeviceError")
    finally:
        store.close()


def test_unknown_device_error_is_german_when_set(tmp_path):
    i18n.set_language("de")
    store = Store(tmp_path / "t.sqlite")
    try:
        try:
            store.device(999)
        except UnknownDeviceError as exc:
            assert str(exc) == "unbekanntes Geraet 999"
        else:
            raise AssertionError("expected UnknownDeviceError")
    finally:
        store.close()


def test_unknown_command_error_is_english_by_default(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        try:
            store.resolve_command("nope")
        except UnknownCommandError as exc:
            assert str(exc) == "unknown command key 'nope'"
        else:
            raise AssertionError("expected UnknownCommandError")
    finally:
        store.close()
```

(check `Store.resolve_command`'s actual signature/behavior in `store.py` before writing the last test — call it exactly as the existing test suite already does elsewhere, e.g. search `tests/model/test_store_commands.py` for a real usage)

Create `tests/commands/test_translate_error_messages.py`:

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Tests fuer die uebersetzten UnsupportedValueError-Texte in
commands/translate.py."""

from __future__ import annotations

import pytest

from loxmatter import i18n
from loxmatter.commands.translate import UnsupportedValueError, _as_number, to_matter_call
from loxmatter.model.store import StoredCommand


def test_as_number_error_is_english_by_default():
    with pytest.raises(UnsupportedValueError, match="value 'abc' is not a number"):
        _as_number("abc")


def test_as_number_error_is_german_when_set():
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="Wert 'abc' ist keine Zahl"):
        _as_number("abc")


def test_unsupported_command_error_is_english_by_default():
    command = StoredCommand(
        key="d1_c99_cmd0",
        slug="cmd0",
        node_id=1,
        endpoint=1,
        cluster_id=99,
        command_id=0,
        takes_value=False,
        device_id=1,
    )
    with pytest.raises(UnsupportedValueError, match="Cluster 99 command 0 is not supported"):
        to_matter_call(command, "")
```

(verify `StoredCommand`'s exact field names/order against `model/store.py` before using this constructor — copy an existing usage from `tests/commands/test_translate.py` instead of guessing if the fields above don't match)

Create `tests/matter/test_client_error_messages.py`:

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Tests fuer die uebersetzten MatterUnavailableError/CommissioningError-
Texte in matter/client.py - nur die Texte, die die einfach zu erreichenden
Zweige betreffen (kein echtes matter-server noetig)."""

from __future__ import annotations

import pytest

from loxmatter import i18n
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError


async def test_require_upstream_error_is_english_by_default():
    client = BridgeMatterClient("ws://example.invalid/ws")
    with pytest.raises(MatterUnavailableError, match="not connected to matter-server"):
        await client.snapshots()


async def test_require_upstream_error_is_german_when_set():
    i18n.set_language("de")
    client = BridgeMatterClient("ws://example.invalid/ws")
    with pytest.raises(MatterUnavailableError, match="nicht verbunden mit matter-server"):
        await client.snapshots()


async def test_snapshot_of_unknown_node_is_english_by_default():
    client = BridgeMatterClient("ws://example.invalid/ws")

    class _FakeUpstream:
        def get_nodes(self):
            return []

    client._upstream = _FakeUpstream()  # bypasses connect() for this narrow test
    with pytest.raises(MatterUnavailableError, match="unknown node 42"):
        await client.snapshot(42)
```

(check `tests/matter/test_client.py` first for how existing tests there construct a `BridgeMatterClient` against a fake upstream — reuse that exact pattern/fixture rather than the ad-hoc `_FakeUpstream` sketched above if a more complete fake already exists in that file)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/model/test_store_error_messages.py tests/commands/test_translate_error_messages.py tests/matter/test_client_error_messages.py -v`
Expected: FAIL — the English-language assertions fail because the source still raises German-only text (e.g. `AssertionError: 'unknown device 999' != 'unbekanntes Geraet 999'`, or a `pytest.raises(..., match=...)` failure since the pattern doesn't match the still-German message)

- [ ] **Step 3: Add the new `strings.yaml` keys**

Add to `src/loxmatter/i18n/strings.yaml`, under a new `# api.errors — cross-module exceptions surfaced via HTTPException(detail=str(exc))` comment:

```yaml
api.errors.unknown_device:
  en: "unknown device {device_id}"
  de: "unbekanntes Geraet {device_id}"
api.errors.unknown_command:
  en: "unknown command key {command_key!r}"
  de: "unbekannter Kommando-Schluessel {command_key!r}"
api.errors.value_not_a_number:
  en: "value {value!r} is not a number"
  de: "Wert {value!r} ist keine Zahl"
api.errors.command_unsupported:
  en: "Cluster {cluster_id} command {command_id} is not supported"
  de: "Cluster {cluster_id} Kommando {command_id} wird nicht unterstuetzt"
api.errors.listener_stopped_early:
  en: "Listener stopped before reporting readiness"
  de: "Listener wurde beendet, bevor er Bereitschaft meldete"
api.errors.listener_timeout:
  en: "matter-server did not report readiness after {timeout:.0f}s"
  de: "matter-server hat nach {timeout:.0f}s keine Bereitschaft gemeldet"
api.errors.not_connected:
  en: "not connected to matter-server"
  de: "nicht verbunden mit matter-server"
api.errors.unknown_node:
  en: "unknown node {node_id}"
  de: "unbekannter Node {node_id}"
api.errors.matter_server_unreachable:
  en: "matter-server unreachable: {exc}"
  de: "matter-server nicht erreichbar: {exc}"
api.errors.command_unknown_to_sdk:
  en: "Cluster {cluster_id} command {command_id} is unknown to the chip SDK"
  de: "Cluster {cluster_id} Kommando {command_id} ist der chip-SDK unbekannt"
api.errors.subscribe_already_called:
  en: "subscribe() has already been called"
  de: "subscribe() wurde bereits aufgerufen"
api.errors.commissioning_failed:
  en: "Commissioning failed: {exc}"
  de: "Einlernen fehlgeschlagen: {exc}"
```

- [ ] **Step 4: Migrate the raise sites**

Edit `src/loxmatter/model/store.py`. Add the import next to the existing `loxmatter.*` imports:

```python
from loxmatter import i18n
```

Change (around line 836):
```python
            raise UnknownDeviceError(f"unbekanntes Geraet {device_id}")
```
to:
```python
            raise UnknownDeviceError(i18n.t("api.errors.unknown_device", device_id=device_id))
```

(there are TWO such raise sites in `store.py` for `UnknownDeviceError` with this exact message — `device()` and `forget_device`-adjacent code; search for the literal string `unbekanntes Geraet {device_id}` and migrate every occurrence, not just the first one found)

Change (around line 1178):
```python
            raise UnknownCommandError(f"unbekannter Kommando-Schluessel {key!r}")
```
to:
```python
            raise UnknownCommandError(i18n.t("api.errors.unknown_command", command_key=key))
```

Edit `src/loxmatter/commands/translate.py`. Add the import:

```python
from loxmatter import i18n
```

Change both occurrences (lines 84 and 91) of:
```python
        raise UnsupportedValueError(f"Wert {value!r} ist keine Zahl") from exc
```
and
```python
        raise UnsupportedValueError(f"Wert {value!r} ist keine Zahl")
```
to:
```python
        raise UnsupportedValueError(i18n.t("api.errors.value_not_a_number", value=value)) from exc
```
and
```python
        raise UnsupportedValueError(i18n.t("api.errors.value_not_a_number", value=value))
```
(keep the `from exc` on the first, no chaining on the second, exactly as today)

Change (lines 132-134):
```python
        raise UnsupportedValueError(
            f"Cluster {command.cluster_id} Kommando {command.command_id} wird nicht unterstuetzt"
        )
```
to:
```python
        raise UnsupportedValueError(
            i18n.t(
                "api.errors.command_unsupported",
                cluster_id=command.cluster_id,
                command_id=command.command_id,
            )
        )
```

Edit `src/loxmatter/matter/client.py`. Add the import:

```python
from loxmatter import i18n
```

Change (lines 241-242):
```python
                msg = "Listener wurde beendet, bevor er Bereitschaft meldete"
                raise MatterUnavailableError(msg)
```
to:
```python
                msg = i18n.t("api.errors.listener_stopped_early")
                raise MatterUnavailableError(msg)
```

Change (lines 244-248):
```python
            msg = (
                f"matter-server hat nach {LISTENER_READY_TIMEOUT_SECONDS:.0f}s "
                "keine Bereitschaft gemeldet"
            )
            raise MatterUnavailableError(msg)
```
to:
```python
            msg = i18n.t(
                "api.errors.listener_timeout", timeout=LISTENER_READY_TIMEOUT_SECONDS
            )
            raise MatterUnavailableError(msg)
```

Change (line 330):
```python
            raise MatterUnavailableError("nicht verbunden mit matter-server")
```
to:
```python
            raise MatterUnavailableError(i18n.t("api.errors.not_connected"))
```

Change (line 355):
```python
        raise MatterUnavailableError(f"unbekannter Node {node_id}")
```
to:
```python
        raise MatterUnavailableError(i18n.t("api.errors.unknown_node", node_id=node_id))
```

Change (lines 386-387):
```python
            msg = f"matter-server nicht erreichbar: {exc}"
            raise MatterUnavailableError(msg) from exc
```
to:
```python
            msg = i18n.t("api.errors.matter_server_unreachable", exc=exc)
            raise MatterUnavailableError(msg) from exc
```

Change (line 389):
```python
            raise CommissioningError(f"Einlernen fehlgeschlagen: {exc}") from exc
```
to:
```python
            raise CommissioningError(i18n.t("api.errors.commissioning_failed", exc=exc)) from exc
```

Change (lines 438-440):
```python
            raise MatterUnavailableError(
                f"Cluster {call.cluster_id} Kommando {call.command_id} ist der chip-SDK unbekannt"
            )
```
to:
```python
            raise MatterUnavailableError(
                i18n.t(
                    "api.errors.command_unknown_to_sdk",
                    cluster_id=call.cluster_id,
                    command_id=call.command_id,
                )
            )
```

Change (line 470):
```python
            raise MatterUnavailableError("subscribe() wurde bereits aufgerufen")
```
to:
```python
            raise MatterUnavailableError(i18n.t("api.errors.subscribe_already_called"))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/model/test_store_error_messages.py tests/commands/test_translate_error_messages.py tests/matter/test_client_error_messages.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`
Expected: all pass. These exception messages are exercised by EXISTING tests elsewhere too (`tests/model/test_store.py`, `tests/commands/test_translate.py`, `tests/matter/test_client.py`, and any `tests/api/*.py` test that triggers a 404/400/502/422 through one of these paths) — search for any assertion on the OLD German text in those files and update it the same way as Phase A's Task 4/5 did (English by default, German companion via `i18n.set_language("de")`), rather than leaving a stale assertion to fail.

Run: `uv run ruff check src/loxmatter/model/store.py src/loxmatter/commands/translate.py src/loxmatter/matter/client.py`
Run: `uv run mypy src/loxmatter/model/store.py src/loxmatter/commands/translate.py src/loxmatter/matter/client.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/model/store.py src/loxmatter/commands/translate.py src/loxmatter/matter/client.py src/loxmatter/i18n/strings.yaml tests/model/test_store_error_messages.py tests/commands/test_translate_error_messages.py tests/matter/test_client_error_messages.py
git commit -m "$(cat <<'EOF'
feat(i18n): Exception-Texte an ihrer Quelle uebersetzt

UnknownDeviceError/UnknownCommandError (store.py), UnsupportedValueError
(commands/translate.py) und MatterUnavailableError/CommissioningError
(matter/client.py) tragen jetzt i18n.t()-Text statt hartkodiertem
Deutsch - str(exc) reicht das automatisch in jede HTTPException(detail=
str(exc))-Stelle weiter, die diese Ausnahmen faengt (api/control.py,
api/devices.py, api/export.py), ohne dass diese Dateien selbst
angefasst werden muessen. Zwei nicht HTTP-erreichbare ValueError/
KeyError-Faelle bewusst ausgenommen (siehe Aufgabentext).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `control.py` + `devices.py` — own `detail=` strings

Task 2 already migrated every exception these two routers merely forward via `str(exc)`. This task covers the strings `control.py`/`devices.py` author themselves. Both files share two near-identical patterns ("X belongs to removed device Y", "unknown signal key") — one shared key each, not two independent ones.

**Files:**
- Modify: `src/loxmatter/api/control.py`, `src/loxmatter/api/devices.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_control.py`, `tests/api/test_devices.py` (read both first — mirror their existing fixture/assertion style, add English-default + German-companion pairs for every literal below, same pattern as Phase A Task 4/5)

**Interfaces:**
- Consumes: `i18n.t` (Phase A), the `api.errors.*` keys already used by `str(exc)` forwarding (Task 2) — this task adds new keys, does not touch those.

- [ ] **Step 1: Write the failing tests**

Read `tests/api/test_control.py` and `tests/api/test_devices.py` first. For every literal changed below, update or add a test asserting the new English text, and add a German-companion test (`i18n.set_language("de")` before the request) asserting the original German text — copy each test's exact existing request setup, do not invent new fixtures.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_control.py tests/api/test_devices.py -v`
Expected: FAIL on the new/changed assertions (still-German source)

- [ ] **Step 3: Add the new `strings.yaml` keys**

```yaml
api.errors.device_unreachable:
  en: "device unreachable: {exc}"
  de: "Geraet nicht erreichbar: {exc}"
api.errors.command_belongs_to_removed_device:
  en: "command {command_key!r} belongs to device {device_id}, which was removed"
  de: "Kommando {command_key!r} gehoert zu Geraet {device_id}, das entfernt wurde"
api.errors.unknown_signal_key:
  en: "unknown signal key {signal_key!r}"
  de: "unbekannter Signal-Schluessel {signal_key!r}"
api.errors.signal_belongs_to_removed_device:
  en: "signal {signal_key!r} belongs to device {device_id}, which was removed"
  de: "Signal {signal_key!r} gehoert zu Geraet {device_id}, das entfernt wurde"
api.control.fail_not_writable:
  en: "The writability of attribute {signal_key!r} cannot be confirmed, so it is not on the allow-list of writable attributes. If it really is writable, it can be added there."
  de: "Die Beschreibbarkeit von Attribut {signal_key!r} laesst sich nicht bestaetigen, es steht deshalb nicht auf der Erlaubnisliste beschreibbarer Attribute. Ist es tatsaechlich beschreibbar, kann es dort ergaenzt werden."
api.control.fail_not_wired:
  en: "Attribute {signal_key!r} is writable, but raw writing is not yet connected to matter-server."
  de: "Attribut {signal_key!r} ist beschreibbar, aber das rohe Schreiben ist noch nicht an matter-server angebunden."
api.devices.fail_no_matter_client:
  en: "No matter-server client configured — the bridge is running without a Matter connection"
  de: "Matter-Client nicht verfuegbar - die Bruecke laeuft ohne Verbindung zu matter-server"
```

- [ ] **Step 4: Migrate the call sites**

Edit `src/loxmatter/api/control.py`. Add `from loxmatter import i18n` next to the existing imports. Then:

```python
        try:
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Kommando {key!r} gehoert zu Geraet {stored.device_id}, das entfernt wurde",
            ) from exc
```
→
```python
        try:
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.command_belongs_to_removed_device",
                    command_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc
```

```python
            logger.exception("Matter-Aufruf fuer Schluessel %r fehlgeschlagen", key)
            raise HTTPException(status_code=502, detail=f"Geraet nicht erreichbar: {exc}") from exc
```
→
```python
            logger.exception("Matter-Aufruf fuer Schluessel %r fehlgeschlagen", key)
            raise HTTPException(
                status_code=502, detail=i18n.t("api.errors.device_unreachable", exc=exc)
            ) from exc
```

```python
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"unbekannter Signal-Schluessel {key!r}")
```
→
```python
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(
                status_code=404, detail=i18n.t("api.errors.unknown_signal_key", signal_key=key)
            )
```

```python
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Signal {key!r} gehoert zu Geraet {stored.device_id}, das entfernt wurde",
            ) from exc
```
→
```python
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.signal_belongs_to_removed_device",
                    signal_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc
```

Note: `t()`'s own first parameter is itself named `key` (the translation lookup key, e.g. `"api.errors.unknown_signal_key"`) — calling `i18n.t("some.key", key=...)` collides with it (`TypeError: t() got multiple values for argument 'key'`), which is why every placeholder above is named `signal_key`/`command_key`, never bare `key`. Task 2's implementer already hit and fixed this exact collision for `api.errors.unknown_command`; apply the same care here.

```python
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Die Beschreibbarkeit von Attribut {key!r} laesst sich nicht "
                    "bestaetigen, es steht deshalb nicht auf der Erlaubnisliste "
                    "beschreibbarer Attribute. Ist es tatsaechlich beschreibbar, kann "
                    "es dort ergaenzt werden."
                ),
            )
```
→
```python
            raise HTTPException(
                status_code=400,
                detail=i18n.t("api.control.fail_not_writable", signal_key=key),
            )
```

```python
        raise HTTPException(
            status_code=501,
            detail=(
                f"Attribut {key!r} ist beschreibbar, aber das rohe Schreiben ist noch "
                "nicht an matter-server angebunden."
            ),
        )
```
→
```python
        raise HTTPException(
            status_code=501,
            detail=i18n.t("api.control.fail_not_wired", signal_key=key),
        )
```

Edit `src/loxmatter/api/devices.py`. Add `from loxmatter import i18n` next to the existing imports. Then:

```python
    def _require_client() -> BridgeMatterClient:
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="Matter-Client nicht verfuegbar - die Bruecke laeuft ohne Verbindung"
                " zu matter-server",
            )
        return client
```
→
```python
    def _require_client() -> BridgeMatterClient:
        if client is None:
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.devices.fail_no_matter_client"),
            )
        return client
```

```python
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"unbekannter Signal-Schluessel {key!r}")
```
→
```python
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(
                status_code=404, detail=i18n.t("api.errors.unknown_signal_key", signal_key=key)
            )
```

```python
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Signal {key!r} gehoert zu Geraet {stored.device_id}, das entfernt wurde",
            ) from exc
```
→
```python
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.signal_belongs_to_removed_device",
                    signal_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc
```

(same `key`-collision note as above applies to both calls here too — `signal_key=key`, never bare `key=key`)

(the remaining `HTTPException(status_code=..., detail=str(exc))` call sites in both files — `_require_device`, `commission_device`'s three except-branches, `remove_device` — forward exceptions already migrated in Task 2; leave them untouched)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_control.py tests/api/test_devices.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`

Run: `uv run ruff check src/loxmatter/api/control.py src/loxmatter/api/devices.py`
Run: `uv run mypy src/loxmatter/api/control.py src/loxmatter/api/devices.py`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/control.py src/loxmatter/api/devices.py src/loxmatter/i18n/strings.yaml tests/api/test_control.py tests/api/test_devices.py
git commit -m "$(cat <<'EOF'
feat(api): control.py und devices.py ueber t() uebersetzt

Eigene detail=-Texte beider Router migriert - die von ihnen nur
weitergereichten Ausnahmetexte (UnknownDeviceError, UnsupportedValueError,
MatterUnavailableError, CommissioningError) traegt bereits Task 2. Zwei
Zeichenketten (unknown_signal_key, signal_belongs_to_removed_device)
teilen sich beide Dateien, statt sie zu verdoppeln.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `auth.py` — own `detail=` strings

**Files:**
- Modify: `src/loxmatter/api/auth.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_auth.py`

**Interfaces:**
- Consumes: `i18n.t`.

- [ ] **Step 1: Write the failing tests**

Read `tests/api/test_auth.py` first, mirror its style. Add English-default + German-companion pairs for the six literals migrated below.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: FAIL on the new/changed assertions

- [ ] **Step 3: Add the new `strings.yaml` keys**

```yaml
api.auth.fail_password_too_short:
  en: "The password must be at least {min_length} characters long."
  de: "Das Passwort muss mindestens {min_length} Zeichen haben."
api.auth.fail_too_many_attempts:
  en: "Too many failed attempts – try again in {wait} seconds."
  de: "Zu viele Fehlversuche – in {wait} Sekunden wieder möglich."
api.auth.fail_already_set_up:
  en: "A password has already been set for this service – initial setup is therefore permanently complete. Forgot the password? In the reference deployment, `docker compose exec loxmatter loxmatter set-password` resets it; for a source install, `uv run loxmatter set-password`."
  de: "Für diesen Dienst ist bereits ein Passwort vergeben – die Ersteinrichtung ist damit dauerhaft abgeschlossen. Passwort vergessen? Im Referenz-Deployment setzt `docker compose exec loxmatter loxmatter set-password` es neu; bei einer Installation aus dem Quellcode `uv run loxmatter set-password`."
api.auth.fail_no_password_set:
  en: "No password has been set for this service yet – please complete initial setup first."
  de: "Für diesen Dienst ist noch kein Passwort vergeben – bitte zuerst die Ersteinrichtung abschließen."
api.auth.fail_wrong_password:
  en: "Wrong password."
  de: "Falsches Passwort."
```

Note: the backtick-quoted shell commands inside `api.auth.fail_already_set_up` stay identical in both languages — they are commands, not prose.

- [ ] **Step 4: Migrate the call sites**

Edit `src/loxmatter/api/auth.py`. Add `from loxmatter import i18n` next to the existing imports.

Delete the module-level constant entirely:
```python
_ALREADY_SET_UP_DETAIL = (
    "Für diesen Dienst ist bereits ein Passwort vergeben – die Ersteinrichtung "
    "ist damit dauerhaft abgeschlossen. Passwort vergessen? Im Referenz-"
    "Deployment setzt `docker compose exec loxmatter loxmatter set-password` "
    "es neu; bei einer Installation aus dem Quellcode `uv run loxmatter "
    "set-password`."
)
```
Both its call sites below now call `i18n.t("api.auth.fail_already_set_up")` directly, resolved fresh (in the correct language) at each raise rather than frozen at module-import time as the old constant was.

```python
    def _require_length(password: str) -> None:
        """Eigene Pruefung statt `Field(min_length=...)` am Modell: die
        Meldung landet in der Oberflaeche und soll dort auf Deutsch stehen
        und sagen, was zu tun ist - nicht als pydantic-Fehlerliste."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."),
            )
```
→
```python
    def _require_length(password: str) -> None:
        """Eigene Pruefung statt `Field(min_length=...)` am Modell: die
        Meldung landet in der Oberflaeche und soll dort in der eingestellten
        Sprache stehen und sagen, was zu tun ist - nicht als pydantic-
        Fehlerliste."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=i18n.t("api.auth.fail_password_too_short", min_length=MIN_PASSWORD_LENGTH),
            )
```

In BOTH `setup` and `login` (the identical block appears once in each):
```python
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Zu viele Fehlversuche – in {wait} Sekunden wieder möglich.",
            )
```
→
```python
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=i18n.t("api.auth.fail_too_many_attempts", wait=wait),
            )
```

In `setup`, both `raise HTTPException(status_code=409, detail=_ALREADY_SET_UP_DETAIL)` lines become `raise HTTPException(status_code=409, detail=i18n.t("api.auth.fail_already_set_up"))` — everything else in that function body, including its German reasoning comments, stays exactly as-is.

In `login`:
```python
        stored = store.auth.password_hash()
        if stored is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Für diesen Dienst ist noch kein Passwort vergeben – bitte zuerst "
                    "die Ersteinrichtung abschließen."
                ),
            )
```
→
```python
        stored = store.auth.password_hash()
        if stored is None:
            raise HTTPException(
                status_code=409,
                detail=i18n.t("api.auth.fail_no_password_set"),
            )
```

```python
        if not await anyio.to_thread.run_sync(
            verify_password, body.password, stored, limiter=_PASSWORD_HASH_LIMITER
        ):
            raise HTTPException(status_code=401, detail="Falsches Passwort.")
```
→
```python
        if not await anyio.to_thread.run_sync(
            verify_password, body.password, stored, limiter=_PASSWORD_HASH_LIMITER
        ):
            raise HTTPException(status_code=401, detail=i18n.t("api.auth.fail_wrong_password"))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`

Run: `uv run ruff check src/loxmatter/api/auth.py`
Run: `uv run mypy src/loxmatter/api/auth.py`
Expected: all clean. Also `grep -rn _ALREADY_SET_UP_DETAIL src/ tests/` to confirm nothing else references the deleted constant by name.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/auth.py src/loxmatter/i18n/strings.yaml tests/api/test_auth.py
git commit -m "$(cat <<'EOF'
feat(api): auth.py ueber t() uebersetzt

Alle sechs eigenen detail=-Texte migriert. _ALREADY_SET_UP_DETAIL als
Modul-Konstante entfernt - sie fror den Text beim Modulimport in genau
einer Sprache ein; beide Aufrufstellen holen ihn jetzt frisch ueber
i18n.t().

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `diagnostics.py` — system checks and fabric-backup errors

**Files:**
- Modify: `src/loxmatter/api/diagnostics.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_diagnostics.py`

**Interfaces:**
- Consumes: `i18n.t`.

- [ ] **Step 1: Write the failing tests**

Read `tests/api/test_diagnostics.py` first, mirror its style — most checks are exercised via `_check_matter_server`/`_check_store`/`_check_ipv6`/`_check_thread`/`_check_miniserver` called directly, or via `GET /api/diagnostics/system`. Add English-default + German-companion pairs for the literals migrated below; only migrate coverage that already exists, don't invent new branch coverage.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_diagnostics.py -v`
Expected: FAIL on the new/changed assertions

- [ ] **Step 3: Add the new `strings.yaml` keys**

```yaml
api.diagnostics.check_failed:
  en: "This check itself failed ({exc}) — that is a bug in the check, not necessarily in the checked system. The full traceback is in the server log."
  de: "Diese Pruefung selbst ist fehlgeschlagen ({exc}) - das ist ein Fehler in der Pruefung, nicht zwangslaeufig im gepruerften System. Der volle Traceback steht im Server-Log."
api.diagnostics.matter_server_not_configured:
  en: "No matter-server client configured — the bridge is running without a Matter connection. This is always set for `loxmatter run`; if it's missing here, this service was started with an incomplete setup."
  de: "Kein matter-server-Client konfiguriert - die Bruecke laeuft ohne Matter-Anbindung. Das ist bei `loxmatter run` immer gesetzt; fehlt es hier, wurde dieser Dienst mit einem unvollstaendigen Aufbau gestartet."
api.diagnostics.matter_server_not_connected:
  en: "No active connection to matter-server. Is the service running (e.g. `docker compose ps matter-server`)? Is the --url address from `loxmatter run` still reachable?"
  de: "Keine aktive Verbindung zu matter-server. Laeuft der Dienst (z. B. `docker compose ps matter-server`)? Ist die --url-Adresse aus `loxmatter run` noch erreichbar?"
api.diagnostics.connected:
  en: "Connected."
  de: "Verbunden."
api.diagnostics.store_not_writable:
  en: "The signal-key database is not writable ({exc}). Check the disk space and file permissions of the mounted data volume — without write access, no new device can be commissioned and no export can be recorded."
  de: "Die Signalschluessel-Datenbank ist nicht beschreibbar ({exc}). Pruefen Sie Speicherplatz und Dateirechte des eingehaengten Datenvolumes - ohne Schreibzugriff kann kein neues Geraet eingelernt und kein Export vermerkt werden."
api.diagnostics.writable:
  en: "Writable."
  de: "Beschreibbar."
api.diagnostics.no_ipv6_support:
  en: "This Python installation was built without IPv6 support."
  de: "Diese Python-Installation wurde ohne IPv6-Unterstuetzung gebaut."
api.diagnostics.ipv6_not_determinable:
  en: "Not determinable: /proc/net/if_inet6 does not exist on this system (not Linux). In the container, the bridge runs under Linux, where this check applies."
  de: "Nicht feststellbar: /proc/net/if_inet6 gibt es auf diesem System nicht (kein Linux). Im Container laeuft die Bruecke unter Linux, dort greift die Pruefung."
api.diagnostics.no_routed_ipv6:
  en: "No routed IPv6 address found — only link-local or loopback. Matter/Thread devices are therefore unreachable. Is the Thread Border Router running, and is it announcing its prefix?"
  de: "Keine geroutete IPv6-Adresse gefunden - nur link-lokale oder Loopback. Matter/Thread-Geraete sind damit nicht erreichbar. Laeuft der Thread-Border-Router, und kuendigt er sein Praefix an?"
api.diagnostics.ipv6_address_on_interface:
  en: "{address} on {interface}"
  de: "{address} auf {interface}"
api.diagnostics.ipv6_more_addresses:
  en: " (and {count} more)"
  de: " (und {count} weitere)"
api.diagnostics.routed_ipv6_found:
  en: "Routed IPv6 addresses present: {shown}{more}."
  de: "Geroutete IPv6-Adressen vorhanden: {shown}{more}."
api.diagnostics.thread_not_determinable:
  en: "Not determinable: /proc/net/if_inet6 does not exist on this system (not Linux)."
  de: "Nicht feststellbar: /proc/net/if_inet6 gibt es auf diesem System nicht (kein Linux)."
api.diagnostics.no_thread_interface:
  en: "No Thread interface ({prefix}*) with a mesh address found. Is the OTBR agent running? It aborts on an RCP timeout — when the radio module stops responding — without the container ending, and is then not restarted by anyone. `docker compose restart otbr` brings it back."
  de: "Keine Thread-Schnittstelle ({prefix}*) mit einer Mesh-Adresse gefunden. Laeuft der OTBR-Agent? Er bricht bei einem RCP-Timeout ab - wenn das Funkmodul nicht mehr antwortet -, ohne dass der Container endet, und wird dann von niemandem neu gestartet. `docker compose restart otbr` holt ihn zurueck."
api.diagnostics.thread_found:
  en: "Thread interface {interface} has {count} mesh address(es), e.g. {example}."
  de: "Thread-Schnittstelle {interface} hat {count} Mesh-Adresse(n), z. B. {example}."
api.diagnostics.no_udp_sender:
  en: "No UDP sender configured — the bridge is not sending values to the Miniserver. This is always set for `loxmatter run`; if it's missing here, this service was started with an incomplete setup."
  de: "Kein UDP-Sender konfiguriert - die Bruecke sendet keine Werte an den Miniserver. Das ist bei `loxmatter run` immer gesetzt; fehlt es hier, wurde dieser Dienst mit einem unvollstaendigen Aufbau gestartet."
api.diagnostics.no_network_path:
  en: "No network path to {host}:{port} found ({exc}). Is the Miniserver switched on and reachable on the same network? Do the IP and port match `loxmatter run --miniserver`/`--port`?"
  de: "Kein Netzwerkpfad zu {host}:{port} gefunden ({exc}). Ist der Miniserver eingeschaltet und im selben Netz erreichbar? Stimmen IP und Port aus `loxmatter run --miniserver`/`--port`?"
api.diagnostics.network_path_exists:
  en: "A network path to {host}:{port} exists. This only confirms routing, not actual delivery — the Miniserver does not evaluate UDP responses."
  de: "Ein Netzwerkpfad zu {host}:{port} existiert. Das bestaetigt nur Routing, keine tatsaechliche Zustellung - der Miniserver wertet UDP-Antworten nicht aus."
api.diagnostics.fabric_backup_not_mounted:
  en: "The matter-server data directory is not mounted for this service — a backup therefore cannot be created. See the deployment (docker-compose.yml, --matter-data-dir)."
  de: "Das matter-server-Datenverzeichnis ist fuer diesen Dienst nicht eingehaengt - eine Sicherung kann deshalb nicht erstellt werden. Siehe die Bereitstellung (docker-compose.yml, --matter-data-dir)."
api.diagnostics.fabric_backup_dir_missing:
  en: "The configured matter-server data directory does not exist or is not a directory. Check the mount."
  de: "Das konfigurierte matter-server-Datenverzeichnis existiert nicht oder ist kein Verzeichnis. Pruefen Sie die Einhaengung."
```

- [ ] **Step 4: Migrate the call sites**

Edit `src/loxmatter/api/diagnostics.py`. Add `from loxmatter import i18n` next to the existing imports. Then, function by function:

`_run_check`'s except-branch:
```python
        return SystemCheckOut(
            name=name,
            ok=False,
            detail=(
                f"Diese Pruefung selbst ist fehlgeschlagen ({exc}) - das ist ein Fehler in "
                "der Pruefung, nicht zwangslaeufig im gepruerften System. Der volle "
                "Traceback steht im Server-Log."
            ),
        )
```
→
```python
        return SystemCheckOut(
            name=name,
            ok=False,
            detail=i18n.t("api.diagnostics.check_failed", exc=exc),
        )
```

`_check_matter_server`:
```python
def _check_matter_server(client: BridgeMatterClient | None) -> tuple[bool, str]:
    if client is None:
        return False, (
            "Kein matter-server-Client konfiguriert - die Bruecke laeuft ohne Matter-"
            "Anbindung. Das ist bei `loxmatter run` immer gesetzt; fehlt es hier, "
            "wurde dieser Dienst mit einem unvollstaendigen Aufbau gestartet."
        )
    if not client.connected:
        return False, (
            "Keine aktive Verbindung zu matter-server. Laeuft der Dienst "
            "(z. B. `docker compose ps matter-server`)? Ist die --url-Adresse aus "
            "`loxmatter run` noch erreichbar?"
        )
    return True, "Verbunden."
```
→
```python
def _check_matter_server(client: BridgeMatterClient | None) -> tuple[bool, str]:
    if client is None:
        return False, i18n.t("api.diagnostics.matter_server_not_configured")
    if not client.connected:
        return False, i18n.t("api.diagnostics.matter_server_not_connected")
    return True, i18n.t("api.diagnostics.connected")
```

`_check_store`:
```python
    try:
        store.check_writable()
    except sqlite3.Error as exc:
        return False, (
            f"Die Signalschluessel-Datenbank ist nicht beschreibbar ({exc}). Pruefen Sie "
            "Speicherplatz und Dateirechte des eingehaengten Datenvolumes - ohne "
            "Schreibzugriff kann kein neues Geraet eingelernt und kein Export vermerkt "
            "werden."
        )
    return True, "Beschreibbar."
```
→
```python
    try:
        store.check_writable()
    except sqlite3.Error as exc:
        return False, i18n.t("api.diagnostics.store_not_writable", exc=exc)
    return True, i18n.t("api.diagnostics.writable")
```

`_check_ipv6` (the `...` below stands for the function's existing docstring — leave it untouched):
```python
def _check_ipv6() -> tuple[bool, str]:
    ...
    if not socket.has_ipv6:
        return False, "Diese Python-Installation wurde ohne IPv6-Unterstuetzung gebaut."
    addresses = _routed_ipv6_addresses()
    if addresses is None:
        return True, (
            "Nicht feststellbar: /proc/net/if_inet6 gibt es auf diesem System nicht "
            "(kein Linux). Im Container laeuft die Bruecke unter Linux, dort greift "
            "die Pruefung."
        )
    if not addresses:
        return False, (
            "Keine geroutete IPv6-Adresse gefunden - nur link-lokale oder Loopback. "
            "Matter/Thread-Geraete sind damit nicht erreichbar. Laeuft der "
            "Thread-Border-Router, und kuendigt er sein Praefix an?"
        )
    shown = ", ".join(f"{address} auf {interface}" for address, interface in addresses[:3])
    more = f" (und {len(addresses) - 3} weitere)" if len(addresses) > 3 else ""
    return True, f"Geroutete IPv6-Adressen vorhanden: {shown}{more}."
```
→
```python
def _check_ipv6() -> tuple[bool, str]:
    ...
    if not socket.has_ipv6:
        return False, i18n.t("api.diagnostics.no_ipv6_support")
    addresses = _routed_ipv6_addresses()
    if addresses is None:
        return True, i18n.t("api.diagnostics.ipv6_not_determinable")
    if not addresses:
        return False, i18n.t("api.diagnostics.no_routed_ipv6")
    shown = ", ".join(
        i18n.t("api.diagnostics.ipv6_address_on_interface", address=address, interface=interface)
        for address, interface in addresses[:3]
    )
    more = (
        i18n.t("api.diagnostics.ipv6_more_addresses", count=len(addresses) - 3)
        if len(addresses) > 3
        else ""
    )
    return True, i18n.t("api.diagnostics.routed_ipv6_found", shown=shown, more=more)
```

`_check_thread`:
```python
    addresses = _routed_ipv6_addresses()
    if addresses is None:
        return True, (
            "Nicht feststellbar: /proc/net/if_inet6 gibt es auf diesem System nicht (kein Linux)."
        )
    thread = [
        (address, interface)
        for address, interface in addresses
        if interface.startswith(_THREAD_INTERFACE_PREFIX)
    ]
    if not thread:
        return False, (
            f"Keine Thread-Schnittstelle ({_THREAD_INTERFACE_PREFIX}*) mit einer "
            "Mesh-Adresse gefunden. Laeuft der OTBR-Agent? Er bricht bei einem "
            "RCP-Timeout ab - wenn das Funkmodul nicht mehr antwortet -, ohne dass "
            "der Container endet, und wird dann von niemandem neu gestartet. "
            "`docker compose restart otbr` holt ihn zurueck."
        )
    return True, (
        f"Thread-Schnittstelle {thread[0][1]} hat {len(thread)} Mesh-Adresse(n), "
        f"z. B. {thread[0][0]}."
    )
```
→
```python
    addresses = _routed_ipv6_addresses()
    if addresses is None:
        return True, i18n.t("api.diagnostics.thread_not_determinable")
    thread = [
        (address, interface)
        for address, interface in addresses
        if interface.startswith(_THREAD_INTERFACE_PREFIX)
    ]
    if not thread:
        return False, i18n.t(
            "api.diagnostics.no_thread_interface", prefix=_THREAD_INTERFACE_PREFIX
        )
    return True, i18n.t(
        "api.diagnostics.thread_found",
        interface=thread[0][1],
        count=len(thread),
        example=thread[0][0],
    )
```

`_check_miniserver`:
```python
    if sender is None:
        return False, (
            "Kein UDP-Sender konfiguriert - die Bruecke sendet keine Werte an den "
            "Miniserver. Das ist bei `loxmatter run` immer gesetzt; fehlt es hier, wurde "
            "dieser Dienst mit einem unvollstaendigen Aufbau gestartet."
        )
    host, port = sender.target
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, port))
    except OSError as exc:
        return False, (
            f"Kein Netzwerkpfad zu {host}:{port} gefunden ({exc}). Ist der Miniserver "
            "eingeschaltet und im selben Netz erreichbar? Stimmen IP und Port aus "
            "`loxmatter run --miniserver`/`--port`?"
        )
    return True, (
        f"Ein Netzwerkpfad zu {host}:{port} existiert. Das bestaetigt nur Routing, keine "
        "tatsaechliche Zustellung - der Miniserver wertet UDP-Antworten nicht aus."
    )
```
→
```python
    if sender is None:
        return False, i18n.t("api.diagnostics.no_udp_sender")
    host, port = sender.target
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, port))
    except OSError as exc:
        return False, i18n.t("api.diagnostics.no_network_path", host=host, port=port, exc=exc)
    return True, i18n.t("api.diagnostics.network_path_exists", host=host, port=port)
```

`fabric_backup`:
```python
        if matter_data_dir is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Das matter-server-Datenverzeichnis ist fuer diesen Dienst nicht "
                    "eingehaengt - eine Sicherung kann deshalb nicht erstellt werden. "
                    "Siehe die Bereitstellung (docker-compose.yml, --matter-data-dir)."
                ),
            )
        if not matter_data_dir.is_dir():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Das konfigurierte matter-server-Datenverzeichnis existiert nicht "
                    "oder ist kein Verzeichnis. Pruefen Sie die Einhaengung."
                ),
            )
```
→
```python
        if matter_data_dir is None:
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.diagnostics.fabric_backup_not_mounted"),
            )
        if not matter_data_dir.is_dir():
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.diagnostics.fabric_backup_dir_missing"),
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_diagnostics.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`

Run: `uv run ruff check src/loxmatter/api/diagnostics.py`
Run: `uv run mypy src/loxmatter/api/diagnostics.py`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/diagnostics.py src/loxmatter/i18n/strings.yaml tests/api/test_diagnostics.py
git commit -m "$(cat <<'EOF'
feat(api): diagnostics.py ueber t() uebersetzt

Systemcheck-Texte (matter-server, Store, IPv6, Thread, Miniserver) und
die beiden 503-Meldungen der Fabric-Sicherung migriert. Die
IPv6-Adressliste baut ihre Teiltexte ("auf", "und ... weitere") jetzt
ebenfalls ueber t() zusammen statt sie im f-String einzubetten.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `export.py` (API) + `server.py`'s remaining own strings

**Scope note:** `api/export.py`'s `Query(..., description=...)` parameter descriptions (on `preview`/`download`) are OpenAPI schema metadata only — `build_app` constructs `FastAPI(title="loxmatter", docs_url=None, redoc_url=None)`, so the auto-generated docs UI that would ever render them is disabled. They are not reachable by any user in this deployment; leave them as-is, do not migrate them (out of scope, not user-facing).

`export.py`'s only user-facing string is `_README_TEXT` (the `Import-Anleitung.txt` bundled into every export ZIP — WebUI-only, the CLI's `export` command does not produce this file). `server.py`'s remaining own strings (not already covered by Tasks 1-5): `build_api_guard`'s 401 detail, `/resync`'s 502 detail, and `/cmd`'s 502 detail (which reuses Task 3's `api.errors.device_unreachable` key — identical wording to `control.py`'s `execute_command`).

**Files:**
- Modify: `src/loxmatter/api/export.py`, `src/loxmatter/loxone/server.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_export_api.py`, `tests/api/test_security.py`, `tests/api/test_web.py` (or wherever `/cmd`/`/resync`/the guard's 401 text is currently asserted — grep first, don't guess)

**Interfaces:**
- Consumes: `i18n.t`, `api.errors.device_unreachable` (Task 3).

- [ ] **Step 1: Write the failing tests**

Read the relevant existing test files first (`tests/api/test_export_api.py` for the README text inside the ZIP; grep `tests/api/` for the literal `Anmeldung erforderlich` and `Full-Resend fehlgeschlagen` to find where the guard's 401 and `/resync`'s 502 are already asserted). Add English-default + German-companion pairs for each.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_export_api.py tests/api/test_security.py tests/api/test_web.py -v`
Expected: FAIL on the new/changed assertions (adjust the file list above to whichever files actually contain the matches from Step 1's grep)

- [ ] **Step 3: Add the new `strings.yaml` keys**

```yaml
api.export.readme_text:
  en: |
    IMPORT INSTRUCTIONS
    ====================

    This ZIP file contains Loxone templates generated by loxmatter.

    1. Files starting with "VIU_" belong in Loxone Config under:
         Templates\VirtualIn\

    2. Files starting with "VO_" belong under:
         Templates\VirtualOut\

    3. Import into Loxone Config: in the project tree, right-click the
       relevant folder (Virtual Inputs or Virtual Outputs) ->
       "Import Template" -> select the file.

    4. VIU_Matter_System.xml and VO_Matter_System.xml (if included in this
       ZIP file) do not belong to any single device. They are only needed
       ONCE per project — do not import them again with every further
       export.
  de: |
    IMPORT-ANLEITUNG
    =================

    Diese ZIP-Datei enthaelt Loxone-Vorlagen, erzeugt von loxmatter.

    1. Dateien, die mit "VIU_" beginnen, gehoeren in Loxone Config nach:
         Templates\VirtualIn\

    2. Dateien, die mit "VO_" beginnen, gehoeren nach:
         Templates\VirtualOut\

    3. Import in Loxone Config: im Projektbaum Rechtsklick auf den
       jeweiligen Ordner (Virtuelle Eingaenge bzw. Virtuelle Ausgaenge) ->
       "Vorlage importieren" -> die Datei auswaehlen.

    4. VIU_Matter_System.xml und VO_Matter_System.xml (falls in dieser
       ZIP-Datei enthalten) gehoeren zu keinem einzelnen Geraet. Sie werden
       nur EINMAL pro Projekt gebraucht - nicht bei jedem weiteren Export
       erneut importieren.
api.server.fail_login_required:
  en: "Sign-in required — please open the interface and log in. Scripts use `Authorization: Bearer <Token>` with the value set under LOXMATTER_API_TOKEN."
  de: "Anmeldung erforderlich – bitte die Oberfläche öffnen und anmelden. Skripte verwenden `Authorization: Bearer <Token>` mit dem unter LOXMATTER_API_TOKEN gesetzten Wert."
api.server.fail_resync:
  en: "Full resend failed: {exc}"
  de: "Full-Resend fehlgeschlagen: {exc}"
```

Note: the YAML `|` block scalar preserves the exact line breaks of `_README_TEXT` (including the trailing newline each block-scalar line implies) — verify after Step 4 that `i18n.t("api.export.readme_text")` still ends with the SAME final-newline behavior the original `_README_TEXT` had before its `.replace("\n", "\r\n")` call (see Step 4), since the ZIP file's readability in Notepad depends on that CRLF conversion running against the same content shape as before.

- [ ] **Step 4: Migrate the call sites**

Edit `src/loxmatter/api/export.py`. Add `from loxmatter import i18n` next to the existing imports. Replace:
```python
_README_TEXT = (
    """\
IMPORT-ANLEITUNG
=================

Diese ZIP-Datei enthaelt Loxone-Vorlagen, erzeugt von loxmatter.

1. Dateien, die mit "VIU_" beginnen, gehoeren in Loxone Config nach:
     Templates\\VirtualIn\\

2. Dateien, die mit "VO_" beginnen, gehoeren nach:
     Templates\\VirtualOut\\

3. Import in Loxone Config: im Projektbaum Rechtsklick auf den
   jeweiligen Ordner (Virtuelle Eingaenge bzw. Virtuelle Ausgaenge) ->
   "Vorlage importieren" -> die Datei auswaehlen.

4. VIU_Matter_System.xml und VO_Matter_System.xml (falls in dieser
   ZIP-Datei enthalten) gehoeren zu keinem einzelnen Geraet. Sie werden
   nur EINMAL pro Projekt gebraucht - nicht bei jedem weiteren Export
   erneut importieren.
"""
).replace("\n", "\r\n")  # Notepad-freundlich - Loxone Config ist eine Windows-Anwendung.
```
with a function (a module-level constant can no longer work here, since the text must resolve in whatever language is current at ZIP-build time — see Task 1's `sync_language` middleware, which is what makes this correct for a request handled by the already-running server):
```python
def _readme_text() -> str:
    """Wie die alte Modul-Konstante `_README_TEXT`, aber pro Aufruf neu
    aufgeloest statt beim Modulimport eingefroren - dieselbe Begruendung wie
    beim Entfernen von `_ALREADY_SET_UP_DETAIL` in `api/auth.py` (Task 4)."""
    return i18n.t("api.export.readme_text").replace("\n", "\r\n")
```
Then change the one call site:
```python
            archive.writestr(_README_NAME, _README_TEXT)
```
to:
```python
            archive.writestr(_README_NAME, _readme_text())
```

Edit `src/loxmatter/loxone/server.py`. `i18n` is already imported (Task 1). Change, in `build_api_guard`'s `guard` function:
```python
        raise HTTPException(
            status_code=401,
            detail=(
                "Anmeldung erforderlich – bitte die Oberfläche öffnen und anmelden. "
                "Skripte verwenden `Authorization: Bearer <Token>` mit dem unter "
                "LOXMATTER_API_TOKEN gesetzten Wert."
            ),
        )
```
to:
```python
        raise HTTPException(
            status_code=401,
            detail=i18n.t("api.server.fail_login_required"),
        )
```

In the `/resync` handler:
```python
            logger.exception("Full-Resend ueber /resync fehlgeschlagen")
            raise HTTPException(
                status_code=502, detail=f"Full-Resend fehlgeschlagen: {exc}"
            ) from exc
```
to:
```python
            logger.exception("Full-Resend ueber /resync fehlgeschlagen")
            raise HTTPException(
                status_code=502, detail=i18n.t("api.server.fail_resync", exc=exc)
            ) from exc
```

In the `/cmd/{key}/{value}` handler:
```python
            logger.exception("Matter-Aufruf fuer Schluessel %r fehlgeschlagen", key)
            raise HTTPException(status_code=502, detail=f"Geraet nicht erreichbar: {exc}") from exc
```
to:
```python
            logger.exception("Matter-Aufruf fuer Schluessel %r fehlgeschlagen", key)
            raise HTTPException(
                status_code=502, detail=i18n.t("api.errors.device_unreachable", exc=exc)
            ) from exc
```
(`/cmd`'s other two branches — `except KeyError`/`except UnsupportedValueError` — already forward exception text migrated in Task 2; leave them untouched)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_export_api.py tests/api/test_security.py tests/api/test_web.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"` then `uv run pytest -q -m slow`

Run: `uv run ruff check src/loxmatter/api/export.py src/loxmatter/loxone/server.py`
Run: `uv run mypy src/loxmatter/api/export.py src/loxmatter/loxone/server.py`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/export.py src/loxmatter/loxone/server.py src/loxmatter/i18n/strings.yaml tests/api/test_export_api.py tests/api/test_security.py tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(api): export.py und server.py ueber t() uebersetzt

_README_TEXT (Import-Anleitung.txt im Export-ZIP) wird jetzt pro
Aufruf ueber i18n.t() aufgeloest statt einmalig beim Modulimport
eingefroren. build_api_guards 401-Text, /resyncs 502-Text und /cmds
502-Text (letzterer teilt sich mit control.py's execute_command)
ebenfalls migriert. Query(description=...)-Metadaten auf preview/
download bewusst nicht angefasst - die API-Dokumentation, die sie
anzeigen wuerde, ist abgeschaltet (docs_url=None).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Export-Vorlagen — Phase C (`export/documents.py`, `export/signals.py`)

**Scope note:** `f"Matter — {device_label}"` (both `Title` fields in `documents.py`) is NOT migrated — "Matter" is a protocol/product name, not German prose, and `device_label` is user data; there is no translatable text in that template. The `origin` strings passed to `emit()` in `signals.py` (`f"dem Impuls von {signal.key!r}"` etc.) are NOT migrated either — per the Global Constraints, these only ever surface inside the internal key-collision `ValueError` that Task 2 deliberately left untranslated (not HTTP-reachable, an invariant check).

**Files:**
- Modify: `src/loxmatter/export/documents.py`, `src/loxmatter/export/signals.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/export/test_documents.py`, `tests/export/test_signals.py`

**Interfaces:**
- Consumes: `i18n.t`.
- Produces: no signature changes to `render_virtual_in_udp`/`render_virtual_out`/`render_system_templates`/`to_inputs` — only the German literals inside them change to `i18n.t(...)` calls, resolved at call time using whatever language Task 1's middleware (WebUI) or Phase A's CLI bootstrap (CLI) currently has active.

- [ ] **Step 1: Write the failing tests**

Read `tests/export/test_documents.py` and `tests/export/test_signals.py` first, mirror their style. Add English-default + German-companion pairs (via `i18n.set_language("de")`) for every literal migrated below — these tests typically assert on a substring of the rendered XML bytes, so match that existing assertion style exactly.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/export/test_documents.py tests/export/test_signals.py -v`
Expected: FAIL on the new/changed assertions

- [ ] **Step 3: Add the new `strings.yaml` keys**

```yaml
export.comment_generated:
  en: "generated by loxmatter"
  de: "erzeugt von loxmatter"
export.system.bridge_alive_title:
  en: "Bridge reachable"
  de: "Bridge erreichbar"
export.system.bridge_alive_comment:
  en: "Watchdog: toggles as long as the bridge is running"
  de: "Watchdog: toggelt, solange die Bridge laeuft"
export.system.resync_title:
  en: "Resend all values"
  de: "Alle Werte neu senden"
export.signals.pulse_comment_suffix:
  en: "{comment} · pulse"
  de: "{comment} · Impuls"
export.signals.counter_title_suffix:
  en: "{title} counter"
  de: "{title} Zähler"
export.signals.counter_comment_suffix:
  en: "{comment} · counter"
  de: "{comment} · Zähler"
export.signals.online_title:
  en: "{device_label} reachable"
  de: "{device_label} erreichbar"
```

- [ ] **Step 4: Migrate the call sites**

Edit `src/loxmatter/export/documents.py`. Add `from loxmatter import i18n` next to the existing imports.

In `render_virtual_in_udp`:
```python
    return render_document(
        "VirtualInUdp",
        [
            ("Title", f"Matter — {device_label}"),
            ("Comment", "erzeugt von loxmatter"),
            ("Address", bridge_ip),
            ("Port", str(port)),
        ],
        [info, *children],
    )
```
→
```python
    return render_document(
        "VirtualInUdp",
        [
            ("Title", f"Matter — {device_label}"),
            ("Comment", i18n.t("export.comment_generated")),
            ("Address", bridge_ip),
            ("Port", str(port)),
        ],
        [info, *children],
    )
```

In `render_virtual_out`:
```python
    return render_document(
        "VirtualOut",
        [
            ("HintText", ""),
            ("Title", f"Matter — {device_label}"),
            ("Comment", "erzeugt von loxmatter"),
            ("Address", base_url),
            ("CmdInit", ""),
            ("CloseAfterSend", "true"),
            ("CmdSep", ""),
        ],
        [info, *children],
    )
```
→
```python
    return render_document(
        "VirtualOut",
        [
            ("HintText", ""),
            ("Title", f"Matter — {device_label}"),
            ("Comment", i18n.t("export.comment_generated")),
            ("Address", base_url),
            ("CmdInit", ""),
            ("CloseAfterSend", "true"),
            ("CmdSep", ""),
        ],
        [info, *children],
    )
```

In `render_system_templates`:
```python
    viu = render_virtual_in_udp(
        "System",
        bridge_ip,
        port,
        [
            LoxoneInput(
                key="bridge_alive",
                title="Bridge erreichbar",
                comment="Watchdog: toggelt, solange die Bridge laeuft",
                analog=True,
                unit_format="",
            )
        ],
    )
    vo = render_virtual_out(
        "System",
        f"http://{bridge_ip}:{listen_port}",
        [
            LoxoneCommand(
                key="resync",
                title="Alle Werte neu senden",
                path="/resync",
                analog=False,
            )
        ],
    )
```
→
```python
    viu = render_virtual_in_udp(
        "System",
        bridge_ip,
        port,
        [
            LoxoneInput(
                key="bridge_alive",
                title=i18n.t("export.system.bridge_alive_title"),
                comment=i18n.t("export.system.bridge_alive_comment"),
                analog=True,
                unit_format="",
            )
        ],
    )
    vo = render_virtual_out(
        "System",
        f"http://{bridge_ip}:{listen_port}",
        [
            LoxoneCommand(
                key="resync",
                title=i18n.t("export.system.resync_title"),
                path="/resync",
                analog=False,
            )
        ],
    )
```

Edit `src/loxmatter/export/signals.py`. Add `from loxmatter import i18n` next to the existing imports.

In `to_inputs`, the event branch:
```python
        if signal.ref.kind is SignalKind.EVENT:
            emit(
                LoxoneInput(
                    signal.key, signal.title, f"{comment} · Impuls", False, "", check_suffix="1"
                ),
                f"dem Impuls von {signal.key!r}",
            )
            emit(
                LoxoneInput(
                    f"{signal.key}_n", f"{signal.title} Zähler", f"{comment} · Zähler", True, ""
                ),
                f"dem Zaehler von {signal.key!r}",
            )
            continue
```
→
```python
        if signal.ref.kind is SignalKind.EVENT:
            emit(
                LoxoneInput(
                    signal.key,
                    signal.title,
                    i18n.t("export.signals.pulse_comment_suffix", comment=comment),
                    False,
                    "",
                    check_suffix="1",
                ),
                f"dem Impuls von {signal.key!r}",
            )
            emit(
                LoxoneInput(
                    f"{signal.key}_n",
                    i18n.t("export.signals.counter_title_suffix", title=signal.title),
                    i18n.t("export.signals.counter_comment_suffix", comment=comment),
                    True,
                    "",
                ),
                f"dem Zaehler von {signal.key!r}",
            )
            continue
```

(the `f"dem Impuls von {signal.key!r}"`/`f"dem Zaehler von {signal.key!r}"` `origin` arguments to `emit()` stay untouched — see the scope note above)

At the end of `to_inputs`, the online signal:
```python
    online_key = f"d{device_id}_online"
    emit(
        # Ein Zustand, kein Impuls - also analog (siehe LoxoneInput).
        LoxoneInput(online_key, f"{device_label} erreichbar", device_label, True, ""),
        "dem Online-Signal",
    )
```
→
```python
    online_key = f"d{device_id}_online"
    emit(
        # Ein Zustand, kein Impuls - also analog (siehe LoxoneInput).
        LoxoneInput(
            online_key,
            i18n.t("export.signals.online_title", device_label=device_label),
            device_label,
            True,
            "",
        ),
        "dem Online-Signal",
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/export/test_documents.py tests/export/test_signals.py -v`
Expected: all pass

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`. This task's functions are also exercised by `tests/test_export_cli.py` (Phase A) and `tests/api/test_export_api.py` — search both for assertions on the now-migrated German literals (`"erzeugt von loxmatter"`, `"Bridge erreichbar"`, `"Alle Werte neu senden"`, `"Zähler"`) and update them the same way (English default + German companion).

Run: `uv run ruff check src/loxmatter/export/documents.py src/loxmatter/export/signals.py`
Run: `uv run mypy src/loxmatter/export/documents.py src/loxmatter/export/signals.py`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/export/documents.py src/loxmatter/export/signals.py src/loxmatter/i18n/strings.yaml tests/export/test_documents.py tests/export/test_signals.py
git commit -m "$(cat <<'EOF'
feat(export): Vorlagentexte ueber t() uebersetzt (Phase C)

Titel-/Kommentarfelder in documents.py und signals.py folgen jetzt der
gemeinsamen Spracheinstellung - fuer CLI und WebUI-Export gleichermassen,
da beide dieselben Funktionen aufrufen. "Matter — {device_label}" bleibt
unveraendert (kein deutscher Text darin); die internen origin-Strings
der Schluessel-Kollisionspruefung bleiben ebenfalls unveraendert (nicht
HTTP-erreichbar, siehe Aufgabentext).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: WebUI-Uebersetzungsmechanismus (`app.js`, `index.html`)

**Design, verifiziert gegen die tatsaechliche Struktur der Dateien (nicht angenommen):** `index.html:55` traegt `<body x-data="app()">` - EIN Alpine-Bauteil fuer die ganze Seite, `app.js:231-232` definiert `function app() { return {...} }`. Die Skripte laden in dieser Reihenfolge (`index.html:758-759`, beide `defer`): `app.js` zuerst (legt die globale Funktion `app()` an), danach `alpine.min.js` (ruft sie auf). Die Seite kennt bereits GENAU das Muster, das dieser Uebersetzungsmechanismus braucht: `authReady` (`app.js:240`) verhindert das Aufblitzen des falschen Bildschirms, bis `/auth-info` geantwortet hat - `<template x-if="authReady && ...">` (`index.html:77,107`) zeigt bis dahin nichts. Dieselbe Technik uebernimmt diese Aufgabe fuer die Uebersetzungen: `stringsReady`, gesetzt von einer neuen `loadI18n()`, nach demselben Muster wie `loadAuthInfo()` (`app.js:424-442`).

**Kein neues JS-Modul, keine neue Datei:** die Kopfkommentare von `index.html` und `app.js` erklaeren ausdruecklich "kein Bundler, kein Modul-System".

**`t()` muss GLOBAL aufrufbar sein, nicht nur eine Methode des `app()`-Objekts.** `app.js` hat neben `app()` bereits eigenstaendige Funktionen ausserhalb davon — `requestJson`/`requestDownload` (Zeile 142ff.) haben keinen Zugriff auf `this` des Alpine-Bauteils, brauchen aber selbst uebersetzten Text (`web.errors.bridge_unreachable`, `web.errors.http_status`, Aufgabe 10). `t()` wird deshalb eine **Top-Level-Funktion** in `app.js`, die eine **Modul-globale, nicht-reaktive** Variable `translationStrings` liest — kein Alpine-Feld. Das ist bewusst so und keine Inkonsistenz zum uebrigen Zustand: die Uebersetzungstabelle aendert sich innerhalb EINER Seitenanzeige nie (ein Sprachwechsel laedt laut Entwurfsgespraech die ganze Seite neu, siehe Spec Abschnitt 7) — sie braucht deshalb keine Alpine-Reaktivitaet, nur einen Ort, den jede Funktion in dieser Datei erreicht. Alpine loest `x-text="t('key')"` trotzdem korrekt auf: liegt `t` nicht auf dem Komponenten-Objekt selbst, faellt die Auswertung auf den umgebenden Skript-Scope zurueck, in dem die globale Funktion `t` sichtbar ist.

`stringsReady`/`language` BLEIBEN Felder auf dem `app()`-Objekt — die muessen reaktiv sein, damit `x-if="stringsReady && ..."` in `index.html` tatsaechlich neu rendert, sobald `loadI18n()` fertig ist.

**`<html lang="de">` (`index.html:35`) liegt AUSSERHALB des `x-data`-Bereichs** (der beginnt erst bei `<body>`) - Alpine-Direktiven koennen dort nicht binden. Wird deshalb ueber eine normale DOM-Zuweisung gesetzt (`document.documentElement.lang = ...`), nicht ueber `:lang="..."`.

**Files:**
- Modify: `src/loxmatter/web/app.js`, `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_web.py` (Python-seitige Pruefung, dass der ausgelieferte `app.js`/`index.html`-Quelltext die neuen Bestandteile enthaelt — dieses Projekt hat keinen JS-Testlaeufer, `tests/api/test_web.py` prueft seit jeher nur den ausgelieferten Text, siehe dort)

**Interfaces:**
- Consumes: `GET /api/i18n` (Task 1) — `{"language": "en"|"de", "strings": {"web.xyz": "...", ...}}`.
- Produces: **global** in `app.js` (top-level, not on `app()`'s object) — `function t(key, values = {})`. **On** the `app()`-returned object — `stringsReady: boolean`, `language: string`, `async loadI18n(): Promise<void>`. Every later WebUI task (10+) calls `t(...)` — either bare (from a plain top-level function) or as `t(...)` inside an Alpine expression (Alpine resolves it as a global) — never `this.strings[...]` directly.

- [ ] **Step 1: Write the failing tests**

Read `tests/api/test_web.py` first, mirror its exact style (it fetches `/` and/or `/static/app.js` and asserts substrings of the response text). Add assertions that:
- the served `app.js` contains `function t(key`, `translationStrings`, and `loadI18n`
- the served `index.html`'s two auth `<template x-if="...">` guards (setup and login screens) now also require `stringsReady`, alongside the existing `authReady`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: FAIL — none of the new source exists yet

- [ ] **Step 3: Add the `strings.yaml` scaffolding key used to prove the mechanism (not real UI text yet — Task 9 adds the real `web.*` table)**

```yaml
web.test.smoke:
  en: "smoke test {value}"
  de: "Rauchtest {value}"
```

(a tiny, deliberately named test-only entry, mirroring `test.*` in Phase A — lets this task's own tests exercise `GET /api/i18n` returning a real `web.*` key without depending on Task 9's not-yet-written content)

- [ ] **Step 4: Implement the mechanism in `app.js`**

Add a module-level variable and the global `t()` function BEFORE `function app()` (i.e. near `requestJson`/`requestDownload` around line 142, at the top level of the file, NOT inside the object `app()` returns):
```javascript
// Modul-global, absichtlich NICHT auf dem app()-Objekt (siehe
// Implementierungsplan, Task 8: "t() muss global aufrufbar sein") - jede
// Funktion in dieser Datei erreicht sie, auch requestJson/requestDownload,
// die keinen Zugriff auf `this` des Alpine-Bauteils haben. Nicht reaktiv,
// weil sie es nicht sein muss: ein Sprachwechsel laedt die ganze Seite neu.
let translationStrings = {};

/** Uebersetzungshelfer - liefert den zu key gehoerenden Text in der
 * aktuellen Sprache, mit {platzhalter} aus values ersetzt. Fehlt der
 * Schluessel (z. B. eine noch nicht neu geladene Seite nach einem
 * Deployment mit neuen Schluesseln), liefert t() den Schluessel selbst
 * zurueck statt abzustuerzen - sichtbar falsch statt einer kaputten
 * Seite, dieselbe Haltung wie ueberall sonst in diesem Projekt
 * ("ein Klick, der nichts bewirkt, muss als klare Absage ankommen"). */
function t(key, values = {}) {
  const template = translationStrings[key];
  if (template === undefined) {
    return key;
  }
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
  );
}
```

Add two REACTIVE state fields next to the existing `authReady`/`passwordSet`/... block (`app.js:236-246`) — NOT `strings`, that lives in the module-level `translationStrings` above:
```javascript
    // --- Uebersetzung -------------------------------------------------------
    // Nach demselben Muster wie authReady: bis GET /api/i18n geantwortet hat,
    // zeigt die Seite nichts - siehe stringsReady in den beiden
    // auth-screen-templates in index.html. Die eigentliche Tabelle liegt
    // NICHT hier, sondern im modul-globalen translationStrings (siehe t()
    // oben) - dieses Feld existiert nur fuer x-if="stringsReady && ...".
    stringsReady: false,
    language: "en",
```

Add `loadI18n()` right next to `loadAuthInfo()` (after it, `app.js:442`), following the identical shape:
```javascript
    /** Laedt die aktuelle Sprache und die web.*-Uebersetzungstabelle - der
     * erste Aufruf jeder Seite, wie loadAuthInfo(), aber unabhaengig davon
     * (siehe init(), das beide parallel startet): GET /api/i18n ist
     * ungeschuetzt, die Ersteinrichtungs-/Anmeldeseite braucht diese Texte,
     * bevor sich jemand angemeldet hat. */
    async loadI18n() {
      try {
        const info = await requestJson("GET", "/api/i18n");
        this.language = info.language;
        translationStrings = info.strings;
        document.documentElement.lang = info.language;
      } finally {
        this.stringsReady = true;
      }
    },
```

Change `init()` (`app.js:361-377`) to load translations and auth info in parallel — both are independent, unauthenticated, and both gate the same auth-screen templates:
```javascript
    async init() {
      window.setInterval(() => {
        this.nowTick = Date.now();
      }, 1000);
      await this.loadAuthInfo();
      if (this.authenticated) {
        await this.startApp();
      }
    },
```
→
```javascript
    async init() {
      window.setInterval(() => {
        this.nowTick = Date.now();
      }, 1000);
      await Promise.all([this.loadI18n(), this.loadAuthInfo()]);
      if (this.authenticated) {
        await this.startApp();
      }
    },
```

- [ ] **Step 5: Gate rendering on `stringsReady` in `index.html`**

Change (`index.html:77`):
```html
    <template x-if="authReady && !authenticated && !passwordSet">
```
to:
```html
    <template x-if="stringsReady && authReady && !authenticated && !passwordSet">
```

Change (`index.html:107`):
```html
    <template x-if="authReady && !authenticated && passwordSet">
```
to:
```html
    <template x-if="stringsReady && authReady && !authenticated && passwordSet">
```

Change (find the `<template x-if="authenticated">` wrapping the main app, right after the login-screen template):
```html
    <template x-if="authenticated">
```
to:
```html
    <template x-if="stringsReady && authenticated">
```

(the top-level comment above this last one already explains the single-child-element reason for the wrapping `<template>` — do not restructure it, only add the `stringsReady &&` condition)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: all pass

- [ ] **Step 7: Manually verify in a real browser** (this project has no JS test runner — this is the actual verification for behavior, not the Python text-pattern test above)

Start the dev server (check `scripts/dev_web_server.py` for how — this project's own way to run the WebUI without a full Matter/Loxone stack) and, using a browser tool:
1. Load the page fresh. Confirm no flash of untranslated `{key}`-looking text and no console error before the setup/login screen appears.
2. Open the browser devtools console, run `document.documentElement.lang` — expect `"en"`.
3. Confirm the page still reaches the setup or login screen normally (this task changes no auth logic, only adds a second, parallel gate).

- [ ] **Step 8: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Uebersetzungsmechanismus fuer die WebUI

stringsReady/language/strings/loadI18n()/t() auf dem app()-Objekt,
nach demselben Muster wie das bestehende authReady/loadAuthInfo() -
init() laedt beides parallel. index.html gated alle drei
Hauptbereiche (Ersteinrichtung, Anmeldung, App) zusaetzlich auf
stringsReady. <html lang> wird jetzt dynamisch gesetzt (ausserhalb
von Alpines x-data-Bereich, deshalb per DOM-Zuweisung statt
Direktive). Noch keine echten web.*-Uebersetzungen - die Seite zeigt
bis Aufgabe 9+ weiterhin denselben deutschen Text wie zuvor.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Die vollstaendige `web.*`-Uebersetzungstabelle

Diese Aufgabe fuegt AUSSCHLIESSLICH neue Eintraege zu `strings.yaml` hinzu — keine Aufrufstellen aendern sich, das ist Aufgabe 10+. Die vollstaendige, verifizierte Zeichenketten-Inventur, gegen die diese Tabelle entstanden ist, liegt unter `.superpowers/sdd/webui-string-inventory.md` (relativ zum Repo-Wurzelverzeichnis dieses Worktrees) — jede spaetere Bindungs-Aufgabe verweist darauf fuer exakte Zeilennummern.

**Entscheidungen, die diese Tabelle bereits trifft (nicht der Bindungs-Aufgabe ueberlassen):**
- Mehrfach identisch auftretende Zeichenketten bekommen EINEN Schluessel, nicht je Fundstelle einen eigenen (Sitzungsablauf, Brücken-IP-Hinweis, "Brücke nicht erreichbar", "IP dieser Brücke"-Feldbezeichnung, "Fehler" als Systemcheck-Badge/Log-Stufe) — siehe die Inventur, Abschnitt "Duplicate literals worth collapsing".
- Rechtschreibfehler im heutigen deutschen Quelltext (`Geraeteliste`, `unveraendert`, `geaendert`, `haelt`/`Anhaengen`/`waehrend`) werden VERBATIM in den `de`-Wert uebernommen, nicht stillschweigend korrigiert — dieselbe Zurueckhaltung wie bei jeder anderen Migration in diesem Plan (kein Refactoring bündelt eine Inhaltsaenderung).
- `loxmatter` als Produktname (Titel, beide `<h1>`) wird NICHT uebersetzt — kein Eintrag dafuer.
- Texte mit eingebettetem HTML (`<strong>`, ein `<span class="key">`) werden als YAML-Blockskalar (`|`) gefuehrt, damit eingebettete Anfuehrungszeichen in `class="key"` nicht maskiert werden muessen — die Bindungs-Aufgabe setzt sie ueber `x-html`, nicht `x-text`.
- Drei Stellen mit einem eingebetteten `<a @click="...">`-Link (Bruecken-IP-Hinweis auf der Geraetekarte und im Export-Tab) werden NICHT als ein HTML-Block gefuehrt, sondern als drei Teiltexte (Praefix, Linktext, Suffix) — ein `x-html`-Block wuerde die lebendige `@click`-Direktive des Links in totes HTML einfrieren.
- `app.js:1009`s Rueckgabe des rohen Backend-Fehlertexts bekommt KEINEN Schluessel — das ist kein Text, den diese Migration uebersetzen kann (siehe Inventur, Punkt 4).

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml`
- Create: `tests/test_i18n.py` addition (one assertion per section, see Step 2)

**Interfaces:**
- Consumes: nothing new.
- Produces: ~110 `web.*` keys, all resolvable via `i18n.t("web.xyz")`/`GET /api/i18n`'s `strings` payload — the complete surface Task 10+ binds against.

- [ ] **Step 1: Add the complete `web.*` table to `strings.yaml`**

```yaml
# --- web.nav — Reiterleiste ---
web.nav.devices:
  en: "Devices"
  de: "Geräte"
web.nav.signals:
  en: "Signals"
  de: "Signale"
web.nav.export:
  en: "Export"
  de: "Export"
web.nav.system:
  en: "System"
  de: "System"
web.nav.settings:
  en: "Settings"
  de: "Einstellungen"

# --- web.header / web.connection — Kopfzeile, Verbindungsstatus ---
web.header.logout:
  en: "Log out"
  de: "Abmelden"
web.header.heartbeat_prefix:
  en: "· Heartbeat"
  de: "· Lebenszeichen"
web.connection.lost_banner:
  en: "The live connection was interrupted. Displayed values may be outdated until the connection is restored."
  de: "Die Live-Verbindung wurde unterbrochen. Angezeigte Werte können inzwischen veraltet sein, bis die Verbindung wiederhergestellt ist."
web.header.toast_dismiss_tooltip:
  en: "Click to dismiss"
  de: "Zum Ausblenden anklicken"
web.connection.live:
  en: "Live connection active"
  de: "Live-Verbindung aktiv"
web.connection.lost_reconnecting:
  en: "Connection lost – reconnecting…"
  de: "Verbindung verloren – verbinde neu…"
web.connection.never_connected:
  en: "Cannot connect to the bridge – still trying…"
  de: "Keine Verbindung zur Brücke möglich – verbinde weiter…"
web.connection.connecting:
  en: "Connecting…"
  de: "Verbinde…"
web.header.time_ago_seconds:
  en: "{seconds}s ago"
  de: "vor {seconds} s"
web.header.time_ago_minutes:
  en: "{minutes}m ago"
  de: "vor {minutes} min"
web.header.time_ago_hours:
  en: "{hours}h ago"
  de: "vor {hours} h"
web.header.last_updated:
  en: "Last updated {text}"
  de: "Zuletzt aktualisiert {text}"
web.header.unchanged_since_load:
  en: "Unchanged since the page loaded"
  de: "Seit dem Laden der Seite unveraendert"

# --- web.auth — Ersteinrichtung, Anmeldung ---
web.auth.setup_heading:
  en: "Set up loxmatter"
  de: "loxmatter einrichten"
web.auth.setup_warning:
  en: |
    No password has been set for this bridge yet. Until it is, <strong>anyone on the network</strong> can take it over by filling out this form. Complete setup now, not later.
  de: |
    Für diese Brücke ist noch kein Passwort vergeben. Bis das geschehen ist, kann <strong>jeder im Netz</strong> sie übernehmen, indem er dieses Formular ausfüllt. Schließe die Einrichtung deshalb jetzt ab und nicht später.
web.auth.password_label:
  en: "Password"
  de: "Passwort"
web.auth.password_repeat_label:
  en: "Repeat password"
  de: "Passwort wiederholen"
web.auth.password_hint:
  en: "At least 8 characters, ideally randomly generated rather than made up — the fabric backup sits behind this login too. This service speaks HTTP without encryption; use a password you don't use anywhere else."
  de: "Mindestens 8 Zeichen, am besten zufällig erzeugt statt ausgedacht – hinter dieser Anmeldung liegt auch die Fabric-Sicherung. Dieser Dienst spricht HTTP ohne Verschlüsselung; nimm ein Passwort, das du nirgendwo sonst benutzt."
web.auth.setup_submit:
  en: "Set password"
  de: "Passwort vergeben"
web.auth.login_submit:
  en: "Log in"
  de: "Anmelden"
web.auth.session_expired:
  en: "The session has expired – please log in again."
  de: "Die Sitzung ist abgelaufen – bitte erneut anmelden."
web.auth.password_mismatch:
  en: "The two entries do not match."
  de: "Die beiden Eingaben stimmen nicht überein."

# --- web.devices — Geraeteliste, Einlernen, Bedienung ---
web.devices.commission_heading:
  en: "Commission a new device"
  de: "Neues Gerät einlernen"
web.devices.code_placeholder:
  en: "Pairing code (11 digits or MT:…)"
  de: "Pairing-Code (11-stellig oder MT:…)"
web.devices.thread_dataset_placeholder:
  en: "Thread dataset (Thread devices only)"
  de: "Thread-Datensatz (nur bei Thread-Geräten)"
web.devices.commission_submit:
  en: "Commission"
  de: "Einlernen"
web.devices.commission_hint:
  en: "If the device is already paired with Apple, Google, or a DIRIGERA, the printed code no longer works here — generate an additional multi-admin code there and enter that instead."
  de: "Hängt das Gerät schon in Apple, Google oder einer DIRIGERA, funktioniert der aufgedruckte Code hier nicht mehr – dort einen zusätzlichen Multi-Admin-Code erzeugen und stattdessen diesen eingeben."
web.devices.empty:
  en: "No device commissioned yet."
  de: "Noch kein Gerät eingelernt."
web.devices.changed_since_export:
  en: "Changed since export"
  de: "Geändert seit Export"
web.devices.offline:
  en: "Offline"
  de: "Offline"
web.devices.remove:
  en: "Remove"
  de: "Entfernen"
web.devices.values_heading:
  en: "Values"
  de: "Werte"
web.devices.signals_loading:
  en: "Loading signals…"
  de: "Signale werden geladen…"
web.devices.no_functional_signals:
  en: "No functional signals for this device."
  de: "Keine funktionalen Signale für dieses Gerät."
web.devices.more_in_signals_view:
  en: "more in the Signals view."
  de: "weitere in der Ansicht „Signale“."
web.devices.controls_heading:
  en: "Controls"
  de: "Bedienung"
web.devices.controls_loading:
  en: "Loading controls…"
  de: "Bedienelemente werden geladen…"
web.devices.no_known_commands:
  en: "No known output commands for this device."
  de: "Keine bekannten Ausgangsbefehle für dieses Gerät."
web.devices.value_placeholder:
  en: "Value"
  de: "Wert"
web.devices.send:
  en: "Send"
  de: "Senden"
web.devices.more_commands_unnamed:
  en: "more commands exist, but are unnamed."
  de: "weitere Kommandos vorhanden, aber nicht benannt."
web.devices.export:
  en: "Export"
  de: "Exportieren"
web.devices.export_hint_prefix:
  en: "First set it in "
  de: "Erst in "
web.settings.miniserver_link:
  en: "Settings → Miniserver connection"
  de: "Einstellungen → Verbindung zum Miniserver"
web.devices.export_hint_suffix:
  en: "."
  de: " hinterlegen."
web.export.settings_hint_prefix:
  en: "Managed in "
  de: "Wird in "
web.export.settings_hint_suffix:
  en: "."
  de: " verwaltet."
web.devices.list_load_error:
  en: "Could not load device list: {message}"
  de: "Geraeteliste konnte nicht geladen werden: {message}"
web.devices.controls_load_error:
  en: "Could not load controls: {message}"
  de: "Bedienelemente konnten nicht geladen werden: {message}"
web.devices.export_never:
  en: "Not exported yet"
  de: "Noch nicht exportiert"
web.devices.export_last:
  en: "Last exported on {timestamp}"
  de: "Zuletzt exportiert am {timestamp}"
web.devices.label_save_error:
  en: "Could not save name: {message}"
  de: "Name konnte nicht gespeichert werden: {message}"
web.devices.remove_error:
  en: "Could not remove device: {message}"
  de: "Gerät konnte nicht entfernt werden: {message}"
web.devices.command_sent:
  en: "\"{slug}\" was sent to {label}."
  de: "\"{slug}\" wurde an {label} gesendet."
web.devices.command_failed:
  en: "\"{slug}\" failed: {message}"
  de: "\"{slug}\" ist fehlgeschlagen: {message}"
web.devices.exported_toast:
  en: "{label} was exported."
  de: "{label} wurde exportiert."
web.devices.export_failed:
  en: "Export failed: {message}"
  de: "Export fehlgeschlagen: {message}"
web.export.bridge_ip_missing:
  en: "Please set the bridge IP in Settings → Miniserver connection first."
  de: "Bitte zuerst in Einstellungen → Verbindung zum Miniserver die Brücken-IP hinterlegen."
web.devices.remove_confirm:
  en: |
    Really remove device "{label}"? This cannot be undone.

    Orphaned afterwards in Loxone:
    • every virtual input/output with the key prefix "d{id}_"
    • the imported templates "VIU_d{id}_….xml" and "VO_d{id}_….xml"

    Delete these by hand in Loxone Config.
  de: |
    Gerät "{label}" wirklich entfernen? Das kann nicht rückgängig gemacht werden.

    In Loxone bleiben danach verwaist:
    • alle virtuellen Ein- und Ausgänge mit dem Schlüssel-Präfix "d{id}_"
    • die importierten Vorlagen "VIU_d{id}_….xml" und "VO_d{id}_….xml"

    Diese in Loxone Config von Hand löschen.
web.devices.commission_code_required:
  en: "Please enter a pairing code first."
  de: "Bitte zuerst einen Pairing-Code eingeben."
web.devices.commission_success:
  en: |
    {label} was commissioned. Live values only appear after a restart of the bridge – until then the device shows "online" but every signal shows "-" (known limitation, Spec 12.3). Exporting the templates already works independently of this.
  de: |
    {label} wurde eingelernt. Live-Werte erscheinen erst nach einem Neustart der Brücke – bis dahin zeigt das Gerät zwar „online“, aber jedes Signal „-“ (bekannte Grenze, Spec 12.3). Der Export der Vorlagen funktioniert davon unabhängig schon jetzt.
web.devices.commission_failed:
  en: "Commissioning failed: {message}"
  de: "Einlernen fehlgeschlagen: {message}"

# --- web.signals — Signalansicht ---
web.signals.key_hint:
  en: "The key (left, greyed out) is the wiring in Loxone and cannot be changed here — doing so would silently disable a block there."
  de: "Der Schlüssel (links, grau hinterlegt) ist die Verdrahtung in Loxone und lässt sich hier nicht ändern – ein Klick würde dort einen Baustein still lahmlegen."
web.signals.functional_vs_expert_explanation:
  en: "\"Functional\" are the signals that belong to the recognized device type (on/off, power, and similar) – \"Expert\" is everything else (network, device, and diagnostic values, technical counters). Both sections keep the export checkbox for each individual signal; the grouping only changes what is visible by default."
  de: "„Funktional“ sind die Signale, die zum erkannten Gerätetyp gehören (Ein/Aus, Leistung und Ähnliches) – „Experte“ alles andere (Netzwerk-, Geräte- und Diagnosewerte, technische Zähler). Beide Blöcke behalten den Exportieren-Haken für jedes einzelne Signal; die Gliederung ändert nur, was standardmäßig zu sehen ist."
web.signals.show_expert:
  en: "Show expert signals"
  de: "Experten-Signale anzeigen"
web.signals.load_button:
  en: "Load signals"
  de: "Signale laden"
web.signals.none_functional:
  en: "No signal of this device is considered functional."
  de: "Kein Signal dieses Geräts gilt als funktional."
web.signals.expert_collapsed_hint:
  en: "Collapsed – {count} signals without a recognized meaning for this device type. \"Show expert signals\" above reveals them for all devices."
  de: "Zugeklappt – {count} Signale ohne erkannte Bedeutung für diesen Gerätetyp. „Experten-Signale anzeigen“ oben blendet sie für alle Geräte ein."
web.signals.key_tooltip:
  en: "Wiring in Loxone – not changeable."
  de: "Verdrahtung in Loxone – nicht änderbar."
web.signals.export_checkbox:
  en: "export"
  de: "exportieren"
web.signals.raw_write_placeholder:
  en: "Write raw value"
  de: "Rohwert schreiben"
web.signals.raw_write_submit:
  en: "Write"
  de: "Schreiben"
web.signals.group_functional:
  en: "Functional"
  de: "Funktional"
web.signals.group_expert:
  en: "Expert"
  de: "Experte"
web.signals.load_error:
  en: "Could not load signals: {message}"
  de: "Signale konnten nicht geladen werden: {message}"
web.signals.title_save_error:
  en: "Could not save title: {message}"
  de: "Titel konnte nicht gespeichert werden: {message}"
web.signals.export_flag_error:
  en: "Could not change export flag: {message}"
  de: "Export-Kennzeichen konnte nicht geaendert werden: {message}"
web.signals.write_success:
  en: "Written."
  de: "Geschrieben."

# --- web.export — Export-Tab ---
web.export.heading:
  en: "Export templates"
  de: "Vorlagen exportieren"
web.bridge_ip_label:
  en: "IP of this bridge"
  de: "IP dieser Brücke"
web.export.udp_port_label:
  en: "UDP port"
  de: "UDP-Port"
web.export.http_port_label:
  en: "HTTP port (commands)"
  de: "HTTP-Port (Kommandos)"
web.export.include_system:
  en: "Include system templates"
  de: "Systemvorlagen einschließen"
web.export.only_pending:
  en: "only devices not yet exported"
  de: "nur noch nicht exportierte Geräte"
web.export.filter_explanation:
  en: |
    The filter applies to the preview <strong>and</strong> to the ZIP: when set, the download contains only the devices from the table below, and only those then count as exported.
  de: |
    Der Filter gilt für die Vorschau <strong>und</strong> für das ZIP: ist er gesetzt, enthält der Download nur die Geräte aus der Tabelle unten, und nur diese gelten danach als exportiert.
web.export.preview_button:
  en: "View preview"
  de: "Vorschau ansehen"
web.export.download_button:
  en: "Download ZIP"
  de: "ZIP herunterladen"
web.export.preview_heading:
  en: "Preview"
  de: "Vorschau"
web.export.col_device:
  en: "Device"
  de: "Gerät"
web.export.col_viu:
  en: "VIU file"
  de: "VIU-Datei"
web.export.col_vo:
  en: "VO file"
  de: "VO-Datei"
web.export.col_inputs:
  en: "Inputs"
  de: "Eingänge"
web.export.col_commands:
  en: "Commands"
  de: "Befehle"
web.export.col_skipped:
  en: "Skipped"
  de: "Übersprungen"
web.export.col_expert_withheld:
  en: "Held back as expert"
  de: "Als Experte zurückgehalten"
web.export.col_last_exported:
  en: "Last exported"
  de: "Zuletzt exportiert"
web.export.expert_withheld_explanation:
  en: "\"Held back as expert\" signals would be technically exportable but don't belong to the device type by default (Signals view, \"Expert\" section) – each one can be enabled individually there."
  de: "„Als Experte zurückgehalten“ sind Signale, die technisch exportierbar wären, aber standardmäßig nicht zum Gerätetyp gehören (Ansicht „Signale“, Block „Experte“) – dort lässt sich jedes einzeln freischalten."
web.export.system_files_prefix:
  en: "System templates: "
  de: "Systemvorlagen: "
web.export.status_load_error:
  en: "Could not load export status: {message}"
  de: "Export-Status konnte nicht geladen werden: {message}"
web.export.preview_failed:
  en: "Preview failed: {message}"
  de: "Vorschau fehlgeschlagen: {message}"
web.export.download_failed:
  en: "Download failed: {message}"
  de: "Download fehlgeschlagen: {message}"

# --- web.system — Systemcheck, Live-Diagnose, Sicherung ---
web.system.checks_heading:
  en: "System check"
  de: "Systemcheck"
web.system.refresh:
  en: "Refresh"
  de: "Aktualisieren"
web.system.check_ok:
  en: "OK"
  de: "OK"
web.system.check_error:
  en: "Error"
  de: "Fehler"
web.system.live_heading:
  en: "Live diagnostics"
  de: "Live-Diagnose"
web.system.diag_disconnected:
  en: "Connection lost – reconnecting…"
  de: "Verbindung getrennt – verbinde neu…"
web.system.resume:
  en: "Resume"
  de: "Fortsetzen"
web.system.pause:
  en: "Pause"
  de: "Pausieren"
web.system.hide_noise:
  en: "Hide heartbeat and full-resend"
  de: "Heartbeat und Full-Resend ausblenden"
web.system.log_level_label:
  en: "Log level"
  de: "Log-Stufe"
web.system.log_level_info:
  en: "Info"
  de: "Info"
web.system.log_level_warn:
  en: "Warning"
  de: "Warnung"
web.system.log_level_critical:
  en: "Critical"
  de: "Kritisch"
web.system.clear:
  en: "Clear"
  de: "Leeren"
web.system.pause_clear_explanation:
  en: |
    "Pause" only stops new lines from being appended, not the connection itself – lines arriving during the pause are not caught up afterwards, like a paused <span class="key">tail -f</span>. "Clear" only affects this page, not the bridge's own ring buffers.
  de: |
    „Pausieren“ haelt nur das Anhaengen neuer Zeilen an, nicht die Verbindung selbst – waehrend der Pause eintreffende Zeilen werden nicht nachgeholt, wie bei einem angehaltenen <span class="key">tail -f</span>. „Leeren“ wirkt nur auf diese Seite, nicht auf die Ringe der Brücke.
web.system.logs_heading:
  en: "Logs"
  de: "Logs"
web.system.logs_hint:
  en: "The bridge's log lines, live – newest on top, auto-scrolling."
  de: "Protokollzeilen der Brücke, laufend – jüngste oben, folgt automatisch."
web.system.udp_heading:
  en: "UDP capture"
  de: "UDP-Mitschnitt"
web.system.udp_hint:
  en: "Datagrams actually sent over the wire, live – newest on top, auto-scrolling."
  de: "Tatsächlich über den Draht geschickte Datagramme, laufend – jüngste oben, folgt automatisch."
web.system.command_log_heading:
  en: "Command log"
  de: "Kommando-Log"
web.system.command_log_hint:
  en: "Incoming HTTP calls with their result, live."
  de: "Eingehende HTTP-Aufrufe mit ihrem Ergebnis, laufend."
web.system.backup_heading:
  en: "Backup"
  de: "Sicherung"
web.system.backup_explanation:
  en: "Backup of the fabric credentials (matter-server data directory) as a ZIP file – the only irreplaceable state of this installation. If it's lost, every device must be recommissioned."
  de: "Sicherung der Fabric-Zugangsdaten (matter-server-Datenverzeichnis) als ZIP-Datei – der einzige unersetzliche Zustand dieser Installation. Geht er verloren, muss jedes Gerät neu eingelernt werden."
web.system.backup_access_note:
  en: "Only accessible after logging in, like every other function on this page: whoever can download it can take over the Matter fabric."
  de: "Nur nach Anmeldung abrufbar, wie jede andere Funktion dieser Seite: wer sie herunterladen kann, kann die Matter-Fabric übernehmen."
web.system.backup_download:
  en: "Download backup"
  de: "Sicherung herunterladen"
web.system.load_error:
  en: "Could not load diagnostics: {message}"
  de: "Diagnose konnte nicht geladen werden: {message}"
web.system.backup_error:
  en: "Backup not possible: {message}"
  de: "Sicherung nicht möglich: {message}"

# --- web.settings — Einstellungen-Tab ---
web.settings.connection_heading:
  en: "Miniserver connection"
  de: "Verbindung zum Miniserver"
web.settings.connection_explanation:
  en: |
    This means the address of the machine loxmatter runs on – as the Miniserver sees it. <strong>Not</strong> the Miniserver's address. The virtual input only accepts datagrams from this address, and the output commands call it as <span class="key">http://&lt;this IP&gt;:HTTP-port</span>. If the Miniserver's IP is entered here, the templates look correct but stay silent – with no error message.
  de: |
    Gemeint ist die Adresse des Rechners, auf dem loxmatter läuft – so, wie der Miniserver ihn sieht. <strong>Nicht</strong> die Adresse des Miniservers. Der virtuelle Eingang nimmt Datagramme nur von dieser Adresse an, und die Ausgangsbefehle rufen sie als <span class="key">http://&lt;diese IP&gt;:HTTP-Port</span> auf. Steht hier die Miniserver-IP, sehen die Vorlagen richtig aus, bleiben aber stumm – ohne jede Fehlermeldung.
web.settings.bridge_ip_placeholder:
  en: "e.g. 192.168.1.20"
  de: "z. B. 192.168.1.20"
web.settings.udp_port_label:
  en: "UDP port (virtual input)"
  de: "UDP-Port (virtueller Eingang)"
web.settings.http_port_label:
  en: "HTTP port (receiving commands)"
  de: "HTTP-Port (Befehle empfangen)"
web.settings.save:
  en: "Save"
  de: "Speichern"
web.settings.last_saved_prefix:
  en: "Last saved: "
  de: "Zuletzt gespeichert: "
web.settings.never_saved:
  en: "Not saved yet."
  de: "Noch nicht gespeichert."
web.settings.more_settings_heading:
  en: "More settings"
  de: "Weitere Einstellungen"
web.settings.load_error:
  en: "Could not load settings: {message}"
  de: "Einstellungen konnten nicht geladen werden: {message}"
web.settings.bridge_ip_required:
  en: "Please enter the IP of this bridge."
  de: "Bitte die IP dieser Brücke eingeben."
web.settings.saved_toast:
  en: "Settings saved."
  de: "Einstellungen gespeichert."
web.settings.save_error:
  en: "Could not save settings: {message}"
  de: "Einstellungen konnten nicht gespeichert werden: {message}"
web.settings.language_heading:
  en: "Language"
  de: "Sprache"
web.settings.language_en:
  en: "English"
  de: "Englisch"
web.settings.language_de:
  en: "German"
  de: "Deutsch"

# --- web.format — gemeinsame Formatierungshelfer ---
web.format.never:
  en: "never"
  de: "noch nie"
web.format.true:
  en: "true"
  de: "wahr"
web.format.false:
  en: "false"
  de: "falsch"

# --- web.errors — Netzwerk-/generische Fehler ---
web.errors.http_status:
  en: "HTTP {status}"
  de: "HTTP {status}"
web.errors.bridge_unreachable:
  en: "The bridge is unreachable – it may not be running."
  de: "Die Brücke ist nicht erreichbar – sie läuft möglicherweise nicht."
```

Note: `web.settings.more_settings_heading` is kept (the card heading stays) but its placeholder paragraph (`"Hier entstehen künftig weitere Einstellungen, sobald sie gebraucht werden."`) is deliberately NOT given a key — Task 12 (language toggle) replaces that paragraph with the actual toggle, it never ships translated as placeholder text.

- [ ] **Step 2: Verify the table loads and resolves correctly**

Add to `tests/test_i18n.py`:

```python
def test_web_namespace_has_no_missing_english_fallback_gaps():
    """Jeder web.*-Schluessel muss mindestens 'en' tragen - raw_template()
    wirft KeyError, wenn selbst 'en' fehlt (siehe dessen Implementierung,
    hinzugefuegt beim Bugfix vor dieser Aufgabe: GET /api/i18n stuerzte
    zuvor an genau dieser Stelle ab, weil t() hier - mit .format() und
    ohne Platzhalterwerte - bei JEDEM web.*-Schluessel mit einem
    {platzhalter} eine KeyError geworfen haette. raw_template() ist die
    richtige Funktion fuer diese Pruefung: sie prueft nur "gibt es
    ueberhaupt einen en-Eintrag", nicht "sind alle Platzhalter befuellt" -
    letzteres ist client-seitig app.js's Aufgabe, nie serverseitig."""
    for key in i18n.strings_with_prefix("web."):
        assert i18n.raw_template(key)  # wirft nur, wenn 'en' fehlt - keine .format()-Falle


def test_web_namespace_key_count_is_substantial():
    """Grobe Bewahrung gegen ein versehentlich unvollstaendiges Einfuegen -
    kein exakter Schwellwert, nur ein Mindestmass."""
    assert len(i18n.strings_with_prefix("web.")) > 100
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: all pass, including the two new ones

- [ ] **Step 4: Run the full test suite to confirm no regressions**

Run: `uv run pytest -q -m "not slow"`

Run: `uv run ruff check src/loxmatter/i18n/`
Expected: clean. Also `python -c "import yaml; yaml.safe_load(open('src/loxmatter/i18n/strings.yaml'))"` to confirm the file is still valid YAML after this large addition (a stray unescaped quote is the most likely failure mode for a table this size).

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml tests/test_i18n.py
git commit -m "$(cat <<'EOF'
feat(i18n): vollstaendige web.*-Uebersetzungstabelle

Rund 110 Schluessel fuer jeden statischen und dynamischen Text der
WebUI (Inventur: .superpowers/sdd/webui-string-inventory.md), nach
Bereich benannt und mit den bereits im Entwurfsgespraech getroffenen
Zusammenlegungen (Sitzungsablauf, Bruecken-IP-Hinweis, u. a.). Noch
keine Aufrufstelle geaendert - die WebUI zeigt weiterhin den
bisherigen deutschen Text, das folgt in den naechsten Aufgaben.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Tasks 10-15: WebUI-Textmigration nach Bereich

Diese sechs Aufgaben binden die in Aufgabe 9 bereits vollstaendig uebersetzten `web.*`-Schluessel an ihre tatsaechlichen Stellen in `index.html`/`app.js`. Jede Aufgabe folgt demselben Muster:

1. **Statischer Text** (`index.html`): `x-text="t('web.xyz')"` fuer reinen Text; `x-html="t('web.xyz')"` NUR fuer die wenigen Stellen mit eingebettetem `<strong>`/`<span class="key">` (Aufgabe 9 fuehrt diese bereits als YAML-Blockskalare — erkennbar am `|` im Wert). Ein `<a @click="...">`-Link innerhalb eines Hinweistexts bleibt als eigenes Element bestehen; nur der umgebende Text und der Linktext selbst werden zu `x-text`, NICHT der ganze Absatz zu `x-html` (das wuerde die `@click`-Direktive einfrieren).
2. **Dynamischer Text** (`app.js`): jede Zeichenkette, die frueher direkt im Code stand, wird zu einem `t('web.xyz', {platzhalter: wert})`-Aufruf an derselben Stelle — `t` ist seit Aufgabe 8 global aufrufbar, unabhaengig davon, ob die aufrufende Funktion eine Methode von `app()` ist oder eine freie Funktion wie `requestJson`.
3. Die autoritative, vollstaendige Fundstellen-Liste ist `.superpowers/sdd/webui-string-inventory.md` (Zeilennummern koennen sich seit ihrer Erstellung geringfuegig verschoben haben — im Zweifel nach dem exakten deutschen Text suchen, nicht nach der genannten Zeilennummer).
4. **Verifikation:** dieses Projekt hat keinen JS-Testlaeufer. Jede Aufgabe verlangt (a) eine Python-seitige Textmuster-Pruefung auf den ausgelieferten Quelltext (wie `tests/api/test_web.py` es bereits tut) UND (b) eine manuelle Pruefung im Browser (Dev-Server ueber `scripts/dev_web_server.py`, siehe Aufgabe 8 Schritt 7) — Text-Pattern-Matching allein kann eine falsch gebundene Alpine-Direktive nicht erkennen.
5. Jede Aufgabe committet fuer sich — sechs kleinere, ueberschaubare Diffs statt eines einzigen riesigen.

### Task 10: Navigation, Kopfzeile, Verbindungsstatus, Formatierungs-/Fehlerhelfer, Zugangsbildschirme

Die Grundlage: die zwei generischen Fehlerstrings (`requestJson`/`requestDownload`) beweisen, dass der globale `t()` aus einer freien Funktion heraus funktioniert; alles andere in dieser Aufgabe sind Alpine-Bindungen.

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`
- Modify: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `t()` (Aufgabe 8), alle `web.nav.*`, `web.header.*`, `web.connection.*`, `web.auth.*`, `web.format.*`, `web.errors.*` Schluessel (Aufgabe 9).

- [ ] **Step 1: Write the failing tests**

Read `tests/api/test_web.py` first. Add assertions that the served `app.js` no longer contains the literal `"Die Brücke ist nicht erreichbar"` (replaced by a `t(...)` call) and that `index.html` no longer contains the literal `>Geräte<` nav-tab text node (replaced by `x-text`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_web.py -v`

- [ ] **Step 3: Bind the nav tabs (`index.html`, worked example)**

Find each of the five nav buttons (around `index.html:163-167`, inventory §1). They look like:
```html
<button :class="{ active: view === 'devices' }" @click="view = 'devices'">Geräte</button>
```
Apply `x-text` and remove the inline text node:
```html
<button :class="{ active: view === 'devices' }" @click="view = 'devices'" x-text="t('web.nav.devices')"></button>
```
Repeat for the remaining four (`web.nav.signals`, `web.nav.export`, `web.nav.system`, `web.nav.settings`), matching each button's existing `@click`/`:class` attributes exactly as found — do not alter them, only add `x-text` and empty the text node.

- [ ] **Step 4: Bind the header/connection status (`index.html` + `app.js`)**

`index.html:136` (logout button): add `x-text="t('web.header.logout')"`, empty the text node — same pattern as Step 3.

`index.html:158-159` (connection-lost banner): plain prose, no embedded markup → `x-text="t('web.connection.lost_banner')"`.

`index.html:752` (toast tooltip attribute): change `title="Zum Ausblenden anklicken"` to `:title="t('web.header.toast_dismiss_tooltip')"`.

`app.js`'s `connectionStatusText()` (around line 1646-1654) — worked example for a dynamic method:
```javascript
function connectionStatusText() {
  if (this.socketConnected) return "Live-Verbindung aktiv";
  if (this.socketEverConnected) return "Verbindung verloren – verbinde neu…";
  if (this.initialConnectFailures > INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE)
    return "Keine Verbindung zur Brücke möglich – verbinde weiter…";
  return "Verbinde…";
}
```
(exact condition shape may differ slightly — read the real function before editing, keep every condition unchanged, only replace the four returned literals)
→
```javascript
function connectionStatusText() {
  if (this.socketConnected) return t("web.connection.live");
  if (this.socketEverConnected) return t("web.connection.lost_reconnecting");
  if (this.initialConnectFailures > INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE)
    return t("web.connection.never_connected");
  return t("web.connection.connecting");
}
```

The three relative-time literals (`app.js:872,876,878`, in the function feeding `heartbeatText()`/`signalAgeTitle()`) — same pattern:
```javascript
return `vor ${seconds} s`;
```
→
```javascript
return t("web.header.time_ago_seconds", { seconds });
```
(and correspondingly `web.header.time_ago_minutes` with `{ minutes }`, `web.header.time_ago_hours` with `{ hours: Math.round(minutes / 60) }` — match whatever the actual local variable is named at each site)

`app.js:911` (`` `Zuletzt aktualisiert ${text}` ``) → `t("web.header.last_updated", { text })`.
`app.js:912` (`"Seit dem Laden der Seite unveraendert"`) → `t("web.header.unchanged_since_load")`.

- [ ] **Step 5: Bind the auth screens (`index.html`)**

Setup screen (`index.html:78-106`, inventory §3):
- `<h1>loxmatter einrichten</h1>` → `<h1 x-text="t('web.auth.setup_heading')"></h1>`
- The warning `<p class="banner warn">...<strong>jeder im Netz</strong>...</p>` → `<p class="banner warn" x-html="t('web.auth.setup_warning')"></p>` (empty the paragraph's own content — the translation string already carries the `<strong>` markup, see Task 9)
- `Passwort` label → `x-text="t('web.auth.password_label')"`
- `Passwort wiederholen` label → `x-text="t('web.auth.password_repeat_label')"`
- The hint paragraph → `x-text="t('web.auth.password_hint')"`
- `Passwort vergeben` button → `x-text="t('web.auth.setup_submit')"`

Login screen (`index.html:108-119`):
- `<h1>loxmatter</h1>` at line 110 is the PRODUCT NAME (see Task 9's scope note) — leave unchanged, do NOT bind it
- `Passwort` label (line 112) → `x-text="t('web.auth.password_label')"` (same shared key as setup screen)
- `Anmelden` button → `x-text="t('web.auth.login_submit')"`

`app.js`: the three identical `"Die Sitzung ist abgelaufen..."` literals (`app.js:113,1362,1610`) each → `t("web.auth.session_expired")`. `app.js:488` (`"Die beiden Eingaben stimmen nicht überein."`) → `t("web.auth.password_mismatch")`.

- [ ] **Step 6: Bind the generic network-error helpers and formatting helpers (`app.js`)**

`requestJson`/`requestDownload` (around lines 132, 166, 206) — the worked example proving `t()` works outside `app()`:
```javascript
async function requestJson(method, path, body) {
  ...
  } catch (error) {
    throw new Error("Die Brücke ist nicht erreichbar – sie läuft möglicherweise nicht.");
  }
}
```
→
```javascript
async function requestJson(method, path, body) {
  ...
  } catch (error) {
    throw new Error(t("web.errors.bridge_unreachable"));
  }
}
```
(match this exact pattern in BOTH `requestJson` and `requestDownload` — inventory confirms the literal is duplicated verbatim in both)

`readErrorDetail`'s fallback (`app.js:132`, `` `HTTP ${response.status}` ``) → `t("web.errors.http_status", { status: response.status })`.

`formatTimestamp`/`formatValue` (around lines 1663-1677):
```javascript
if (!isoTimestamp) return "noch nie";
...
return value ? "wahr" : "falsch";
```
→
```javascript
if (!isoTimestamp) return t("web.format.never");
...
return value ? t("web.format.true") : t("web.format.false");
```

Also in `formatTimestamp`: `new Date(isoTimestamp).toLocaleString("de-DE")` → the locale must follow the active language, not stay hardcoded:
```javascript
new Date(isoTimestamp).toLocaleString(this.language === "de" ? "de-DE" : "en-US")
```
(check whether `formatTimestamp` is itself a method on `app()` — if `this` is not available there, use the reactive `language` field via whichever object is in scope when this function is called, e.g. pass it as a parameter, or reference `document.documentElement.lang` instead of `this.language` if `formatTimestamp` turns out to be a free function; read the actual function signature before deciding, don't guess)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -v`

- [ ] **Step 8: Manually verify in the browser**

Start the dev server. Confirm: nav tabs, header, logout button, and (if reachable without a full backend) the setup/login screen all render in English by default, with no `{key}`-looking raw text and no `undefined`. Force German by editing the language setting directly in the test database the dev server uses (or via whatever `scripts/dev_web_server.py` exposes) and reload — confirm the same elements now render in German with correct umlauts.

- [ ] **Step 9: Run the full test suite and commit**

Run: `uv run pytest -q -m "not slow"`, `uv run ruff check src/loxmatter/`, `uv run mypy src/loxmatter/`, all clean (these are JS files, ruff/mypy only cover the Python test file changes — still run them for the whole `src/loxmatter/` tree to catch anything unexpected).

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Navigation, Kopfzeile, Zugangsbildschirme uebersetzt

Erste inhaltliche WebUI-Aufgabe: Reiterleiste, Kopfzeile,
Verbindungsstatus, Ersteinrichtung/Anmeldung, sowie die
Formatierungs- und generischen Netzwerk-Fehlerhelfer in app.js -
letztere als Beleg, dass t() auch aus freien Funktionen heraus
funktioniert, nicht nur aus Alpine-Ausdruecken.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

### Task 11: Geraeteliste, Einlernen (Dashboard-Tab)

Follows Task 10's pattern (see its intro for methodology/verification — applies unchanged here). New pattern introduced by this task: the "set the bridge IP first" hint splits across a prefix, a clickable link, and a suffix — the link keeps its own live `@click` binding, so it does NOT collapse into one `x-html` blob.

**Files:** `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `tests/api/test_web.py`
**Interfaces:** Consumes `t()` (Task 8), all `web.devices.*` and `web.export.bridge_ip_missing`/`web.settings.miniserver_link` keys (Task 9).

- [ ] **Step 1-2:** Write failing tests in `tests/api/test_web.py` asserting the served source no longer contains `"Neues Gerät einlernen"` (index.html) and no longer contains `"wurde exportiert."` as a bare string in app.js (now via `t(...)`); run to confirm failure.

- [ ] **Step 3: Bind the commissioning card and device-list text (`index.html:174-332`, inventory §4)**

Static text nodes (same `x-text`/`x-html` pattern as Task 10) for: `web.devices.commission_heading` (176), the two input `placeholder`s → `:placeholder="t(...)"` (181, 186), `web.devices.commission_submit` (189-190), `web.devices.commission_hint` (192-196), `web.devices.empty` (208-210), `web.devices.changed_since_export` (226), `web.devices.offline` (230), `web.devices.remove` (232), `web.devices.values_heading` (236), `web.devices.signals_loading` (237), `web.devices.no_functional_signals` (240-242), `web.devices.more_in_signals_view` (264-267, keep the `x-text="remainingSignalCount(device.id)"` count span exactly as-is, only translate the surrounding text node), `web.devices.controls_heading` (271), `web.devices.controls_loading` (272-274), `web.devices.no_known_commands` (275-279), `web.devices.value_placeholder` as `:placeholder` (295), `web.devices.send` (298-303), `web.devices.more_commands_unnamed` (308-310, same count-span caveat), `web.devices.export` (318-324).

The bridge-IP hint (`index.html:326-328`, worked example for the 3-part pattern):
```html
<p class="hint" x-show="!bridgeSettings.bridge_ip" x-cloak>
  Erst in
  <a href="#" @click.prevent="view = 'settings'">Einstellungen → Verbindung zum Miniserver</a>
  hinterlegen.
</p>
```
(read the actual markup first — the `@click`/`href` shape may differ from this sketch, keep whatever is actually there unchanged)
→
```html
<p class="hint" x-show="!bridgeSettings.bridge_ip" x-cloak>
  <span x-text="t('web.devices.export_hint_prefix')"></span>
  <a href="#" @click.prevent="view = 'settings'" x-text="t('web.settings.miniserver_link')"></a>
  <span x-text="t('web.devices.export_hint_suffix')"></span>
</p>
```

- [ ] **Step 4: Bind `app.js`'s device-list dynamic strings (inventory §4, §9)**

Direct `t(...)` substitutions, one per line — `598` (`web.devices.list_load_error`, `{message: error.message}`), `632` (`web.devices.controls_load_error`), `686` (`web.devices.export_never`), `688` (`web.devices.export_last`, `{timestamp: this.formatTimestamp(status.exported_at)}`), `783` (`web.devices.label_save_error`), `816` (`web.devices.remove_error`), `825` (`web.devices.command_sent`, `{slug: command.slug, label: device.label}`), `827` (`web.devices.command_failed`, `{slug: command.slug, message: error.message}`), `1189` (`web.devices.exported_toast`, `{label: device.label}`), `1191` (`web.devices.export_failed`), the three copies of the "set bridge IP first" validation at `1177-1179` (this one; the other two live in Task 13) → `t("web.export.bridge_ip_missing")`, `918` (`web.devices.commission_code_required`), `944-947` (`web.devices.commission_success`, `{label: device.label}` — this key's translation already contains the full multi-sentence message, replace the whole concatenated literal with one `t(...)` call), `952` (`web.devices.commission_failed`).

The `window.confirm(...)` in `removeDevice` (`app.js:799-805`) — replace the entire hand-built template-literal with one call:
```javascript
window.confirm(t("web.devices.remove_confirm", { label: device.label, id: device.id }))
```
(verify the rendered result still has the same paragraph breaks as before — `web.devices.remove_confirm`'s YAML block-scalar value in Task 9 preserves them)

- [ ] **Step 5-7:** Run tests, verify in browser (commission form, device cards, remove-confirm dialog text — the native `confirm()` dialog itself can't be styled, just confirm its text is now `t()`-sourced and reads correctly in both languages), run full suite + lint/type-check, commit as `feat(web): Geraeteliste und Einlernen uebersetzt` with the same trailer convention as prior tasks.

### Task 12: Signalansicht

**Files:** `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `tests/api/test_web.py`
**Interfaces:** Consumes `t()`, all `web.signals.*` keys.

- [ ] **Step 1-2:** Failing tests as before (assert `"Zugeklappt"` / `"Rohwert schreiben"` no longer in served source).

- [ ] **Step 3: Bind `index.html:337-465` (inventory §5)** — same `x-text`/`x-html` pattern as prior tasks: `web.signals.key_hint` (339-342), `web.signals.functional_vs_expert_explanation` (343-348), `web.signals.show_expert` (350-352), `web.signals.load_button` (361), `web.signals.none_functional` (384-386), `web.signals.expert_collapsed_hint` (387-391, `{count: group.signals.length}` — keep the existing count binding, wrap only the surrounding text), `web.signals.key_tooltip` as `:title` (398), `web.signals.export_checkbox` (424, 430), `web.signals.raw_write_placeholder` as `:placeholder` (441), `web.signals.raw_write_submit` (444-449).

- [ ] **Step 4: Bind `app.js`'s signal group titles and dynamic strings (inventory §5)** — `761`/`762` (`signalGroupsFor`'s object-literal group titles) → `t("web.signals.group_functional")`/`t("web.signals.group_expert")`; `971` (`web.signals.load_error`), `984` (`web.signals.title_save_error`), `995` (`web.signals.export_flag_error`), `1007` (`web.signals.write_success`). Leave `app.js:1009` (`rawWriteMessages[...].text = error.message`) UNTOUCHED — per Task 9's scope note, this passes the backend's own already-translated `detail` text straight through; wrapping it in `t()` would be wrong (there is no `web.*` key for arbitrary backend text).

- [ ] **Step 5-7:** Tests, browser check (load a device's signals, toggle expert view, attempt a raw write), full suite, commit as `feat(web): Signalansicht uebersetzt`.

### Task 13: Export-Tab

**Files:** `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `tests/api/test_web.py`
**Interfaces:** Consumes `t()`, all `web.export.*` and `web.bridge_ip_label` keys.

- [ ] **Step 1-2:** Failing tests (assert `"Vorlagen exportieren"` / `"Übersprungen"` no longer in served source).

- [ ] **Step 3: Bind `index.html:470-557` (inventory §6)** — `web.export.heading` (472), `web.bridge_ip_label` (475, SHARED key — same one Task 15 binds on the settings tab), `web.export.udp_port_label` (478), `web.export.http_port_label` (480-481), the settings-managed hint (484-488) with the SAME 3-part prefix/link/suffix pattern as Task 11's bridge-IP hint (`web.export.settings_hint_prefix` / `web.settings.miniserver_link` / `web.export.settings_hint_suffix`), `web.export.include_system` (490), `web.export.only_pending` (491-494), `web.export.filter_explanation` via `x-html` (496-500, has inline `<strong>und</strong>`), `web.export.preview_button` (502-504), `web.export.download_button` (511), `web.export.preview_heading` (517), the eight table headers `web.export.col_device`/`col_viu`/`col_vo`/`col_inputs`/`col_commands`/`col_skipped`/`col_expert_withheld`/`col_last_exported` (522-529), `web.export.expert_withheld_explanation` (548-552), `web.export.system_files_prefix` (553-555, keep the following `x-text` that joins `exportPreview.system_files` unchanged, only translate the prefix).

- [ ] **Step 4: Bind `app.js`'s export-tab dynamic strings (inventory §6)** — `1034` (`web.export.status_load_error`), the remaining two of the three `web.export.bridge_ip_missing` copies at `1084-1086` and `1151-1153` (the third was Task 11's, at `1177-1179`), `1097` (`web.export.preview_failed`), `1158` (`web.export.download_failed`).

- [ ] **Step 5-7:** Tests, browser check (export tab: preview, download, the include-system and only-pending checkboxes), full suite, commit as `feat(web): Export-Tab uebersetzt`.

### Task 14: System-/Diagnose-Tab

**Files:** `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `tests/api/test_web.py`
**Interfaces:** Consumes `t()`, all `web.system.*` keys.

- [ ] **Step 1-2:** Failing tests (assert `"Systemcheck"` / `"Live-Diagnose"` no longer in served source).

- [ ] **Step 3: Bind `index.html:562-690` (inventory §7)** — most entries are plain `x-text`, same pattern as prior tasks: `web.system.checks_heading` (565), `web.system.refresh` (566), `web.system.live_heading` (590), `web.system.hide_noise` (605), `web.system.log_level_label` (617), the four log-level `<option>`s `web.system.log_level_info`/`log_level_warn`/`log_level_critical` (619,620,622) and `web.system.check_error` reused for the "Fehler" option (621), `web.system.clear` (625), `web.system.logs_heading` (636), `web.system.logs_hint` (637), `web.system.udp_heading` (649), `web.system.udp_hint` (650-652), `web.system.command_log_heading` (664), `web.system.command_log_hint` (665), `web.system.backup_heading` (677), `web.system.backup_explanation` (678-682), `web.system.backup_access_note` (683-686), `web.system.backup_download` (687).

Three entries are literals EMBEDDED INSIDE `x-text` ternary expressions, not separate text nodes (Task 9's scope note and the inventory's own flag #3 — easy to miss, look for `x-text="... ? '...' : '...'"`):
```html
<span x-text="check.ok ? 'OK' : 'Fehler'"></span>
```
→
```html
<span x-text="check.ok ? t('web.system.check_ok') : t('web.system.check_error')"></span>
```
Apply the same substitution to the other two: `diagnosticsConnected ? 'Live-Verbindung aktiv' : 'Verbindung getrennt – verbinde neu…'` (`index.html:594-595`) → `diagnosticsConnected ? t('web.connection.live') : t('web.system.diag_disconnected')` (reuses Task 10's `web.connection.live` — same text, same meaning), and `diagnosticsPaused ? 'Fortsetzen' : 'Pausieren'` (`index.html:600-602`) → `diagnosticsPaused ? t('web.system.resume') : t('web.system.pause')`.

The pause/clear explanation (`index.html:628-632`) has an embedded `<span class="key">tail -f</span>` → `x-html="t('web.system.pause_clear_explanation')"` (Task 9's value already carries that span).

- [ ] **Step 4: Bind `app.js`'s system/diagnostics dynamic strings** — `1213` (`web.system.load_error`), `1228` (`web.system.backup_error`).

- [ ] **Step 5-7:** Tests, browser check (system tab: run the system check, toggle log-level filter, pause/resume live diagnostics, download the fabric backup if reachable), full suite, commit as `feat(web): System-/Diagnose-Tab uebersetzt`.

### Task 15: Einstellungen-Tab und Sprachumschalter

The one task in this group that adds NEW markup, not just translates existing text — the language toggle itself (Spec §7, confirmed design: EN/DE buttons, `PATCH /api/language` then `window.location.reload()`).

**Files:** `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `tests/api/test_web.py`
**Interfaces:** Consumes `t()`, all `web.settings.*` and `web.bridge_ip_label` keys; `PATCH /api/language` (Task 1).

- [ ] **Step 1-2:** Failing tests (assert `"Verbindung zum Miniserver"` no longer in served source; assert the served `app.js` contains a new `setLanguage` — or whatever name Step 5 below settles on — function).

- [ ] **Step 3: Bind `index.html:695-730` (inventory §8)** — `web.settings.connection_heading` (697), `web.settings.connection_explanation` via `x-html` (698-705, has `<strong>Nicht</strong>` AND a `<span class="key">` wrapping the URL example), `web.bridge_ip_label` (708, SHARED with Task 13's export-tab label), `web.settings.bridge_ip_placeholder` as `:placeholder` (709), `web.settings.udp_port_label` (711), `web.settings.http_port_label` (713-714), `web.settings.save` (718-720), `web.settings.last_saved_prefix` (721-723, keep the following `x-text="formatTimestamp(...)"` unchanged, only translate the prefix), `web.settings.never_saved` (724-726).

- [ ] **Step 4: Replace the "Weitere Einstellungen" placeholder card (`index.html:731-734`) with the language toggle**

Current (per Task 9's note, this placeholder text itself never gets a translation key — it's replaced, not translated):
```html
<div class="card">
  <h2>Weitere Einstellungen</h2>
  <p class="hint">Hier entstehen künftig weitere Einstellungen, sobald sie gebraucht werden.</p>
</div>
```
→
```html
<div class="card">
  <h2 x-text="t('web.settings.language_heading')"></h2>
  <div class="button-group">
    <button :class="{ active: language === 'en' }" @click="setLanguage('en')" x-text="t('web.settings.language_en')"></button>
    <button :class="{ active: language === 'de' }" @click="setLanguage('de')" x-text="t('web.settings.language_de')"></button>
  </div>
</div>
```
(`class="button-group"` is a guess at a reasonable class name — check `style.css` first for whatever button-grouping class this project's existing markup already uses elsewhere, e.g. the nav tabs or the log-level options, and reuse THAT class instead of inventing a new one; do not add new CSS in this task if an existing pattern already covers a row of toggle buttons)

- [ ] **Step 5: Implement `setLanguage` in `app.js`**

Add next to `saveSettings` (the existing settings-save method, follow its exact error-handling shape):
```javascript
/** Setzt die gemeinsame Spracheinstellung (PATCH /api/language, Aufgabe 1)
 * und laedt danach die ganze Seite neu - bestaetigte, einfachere Variante
 * aus dem Entwurfsgespraech (Spec Abschnitt 7): kein Sonderfall fuer
 * bereits angezeigte Toasts oder WebSocket-Zustaende, die sonst in der
 * alten Sprache stehen blieben. */
async setLanguage(language) {
  if (language === this.language) {
    return;
  }
  await this.request("PATCH", "/api/language", { language });
  window.location.reload();
},
```
(check `this.request`'s exact signature against its real definition — Task 8's context already showed it as `async request(method, path, body)`; match it exactly, don't guess at argument order)

- [ ] **Step 6: Bind `app.js`'s remaining settings dynamic strings (inventory §8)** — `1056` (`web.settings.load_error`), `1063` (`web.settings.bridge_ip_required`), `1073` (`web.settings.saved_toast`), `1075` (`web.settings.save_error`).

- [ ] **Step 7-9:** Tests, browser check (settings tab renders and saves correctly; click the German toggle, confirm the page reloads and now renders in German including the toggle itself now showing the German button as active; switch back to English), full suite, commit as `feat(web): Einstellungen-Tab uebersetzt, Sprachumschalter`.
