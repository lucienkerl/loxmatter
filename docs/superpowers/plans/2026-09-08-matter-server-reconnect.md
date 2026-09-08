# Wiederverbindung zu matter-server — Umsetzungsplan

> **Für agentische Bearbeiter:** ERFORDERLICHE SUB-SKILL: `superpowers:subagent-driven-development` (empfohlen) oder `superpowers:executing-plans`, um diesen Plan Aufgabe für Aufgabe umzusetzen. Die Schritte tragen Kästchen (`- [ ]`) zum Abhaken.

**Ziel:** loxmatter bemerkt den Verlust der Websocket-Verbindung zu matter-server, baut sie mit Backoff wieder auf, und sagt die Wahrheit darüber, solange sie fehlt.

**Architektur:** Der Listener-Task, den `connect()` startet, ist bereits das Signal für den Abriss — es wird heute nur weggeworfen. Aufgabe 1 hebt es auf, Aufgabe 2 baut darauf einen Verwalter, der die vorhandene Startsequenz erneut durchläuft. Aufgabe 3 und 4 machen den Zustand nach außen sichtbar: für Loxone über den verstummenden Heartbeat, für die Oberfläche über einen „zuletzt gehört"-Zeitstempel.

**Tech-Stack:** Python 3.12, `asyncio`, `pytest` (`asyncio_mode=auto`), `ruff`, `mypy --strict`.

**Entwurf:** [docs/superpowers/specs/2026-09-08-matter-server-reconnect-design.md](../specs/2026-09-08-matter-server-reconnect-design.md)

## Globale Randbedingungen

- Alle Befehle laufen aus dem Wurzelverzeichnis des Worktrees, nie mit `cd` in das Hauptcheckout. Niemals `git stash` — der Stash-Stapel ist mit anderen Sitzungen geteilt.
- Projektsprache in Kommentaren und Docstrings ist Deutsch. **Neue** Kommentarblöcke ohne Umlaute (`waere`, `Geraet`, `fuehrt`); bestehende Umlaute in unberührten Zeilen bleiben.
- Zeilenlänge 100 (`[tool.ruff] line-length = 100`).
- Die vier Prüfungen: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`. Die volle Suite braucht rund **vier Minuten** — das sieht aus wie ein Hänger, ist keiner, nicht abbrechen.
- Ausgangsstand der Suite auf diesem Branch: **1392 passed, 2 skipped**. Jede Aufgabe nennt, um wie viel die Zahl steigen muss.
- Zeitstempel kommen ausschließlich aus `loxmatter.timestamps.now_iso()` — die Codebasis hat dafür genau einen Helfer, und der bleibt es.
- `mypy` läuft mit `strict = true` über `src` und `scripts`. Das ist in Aufgabe 3 ein tragender Teil der Absicherung, nicht nur Formsache.

## Abweichung vom Entwurf, bewusst

Der Entwurf verlangt in Abschnitt 5 und in Test 3 einen Test, der die **Weitergabe** von `link_ok` aus `cli.serve()` prüft — damit dort nicht versehentlich `client.connected` (eine einmal ausgewertete Property) statt `lambda: client.connected` steht.

Dieser Plan löst das anders und besser: `link_ok` bekommt die Annotation `Callable[[], bool]`. Ein `bool` an dieser Stelle ist damit ein **Typfehler**, den `mypy --strict` in CI abweist — der Fehler kann gar nicht erst committet werden. Ein Test hätte dieselbe Sache schwächer geprüft, denn er müsste `cli.serve()` starten oder dessen Aufbau nachbilden.

Was der Entwurf inhaltlich will, prüft Aufgabe 3 trotzdem: ein `link_ok`, das erst `True` und dann `False` liefert, muss den Puls tatsächlich anhalten. Das schließt die Lücke „einmal ausgewertet" auf der Verhaltensseite.

## Dateien

| Datei | Rolle | Aufgabe |
| --- | --- | --- |
| `src/loxmatter/matter/client.py` | `connected` ehrlich, `wait_for_link_loss()` neu | 1 |
| `src/loxmatter/matter/supervisor.py` | **neu** — `attach()` und `supervise()` | 2 |
| `src/loxmatter/cli.py` | benutzt `attach()`, startet `supervise()`, übergibt `link_ok` | 2, 3 |
| `src/loxmatter/loxone/runtime.py` | `link_ok`, `_last_heard`, `last_heard_for()` | 3, 4 |
| `src/loxmatter/api/models.py` | `DeviceOut.last_heard` | 4 |
| `src/loxmatter/api/devices.py` | füllt `last_heard` | 4 |
| `tests/matter/test_client.py` | Abriss wird bemerkt | 1 |
| `tests/matter/test_supervisor.py` | **neu** — Wiederaufbau mit Backoff | 2 |
| `tests/loxone/test_runtime.py` | Heartbeat schweigt, „zuletzt gehört" | 3, 4 |

---

### Aufgabe 1: Der Client merkt den Abriss

**Dateien:**
- Ändern: `src/loxmatter/matter/client.py` (Eigenschaft `connected`, Zeile 382–392)
- Ändern: `tests/matter/test_client.py` (anhängen)

**Schnittstellen:**
- Nutzt: `self._listener_task` (bereits vorhanden, gesetzt in `connect()` Zeile 332, geleert in `disconnect()` Zeile 349).
- Liefert: `BridgeMatterClient.connected -> bool` (Bedeutung ändert sich) und neu `async BridgeMatterClient.wait_for_link_loss() -> None`. Aufgabe 2 und 3 bauen darauf.

- [ ] **Schritt 1: Die fehlschlagenden Tests schreiben**

An `tests/matter/test_client.py` anhängen. `FakeUpstream` (Zeile 51) nimmt bereits `fail_connect`; gebraucht wird ein Listener, der **nach** der Bereitschaft stirbt — den gibt es noch nicht, deshalb eine eigene Attrappe:

```python
class DyingUpstream(FakeUpstream):
    """Ein Listener, der Bereitschaft meldet und danach stirbt.

    Genau der Fall aus dem Betriebsausfall vom 8. September 2026: die
    Verbindung steht, `connect()` ist laengst zurueck, und dann bricht das
    Websocket weg. `FakeUpstream(fail_connect=True)` bildet das NICHT ab -
    das scheitert schon vor der Bereitschaft und wird von `_start_listener`
    abgeraeumt, bevor ein Client entsteht.
    """

    async def start_listening(self, init_ready=None) -> None:
        self.start_listening_calls += 1
        self._nodes = self._configured_nodes
        if init_ready is not None:
            init_ready.set()
        # Kurz warten, damit `connect()` den Task uebernehmen kann, bevor er
        # endet - sonst ist er schon fertig, wenn `_start_listener` ihn
        # zurueckgibt, und der Test pruefte einen anderen Fall.
        await asyncio.sleep(0)
        raise ConnectionResetError("Websocket weg")


