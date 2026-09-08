# Updates über die Oberfläche, Stufe 1: Identität und Auslieferung

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Brücke weiß und zeigt, welche Fassung sie ist, die CI veröffentlicht fertige Images nach GHCR, und ein Update über die Konsole zieht ein Image, statt auf dem Pi zu bauen.

**Architecture:** Die Identität kommt aus der Umgebung des Images (`ENV`, gesetzt aus Build-Argumenten der CI), nicht aus dem Checkout auf dem Host — der kann inzwischen woanders stehen. Ein neues Modul `loxmatter.version` liest sie, `GET /api/version` gibt sie aus, die WebUI zeigt sie im System-Tab. Die Compose-Datei wechselt auf `image:` mit einem über `.env` steuerbaren Tag; `build:` bleibt daneben als Rückfallebene stehen.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, Alpine.js (vendort), Docker Compose, GitHub Actions, `docker/build-push-action` (buildx, multi-arch), pytest, ruff, mypy.

**Grundlage:** [`docs/superpowers/specs/2026-09-08-webui-updates-design.md`](../specs/2026-09-08-webui-updates-design.md), Abschnitte 4, 5, 15. Stufe 2 (Beiwagen, `/api/update/*`, die Update-Karte) hat einen eigenen Plan und setzt diesen hier voraus.

## Global Constraints

- **Jede neue Quelldatei beginnt mit dem GPL-Kopf** in der englischen FSF-Formulierung, wortgleich zu bestehenden Dateien (z. B. `src/loxmatter/api/settings.py:1-15`). Das ist die eine bewusste Ausnahme von der deutschen Prosa — er ist ein Rechtsverweis auf `LICENSE`, kein zu übersetzender Text.
- **Entwicklerprosa auf Deutsch:** Docstrings, Kommentare, Commit-Nachrichten. Dicht und begründend — *warum* eine Entscheidung so fiel, nicht nur was der Code tut.
- **Jeder nutzersichtbare Text geht über `i18n.t()`** mit `en`- **und** `de`-Eintrag in `src/loxmatter/i18n/strings.yaml`. Kein fest verdrahtetes Deutsch in `cli.py`, `api/*` oder der WebUI.
- **Registry und Repository:** `ghcr.io/lucienkerl/loxmatter`, `github.com/lucienkerl/loxmatter`.
- **Die Vier Build-Argumente heißen exakt** `LOXMATTER_VERSION`, `LOXMATTER_COMMIT`, `LOXMATTER_BUILT_AT`, `LOXMATTER_SCHEMA_VERSION`.
- **Der Compose-Tag-Schalter heißt exakt** `LOXMATTER_IMAGE_TAG`, Vorgabe `stable`.
- **Die volle Testsuite braucht rund drei Minuten.** Das sieht aus wie ein Hänger, ist keiner — nicht abbrechen.
- **Vor jedem Commit müssen durchlaufen:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.
- **Jeder Test muss einmal probeweise scheitern.** Ein Test, der eine Struktur nur benennt statt sie zu prüfen, ist in diesem Projekt schon vorgekommen. Schritt „Run test to verify it fails" ist keine Formalie.

## File Structure

| Datei | Verantwortung |
|---|---|
| `src/loxmatter/version.py` (neu) | Liest die Bau-Identität aus der Umgebung. Einzige Stelle, die `os.environ` dafür anfasst. |
| `src/loxmatter/model/store.py` (ändern) | Bekommt `schema_version()` als öffentlichen Zugang zur bisher privaten `_SCHEMA_VERSION`. |
| `src/loxmatter/api/version.py` (neu) | `GET /api/version`. Ein Router, ein Modell, keine Logik. |
| `src/loxmatter/loxone/server.py` (ändern) | Bindet den neuen Router ein, hinter demselben Wächter wie alle `/api`-Routen. |
| `src/loxmatter/web/index.html`, `app.js` (ändern) | Karte „Version" im System-Tab. |
| `src/loxmatter/i18n/strings.yaml` (ändern) | Die neuen `web.system.version.*`-Schlüssel. |
| `Dockerfile` (ändern) | Vier `ARG`/`ENV`-Paare. |
| `.github/workflows/ci.yml` (ändern) | Neuer Job `image`: multi-arch bauen und nach GHCR schieben. |
| `deploy/testhost/docker-compose.yml` (ändern) | `image:` neben `build:`. |
| `scripts/update.sh` (ändern) | `compose pull` statt `compose build`. |
| `CHANGELOG.md` (neu), `docs/DEVELOPMENT.md` (ändern), `README.md` (ändern) | Was ein Release ausmacht, und wie man aktualisiert. |
| `tests/test_version.py`, `tests/api/test_version_api.py`, `tests/test_build_arguments.py`, `tests/test_update_script.py` (neu) | siehe jeweilige Task. |

---

### Task 1: `loxmatter.version` — die Identität aus der Umgebung

**Files:**
- Create: `src/loxmatter/version.py`
- Modify: `src/loxmatter/model/store.py` (neue öffentliche Funktion `schema_version()`, direkt unter `_SCHEMA_VERSION = 7`)
- Test: `tests/test_version.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `loxmatter.version.BuildInfo` (frozen dataclass mit `version: str`, `commit: str | None`, `built_at: str | None`, `schema_version: int`) und `loxmatter.version.build_info() -> BuildInfo`. `loxmatter.model.store.schema_version() -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_version.py` (mit GPL-Kopf, wortgleich aus `src/loxmatter/api/settings.py:1-15` übernommen):

```python
"""Tests fuer die Bau-Identitaet - Entwurf "Updates ueber die Oberflaeche
einspielen" (2026-09-08), Abschnitt 4.

Die vier Faelle unten decken genau die vier Wege ab, auf denen diese
Angaben falsch sein koennten: gar nicht gesetzt (Entwicklungscheckout),
leer gesetzt (Docker Compose interpoliert eine fehlende .env-Variable zu
einem leeren String), richtig gesetzt, und - der wichtigste - die
Schema-Version, die sich aus der Umgebung NICHT faelschen laesst."""

from __future__ import annotations

from loxmatter.model import store as store_module
from loxmatter.version import build_info


def test_ohne_umgebung_meldet_sich_die_bruecke_als_entwicklungsstand(monkeypatch):
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None
    assert info.built_at is None


