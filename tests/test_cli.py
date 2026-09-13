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
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import typer
from matter_server.client.exceptions import CannotConnect
from typer.testing import CliRunner

from loxmatter import cli, i18n
from loxmatter.auth.passwords import hash_password, verify_password
from loxmatter.cli import app, render_report
from loxmatter.diagnostics.logbuffer import LogBufferHandler
from loxmatter.loxone.runtime import HEARTBEAT_KEY, Runtime
from loxmatter.matter import client as matter_client
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import ZigbeeRadioSettings
from loxmatter.sources.supervisor import supervise
from loxmatter.zigbee import runtime as zigbee_runtime_module
from loxmatter.zigbee.source import ZigbeeSource, ZigbeeUnavailableError

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

    def __init__(
        self, store: Store, sender: _SpySender, *, link_ok: Callable[[], bool] = lambda: True
    ) -> None:
        self.store = store
        self.sender = sender
        # Held on to like store/sender above, for the same reason: a test
        # might later want to prove WHAT cli.serve() passed as link_ok (see
        # cli.py: `sources.all_connected`), instead of just accepting the
        # keyword and throwing it away.
        self.link_ok = link_ok
        self.started = False
        self.stop_calls = 0
        self.resend_calls = 0
        self.seed_calls = 0
        # Order of the two calls, so a test can verify that
        # the seeding happens BEFORE the first resend (see _run docstring):
        # a resend after the seeding is the whole point of Spec 6.4, a
        # resend before it would find a still-empty cache.
        self.call_order: list[str] = []
        # The optional radio's own signal (design 2026-09-12, section 4.9).
        # Seeded without sending before `attach()`, then written by the
        # source's own `on_connection_change` hook.
        self.zigbee_cached: list[bool] = []
        self.zigbee_sent: list[bool] = []

    def cache_zigbee_connected(self, connected: bool) -> None:
        self.zigbee_cached.append(connected)
        self.call_order.append("zigbee-cache")

    async def set_zigbee_connected(self, connected: bool) -> None:
        self.zigbee_sent.append(connected)

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


class _YieldingUvicornServer:
    """Like `_SpyUvicornServer` (serve() returns on its own), but yields to
    the event loop exactly once beforehand.

    Needed for everything concerning the supervisor task: between
    `asyncio.ensure_future(supervise(...))` and the `finally` in `_run()`
    there is otherwise not a single suspension point. The task would be
    cancelled before its FIRST step, so `supervise()` would never start up -
    and a test would see "cancelled" even if the supervisor had never been
    started at all. That one yield lets it get going."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def serve(self) -> None:
        await asyncio.sleep(0)


class _SpySupervisor:
    """Stands in for `sources.supervisor.supervise` - records WITH WHAT the
    supervisor was started, and then blocks like the original.

    The blocking is not incidental: the real `supervise()` never returns on
    its own (endless loop, see its docstring). A stand-in that returned
    immediately would long be finished by cleanup time, and the test could
    no longer tell whether `_run()` cancels the task or whether it had
    already ended anyway."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any, Any]] = []
        # Its own task, fetched from the inside: only through it can a test
        # check `task.cancelled()` - `_run()` holds `supervisor_task` in a
        # local variable and never hands it out anywhere.
        self.task: asyncio.Task[None] | None = None

    async def __call__(self, client: Any, store: Any, runtime: Any) -> None:
        self.calls.append((client, store, runtime))
        self.task = asyncio.current_task()
        await asyncio.Event().wait()


def _install_run_spies(
    monkeypatch: pytest.MonkeyPatch, *, connect_error: BaseException | None = None
) -> tuple[list[_SpySender], list[_SpyRuntime], list[BridgeMatterClient], _SpySupervisor]:
    """Replaces the sender, runtime, matter client and connection supervisor
    with stand-ins so _run() can be tested without network/hardware.
    uvicorn.Server remains each test's own concern (different serve
    behavior)."""
    senders: list[_SpySender] = []
    runtimes: list[_SpyRuntime] = []
    clients: list[BridgeMatterClient] = []
    supervisor = _SpySupervisor()

    def make_sender(host: str, port: int) -> _SpySender:
        sender = _SpySender(host, port)
        senders.append(sender)
        return sender

    def make_runtime(
        store: Store, sender: _SpySender, *, link_ok: Callable[[], bool]
    ) -> _SpyRuntime:
        # Taken as a named parameter instead of **kwargs and passed on to the
        # stand-in: should cli.serve() ever stop passing `link_ok`, a TypeError
        # falls here instead of a silently green test.
        runtime = _SpyRuntime(store, sender, link_ok=link_ok)
        runtimes.append(runtime)
        return runtime

    def make_client(url: str) -> BridgeMatterClient:
        client = _fake_client(nodes=[], connect_error=connect_error)
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "UdpSender", make_sender)
    monkeypatch.setattr(cli, "Runtime", make_runtime)
    monkeypatch.setattr(cli, "_build_client", make_client)
    # The real supervisor would wait endlessly in every one of these tests for
    # a link loss that never comes - hence replaced here as well, and not in a
    # second layer of stand-ins next to it.
    monkeypatch.setattr(cli, "supervise", supervisor)

    # The one OTBR read `build_zigbee_source` makes, per build. Left alone
    # it would open a real aiohttp session against 127.0.0.1:8081 from
    # every `_run` test that configures a stick - a network call in a unit
    # test, whose answer depends on whether anything happens to be
    # listening on the machine running the suite.
    async def no_border_router(*args: Any, **kwargs: Any) -> int | None:
        return None

    monkeypatch.setattr(zigbee_runtime_module, "current_thread_channel", no_border_router)
    return senders, runtimes, clients, supervisor


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
    senders, runtimes, clients, _supervisor = _install_run_spies(monkeypatch)
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
    _, runtimes, _, _ = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert runtimes[0].seed_calls == 1
    assert runtimes[0].call_order == ["seed", "resend"]