async def test_connected_becomes_false_when_the_listener_dies():
    """`connected` sagte bisher nur, ob `connect()` gelaufen ist.

    Die alte Bedingung war `self._upstream is not None` - gesetzt in
    `connect()`, geleert einzig von `disconnect()`. Stirbt das Websocket,
    blieb sie stehen, und der Diagnosepunkt `matter-server` meldete
    "Connected", waehrend kein Wert mehr ankam und jedes /cmd mit 502
    scheiterte.
    """
    upstream = DyingUpstream()
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: FakeSession(),
    )
    await bridge.connect()
    assert bridge.connected is True

    # Dem Listener-Task Gelegenheit geben, tatsaechlich zu sterben.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert bridge.connected is False


async def test_wait_for_link_loss_returns_when_the_listener_dies():
    upstream = DyingUpstream()
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: FakeSession(),
    )
    await bridge.connect()

    # Kehrt zurueck, statt zu haengen - und wirft die Ausnahme des Listeners
    # NICHT weiter: der Aufrufer will wissen, DASS die Verbindung weg ist.
    await asyncio.wait_for(bridge.wait_for_link_loss(), timeout=1.0)


async def test_wait_for_link_loss_returns_immediately_without_a_listener():
    """Ein nie verbundener Client darf hier nicht haengen."""
    bridge, _upstream = make_connected_pair()
    await asyncio.wait_for(bridge.wait_for_link_loss(), timeout=1.0)
```

- [ ] **Schritt 2: Prüfen, dass die Tests fehlschlagen**

```bash
uv run pytest tests/matter/test_client.py -k "listener_dies or without_a_listener" -v
```

Erwartung: **3 failed.** Die ersten beiden mit `AttributeError: 'BridgeMatterClient' object has no attribute 'wait_for_link_loss'` bzw. `assert True is False`; der dritte ebenfalls mit `AttributeError`.

- [ ] **Schritt 3: `connected` ehrlich machen**

In `src/loxmatter/matter/client.py` den Rumpf der Eigenschaft `connected` (Zeile 382–392) ersetzen. Der bestehende Docstring beschreibt die alte Bedingung und wird mit ersetzt:

```python
    @property
    def connected(self) -> bool:
        """Ob die Verbindung zu matter-server gerade TRAEGT.

        Frueher war das `self._upstream is not None` - also die Antwort auf
        "hat jemand connect() aufgerufen?", nicht auf "steht die
        Verbindung?". Das Feld wird einmal in `connect()` gesetzt und
        ausschliesslich von `disconnect()` geleert; stirbt das Websocket,
        blieb es stehen. Am 8. September 2026 hat genau das einen Ausfall
        unsichtbar gemacht: `GET /api/diagnostics/system` meldete
        "Connected", waehrend kein Geraetewert mehr ankam und jeder
        Loxone-Befehl mit 502 scheiterte (siehe Entwurf 2026-09-08,
        Abschnitt 1.3).

        Deshalb zaehlt jetzt zusaetzlich der Listener-Task: solange er
        laeuft, empfaengt dieser Client Push-Updates; ist er beendet, ist
        die Verbindung weg, ganz gleich was `_upstream` noch enthaelt.

        Anders als frueher ist das damit NICHT mehr dieselbe Bedingung wie
        in `_require_upstream`. Das ist Absicht: ein Aufruf gegen einen
        toten Upstream soll weiterhin an der Stelle scheitern, an der er
        stattfindet, und nicht schon hier.
        """
        return (
            self._upstream is not None
            and self._listener_task is not None
            and not self._listener_task.done()
        )
```

- [ ] **Schritt 4: `wait_for_link_loss()` ergänzen**

Unmittelbar hinter der Eigenschaft `connected` einfügen:

```python
    async def wait_for_link_loss(self) -> None:
        """Kehrt zurueck, sobald der Listener endet - gleich aus welchem Grund.

        Das Signal fuer einen Verbindungsabriss existiert bereits: der Task
        aus `upstream.start_listening()`. Er wurde bis zum 8. September 2026
        nur nirgends abgeholt - kein `add_done_callback`, keine
        Ueberwachung -, sodass seine Ausnahme still versickerte und niemand
        merkte, dass die Bruecke taub war.

        `asyncio.wait` statt `await task`: ein `await` auf einen Task
        UEBERTRAEGT die Abbruchanforderung des Wartenden auf den Task. Wird
        der Verwalter (siehe `matter/supervisor.py`) beim Herunterfahren
        abgebrochen, risse er damit den Listener mit - und `disconnect()`
        faende ihn bereits abgebrochen vor. `asyncio.wait` beruehrt die
        uebergebenen Tasks nicht.

        Die Ausnahme des Listeners wird abgeholt und protokolliert, nicht
        weitergeworfen: der Aufrufer will wissen, DASS die Verbindung weg
        ist, und soll nicht zwischen Abbruchgruenden unterscheiden muessen.
        Ohne das `exception()` wuerde Python beim Aufraeumen des Tasks
        ausserdem "Task exception was never retrieved" ins Log schreiben.
        """
        task = self._listener_task
        if task is None:
            return
        await asyncio.wait({task})
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Verbindung zu matter-server verloren: %s", exc)
        else:
            logger.warning("Listener von matter-server endete ohne Fehler")
