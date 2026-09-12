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

"""GET and POST /api/radios (design 2026-09-11 "Radios in the Web UI",
section 7)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

SONOFF = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_heartbeat(update_dir: Path, phase: str = "idle") -> None:
    body = {
        "id": None,
        "phase": phase,
        "updater_seen_at": _now(),
        "updater_stack_host_path": "/home/pi/stack",
    }
    (update_dir / "state.json").write_text(json.dumps(body), encoding="utf-8")


def _radios_heartbeat(update_dir: Path, **fields: Any) -> None:
    body = {
        "id": None,
        "phase": "idle",
        "steps": [],
        "error": None,
        "rolled_back": False,
        "healthy": None,
        "current": {
            "thread_enabled": True,
            "thread_device": "/dev/ttyUSB0",
            "bluetooth_adapter": 0,
            "otbr_running": True,
        },
        "capable": True,
        "capable_reason": None,
        "seen_at": _now(),
    }
    body.update(fields)
    (update_dir / "radios-state.json").write_text(json.dumps(body), encoding="utf-8")


def _host(tmp_path: Path) -> tuple[Path, Path]:
    host_dev, sys_root = tmp_path / "dev", tmp_path / "sys"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    (host_dev / "ttyUSB0").write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / SONOFF).symlink_to(Path("../..") / "ttyUSB0")
    usb = sys_root / "devices" / "usb1" / "1-1"
    (usb / "1-1:1.0" / "ttyUSB0").mkdir(parents=True)
    (usb / "idVendor").write_text("10c4\n", encoding="utf-8")
    (usb / "idProduct").write_text("ea60\n", encoding="utf-8")
    (usb / "product").write_text("SONOFF Dongle Plus MG24\n", encoding="utf-8")
    (sys_root / "class" / "tty" / "ttyUSB0").mkdir(parents=True)
    (sys_root / "class" / "tty" / "ttyUSB0" / "device").symlink_to(usb / "1-1:1.0" / "ttyUSB0")
    serial = sys_root / "devices" / "serial0" / "serial0-0"
    serial.mkdir(parents=True)
    (sys_root / "class" / "bluetooth" / "hci0").mkdir(parents=True)
    (sys_root / "class" / "bluetooth" / "hci0" / "device").symlink_to(serial)
    return host_dev, sys_root


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Path]]:
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    host_dev, sys_root = _host(tmp_path)
    store = Store(tmp_path / "t.sqlite")
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        update_dir=update_dir,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await authenticate(store, client)
        yield client, update_dir
    store.close()


async def test_without_a_sidecar_the_card_is_read_only_with_detection(api):
    client, _ = api
    body = (await client.get("/api/radios")).json()
    assert body["sidecar"] == "missing"
    assert body["serial"] == [
        {
            "path": f"/dev/serial/by-id/{SONOFF}",
            "tty": "ttyUSB0",
            "manufacturer": None,
            "product": "SONOFF Dongle Plus MG24",
            "serial": None,
            "vid_pid": "10c4:ea60",
        }
    ]
    assert body["bluetooth"] == [
        {"index": 0, "name": "hci0", "bus": "uart", "product": None, "rfkill_blocked": False}
    ]
    assert body["current"] is None


async def test_a_ready_sidecar_reports_current_with_the_legacy_path_mapped(api):
    """Fault to prove it: return the sidecar's raw thread_device."""
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    body = (await client.get("/api/radios")).json()
    assert body["sidecar"] == "ready"
    assert body["updater_stack_host_path"] == "/home/pi/stack"
    assert body["current"] == {
        "thread_enabled": True,
        "thread_device": f"/dev/serial/by-id/{SONOFF}",
        "thread_device_present": True,
        "bluetooth_adapter": 0,
        "otbr_running": True,
    }


async def test_an_outdated_and_an_unmounted_sidecar_are_told_apart(api):
    client, update_dir = api
    _update_heartbeat(update_dir)
    assert (await client.get("/api/radios")).json()["sidecar"] == "outdated"
    _radios_heartbeat(update_dir, capable=False, capable_reason="host_dev_not_mounted")
    body = (await client.get("/api/radios")).json()
    assert body["sidecar"] == "unmounted"
    assert body["current"] is not None


async def test_the_latest_job_is_reported(api):
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(
        update_dir,
        id="j1",
        phase="failed",
        steps=["validate", "verify_thread"],
        error="verify_thread_failed",
        rolled_back=True,
        healthy=True,
    )
    job = (await client.get("/api/radios")).json()["job"]
    assert job == {
        "id": "j1",
        "phase": "failed",
        "steps": ["validate", "verify_thread"],
        "error": "verify_thread_failed",
        "rolled_back": True,
        "healthy": True,
    }


def _body(
    device: str | None = f"/dev/serial/by-id/{SONOFF}", enabled: bool = True, adapter: int = 0
):
    return {"thread": {"enabled": enabled, "device": device}, "bluetooth": {"adapter": adapter}}


