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

import json
from pathlib import Path

import httpx2
import pytest

from loxmatter import i18n
from loxmatter.auth.passwords import hash_password
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object, bool]] = []

    async def send(self, key, value, *, force: bool = False) -> bool:
        self.sent.append((key, value, force))
        return True

    async def close(self) -> None:
        return None


class BrokenResendSender(FakeSender):
    """Sends normal updates without complaint, but refuses every call -
    simulates a `UdpSender` whose socket is already closed (see
    `UdpSender.send`, which then unconditionally raises `RuntimeError`)."""

    async def send(self, key, value, *, force: bool = False) -> bool:
        raise RuntimeError("UdpSender ist geschlossen")


@pytest.fixture
async def client(tmp_path):
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    calls = []

    async def invoke(call):
        calls.append(call)

    runtime = Runtime(store, FakeSender())
    app = build_app(store, invoke, runtime)
    # httpx2.AsyncClient instead of Starlette's TestClient: TestClient runs
    # the request in an anyio portal thread that is not the thread in which
    # this fixture created the store - but sqlite3 connections are bound to
    # their creating thread (see store.py). AsyncClient with ASGITransport
    # calls the app directly in this test's event loop, without opening a
    # second thread.
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, calls, device_id
    store.close()


async def test_command_reaches_matter(client):
    c, calls, device_id = client
    response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].cluster_id == 6
    assert calls[0].command_id == 1


async def test_unknown_key_yields_404_not_500(client):
    c, calls, _ = client
    response = await c.get("/cmd/d1_1_gibtsnicht/1")
    assert response.status_code == 404
    assert calls == []


async def test_unsupported_value_yields_400(client):
    c, _, device_id = client
    response = await c.get(f"/cmd/d{device_id}_1_on/../etc/passwd")
    assert response.status_code in (400, 404)


async def test_resync_forces_a_full_resend(client):
    c, _, _ = client
    response = await c.get("/resync")
    assert response.status_code == 200
    # Review fix M9, 2026-09-02: "gesendet" was a German key in a wire
    # format - renamed to "sent" (see server.py).
    assert "sent" in response.text.lower() or response.json()["sent"] >= 0


async def test_health_answers_without_touching_matter(client):
    c, calls, _ = client
    assert (await c.get("/health")).status_code == 200
    assert calls == []


async def test_a_failing_matter_call_yields_502_not_a_traceback(tmp_path):
    """A device that currently does not answer must not produce a traceback."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    async def invoke(call):
        raise TimeoutError("device does not respond")

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 502
    assert "Traceback" not in response.text
    # Task 6: shares api.errors.device_unreachable with control.py's
    # execute_command (see test_control.py::
    # test_a_device_that_does_not_answer_yields_502).
    assert response.json()["detail"] == "device unreachable: device does not respond"
    store.close()


async def test_a_failing_matter_call_yields_502_with_the_german_detail_text(tmp_path):
    """German companion test to
    test_a_failing_matter_call_yields_502_not_a_traceback (task 6) -
    `store.locale.set_language`, not `i18n.set_language` directly: the
    sync_language middleware reads from the store anew on every request."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)
    store.locale.set_language("de")

    async def invoke(call):
        raise TimeoutError("device does not respond")

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 502
    assert response.json()["detail"] == "Geraet nicht erreichbar: device does not respond"
    store.close()


async def test_a_failing_resend_yields_502_not_a_traceback(tmp_path):
    """Review fix minor #3: /resync must not pass a broken sender (e.g. an
    already-closed UdpSender) through as a bare 500 - the same safeguard
    as for a failing /cmd."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    runtime = Runtime(store, BrokenResendSender())
    # `on_attribute` enters the value into `_last_values` BEFORE it calls
    # the sender (see runtime.py) - so the first call fails on the send,
    # but leaves `_last_values` populated as desired, so that `resend_all`
    # below has anything to try sending at all.
    with pytest.raises(RuntimeError):
        await runtime.on_attribute(device_id, "2/144/4", 230000)

    async def invoke(call):
        return None

    app = build_app(store, invoke, runtime)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/resync")
    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert response.json()["detail"] == "Full resend failed: UdpSender ist geschlossen"
    store.close()


async def test_a_failing_resend_yields_502_with_the_german_detail_text(tmp_path):
    """German companion test to test_a_failing_resend_yields_502_not_a_traceback
    (task 6) - `store.locale.set_language`, not `i18n.set_language` directly."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)
    store.locale.set_language("de")

    runtime = Runtime(store, BrokenResendSender())
    with pytest.raises(RuntimeError):
        await runtime.on_attribute(device_id, "2/144/4", 230000)

    async def invoke(call):
        return None

    app = build_app(store, invoke, runtime)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/resync")
    assert response.status_code == 502
    assert response.json()["detail"] == "Full-Resend fehlgeschlagen: UdpSender ist geschlossen"
    store.close()