```

- [ ] **Schritt 5: Prüfen, dass die Tests bestehen**

```bash
uv run pytest tests/matter/test_client.py -v
```

Erwartung: alle Tests der Datei grün, einschließlich der drei neuen.

- [ ] **Schritt 6: Beweisen, dass der erste Test wirklich beißt**

`connected` probeweise auf die alte Bedingung zurücksetzen — nur die Rückgabe, den Docstring stehen lassen:

```python
        return self._upstream is not None
```

```bash
uv run pytest tests/matter/test_client.py -k "connected_becomes_false" -v
```

Erwartung: **1 failed** mit `assert True is False`. Danach zurücknehmen und Schritt 5 wiederholen.

- [ ] **Schritt 7: Prüfläufe und Commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Erwartung: alle vier grün, **1395 passed, 2 skipped** (drei neue Tests).

```bash
git add src/loxmatter/matter/client.py tests/matter/test_client.py
git commit -m "$(cat <<'EOF'
fix(matter): `connected` sagt die Wahrheit, und der Abriss wird abgeholt

`connected` war `self._upstream is not None` - die Antwort auf "hat jemand
connect() aufgerufen?", nicht auf "steht die Verbindung?". Das Feld wird
einmal gesetzt und nur von disconnect() geleert; stirbt das Websocket, blieb
es stehen. Am 8. September 2026 meldete die Diagnose deshalb "Connected",
waehrend kein Geraetewert mehr ankam und jeder Loxone-Befehl mit 502
scheiterte.

Das Signal fuer den Abriss gab es die ganze Zeit: den Task aus
start_listening(). Er wurde nur nirgends abgeholt. `wait_for_link_loss()`
holt ihn jetzt ab - mit asyncio.wait statt await, damit ein abgebrochener
Wartender den Listener nicht mitreisst.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Aufgabe 2: Die Wiederanbindung

**Dateien:**
- Erstellen: `src/loxmatter/matter/supervisor.py`
- Ändern: `src/loxmatter/cli.py` (Startsequenz in `serve()`, Zeile 600–623)
- Erstellen: `tests/matter/test_supervisor.py`

**Schnittstellen:**
- Nutzt aus Aufgabe 1: `await client.wait_for_link_loss() -> None`.
- Liefert: `async attach(client, store, runtime) -> int` (Anzahl nachgetragener Kommandos) und `async supervise(client, store, runtime, *, sleep=asyncio.sleep, backoff_start=1.0, backoff_max=60.0) -> None`.

- [ ] **Schritt 1: Den fehlschlagenden Test schreiben**

Neue Datei `tests/matter/test_supervisor.py`:

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

"""Der Verwalter, der die Verbindung zu matter-server wieder aufbaut."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.matter.supervisor import attach, supervise


class FakeClient:
    """Steht fuer `BridgeMatterClient`, so weit der Verwalter ihn benutzt."""

    def __init__(self, connect_failures: int = 0) -> None:
        self._connect_failures = connect_failures
        self.connect_calls = 0
        self.subscribe_calls = 0
        self._link_lost = asyncio.Event()

    async def wait_for_link_loss(self) -> None:
        await self._link_lost.wait()
        self._link_lost.clear()

    def drop_link(self) -> None:
        self._link_lost.set()

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self._connect_failures:
            raise ConnectionError("matter-server nicht erreichbar")

    async def subscribe(self, resolve_device_id, handler) -> None:
        self.subscribe_calls += 1

    async def snapshots(self) -> list[object]:
        return []


class FakeStore:
    def __init__(self) -> None:
        self.backfill_types_calls = 0
        self.backfill_commands_calls = 0

    def device_id_for_node(self, node_id: int) -> int | None:
        return None

    def backfill_device_types(self, snapshots) -> int:
        self.backfill_types_calls += 1
        return 0

    def backfill_commands(self, snapshots) -> int:
        self.backfill_commands_calls += 1
        return 3


class FakeRuntime:
    def __init__(self) -> None:
        self.seed_calls = 0
        self.resend_calls = 0

    async def seed_from_snapshot(self, snapshots) -> int:
        self.seed_calls += 1
        return 0

    async def resend_all(self) -> int:
        self.resend_calls += 1
        return 0


async def test_attach_runs_the_whole_startup_sequence():
    """`attach` ist die Sequenz, die bis dahin nur in `cli.serve()` stand.

    Sie hier zu buendeln ist der Kern dieser Aufgabe: Start und
    Wiederaufbau muessen dasselbe tun, sonst driften sie auseinander - und
    das faellt erst auf, wenn nach einem Reconnect etwas fehlt, das beim
    Start selbstverstaendlich war.
    """
    client, store, runtime = FakeClient(), FakeStore(), FakeRuntime()

    gained = await attach(client, store, runtime)

    assert client.subscribe_calls == 1
    assert runtime.seed_calls == 1
    assert store.backfill_types_calls == 1
    assert store.backfill_commands_calls == 1
    # Der eigentliche Gewinn: Loxone bekommt den VOLLSTAENDIGEN Zustand
    # zurueck, statt auf den Werten sitzenzubleiben, die beim Abriss
    # zufaellig die letzten waren.
    assert runtime.resend_calls == 1
    assert gained == 3


async def test_supervise_rebuilds_after_a_link_loss():
    client, store, runtime = FakeClient(), FakeStore(), FakeRuntime()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    task = asyncio.ensure_future(supervise(client, store, runtime, sleep=fake_sleep))
    await asyncio.sleep(0)
    client.drop_link()
    for _ in range(10):
        await asyncio.sleep(0)

    assert client.connect_calls == 1
    assert runtime.resend_calls == 1
    assert slept == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_supervise_backs_off_and_keeps_trying():
    """Zwei Fehlschlaege, dann traegt es - die Wartezeiten verdoppeln sich.

    Gemessen wird ueber eine eingesetzte `sleep`-Funktion, nicht real
    abgewartet: ein Test, der acht Sekunden schlaeft, wird beim naechsten
    Umbau als "langsam" uebersprungen und prueft dann gar nichts mehr.
    """
    client = FakeClient(connect_failures=2)
    store, runtime = FakeStore(), FakeRuntime()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    task = asyncio.ensure_future(supervise(client, store, runtime, sleep=fake_sleep))
    await asyncio.sleep(0)
    client.drop_link()
    for _ in range(20):
        await asyncio.sleep(0)

    assert client.connect_calls == 3
    assert slept == [1.0, 2.0]
    assert runtime.resend_calls == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_supervise_caps_the_backoff():
    client = FakeClient(connect_failures=12)
    store, runtime = FakeStore(), FakeRuntime()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    task = asyncio.ensure_future(
        supervise(client, store, runtime, sleep=fake_sleep, backoff_max=8.0)
    )
    await asyncio.sleep(0)
    client.drop_link()
    for _ in range(60):
        await asyncio.sleep(0)

    # 1, 2, 4, 8, dann gedeckelt - und es gibt KEINE Aufgabegrenze:
    # matter-server kann beliebig lange weg sein, und niemand steht daneben.
    assert slept[:5] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert all(s <= 8.0 for s in slept)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
```

- [ ] **Schritt 2: Prüfen, dass die Tests fehlschlagen**

```bash
uv run pytest tests/matter/test_supervisor.py -v
```

Erwartung: Sammelfehler `ModuleNotFoundError: No module named 'loxmatter.matter.supervisor'`.

- [ ] **Schritt 3: Das Modul schreiben**

Neue Datei `src/loxmatter/matter/supervisor.py`:

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

"""Haelt die Verbindung zu matter-server aufrecht.

