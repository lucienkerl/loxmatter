# Phase 5: WebUI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Gerät lässt sich über den Browser einlernen, ansehen, bedienen und exportieren — und wenn etwas nicht funktioniert, zeigt die Oberfläche, an welcher Seite es liegt.

**Architecture:** Der FastAPI-Dienst aus Phase 4 bekommt ein zweites Gesicht. Unter `/cmd` und `/resync` spricht er weiter mit dem Miniserver; unter `/api` mit der Oberfläche. Die Fachlogik wird nicht verdoppelt: Bedienung geht über dasselbe `commands/`, das der Loxone-Endpunkt benutzt, und die Live-Werte kommen aus derselben Matter-Subscription, die den UDP-Sender speist. Die Oberfläche selbst ist statisches HTML mit mitgeliefertem Alpine.js, das FastAPI direkt ausliefert.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, `mypy` (strict), FastAPI, `httpx2` für Tests, Alpine.js (mitgeliefert, kein Build-Schritt).

## Global Constraints

- **Tests laufen ohne Hardware und ohne Netzwerkzugriff.** Ein UDP-Socket auf `127.0.0.1` und FastAPIs In-Process-Testclient gelten nicht als Netzwerkzugriff (Spec 10.1).
- **Deutsch in Prosa, Kommentaren, Docstrings, Hilfetexten, Fehlermeldungen und in der Oberfläche**, Englisch in Bezeichnern — **auch in Tests, auch in JavaScript, auch in JSON-Feldnamen**. Diese Regel wurde in Phase 4 sechsmal verletzt und sechsmal korrigiert; sie gilt für alles, was ins Repository kommt.
- **Alle Datenklassen unveränderlich** (`frozen=True`), solange kein Grund dagegen spricht.
- **Schlüssel sind unveränderlich** (Spec 6.2). Diese Phase zeigt sie an und ändert sie nie. Der Titel ist frei änderbar, der Schlüssel nicht — die Oberfläche muss das sichtbar machen.
- **Keine zweite Umrechnung.** Bedienung geht durch `commands.translate`, Werte durch `loxone.values`. Eine Kopie in der API driftet (Spec 4.2).
- **Kein Build-Schritt im Frontend.** Alpine.js wird als Datei mitgeliefert, nicht vom CDN geladen: die Bridge läuft in Installationen ohne Internet.
- `uv run ruff check .`, `uv run ruff format --check .` und `uv run mypy` müssen sauber bleiben. ruff formatiert auch Python-Blöcke in Markdown.
- Die unsanierten Vorlagen unter `tests/fixtures/VirtualIn/` und `tests/fixtures/VirtualOut/` enthalten Zugangsdaten einer echten Installation und sind git-ignoriert. **Nicht lesen.**

---

## Was diese Phase ausdrücklich nicht baut

Spec 8.2 zieht die Grenze, und sie ist wichtig genug, sie hier zu wiederholen: **Inbetriebnahme- und Diagnosewerkzeug, keine Smart-Home-Oberfläche.** Keine Szenen, keine Zeitpläne, keine Automatisierung, keine Favoritenseiten, keine Räume, keine Nutzerverwaltung, keine App. Das alles ist Loxones Aufgabe, und eine halbgare zweite Bedienoberfläche daneben wäre schlechter als keine.

Wenn beim Bauen der Wunsch aufkommt, „nur noch schnell" eine Gruppierung oder eine Szene einzubauen: nicht tun. Es steht als Nicht-Ziel in der Spec.

## Warum die Bedienung kein Komfortmerkmal ist

Spec 8.1: Ansicht 1 ist das Diagnosewerkzeug des Projekts. Schaltet eine Lampe über Loxone nicht, trennt ein Klick in der Oberfläche die beiden möglichen Ursachen — reagiert das Gerät hier, liegt der Fehler in der Loxone-Verdrahtung oder im Export; reagiert es nicht, in Matter, Thread oder am Gerät.

Für ein Werkzeug, das in fremden Installationen läuft, ist das der Unterschied zwischen einem beantwortbaren und einem unbeantwortbaren Fehlerbericht. Jede Entscheidung in dieser Phase, die zwischen „hübscher" und „sagt genauer, wo der Fehler sitzt" wählen muss, wählt das Zweite.

## Sicherheit: was diese Phase erreichbar macht

Bisher war der HTTP-Dienst ein Endpunkt für den Miniserver. Ab dieser Phase ist er eine Bedienoberfläche, die Geräte einlernt, entfernt und schaltet — **ohne jede Authentifizierung, gebunden an alle Schnittstellen.** Das war in Phase 4 als bewusst hingenommener Punkt vermerkt, weil nur `/cmd` und `/resync` erreichbar waren.

Mit dem Einlernen ändert sich das Gewicht: wer den Port erreicht, kann Geräte aus der Fabric werfen. Task 8 dieser Phase behandelt das ausdrücklich; bis dahin gilt der Dienst als nicht exponierbar, und das gehört in die Bedienungsanleitung, nicht in eine stille Annahme.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `src/loxmatter/matter/client.py` | zusätzlich `commission_with_code`, `remove_node`, `set_thread_dataset` |
| `src/loxmatter/api/__init__.py` | — |
| `src/loxmatter/api/models.py` | Antwortmodelle der REST-API, getrennt von den Speichermodellen |
| `src/loxmatter/api/devices.py` | Geräte und Signale: lesen, umbenennen, exportieren-Flag, entfernen |
| `src/loxmatter/api/control.py` | Bedienung und rohes Attributschreiben |
| `src/loxmatter/api/export.py` | Vorschau und Download der Vorlagen |
| `src/loxmatter/api/diagnostics.py` | UDP-Mitschnitt, Kommando-Log, Systemcheck |
| `src/loxmatter/api/live.py` | WebSocket für Live-Werte |
| `src/loxmatter/loxone/server.py` | bindet die API-Router ein, liefert die Oberfläche aus |
| `src/loxmatter/web/index.html` | die vier Ansichten |
| `src/loxmatter/web/app.js` | Zustand und Aufrufe |
| `src/loxmatter/web/vendor/alpine.min.js` | mitgeliefert, kein CDN |

---

### Task 1: Einlernen und Entfernen

Bisher konnte das Projekt Geräte nur lesen. Eingelernt wurden sie in Phase 1 mit einem
Wegwerf-Skript — das ist die letzte Lücke zwischen „liest ein Gerät" und „betreibt eine
Bridge".

**Files:**
- Modify: `src/loxmatter/matter/client.py`
- Create: `tests/matter/test_client_commissioning.py`

**Interfaces:**
- Consumes: der vorhandene `session_factory`/`http_session_factory`-Seam
- Produces:
  - `async def commission_with_code(self, code: str) -> NodeSnapshot`
  - `async def remove_node(self, node_id: int) -> None`
  - `async def set_thread_dataset(self, dataset: str) -> None`
  - `CommissioningError(RuntimeError)` — deutscher Text

- [ ] **Step 1: Die Upstream-Signaturen nachsehen, nicht annehmen**

In Phase 4 waren zwei Annahmen dieses Plans über `python-matter-server` falsch, und beide
hätten stillen Ausfall erzeugt. Sieh nach, bevor du schreibst:

