# loxmatter - connects Matter devices to a Loxone Miniserver.
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

from loxmatter import cli, i18n
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
    assert "1/59/0" in report  # event from the EventList
    assert "1/59/1" in report


def test_report_hides_global_attributes():
    assert "65531" not in render_report(load())


def test_report_flags_attributes_the_device_claimed_but_did_not_report():
    # AttributeList names 0 and 16, only 0 was delivered.
    report = render_report(load())
    assert "NOT DELIVERED" in report
    assert "1/6/16" in report


def test_report_flags_attributes_the_device_claimed_but_did_not_report_in_german():
    # AttributeList names 0 and 16, only 0 was delivered.
    i18n.set_language("de")
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
    assert "NOT READABLE" in report
    assert "kaputt" in report


def test_report_flags_unparsable_paths_in_german():
    i18n.set_language("de")
    snap = NodeSnapshot.from_raw(1, {"attributes": {"kaputt": 1, "1/6/0": True}})
    report = render_report(snap)
    assert "NICHT LESBAR" in report
    assert "kaputt" in report


def test_report_flags_clusters_with_undiscoverable_events():
    # Cluster 42 (OTA Requestor) has mandatory events, but neither an
    # EventList nor an entry in FEATURE_MAP_EVENTS.
    snap = NodeSnapshot.from_raw(1, {"attributes": {"0/42/0": 1}})
    report = render_report(snap)
    assert "NOT DERIVABLE" in report
    assert "0/42" in report


def test_report_flags_clusters_with_undiscoverable_events_in_german():
    # Cluster 42 (OTA Requestor) has mandatory events, but neither an
    # EventList nor an entry in FEATURE_MAP_EVENTS.
    i18n.set_language("de")
    snap = NodeSnapshot.from_raw(1, {"attributes": {"0/42/0": 1}})
    report = render_report(snap)
    assert "NICHT ABLEITBAR" in report
    assert "0/42" in report


def test_report_omits_undiscoverable_events_section_when_empty():
    # Switch (59) is in FEATURE_MAP_EVENTS - nothing non-derivable here.
    snap = NodeSnapshot.from_raw(1, {"attributes": {"1/59/0": True}})
    assert "NICHT ABLEITBAR" not in render_report(snap)


class _FakeUpstream:
    """Stand-in for matter_server.client.MatterClient - offline, no socket.

    start_listening() mirrors the real contract: it fills the
    node cache, reports readiness via init_ready and then blocks until
    it is cancelled - see BridgeMatterClient.connect().
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
            await asyncio.Event().wait()  # blocks until cancelled
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
        """For `loxmatter run` (BridgeMatterClient.subscribe()) - the
        run() tests below check setup/teardown, not the delivery of
        individual updates (tests/matter/test_client.py already
        does that extensively)."""
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
    assert "not known" in result.stderr  # cli.common.fail_node_unknown


def test_cli_reports_node_not_found_in_german(monkeypatch):
    i18n.set_language("de")
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
    assert "unreachable" in result.stderr  # cli.common.fail_matter_unreachable


def test_cli_reports_unreachable_server_in_german(monkeypatch):
    i18n.set_language("de")
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
    # The server accepts the websocket but never reports readiness - exactly
    # the case LISTENER_READY_TIMEOUT_SECONDS exists for. Patched small
    # so the test doesn't actually wait ten seconds.
    monkeypatch.setattr(matter_client, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cli, "_build_client", lambda url: _fake_client(never_ready=True))

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://test/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "did not report readiness" in result.stderr  # cli.common.fail_matter_not_ready
    # Distinguishable from the two other error paths:
    assert "unreachable" not in result.stderr
    assert "not known" not in result.stderr


def test_cli_reports_connect_timeout_without_traceback_in_german(monkeypatch):
    i18n.set_language("de")
    monkeypatch.setattr(matter_client, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cli, "_build_client", lambda url: _fake_client(never_ready=True))

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://test/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "keine Bereitschaft" in result.stderr
    # Distinguishable from the two other error paths:
    assert "nicht erreichbar" not in result.stderr
    assert "nicht bekannt" not in result.stderr


# --- loxmatter run: setup/teardown without a network -----------------------------
#
# What is NOT checked here: the delivery of individual attribute/event
# updates via subscribe() (see tests/matter/test_client.py) and
# the actual HTTP behavior of build_app() (see tests/loxone/). Here
# it's exclusively about _run()'s own responsibility: does it start the
# four resources, and - more importantly - does it clean them up again in every case.


class _SpySender:
    """Stands in for UdpSender - without a real socket."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.close_calls = 0

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        self.close_calls += 1