Am 8. September 2026 starb das Websocket zu matter-server, und loxmatter
verband sich nicht neu - es bemerkte es nicht einmal. Von aussen sah alles
gesund aus: die WebUI antwortete, der Heartbeat pulste weiter, die Diagnose
meldete "Connected". Tatsaechlich kam kein Geraetewert mehr an, und jeder
Loxone-Befehl scheiterte mit 502. Einziger Ausweg war ein Neustart des
Containers, und dasselbe passierte am selben Abend ein zweites Mal.

Dieses Modul liegt bewusst NICHT im Client: der Wiederaufbau braucht
`Store` und `Runtime`, und ein Client, der die halbe Anwendung kennt, waere
die schlechtere Grenze. Der Client meldet nur, dass die Verbindung weg ist
(`wait_for_link_loss`); was danach zu geschehen hat, weiss diese Ebene.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0


async def attach(client: Any, store: Any, runtime: Any) -> int:
    """Bindet eine bestehende Verbindung an Store und Laufzeit.

    Genau die Sequenz, die bis zum 8. September 2026 nur in `cli.serve()`
    stand. Sie hier zu buendeln ist keine Aufraeumarbeit: Start und
    Wiederaufbau MUESSEN dasselbe tun, sonst driften sie auseinander - und
    das faellt erst auf, wenn nach einem Reconnect etwas fehlt, das beim
    Start selbstverstaendlich war.

    `resend_all()` am Ende ist der eigentliche Gewinn. Die bestehende
    Zusicherung "ein Neustart der Bruecke wirkt wie /resync" (Spec 6.4) gilt
    damit auch fuer eine Wiederverbindung: Loxone bekommt den vollstaendigen
    Zustand zurueck, statt auf den Werten sitzenzubleiben, die beim Abriss
    zufaellig die letzten waren.

    Liefert die Anzahl nachgetragener Kommandos zurueck - `cli.serve()`
    meldet sie beim Start auf der Konsole, der Verwalter protokolliert sie.
    Ein `typer.echo` gehoert nicht hierher: dieses Modul laeuft auch dann,
    wenn niemand auf ein Terminal sieht.

    `runtime.start()` gehoert BEWUSST NICHT hierher. Es startet die
    Heartbeat- und Resend-Schleifen, und die sollen einen Ausfall
    ueberdauern, nicht mit ihm neu beginnen.
    """
    await client.subscribe(store.device_id_for_node, runtime)
    snapshots = await client.snapshots()
    await runtime.seed_from_snapshot(snapshots)
    store.backfill_device_types(snapshots)
    gained: int = store.backfill_commands(snapshots)
    await runtime.resend_all()
    return gained


async def supervise(
    client: Any,
    store: Any,
    runtime: Any,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    backoff_start: float = BACKOFF_START_SECONDS,
    backoff_max: float = BACKOFF_MAX_SECONDS,
) -> None:
    """Baut die Verbindung nach jedem Abriss wieder auf - endlos.

    **Ohne Aufgabegrenze**, und das ist eine Entscheidung: eine Bruecke, die
    nach zehn Versuchen aufgibt, ist schlechter als eine, die alle sechzig
    Sekunden weiter anklopft. matter-server kann beliebig lange weg sein -
    ein Update, ein Neustart des Hosts, ein Container, der erst nach dem
    Netz hochkommt -, und niemand steht daneben, um von Hand nachzuhelfen.

    `sleep` ist einsetzbar, damit die Tests die Wartezeiten MESSEN koennen,
    statt sie abzuwarten. Ein Test, der acht Sekunden schlaeft, wird beim
    naechsten Umbau als "langsam" uebersprungen und prueft dann nichts mehr.
    """
    while True:
        await client.wait_for_link_loss()
        logger.warning("Verbindung zu matter-server verloren - baue sie neu auf")
        delay = backoff_start
        while True:
            try:
                await client.connect()
                gained = await attach(client, store, runtime)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Wiederaufbau gescheitert (%s) - naechster Versuch in %.0f s", exc, delay
                )
                await sleep(delay)
                delay = min(delay * 2, backoff_max)
            else:
                logger.info(
                    "Verbindung zu matter-server wiederhergestellt (%d Kommandos nachgetragen)",
                    gained,
                )
                break