```bash
uv run python -c "
import inspect
from matter_server.client.client import MatterClient
for name in ('commission_with_code', 'remove_node', 'set_thread_operational_dataset'):
    print(name, inspect.signature(getattr(MatterClient, name)))
"
```

Trage die tatsächlichen Signaturen und Rückgabetypen in den Docstring von `client.py`
ein. Weicht etwas ab, ist die Bibliothek maßgeblich — melde es, statt es passend zu machen.

- [ ] **Step 2: Write the failing test**

`tests/matter/test_client_commissioning.py`:

```python
import pytest

from loxmatter.matter.client import BridgeMatterClient, CommissioningError


class FakeNode:
    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.available = True
        self.node_data = type("Data", (), {"attributes": attributes})()


class FakeUpstream:
    def __init__(self) -> None:
        self.nodes: list[FakeNode] = []
        self.removed: list[int] = []
        self.datasets: list[str] = []
        self.fail_with: Exception | None = None

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def start_listening(self, ready=None) -> None:
        if ready is not None:
            ready.set()

    def get_nodes(self) -> list[FakeNode]:
        return self.nodes

    async def commission_with_code(self, code: str, network_only: bool = False):
        if self.fail_with is not None:
            raise self.fail_with
        node = FakeNode(7, {"0/40/1": "IKEA of Sweden", "1/6/0": True})
        self.nodes.append(node)
        return node

    async def remove_node(self, node_id: int) -> None:
        self.removed.append(node_id)

    async def set_thread_operational_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)


@pytest.fixture
def client() -> tuple[BridgeMatterClient, FakeUpstream]:
    upstream = FakeUpstream()
    return (
        BridgeMatterClient(
            "ws://test/ws",
            session_factory=lambda _session: upstream,
            http_session_factory=lambda: type("S", (), {"close": lambda self: None})(),
        ),
        upstream,
    )


async def test_commissioning_returns_a_snapshot(client):
    bridge, _ = client
    await bridge.connect()
    snapshot = await bridge.commission_with_code("MT:ABC123")
    assert snapshot.node_id == 7
    assert snapshot.vendor_name == "IKEA of Sweden"
    await bridge.disconnect()


async def test_commissioning_without_connection_raises(client):
    bridge, _ = client
    with pytest.raises(Exception, match="nicht verbunden"):
        await bridge.commission_with_code("MT:ABC123")


async def test_a_failed_commissioning_says_so_in_german(client):
    bridge, upstream = client
    upstream.fail_with = RuntimeError("device not found")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_remove_node_reaches_upstream(client):
    bridge, upstream = client
    await bridge.connect()
    await bridge.remove_node(7)
    assert upstream.removed == [7]
    await bridge.disconnect()


async def test_thread_dataset_reaches_upstream(client):
    """Ohne Datensatz kann matter-server einem Thread-Geraet kein Netz nennen."""
    bridge, upstream = client
    await bridge.connect()
    await bridge.set_thread_dataset("0e08...")
    assert upstream.datasets == ["0e08..."]
    await bridge.disconnect()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_client_commissioning.py -v`
Expected: FAIL mit `ImportError: cannot import name 'CommissioningError'`

- [ ] **Step 4: Write minimal implementation**

In `src/loxmatter/matter/client.py` ergänzen:

```python
class CommissioningError(RuntimeError):
    """Das Einlernen eines Geraets ist am Geraet selbst gescheitert (z. B.
    falscher Code, Geraet haengt schon in einem anderen Oekosystem, Timeout
    beim Interview).

    Ein Verbindungsverlust zu matter-server WAEHREND des Einlernens ist
    davon ausdruecklich abgegrenzt: commission_with_code() faengt
    `NotConnected`/`ConnectionClosed`/`CannotConnect` gesondert ab und wirft
    dafuer `MatterUnavailableError`, denn nur so laesst sich unterscheiden,
    ob das Geraet abgelehnt hat oder matter-server nicht erreichbar war
    (Spec 8.1/9). Die urspruengliche Ausnahme bleibt ueber `__cause__`
    erhalten."""


async def commission_with_code(self, code: str) -> NodeSnapshot:
    """Lernt ein Geraet ueber seinen Pairing-Code ein.

    Der Code ist die 11-stellige Zahl oder der 21-stellige MT:-Code vom
    Geraet oder seiner Verpackung. Haengt das Geraet schon in einem anderen
    Oekosystem, funktioniert der aufgedruckte Code nicht mehr - dann braucht
    es von dort einen Multi-Admin-Code (Spec 7.1).
    """
    upstream = self._require_upstream()

    # Lazy importiert wie _default_session_factory: Tests mit einem
    # Fake-Upstream sollen matter_server nie laden müssen.
    from matter_server.client.exceptions import CannotConnect, ConnectionClosed, NotConnected

    try:
        node = await upstream.commission_with_code(code)
    except (NotConnected, ConnectionClosed, CannotConnect) as exc:
        # Verbindungsverlust zu matter-server ist keine Ablehnung durch das
        # Geraet — muss VOR dem generischen except Exception unten stehen,
        # sonst würde er dort mitgefangen und als CommissioningError
        # gemeldet (Spec 8.1/9 verlangt die Unterscheidung).
        msg = f"matter-server nicht erreichbar: {exc}"
        raise MatterUnavailableError(msg) from exc
    except Exception as exc:
        raise CommissioningError(f"Einlernen fehlgeschlagen: {exc}") from exc
    return NodeSnapshot.from_raw(node.node_id, {"attributes": node.node_data.attributes})


async def remove_node(self, node_id: int) -> None:
    """Entfernt ein Geraet aus der Fabric."""
    await self._require_upstream().remove_node(node_id)


async def set_thread_dataset(self, dataset: str) -> None:
    """Uebergibt matter-server die Thread-Zugangsdaten.

    Ohne diesen Schritt scheitert das Einlernen eines Thread-Geraets mit
    "Required network information not provided" - der Controller findet das
    Geraet per BLE, kann ihm aber kein Netz nennen.
    """
    await self._require_upstream().set_thread_operational_dataset(dataset)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_client_commissioning.py -v`
Expected: PASS, 8 Tests

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/matter/client.py tests/matter/test_client_commissioning.py
git commit -m "feat(matter): Geraete einlernen und entfernen"
```

---

### Task 2: Geräte- und Signal-API

**Files:**
- Create: `src/loxmatter/api/__init__.py`
- Create: `src/loxmatter/api/models.py`
- Create: `src/loxmatter/api/devices.py`
- Create: `tests/api/test_devices.py`
- Modify: `src/loxmatter/model/store.py` (Abfragen, die die API braucht; **neue Spalte
  `signal.exported` — siehe "Schema-Migration" unten, NICHT einfach an `_SCHEMA`
  anhängen)
- Create: `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: `Store`, `StoredSignal`, `BridgeMatterClient`, `Runtime`
- Produces:
  - `DeviceOut`, `SignalOut` — frozen Pydantic-Modelle
  - `build_device_router(store, client, runtime) -> APIRouter` mit Präfix `/api`

**Schema-Migration (Review-Fix Important #1, 2026-09-02 — hier ergänzt, weil eine
frühere Fassung dieses Plans eine neue Spalte lehrte, ohne eine Migration dafür
vorzusehen):**