async def test_run_starts_the_supervisor_with_the_same_client_store_and_runtime(
    monkeypatch, tmp_path
):
    """The one line the outage of 8 September 2026 is about:
    `asyncio.ensure_future(supervise(client, store, runtime))` in `_run()`.

    Without it nobody notices that the websocket to matter-server has died,
    and nothing rebuilds it - exactly the state of that evening. Until this
    test the line was unchecked: whoever deleted it got a green suite.

    What is checked is not only THAT, but WITH WHAT: the supervisor must get
    the same three objects the rest of the service works with. Were it given
    a second client, that one would indeed reconnect, but the runtime and the
    HTTP layer would still hang on the dead one."""
    _, runtimes, clients, supervisor = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _HangingUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    task = asyncio.create_task(cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert supervisor.calls == [(clients[0], store, runtimes[0])]


async def test_run_cancels_the_supervisor_after_a_clean_shutdown(monkeypatch, tmp_path):
    """The supervisor waits endlessly - if it keeps running after the
    shutdown, it holds `client` and `store` alive that `_run()` has just
    closed, and the next rebuild attempt would run against a closed
    database.

    `_YieldingUvicornServer` instead of `_SpyUvicornServer`: see there for
    why a test without that one yield would see "cancelled" even if the
    supervisor had never started up. That is exactly why the first assertion
    below comes first."""
    _, _, _, supervisor = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _YieldingUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert len(supervisor.calls) == 1  # it really did start up
    assert supervisor.task is not None
    assert supervisor.task.cancelled() is True


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


def test_run_writes_the_bridges_own_log_to_stderr_for_the_container_log(monkeypatch, tmp_path):
    """`docker logs` reads the process's stderr, and nothing of the
    bridge's own reached it: the ring the System tab shows was the only
    handler on the `loxmatter` logger, which also keeps Python's last-resort
    output away. A Zigbee stick that would not open, the firmware line the
    hardware checklist asks for and the password warning below were all
    invisible there, and the ring forgets after 500 lines.

    Exactly one stream handler, from `run` alone: the password warning,
    logged before `_run` even starts, has to arrive on stderr once, in
    uvicorn's `LEVEL:` shape with the logger's name.

    Fault to prove it: drop `_install_stderr_log()` from `run` (nothing on
    stderr), or call it twice (the line arrives twice)."""
    _install_run_spies(monkeypatch, connect_error=CannotConnect("boom"))
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)

    result = CliRunner().invoke(
        app, ["run", "--miniserver", "127.0.0.1", "--store-path", str(tmp_path / "run.sqlite")]
    )

    lines = [
        line
        for line in result.stderr.splitlines()
        if "No password has been set for this bridge yet" in line
    ]
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("WARNING:")
    assert "loxmatter.cli:" in lines[0]