def test_leere_variablen_gelten_wie_fehlende(monkeypatch):
    # Docker Compose interpoliert eine in .env fehlende Variable zu einem
    # LEEREN String, nicht zu "nicht gesetzt" - dieselbe Falle, die bei
    # LOXMATTER_API_TOKEN schon einmal zuschlug (siehe Compose-Datei).
    monkeypatch.setenv("LOXMATTER_VERSION", "")
    monkeypatch.setenv("LOXMATTER_COMMIT", "   ")
    monkeypatch.delenv("LOXMATTER_BUILT_AT", raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None


def test_gesetzte_variablen_kommen_unveraendert_durch(monkeypatch):
    monkeypatch.setenv("LOXMATTER_VERSION", "0.3.0")
    monkeypatch.setenv("LOXMATTER_COMMIT", "a3f91c2")
    monkeypatch.setenv("LOXMATTER_BUILT_AT", "2026-09-08T10:00:00Z")
    info = build_info()
    assert info.version == "0.3.0"
    assert info.commit == "a3f91c2"
    assert info.built_at == "2026-09-08T10:00:00Z"


def test_die_schema_version_laesst_sich_aus_der_umgebung_nicht_faelschen(monkeypatch):
    """Die einzige Angabe, die NICHT aus der Umgebung kommt.

    Im Image steht sie zusaetzlich als ENV - aber fuer den Updater aus
    Stufe 2, der sie mit `docker inspect` aus einem noch nicht gestarteten
    Image liest. Der laufende Prozess hat sie ohnehin im Speicher, und eine
    zweite Quelle waere eine Quelle, die irgendwann etwas anderes
    behauptet."""
    monkeypatch.setenv("LOXMATTER_SCHEMA_VERSION", "999")
    assert build_info().schema_version == store_module._SCHEMA_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.version'`

- [ ] **Step 3: Add `schema_version()` to the store**

In `src/loxmatter/model/store.py`, direkt unter der Zeile `_SCHEMA_VERSION = 7`:

```python
def schema_version() -> int:
    """Die Schema-Version dieses Moduls, oeffentlich lesbar.

    `_SCHEMA_VERSION` bleibt privat: wer sie aendert, soll den langen
    Kommentarblock darueber sehen, der jede einzelne Stufe begruendet.
    Diese Funktion gibt sie nach aussen, damit `loxmatter.version` und die
    CI nicht auf einen privaten Namen zugreifen muessen - und damit es
    genau EINE Quelle fuer diese Zahl gibt.
    """
    return _SCHEMA_VERSION
```

- [ ] **Step 4: Write the module**

`src/loxmatter/version.py` (GPL-Kopf, dann):

```python
"""Woher die laufende Fassung ihre Identitaet kennt - Entwurf "Updates
ueber die Oberflaeche einspielen" (2026-09-08), Abschnitt 4.

Die Angaben kommen aus der UMGEBUNG, nicht aus dem Checkout auf dem Host.
`Dockerfile` legt sie beim Bau als `ENV` ab, gespeist aus Build-Argumenten,
die die CI setzt. Der Grund fuer diese Richtung: ein Checkout auf dem Host
kann inzwischen woanders stehen, weitergewandert oder umgezogen sein, ohne
dass das je ausgeliefert wurde - das Image dagegen IST, was laeuft.

Ausserhalb eines Images - im Entwicklungscheckout, wo `uv run loxmatter`
direkt startet - fehlen die Variablen. Das ist kein Fehlerfall, sondern
der Normalfall beim Entwickeln: `version` heisst dann "dev",
`commit`/`built_at` sind None. Wer daraus eine Ausnahme machte, koennte
die Bruecke ausserhalb von Docker nicht mehr starten.
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
    """Leere Umgebungsvariablen wie fehlende behandeln.

    Docker Compose interpoliert eine in `.env` fehlende Variable zu einem
    LEEREN String, nicht zu "nicht gesetzt". Genau diese Falle hat bei
    `LOXMATTER_API_TOKEN` schon einmal zugeschlagen (siehe die ausfuehrliche
    Begruendung in deploy/testhost/docker-compose.yml); ohne diese Funktion
    hiesse die Version auf einem Host ohne gesetzten Wert "" statt "dev".
    """
    value = os.environ.get(name, "").strip()
    return value or None


def build_info() -> BuildInfo:
    return BuildInfo(
        version=_clean("LOXMATTER_VERSION") or "dev",
        commit=_clean("LOXMATTER_COMMIT"),
        built_at=_clean("LOXMATTER_BUILT_AT"),
        # Bewusst NICHT aus der Umgebung: siehe Docstring von
        # `test_die_schema_version_laesst_sich_aus_der_umgebung_nicht_faelschen`.
        schema_version=schema_version(),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_version.py -v`
Expected: 4 passed

- [ ] **Step 6: Prove the last test can fail**

Ändere `version.py` probeweise auf `schema_version=int(os.environ.get("LOXMATTER_SCHEMA_VERSION", schema_version()))`, führe `uv run pytest tests/test_version.py -v` aus.
Expected: `test_die_schema_version_laesst_sich_aus_der_umgebung_nicht_faelschen` FAILT (`999 != 7`). Danach die Änderung zurücknehmen und erneut laufen lassen: 4 passed.

- [ ] **Step 7: Lint, types, full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: alles grün (die volle Suite braucht rund drei Minuten)

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/version.py src/loxmatter/model/store.py tests/test_version.py
git commit -m "feat(version): Bau-Identitaet aus der Umgebung lesen

Die Bruecke konnte bisher nicht sagen, welche Fassung sie ist:
pyproject.toml steht seit 628 Commits auf 0.1.0, und der git-Stand liegt
im Checkout auf dem Host, den der laufende Prozess nicht kennt. Ein
Update setzt aber voraus, dass 'vorher' und 'nachher' benennbar sind.

Die Angaben kommen deshalb aus dem Image selbst. Leere Werte gelten wie
fehlende - Docker Compose interpoliert eine fehlende .env-Variable zu
einem leeren String, dieselbe Falle wie seinerzeit bei
LOXMATTER_API_TOKEN.

Die Schema-Version nimmt bewusst den anderen Weg und kommt aus
model.store, nicht aus der Umgebung: der laufende Prozess hat sie
ohnehin, und eine zweite Quelle waere eine, die irgendwann etwas anderes
behauptet. Ein Test belegt, dass ein gesetztes LOXMATTER_SCHEMA_VERSION
sie nicht verschieben kann.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `GET /api/version`

**Files:**
- Create: `src/loxmatter/api/version.py`
- Modify: `src/loxmatter/loxone/server.py` (Import und ein `include_router`-Aufruf neben `build_language_router`, um Zeile 494)
- Test: `tests/api/test_version_api.py`

**Interfaces:**
- Consumes: `loxmatter.version.build_info()` aus Task 1.
- Produces: `loxmatter.api.version.build_version_router() -> APIRouter` mit Präfix `/api`; Antwortmodell `VersionOut(version: str, commit: str | None, built_at: str | None, schema_version: int)`. Die WebUI in Task 4 liest genau diese vier Felder.

- [ ] **Step 1: Write the failing test**

`tests/api/test_version_api.py` (GPL-Kopf, dann):

```python
"""Tests fuer GET /api/version.

Die `api`-Fixture folgt demselben Muster wie in `test_language.py`: eine
lokale, bereits ANGEMELDETE Fixture. `unauthenticated_api` daneben belegt,
dass diese Route KEINE der drei bewussten Ausnahmen von der
Anmeldepflicht ist (`/cmd`, `/resync`, `GET /api/i18n`)."""

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
async def unauthenticated_api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[httpx.AsyncClient]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    store.close()


async def test_die_route_nennt_die_vier_angaben(api, monkeypatch):
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


async def test_im_entwicklungscheckout_antwortet_sie_trotzdem(api, monkeypatch):
    """Kein 500, wenn die Variablen fehlen - sonst waere die Oberflaeche
    ausserhalb von Docker unbenutzbar."""
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    response = await api.get("/api/version")
    assert response.status_code == 200
    assert response.json()["version"] == "dev"
    assert response.json()["commit"] is None


async def test_ohne_sitzung_kein_zugriff(unauthenticated_api):
    response = await unauthenticated_api.get("/api/version")
    assert response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_version_api.py -v`
Expected: FAIL — die beiden ersten mit `404`, weil die Route noch nicht existiert

- [ ] **Step 3: Write the router**

`src/loxmatter/api/version.py` (GPL-Kopf, dann):

```python
"""Die Bau-Identitaet ueber die API - Entwurf "Updates ueber die
Oberflaeche einspielen" (2026-09-08), Abschnitt 4.

`build_version_router` baut einen `APIRouter` mit Praefix `/api`, genau wie
`api.settings.build_settings_router` - eingebunden in
`loxone.server.build_app` hinter demselben `api_guard`.

Anders als `GET /api/i18n` ist diese Route NICHT von der Anmeldepflicht
ausgenommen: die Anmeldeseite braucht sie nicht, um sich anzuzeigen. Wer
die Version wissen will, soll angemeldet sein - eine Versionsnummer ist
fuer jemanden, der ohnehin schon im Netz steht, ein brauchbarer Hinweis
darauf, welche bekannten Luecken diese Installation noch hat.

Keine Zwischenspeicherung: `build_info()` liest `os.environ`, und das ist
im laufenden Prozess unveraenderlich - aber die Tests setzen die Variablen
mit `monkeypatch` pro Testfall, und ein Cache machte genau diese Tests
voneinander abhaengig."""

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

In `src/loxmatter/loxone/server.py`, beim Importblock der übrigen Router:

```python
from loxmatter.api.version import build_version_router
```

und direkt nach der Zeile `app.include_router(build_language_router(store), dependencies=api_guard)`:

```python
    app.include_router(build_version_router(), dependencies=api_guard)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_version_api.py -v`
Expected: 3 passed

- [ ] **Step 6: Prove the guard test can fail**

Entferne probeweise `dependencies=api_guard` aus dem neuen `include_router`-Aufruf, führe `uv run pytest tests/api/test_version_api.py -v` aus.
Expected: `test_ohne_sitzung_kein_zugriff` FAILT (`200 != 401`). Danach zurücknehmen, erneut laufen lassen: 3 passed.

- [ ] **Step 7: Lint, types, full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: alles grün

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/api/version.py src/loxmatter/loxone/server.py tests/api/test_version_api.py
git commit -m "feat(api): GET /api/version

Gibt die vier Angaben aus Task 1 aus, hinter demselben Waechter wie jede
andere /api-Route. Bewusst KEINE vierte Ausnahme von der Anmeldepflicht:
die Anmeldeseite braucht die Version nicht, um sich anzuzeigen, und eine
Versionsnummer ist fuer jemanden im selben Netz ein brauchbarer Hinweis
darauf, welche bekannten Luecken diese Installation noch hat.

Ohne Zwischenspeicherung, obwohl os.environ im laufenden Prozess
unveraenderlich ist: ein Cache machte die Tests voneinander abhaengig,
die die Variablen je Fall mit monkeypatch setzen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `Dockerfile` — die vier Build-Argumente

**Files:**
- Modify: `Dockerfile` (nach dem `ENV PATH=`-Block, vor `EXPOSE 8080`)

**Interfaces:**
- Consumes: nichts.
- Produces: vier `ARG`-Deklarationen mit exakt den Namen `LOXMATTER_VERSION`, `LOXMATTER_COMMIT`, `LOXMATTER_BUILT_AT`, `LOXMATTER_SCHEMA_VERSION`, jeweils als gleichnamiges `ENV` gesetzt. Task 4 prüft, dass die CI genau diese vier durchreicht, und liest `LOXMATTER_SCHEMA_VERSION` später (Stufe 2) per `docker inspect`.

- [ ] **Step 1: Add the arguments**

Im `Dockerfile`, unmittelbar vor der Zeile `EXPOSE 8080`:

```dockerfile
# Die Bau-Identitaet (Entwurf "Updates ueber die Oberflaeche einspielen",
# 2026-09-08, Abschnitt 4). Gesetzt von der CI, gelesen von
# `loxmatter/version.py` und - fuer LOXMATTER_SCHEMA_VERSION - vom Updater
# aus Stufe 2, der sie mit `docker inspect` aus einem Image liest, das er
# noch gar nicht gestartet hat. Genau deshalb steht sie hier als ENV und
# nicht nur im Code: ein `docker inspect` sieht keine Python-Konstante.
#
# Die Vorgaben unten machen einen Bau von Hand (`docker compose build`)
# moeglich, ohne dass jemand vier Argumente kennen muss - er ergibt dann
# ein Image, das sich ehrlich als "dev" ausgibt, statt eine Version zu
# behaupten, die es nicht ist.
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

Run: `docker build --check . 2>&1 | tail -5` (falls Docker vorhanden; sonst überspringen — Task 4 prüft die Konsistenz ohne Docker)
Expected: keine Fehler zu `ARG`/`ENV`

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "build: vier Build-Argumente fuer die Bau-Identitaet

LOXMATTER_SCHEMA_VERSION steht bewusst auch dann als ENV im Image, wenn
der laufende Prozess sie ohnehin aus model.store kennt: der Updater aus
Stufe 2 liest sie mit \`docker inspect\` aus einem Image, das er noch
nicht gestartet hat - und ein docker inspect sieht keine Python-Konstante.

Vorgaben fuer alle vier, damit ein Bau von Hand ohne Argumentkenntnis
gelingt und ein Image ergibt, das sich ehrlich als 'dev' ausgibt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Die WebUI zeigt die Version

**Files:**
- Modify: `src/loxmatter/web/index.html` (neue Karte als **erste** Karte im `view === 'system'`-Abschnitt, vor der Karte „Erneut senden")
- Modify: `src/loxmatter/web/app.js` (Zustand `versionInfo`, Laden in `loadSystem()`)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_version_api.py` (eine Ergänzung, siehe Step 1)

**Interfaces:**
- Consumes: `GET /api/version` aus Task 2, `this.request(...)` und `t(...)` aus `app.js`.
- Produces: Alpine-Zustand `versionInfo` (`{version, commit, built_at, schema_version}` oder `null`), Anzeige im System-Tab. Stufe 2 hängt ihre Update-Karte an genau diese Stelle.

- [ ] **Step 1: Write the failing test**

Ans Ende von `tests/api/test_version_api.py`:

```python
async def test_die_oberflaeche_kennt_alle_texte_der_versionskarte():
    """Ein fehlender Schluessel faellt sonst erst im Browser auf - als
    leeres Feld, nicht als Fehler. Die Liste hier ist die Verbindung
    zwischen index.html und strings.yaml, die sonst niemand prueft."""
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

Run: `uv run pytest tests/api/test_version_api.py::test_die_oberflaeche_kennt_alle_texte_der_versionskarte -v`
Expected: FAIL — `KeyError: 'web.system.version_heading'`

- [ ] **Step 3: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, unmittelbar vor `web.system.resync_heading:`:

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

Im Alpine-Zustandsobjekt, direkt neben `systemChecks`:

```javascript
    // Die Bau-Identitaet (GET /api/version). `null`, solange der System-Tab
    // nicht geoeffnet war - die Karte zeigt dann nichts statt "undefined".
    versionInfo: null,
```

In `loadSystem()`, innerhalb des `try`-Blocks, VOR dem bestehenden `this.systemChecks = ...`:

```javascript
        // Vor den Pruefungen, nicht danach: die Version steht als erste
        // Karte im Tab, und sie soll nicht erst erscheinen, wenn die
        // Pruefungen (die echte Netzarbeit machen) durch sind.
        this.versionInfo = await this.request("GET", "/api/version");
```

- [ ] **Step 6: Add the card in `index.html`**

Als erste Karte innerhalb von `<section x-show="view === 'system'">`, vor der bestehenden Karte mit `t('web.system.resync_heading')`:

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

Ein Browsertest belegt nur, dass Dateien ausgeliefert werden — die Bindungen müssen laufen. Starte `uv run python scripts/dev_web_server.py`, öffne den System-Tab und prüfe:

- Die Karte „Version" steht **oben**, vor „Alle Werte erneut senden".
- Sie zeigt `Läuft: dev` (im Entwicklungscheckout) und darunter den Hinweis auf die Arbeitskopie.
- Die Konsole des Browsers zeigt **keine** Alpine-Fehler und keine `t()`-Warnung über einen fehlenden Schlüssel.

Danach in derselben Sitzung `LOXMATTER_VERSION=0.3.0 LOXMATTER_COMMIT=a3f91c2 uv run python scripts/dev_web_server.py` starten:

- Die Karte zeigt `Läuft: 0.3.0` und `Commit a3f91c2`, und der Arbeitskopie-Hinweis ist **weg**.

- [ ] **Step 8: Lint, types, full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: alles grün

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js src/loxmatter/i18n/strings.yaml tests/api/test_version_api.py
git commit -m "feat(web): Versionskarte im System-Tab

Die Oberflaeche zeigte bisher nirgends, welche Fassung laeuft. Die Karte
steht als erste im Tab und wird vor den Pruefungen geladen - die machen
echte Netzarbeit, und die Version soll nicht auf sie warten.

Im Entwicklungscheckout steht dort 'Laeuft: dev' plus ein Hinweis, dass
diese Bruecke aus einer Arbeitskopie gebaut wurde. Das ist kein
Fehlerzustand, sondern der Normalfall beim Entwickeln - und die einzige
Formulierung, die nicht so tut, als gaebe es hier eine Version.

Der Test prueft die Uebersetzungsschluessel, nicht das Markup: ein
fehlender Schluessel faellt sonst erst im Browser auf, und dort als
leeres Feld statt als Fehler.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: CI baut und veröffentlicht multi-arch nach GHCR

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_build_arguments.py` (neu)

**Interfaces:**
- Consumes: die vier `ARG`-Namen aus Task 3, `schema_version()` aus Task 1.
- Produces: die Images `ghcr.io/lucienkerl/loxmatter:dev`, `:sha-<kurz>` (Push auf `main`) und `:<version>`, `:stable` (Tag `v*`). Task 7 und 8 setzen voraus, dass `:stable` existiert.

- [ ] **Step 1: Write the failing test**

`tests/test_build_arguments.py` (GPL-Kopf, dann):

```python
"""Die CI und das Dockerfile muessen sich ueber dieselben vier Argumente
einig sein.

Dies ist bewusst KEIN Test, der nur die Existenz von vier Zeilen im
Dockerfile behauptet - so einer waere wahr, sobald jemand die Namen
tippt, und bliebe wahr, wenn die CI danach andere durchreicht. Geprueft
wird der Abgleich zwischen beiden Dateien, also genau der Fehler, der
sonst erst am Image auffaellt: ein `--build-arg`, das das Dockerfile nicht
kennt, wird von Docker STILLSCHWEIGEND verworfen (nur eine Warnung), und
das Image traegt dann eine leere Version.

Der dritte Test deckt die dritte Quelle ab: die CI liest die
Schema-Version mit einem grep aus store.py. Aendert sich dort die
Schreibweise der Zeile, liefert der grep leer - und der Test faellt hier,
nicht erst beim naechsten Update auf einem fremden Pi."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from loxmatter.model.store import schema_version

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# Dieselbe Schreibweise, die der grep-Aufruf in der CI benutzt. Beide
# stehen bewusst nebeneinander: der Test ist nur dann etwas wert, wenn er
# dasselbe Muster prueft, das die CI wirklich anwendet.
SCHEMA_PATTERN = r"^_SCHEMA_VERSION = ([0-9]+)$"


def _build_push_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["image"]["steps"]:
        if str(step.get("uses", "")).startswith("docker/build-push-action"):
            return step
    raise AssertionError("Kein docker/build-push-action-Schritt im Job 'image'")


def test_die_ci_reicht_genau_die_argumente_durch_die_das_dockerfile_kennt() -> None:
    declared = set(re.findall(r"^ARG\s+([A-Z_][A-Z0-9_]*)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE))
    passed = {
        line.split("=", 1)[0].strip()
        for line in _build_push_step()["with"]["build-args"].strip().splitlines()
        if line.strip()
    }
    assert passed == declared


def test_beide_architekturen_werden_gebaut() -> None:
    # Der Pi ist der Normalfall dieses Projekts, nicht die Ausnahme. Faellt
    # arm64 weg, bemerkt das niemand, bis ein Nutzer "no matching manifest"
    # liest.
    platforms = _build_push_step()["with"]["platforms"]
    assert "linux/arm64" in platforms
    assert "linux/amd64" in platforms


def test_der_grep_der_ci_findet_die_schema_version() -> None:
    store_source = (ROOT / "src" / "loxmatter" / "model" / "store.py").read_text(encoding="utf-8")
    found = re.findall(SCHEMA_PATTERN, store_source, re.MULTILINE)
    assert len(found) == 1, "genau eine Zeile muss passen, sonst greift der grep daneben"
    assert int(found[0]) == schema_version()


def test_die_ci_benutzt_genau_dieses_muster() -> None:
    workflow_source = WORKFLOW.read_text(encoding="utf-8")
    assert SCHEMA_PATTERN.strip("^$") in workflow_source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_arguments.py -v`
Expected: FAIL — `KeyError: 'image'`, der Job existiert noch nicht

- [ ] **Step 3: Add the `image` job**

Ans Ende von `.github/workflows/ci.yml`:

```yaml

  # Baut und veroeffentlicht das Image (Entwurf "Updates ueber die
  # Oberflaeche einspielen", 2026-09-08, Abschnitt 5). `needs: test` ist
  # nicht Kosmetik: ein Image, das die Testsuite nicht bestanden hat, darf
  # gar nicht erst unter einem Tag stehen, den jemand einspielen kann.
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
          # Die Schema-Version hat genau eine Quelle: store.py. Schlaegt der
          # grep fehl, bricht der Bau ab, statt ein Image mit leerem
          # LOXMATTER_SCHEMA_VERSION zu veroeffentlichen - der Updater aus
          # Stufe 2 koennte damit seine Vorabpruefung nicht machen und
          # muesste raten. tests/test_build_arguments.py prueft dasselbe
          # Muster, damit ein Umbau in store.py hier nicht erst in der CI
          # auffaellt.
          schema="$(sed -n -E 's/^_SCHEMA_VERSION = ([0-9]+)$/\1/p' src/loxmatter/model/store.py)"
          [ -n "$schema" ] || { echo "Keine _SCHEMA_VERSION in store.py gefunden"; exit 1; }
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

Entferne probeweise die Zeile `LOXMATTER_BUILT_AT=...` aus `build-args` und führe `uv run pytest tests/test_build_arguments.py -v` aus.
Expected: `test_die_ci_reicht_genau_die_argumente_durch_die_das_dockerfile_kennt` FAILT. Danach zurücknehmen: 4 passed.

- [ ] **Step 6: Commit and push, then watch the run**

```bash
git add .github/workflows/ci.yml tests/test_build_arguments.py
git commit -m "ci: multi-arch Image nach GHCR veroeffentlichen

Push auf main ergibt :dev und :sha-<kurz>, ein Tag v* ergibt :<version>
und :stable. \`needs: test\` ist nicht Kosmetik - ein Image, das die
Testsuite nicht bestanden hat, darf gar nicht erst unter einem Tag
stehen, den jemand einspielen kann.

Die Schema-Version kommt per sed aus store.py und bricht den Bau ab, wenn
sie dort nicht gefunden wird. Ein leeres LOXMATTER_SCHEMA_VERSION im
Image waere ein Image, dessen Schemasprung der Updater aus Stufe 2 nicht
vorab pruefen koennte - er muesste raten, und das ist genau der Fall, den
die Vorabpruefung abschaffen soll.

Der Test gleicht Dockerfile und Workflow gegeneinander ab, statt die
Existenz von vier Zeilen zu behaupten: ein --build-arg, das das
Dockerfile nicht kennt, verwirft Docker STILLSCHWEIGEND.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push
```

Danach: `gh run watch` bis der Job `image` durch ist, und
`gh api /users/lucienkerl/packages/container/loxmatter/versions --jq '.[0].metadata.container.tags'`
Expected: enthält `dev`

- [ ] **Step 7: Verify the published image actually carries its identity**

```bash
docker pull ghcr.io/lucienkerl/loxmatter:dev
docker inspect ghcr.io/lucienkerl/loxmatter:dev --format '{{json .Config.Env}}' | tr ',' '\n' | grep LOXMATTER
```
Expected: vier Zeilen, `LOXMATTER_VERSION=dev`, ein siebenstelliger Commit, ein Zeitstempel, und `LOXMATTER_SCHEMA_VERSION=7`. **Ist eine davon leer, hier anhalten** — Stufe 2 hängt an genau diesen Werten.

---

### Task 6: Was ein Release ausmacht

**Files:**
- Create: `CHANGELOG.md`
- Modify: `docs/DEVELOPMENT.md` (neuer Abschnitt am Ende)

**Interfaces:**
- Consumes: nichts.
- Produces: die schriftliche Regel, an die sich Task 7 hält.

- [ ] **Step 1: Write `CHANGELOG.md`**

```markdown
# Änderungen

Dieses Projekt vergibt ab 0.2.0 Versionsnummern nach [Semantic
Versioning](https://semver.org/lang/de/). Jede veröffentlichte Version
trägt hier einen Abschnitt, und die Oberfläche zeigt seinen Text als
Änderungsnotizen an, bevor jemand ein Update einspielt — er wird also von
Leuten gelesen, die den Code nicht kennen.

## [Unveröffentlicht]

## [0.2.0] — 2026-09-08

### Neu

- Die Oberfläche zeigt im System-Tab, welche Version läuft.
- Fertige Images liegen unter `ghcr.io/lucienkerl/loxmatter` bereit
  (`arm64` und `amd64`). Ein Update lädt sie, statt auf dem Raspberry Pi
  zu bauen — das dauert statt fünf bis zehn Minuten rund eine.

### Geändert

- `scripts/update.sh` zieht das Image, statt lokal zu bauen. `--build`
  stellt das alte Verhalten wieder her.
- Der Stack läuft aus einem veröffentlichten Image. **Diese eine
  Umstellung braucht einmalig die Konsole:** `git pull &&
  ./scripts/update.sh` auf dem Rechner, auf dem die Brücke läuft.
```

- [ ] **Step 2: Add the release rule to `docs/DEVELOPMENT.md`**

Am Ende der Datei:

```markdown
## Eine Version veröffentlichen

Ab 0.2.0 verlassen sich fremde Installationen auf Versionsnummern: die
Oberfläche vergleicht die laufende Version mit dem letzten Release, und
der Updater spielt genau das ein, was hier veröffentlicht wurde. Diese
Kette wird nicht von Code getragen, sondern von Disziplin — deshalb steht
sie hier.

1. `CHANGELOG.md`: den Abschnitt `[Unveröffentlicht]` auf die neue Nummer
   umschreiben, mit Datum. **Für Leute schreiben, die den Code nicht
   kennen** — dieser Text steht im Bestätigungsdialog vor dem Update.
2. Nummer wählen: `PATCH` für Fehlerbehebungen, `MINOR` für neue
   Funktionen, `MAJOR` für alles, was eine bestehende Installation
   von Hand nachziehen muss.
3. **Steigt `_SCHEMA_VERSION` in `model/store.py`, gehört das in die
   Notizen.** Ein Schemasprung ist der einzige Fall, in dem ein Rückfall
   auf die vorherige Version nicht folgenlos ist (siehe Spec-Abschnitt 8).
4. Commit, dann `git tag -a v0.3.0 -m "0.3.0"` und `git push --tags`.
5. Die CI baut daraus `:0.3.0` und `:stable`. **Erst wenn beide in der
   Registry stehen**, ist die Version veröffentlicht — vorher zeigt die
   Oberfläche sie an, und ein Einspielen liefe ins Leere.
6. GitHub-Release anlegen, dessen Text der Changelog-Abschnitt ist. Die
   Oberfläche liest genau diesen Text.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/DEVELOPMENT.md
git commit -m "docs: Changelog und die Regel, was ein Release ausmacht

Ab 0.2.0 verlassen sich fremde Installationen auf Versionsnummern - die
Oberflaeche vergleicht dagegen, der Updater spielt genau das ein. Diese
Kette wird nicht von Code getragen, sondern von Disziplin; deshalb steht
sie geschrieben, samt der Vorgabe, den Changelog-Text fuer Leute zu
schreiben, die den Code nicht kennen: er steht im Bestaetigungsdialog vor
dem Update.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Die erste veröffentlichte Version

**Files:**
- Modify: `pyproject.toml:3` (`version = "0.1.0"` → `version = "0.2.0"`)

**Interfaces:**
- Consumes: den CI-Job aus Task 5, die Regel aus Task 6.
- Produces: die Tags `ghcr.io/lucienkerl/loxmatter:0.2.0` und `:stable`. **Task 8 und 9 setzen voraus, dass `:stable` existiert** — ohne diese Task zeigt die Compose-Datei danach auf ein Image, das es nicht gibt.

- [ ] **Step 1: Raise the version**

In `pyproject.toml`, Zeile 3: `version = "0.2.0"`

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`
Expected: alles grün

- [ ] **Step 3: Commit, tag, push**

```bash
git add pyproject.toml
git commit -m "release: 0.2.0

Die erste Version, auf die sich eine fremde Installation berufen kann.
pyproject.toml stand seit 628 Commits auf 0.1.0.

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

**Ohne dieses Ergebnis darf Task 8 nicht beginnen.**

- [ ] **Step 5: Create the GitHub release**

```bash
gh release create v0.2.0 --title "0.2.0" --notes-file - <<'EOF'
### Neu

- Die Oberfläche zeigt im System-Tab, welche Version läuft.
- Fertige Images liegen unter `ghcr.io/lucienkerl/loxmatter` bereit (arm64 und amd64). Ein Update lädt sie, statt auf dem Raspberry Pi zu bauen — das dauert statt fünf bis zehn Minuten rund eine.

### Geändert

- `scripts/update.sh` zieht das Image, statt lokal zu bauen. `--build` stellt das alte Verhalten wieder her.
- Der Stack läuft aus einem veröffentlichten Image. **Diese eine Umstellung braucht einmalig die Konsole:** `git pull && ./scripts/update.sh` auf dem Rechner, auf dem die Brücke läuft.
EOF
```

---

### Task 8: Compose läuft aus dem Image

**Files:**
- Modify: `deploy/testhost/docker-compose.yml` (Dienst `loxmatter`)
- Test: `tests/test_compose_profiles.py` (drei Ergänzungen)

**Interfaces:**
- Consumes: `ghcr.io/lucienkerl/loxmatter:stable` aus Task 7.
- Produces: die Umgebungsvariable `LOXMATTER_IMAGE_TAG` als einzige Stelle, an der die laufende Version festgelegt wird. **Stufe 2 schreibt genau diesen Wert in `.env` um, um zurückzufallen.**

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_compose_profiles.py`:

```python
def test_die_bruecke_laeuft_aus_einem_veroeffentlichten_image() -> None:
    # Vor 0.2.0 baute Compose das Image auf dem Pi - fuenf bis zehn
    # Minuten, mit PyPI und Speicher als Fehlerquellen mitten im Update.
    image = _stack()["services"]["loxmatter"]["image"]
    assert image.startswith("ghcr.io/lucienkerl/loxmatter:")
    assert "${LOXMATTER_IMAGE_TAG:-stable}" in image


def test_der_bauweg_bleibt_daneben_bestehen() -> None:
    # `image:` und `build:` am selben Dienst: `compose pull` zieht,
    # `compose build` baut, und `up` baut nur, wenn lokal kein Image liegt.
    # Auf einem Host ohne GHCR-Zugang ist das die Rueckfallebene. Ein
    # Profil waere hier nicht moeglich - Profile gelten fuer Dienste, nicht
    # fuer einzelne Schluessel eines Dienstes.
    assert _stack()["services"]["loxmatter"]["build"]["context"] == "../.."


def test_die_laufende_version_steht_an_genau_einer_stelle() -> None:
    # Stufe 2 setzt darauf auf: der Rueckfall schreibt EINE Zeile in .env
    # zurueck. Taucht der Tag an einer zweiten Stelle auf, faellt nur die
    # eine zurueck und die andere nicht.
    source = COMPOSE.read_text(encoding="utf-8")
    assert source.count("LOXMATTER_IMAGE_TAG") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: FAIL — `KeyError: 'image'`

- [ ] **Step 3: Change the compose file**

Im Dienst `loxmatter`, **vor** dem bestehenden `build:`-Block:

```yaml
    # Seit 0.2.0 aus einem veroeffentlichten Image statt auf dem Pi gebaut
    # (Entwurf "Updates ueber die Oberflaeche einspielen", 2026-09-08,
    # Abschnitt 5). Der Bau brauchte auf dem Test-Pi fuenf bis zehn Minuten
    # und konnte an einem PyPI-Ausfall oder am Speicher scheitern - mitten
    # in einem Update, das jemand ueber den Browser angestossen hat, ist
    # das die falsche Sorte Ueberraschung.
    #
    # LOXMATTER_IMAGE_TAG steht in der .env und ist die EINZIGE Stelle, an
    # der die laufende Version festgelegt wird. Genau deshalb: der Updater
    # (Stufe 2) faellt zurueck, indem er diese eine Zeile zurueckschreibt.
    # Eine zweite Erwaehnung des Tags waere eine, die dabei stehen bliebe.
    #
    # `build:` bleibt darunter stehen. Ein Compose-Profil kaeme dafuer
    # nicht in Frage (Profile gelten fuer Dienste, nicht fuer einzelne
    # Schluessel), und es wird auch keines gebraucht: `compose pull` zieht,
    # `compose build` baut ausdruecklich, und `up` baut nur, wenn lokal
    # gar kein Image liegt. Auf einem Host ohne GHCR-Zugang ist genau das
    # die gewuenschte Rueckfallebene.
    image: ghcr.io/lucienkerl/loxmatter:${LOXMATTER_IMAGE_TAG:-stable}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: 6 passed

- [ ] **Step 5: Prove the single-mention test can fail**

Füge probeweise irgendwo in der Compose-Datei einen Kommentar `# LOXMATTER_IMAGE_TAG` ein, führe die Tests aus.
Expected: `test_die_laufende_version_steht_an_genau_einer_stelle` FAILT (`2 != 1`). Danach entfernen: 6 passed.

- [ ] **Step 6: Verify against the real registry**

```bash
cd deploy/testhost && docker compose pull loxmatter && docker compose config | grep 'image: ghcr'
```
Expected: der Pull gelingt, und `config` zeigt `ghcr.io/lucienkerl/loxmatter:stable`

- [ ] **Step 7: Commit**

```bash
git add deploy/testhost/docker-compose.yml tests/test_compose_profiles.py
git commit -m "build(compose): aus dem veroeffentlichten Image statt vom Pi bauen

LOXMATTER_IMAGE_TAG ist die einzige Stelle, an der die laufende Version
festgelegt wird - der Updater aus Stufe 2 faellt zurueck, indem er genau
diese eine Zeile in der .env zurueckschreibt. Ein Test haelt fest, dass
sie einzig bleibt: eine zweite Erwaehnung waere eine, die beim Rueckfall
stehen bliebe.

build: bleibt daneben stehen, ohne Profil - Profile gelten fuer Dienste,
nicht fuer einzelne Schluessel, und gebraucht wird auch keines: up baut
nur, wenn lokal kein Image liegt. Auf einem Host ohne GHCR-Zugang ist das
die gewuenschte Rueckfallebene.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: `update.sh` zieht, statt zu bauen

**Files:**
- Modify: `scripts/update.sh` (Argumentauswertung um Zeile 36-46, Bauschritt um Zeile 95-102)
- Modify: `README.md` (neuer Abschnitt „Aktualisieren", der bisher fehlt)
- Test: `tests/test_update_script.py` (neu)

**Interfaces:**
- Consumes: die Compose-Datei aus Task 8.
- Produces: `./scripts/update.sh` mit den Schaltern `--no-pull`, `--build`, `--no-cache`, `--help`. Stufe 2 baut denselben Ablauf im Beiwagen nach.

- [ ] **Step 1: Write the failing test**

`tests/test_update_script.py` (GPL-Kopf, dann):

```python
"""Verhaltenstests fuer scripts/update.sh.

Dasselbe Verfahren wie in `test_install_script.py`: das Skript laeuft
gegen einen versiegelten PATH aus gefaelschten Binaries, und geprueft
wird, WELCHE Befehle es waehlt - nicht, was sie bewirken. Ein echtes
`docker compose pull` waere weder in der CI noch auf einem
Entwicklungsrechner zumutbar.

Der wichtigste Test unten ist `test_ohne_build_wird_nie_gebaut`: das
Skript baute vor 0.2.0 immer, und der ganze Sinn dieser Aenderung ist,
dass ein Update auf einem Pi keine fuenf bis zehn Minuten mehr dauert.
Ein zurueckgerutschtes `compose build` faellt sonst niemandem auf - es
funktioniert ja, nur langsam."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update.sh"

SYSTEM_TOOLS = ("bash", "sh", "cat", "grep", "sed", "awk", "tr", "printf", "mkdir", "rm", "sleep", "date", "ls", "xargs", "tail", "seq", "hostname")


@pytest.fixture
def sealed(tmp_path):
    """Ein PATH aus zwei Verzeichnissen: gefaelschte Werkzeuge und die
    echten, die das Skript legitim braucht. Jeder Stub protokolliert seinen
    Aufruf nach $STUB_LOG und endet erfolgreich - `curl` gibt zusaetzlich
    eine Gesundheitsantwort aus, damit die Warteschleife sofort
    weiterlaeuft statt 120 Sekunden zu warten."""
    bindir = tmp_path / "bin"
    sysdir = tmp_path / "sys"
    bindir.mkdir()
    sysdir.mkdir()
    log = tmp_path / "stub.log"

    def stub(name: str, body: str = "") -> None:
        path = bindir / name
        path.write_text(f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8")
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
            env={**os.environ, "PATH": f"{bindir}:{sysdir}", "STUB_LOG": str(log), "HOME": str(tmp_path)},
        )
        return result, log.read_text(encoding="utf-8") if log.exists() else ""

    return run


def test_es_zieht_das_image_statt_es_zu_bauen(sealed):
    _, calls = sealed("--no-pull")
    assert "compose pull loxmatter" in calls


def test_ohne_build_wird_nie_gebaut(sealed):
    _, calls = sealed("--no-pull")
    assert "compose build" not in calls


def test_mit_build_wird_gebaut_und_nicht_gezogen(sealed):
    _, calls = sealed("--no-pull", "--build")
    assert "compose build loxmatter" in calls
    assert "compose pull loxmatter" not in calls


def test_das_ziehen_kommt_vor_dem_neustart(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("compose pull") < calls.index("compose up")


def test_der_neustart_laesst_die_nachbardienste_in_ruhe(sealed):
    # --no-deps: OTBRs Thread-Zustand haengt an einem Volume, und ein
    # Neustart des Thread-Netzes gehoert nicht zu einem Update.
    _, calls = sealed("--no-pull")
    up_line = next(line for line in calls.splitlines() if "compose up" in line)
    assert "--no-deps" in up_line


def test_die_datenbank_wird_vor_allem_anderen_gesichert(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("volume inspect") < calls.index("compose pull")


def test_no_cache_ohne_build_wird_abgewiesen(sealed):
    result, _ = sealed("--no-pull", "--no-cache")
    assert result.returncode != 0
    assert "--build" in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_script.py -v`
Expected: FAIL — `test_es_zieht_das_image_statt_es_zu_bauen`, `test_ohne_build_wird_nie_gebaut`, `test_mit_build_...`, `test_das_ziehen_...`, `test_no_cache_...` scheitern; die beiden über `--no-deps` und die Sicherung sollten bereits **passen** (das Skript tut das heute schon)

- [ ] **Step 3: Rewrite the argument parsing**

In `scripts/update.sh` den `for arg in "$@"`-Block ersetzen durch:

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
    *)          printf 'Unbekanntes Argument: %s (erlaubt: --no-pull, --build, --no-cache, --help)\n' "$arg" >&2; exit 2 ;;
  esac
done
# --no-cache steuert einen Bau. Ohne --build steuert es gar nichts, und
# ein Schalter, der stillschweigend wirkungslos bleibt, ist schlimmer als
# einer, der fehlt: er laesst jemanden glauben, er habe frisch gebaut.
if [ -n "$NO_CACHE" ] && [ "$BUILD" -eq 0 ]; then
  printf 'Abbruch: --no-cache wirkt nur zusammen mit --build.\n' >&2
  exit 2
fi
```

Und den Kopfkommentar (Zeilen 19-33) auf die neuen Schalter umschreiben:

```bash
# Bringt die laufende Bruecke auf den Stand der veroeffentlichten Version.
#
#   ./scripts/update.sh              # holen, Image ziehen, neu starten
#   ./scripts/update.sh --no-pull    # nur ziehen und neu starten
#   ./scripts/update.sh --build      # aus der Quelle bauen statt ziehen
#   ./scripts/update.sh --build --no-cache   # ohne Layer-Cache bauen
#
# Auf dem Rechner auszufuehren, auf dem die Bruecke laeuft. Der Stack liegt
# im Repository selbst (deploy/testhost/), das Skript findet ihn ueber
# seinen eigenen Pfad - kein Konfigurationsschritt.
#
# Seit 0.2.0 wird gezogen statt gebaut: der Bau brauchte auf dem Test-Pi
# fuenf bis zehn Minuten und konnte an einem PyPI-Ausfall oder am
# Speicher scheitern. --build stellt den alten Weg wieder her, fuer
# Entwicklung und fuer Hosts ohne Zugang zur Registry.
#
# Der Dienst wird mit `--no-deps` gestartet: matter-server und OTBR bleiben
# unangetastet. Ohne das erzeugt Compose sie mit neu, sobald sich die
# Projektkonfiguration geaendert hat - und OTBRs Thread-Zustand haengt an
# einem Volume, das ein Neubau zwar ueberlebt, aber ein Neustart des
# Thread-Netzes ohne Grund gehoert nicht zu einem Update.
```

- [ ] **Step 4: Replace the build step**

Den Block `say "Baue das Image"` samt seinem Kommentar ersetzen durch:

```bash
# Ziehen statt bauen (0.2.0). Der lange Kommentar von 2026-09-03 darueber,
# dass `docker compose build` und nicht ein eigenes `docker build` zu
# benutzen ist, gilt unveraendert weiter - er betrifft jetzt nur noch den
# --build-Zweig unten. Die Ursache von damals bleibt dieselbe: der Dienst
# traegt in der Compose-Datei einen `build:`-Block und baut sein eigenes
# Image; ein daneben gebautes `loxmatter:local` benutzt niemand.
if [ "$BUILD" -eq 1 ]; then
  say "Baue das Image"
  (cd "$STACK" && docker compose build ${NO_CACHE:+--no-cache} "$SERVICE") \
    || die "Build fehlgeschlagen - der laufende Dienst bleibt unveraendert."
else
  say "Hole das Image"
  (cd "$STACK" && docker compose pull "$SERVICE") \
    || die "Kein Image geladen - der laufende Dienst bleibt unveraendert. Ohne Zugang zur Registry hilft --build."
fi
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_script.py -v`
Expected: 7 passed

- [ ] **Step 6: Prove the central test can fail**

Ändere probeweise den `else`-Zweig auf `docker compose build "$SERVICE"`, führe die Tests aus.
Expected: `test_ohne_build_wird_nie_gebaut` und `test_es_zieht_das_image_statt_es_zu_bauen` FAILEN. Danach zurücknehmen: 7 passed.

- [ ] **Step 7: Add the missing README section**

Im `README.md` nach dem Installationsabschnitt:

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
Expected: alles grün

- [ ] **Step 9: Run it for real on the test host**

```bash
./scripts/update.sh
```
Expected: „Hole das Image", danach ein gesunder Dienst und die Ausgabe des Gerätestands. Im Browser zeigt der System-Tab jetzt `Läuft: 0.2.0`.

- [ ] **Step 10: Commit**

```bash
git add scripts/update.sh README.md tests/test_update_script.py
git commit -m "feat(update): das Image ziehen statt es auf dem Pi zu bauen

Der Bau brauchte auf dem Test-Pi fuenf bis zehn Minuten und konnte an
einem PyPI-Ausfall oder am Speicher scheitern. Fuer die Konsole war das
ein langer Balken; fuer den Knopf in der Oberflaeche (Stufe 2) waere es
eine Zumutung gewesen.

--build stellt den alten Weg wieder her, fuer Entwicklung und fuer Hosts
ohne Zugang zur Registry. --no-cache ohne --build wird jetzt abgewiesen
statt stillschweigend wirkungslos zu bleiben: ein Schalter, der nichts
tut, laesst jemanden glauben, er habe frisch gebaut.

Die Tests laufen wie die von install.sh gegen einen versiegelten PATH und
pruefen, WELCHE Befehle das Skript waehlt. Der wichtigste haelt fest, dass
ohne --build nie gebaut wird - ein zurueckgerutschtes compose build faellt
sonst niemandem auf, es funktioniert ja, nur langsam.

Das README verlor bisher kein Wort ueber das Aktualisieren.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Abschluss von Stufe 1

Nach Task 9 gilt:

- Die Oberfläche sagt, welche Version läuft.
- Es gibt eine veröffentlichte Version `0.2.0`, ein `:stable`-Image für `arm64` und `amd64`, und einen Changelog.
- Ein Update über die Konsole dauert rund eine Minute statt zehn.

Das trägt für sich. **Stufe 2** — Beiwagen, `/api/update/*`, die Update-Karte mit ihren vier Zuständen und dem selbsttätigen Rückfall — hat einen eigenen Plan: `docs/superpowers/plans/2026-09-08-webui-updates-stufe2.md`.