`_SCHEMA` verwendet `CREATE TABLE IF NOT EXISTS` — das erreicht eine bereits
bestehende Tabelle nie mit einer neuen Spalte. Die Spalte `signal.exported` diesem
String einfach hinzuzufügen reicht deshalb NICHT: gegen eine Datenbank, die vor
diesem Task angelegt wurde (`loxmatter export`/`loxmatter run` aus Phase 4 oder
früheren Läufen dieser Phase), bleibt sie unsichtbar, und `Store.signals()`
scheitert mit `IndexError: No item with that key`. Weil die Datenbank die
Signalschlüssel trägt — die Verdrahtung in Loxone, siehe Modul-Docstring von
`store.py` — ist die einzige Abhilfe ohne Migration das Löschen der gesamten
Datenbank, was jeden Schlüssel und jede bestehende Verdrahtung im Haus zerstört.

Die Migration verwaltet `PRAGMA user_version` als Schema-Version:

- Version 0 ist "vor dieser Migrationslogik" — jede Datenbank, bei der
  `user_version` noch nie gesetzt wurde, sowohl eine echte Alt-Datenbank als auch
  (bevor der erste `Store(...)`-Aufruf sie stempelt) eine frisch angelegte.
- Version 1 fügt `signal.exported` hinzu (`ALTER TABLE ... ADD COLUMN`) und
  befüllt bestehende Zeilen zurückwirkend — **nicht** pauschal mit dem
  Spalten-Default, sondern nach derselben Regel wie ein frisch registriertes
  Signal: exportierbar (ANALOG/DIGITAL) → `True`, sonst (TEXT, NONE) → `False`
  (siehe `is_exportable` unten).
- Läuft in einer Transaktion: `ALTER TABLE ADD COLUMN` ist in SQLite vollständig
  transaktional, ein `db.rollback()` im Fehlerfall macht auch schon ausgeführte
  Schritte dieses Laufs wieder rückgängig, `PRAGMA user_version` wird nur bei
  vollständigem Erfolg erhöht.
- Auf dem neuesten Stand: kein Schreibzugriff, echtes No-op — jeder Start außer
  dem allerersten nach einer Schema-Änderung.

Tests dafür (`tests/model/test_store_migration.py`) bauen die Alt-Datenbank direkt
per `sqlite3` mit dem Schema-Stand VOR `exported` auf (nicht über `Store`, die legt
die Spalte ja längst an), fügen ein Gerät und mehrere Signale mit unterschiedlicher
`exportability` ein und öffnen sie dann mit dem aktuellen `Store`: gelesen wird
korrekt, der Backfill stimmt, die Version steht danach auf 1, und ein erneutes
Öffnen ist ein No-op (ein zwischenzeitlich vom Nutzer gesetztes `exported` bleibt
erhalten statt vom Backfill überschrieben zu werden).

**Achtung, Signaturänderung:** `build_app` aus Phase 4 nimmt heute
`(store, invoke, runtime)`. Für das Einlernen braucht es zusätzlich den Matter-Client:

```python
def build_app(
    store: Store,
    invoke: Invoker,
    runtime: Runtime,
    client: BridgeMatterClient | None = None,
) -> FastAPI:
```

`client=None` bedeutet: die Einlern-Routen antworten mit 503 und einer Meldung, die
sagt warum. So bleiben die bestehenden Tests aus Phase 4 gültig, die `build_app` mit
drei Argumenten aufrufen — prüfe das, statt es anzunehmen.

Routen:

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/devices` | Liste mit Online-Status und Signalzahl |
| GET | `/api/devices/{device_id}` | ein Gerät mit den wichtigsten Live-Werten |
| GET | `/api/devices/{device_id}/signals` | vollständiger Baum |
| PATCH | `/api/devices/{device_id}` | Gerät umbenennen |
| PATCH | `/api/signals/{key}` | Titel ändern, Export-Flag setzen |
| POST | `/api/devices/commission` | Pairing-Code einlernen |
| DELETE | `/api/devices/{device_id}` | Gerät entfernen |

- [ ] **Step 1: Write the failing test**

`tests/api/test_devices.py`:

```python
import json
from pathlib import Path

import httpx2 as httpx
import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
async def api(tmp_path):
    from loxmatter.loxone.server import build_app

    store = Store(tmp_path / "t.sqlite")
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, _no_invoke, _fake_runtime(store), client=_FakeClient())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, store, device_id
    store.close()


async def test_device_list_carries_name_and_signal_count(api):
    client, _, device_id = api
    response = await client.get("/api/devices")
    assert response.status_code == 200
    devices = response.json()
    assert len(devices) == 1
    assert devices[0]["id"] == device_id
    assert "GRILLPLATS" in devices[0]["label"]
    assert devices[0]["signal_count"] == 159


async def test_signal_tree_marks_what_cannot_be_exported(api):
    """Spec 6.6: nicht abbildbare Werte werden angezeigt, aber nicht exportierbar."""
    client, _, device_id = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    assert len(signals) == 159
    assert sum(1 for s in signals if s["exportable"]) == 109
    unexportable = next(s for s in signals if not s["exportable"])
    assert unexportable["reason"]


async def test_signal_carries_its_immutable_key_and_editable_title(api):
    client, _, device_id = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    signal = signals[0]
    assert signal["key"].startswith(f"d{device_id}_")
    assert "title" in signal


async def test_renaming_a_signal_leaves_its_key_alone(api):
    """Spec 6.2: der Schluessel ist die Verdrahtung in Loxone."""
    client, store, device_id = api
    before = {s.ref: s.key for s in store.signals(device_id)}
    key = next(iter(before.values()))
    response = await client.patch(f"/api/signals/{key}", json={"title": "Kaffeemaschine"})
    assert response.status_code == 200
    assert {s.ref: s.key for s in store.signals(device_id)} == before
    assert any(s.title == "Kaffeemaschine" for s in store.signals(device_id))


async def test_the_key_cannot_be_changed_through_the_api(api):
    client, store, device_id = api
    key = store.signals(device_id)[0].key
    response = await client.patch(f"/api/signals/{key}", json={"key": "d99_9_boese"})
    assert response.status_code in (200, 422)
    assert any(s.key == key for s in store.signals(device_id))


async def test_unknown_signal_yields_404(api):
    client, _, _ = api
    assert (
        await client.patch("/api/signals/d1_1_gibtsnicht", json={"title": "x"})
    ).status_code == 404


async def test_unknown_device_yields_404(api):
    client, _, _ = api
    assert (await client.get("/api/devices/999/signals")).status_code == 404
```

Die Hilfsfunktionen `_no_invoke`, `_fake_runtime` und `_FakeClient` gehören in eine
`tests/api/conftest.py` — sie werden von jeder Task dieser Phase gebraucht.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_devices.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.api'`

- [ ] **Step 3: Antwortmodelle**

`src/loxmatter/api/models.py`:

```python
"""Antwortmodelle der REST-API.

Bewusst getrennt von den Speichermodellen in `model.store`: was die
Oberflaeche sieht, ist eine Sicht auf den Zustand, keine Abbildung der
Tabellen. Aendert sich das Schema, aendert sich nicht zwangslaeufig die API.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SignalOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    path: str
    kind: str
    title: str
    unit: str
    value: float | bool | str | None
    exportable: bool
    reason: str | None
    exported: bool


class DeviceOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    node_id: int
    label: str
    online: bool
    signal_count: int
    exportable_count: int
```