class _SpyRuntime:
    """Stands in for Runtime - satisfies RuntimeEventHandler and counts calls."""

    def __init__(self, store: Store, sender: _SpySender) -> None:
        self.store = store
        self.sender = sender
        self.started = False
        self.stop_calls = 0
        self.resend_calls = 0
        self.seed_calls = 0
        # Order of the two calls, so a test can verify that
        # the seeding happens BEFORE the first resend (see _run docstring):
        # a resend after the seeding is the whole point of Spec 6.4, a
        # resend before it would find a still-empty cache.
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
    """serve() returns immediately - as uvicorn itself does after a first,
    cleanly caught Ctrl-C (Server.capture_signals)."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def serve(self) -> None:
        return None


class _HangingUvicornServer:
    """serve() blocks until the surrounding task is cancelled - as with
    real uvicorn, as long as no signal arrives."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def serve(self) -> None:
        await asyncio.Event().wait()


class _FailingUvicornServer:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def serve(self) -> None:
        raise OSError("address already in use")


def _install_run_spies(
    monkeypatch: pytest.MonkeyPatch, *, connect_error: BaseException | None = None
) -> tuple[list[_SpySender], list[_SpyRuntime], list[BridgeMatterClient]]:
    """Replaces the sender, runtime, and matter client with stand-ins so
    _run() can be tested without network/hardware. uvicorn.Server remains
    each test's own concern (different serve behavior)."""
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
    """Restores the handler list and level of the `loxmatter` logger.

    Factored out of the fixture below so that exactly this teardown
    behavior is testable on its own, without having to trigger pytest's
    fixture machinery in a nested way (see
    `test_reset_loxmatter_logger_removes_every_leaked_log_buffer_handler`
    below - the proof the fix for Fix 1 calls for)."""
    logger = logging.getLogger("loxmatter")
    logger.handlers[:] = original_handlers
    logger.setLevel(original_level)


@pytest.fixture(autouse=True)
def _restore_loxmatter_logger() -> Iterator[None]:
    """Resets the `loxmatter` logger to its previous state after every
    test in this file.

    Deliberately HERE, not in `tests/conftest.py`: `install_log_buffer()`
    is called exclusively by `run()` (see its docstring, since the
    fix for Task 7, Fix 1 - previously by `_run()`, same file, same
    argument), and only this file calls `run()`/`_run()` directly - no
    other test module in the suite touches the `loxmatter` logger. A global,
    process-wide fixture would drag the same teardown along for all
    the other 600+ tests in the rest of the suite that have nothing to do
    with logging; as an `autouse` fixture of THIS file, it only kicks in
    where the state can arise at all.

    Without this: every `run()` call in this file attaches, via
    `install_log_buffer()`, a new `LogBufferHandler` to the
    process-wide `loxmatter` logger and sets its level to `INFO` -
    and both survive the individual test, because `logging.getLogger(...)`
    returns the same, module-wide logger no matter how often it is
    called. Measured (see task report): without teardown, this file alone
    left five orphaned `LogBufferHandler`s hanging off the logger, and
    its level stayed permanently at `INFO` (20) instead of `NOTSET` (0)."""
    logger = logging.getLogger("loxmatter")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    yield
    _reset_loxmatter_logger(original_handlers, original_level)


def test_reset_loxmatter_logger_removes_every_leaked_log_buffer_handler():
    """Proof instead of assertion for the autouse fixture above (fix for
    Task 5, Fix 1): attaches TWO `LogBufferHandler`s to the `loxmatter`
    logger - more than a single `_run()` call should ever attach, but
    exactly the picture a forgotten cleanup leaves behind across
    several tests - and checks that `_reset_loxmatter_logger` (the
    teardown logic the fixture calls after every test) leaves
    EXACTLY NONE behind afterward and the level falls back to its
    starting value."""
    logger = logging.getLogger("loxmatter")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    assert not any(isinstance(h, LogBufferHandler) for h in original_handlers)

    cli.install_log_buffer()
    cli.install_log_buffer()
    assert (
        len([h for h in logger.handlers if isinstance(h, LogBufferHandler)]) == 2
    )  # the starting state that needs cleaning up
    assert logger.level == logging.INFO

    _reset_loxmatter_logger(original_handlers, original_level)

    assert logger.handlers == original_handlers
    assert not any(isinstance(h, LogBufferHandler) for h in logger.handlers)
    assert logger.level == original_level