async def test_a_valid_change_writes_a_request(api):
    """The full both-halves shape, which every version of this API has
    accepted and must keep accepting - Task 7d added a `null` half beside
    it, it did not replace it."""
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    response = await client.post("/api/radios", json=_body())
    assert response.status_code == 202
    request = json.loads((update_dir / "radios-request.json").read_text(encoding="utf-8"))
    assert request["id"] == response.json()["id"]
    assert request["thread"] == {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}"}
    assert request["bluetooth"] == {"adapter": 0}


async def test_a_thread_half_nobody_is_changing_is_passed_through_as_null(api):
    """Task 7d, the API half of the fix. The user's configured Thread stick
    has fallen out (no by-id entry is left to scan), and they want to change
    only Bluetooth. Their Thread half is `null` - not a value that could be
    wrong, but the absence of a request for that radio - so none of the
    device checks may run on it, and the `null` must reach the sidecar
    intact, since that is what tells the sidecar to skip validate, write,
    apply and verify for Thread.

    Fault to prove it: drop the `body.thread is not None` guard from the
    device checks in `post_radios` (a 500 from `None.enabled`), or write a
    fabricated both-halves body to the request file instead of passing the
    `None` through (`request["thread"]` is then an object and the sidecar
    would act on a radio nobody named)."""
    client, update_dir = api
    (update_dir.parent / "dev" / "serial" / "by-id" / SONOFF).unlink()
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    response = await client.post("/api/radios", json={"thread": None, "bluetooth": {"adapter": 0}})
    assert response.status_code == 202
    request = json.loads((update_dir / "radios-request.json").read_text(encoding="utf-8"))
    assert request["thread"] is None
    assert request["bluetooth"] == {"adapter": 0}


async def test_a_bluetooth_half_nobody_is_changing_is_passed_through_as_null(api):
    """The symmetric case: a Thread-only change leaves matter-server
    alone."""
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    response = await client.post(
        "/api/radios",
        json={
            "thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}"},
            "bluetooth": None,
        },
    )
    assert response.status_code == 202
    request = json.loads((update_dir / "radios-request.json").read_text(encoding="utf-8"))
    assert request["bluetooth"] is None
    assert request["thread"] == {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}"}


async def test_both_keys_must_be_present_even_when_null(api):
    """Required-but-nullable, the same strictness the sidecar's own key
    whitelist has: `null` is a deliberate "leave this alone", a missing key
    is a caller that forgot. Fault to prove it: give `RadiosIn.thread` and
    `RadiosIn.bluetooth` a `None` default, which silently accepts both."""
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    assert (await client.post("/api/radios", json={"bluetooth": {"adapter": 0}})).status_code == 422
    assert (await client.post("/api/radios", json={"thread": None})).status_code == 422
    assert not (update_dir / "radios-request.json").exists()


async def test_no_request_unless_the_sidecar_is_ready(api):
    """Fault to prove it: drop the readiness check in the POST route."""
    client, update_dir = api
    _update_heartbeat(update_dir)
    response = await client.post("/api/radios", json=_body())
    assert response.status_code == 503
    assert not (update_dir / "radios-request.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        _body(device=None, enabled=True),
        _body(device="/dev/serial/by-id/usb-Gone"),
        _body(adapter=3),
    ],
)
async def test_obviously_invalid_values_are_a_400(api, body):
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    response = await client.post("/api/radios", json=body)
    assert response.status_code == 400
    assert not (update_dir / "radios-request.json").exists()


async def test_disabling_thread_needs_no_device(api):
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    assert (
        await client.post("/api/radios", json=_body(device=None, enabled=False))
    ).status_code == 202


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits do not apply on Windows")
async def test_an_unwritable_update_directory_returns_a_mapped_status_not_a_bare_500(api):
    """Mirrors `tests/api/test_update_api.py`'s test of the same name: a
    read-only remount after an SD-card fault, or a full disk, must land on
    the mapped 503 (`api.radios.fail_unwritable`), not the bare 500 an
    unhandled `PermissionError` from `request_radios`'s `write_text` would
    otherwise produce."""
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    os.chmod(update_dir, 0o500)  # read + execute only - no write, no create
    try:
        response = await client.post("/api/radios", json=_body())
    finally:
        os.chmod(update_dir, 0o700)  # tmp_path cleanup needs this back
    assert response.status_code == 503
    assert response.json()["detail"]


async def test_a_running_update_is_a_409(api):
    client, update_dir = api
    _update_heartbeat(update_dir, phase="recreate")
    _radios_heartbeat(update_dir)
    assert (await client.post("/api/radios", json=_body())).status_code == 409


async def test_the_routes_need_a_login(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Without a password set and a session, no /api route answers with
        # content (README, "Locked down by default").
        assert (await client.get("/api/radios")).status_code == 401
    store.close()
