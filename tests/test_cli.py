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

import asyncio
import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
from matter_server.client.exceptions import CannotConnect
from typer.testing import CliRunner

from loxmatter import cli
from loxmatter.auth.passwords import hash_password, verify_password
from loxmatter.cli import app, render_report
from loxmatter.diagnostics.logbuffer import LogBufferHandler
from loxmatter.matter import client as matter_client
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "nodes" / "example_light.json"


def load() -> NodeSnapshot:
    raw = json.loads(FIXTURE.read_text())
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_report_names_the_device():
    report = render_report(load())
    assert "IKEA of Sweden" in report
    assert "TRADFRI bulb" in report


def test_report_lists_attribute_and_event_signals():
    report = render_report(load())
    assert "1/6/0" in report
    assert "1/8/0" in report
    assert "1/59/0" in report  # Event aus der EventList
    assert "1/59/1" in report


def test_report_hides_global_attributes():
    assert "65531" not in render_report(load())


def test_report_flags_attributes_the_device_claimed_but_did_not_report():
    # AttributeList nennt 0 und 16, geliefert wurde nur 0.
    report = render_report(load())
    assert "NICHT GELIEFERT" in report
    assert "1/6/16" in report


def test_cli_reads_a_fixture_without_network():
    result = CliRunner().invoke(app, ["inspect", "--fixture", str(FIXTURE)])
    assert result.exit_code == 0
    assert "TRADFRI bulb" in result.stdout


def test_report_flags_unparsable_paths():
    snap = NodeSnapshot.from_raw(1, {"attributes": {"kaputt": 1, "1/6/0": True}})
    report = render_report(snap)
    assert "NICHT LESBAR" in report
    assert "kaputt" in report


def test_report_flags_clusters_with_undiscoverable_events():
    # Cluster 42 (OTA Requestor) hat mandatorische Events, aber weder eine
    # EventList noch einen Eintrag in FEATURE_MAP_EVENTS.
    snap = NodeSnapshot.from_raw(1, {"attributes": {"0/42/0": 1}})
    report = render_report(snap)
    assert "NICHT ABLEITBAR" in report
    assert "0/42" in report


def test_report_omits_undiscoverable_events_section_when_empty():
    # Switch (59) steht in FEATURE_MAP_EVENTS — nichts Unableitbares hier.
    snap = NodeSnapshot.from_raw(1, {"attributes": {"1/59/0": True}})
    assert "NICHT ABLEITBAR" not in render_report(snap)


class _FakeUpstream:
    """Attrappe für matter_server.client.MatterClient — offline, kein Socket.

    start_listening() bildet den echten Vertrag nach: Sie füllt den
    Node-Cache, meldet Bereitschaft über init_ready und blockiert danach, bis
    sie abgebrochen wird — siehe BridgeMatterClient.connect().
    """

    def __init__(
        self,
        nodes: list[Any] | None = None,
        connect_error: BaseException | None = None,
        never_ready: bool = False,
    ) -> None:
        self._nodes = nodes or []
        self._connect_error = connect_error
        self._never_ready = never_ready

    async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
        if self._connect_error is not None:
            raise self._connect_error
        if init_ready is not None and not self._never_ready:
            init_ready.set()
        try:
            await asyncio.Event().wait()  # blockiert, bis abgebrochen
        except asyncio.CancelledError:
            pass

    async def disconnect(self) -> None:
        pass

    def get_nodes(self) -> list[Any]:
        return self._nodes

    def subscribe_events(
        self,
        callback: Any,
        event_filter: Any = None,
        node_filter: Any = None,
        attr_path_filter: Any = None,
    ) -> Any:
        """Fuer `loxmatter run` (BridgeMatterClient.subscribe()) — die
        run()-Tests unten pruefen Aufbau/Abbau, nicht die Zustellung
        einzelner Aktualisierungen (das leistet tests/matter/test_client.py
        bereits ausfuehrlich)."""
        return lambda: None


class _FakeHttpSession:
    async def close(self) -> None:
        pass


def _fake_client(
    *,
    nodes: list[Any] | None = None,
    connect_error: BaseException | None = None,
    never_ready: bool = False,
) -> BridgeMatterClient:
    upstream = _FakeUpstream(nodes=nodes, connect_error=connect_error, never_ready=never_ready)
    return BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=_FakeHttpSession,
    )