async def test_run_stops_everything_after_a_clean_shutdown(monkeypatch, tmp_path):
    """uvicorn.Server.serve() returns cleanly after a first Ctrl-C
    (see _run docstring) - this test reproduces exactly that."""
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
    """Live run from 2026-09-02 (Spec 6.4): without a seed from the current
    device state BEFORE the first `resend_all()`, that resend finds an
    empty cache and sends nothing."""
    _, runtimes, _ = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].seed_calls == 1
    assert runtimes[0].call_order == ["seed", "resend"]


def test_run_installs_the_log_buffer_before_the_password_warning(monkeypatch, tmp_path):
    """Fix for Task 7, Fix 1: `install_log_buffer()` used to hang, until now,
    in `_run()`, right before `uvicorn.Config(...)` - that is, AFTER
    `client.connect()`, `subscribe()`, `runtime.start()`,
    `seed_from_snapshot()` and `resend_all()`, and above all AFTER the
    password warning from `run()` (`_warn_if_no_password`), which runs
    synchronously BEFORE `_run()` even begins. Every one of these lines was
    therefore gone before the ring even existed - first and foremost the
    security notice about the missing password, which is meant precisely for
    the person sitting in front of the display instead of a terminal
    (see cli.py, docstring of `run()`).

    This test holds a fresh, passwordless database (Store default)
    and deliberately lets `connect()` fail (CannotConnect, like
    `test_run_prints_which_store_was_used` above), so it runs through
    without a network and without a running HTTP server - the password
    warning happens long before this failure. The proof: AFTER the
    `run()` call, exactly ONE `LogBufferHandler` is attached to the
    `loxmatter` logger, and its ring contains the warning line - even
    though it arose before EVERY one of the steps named above."""
    _install_run_spies(monkeypatch, connect_error=CannotConnect("boom"))
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store_path = tmp_path / "run.sqlite"

    result = CliRunner().invoke(
        app, ["run", "--miniserver", "127.0.0.1", "--store-path", str(store_path)]
    )

    assert (
        result.exit_code != 0
    )  # CannotConnect -> _fail() -> Exit(1); not the subject of this test
    log_buffer_handlers = [
        h for h in logging.getLogger("loxmatter").handlers if isinstance(h, LogBufferHandler)
    ]
    assert len(log_buffer_handlers) == 1
    messages = [e.message for e in log_buffer_handlers[0].entries]
    assert any(
        "No password has been set for this bridge yet" in message for message in messages
    )  # cli.run.warn_no_password


def test_run_installs_the_log_buffer_before_the_password_warning_in_german(monkeypatch, tmp_path):
    """German counterpart to `test_run_installs_the_log_buffer_before_the_
    password_warning` above - see there for the detailed reasoning."""
    i18n.set_language("de")
    _install_run_spies(monkeypatch, connect_error=CannotConnect("boom"))
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store_path = tmp_path / "run.sqlite"

    result = CliRunner().invoke(
        app, ["run", "--miniserver", "127.0.0.1", "--store-path", str(store_path)]
    )

    assert result.exit_code != 0
    log_buffer_handlers = [
        h for h in logging.getLogger("loxmatter").handlers if isinstance(h, LogBufferHandler)
    ]
    assert len(log_buffer_handlers) == 1
    messages = [e.message for e in log_buffer_handlers[0].entries]
    assert any("noch kein Passwort vergeben" in message for message in messages)


def test_run_installs_the_log_buffer_exactly_once_and_passes_it_to__run(monkeypatch, tmp_path):
    """`run()` has called `install_log_buffer()` at exactly one place -
    as its very first instruction - since the fix for Task 7, Fix 1. A
    spy around `install_log_buffer` AND a spy in place of `_run` capture
    both at once: the call counter AND that EXACTLY the handler
    delivered by `install_log_buffer()` arrives at `_run()`, not
    just any `LogBufferHandler` (the same bug would otherwise have
    stayed invisible, see `test_run_installs_the_log_buffer_before_
    the_password_warning` above for the more detailed reasoning why
    the number of call sites counts, not their position)."""
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
    """`_run()` itself no longer installs a `LogBufferHandler` since the
    fix for Task 7, Fix 1 - that is now handled exclusively by `run()`,
    BEFORE the call (see its docstring). `_run()`'s only remaining
    responsibility in this matter: pass the received handler through
    unchanged to `build_app()`, so the `/api/diagnostics/live` route
    gets its log branch."""
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
    """If connect() already fails, neither the runtime nor the sender nor
    the database may remain open - even if runtime.start() never ran."""
    senders, runtimes, _clients = _install_run_spies(
        monkeypatch, connect_error=CannotConnect("boom")
    )
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    with pytest.raises(typer.Exit):
        await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].started is False
    assert runtimes[0].stop_calls == 1  # safe to call, even when never started
    assert senders[0].close_calls == 1
    _assert_store_is_closed(store)


