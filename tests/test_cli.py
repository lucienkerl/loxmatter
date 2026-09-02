import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import typer
from matter_server.client.exceptions import CannotConnect
from typer.testing import CliRunner

from loxmatter import cli
from loxmatter.cli import app, render_report
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

    async def resend_all(self) -> int:
        self.resend_calls += 1
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