```

- [ ] **Schritt 4: Prüfen, dass die Tests bestehen**

```bash
uv run pytest tests/matter/test_supervisor.py -v
```

Erwartung: **5 passed.**

- [ ] **Schritt 5: `cli.serve()` auf `attach` umstellen**

In `src/loxmatter/cli.py` den Block von `await client.subscribe(...)` bis `await runtime.resend_all()` (Zeile 600–623) ersetzen.

**Dabei wandert `await runtime.start()` nach vorn**, und das ist eine bewusste Verhaltensänderung, keine Umsortierung aus Bequemlichkeit. Heute steht es **zwischen** `client.subscribe(...)` und `client.snapshots()`. Da `attach()` genau diese Spanne umschließt, kann es nicht dort bleiben — es müsste sonst mitten in `attach()` stehen und wäre bei jedem Wiederaufbau erneut aufgerufen worden, was die Heartbeat- und Resend-Schleifen jedes Mal neu gestartet hätte.

Die neue Position **vor** `attach()` ist unbedenklich: `runtime.start()` startet zwei Hintergrund-Schleifen und wartet auf nichts. Die Heartbeat-Schleife darf vor dem ersten Wert laufen — die Verbindung steht zu diesem Zeitpunkt bereits (`client.connect()` lief davor), und ab Aufgabe 3 ist genau das die Bedingung, die sie pulsen lässt. Die Resend-Schleife verschickt nur markierte Signale und findet bis zum ersten `seed_from_snapshot` schlicht nichts vor.

Wandert es stattdessen **hinter** `attach()`, wäre `resend_all()` gelaufen, bevor die Schleifen stehen — das ist folgenlos, aber die frühere Position hält den Zustand „Brücke lebt" ab dem frühestmöglichen Moment aufrecht, und genau darum geht es in dieser Aufgabe.

```python
        await runtime.start()
        # Startsequenz und Wiederaufbau teilen sich seit dem 8. September
        # 2026 eine Stelle (`matter.supervisor.attach`) - siehe dort, warum.
        gained = await attach(client, store, runtime)
        if gained:
            typer.echo(i18n.t("cli.run.echo_commands_backfilled", count=gained))
        # Der Verwalter laeuft, solange der Dienst laeuft: stirbt das
        # Websocket zu matter-server, baut er die Verbindung wieder auf und
        # laesst `attach` erneut laufen. Ohne ihn bleibt die Bruecke nach
        # einem Neustart von matter-server stumm, ohne es zu melden - genau
        # der Ausfall vom 8. September 2026.
        supervisor_task = asyncio.ensure_future(supervise(client, store, runtime))
```

Der Import oben in `cli.py` ergänzt sich zu:

```python
from loxmatter.matter.supervisor import attach, supervise
```

Im `finally`-Block, **vor** `await runtime.stop()`, den Verwalter abräumen:

```python
        supervisor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor_task
```

Falls `contextlib` in `cli.py` noch nicht importiert ist, `import contextlib` zu den Standardbibliothek-Importen ergänzen. `supervisor_task` muss vor dem `try` mit `supervisor_task: asyncio.Task[None] | None = None` vorbelegt und im `finally` auf `None` geprüft werden — sonst schlägt der Abbruch fehl, wenn `serve()` schon vor dem Start des Verwalters scheitert:

```python
        if supervisor_task is not None:
            supervisor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor_task
```

- [ ] **Schritt 6: Prüfläufe und Commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Erwartung: alle vier grün, **1400 passed, 2 skipped** (fünf neue Tests).

Schlägt ein bestehender CLI-Test fehl, ist das ein Befund über die Umstellung in Schritt 5 und **kein** Anlass, den Test anzupassen — melden.

```bash
git add src/loxmatter/matter/supervisor.py src/loxmatter/cli.py tests/matter/test_supervisor.py
git commit -m "$(cat <<'EOF'
feat(matter): Verwalter baut die Verbindung zu matter-server wieder auf

Stirbt das Websocket, verband loxmatter sich bisher nicht neu - und bemerkte
es nicht einmal. `supervise()` wartet jetzt auf den Abriss, protokolliert ihn
und baut mit Backoff (1s, verdoppelnd, gedeckelt bei 60s) wieder auf. Ohne
Aufgabegrenze: matter-server kann beliebig lange weg sein, und eine Bruecke,
die aufgibt, ist schlechter als eine, die weiter anklopft.

`attach()` buendelt die Startsequenz, die bisher nur in cli.serve() stand.
Start und Wiederaufbau teilen sich damit eine Stelle - inklusive
`resend_all()`, sodass Loxone nach einem Wiederaufbau den vollstaendigen
Zustand zurueckbekommt statt der Werte, die beim Abriss zufaellig die
letzten waren.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Aufgabe 3: Der Heartbeat verstummt

**Dateien:**
- Ändern: `src/loxmatter/loxone/runtime.py` (`__init__` Zeile 70–77, `_heartbeat_loop` Zeile 511–534)
- Ändern: `src/loxmatter/cli.py` (Aufbau von `Runtime`, Zeile 586)
- Ändern: `tests/loxone/test_runtime.py` (anhängen)

**Schnittstellen:**
- Nutzt aus Aufgabe 1: `BridgeMatterClient.connected -> bool`.
- Liefert: `Runtime(..., link_ok: Callable[[], bool] = lambda: True)`.

- [ ] **Schritt 1: Die fehlschlagenden Tests schreiben**

An `tests/loxone/test_runtime.py` anhängen:

```python
async def test_the_heartbeat_stays_silent_without_a_matter_link(tmp_path):
    """Ohne Matter-Verbindung darf `bridge_alive` NICHT pulsen.

    Der Heartbeat ist der Watchdog-Eingang in Loxone. Pulst er weiter,
    waehrend die Bruecke taub ist, meldet Loxone "alles in Ordnung" - genau
    das ist am 8. September 2026 passiert, und deshalb fiel der Ausfall
    stundenlang niemandem auf.
    """
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    runtime = Runtime(
        store, sender, heartbeat_seconds=0.01, link_ok=lambda: False
    )
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()

    assert HEARTBEAT_KEY not in sender


async def test_the_heartbeat_pulses_with_a_matter_link(tmp_path):
    """Gegenprobe - sonst prueft der Test oben nur, dass nichts passiert."""
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    runtime = Runtime(store, sender, heartbeat_seconds=0.01, link_ok=lambda: True)
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()

    assert HEARTBEAT_KEY in sender


async def test_the_heartbeat_falls_silent_when_the_link_drops(tmp_path):
    """`link_ok` wird bei JEDEM Takt neu gefragt, nicht einmal beim Aufbau.

    Wuerde `cli.serve()` versehentlich `client.connected` statt
    `lambda: client.connected` uebergeben - eine Property, also ein einmal
    ausgewerteter Bool -, haenge der Heartbeat fuer immer am Zustand des
    Startmoments und verstummte nie. `mypy --strict` weist das inzwischen
    als Typfehler ab; dieser Test deckt dieselbe Luecke auf der
    Verhaltensseite ab.
    """
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    alive = [True]
    runtime = Runtime(
        store, sender, heartbeat_seconds=0.01, link_ok=lambda: alive[0]
    )
    await runtime.start()
    await asyncio.sleep(0.05)
    assert HEARTBEAT_KEY in sender

    alive[0] = False
    before = len([k for k in sender.keys() if k == HEARTBEAT_KEY])
    await asyncio.sleep(0.05)
    after = len([k for k in sender.keys() if k == HEARTBEAT_KEY])
    await runtime.stop()

    assert after == before
```