async def test_run_cleans_up_when_serve_raises(monkeypatch, tmp_path):
    """An error starting the HTTP server (e.g. port in use) must not
    leave the runtime, sender, client, or database open."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _FailingUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    with pytest.raises(OSError, match="address"):
        await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].stop_calls == 1
    assert senders[0].close_calls == 1
    with pytest.raises(MatterUnavailableError):
        await clients[0].snapshots()
    _assert_store_is_closed(store)


async def test_run_cleans_up_on_cancellation(monkeypatch, tmp_path):
    """Simulates Ctrl-C via a real task cancellation: serve() hangs
    until the _run task is cancelled - asyncio.run() has itself installed
    a SIGINT handler since Python 3.11 that does exactly that (see
    the _run docstring)."""
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
    """If a cleanup step fails (here: runtime.stop()), the following
    ones must still run - every step in _run() sits in its own
    try/except for exactly that reason."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)

    def make_broken_runtime(store: Store, sender: _SpySender) -> _SpyRuntime:
        runtime = _SpyRuntime(store, sender)

        async def broken_stop() -> None:
            runtime.stop_calls += 1
            raise RuntimeError("Send error during the last full resend")

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
    """Aborts while inside `resend_all()` - that is, BEFORE `serve()`, unlike
    `test_run_cleans_up_on_cancellation` above, which always reaches `serve()`
    first (its 0.05s sleep is more than enough for connect()/subscribe()/start()/
    resend_all() of the stand-ins to run through). Of the four steps before
    `serve()`, `resend_all()` is deliberately chosen: it is the only one with
    its own inner `await` (here deliberately on an event that is never set)
    where a cancellation can land at all - the three other fake calls
    return synchronously and would offer no interrupt point."""
    senders, runtimes, clients = _install_run_spies(monkeypatch)

    def make_slow_runtime(store: Store, sender: _SpySender) -> _SpyRuntime:
        runtime = _SpyRuntime(store, sender)

        async def resend_all_blocks_until_cancelled() -> int:
            runtime.resend_calls += 1
            await asyncio.Event().wait()  # blockiert, bis abgebrochen
            return 0  # pragma: no cover - never reached

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

    # started() and the one resend_all() call ran - serve() never did:
    # otherwise this would only repeat test_run_cleans_up_on_cancellation.
    assert runtimes[0].started is True
    assert runtimes[0].resend_calls == 1
    assert runtimes[0].stop_calls == 1
    assert senders[0].close_calls == 1
    with pytest.raises(MatterUnavailableError):
        await clients[0].snapshots()
    _assert_store_is_closed(store)


def test_run_prints_which_store_was_used(monkeypatch, tmp_path):
    """Review-Fix M10, 2026-09-02: `export` already printed the store path
    used, `run` did not so far - the most likely misconfiguration
    (exported with `--store-path`, started without, or vice versa) would
    otherwise only show up as a 404 in a log nobody reads. The test
    deliberately lets `connect()` fail (CannotConnect), so it runs through
    without a network and without a running HTTP server - the output
    happens well before this failure."""
    _install_run_spies(monkeypatch, connect_error=CannotConnect("boom"))
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store_path = tmp_path / "run.sqlite"

    result = CliRunner().invoke(
        app, ["run", "--miniserver", "127.0.0.1", "--store-path", str(store_path)]
    )

    assert str(store_path) in result.stdout


# --- fake-miniserver: --template ----------------------------------------