def test_cli_reports_malformed_fixture_missing_node_id(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"attributes": {}}), encoding="utf-8")

    result = CliRunner().invoke(app, ["inspect", "--fixture", str(broken)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "node_id" in result.stderr


def test_cli_reports_fixture_that_is_not_valid_json(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not valid json", encoding="utf-8")

    result = CliRunner().invoke(app, ["inspect", "--fixture", str(broken)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "JSON" in result.stderr


def test_cli_reports_node_not_found(monkeypatch):
    monkeypatch.setattr(cli, "_build_client", lambda url: _fake_client(nodes=[]))

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://test/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "nicht bekannt" in result.stderr


def test_cli_reports_unreachable_server(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_build_client",
        lambda url: _fake_client(connect_error=CannotConnect("boom")),
    )

    result = CliRunner().invoke(
        app, ["inspect", "--node", "1", "--url", "ws://testhost.invalid:5580/ws"]
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "nicht erreichbar" in result.stderr


def test_cli_reports_connect_timeout_without_traceback(monkeypatch):
    # Der Server nimmt das Websocket an, meldet aber nie Bereitschaft — genau
    # der Fall, für den LISTENER_READY_TIMEOUT_SECONDS existiert. Klein
    # gepatcht, damit der Test nicht wirklich zehn Sekunden wartet.
    monkeypatch.setattr(matter_client, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cli, "_build_client", lambda url: _fake_client(never_ready=True))

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://test/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "keine Bereitschaft" in result.stderr
    # Von den beiden anderen Fehlerpfaden unterscheidbar:
    assert "nicht erreichbar" not in result.stderr
    assert "nicht bekannt" not in result.stderr


# --- loxmatter run: Aufbau/Abbau ohne Netz -----------------------------
#
# Was hier NICHT geprüft wird: die Zustellung einzelner Attribut-/Event-
# Aktualisierungen über subscribe() (siehe tests/matter/test_client.py) und
# das eigentliche HTTP-Verhalten von build_app() (siehe tests/loxone/). Hier
# geht es ausschließlich um _run()s eigene Verantwortung: startet es die
# vier Ressourcen, und — wichtiger — räumt es sie in jedem Fall wieder auf.


class _SpySender:
    """Steht für UdpSender — ohne echten Socket."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.close_calls = 0

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        self.close_calls += 1


class _SpyRuntime:
    """Steht für Runtime — erfüllt RuntimeEventHandler und zählt Aufrufe."""

    def __init__(self, store: Store, sender: _SpySender) -> None:
        self.store = store
        self.sender = sender
        self.started = False
        self.stop_calls = 0
        self.resend_calls = 0
        self.seed_calls = 0
        # Reihenfolge der beiden Aufrufe, damit ein Test pruefen kann, dass
        # das Saeen VOR dem ersten Resend passiert (siehe _run-Docstring):
        # ein Resend nach dem Saeen ist der ganze Witz von Spec 6.4, ein
        # Resend davor faende einen noch leeren Cache vor.
        self.call_order: list[str] = []

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        pass

    async def on_event(self, device_id: int, path: str) -> None:
        pass

    async def set_online(self, device_id: int, online: bool) -> None:
        pass

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stop_calls += 1

    async def seed_from_snapshot(self, snapshots: list[NodeSnapshot]) -> int:
        self.seed_calls += 1
        self.call_order.append("seed")
        return 0

    async def resend_all(self) -> int:
        self.resend_calls += 1
        self.call_order.append("resend")
        return 0


class _SpyUvicornServer:
    """serve() kehrt sofort zurück — wie uvicorn es nach einem ersten,
    geordnet abgefangenen Strg-C selbst tut (Server.capture_signals)."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def serve(self) -> None:
        return None


class _HangingUvicornServer:
    """serve() blockiert, bis der umgebende Task abgebrochen wird — wie bei
    echtem uvicorn, solange kein Signal eintrifft."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def serve(self) -> None:
        await asyncio.Event().wait()


class _FailingUvicornServer:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def serve(self) -> None:
        raise OSError("Adresse bereits verwendet")


def _install_run_spies(
    monkeypatch: pytest.MonkeyPatch, *, connect_error: BaseException | None = None
) -> tuple[list[_SpySender], list[_SpyRuntime], list[BridgeMatterClient]]:
    """Ersetzt Sender, Laufzeit und matter-Client durch Attrappen, damit
    _run() ohne Netzwerk/Hardware getestet werden kann. uvicorn.Server bleibt
    Sache des jeweiligen Tests (unterschiedliches Serve-Verhalten)."""
    senders: list[_SpySender] = []
    runtimes: list[_SpyRuntime] = []
    clients: list[BridgeMatterClient] = []

    def make_sender(host: str, port: int) -> _SpySender:
        sender = _SpySender(host, port)
        senders.append(sender)
        return sender

    def make_runtime(store: Store, sender: _SpySender) -> _SpyRuntime:
        runtime = _SpyRuntime(store, sender)
        runtimes.append(runtime)
        return runtime

    def make_client(url: str) -> BridgeMatterClient:
        client = _fake_client(nodes=[], connect_error=connect_error)
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "UdpSender", make_sender)
    monkeypatch.setattr(cli, "Runtime", make_runtime)
    monkeypatch.setattr(cli, "_build_client", make_client)
    return senders, runtimes, clients


def _assert_store_is_closed(store: Store) -> None:
    with pytest.raises(sqlite3.ProgrammingError):
        store.udp_port(1)


def _reset_loxmatter_logger(original_handlers: list[logging.Handler], original_level: int) -> None:
    """Stellt Handler-Liste und Stufe des Loggers `loxmatter` wieder her.

    Aus der Fixture unten ausgelagert, damit genau dieses Rueckbau-Verhalten
    fuer sich allein testbar ist, ohne pytests Fixture-Maschinerie
    verschachtelt anstossen zu muessen (siehe
    `test_reset_loxmatter_logger_removes_every_leaked_log_buffer_handler`
    unten - der Beleg, den die Nachbesserung zu Fix 1 verlangt)."""
    logger = logging.getLogger("loxmatter")
    logger.handlers[:] = original_handlers
    logger.setLevel(original_level)


@pytest.fixture(autouse=True)
def _restore_loxmatter_logger() -> Iterator[None]:
    """Setzt den Logger `loxmatter` nach jedem Test dieser Datei auf seinen
    vorherigen Zustand zurueck.

    Absichtlich HIER, nicht in `tests/conftest.py`: `install_log_buffer()`
    wird ausschliesslich von `run()` aufgerufen (siehe dessen Docstring, seit
    Nachbesserung Task 7, Fix 1 — vorher von `_run()`, gleiche Datei, gleiches
    Argument), und nur diese Datei ruft `run()`/`_run()` direkt auf — kein
    anderes Testmodul der Suite fasst den Logger `loxmatter` an. Eine globale,
    prozessweite Fixture wuerde denselben Rueckbau fuer alle ueber 600
    Tests der uebrigen Suite mitschleppen, die mit Logging nichts zu tun
    haben; als `autouse`-Fixture DIESER Datei greift sie nur dort, wo der
    Zustand ueberhaupt entstehen kann.

    Ohne das: jeder `run()`-Aufruf in dieser Datei haengt ueber
    `install_log_buffer()` einen neuen `LogBufferHandler` an den
    prozessweiten Logger `loxmatter` und setzt dessen Stufe auf `INFO` —
    und beides ueberlebt den einzelnen Test, weil `logging.getLogger(...)`
    denselben, modulweiten Logger liefert, gleich wie oft er aufgerufen
    wird. Gemessen (siehe Task-Bericht): nach dieser Datei allein blieben
    ohne Rueckbau fuenf verwaiste `LogBufferHandler` am Logger haengen, und
    seine Stufe stand dauerhaft auf `INFO` (20) statt auf `NOTSET` (0)."""
    logger = logging.getLogger("loxmatter")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    yield
    _reset_loxmatter_logger(original_handlers, original_level)


def test_reset_loxmatter_logger_removes_every_leaked_log_buffer_handler():
    """Beleg statt Behauptung fuer die autouse-Fixture oben (Nachbesserung
    Task 5, Fix 1): haengt ZWEI `LogBufferHandler` an den Logger `loxmatter`
    an - mehr, als ein einzelner `_run()`-Aufruf je anhaengen sollte, aber
    genau das Bild, das ein vergessenes Aufraeumen ueber mehrere Tests
    hinweg hinterlaesst - und prueft, dass `_reset_loxmatter_logger` (die
    von der Fixture nach jedem Test aufgerufene Rueckbau-Logik) danach
    GENAU KEINEN mehr uebrig laesst und die Stufe auf ihren Ausgangswert
    zurueckfaellt."""
    logger = logging.getLogger("loxmatter")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    assert not any(isinstance(h, LogBufferHandler) for h in original_handlers)

    cli.install_log_buffer()
    cli.install_log_buffer()
    assert (
        len([h for h in logger.handlers if isinstance(h, LogBufferHandler)]) == 2
    )  # der zu bereinigende Ausgangszustand
    assert logger.level == logging.INFO

    _reset_loxmatter_logger(original_handlers, original_level)

    assert logger.handlers == original_handlers
    assert not any(isinstance(h, LogBufferHandler) for h in logger.handlers)
    assert logger.level == original_level


async def test_run_stops_everything_after_a_clean_shutdown(monkeypatch, tmp_path):
    """uvicorn.Server.serve() kehrt nach einem ersten Strg-C geordnet
    zurück (siehe _run-Docstring) — dieser Test bildet genau das nach."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].started is True
    assert runtimes[0].resend_calls == 1
    assert runtimes[0].stop_calls == 1
    assert senders[0].close_calls == 1
    with pytest.raises(MatterUnavailableError):
        await clients[0].snapshots()
    _assert_store_is_closed(store)


async def test_run_seeds_the_runtime_before_the_first_resend(monkeypatch, tmp_path):
    """Live-Lauf vom 2026-09-02 (Spec 6.4): ohne ein Saeen aus dem aktuellen
    Geraetezustand VOR dem ersten `resend_all()` findet dieser Resend einen
    leeren Cache vor und sendet nichts."""
    _, runtimes, _ = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].seed_calls == 1
    assert runtimes[0].call_order == ["seed", "resend"]


def test_run_installs_the_log_buffer_before_the_password_warning(monkeypatch, tmp_path):
    """Nachbesserung Task 7, Fix 1: `install_log_buffer()` hing bis hierher
    in `_run()`, unmittelbar vor `uvicorn.Config(...)` - also NACH
    `client.connect()`, `subscribe()`, `runtime.start()`,
    `seed_from_snapshot()` und `resend_all()`, und vor allem NACH der
    Passwortwarnung aus `run()` (`_warn_if_no_password`), die synchron
    laeuft, BEVOR `_run()` ueberhaupt beginnt. Jede dieser Zeilen war damit
    weg, bevor der Ring existierte - allen voran der Sicherheitshinweis zum
    fehlenden Passwort, der genau fuer die Person gedacht ist, die vor der
    Ansicht statt einem Terminal sitzt (siehe cli.py, Docstring von `run()`).

    Dieser Test haelt eine frische, passwortlose Datenbank (Store-Vorgabe)
    und laesst `connect()` bewusst scheitern (CannotConnect, wie
    `test_run_prints_which_store_was_used` oben), damit er ohne Netz und
    ohne laufenden HTTP-Server durchlaeuft - die Passwortwarnung passiert
    lange vor diesem Fehlschlag. Der Beleg: NACH dem `run()`-Aufruf haengt
    genau EIN `LogBufferHandler` am Logger `loxmatter`, und dessen Ring
    enthaelt die Warnzeile - obwohl sie vor JEDEM der oben genannten
    Schritte entstanden ist."""
    _install_run_spies(monkeypatch, connect_error=CannotConnect("boom"))
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store_path = tmp_path / "run.sqlite"

    result = CliRunner().invoke(
        app, ["run", "--miniserver", "127.0.0.1", "--store-path", str(store_path)]
    )

    assert (
        result.exit_code != 0
    )  # CannotConnect -> _fail() -> Exit(1); nicht Gegenstand dieses Tests
    log_buffer_handlers = [
        h for h in logging.getLogger("loxmatter").handlers if isinstance(h, LogBufferHandler)
    ]
    assert len(log_buffer_handlers) == 1
    messages = [e.message for e in log_buffer_handlers[0].entries]
    assert any("noch kein Passwort vergeben" in message for message in messages)


def test_run_installs_the_log_buffer_exactly_once_and_passes_it_to__run(monkeypatch, tmp_path):
    """`run()` ruft `install_log_buffer()` seit der Nachbesserung (Task 7,
    Fix 1) an genau einer Stelle auf - als seine allererste Anweisung. Ein
    Spion um `install_log_buffer` UND ein Spion an Stelle von `_run` halten
    beides zugleich fest: den Aufrufzaehler UND dass GENAU der von
    `install_log_buffer()` gelieferte Handler bei `_run()` ankommt, nicht
    bloss irgendein `LogBufferHandler` (derselbe Fehler waere sonst
    unsichtbar geblieben, siehe `test_run_installs_the_log_buffer_before_
    the_password_warning` oben fuer die ausfuehrlichere Begruendung, warum
    die Zahl der Aufrufstellen zaehlt, nicht ihre Position)."""
    installed: list[LogBufferHandler] = []
    original_install = cli.install_log_buffer

    def spy_install(*args: Any, **kwargs: Any) -> LogBufferHandler:
        handler = original_install(*args, **kwargs)
        installed.append(handler)
        return handler

    received: dict[str, Any] = {}

    async def fake_run(
        store: Store,
        url: str,
        miniserver: str,
        port: int,
        listen: int,
        matter_data_dir: Path | None = None,
        host: str = "0.0.0.0",
        api_token: str | None = None,
        log_handler: LogBufferHandler | None = None,
    ) -> None:
        received["log_handler"] = log_handler

    monkeypatch.setattr(cli, "install_log_buffer", spy_install)
    monkeypatch.setattr(cli, "_run", fake_run)
    store_path = tmp_path / "run.sqlite"

    result = CliRunner().invoke(
        app, ["run", "--miniserver", "127.0.0.1", "--store-path", str(store_path)]
    )

    assert result.exit_code == 0, result.output
    assert len(installed) == 1
    assert received["log_handler"] is installed[0]


async def test__run_forwards_the_given_log_handler_to_build_app(monkeypatch, tmp_path):
    """`_run()` selbst installiert seit der Nachbesserung (Task 7, Fix 1)
    keinen `LogBufferHandler` mehr - das uebernimmt ausschliesslich `run()`,
    VOR dem Aufruf (siehe dessen Docstring). `_run()`s einzige verbleibende
    Verantwortung in dieser Sache: den erhaltenen Handler unveraendert an
    `build_app()` durchreichen, damit die Route `/api/diagnostics/live`
    ihren Log-Zweig bekommt."""
    _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    captured: dict[str, Any] = {}
    original_build_app = cli.build_app

    def spy_build_app(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return original_build_app(*args, **kwargs)

    monkeypatch.setattr(cli, "build_app", spy_build_app)
    store = Store(tmp_path / "t.sqlite")
    handler = cli.install_log_buffer()

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080, log_handler=handler)

    assert captured["log_handler"] is handler


async def test_run_cleans_up_when_matter_server_is_unreachable(monkeypatch, tmp_path):
    """Scheitert schon connect(), dürfen weder Runtime noch Sender noch die
    Datenbank offen bleiben — auch wenn runtime.start() nie lief."""
    senders, runtimes, _clients = _install_run_spies(
        monkeypatch, connect_error=CannotConnect("boom")
    )
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    with pytest.raises(typer.Exit):
        await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].started is False
    assert runtimes[0].stop_calls == 1  # sicher aufrufbar, auch ungestartet
    assert senders[0].close_calls == 1
    _assert_store_is_closed(store)


async def test_run_cleans_up_when_serve_raises(monkeypatch, tmp_path):
    """Ein Fehler beim Start des HTTP-Servers (z. B. Port belegt) darf
    Laufzeit, Sender, Client und Datenbank nicht offen lassen."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _FailingUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    with pytest.raises(OSError, match="Adresse"):
        await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].stop_calls == 1
    assert senders[0].close_calls == 1
    with pytest.raises(MatterUnavailableError):
        await clients[0].snapshots()
    _assert_store_is_closed(store)


async def test_run_cleans_up_on_cancellation(monkeypatch, tmp_path):
    """Simuliert Strg-C über eine echte Task-Cancellation: serve() hängt,
    bis der _run-Task abgebrochen wird — asyncio.run() installiert seit
    Python 3.11 selbst einen SIGINT-Handler, der genau das tut (siehe
    _run-Docstring)."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _HangingUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    task = asyncio.create_task(cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtimes[0].stop_calls == 1
    assert senders[0].close_calls == 1
    with pytest.raises(MatterUnavailableError):
        await clients[0].snapshots()
    _assert_store_is_closed(store)


async def test_run_continues_cleanup_when_one_step_fails(monkeypatch, tmp_path):
    """Scheitert ein Aufräumschritt (hier: runtime.stop()), müssen die
    folgenden trotzdem laufen — jeder Schritt steht in _run() in seinem
    eigenen try/except, genau dafür."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)

    def make_broken_runtime(store: Store, sender: _SpySender) -> _SpyRuntime:
        runtime = _SpyRuntime(store, sender)

        async def broken_stop() -> None:
            runtime.stop_calls += 1
            raise RuntimeError("Sendefehler beim letzten Full-Resend")

        runtime.stop = broken_stop  # type: ignore[method-assign]
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(cli, "Runtime", make_broken_runtime)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].stop_calls == 1
    assert senders[0].close_calls == 1  # trotz gescheitertem runtime.stop()
    with pytest.raises(MatterUnavailableError):
        await clients[0].snapshots()
    _assert_store_is_closed(store)


async def test_run_cleans_up_when_cancelled_during_startup(monkeypatch, tmp_path):
    """Bricht waehrend `resend_all()` ab - also VOR `serve()`, im Unterschied zu
    `test_run_cleans_up_on_cancellation` oben, das immer erst `serve()` erreicht
    (dessen 0.05s-Schlaf reicht laengst, bis connect()/subscribe()/start()/
    resend_all() der Attrappen durchgelaufen sind). Von den vier Schritten vor
    `serve()` ist `resend_all()` gezielt gewaehlt: es ist der einzige mit einem
    eigenen inneren `await` (hier bewusst auf ein nie gesetztes Event), an dem
    eine Cancellation ueberhaupt landen kann - die drei anderen Fake-Aufrufe
    kehren synchron zurueck und boeten keinen Interrupt-Punkt."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)

    def make_slow_runtime(store: Store, sender: _SpySender) -> _SpyRuntime:
        runtime = _SpyRuntime(store, sender)

        async def resend_all_blocks_until_cancelled() -> int:
            runtime.resend_calls += 1
            await asyncio.Event().wait()  # blockiert, bis abgebrochen
            return 0  # pragma: no cover - wird nie erreicht

        runtime.resend_all = resend_all_blocks_until_cancelled  # type: ignore[method-assign]
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(cli, "Runtime", make_slow_runtime)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    task = asyncio.create_task(cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # started() und der eine resend_all()-Aufruf sind gelaufen - serve() nie:
    # sonst wuerde dies nur test_run_cleans_up_on_cancellation wiederholen.
    assert runtimes[0].started is True
    assert runtimes[0].resend_calls == 1
    assert runtimes[0].stop_calls == 1
    assert senders[0].close_calls == 1
    with pytest.raises(MatterUnavailableError):
        await clients[0].snapshots()
    _assert_store_is_closed(store)


def test_run_prints_which_store_was_used(monkeypatch, tmp_path):
    """Review-Fix M10, 2026-09-02: `export` gab den verwendeten Store-Pfad
    schon aus, `run` bislang nicht — die wahrscheinlichste Fehlkonfiguration
    (exportiert mit `--store-path`, gestartet ohne, oder umgekehrt) zeigte
    sich sonst erst als 404 in einem Log, das niemand liest. Der Test laesst
    `connect()` bewusst scheitern (CannotConnect), damit er ohne Netz und
    ohne einen laufenden HTTP-Server durchläuft — die Ausgabe passiert schon
    vor diesem Fehlschlag."""
    _install_run_spies(monkeypatch, connect_error=CannotConnect("boom"))
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store_path = tmp_path / "run.sqlite"

    result = CliRunner().invoke(
        app, ["run", "--miniserver", "127.0.0.1", "--store-path", str(store_path)]
    )

    assert str(store_path) in result.stdout


# --- fake-miniserver: --template ----------------------------------------


def test_fake_miniserver_rejects_a_missing_template_before_listening(tmp_path):
    """Ein falscher --template-Pfad soll sofort scheitern, statt erst nach dem
    Warten auf Strg-C (Review-Fix Minor #5) - `CliRunner.invoke` haengt hier
    deshalb nicht: die Pruefung sitzt vor `asyncio.run(_fake_miniserver(...))`."""
    missing = tmp_path / "nicht_da.xml"

    result = CliRunner().invoke(app, ["fake-miniserver", "--template", str(missing)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "wurde nicht gefunden" in result.stderr


def test_silent_keys_report_distinguishes_nothing_to_check_from_all_seen():
    """Review-Fix Minor #4: eine Vorlage ohne Check-Attribute (z. B. eine
    VO_-Datei) hat nichts zu pruefen - das darf nicht wie "alles gesehen"
    aussehen, sonst liest es sich wie eine bestandene statt einer
    ausgebliebenen Pruefung."""
    nothing_to_check = cli._silent_keys_report("VO_x.xml", announced=set(), silent=[])
    assert "nichts zu prüfen" in nothing_to_check
    assert "Alle" not in nothing_to_check

    all_seen = cli._silent_keys_report("VIU_x.xml", announced={"a", "b"}, silent=[])
    assert "Alle 2 Signale" in all_seen

    some_silent = cli._silent_keys_report("VIU_x.xml", announced={"a", "b"}, silent=["b"])
    assert "1 Signale aus VIU_x.xml nie gesehen" in some_silent
    assert "  b" in some_silent


def test_set_password_writes_a_hash_and_clears_sessions(tmp_path):
    """Der Notausgang aus Spec 9: ein headless aufgesetzter Dienst mit
    vergessenem Passwort waere sonst endgueltig verloren."""
    path = tmp_path / "t.sqlite"
    store = Store(path)
    store.auth.set_password_hash(hash_password("altes-passwort"))
    store.auth.create_session("alte-sitzung", created_at=1, expires_at=2**31)
    store.close()

    result = CliRunner().invoke(
        app, ["set-password", "--store-path", str(path)], input="neues-passwort\nneues-passwort\n"
    )
    assert result.exit_code == 0

    store = Store(path)
    try:
        stored = store.auth.password_hash()
        assert stored is not None
        assert verify_password("neues-passwort", stored) is True
        # Wer das Passwort zuruecksetzt, will nicht, dass eine alte Sitzung
        # weiterlaeuft.
        assert store.auth.session_expires_at("alte-sitzung") is None
    finally:
        store.close()
    # Das Passwort selbst darf in keiner Ausgabe stehen.
    assert "neues-passwort" not in result.output


def test_set_password_rejects_a_short_password(tmp_path):
    path = tmp_path / "t.sqlite"
    Store(path).close()
    result = CliRunner().invoke(
        app, ["set-password", "--store-path", str(path)], input="kurz\nkurz\n"
    )
    assert result.exit_code != 0
    store = Store(path)
    try:
        assert store.auth.password_hash() is None
    finally:
        store.close()


def test_set_password_fails_loudly_instead_of_creating_a_new_database(tmp_path):
    """Notausgang-Fund (2026-09-03): `Store(...)` legt eine fehlende Datei
    kommentarlos neu an. Auf der Referenz-Installation liegt die eigentliche
    Datenbank aber in einem Docker-Volume, das auf dem Host unter diesem
    Pfad gar nicht sichtbar ist — ohne diese Pruefung traefe der Befehl dort
    eine leere Fremddatenbank, schriebe den Hash hinein und meldete Erfolg,
    waehrend die Bruecke unveraendert gesperrt bliebe. `set-password` setzt
    ein Passwort ZURUECK; eine neue Datenbank anzulegen ist in keinem seiner
    Anwendungsfaelle gewollt."""
    path = tmp_path / "kein-solches-volume" / "loxmatter.sqlite"
    result = CliRunner().invoke(
        app, ["set-password", "--store-path", str(path)], input="neues-passwort\nneues-passwort\n"
    )
    assert result.exit_code != 0
    assert not path.exists()
    assert not path.parent.exists()