`HEARTBEAT_KEY` und `asyncio` müssen dafür in der Testdatei importiert sein. Prüfen und gegebenenfalls ergänzen:

```python
import asyncio

from loxmatter.loxone.runtime import HEARTBEAT_KEY, Runtime
```

- [ ] **Schritt 2: Prüfen, dass die Tests fehlschlagen**

```bash
uv run pytest tests/loxone/test_runtime.py -k "heartbeat_st or heartbeat_pulses or heartbeat_falls" -v
```

Erwartung: **3 failed** mit `TypeError: Runtime.__init__() got an unexpected keyword argument 'link_ok'`.

- [ ] **Schritt 3: `link_ok` in `Runtime` aufnehmen**

In `src/loxmatter/loxone/runtime.py` die Signatur (Zeile 70–77) erweitern und das Feld setzen:

```python
    def __init__(
        self,
        store: Store,
        sender: Sender,
        *,
        heartbeat_seconds: float = 30.0,
        resend_poll_seconds: float = 5.0,
        link_ok: Callable[[], bool] = lambda: True,
    ) -> None:
        self._store = store
        self._sender = sender
        self._heartbeat_seconds = heartbeat_seconds
        self._resend_poll_seconds = resend_poll_seconds
        # Ob die Verbindung zu matter-server gerade traegt - vom Heartbeat
        # bei JEDEM Takt neu gefragt (siehe `_heartbeat_loop`). Die
        # Annotation `Callable[[], bool]` ist hier tragende Absicherung,
        # nicht Formsache: `cli.serve()` uebergibt `lambda: client.connected`,
        # und `client.connected` allein - eine Property, also ein einmal
        # ausgewerteter Bool - waere damit ein Typfehler, den `mypy --strict`
        # in CI abweist. Ohne diese Annotation haenge der Heartbeat still am
        # Zustand des Startmoments und verstummte nie.
        self._link_ok = link_ok
```

- [ ] **Schritt 4: `_heartbeat_loop` anpassen**

In `_heartbeat_loop` (Zeile 511 ff.) die drei Zeilen im `try` in eine Bedingung fassen. Der bestehende Kommentarblock über `_notify_observers` bleibt unverändert stehen:

```python
    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                # Kein Puls ohne Matter-Verbindung (8. September 2026): der
                # Heartbeat ist der Watchdog-Eingang in Loxone. Pulst er
                # weiter, waehrend die Bruecke taub ist, meldet Loxone
                # "alles in Ordnung" - und genau daran ist ein
                # stundenlanger Ausfall niemandem aufgefallen.
                #
                # Das ist etwas ANDERES als der Fehlerfall unten: scheitert
                # das SENDEN, laeuft die Schleife weiter und pulst weiter,
                # damit der Fehler sichtbar wird. Fehlt die VERBINDUNG,
                # schweigt sie, damit er sichtbar wird. Zwei Zustaende, zwei
                # richtige Antworten - der Unterschied ist, wovon der Puls
                # eine Aussage macht.
                #
                # Der Wechsel von `_heartbeat_on` steht bewusst INNERHALB
                # der Bedingung: sonst liefe die Phase waehrend des Ausfalls
                # weiter und der erste Puls danach kaeme zufaellig auf
                # demselben Wert heraus wie der letzte davor - fuer einen
                # flankengesteuerten Watchdog waere das ein verschluckter
                # Takt.
                if self._link_ok():
                    self._heartbeat_on = not self._heartbeat_on
                    await self._sender.send(HEARTBEAT_KEY, self._heartbeat_on, force=True)
                    # Auch an die Oberflaeche (2026-09-03). Vorher ging der
                    # Heartbeat nur an Loxone, und eine Bruecke, an der sich
                    # gerade nichts aendert - eine Steckdose ohne Last meldet
                    # weder Strom noch Leistung -, war in der Live-Ansicht von
                    # einer abgestuerzten nicht zu unterscheiden: kein Wert
                    # bewegte sich, und niemand konnte sagen, ob nichts passiert
                    # oder nichts ankommt. Der Heartbeat ist genau das Signal,
                    # das diese Frage beantwortet; ihn der Oberflaeche
                    # vorzuenthalten war eine Luecke, keine Entscheidung.
                    self._notify_observers(HEARTBEAT_KEY, self._heartbeat_on)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Genau der Fehlerfall, den der Heartbeat melden soll, darf
                # ihn nicht zum Schweigen bringen - sonst friert der
                # Loxone-Watchdog auf dem letzten Wert ein, waehrend nichts
                # mehr laeuft (Review-Fix Important #1).
                logger.exception("Heartbeat konnte nicht gesendet werden - Schleife laeuft weiter")
            await asyncio.sleep(self._heartbeat_seconds)
```

- [ ] **Schritt 5: `cli.serve()` verdrahten**

In `src/loxmatter/cli.py` steht der Aufbau heute so (Zeile 585–587):

```python
    sender = UdpSender(miniserver, port)
    runtime = Runtime(store, sender)
    client = _build_client(url)
```

Die Reihenfolge muss sich umkehren, weil `Runtime` den Client braucht:

```python
    sender = UdpSender(miniserver, port)
    client = _build_client(url)
    # `lambda: client.connected`, NICHT `client.connected`: die zweite Form
    # waere ein einmal ausgewerteter Bool, und der Heartbeat haenge damit
    # fuer immer am Zustand des Startmoments. `mypy --strict` weist das ab.
    runtime = Runtime(store, sender, link_ok=lambda: client.connected)
```