def test_subcommands_other_than_run_print_no_log_lines(monkeypatch, tmp_path):
    """The stream handler belongs to the server. A subcommand a person
    types - here `set-language` - must not start echoing log lines into
    that person's terminal, and must not leave a handler on the process-wide
    logger behind it.

    Fault to prove it: install the stream handler at import time or in the
    Typer callback instead of in `run`."""
    store_path = tmp_path / "t.sqlite"
    Store(store_path).close()
    logger = logging.getLogger("loxmatter")
    before = list(logger.handlers)

    result = CliRunner().invoke(app, ["set-language", "de", "--store-path", str(store_path)])

    assert result.exit_code == 0, result.output
    assert logger.handlers == before
    assert not any(isinstance(h, logging.StreamHandler) for h in logger.handlers)


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
        update_dir: Path = Path("/data/update"),
        zigbee_device: str | None = None,
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
    senders, runtimes, _clients, _supervisor = _install_run_spies(
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
    senders, runtimes, clients, _supervisor = _install_run_spies(monkeypatch)
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
    senders, runtimes, clients, _supervisor = _install_run_spies(monkeypatch)
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
    senders, runtimes, clients, _supervisor = _install_run_spies(monkeypatch)

    def make_broken_runtime(
        store: Store, sender: _SpySender, *, link_ok: Callable[[], bool]
    ) -> _SpyRuntime:
        runtime = _SpyRuntime(store, sender, link_ok=link_ok)

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
    senders, runtimes, clients, _supervisor = _install_run_spies(monkeypatch)

    def make_slow_runtime(
        store: Store, sender: _SpySender, *, link_ok: Callable[[], bool]
    ) -> _SpyRuntime:
        runtime = _SpyRuntime(store, sender, link_ok=link_ok)

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


# --- loxmatter run: the optional Zigbee radio -----------------------------------
#
# Matter is the MANDATORY source and Zigbee is not, and almost everything
# below is one consequence of that sentence: the watchdog covers the
# mandatory one, a radio that never comes up is logged rather than fatal,
# nothing on the startup path waits for it, and an installation without a
# stick pays nothing at all.

ZIGBEE_PATH = "/dev/serial/by-id/usb-SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2-if00"


class _NeverConnectingZigbeeSource:
    """A Zigbee source whose radio never comes up.

    Satisfies `DeviceSource` as far as `_run` and `supervise` reach into it,
    and nothing more. `connect()` raises the error a missing or wedged stick
    really produces (`ZigbeeUnavailableError`, which is deliberately NOT a
    `CannotConnect` and NOT a `DeviceUnreachableError`), and
    `wait_for_link_loss()` returns at once, which is the contract that puts
    the supervisor straight into its backoff loop for a source that was
    never connected."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.subscribe_calls = 0

    @property
    def technology(self) -> str:
        return "zigbee"

    @property
    def connected(self) -> bool:
        return False

    async def connect(self) -> None:
        self.connect_calls += 1
        raise ZigbeeUnavailableError("the Zigbee stick could not be found")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def wait_for_link_loss(self) -> None:
        return None

    async def subscribe(self, resolve_device_id: Any, handler: Any) -> None:
        self.subscribe_calls += 1

    async def snapshots(self) -> list[NodeSnapshot]:
        return []

    async def send(self, call: Any) -> None:
        raise AssertionError("nothing should be sent over a radio that never came up")

    async def remove(self, address: str) -> None:
        raise AssertionError("nothing should be removed over a radio that never came up")


class _SlowToConnectZigbeeSource(_NeverConnectingZigbeeSource):
    """A radio whose `connect()` takes as long as a Raspberry Pi's quirks
    warm-up does - 9-15 s there, unbounded here.

    `ZigbeeSource.connect()` opens by awaiting `ensure_quirks_loaded()`, so
    a startup path that waited on `connect()` in any shape - inline, or on a
    task it awaits - would hold `/health` and the whole web UI for the
    duration of that warm-up, on every single start. The updater's own
    health wait reads that as a failed update."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.warm_up_finished = asyncio.Event()
        self.connect_started = asyncio.Event()

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connect_started.set()
        await self.warm_up_finished.wait()


class _CapturingUvicornServer:
    """`_SpyUvicornServer` that remembers the config it was handed, so a
    test can assert `_run` REACHED `uvicorn.Config` rather than only that it
    returned."""

    configs: ClassVar[list[Any]] = []

    def __init__(self, config: Any) -> None:
        self.config = config
        _CapturingUvicornServer.configs.append(config)

    async def serve(self) -> None:
        # One yield, so a supervisor task started a few lines earlier in
        # `_run` gets its first turn - see `_YieldingUvicornServer`.
        await asyncio.sleep(0)


def _capture_build_app(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Records what `_run` handed `build_app`, the real one included.

    `sources` is the only place the source registry `_run` built is visible
    from the outside - `zigbee` itself is a local variable."""
    captured: dict[str, Any] = {}
    real = cli.build_app

    def spy(store: Any, invoke: Any, runtime: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        captured["runtime"] = runtime
        return real(store, invoke, runtime, **kwargs)

    monkeypatch.setattr(cli, "build_app", spy)
    return captured


async def test_the_heartbeat_keeps_pulsing_when_only_zigbee_is_down(monkeypatch, tmp_path):
    """Boundary design open point 9.1. The Loxone watchdog means "the bridge
    and the MANDATORY source are alive" - if it went quiet because a USB
    stick was unplugged, the Miniserver would treat every Matter device as
    dead too, and a user with no Zigbee devices at all could lose their whole
    installation to a radio they never used.

    Fault to prove it: pass `sources.all_connected` as `link_ok`. The
    heartbeat below then falls silent while the Matter link is perfectly
    healthy."""
    _, runtimes, clients, _supervisor = _install_run_spies(monkeypatch)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    # The bridge is asked while it is SERVING - after the shutdown the Matter
    # client is disconnected too, and every `link_ok` would answer `False`.
    monkeypatch.setattr(cli.uvicorn, "Server", _HangingUvicornServer)
    captured = _capture_build_app(monkeypatch)
    store = Store(tmp_path / "t.sqlite")

    task = asyncio.create_task(
        cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080, zigbee_device=ZIGBEE_PATH)
    )
    await asyncio.sleep(0)
    while not runtimes or not runtimes[0].started:
        await asyncio.sleep(0)

    try:
        link_ok = runtimes[0].link_ok
        assert clients[0].connected is True, "the Matter link is what this test holds fixed"
        assert captured["sources"].get("zigbee").connected is False
        assert link_ok() is True

        # Not merely the predicate: the heartbeat it actually drives. A real
        # `Runtime` with that same `link_ok` must put `bridge_alive` on the
        # wire while the Zigbee stick is missing.
        sender = _RecordingSender()
        heartbeat_store = Store(tmp_path / "heartbeat.sqlite")
        runtime = Runtime(heartbeat_store, sender, heartbeat_seconds=0.01, link_ok=link_ok)
        await runtime.start()
        await asyncio.sleep(0.05)
        await runtime.stop()
        heartbeat_store.close()

        assert HEARTBEAT_KEY in [key for key, _value in sender.sent]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self.sent.append((key, value))
        return True

    async def close(self) -> None:
        return None


async def test_the_heartbeat_goes_quiet_when_matter_is_down(monkeypatch, tmp_path):
    """The sibling `test_the_heartbeat_keeps_pulsing_when_only_zigbee_is_down`
    pins only half of what the heartbeat's wiring has to get right. Nothing
    at the `cli._run` level pinned the other half: `link_ok=lambda: True`
    would pass every test that existed here before this one, because none of
    them ever made the Matter link go down while `_run` kept serving - the
    predicate itself is only exercised elsewhere, at `Runtime`, against an
    injected stand-in.

    Fault to prove it: wire `link_ok=lambda: True` (or anything else that
    ignores `client.connected`) in `cli._run`. `link_ok()` below then still
    answers `True` with the Matter listener already dead, and the heartbeat a
    real `Runtime` drives from it never falls silent."""
    _, runtimes, clients, _supervisor = _install_run_spies(monkeypatch)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    # As in the sibling test: the bridge is asked while it is SERVING, not
    # while it is shutting down - `_HangingUvicornServer` keeps `_run` inside
    # `serve()` for the whole test, so `finally` never runs and never
    # disconnects the Matter client for reasons unrelated to the fault.
    monkeypatch.setattr(cli.uvicorn, "Server", _HangingUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    task = asyncio.create_task(
        cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080, zigbee_device=ZIGBEE_PATH)
    )
    await asyncio.sleep(0)
    while not runtimes or not runtimes[0].started:
        await asyncio.sleep(0)

    try:
        link_ok = runtimes[0].link_ok
        assert link_ok() is True, "the Matter link is up before this test kills it"

        # Matter goes down WHILE `_run` keeps serving. `BridgeMatterClient
        # .connected`'s own docstring: "once it [the listener task] has
        # ended, the connection is gone, no matter what `_upstream` still
        # holds" - so ending the listener task, without touching `_upstream`
        # at all, is what a real dropped connection looks like from here.
        client = clients[0]
        assert client._listener_task is not None
        client._listener_task.cancel()
        while not client._listener_task.done():
            await asyncio.sleep(0)

        assert client.connected is False
        assert link_ok() is False

        # Not merely the predicate: the heartbeat it actually drives. A real
        # `Runtime` with that same `link_ok` must NOT put `bridge_alive` on
        # the wire while the mandatory source is down.
        sender = _RecordingSender()
        heartbeat_store = Store(tmp_path / "heartbeat.sqlite")
        runtime = Runtime(heartbeat_store, sender, heartbeat_seconds=0.01, link_ok=link_ok)
        await runtime.start()
        await asyncio.sleep(0.05)
        await runtime.stop()
        heartbeat_store.close()

        assert HEARTBEAT_KEY not in [key for key, _value in sender.sent]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_zigbee_radio_that_will_not_come_up_does_not_stop_the_bridge(
    monkeypatch, tmp_path, caplog
):
    """Matter is mandatory and Zigbee is not. `client.connect()` failing
    still ends startup; a Zigbee radio that never comes up does not, and
    `cli._run` does not connect it at all - the supervisor does, retrying
    forever on its own 1 s -> 60 s backoff, and `supervise()` is the thing
    that must turn a raised `ZigbeeUnavailableError` into a logged warning
    plus another attempt rather than into a dead task.

    Fault to prove it: narrow `supervise()`'s inner `except Exception` to
    `except CannotConnect` (or delete the inner `try` entirely). The
    supervisor task then dies on the first `ZigbeeUnavailableError`, nothing
    ever retries the radio, and a stick plugged in five minutes later is
    never found - while `_run` itself still serves happily, which is exactly
    why asserting only that "the bridge runs on" would keep passing."""
    _install_run_spies(monkeypatch)
    # The REAL supervisor, deliberately: this test is about what it does
    # with an exception, so a stand-in would be the one thing that cannot
    # answer the question.
    monkeypatch.setattr(cli, "supervise", supervise)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    _CapturingUvicornServer.configs = []
    monkeypatch.setattr(cli.uvicorn, "Server", _CapturingUvicornServer)
    captured = _capture_build_app(monkeypatch)
    store = Store(tmp_path / "t.sqlite")

    with caplog.at_level(logging.WARNING, logger="loxmatter.sources.supervisor"):
        await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080, zigbee_device=ZIGBEE_PATH)

    zigbee = captured["sources"].get("zigbee")
    assert zigbee.connect_calls >= 1, "the supervisor never even tried the radio"
    assert any(
        "rebuild of source zigbee failed" in record.getMessage()
        and "next attempt in 1 s" in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]
    # And the bridge served regardless.
    assert len(_CapturingUvicornServer.configs) == 1


async def test_the_quirks_warm_up_does_not_delay_the_web_ui(monkeypatch, tmp_path):
    """`cli._run` starts uvicorn only AFTER `attach`, and the warm-up is an
    estimated 9-15 s on a Pi 4. Held anywhere on that path, `/health` and the
    web UI would be unreachable for the whole of it, every start - which the
    updater's own health wait would read as a failed update.

    What this proves, now that `cli._run` connects nothing itself: the
    startup path contains NO wait on `ZigbeeSource.connect()` - neither an
    inline `await`, nor an `await` on a supervisor task, nor any other shape.
    The only caller of `connect()` is `supervise()`, started with
    `ensure_future` and never awaited before `uvicorn.Config`, so a Raspberry
    Pi's warm-up runs entirely beside a web UI that is already answering.

    Fault to prove it: put `await zigbee.connect()` into `cli._run` just
    before `attach` - the shape an earlier draft had, and the one thing that
    could plausibly be reintroduced. `connect()` begins by awaiting
    `ensure_quirks_loaded()`, so awaiting it reproduces the exact delay this
    test exists to catch. The warm-up below never finishes, so `_run` either
    reaches `uvicorn.Config` beside it - or never returns at all, and the
    bound turns that into a failure rather than a hung suite."""
    _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli, "supervise", supervise)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _SlowToConnectZigbeeSource)
    _CapturingUvicornServer.configs = []
    monkeypatch.setattr(cli.uvicorn, "Server", _CapturingUvicornServer)
    captured = _capture_build_app(monkeypatch)
    store = Store(tmp_path / "t.sqlite")

    await asyncio.wait_for(
        cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080, zigbee_device=ZIGBEE_PATH), 5
    )

    zigbee = captured["sources"].get("zigbee")
    # The warm-up really was under way and really did not finish - without
    # both halves this would pass for a source nobody ever connected.
    assert zigbee.connect_started.is_set()
    assert not zigbee.warm_up_finished.is_set()
    assert len(_CapturingUvicornServer.configs) == 1
    # And the supervisor's `connect()` is the only one: `_run` itself calls
    # it nowhere, so one serial port has exactly one opener.
    assert zigbee.connect_calls == 1
    zigbee.warm_up_finished.set()


async def test_no_zigbee_work_happens_when_no_radio_is_configured(monkeypatch, tmp_path):
    """An installation without a Zigbee stick pays nothing: no source in the
    registry, no radio state on the wire, nothing for Loxone to wire up.

    Fault to prove it: always construct the source."""
    _, runtimes, _clients, _supervisor = _install_run_spies(monkeypatch)
    built: list[Any] = []

    def _refuse(**kwargs: Any) -> Any:
        built.append(kwargs)
        raise AssertionError("a Zigbee source was built without a radio configured")

    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _refuse)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    captured = _capture_build_app(monkeypatch)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080)

    assert built == []
    assert [source.technology for source in captured["sources"].all()] == ["matter"]
    # Loxone never sees an input it has no use for.
    assert runtimes[0].zigbee_cached == []


async def test_a_freshly_built_zigbee_source_has_the_store_so_configure_on_join_can_defer(
    monkeypatch, tmp_path
):
    """`ZigbeeSource.__init__` has taken `store` since configure-on-join
    landed - the pending table `configure_device` writes to before every
    attempt - but nothing before this wiring ever constructed a
    `ZigbeeSource` with it set. `_build_zigbee_source` is the first code
    anywhere that builds one at all, so it is the first place this can go
    wrong, and Task 11's later, permanent builder inherits whatever this one
    gets right or wrong.

    Fault to prove it: build `ZigbeeSource(...)` in `_build_zigbee_source`
    without passing `store=store`. A device interrupted mid-configuration is
    then never reconfigured - not because `configure_device` was never
    called, but because it had no table to write what it still owed into
    (`test_a_device_with_pending_configuration_is_watched_again_after_a_restart`
    in `tests/zigbee/test_source.py` measures that consequence against a
    real device).

    The other three arguments are asserted here too, because this one
    function is the only place any of them is decided."""
    _, runtimes, _clients, _supervisor = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    captured = _capture_build_app(monkeypatch)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(
        store,
        "ws://test/ws",
        "127.0.0.1",
        7000,
        8080,
        tmp_path / "matter",
        zigbee_device=ZIGBEE_PATH,
    )

    zigbee = captured["sources"].get("zigbee")
    assert isinstance(zigbee, ZigbeeSource)
    assert zigbee._store is store
    assert zigbee._path == ZIGBEE_PATH
    # Beside the store (`tmp_path / "t.sqlite"`), not under the matter data
    # directory passed above - see the test directly below.
    assert zigbee._database == tmp_path / "zigbee.sqlite"
    # The radio's own signal, wired to the runtime rather than to the
    # watchdog. Identity alone only proves the two attributes point at the
    # same function - calling it is what proves the pipe actually carries a
    # value through to the runtime the Miniserver reads from.
    #
    # `_run`'s own `finally` already disconnected this (never-connected)
    # source by now, which appends its own `False` - so the assertion below
    # checks what THIS call added, not the full history.
    assert zigbee._on_connection_change == runtimes[0].set_zigbee_connected
    await zigbee._on_connection_change(True)
    assert runtimes[0].zigbee_sent[-1] is True


async def test_every_open_of_the_built_zigbee_source_asks_the_thread_lock_out(
    monkeypatch, tmp_path
):
    """The Thread lock-out used to exist only on the `PUT` that chooses a
    stick. A stored setting reopened at boot, on a supervisor retry or on
    an apply was never checked again - so a report gone missing, or Thread
    moved onto that stick by hand, reached zigpy unchallenged. `_run` is
    the one place the production source gets its guard, bound to the SAME
    update directory and device trees the radios routes read, so the two
    cannot disagree about which stick Thread is on.

    Fault to prove it: drop `open_guard=` from `_run`'s `build_source`, or
    bind it to a different `update_dir`."""
    from loxmatter.radios.thread_lockout import open_refusal

    _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    captured = _capture_build_app(monkeypatch)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(
        store,
        "ws://test/ws",
        "127.0.0.1",
        7000,
        8080,
        zigbee_device=ZIGBEE_PATH,
        update_dir=tmp_path / "update",
        radios_host_dev=tmp_path / "dev",
        radios_sys_root=tmp_path / "sys",
    )

    guard = captured["sources"].get("zigbee")._open_guard
    assert guard.func is open_refusal
    assert guard.keywords == {
        "update_dir": tmp_path / "update",
        "host_dev": tmp_path / "dev",
        "sys_root": tmp_path / "sys",
    }
    assert (captured["update_dir"], captured["radios_host_dev"], captured["radios_sys_root"]) == (
        tmp_path / "update",
        tmp_path / "dev",
        tmp_path / "sys",
    )
    # And it answers: with no sidecar report under that directory, the
    # stored stick is refused rather than opened.
    assert guard(ZIGBEE_PATH).key == "api.errors.zigbee_open_thread_unknown"


async def test_zigpy_opens_the_stick_under_the_host_dev_mount_the_run_binds(monkeypatch, tmp_path):
    """The stored stick is a HOST path, `/dev/serial/by-id/...`, and the
    bridge's container has no such directory: its own `/dev` is Docker's
    private tmpfs, and the host's `/dev` is mounted at `/host/dev` only.
    zigpy was handed the host path unchanged, so on the shipped container
    every open failed with `FileNotFoundError` - "the Zigbee stick is no
    longer there" - while the card, which resolves through the mount,
    reported the stick present.

    What zigpy opens is the node under the `radios_host_dev` the production
    `_run` binds; what the source keeps as its own path - for the Thread
    guard, the log line and the API - is still the host path.

    Fault to prove it: drop `host_dev=` from `_run`'s `build_source`
    binding, or put `self._path` back into `_config()`."""
    _install_run_spies(monkeypatch)
    host_dev, sys_root = _host_with_the_zbt2(tmp_path)
    store = Store(tmp_path / "t.sqlite")

    captured = await _run_once(
        monkeypatch,
        store,
        zigbee_device=ZBT2_PATH,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
    )

    zigbee = captured["sources"].get("zigbee")
    opened = zigbee._config()["device"]["path"]
    assert opened.startswith(f"{host_dev}/")
    assert opened == f"{host_dev}/serial/by-id/{ZBT2_NAME}"
    assert Path(opened).exists()
    assert zigbee._path == ZBT2_PATH


@pytest.mark.parametrize("matter_data_dir", [None, "matter-data"])
async def test_the_zigbee_database_follows_the_store_and_never_the_matter_data_dir(
    monkeypatch, tmp_path, matter_data_dir
):
    """zigpy's database belongs in the store's own directory (design 8.5),
    because that is the directory the bridge writes to. `--matter-data-dir`
    is matter-server's directory, lent to the fabric-backup route, and the
    shipped compose file mounts it READ-ONLY - the first build put
    `zigbee.sqlite` there, and the first Zigbee Apply on the Pi would have
    failed to create it.

    The store sits in a directory of its own here, apart from both
    `tmp_path` and the matter data directory, so an answer derived from
    either of those cannot pass by coincidence. With no matter data
    directory at all the answer is the same, and startup does not crash on
    `None / "zigbee.sqlite"`.

    Fault to prove it: build `database` from `matter_data_dir` again (the
    string case fails), or from a fixed path (both fail)."""
    _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    captured = _capture_build_app(monkeypatch)
    (tmp_path / "store").mkdir()
    store = Store(tmp_path / "store" / "loxmatter.sqlite")

    await cli._run(
        store,
        "ws://test/ws",
        "127.0.0.1",
        7000,
        8080,
        None if matter_data_dir is None else tmp_path / matter_data_dir,
        zigbee_device=ZIGBEE_PATH,
    )

    assert captured["sources"].get("zigbee")._database == tmp_path / "store" / "zigbee.sqlite"


async def test_the_radio_state_is_seeded_before_the_first_resend(monkeypatch, tmp_path):
    """`attach()` ends in `resend_all()`, which sends every cached value with
    `force=True`. Seeding `zigbee_connected` before that loop is what puts
    the key on the wire at startup at all; seeding it by SENDING would put it
    there twice (review fix C1, 2026-09-02).

    `False` and not `True`: at that point the radio has not come up - the
    supervisor connects it a few lines later - and the first successful
    connect sets it to `True` through `on_connection_change`.

    Fault to prove it: seed after the `attach()` loop, or seed `True`."""
    _, runtimes, _clients, _supervisor = _install_run_spies(monkeypatch)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")

    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080, zigbee_device=ZIGBEE_PATH)

    assert runtimes[0].zigbee_cached == [False]
    # One `attach()` per source, each ending in its own `resend_all()` - and
    # the seed before all of them, so the very first resend already carries
    # the key.
    assert runtimes[0].call_order == ["zigbee-cache", "seed", "resend", "seed", "resend"]


# --- `--zigbee-device`: a seed, never an override ---------------------------

# A Home Assistant Connect ZBT-2, chosen because every one of its three
# parameters can be told apart from `DEFAULT_UNKNOWN`'s: the fingerprint
# table says 460800/hardware where the fallback says 115200/software. A
# stick whose row happened to match the fallback would let "fingerprint the
# path" and "do not bother" pass the same assertions.
ZBT2_NAME = "usb-Nabu_Casa_ZBT-2_1234-if00"
ZBT2_PATH = f"/dev/serial/by-id/{ZBT2_NAME}"


def _host_with_the_zbt2(tmp_path: Path) -> tuple[Path, Path]:
    """The two trees `scan_serial` reads, holding exactly one known stick.

    The same shape `tests/api/test_zigbee_api.py` builds, minus the
    character devices: nothing here resolves a device node, only the
    fingerprint lookup runs."""
    host_dev, sys_root = tmp_path / "dev", tmp_path / "sys"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    (host_dev / "ttyUSB0").write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / ZBT2_NAME).symlink_to(Path("../..") / "ttyUSB0")
    usb = sys_root / "devices" / "usb1" / "1-1.1"
    (usb / "1-1.1:1.0" / "ttyUSB0").mkdir(parents=True)
    (usb / "idVendor").write_text("303a\n", encoding="utf-8")
    (usb / "idProduct").write_text("4001\n", encoding="utf-8")
    (usb / "manufacturer").write_text("Nabu Casa\n", encoding="utf-8")
    (sys_root / "class" / "tty" / "ttyUSB0").mkdir(parents=True)
    (sys_root / "class" / "tty" / "ttyUSB0" / "device").symlink_to(usb / "1-1.1:1.0" / "ttyUSB0")
    return host_dev, sys_root


async def _run_once(monkeypatch: pytest.MonkeyPatch, store: Store, **kwargs: Any) -> dict[str, Any]:
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    captured = _capture_build_app(monkeypatch)
    await cli._run(store, "ws://test/ws", "127.0.0.1", 7000, 8080, **kwargs)
    return captured


def _stored_after_the_run(path: Path) -> ZigbeeRadioSettings:
    """What survived the process, read the way the NEXT start reads it.

    `_run` closes the store in its own `finally`, so the setting has to be
    read back through a fresh connection - which is the honest test anyway:
    the whole point of storing the radio is that a container recreation
    finds it again."""
    store = Store(path)
    try:
        return store.zigbee_settings.get()
    finally:
        store.close()


async def test_the_zigbee_flag_stores_the_parameters_of_the_stick_it_names(monkeypatch, tmp_path):
    """The flag's path is FINGERPRINTED, exactly as the `PUT` handler
    fingerprints the stick the user picks in the interface.

    The seeding used to be `replace(stored, path=..., saved_at=...)`, which
    keeps whatever radio type, baud rate and flow control were already in
    the row and pins them to the new path. `ZigbeeSettingsStore.save`'s own
    docstring names that exact outcome - "a path from the new stick and a
    baud rate from the old one, and open the new stick at the wrong speed -
    a failure that looks exactly like a broken stick and is invisible in the
    setting the user can see" - as the reason its five keys share one
    transaction. It then arrived through this door instead.

    Fault to prove it: go back to `replace(stored, path=zigbee_device,
    saved_at=now_iso())`. The ZBT-2 below is then stored at 115200/software
    - `DEFAULT_UNKNOWN`'s values, which is what an empty row reads as - and
    opened at less than a quarter of its speed, with no flow control it
    needs."""
    _install_run_spies(monkeypatch)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    host_dev, sys_root = _host_with_the_zbt2(tmp_path)
    store = Store(tmp_path / "t.sqlite")

    await _run_once(
        monkeypatch,
        store,
        zigbee_device=ZBT2_PATH,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
    )

    stored = _stored_after_the_run(tmp_path / "t.sqlite")
    assert stored.path == ZBT2_PATH
    assert (stored.radio_type, stored.baudrate, stored.flow_control) == (
        "ezsp",
        460800,
        "hardware",
    )
    assert stored.saved_at is not None


async def test_the_zigbee_flag_never_overrules_a_setting_that_already_exists(
    monkeypatch, tmp_path, caplog
):
    """A flag left behind in a compose file must not re-apply itself on
    every restart.

    It used to: `if stored.path != zigbee_device: save(...)` reran on every
    single start, so a stick chosen in the interface was silently replaced
    by the one in a YAML file nobody had looked at in months - at 03:00,
    with nothing in the log connecting the two. The stored setting is the
    user's own latest word and wins; the flag is for an installation that
    has never had one.

    Fault to prove it: gate on `stored.path != zigbee_device` again, or on
    `stored.path is None` (which would also re-apply the flag over a
    deliberate "no Zigbee stick in this installation")."""
    _install_run_spies(monkeypatch)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    host_dev, sys_root = _host_with_the_zbt2(tmp_path)
    store = Store(tmp_path / "t.sqlite")
    chosen = ZigbeeRadioSettings(
        path="/dev/serial/by-id/usb-CHOSEN_IN_THE_UI-if00",
        radio_type="znp",
        baudrate=38400,
        flow_control="software",
        saved_at="2026-09-12T09:00:00Z",
    )
    store.zigbee_settings.save(chosen)

    with caplog.at_level(logging.WARNING, logger="loxmatter.cli"):
        await _run_once(
            monkeypatch,
            store,
            zigbee_device=ZBT2_PATH,
            radios_host_dev=host_dev,
            radios_sys_root=sys_root,
        )

    assert _stored_after_the_run(tmp_path / "t.sqlite") == chosen
    # And said out loud, because the silence is what made the old behaviour
    # impossible to diagnose.
    assert any(ZBT2_PATH in record.getMessage() for record in caplog.records)


async def test_the_zigbee_flag_does_not_overrule_a_deliberate_no_zigbee_stick(
    monkeypatch, tmp_path
):
    """Choosing "no Zigbee stick" in the interface is a choice, not an empty
    row, and `saved_at` is what tells them apart - it is written by every
    path that stores a setting and removed only by `clear()`.

    Fault to prove it: gate the seeding on `stored.path is None`. The user
    who unplugged their stick and said so then finds it configured again
    after the next container recreation."""
    _install_run_spies(monkeypatch)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    host_dev, sys_root = _host_with_the_zbt2(tmp_path)
    store = Store(tmp_path / "t.sqlite")
    store.zigbee_settings.save(
        ZigbeeRadioSettings(
            path=None,
            radio_type="ezsp",
            baudrate=115200,
            flow_control="software",
            saved_at="2026-09-12T09:00:00Z",
        )
    )

    captured = await _run_once(
        monkeypatch,
        store,
        zigbee_device=ZBT2_PATH,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
    )

    assert _stored_after_the_run(tmp_path / "t.sqlite").path is None
    assert [source.technology for source in captured["sources"].all()] == ["matter"]


async def test_without_the_flag_the_stored_setting_is_what_opens(monkeypatch, tmp_path):
    """The fourth combination, and the one the shipped help text used to
    deny outright: with no `--zigbee-device` on the command line a source IS
    built, from the store.

    Fault to prove it: read the flag directly instead of the stored
    setting. Every installation then loses its Zigbee radio the moment the
    flag is taken out of the compose file, which is precisely what the old
    help text told the user to expect."""
    _install_run_spies(monkeypatch)
    monkeypatch.setattr(zigbee_runtime_module, "ZigbeeSource", _NeverConnectingZigbeeSource)
    store = Store(tmp_path / "t.sqlite")
    store.zigbee_settings.save(
        ZigbeeRadioSettings(
            path=ZIGBEE_PATH,
            radio_type="ezsp",
            baudrate=115200,
            flow_control="software",
            saved_at="2026-09-12T09:00:00Z",
        )
    )

    captured = await _run_once(monkeypatch, store)

    assert captured["sources"].get("zigbee").kwargs["path"] == ZIGBEE_PATH


async def test_no_flag_and_no_stored_setting_builds_no_zigbee_source(monkeypatch, tmp_path):
    """The fresh install, and the combination that keeps the other three
    honest: nothing configured anywhere means no source, no supervisor and
    no Zigbee state on the wire to Loxone.

    Fault to prove it: seed from the flag's default (`None`) as though it
    were a value - `settings_for_path(None, ...)` writes a row with a
    `saved_at`, which would then read as a deliberate "no Zigbee stick" and
    lock the flag out for good on an installation that had never used it."""
    _install_run_spies(monkeypatch)
    store = Store(tmp_path / "t.sqlite")

    captured = await _run_once(monkeypatch, store)

    assert [source.technology for source in captured["sources"].all()] == ["matter"]
    assert _stored_after_the_run(tmp_path / "t.sqlite").saved_at is None, "nothing was written"