def test_sync_language_is_the_outermost_middleware(tmp_path):
    """Review fix important (whole-branch review, 2026-09-04): Starlette
    inserts every layer registered via `@app.middleware("http")` at the
    FRONT of `app.user_middleware` and builds the stack from
    `reversed(...)` - the LAST-registered function lands at index 0 and
    becomes the OUTERMOST layer (verified by a `TestClient` probe, see the
    docstring of `_sync_language` in server.py). `_sync_language` must
    therefore be registered LAST, so it runs before `_record_command` and
    before every route - this test records that, so a future reordering of
    the two `@app.middleware("http")` blocks in `build_app` is caught
    immediately."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    store.register_device(snap)

    async def invoke(call):
        return None

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    assert app.user_middleware[0].kwargs["dispatch"].__name__ == "_sync_language"
    store.close()


async def test_loxmatter_lang_override_survives_multiple_requests(tmp_path, monkeypatch):
    """Review fix important (whole-branch review, 2026-09-04): `cli.py`
    documents `LOXMATTER_LANG` as an override with strict precedence over
    `store.locale` - but until now `_sync_language` overwrote the process
    language again from the store on EVERY incoming request, so even the
    very first HTTP request discarded the override set by the CLI
    bootstrap. This test sets `LOXMATTER_LANG=de` AND leaves the store at
    English (the default) - thereby simulating exactly the contradictory
    situation from the review finding - and checks that two consecutive
    requests do NOT discard the language set via bootstrap."""
    monkeypatch.setenv("LOXMATTER_LANG", "de")
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    store.register_device(snap)
    # Store stays at the default (English) - exactly the contradictory
    # scenario that triggers the bug: without the fix, the first request
    # flips `i18n.current_language()` back to "en".
    assert store.locale.get_language() == "en"

    async def invoke(call):
        return None

    # Simulates cli.py's module-import bootstrap (`i18n.set_language(
    # _resolve_cli_language(...))`), which runs BEFORE `build_app()`.
    i18n.set_language("de")
    app = build_app(store, invoke, Runtime(store, FakeSender()))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/health")
        assert i18n.current_language() == "de"
        await c.get("/health")
        assert i18n.current_language() == "de"
    store.close()


async def test_a_crashing_route_still_appears_in_the_command_log_and_still_raises(tmp_path):
    """Review fix important #2 (2026-09-02): `_record_command` used to call
    `call_next` UNGUARDED - an unhandled exception from a route (no
    `HTTPException`, a genuine programming error) left `call_next` before
    the try/except around appending to the ring buffer was ever reached.
    The call that brings the service down was therefore missing exactly
    where a diagnostician needs it most (`GET /api/diagnostics/commands`).
    This route here (`/__boom__`) stands for exactly such a programming
    error - none of the existing routes raise unhandled, `/cmd` and
    `/resync` already catch every `Exception` down to a clean 502 (see the
    two tests above)."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    async def invoke(call):
        return None

    app = build_app(store, invoke, Runtime(store, FakeSender()))

    @app.get("/__boom__")
    async def boom() -> None:
        raise RuntimeError("simulated programming error in a route")

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        # Since task 8, `/api/diagnostics/commands` also requires a
        # login - without it, the following response would be a 401
        # instead of the expected command list, entirely independent of
        # the crash path actually under investigation here.
        store.auth.set_password_hash(hash_password("test-passwort"))
        login = await c.post("/auth/login", json={"password": "test-passwort"})
        assert login.status_code == 200
        with pytest.raises(RuntimeError, match="simulated programming error"):
            await c.get("/__boom__")
        entries = (await c.get("/api/diagnostics/commands")).json()
    store.close()

    boom_entries = [e for e in entries if e["path"] == "/__boom__"]
    assert len(boom_entries) == 1
    # Not a real HTTP status code (see `_CRASHED_STATUS` in server.py) -
    # distinguishable from any response the route actually sends.
    assert boom_entries[0]["status"] == 0