def test_fake_miniserver_rejects_a_missing_template_before_listening(tmp_path):
    """A wrong --template path is meant to fail immediately, instead of only
    after waiting for Ctrl-C (Review-Fix Minor #5) - `CliRunner.invoke`
    therefore does not hang here: the check sits before `asyncio.run(_fake_miniserver(...))`."""
    missing = tmp_path / "missing.xml"

    result = CliRunner().invoke(app, ["fake-miniserver", "--template", str(missing)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "was not found" in result.stderr  # cli.fake_miniserver.fail_template_not_found


def test_fake_miniserver_rejects_a_missing_template_before_listening_in_german(tmp_path):
    """German counterpart to
    `test_fake_miniserver_rejects_a_missing_template_before_listening` above."""
    i18n.set_language("de")
    missing = tmp_path / "missing.xml"

    result = CliRunner().invoke(app, ["fake-miniserver", "--template", str(missing)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "wurde nicht gefunden" in result.stderr


def test_silent_keys_report_distinguishes_nothing_to_check_from_all_seen():
    """Review-Fix Minor #4: a template without check attributes (e.g. a
    VO_ file) has nothing to check - that must not look like "everything
    seen", otherwise it reads like a passed check instead of a
    check that never happened."""
    nothing_to_check = cli._silent_keys_report("VO_x.xml", announced=set(), silent=[])
    assert "nothing to check" in nothing_to_check  # cli.fake_miniserver.report_no_check_signals
    assert "All" not in nothing_to_check

    all_seen = cli._silent_keys_report("VIU_x.xml", announced={"a", "b"}, silent=[])
    assert "All 2 signals" in all_seen  # cli.fake_miniserver.report_all_seen

    some_silent = cli._silent_keys_report("VIU_x.xml", announced={"a", "b"}, silent=["b"])
    assert (
        "1 signals from VIU_x.xml never seen" in some_silent
    )  # cli.fake_miniserver.report_silent_header
    assert "  b" in some_silent


def test_silent_keys_report_distinguishes_nothing_to_check_from_all_seen_in_german():
    """German counterpart to
    `test_silent_keys_report_distinguishes_nothing_to_check_from_all_seen` above."""
    i18n.set_language("de")
    nothing_to_check = cli._silent_keys_report("VO_x.xml", announced=set(), silent=[])
    assert "nichts zu prüfen" in nothing_to_check
    assert "Alle" not in nothing_to_check

    all_seen = cli._silent_keys_report("VIU_x.xml", announced={"a", "b"}, silent=[])
    assert "Alle 2 Signale" in all_seen

    some_silent = cli._silent_keys_report("VIU_x.xml", announced={"a", "b"}, silent=["b"])
    assert "1 Signale aus VIU_x.xml nie gesehen" in some_silent
    assert "  b" in some_silent


def test_set_password_writes_a_hash_and_clears_sessions(tmp_path):
    """The emergency exit from Spec 9: a headlessly set up service with
    a forgotten password would otherwise be permanently lost."""
    path = tmp_path / "t.sqlite"
    store = Store(path)
    store.auth.set_password_hash(hash_password("old-password"))
    store.auth.create_session("old-session", created_at=1, expires_at=2**31)
    store.close()

    result = CliRunner().invoke(
        app, ["set-password", "--store-path", str(path)], input="new-password\nnew-password\n"
    )
    assert result.exit_code == 0

    store = Store(path)
    try:
        stored = store.auth.password_hash()
        assert stored is not None
        assert verify_password("new-password", stored) is True
        # Whoever resets the password doesn't want an old session to
        # keep running.
        assert store.auth.session_expires_at("old-session") is None
    finally:
        store.close()
    # The password itself must not appear in any output.
    assert "new-password" not in result.output


def test_set_password_rejects_a_short_password(tmp_path):
    path = tmp_path / "t.sqlite"
    Store(path).close()
    result = CliRunner().invoke(
        app, ["set-password", "--store-path", str(path)], input="short\nshort\n"
    )
    assert result.exit_code != 0
    store = Store(path)
    try:
        assert store.auth.password_hash() is None
    finally:
        store.close()


def test_set_password_fails_loudly_instead_of_creating_a_new_database(tmp_path):
    """Emergency-exit finding (2026-09-03): `Store(...)` silently creates a
    missing file anew. On the reference installation, though, the actual
    database sits in a Docker volume that isn't visible at all under this
    path on the host - without this check, the command would there hit
    an empty, unrelated database, write the hash into it, and report success,
    while the bridge stayed locked, unchanged. `set-password` RESETS
    a password; creating a new database is not intended in any of its
    use cases."""
    path = tmp_path / "no-such-volume" / "loxmatter.sqlite"
    result = CliRunner().invoke(
        app, ["set-password", "--store-path", str(path)], input="new-password\nnew-password\n"
    )
    assert result.exit_code != 0
    assert not path.exists()
    assert not path.parent.exists()