- [ ] **Step 4: Router schreiben**

`src/loxmatter/api/devices.py` baut den Router. Die Kernpunkte:

```python
@router.patch("/signals/{key}")
async def rename_signal(key: str, patch: SignalPatch) -> SignalOut:
    """Aendert Titel und Export-Flag. Der Schluessel bleibt unberuehrt.

    Spec 6.2: der Schluessel ist die Verdrahtung in Loxone. Waere er hier
    aenderbar, koennte ein Klick in der Oberflaeche einen Baustein im Haus
    still totlegen. Das Modell `SignalPatch` kennt deshalb gar kein Feld
    dafuer - ein mitgeschicktes `key` wird verworfen, nicht angewendet.
    """
```

`SignalPatch` trägt ausschließlich `title: str | None` und `exported: bool | None`.

`rename_signal` muss — wie jede geräte-gebundene Route dieses Routers — erst
prüfen, ob das Gerät hinter `signal_by_key(key).device_id` noch aktiv ist, bevor
es etwas ändert (Review-Fix Important #4, 2026-09-02): sonst bleibt die Zeile
eines per `DELETE /api/devices/{id}` entfernten Geräts über ihren Schlüssel
weiterhin lesbar und mutierbar, obwohl `GET /api/devices/{id}` für dasselbe Gerät
längst 404 meldet. 404 mit einer deutschen Meldung, die sagt, dass das Gerät
entfernt wurde — nicht die generische "unbekanntes Geräte-ID"-Meldung von
`_require_device`, die zwischen "nie existiert" und "entfernt" nicht
unterscheidet.

`register_signals` in `store.py` setzt das Default von `exported` beim ersten
Registrieren eines Signals auf `is_exportable(profile.exportability)` — dieselbe
Funktion, die `_signal_out`/`_device_out` unten für `exportable`/
`exportable_count` aufrufen (`profiles.table.is_exportable`, exportierbar genau
für ANALOG/DIGITAL). Eine zweite, unabhängig hingeschriebene Fassung derselben
Regel (etwa `exportability is not Exportability.NONE`, was TEXT fälschlich
mit einschlösse) ist genau das, was Review-Fix Important #2 (2026-09-02) beheben
musste — beide Stellen dieses Tasks müssen dieselbe Funktion aufrufen, nicht
eigenständig dieselbe Idee nachbilden.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/ -v`
Expected: PASS, 22 Tests (7 aus diesem Plan-Entwurf plus 15, die im Zuge dieses
Tasks tatsächlich dazukamen: Einlernen, Entfernen, das Export-Flag, und der
Review-Fix zu einem Signal-Zugriff auf ein bereits entferntes Gerät)

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api tests/api
git commit -m "feat(api): Geraete und Signale lesen und benennen"
```

---

### Task 3: WebSocket für Live-Werte

Spec 8.3: dieselbe Subscription, die den UDP-Sender speist — kein zweiter Pfad, kein
Polling.

**Files:**
- Create: `src/loxmatter/api/live.py`
- Modify: `src/loxmatter/loxone/runtime.py` (Beobachter)
- Create: `tests/api/test_live.py`

**Interfaces:**
- Produces:
  - `Runtime.add_observer(callback)` / `remove_observer(callback)`
  - `build_live_router(runtime) -> APIRouter` mit `/api/live`

- [ ] **Step 1: Write the failing test**

`tests/api/test_live.py`:

```python
import asyncio

import pytest

from loxmatter.loxone.runtime import Runtime


class RecordingSender:
    async def send(self, key, value, *, force=False) -> bool:
        return True

    async def close(self) -> None: ...


async def test_observer_sees_every_value_the_sender_sees(tmp_path, plug_store):
    """Spec 8.3: ein Pfad, nicht zwei."""
    store, device_id = plug_store
    seen: list[tuple[str, object]] = []
    runtime = Runtime(store, RecordingSender())
    runtime.add_observer(lambda key, value: seen.append((key, value)))
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert seen == [(f"d{device_id}_2_voltage", pytest.approx(230.0))]


async def test_a_failing_observer_does_not_stop_the_udp_sender(tmp_path, plug_store):
    """Die Oberflaeche darf die Bruecke nicht mitreissen."""
    store, device_id = plug_store
    sent: list[str] = []

    class Sender:
        async def send(self, key, value, *, force=False) -> bool:
            sent.append(key)
            return True

        async def close(self) -> None: ...

    runtime = Runtime(store, Sender())

    def boom(key: str, value: object) -> None:
        raise RuntimeError("Beobachter kaputt")

    runtime.add_observer(boom)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sent == [f"d{device_id}_2_voltage"]


async def test_removed_observer_stops_receiving(tmp_path, plug_store):
    store, device_id = plug_store
    seen: list[str] = []
    runtime = Runtime(store, RecordingSender())
    observer = lambda key, value: seen.append(key)  # noqa: E731
    runtime.add_observer(observer)
    runtime.remove_observer(observer)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert seen == []


async def test_websocket_delivers_a_value(api_with_runtime):
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live") as ws:
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(ws.receive_json(), timeout=2)
    assert message["key"] == f"d{device_id}_2_voltage"
    assert message["value"] == pytest.approx(230.0)


async def test_a_disconnecting_client_is_dropped_without_noise(api_with_runtime):
    """Ein geschlossener Browser-Tab darf keinen Fehler ins Log schreiben."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live"):
        pass
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert runtime.observer_count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_live.py -v`
Expected: FAIL mit `AttributeError: 'Runtime' object has no attribute 'add_observer'`

- [ ] **Step 3: Beobachter in der Laufzeit**

In `Runtime` ergänzen. Zwei Regeln, die im Docstring stehen müssen:

- Der Beobachter wird **nach** dem Senden aufgerufen. Die Brücke zu Loxone ist der
  Zweck; die Oberfläche schaut zu.
- Ein Beobachter, der wirft, wird geloggt und übersprungen. Er darf den UDP-Pfad nicht
  mitreißen — dieselbe Regel, aus der in Phase 4 die Heartbeat-Schleife gehärtet wurde.

- [ ] **Step 4: WebSocket-Router**

Jede Verbindung meldet sich als Beobachter an und beim Trennen wieder ab. Ein
`WebSocketDisconnect` ist der Normalfall, kein Fehler — er darf nichts ins Log schreiben.

Die Warteschlange je Verbindung ist **begrenzt** (`QUEUE_MAXSIZE = 512`, Review-Fix
Important #1, 2026-09-02) — nicht unbegrenzt, wie eine frühere Fassung annahm. Diese
Brücke läuft wochenlang unbeaufsichtigt in jemandes Zuhause; ein Browser-Tab im
Hintergrund oder ein eingeschlafenes Laptop, das nicht mehr liest, ist dort Alltag, kein
Randfall. Die Grenze ist so gewählt, dass sie einen vollen Resend-Burst (`/resync`,
Spec 6.4 — schon ein einzelnes Gerät wie der Testsuite-Stecker kommt auf ~110
Datagramme) klaglos aufnimmt, mit deutlicher Luft nach oben. Bei Überlauf fällt der
**älteste** Eintrag, nicht der neueste — eine Live-Ansicht will den aktuellsten Stand.
Ein Debug-Log meldet sich beim Übergang ins Verwerfen (nicht bei jedem weiteren
Verwurf), damit eine hängende Verbindung im Betrieb auffindbar bleibt. Bewusst NICHT
umgesetzt: die Verbindung aktiv zu trennen, wenn sie dauerhaft voll bleibt — die
Begrenzung deckelt bereits die einzige Gefahr (unbegrenztes Wachstum) auf eine feste,
kleine Größe; eine zusätzliche Zeitschwelle bräuchte eine eigene, schwer zu
begründende Kalibrierung und würde riskieren, eine nur kurz gedrosselte Sitzung
rauszuwerfen, für einen Gewinn, der bei bereits gedeckeltem Speicher gering ist.

Um einen Client, der während des Trennens mitten im Versand steckt, robust zu behandeln
(Review-Fix Important #2, 2026-09-02): `_send_loop` fängt nicht nur `WebSocketDisconnect`
ab, sondern auch `RuntimeError` direkt an der Sendestelle — manche ASGI-Server werfen bei
einem Sendeversuch auf eine bereits verlorene Verbindung genau das statt
`WebSocketDisconnect`. Beides ist derselbe Fall (ein Browser-Tab, der weg ist, kein
Programmfehler) und landet deshalb auf `logger.debug`, nie auf `logger.error` — und die
Route meldet den Beobachter trotzdem im `finally` ab.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_live.py -v`
Expected: PASS, 9 Tests (5 aus der ursprünglichen Task 3, dazu 4 aus dem Review-Fix vom
2026-09-02: Warteschlangen-Überlauf verwirft den ältesten Eintrag und lässt UDP-Pfad wie
Beobachter-Registrierung unberührt, ein `RuntimeError` beim Versand wird wie eine
Trennung behandelt ohne Fehler-Log, und zwei gleichzeitige Verbindungen bleiben
voneinander isoliert)

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/live.py src/loxmatter/loxone/runtime.py tests/api/test_live.py
git commit -m "feat(api): WebSocket fuer Live-Werte aus derselben Subscription"
```

---

### Task 4: Bedienung und rohes Attributschreiben

Das Herz der Diagnosefähigkeit aus Spec 8.1.

**Files:**
- Create: `src/loxmatter/api/control.py`
- Create: `tests/api/test_control.py`

**Interfaces:**
- Consumes: `Store.resolve_command`, `commands.translate.to_matter_call`, der `invoke`-Callback aus Phase 4
- Produces: `build_control_router(store, invoke) -> APIRouter`

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/devices/{device_id}/controls` | welche Bedienelemente dieses Gerät hat |
| POST | `/api/commands/{key}` | ein Kommando ausführen, Wert im Rumpf |
| POST | `/api/signals/{key}/write` | ein Attribut roh setzen |

- [ ] **Step 1: Write the failing test**

`tests/api/test_control.py`:

```python
import pytest


async def test_plug_offers_exactly_its_three_commands(api):
    """Spec 6.7: Ausgangsbefehle stammen aus AcceptedCommandList, nicht aus Attributen."""
    client, _, device_id = api
    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()
    assert sorted(c["slug"] for c in controls) == ["off", "on", "toggle"]


async def test_button_offers_no_controls(api_button):
    """Ein Taster ist ein Eingabegeraet."""
    client, _, device_id = api_button
    assert (await client.get(f"/api/devices/{device_id}/controls")).json() == []


async def test_executing_a_command_reaches_matter(api):
    client, _, device_id = api
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_the_same_translation_as_the_loxone_endpoint(api, invocations):
    """Spec 4.2: eine Umrechnung, zwei Aufrufer - sonst driften sie."""
    client, _, device_id = api
    key = f"d{device_id}_1_on"
    await client.post(f"/api/commands/{key}", json={"value": "1"})
    await client.get(f"/cmd/{key}/1")
    assert len(invocations) == 2
    assert invocations[0] == invocations[1]


async def test_unknown_command_yields_404(api):
    client, _, _ = api
    response = await client.post("/api/commands/d1_1_gibtsnicht", json={"value": "1"})
    assert response.status_code == 404


async def test_a_device_that_does_not_answer_yields_502(api_failing_invoke):
    client, _, device_id = api_failing_invoke
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 502
    assert "Traceback" not in response.text


async def test_raw_write_of_a_non_writable_attribute_is_refused(api):
    """Lieber eine klare Absage als ein Schreibversuch, der still nichts tut."""
    client, store, device_id = api
    key = next(s.key for s in store.signals(device_id) if s.ref.cluster_id == 40)
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_control.py -v`
Expected: FAIL mit `404` auf `/api/devices/{id}/controls` — die Route existiert nicht

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/api/control.py`. Der Docstring hält fest, warum es dieses Modul gibt:

```python
"""Bedienung eines Geraets aus der Oberflaeche.

Das ist kein Komfortmerkmal (Spec 8.1). Schaltet eine Lampe ueber Loxone
nicht, trennt ein Klick hier die beiden moeglichen Ursachen: reagiert das
Geraet, liegt der Fehler in der Loxone-Verdrahtung oder im Export; reagiert
es nicht, in Matter, Thread oder am Geraet.

Die Uebersetzung kommt aus `commands.translate` - derselben, die der
Loxone-Endpunkt benutzt. Eine eigene Kopie hier wuerde driften, und dann
haette die Diagnose genau den Fehler, den sie finden soll (Spec 4.2).
"""
```

Die Statuscodes folgen dem Loxone-Endpunkt aus Phase 4: 404 unbekannter Schlüssel,
400 unpassender Wert, 502 Gerät antwortet nicht.

Für das rohe Schreiben: die Schreibbarkeit eines Attributs steht nicht im Snapshot.
**Prüfe, ob `python-matter-server` sie zugänglich macht**, und wenn nicht, lehne
Schreibversuche auf Attribute ab, die nicht in einer Erlaubnisliste stehen — dieselbe
Asymmetrie wie bei den Kommandos in Spec 6.7. Trage den Befund in die Spec ein.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_control.py -v`
Expected: PASS, 7 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/control.py tests/api/test_control.py
git commit -m "feat(api): Geraete aus der Oberflaeche bedienen"
```

**Review-Fix (2026-09-02), zwei Important- und zwei Minor-Befunde:**

1. **Important — `POST /api/commands/{key}` prüfte nie, ob das Gerät des Kommandos
   noch aktiv ist.** `Store.resolve_command` löst den Schlüssel allein über die
   `command`-Tabelle auf, und `forget_device` löscht dort keine Zeile — es setzt nur
   `device.active = 0`. Ein Kommando gegen ein bereits entferntes Gerät ließ sich
   dadurch weiterhin auslösen, während `GET /api/devices/{id}/controls` für dasselbe
   Gerät korrekt 404 meldete. Behoben nach demselben Muster wie `write_signal` (dort
   schon vorhanden) und `PATCH /api/signals/{key}` (`api/devices.py`, Review-Fix
   Important #4 aus Task 2): `StoredCommand` trägt jetzt `device_id`, und
   `execute_command` prüft `store.device(stored.device_id)`, bevor es übersetzt und
   auslöst — ein entferntes Gerät liefert 404 mit deutscher Meldung. Neuer Test:
   `test_command_at_a_removed_device_is_refused`. `GET /api/devices/{id}/controls`
   und `POST /api/signals/{key}/write` wurden dabei erneut geprüft — beide waren
   bereits abgesichert (`_require_device` bzw. die vorhandene Prüfung in
   `write_signal`), keine Änderung nötig.
2. **Important — Spec 8.4 und der Moduldocstring behaupteten fälschlich, eine
   Volltextsuche nach „writable“ habe keinen Treffer ergeben.** Tatsächlich trägt
   `chip/clusters/CHIPClusters.py` (Teil des installierten `chip`-Pakets) 250
   Vorkommen von `"writable": True`, darunter für `BasicInformation` exakt die drei
   Attribute, auf die die Erlaubnisliste unabhängig davon schon kam. Die Information
   existiert also — sie steht nur in einem Modul, das in dieser Distribution nicht
   importierbar ist (`ImportError: cannot import name 'exceptions' from 'chip'`, weil
   `home_assistant_chip_clusters` `CHIPClusters.py` ohne das dazugehörige
   `chip/exceptions.py` ausliefert) und das python-matter-server nirgends benutzt. Die
   praktische Konsequenz (Erlaubnisliste bleibt richtig) ändert sich dadurch nicht,
   aber die Begründung wurde in Spec 8.4 und im Moduldocstring korrigiert. Spec 12
   bekommt dazu einen neuen Punkt 7: die von Hand gepflegte Erlaubnisliste skaliert
   nicht über eine Handvoll Geräte hinaus und könnte ersetzt werden, sobald dieses
   Modul importierbar wird oder sich das Parsen als Daten als vertretbar erweist.
3. **Minor — die 400/501-Antworten von `POST /api/signals/{key}/write` verwiesen auf
   „den Moduldocstring von api/control.py“**, brauchbar in einem Log, aber nichtssagend
   für die Oberfläche. Beide Meldungen sagen jetzt selbst auf Deutsch, was los ist und
   was sich tun lässt, ohne auf eine Datei zu verweisen.
4. **Minor — `GET /api/devices/{id}/controls` zeigte gefilterte rohe Kommandos gar
   nicht an**, was korrekt ist (Spec 6.7), aber eine Person, die ein unbekanntes Gerät
   diagnostiziert, verlor dabei die Information, dass es sie überhaupt gibt. Die Route
   liefert jetzt `{"commands": [...], "hidden_raw_commands": N}` statt einer nackten
   Liste (neues Modell `ControlsOut`). Neuer Test:
   `test_hidden_raw_commands_are_counted`.

Zwei neue Tests (`test_command_at_a_removed_device_is_refused`,
`test_hidden_raw_commands_are_counted`) zu den ursprünglichen sieben — **Expected:
PASS, 9 Tests** in `tests/api/test_control.py`.

```bash
git add src tests docs
git commit -m "fix(api): Kommandos an entfernte Geraete abweisen"
```

---

### Task 5: Export über die API

**Files:**
- Create: `src/loxmatter/api/export.py`
- Create: `tests/api/test_export_api.py`

**Interfaces:**
- Consumes: `export.documents`, `export.signals`, `Store`
- Produces: `build_export_router(store) -> APIRouter`

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/export/preview` | was entstünde: Dateien, Objekte, Befehle, Übersprungenes |
| GET | `/api/export/download` | ZIP mit allen Vorlagen und der Kurzanleitung |
| GET | `/api/export/status` | pro Gerät: wann zuletzt exportiert, seither geändert |

- [ ] **Step 1: Write the failing test**

`tests/api/test_export_api.py`:

```python
import io
import zipfile


async def test_preview_reports_what_would_be_written(api):
    client, _, device_id = api
    preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
    device = next(d for d in preview["devices"] if d["device_id"] == device_id)
    assert device["inputs"] == 110
    assert device["commands"] == 3
    assert device["skipped"] == 50


async def test_preview_does_not_write_anything(api, tmp_path):
    """Vorschau heisst Vorschau."""
    client, _, _ = api
    before = set(tmp_path.iterdir())
    await client.get("/api/export/preview?bridge_ip=192.168.1.50")
    assert set(tmp_path.iterdir()) == before


async def test_download_returns_a_zip_with_both_templates(api):
    client, _, device_id = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert any(n.startswith(f"VIU_d{device_id}_") for n in names)
    assert any(n.startswith(f"VO_d{device_id}_") for n in names)


async def test_zip_contains_the_system_templates_and_a_readme(api):
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&system=true")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "VIU_Matter_System.xml" in names
    assert "VO_Matter_System.xml" in names
    assert any(n.lower().endswith(".md") or n.lower().endswith(".txt") for n in names)


async def test_files_in_the_zip_keep_bom_and_crlf(api):
    """Spec 6.1: das Format ist gemessen, nicht verhandelbar - auch im Archiv."""
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    name = next(n for n in archive.namelist() if n.startswith("VIU_"))
    raw = archive.read(name)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw.replace(b"\r\n", b"")


async def test_status_marks_a_device_as_never_exported(api):
    client, _, device_id = api
    status = (await client.get("/api/export/status")).json()
    entry = next(s for s in status if s["device_id"] == device_id)
    assert entry["exported_at"] is None


async def test_missing_bridge_ip_yields_422(api):
    client, _, _ = api
    assert (await client.get("/api/export/preview")).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: FAIL — die Routen existieren nicht

- [ ] **Step 3: Write minimal implementation**

Der Download baut das ZIP im Speicher. Die Kurzanleitung darin nennt die Zielordner
(`Templates\VirtualIn\` und `Templates\VirtualOut\`), den Importweg in Loxone Config,
und den Hinweis, dass die Systemvorlagen nur einmal gebraucht werden.

**Der Export über die API muss dieselbe Datenbank schreiben wie `loxmatter export`.**
Andernfalls vergibt die Oberfläche andere Schlüssel als die CLI, und ein Nutzer, der
beides benutzt, bekommt zwei Sätze Vorlagen für dasselbe Gerät. Ein Test dagegen gehört
dazu.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: PASS, 7 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/export.py tests/api/test_export_api.py
git commit -m "feat(api): Vorlagen als Vorschau und als ZIP"
```

---

### Task 6: Diagnose

Spec 10.5. Diese vier Dinge sind der Grund, warum ein Fehlerbericht aus einer fremden
Installation beantwortbar wird.

**Files:**
- Create: `src/loxmatter/api/diagnostics.py`
- Modify: `src/loxmatter/loxone/sender.py` (Mitschnitt)
- Modify: `src/loxmatter/loxone/server.py` (Kommando-Log)
- Create: `tests/api/test_diagnostics.py`

**Interfaces:**
- Produces:
  - `class RingBuffer` — feste Größe, älteste fallen heraus
  - `build_diagnostics_router(...) -> APIRouter`

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/diagnostics/datagrams` | die letzten N gesendeten, filterbar pro Gerät |
| GET | `/api/diagnostics/commands` | eingehende HTTP-Aufrufe mit Ergebnis |
| GET | `/api/diagnostics/system` | Systemcheck, jede Zeile grün oder rot |
| GET | `/api/diagnostics/fabric-backup` | Sicherung der Fabric-Credentials als Download |

**Die Sicherung ist kein Nebenpunkt.** Spec 4.1 nennt das Volume mit den
Fabric-Credentials den einzigen unersetzlichen Zustand des ganzen Systems: geht es
verloren, muss jedes Gerät neu eingelernt werden — und bei Thread-Geräten heißt das
zurücksetzen, aus dem alten Netz werfen, neu koppeln. Spec 8 führt die Sicherung
deshalb ausdrücklich in der Systemansicht auf.

Der Endpunkt liefert den Inhalt des matter-server-Datenverzeichnisses als Archiv.
**Diese Datei ist ein Schlüsselmaterial**, kein Protokoll: sie erlaubt es, die Fabric
zu übernehmen. Der Download gehört deshalb hinter das Token aus Task 8, und die
Oberfläche muss danebenschreiben, was da heruntergeladen wird — nicht nur einen
Knopf mit „Backup" zeigen.

- [ ] **Step 1: Write the failing test**

`tests/api/test_diagnostics.py`:

```python
import pytest

from loxmatter.api.diagnostics import RingBuffer


def test_ring_buffer_drops_the_oldest():
    buffer = RingBuffer(maxlen=3)
    for i in range(5):
        buffer.append(i)
    assert list(buffer) == [2, 3, 4]


def test_ring_buffer_of_a_long_running_bridge_stays_bounded():
    """Eine Bruecke laeuft monatelang - der Mitschnitt darf nicht mitwachsen."""
    buffer = RingBuffer(maxlen=100)
    for i in range(1_000_000):
        buffer.append(i)
    assert len(list(buffer)) == 100


async def test_datagram_log_shows_what_was_sent(api_with_sender):
    client, sender, device_id = api_with_sender
    await sender.send(f"d{device_id}_2_voltage", 230.0)
    entries = (await client.get("/api/diagnostics/datagrams")).json()
    assert entries[-1]["key"] == f"d{device_id}_2_voltage"
    assert entries[-1]["value"] == "230"
    assert entries[-1]["timestamp"]


async def test_datagram_log_filters_by_device(api_with_sender):
    client, sender, device_id = api_with_sender
    await sender.send(f"d{device_id}_2_voltage", 230.0)
    await sender.send("bridge_alive", True)
    entries = (await client.get(f"/api/diagnostics/datagrams?device_id={device_id}")).json()
    assert all(e["key"].startswith(f"d{device_id}_") for e in entries)


async def test_command_log_records_the_result(api):
    client, _, device_id = api
    await client.get(f"/cmd/d{device_id}_1_on/1")
    await client.get("/cmd/d1_1_gibtsnicht/1")
    entries = (await client.get("/api/diagnostics/commands")).json()
    assert entries[-2]["status"] == 200
    assert entries[-1]["status"] == 404


async def test_system_check_reports_each_line_with_a_verdict(api):
    client, _, _ = api
    checks = (await client.get("/api/diagnostics/system")).json()
    names = {c["name"] for c in checks}
    assert {"matter-server", "store", "ipv6"} <= names
    for check in checks:
        assert check["ok"] in (True, False)
        assert check["detail"]


async def test_fabric_backup_is_a_real_archive(api):
    """Spec 4.1: das einzige unersetzliche Datum des Systems."""
    client, _, _ = api
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 200
    assert response.headers["content-type"] in ("application/zip", "application/gzip")
    assert len(response.content) > 0


async def test_a_failing_check_says_what_to_do(api_without_matter):
    """Ein roter Punkt ohne Hinweis hilft niemandem."""
    client, _, _ = api_without_matter
    checks = (await client.get("/api/diagnostics/system")).json()
    failing = next(c for c in checks if not c["ok"])
    assert len(failing["detail"]) > 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_diagnostics.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.api.diagnostics'`

- [ ] **Step 3: Write minimal implementation**

```python
class RingBuffer[T]:
    """Haelt die letzten N Eintraege, aeltere fallen heraus.

    Eine Bruecke laeuft monatelang. Ein Mitschnitt, der mitwaechst, ist irgendwann
    das groesste Objekt im Prozess - und der interessante Teil sind ohnehin die
    letzten Minuten.
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._items: collections.deque[T] = collections.deque(maxlen=maxlen)

    def append(self, item: T) -> None:
        self._items.append(item)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)
```

Der Mitschnitt hängt sich in `UdpSender` ein, nicht daneben — sonst zeigt er, was
gesendet werden *sollte*, statt was gesendet *wurde*. Das ist der Unterschied, auf den
es bei einer Diagnose ankommt.

Der Systemcheck prüft mindestens: matter-server verbunden, Datenbank beschreibbar, IPv6
vorhanden, Miniserver erreichbar. **Jede rote Zeile trägt einen konkreten Hinweis**, was
zu tun ist — ein roter Punkt ohne Erklärung verschiebt das Rätsel nur.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_diagnostics.py -v`
Expected: PASS, 8 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/diagnostics.py src/loxmatter/loxone tests/api/test_diagnostics.py
git commit -m "feat(api): Mitschnitt, Kommando-Log und Systemcheck"
```

---

### Task 7: Die Oberfläche

**Files:**
- Create: `src/loxmatter/web/index.html`
- Create: `src/loxmatter/web/app.js`
- Create: `src/loxmatter/web/style.css`
- Create: `src/loxmatter/web/vendor/alpine.min.js`
- Modify: `src/loxmatter/loxone/server.py`
- Create: `tests/api/test_web.py`

**Interfaces:**
- Produces: die vier Ansichten aus Spec 8, ausgeliefert unter `/`

- [ ] **Step 1: Alpine.js mitliefern**

Lade `alpine.min.js` in der aktuellen 3.x-Fassung herunter und lege sie unter
`src/loxmatter/web/vendor/` ab. **Kein CDN-Verweis im HTML** — die Brücke läuft in
Installationen ohne Internet, und eine Oberfläche, die dort weiß bleibt, ist wertlos.

Notiere Version und Herkunft in einem Kommentar am Kopf von `index.html`.

- [ ] **Step 2: Write the failing test**

`tests/api/test_web.py`:

```python
async def test_root_serves_the_interface(api):
    client, _, _ = api
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_alpine_is_served_locally_not_from_a_cdn(api):
    """Die Bruecke laeuft in Installationen ohne Internet."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "cdn." not in page
    assert "unpkg" not in page
    assert (await client.get("/static/vendor/alpine.min.js")).status_code == 200


async def test_the_page_names_all_four_views(api):
    client, _, _ = api
    page = (await client.get("/")).text
    for view in ("Geräte", "Signale", "Export", "System"):
        assert view in page


async def test_the_page_does_not_promise_what_the_spec_excludes(api):
    """Spec 8.2: Inbetriebnahme- und Diagnosewerkzeug, keine Smart-Home-Oberflaeche."""
    client, _, _ = api
    page = (await client.get("/")).text.lower()
    for absent in ("szene", "zeitplan", "automatisierung", "favorit"):
        assert absent not in page


async def test_static_files_do_not_escape_their_directory(api):
    client, _, _ = api
    response = await client.get("/static/../../../etc/passwd")
    assert response.status_code in (404, 400)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: FAIL mit `404` auf `/`

- [ ] **Step 4: Die vier Ansichten bauen**

Die Auslieferung in `server.py`:

```python
_WEB_DIR = Path(__file__).parents[1] / "web"

app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Liefert die Oberflaeche aus. Kein Build-Schritt, keine CDN-Abhaengigkeit."""
    return FileResponse(_WEB_DIR / "index.html")
```

`index.html` trägt alle vier Ansichten, umgeschaltet über Alpine ohne Seitenwechsel.

**Ansicht Geräte.** Liste mit Online-Punkt, Name, Signalzahl. Pro Gerät die Bedienelemente
aus `/api/devices/{id}/controls` und die wichtigsten Live-Werte. Ein Eingabefeld für den
Pairing-Code mit einem Hinweis daneben: hängt das Gerät schon in Apple, Google oder einer
DIRIGERA, funktioniert der aufgedruckte Code nicht — dort einen Multi-Admin-Code erzeugen
(Spec 7.1). Das ist der häufigste Stolperstein und gehört in die Oberfläche, nicht in eine
Anleitung, die niemand liest.

**Ansicht Signale.** Der vollständige Baum pro Gerät mit Live-Wert. Der **Schlüssel wird
angezeigt, aber nicht editierbar** — mit einem kurzen Hinweis, warum: er ist die
Verdrahtung in Loxone. Nicht exportierbare Werte bekommen statt der Checkbox den Grund
angezeigt (Spec 6.6).

**Ansicht Export.** Miniserver-IP und Port eintragen, Vorschau ansehen, ZIP herunterladen.
Pro Gerät sichtbar, wann zuletzt exportiert wurde.

**Ansicht System.** Der Systemcheck als Liste grüner und roter Zeilen, darunter der
UDP-Mitschnitt und das Kommando-Log.

Die Live-Werte kommen über den WebSocket aus Task 3. Bricht er ab, zeigt die Oberfläche
das an und verbindet sich neu — eine Oberfläche, die eingefrorene Werte als aktuell
darstellt, ist schlimmer als eine, die sagt, dass sie die Verbindung verloren hat.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS, 5 Tests

- [ ] **Step 6: Von Hand ansehen**

```bash
uv run loxmatter run --url ws://<matter-server>:5580/ws --miniserver 127.0.0.1 --port 7000
```

Dann `http://localhost:8080` öffnen und alle vier Ansichten durchgehen. Was hier
auffällt, gehört in den Bericht — eine Oberfläche lässt sich nicht allein über Tests
beurteilen.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web src/loxmatter/loxone/server.py tests/api/test_web.py
git commit -m "feat(web): vier Ansichten ohne Build-Schritt"
```

---

### Task 8: Zusammenbau, Absicherung, Durchstich

**Files:**
- Modify: `src/loxmatter/loxone/server.py`, `src/loxmatter/cli.py`
- Modify: `deploy/testhost/docker-compose.yml`
- Modify: `README.md`
- Create: `tests/api/test_security.py`

- [ ] **Step 1: Die Absicherung, die diese Phase nötig macht**

Bis Phase 4 bot der Dienst `/cmd` und `/resync`. Ab jetzt lernt er Geräte ein und
entfernt sie — **wer den Port erreicht, kann ein Gerät aus der Fabric werfen.**

Das ist kein theoretischer Punkt: `run` bindet auf `0.0.0.0` ohne Option, das Vorbild.

Baue mindestens:

- eine `--host`-Option mit Standard `0.0.0.0` (der Miniserver muss den Dienst erreichen),
- ein optionales Token über `--api-token` oder `LOXMATTER_API_TOKEN`, das **nur** die
  `/api`-Routen schützt, nicht `/cmd` und `/resync` — der Miniserver kann keinen Header
  mitschicken,
- und beim Start eine deutliche Warnung im Log, wenn kein Token gesetzt ist.

```python
def build_api_guard(token: str | None) -> Callable[..., None]:
    """Schuetzt die /api-Routen, nicht die des Miniservers.

    Der Miniserver ruft virtuelle Ausgaenge ohne Header auf - er kann kein
    Token mitschicken. /cmd und /resync muessen deshalb offen bleiben, und
    das ist eine bewusste Grenze, keine Nachlaessigkeit: wer den Port
    erreicht, kann weiterhin Geraete schalten. Was das Token verhindert,
    ist das Einlernen, das Entfernen und der Download der
    Fabric-Sicherung - also alles, was den Bestand veraendert.
    """

    async def guard(authorization: str | None = Header(default=None)) -> None:
        if token is None:
            return
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Ungültiges oder fehlendes Token")

    return guard
```

Beim Start ohne Token:

```python
    if api_token is None:
        logger.warning(
            "Kein API-Token gesetzt — die Oberfläche ist für jeden erreichbar, "
            "der den Port erreicht, einschließlich Einlernen und Entfernen von "
            "Geräten. Setze LOXMATTER_API_TOKEN oder --api-token."
        )
```

Tests: ohne Token sind die `/api`-Routen offen und die Warnung erscheint; mit Token
antworten sie ohne Header mit 401, `/cmd` und `/resync` aber unverändert; und die
Fabric-Sicherung ist auch mit gesetztem Token nur mit Header erreichbar.

Trage die Entscheidung in Spec 9 ein — mitsamt der Begründung, warum der Loxone-Pfad
ungeschützt bleiben muss.

- [ ] **Step 2: Alles verbinden**

`build_app` bindet die fünf Router ein und liefert die Oberfläche aus. `run` reicht den
Matter-Client durch, damit Einlernen funktioniert.

- [ ] **Step 3: Compose und README**

Der `loxmatter`-Dienst in `deploy/testhost/docker-compose.yml` veröffentlicht jetzt einen
Port, der eine Bedienoberfläche trägt. Vermerke das dort und im README, zusammen mit dem
Hinweis zum Token.

- [ ] **Step 4: Vollständige Prüfung**

```bash
uv run pytest -v && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 5: Durchstich an echter Hardware**

**Dieser Schritt braucht einen Menschen.** Er ist der Zweck der Phase.

Mit laufendem matter-server:

1. Ein Gerät über die Oberfläche **einlernen** — Pairing-Code eingeben, Gerät erscheint.
2. In der Signalansicht die Live-Werte sehen und einen Titel ändern; prüfen, dass der
   Schlüssel unverändert bleibt.
3. Das Gerät über die Oberfläche **schalten** und die Reaktion am Gerät beobachten.
4. Vorlagen als ZIP herunterladen und den Inhalt prüfen.
5. Im Systemcheck einen Fehler provozieren (matter-server stoppen) und sehen, ob die
   rote Zeile den richtigen Hinweis gibt.
6. Ein Gerät wieder **entfernen**.

Was abweicht, geht in die Spec — **nicht** in eine Anpassung der Tests.

- [ ] **Step 6: Commit**

```bash
git add src tests deploy README.md docs
git commit -m "feat(web): Oberflaeche verbunden und abgesichert"
```

---

## Abschluss der Phase

Die Phase ist fertig, wenn:

1. `uv run pytest` ohne Hardware und ohne Netz durchläuft,
2. die sechs Punkte aus Task 8 Schritt 5 an echter Hardware bestätigt sind,
3. die Absicherungsentscheidung in Spec 9 steht,
4. Abweichungen in der Spec stehen.

**Nicht Teil dieser Phase:** alles aus Spec 8.2 — Szenen, Zeitpläne, Automatisierung,
Räume, Nutzerverwaltung. Und die Farbbedienung bleibt so unvalidiert wie in Phase 4,
solange keine Matter-Leuchte zur Verfügung steht; die Oberfläche zeigt die Regler, aber
niemand hat gesehen, ob die Farbe stimmt.