- [ ] **Schritt 6: Prüfen, dass die Tests bestehen**

```bash
uv run pytest tests/loxone/test_runtime.py -v
```

Erwartung: alle Tests der Datei grün, einschließlich der drei neuen.

- [ ] **Schritt 7: Beweisen, dass mypy die Falle wirklich stellt**

In `cli.py` probeweise `link_ok=lambda: client.connected` durch `link_ok=client.connected` ersetzen:

```bash
uv run mypy
```

Erwartung: **Fehlschlag** mit einer Meldung der Art `Argument "link_ok" to "Runtime" has incompatible type "bool"; expected "Callable[[], bool]"`. Danach zurücknehmen und `uv run mypy` erneut laufen lassen: keine Befunde.

Bleibt mypy hier still, ist die Absicherung aus der Abweichungsnotiz oben wirkungslos und der Befund gehört gemeldet — dann braucht es doch einen Test auf die Weitergabe.

- [ ] **Schritt 8: Prüfläufe und Commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Erwartung: alle vier grün, **1403 passed, 2 skipped** (drei neue Tests).

```bash
git add src/loxmatter/loxone/runtime.py src/loxmatter/cli.py tests/loxone/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(loxone): kein Heartbeat ohne Matter-Verbindung

`bridge_alive` ist der Watchdog-Eingang in Loxone. Er pulste bisher auch
dann weiter, wenn die Bruecke gar keine Verbindung zu matter-server mehr
hatte - Loxone meldete "alles in Ordnung", waehrend kein Wert mehr ankam.
Genau daran ist der Ausfall vom 8. September 2026 stundenlang niemandem
aufgefallen.

`Runtime` bekommt dafuer `link_ok: Callable[[], bool]`, das der Heartbeat bei
jedem Takt neu fragt. Die Annotation ist tragende Absicherung: `cli.serve()`
uebergibt `lambda: client.connected`, und die Property allein waere ein
Typfehler, den mypy --strict abweist.

Unangetastet bleibt die aeltere Zusicherung: scheitert das SENDEN, laeuft
die Schleife weiter und pulst weiter. Fehlt die VERBINDUNG, schweigt sie.
Zwei Zustaende, zwei richtige Antworten.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Aufgabe 4: „Zuletzt gehört"

**Dateien:**
- Ändern: `src/loxmatter/loxone/runtime.py` (`__init__`, `on_attribute` Zeile 222, `on_node_snapshot` Zeile 288, `on_event` Zeile 328, neue Methode neben `last_values_for` Zeile 378)
- Ändern: `src/loxmatter/api/models.py` (`DeviceOut`, Zeile 74 ff.)
- Ändern: `src/loxmatter/api/devices.py` (Zusammenbau von `DeviceOut`, Zeile 222 ff.)
- Ändern: `tests/loxone/test_runtime.py` (anhängen)

**Schnittstellen:**
- Nutzt: `loxmatter.timestamps.now_iso() -> str`.
- Liefert: `Runtime.last_heard_for(device_id: int) -> str | None` und `DeviceOut.last_heard: str | None`.

- [ ] **Schritt 1: Die fehlschlagenden Tests schreiben**

An `tests/loxone/test_runtime.py` anhängen:

```python
async def test_last_heard_is_none_until_something_arrives(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    runtime = Runtime(store, FakeSender())
    assert runtime.last_heard_for(1) is None


async def test_last_heard_is_set_even_for_an_unmapped_path(tmp_path):
    """Auch ein Pfad ohne Signal zaehlt als "gehoert".

    `on_attribute` bricht bei einem unbekannten Pfad frueh ab - aber
    ANGEKOMMEN ist die Meldung trotzdem, und genau das soll der Zeitstempel
    sagen. Wuerde er erst nach der Zuordnung gesetzt, meldete ein Geraet,
    dessen Signale niemand exportiert hat, auf ewig "nie gehoert".
    """
    store = Store(tmp_path / "s.sqlite")
    runtime = Runtime(store, FakeSender())

    await runtime.on_attribute(1, "1/6/0", True)

    stamp = runtime.last_heard_for(1)
    assert stamp is not None
    assert stamp.startswith("20")


async def test_last_heard_is_set_by_an_event(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    runtime = Runtime(store, FakeSender())

    await runtime.on_event(2, "1/59/1")

    assert runtime.last_heard_for(2) is not None
    # Ein anderes Geraet bleibt unberuehrt - der Zeitstempel ist je Geraet.
    assert runtime.last_heard_for(3) is None
```

- [ ] **Schritt 2: Prüfen, dass die Tests fehlschlagen**

```bash
uv run pytest tests/loxone/test_runtime.py -k "last_heard" -v
```

Erwartung: **3 failed** mit `AttributeError: 'Runtime' object has no attribute 'last_heard_for'`.

- [ ] **Schritt 3: `Runtime` erweitern**

Import oben in `runtime.py` ergänzen:

```python
from loxmatter.timestamps import now_iso
```

In `__init__`, hinter `self._link_ok = link_ok`:

```python
        # Wann von einem Geraet zuletzt ueberhaupt etwas eintraf - je
        # Geraete-ID ein ISO-Zeitstempel. NUR IM SPEICHER, nicht in der
        # Datenbank: dieselbe Begruendung, die im Docstring von
        # `StoredDevice` schon fuer `online` steht - Erreichbarkeit ist
        # Laufzeitzustand. Ein Zeitstempel, der einen Neustart ueberlebt,
        # behauptet nach dem Start etwas, das niemand geprueft hat; `None`
        # heisst dagegen ehrlich "seit dem Start dieser Bruecke nichts
        # gehoert".
        #
        # Der Anlass (8. September 2026): ein Fensterkontakt, der nur bei
        # Aenderung sendet, sah in der Oberflaeche exakt so aus wie ein
        # Taster, von dem seit fuenf Tagen nichts kam - beide `online: true`.
        # `online` bleibt, was es ist; dies ist die zweite Zahl, die die
        # Frage erst beantwortbar macht.
        self._last_heard: dict[int, str] = {}
```

Die drei Eingangsmethoden bekommen jeweils **als erste Zeile** den Vermerk. `on_attribute` (Zeile 222):

```python
    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        self._mark_heard(device_id)
        key = self._cache_attribute(device_id, path, raw)
```

`on_event` (Zeile 328):

```python
    async def on_event(self, device_id: int, path: str) -> None:
        self._mark_heard(device_id)
        signal = self._signal_for(device_id, path, SignalKind.EVENT)
```

`on_node_snapshot` (Zeile 288) — direkt hinter dem vorhandenen Docstring, vor der ersten Anweisung:

```python
        self._mark_heard(device_id)
```

Und die beiden neuen Methoden, unmittelbar vor `last_values_for` (Zeile 378):

```python
    def _mark_heard(self, device_id: int) -> None:
        """Haelt fest, dass von diesem Geraet gerade etwas eintraf.

        Steht in `on_attribute`/`on_event`/`on_node_snapshot` jeweils GANZ
        OBEN, vor jedem fruehen Abbruch: ob ein Pfad einem exportierten
        Signal zugeordnet werden kann, ist eine Frage der Konfiguration -
        angekommen ist die Meldung so oder so, und nur das sagt dieser
        Zeitstempel aus.
        """
        self._last_heard[device_id] = now_iso()

    def last_heard_for(self, device_id: int) -> str | None:
        """Wann von diesem Geraet zuletzt etwas eintraf, oder `None`.

        `None` heisst "seit dem Start dieser Bruecke nichts gehoert" - siehe
        `_last_heard` im Konstruktor, warum das nicht persistiert wird.
        """
        return self._last_heard.get(device_id)
```

- [ ] **Schritt 4: Prüfen, dass die Tests bestehen**

```bash
uv run pytest tests/loxone/test_runtime.py -k "last_heard" -v
```

Erwartung: **3 passed.**

- [ ] **Schritt 5: Das Feld an die API durchreichen**

In `src/loxmatter/api/models.py`, in `DeviceOut` hinter `online: bool`:

```python
    online: bool
    # Wann von diesem Geraet zuletzt ueberhaupt etwas eintraf; `None`, wenn
    # seit dem Start der Bruecke nichts kam. `online` allein beantwortet die
    # Frage nicht, die am 8. September 2026 offenblieb: ein Geraet, das nur
    # bei Aenderung sendet und gerade still ist, sieht dort genauso aus wie
    # eines, von dem seit Tagen nichts kommt.
    last_heard: str | None
```

In `src/loxmatter/api/devices.py`, wo heute `online` gelesen wird (Zeile 222 ff.), die Zeile ergänzen:

```python
    values = runtime.last_values_for(device.id)
    online = bool(values.get(f"d{device.id}_online", False))
    last_heard = runtime.last_heard_for(device.id)
```

und im `return DeviceOut(...)` hinter `online=online`:

```python
        last_heard=last_heard,
```

- [ ] **Schritt 6: Prüfläufe**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Erwartung: alle vier grün, **1406 passed, 2 skipped** (drei neue Tests).

Schlägt ein bestehender API-Test fehl, weil `DeviceOut` ein Pflichtfeld mehr hat, ist das erwartet: die betroffenen Testaufbauten bekommen `last_heard=None` ergänzt. Das ist die einzige erlaubte Anpassung bestehender Tests in dieser Aufgabe — jeder andere Fehlschlag gehört gemeldet.

- [ ] **Schritt 7: Commit**

```bash
git add src/loxmatter/loxone/runtime.py src/loxmatter/api/models.py src/loxmatter/api/devices.py tests/loxone/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(api): "zuletzt gehoert" je Geraet

`online` beantwortet die Frage nicht, die am 8. September 2026 offenblieb:
ein Fensterkontakt, der nur bei Aenderung sendet und gerade still ist, sah
in der Oberflaeche exakt so aus wie ein Taster, von dem seit fuenf Tagen
nichts kam - beide `online: true`. Gesucht wurde stundenlang am falschen
Geraet.

`Runtime` fuehrt jetzt je Geraet einen Zeitstempel, gesetzt in
on_attribute/on_event/on_node_snapshot, und zwar VOR jedem fruehen Abbruch:
ob ein Pfad einem exportierten Signal zugeordnet werden kann, ist eine Frage
der Konfiguration - angekommen ist die Meldung so oder so.

Nur im Speicher, nicht in der Datenbank - dieselbe Begruendung, die im
Docstring von StoredDevice schon fuer `online` steht. `None` heisst ehrlich
"seit dem Start dieser Bruecke nichts gehoert".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Abdeckung gegen den Entwurf

| Entwurf | Aufgabe |
| --- | --- |
| 3. `connected` prüft beides | Aufgabe 1, Schritt 3 |
| 3. `wait_for_link_loss()` | Aufgabe 1, Schritt 4 |
| 3. Diagnose wird ohne eigene Änderung ehrlich | Aufgabe 1, Nebenprodukt — `_check_matter_server` liest `client.connected` |
| 4. `attach()` | Aufgabe 2, Schritt 3 |
| 4. `supervise()` mit Backoff, ohne Aufgabegrenze | Aufgabe 2, Schritt 3; Tests in Schritt 1 |
| 4. `runtime.start()` bleibt draußen | Aufgabe 2, Schritt 5 |
| 5. Heartbeat verstummt | Aufgabe 3, Schritte 3–5 |
| 5. Sendefehler lässt weiterpulsen | Aufgabe 3, Schritt 4 — der bestehende `except`-Zweig bleibt unangetastet |
| 5. Weitergabe aus `serve()` abgesichert | Aufgabe 3, Schritt 7 — über `mypy --strict` statt über einen Test, siehe Abweichungsnotiz oben |
| 6. `_last_heard`, `last_heard_for` | Aufgabe 4, Schritt 3 |
| 6. `DeviceOut.last_heard` | Aufgabe 4, Schritt 5 |
| 6. nicht persistiert | Aufgabe 4, Schritt 3 — Kommentar im Konstruktor |
| 7. Tests 1–4 samt Beißprobe | Aufgabe 1 Schritt 6, Aufgabe 2 Schritt 1, Aufgabe 3 Schritt 7, Aufgabe 4 Schritt 1 |
| 8. Nicht Teil des Entwurfs | keine Aufgabe — bewusst |
