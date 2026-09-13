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

"""`GET` and `PUT /api/zigbee/radio` - choosing the Zigbee coordinator, and
refusing the stick a live Thread border router is running on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter import i18n
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import settings_for_path
from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.radios.inventory import scan_serial
from loxmatter.sources import Sources
from loxmatter.zigbee import source as source_module
from loxmatter.zigbee.runtime import ZigbeeRuntime
from loxmatter.zigbee.source import ZigbeeSource

# Verbatim from `ls -l /dev/serial/by-id/` on the maintainer's Pi.
REAL_ITEAD = (
    "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0"
)
REAL_MG24 = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"

ITEAD_PATH = f"/dev/serial/by-id/{REAL_ITEAD}"
MG24_PATH = f"/dev/serial/by-id/{REAL_MG24}"

# Two REAL character devices, standing in for the two sticks. A test cannot
# create a device node without root, and the fixture below cannot use plain
# files either: `device_identity` refuses anything that is not a character
# device, and two plain files would both resolve to nothing - which would
# make the Thread stick selectable and this whole suite green for the wrong
# reason. These two exist on every POSIX host, and - the property that
# matters - they share a major and differ only in their minor, exactly as
# the maintainer's two sticks do (both major 188, minors 0 and 1).
CHAR_DEVICES = {"ttyUSB0": "/dev/null", "ttyUSB1": "/dev/zero"}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _current(**fields: object) -> dict[str, object]:
    """The sidecar's reported state, as the live Pi reports it: Thread on,
    running on ttyUSB0, which is the MG24."""
    body: dict[str, object] = {
        "thread_enabled": True,
        "thread_device": "/dev/ttyUSB0",
        "thread_device_present": True,
        "bluetooth_adapter": 0,
        "otbr_running": True,
    }
    body.update(fields)
    return body


def _radios_heartbeat(update_dir: Path, **fields: Any) -> None:
    body: dict[str, Any] = {
        "id": None,
        "phase": "idle",
        "steps": [],
        "error": None,
        "rolled_back": False,
        "healthy": None,
        "current": _current(),
        "capable": True,
        "capable_reason": None,
        "seen_at": _now(),
    }
    body.update(fields)
    (update_dir / "radios-state.json").write_text(json.dumps(body), encoding="utf-8")


def _host_two_sticks(tmp_path: Path) -> tuple[Path, Path]:
    """The maintainer's real Pi, as a directory tree.

    Both sticks are `10c4:ea60` because that is what the hardware reports,
    and both resolve to character devices that share a major and differ
    only in their minor - the Pi's own 188:0 and 188:1. A fixture that gave
    them different vendor ids, or let them resolve to plain files, would
    let a matcher keyed on either of those pass while failing on the real
    machine.

    Each `ttyUSB*` node is a symlink onto a real character device rather
    than the file the plan sketched: `os.stat` follows it, so the by-id
    entry and the tty name resolve to the SAME `st_rdev`, which is the
    whole mechanism under test. A regular file would resolve to nothing at
    all.
    """
    host_dev, sys_root = tmp_path / "dev", tmp_path / "sys"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    for tty, by_id, minor in (
        ("ttyUSB0", REAL_MG24, 0),  # the THREAD stick - RADIO_DEVICE names it
        ("ttyUSB1", REAL_ITEAD, 1),  # the Zigbee coordinator
    ):
        (host_dev / tty).symlink_to(CHAR_DEVICES[tty])
        (host_dev / "serial" / "by-id" / by_id).symlink_to(Path("../..") / tty)
        usb = sys_root / "devices" / "usb1" / f"1-1.{minor}"
        (usb / f"1-1.{minor}:1.0" / tty).mkdir(parents=True)
        (usb / "idVendor").write_text("10c4\n", encoding="utf-8")
        (usb / "idProduct").write_text("ea60\n", encoding="utf-8")
        (sys_root / "class" / "tty" / tty).mkdir(parents=True)
        (sys_root / "class" / "tty" / tty / "device").symlink_to(usb / f"1-1.{minor}:1.0" / tty)
    (sys_root / "class" / "bluetooth" / "hci0").mkdir(parents=True)
    return host_dev, sys_root


class _Matter:
    """The mandatory source, so the registry is never empty."""

    technology = "matter"
    connected = True


class _FakeRadio:
    """A `ZigbeeSource` without zigpy, whose `connect()` can be made slow.

    `connect_delay` is what distinguishes "the handler scheduled the
    reconnection" from "the handler waited for it": with it set far higher
    than the request's own timeout, a `PUT` that answers at all proves the
    radio's cost was not on the request.
    """

    technology = "zigbee"

    def __init__(self, path: str, *, connect_delay: float = 0.0) -> None:
        self.path = path
        self.connected = False
        self.connect_started = False
        self.connect_delay = connect_delay
        self.disconnects = 0
        self.connected_event = asyncio.Event()
        self._progress = _progress("idle")
        self.coordinator_info: Any = None

    def progress(self) -> Any:
        return self._progress

    def coordinator(self) -> Any:
        return self.coordinator_info

    def set_progress(self, state: str, **fields: Any) -> None:
        self._progress = _progress(state, **fields)

    async def connect(self) -> None:
        self.connect_started = True
        self.set_progress("loading_quirks")
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        self.connected = True
        self.set_progress("connected")
        self.connected_event.set()

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False
        self.set_progress("idle")

    async def wait_for_link_loss(self) -> None:
        if not self.connected:
            return
        await asyncio.Event().wait()

    async def snapshots(self) -> list[NodeSnapshot]:
        return []

    async def subscribe(self, resolve_device_id: Any, handler: Any) -> None:
        return None

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None:
        return None

    async def send(self, call: Any) -> None:
        return None

    async def remove(self, address: str) -> None:
        return None


def _progress(state: str, **fields: Any) -> Any:
    from loxmatter.zigbee.source import ConnectionProgress

    return ConnectionProgress(
        state=state,  # type: ignore[arg-type]
        attempts=fields.get("attempts", 0),
        error=fields.get("error"),
        changed_at=_now(),
    )


class _AttachableRuntime:
    """`FakeRuntime` plus the two methods the REAL `supervise()` reaches.

    The suite drives the real supervisor rather than a stand-in for it,
    because "the reconnection happens in the background" is a claim about
    that exact object - `attach()` included."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self.seeded = 0
        # What a source told it through the `RuntimeEventHandler` half of
        # this object. The pairing suite subscribes a real `ZigbeeSource`
        # to it, so an adoption that forgets to seed its device is visible
        # here as a snapshot that never arrived.
        self.snapshots: list[tuple[int, Any]] = []

    async def seed_from_snapshot(self, snapshots: Any) -> None:
        self.seeded += 1

    async def on_node_snapshot(self, device_id: int, snapshot: Any) -> None:
        self.snapshots.append((device_id, snapshot))

    async def on_attribute(self, device_id: int, path: str, raw: Any) -> None:
        return None

    async def on_event(self, device_id: int, path: str) -> None:
        return None

    async def resend_all(self) -> int:
        return 0

    async def set_online(self, device_id: int, online: bool) -> None:
        return None

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        return {}

    def last_heard_for(self, device_id: int) -> str | None:
        return None


@dataclass
class _Harness:
    store: Store
    holder: ZigbeeRuntime
    built: list[_FakeRadio]
    sources: Sources
    connect_delay: list[float]
    # Held open for as long as a test wants the swap window to last - the
    # window between the old source being released and the new one existing,
    # which is `disconnect()` plus a border-router read on real hardware and
    # is otherwise far too short to look inside.
    hold_the_build: asyncio.Event


@pytest.fixture
async def api(tmp_path, no_invoke) -> AsyncIterator[tuple[httpx.AsyncClient, Path, _Harness]]:
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    host_dev, sys_root = _host_two_sticks(tmp_path)
    store = Store(tmp_path / "t.sqlite")
    runtime = _AttachableRuntime(store)
    sources = Sources([_Matter()])  # type: ignore[list-item]
    built: list[_FakeRadio] = []
    connect_delay = [0.0]
    hold_the_build = asyncio.Event()
    hold_the_build.set()

    async def build(settings: Any) -> Any:
        await hold_the_build.wait()
        if settings.path is None:
            return None
        radio = _FakeRadio(settings.path, connect_delay=connect_delay[0])
        built.append(radio)
        return radio

    holder = ZigbeeRuntime(store, runtime, sources, build_source=build)  # type: ignore[arg-type]
    app = build_app(
        store,
        no_invoke,
        runtime,
        sources=sources,
        update_dir=update_dir,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
        zigbee_runtime=holder,
    )
    _radios_heartbeat(update_dir)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await authenticate(store, client)
        yield (
            client,
            update_dir,
            _Harness(store, holder, built, sources, connect_delay, hold_the_build),
        )
    hold_the_build.set()
    await holder.stop()
    store.close()


# --- The exclusion --------------------------------------------------------


async def test_the_thread_stick_is_refused_by_the_api_not_only_hidden_by_the_card(api):
    """The UI filter is a courtesy; the server check is the guarantee. A
    `PUT` naming the Thread device must be refused even though the card
    never offers it - a stale page, a second tab or a curl call must not be
    able to point zigpy at the border router's radio.

    Fault to prove it: drop the server-side check and rely on the card."""
    client, _update_dir, harness = api
    response = await client.put("/api/zigbee/radio", json={"path": MG24_PATH})
    assert response.status_code == 400
    assert "thread" in response.json()["detail"].lower()
    # And nothing was stored or built behind the refusal.
    assert harness.store.zigbee_settings.get().path is None
    assert harness.built == []


async def test_the_two_sticks_on_the_maintainers_pi_end_up_on_opposite_sides(api):
    """THE REAL INSTALLATION, PINNED (measured 12 September 2026).

    `ls -l /dev/serial/by-id/` on the maintainer's Pi returns exactly two
    entries, and `RADIO_DEVICE=/dev/ttyUSB0` in the live stack names the
    MG24. Both sticks report `10c4:ea60` and both are major 188, so neither
    the USB ids nor the major can separate them - only the resolved minor
    and the by-id name can.

    The ITEAD stick must be OFFERED and the MG24 must be REFUSED. If this
    ever comes out the other way round on real hardware, the picker hands
    the user the radio their Thread border router is running on, and
    selecting it takes down every Thread device in the house.

    Fault to prove it: drop the server-side check entirely (MEASURED: the
    MG24 then becomes selectable and the PUT is accepted).

    The plan named a different fault here - "compare path strings instead
    of resolved major:minor" - and it was INJECTED AND DID NOT FAIL this
    test. The reason is worth recording rather than papering over:
    `match_current_device` has ALREADY mapped the sidecar's
    `/dev/ttyUSB0` onto the MG24's by-id path by the time
    `_is_thread_stick` sees it, so on this `.env` spelling the two strings
    are equal and a string compare survives. The input that does separate
    the two implementations is a `RADIO_DEVICE` spelling
    `match_current_device` cannot map, and it has its own test below -
    `test_the_thread_stick_is_still_refused_under_a_name_the_card_cannot_map`."""
    client, _update_dir, _harness = api
    body = (await client.get("/api/zigbee/radio")).json()
    by_path = {stick["path"]: stick for stick in body["serial"]}
    itead = by_path[ITEAD_PATH]
    mg24 = by_path[MG24_PATH]

    assert (itead["is_thread"], itead["selectable"]) == (False, True)
    assert (mg24["is_thread"], mg24["selectable"]) == (True, False)

    assert (await client.put("/api/zigbee/radio", json={"path": itead["path"]})).status_code == 202
    refused = await client.put("/api/zigbee/radio", json={"path": mg24["path"]})
    assert refused.status_code == 400
    assert "thread" in refused.json()["detail"].lower()


async def test_the_thread_stick_is_still_refused_under_a_name_the_card_cannot_map(api, tmp_path):
    """THE test that separates resolved major:minor from a string compare,
    and the reason the comparison resolves at all.

    `match_current_device` maps the installer's `/dev/ttyUSB0` onto a by-id
    path, so for THAT spelling the two strings happen to be equal and a
    string compare would pass. It cannot map every spelling: a
    `RADIO_DEVICE` naming the same stick through `/dev/serial/by-path/...`
    - which is a legitimate, stable name a user may well put in `.env` by
    hand, and which `match_current_device` explicitly hands back unchanged
    with `present=False` - is a THIRD string for the one piece of hardware.

    Resolved through the container's `/host/dev` mount, the by-path entry,
    the by-id entry and `ttyUSB0` are one node with one major:minor, and
    the MG24 stays refused. Compared as strings, it becomes selectable and
    the user can point zigpy at their live Thread coordinator.

    Fault to prove it: compare the path strings in `is_same_device`."""
    client, update_dir, _harness = api
    host_dev = tmp_path / "dev"
    (host_dev / "serial" / "by-path").mkdir(parents=True)
    by_path = "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.2:1.0-port0"
    (host_dev / "serial" / "by-path" / by_path).symlink_to(Path("../..") / "ttyUSB0")
    _radios_heartbeat(
        update_dir,
        current={**_current(), "thread_device": f"/dev/serial/by-path/{by_path}"},
    )

    body = (await client.get("/api/zigbee/radio")).json()
    by_id = {stick["path"]: stick for stick in body["serial"]}
    assert (by_id[MG24_PATH]["is_thread"], by_id[MG24_PATH]["selectable"]) == (True, False)
    assert (by_id[ITEAD_PATH]["is_thread"], by_id[ITEAD_PATH]["selectable"]) == (False, True)

    refused = await client.put("/api/zigbee/radio", json={"path": MG24_PATH})
    assert refused.status_code == 400
    assert "thread" in refused.json()["detail"].lower()


async def test_a_stick_freed_by_turning_thread_off_becomes_selectable_again(api):
    """The escape hatch, and the reason the refusal is gated on
    `thread_enabled` rather than on `RADIO_DEVICE` alone.

    MEASURED in `deploy/updater/radios-once.sh`: the `down` path rewrites
    `COMPOSE_PROFILES` and LEAVES `RADIO_DEVICE` naming the stick. A refusal
    keyed on the stored device alone would therefore be permanent - the user
    who disables Thread specifically in order to repurpose their MG24 would
    find it greyed out forever, under a message telling them to release it
    in a row where they already have. An MG24 is dual-capable hardware and
    this is the only legitimate way to move it across.

    Fault to prove it: ignore `thread_enabled` and refuse whenever the path
    resolves to `RADIO_DEVICE`. The second half of this test then fails
    while the first half still passes."""
    client, update_dir, _harness = api
    assert (await client.put("/api/zigbee/radio", json={"path": MG24_PATH})).status_code == 400

    _radios_heartbeat(
        update_dir, current={**_current(), "thread_enabled": False, "otbr_running": False}
    )
    body = (await client.get("/api/zigbee/radio")).json()
    freed = next(s for s in body["serial"] if s["path"] == MG24_PATH)
    assert (freed["is_thread"], freed["selectable"]) == (False, True)
    assert (await client.put("/api/zigbee/radio", json={"path": MG24_PATH})).status_code == 202


async def test_a_border_router_still_running_keeps_the_stick_locked(api):
    """`thread_enabled` and `otbr_running` are two independent facts, and
    the refusal is gated on EITHER. A `.env` already rewritten to
    `thread_enabled: false` while the OTBR container has not actually been
    taken down yet is the window in which the stick is still very much in
    use.

    Fault to prove it: read `thread_enabled` alone. The stick then becomes
    selectable while the border router is still holding it open, and the
    apply lands on a busy port - or worse, does not."""
    client, update_dir, _harness = api
    _radios_heartbeat(
        update_dir, current={**_current(), "thread_enabled": False, "otbr_running": True}
    )
    body = (await client.get("/api/zigbee/radio")).json()
    mg24 = next(s for s in body["serial"] if s["path"] == MG24_PATH)
    assert (mg24["is_thread"], mg24["selectable"]) == (True, False)
    assert (await client.put("/api/zigbee/radio", json={"path": MG24_PATH})).status_code == 400


def _stale() -> str:
    return (datetime.now(UTC) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")


# Every way the sidecar's report can fail to vouch for the Thread stick. Each
# one used to exclude NOTHING - the MG24 was offered and accepted - because a
# missing report read as "Thread uses no stick". `mg24_is_thread` is what a
# stale report still says about the MG24: it is out of date, not wrong.
_UNVOUCHED_REPORTS = {
    "sidecar never ran": (lambda update_dir: (update_dir / "radios-state.json").unlink(), False),
    "report unparseable": (
        lambda update_dir: (update_dir / "radios-state.json").write_text("{", encoding="utf-8"),
        False,
    ),
    "report not an object": (
        lambda update_dir: (update_dir / "radios-state.json").write_text("[]", encoding="utf-8"),
        False,
    ),
    "current block malformed": (
        lambda update_dir: _radios_heartbeat(update_dir, current={"thread_enabled": "yes"}),
        False,
    ),
    "report stale": (lambda update_dir: _radios_heartbeat(update_dir, seen_at=_stale()), True),
}


@pytest.mark.parametrize("case", list(_UNVOUCHED_REPORTS))
async def test_without_a_current_thread_report_no_stick_can_be_chosen(api, case):
    """THE FAIL-SAFE. With no current report the bridge cannot tell which
    stick Thread is on, so it refuses to newly choose ANY stick - and says
    why, once, in `thread_refusal`, for the card to show under the select.

    Before this, every case here left both sticks selectable, and `PUT`
    accepted the MG24: on the maintainer's Pi a sidecar that had not
    reported yet was all it took to point zigpy at his live Thread radio.

    Fault to prove it: have `read_thread_stick` answer `known` whatever the
    report (the selectable and 503 assertions fail), or drop the
    `selection_refusal()` check from `PUT` (the 503s become 202s)."""
    client, update_dir, harness = api
    break_report, mg24_is_thread = _UNVOUCHED_REPORTS[case]
    break_report(update_dir)

    body = (await client.get("/api/zigbee/radio")).json()
    by_path = {stick["path"]: stick for stick in body["serial"]}
    assert body["thread_status"] == "unknown"
    assert body["thread_refusal"] == i18n.t("api.errors.zigbee_thread_unknown")
    assert [stick["selectable"] for stick in body["serial"]] == [False, False]
    assert by_path[MG24_PATH]["is_thread"] is mg24_is_thread
    assert by_path[ITEAD_PATH]["is_thread"] is False

    refused = await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})
    assert refused.status_code == 503
    assert refused.json()["detail"] == i18n.t("api.errors.zigbee_thread_unknown")
    mg24 = await client.put("/api/zigbee/radio", json={"path": MG24_PATH})
    # A stale report still names the MG24, and that refusal is the more
    # specific one; with no report at all the MG24 is refused as "unknown".
    assert mg24.status_code == (400 if mg24_is_thread else 503)
    assert harness.store.zigbee_settings.get().path is None
    assert harness.built == []


async def test_no_zigbee_stick_stays_choosable_without_a_thread_report(api):
    """The refusal covers sticks, never "No Zigbee stick": turning Zigbee
    off opens no port, and is the one change a user must always be able to
    make - a Zigbee stick they want released must not be held hostage to a
    sidecar that is not running.

    Fault to prove it: check `selection_refusal()` before `body.path is not
    None` in `PUT`."""
    client, update_dir, harness = api
    assert (await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})).status_code == 202
    (update_dir / "radios-state.json").unlink()

    assert (await client.put("/api/zigbee/radio", json={"path": None})).status_code == 202
    assert harness.store.zigbee_settings.get().path is None


@pytest.mark.parametrize("changing", ["job running", "request not picked up"])
async def test_while_a_radios_change_runs_no_stick_can_be_chosen(api, changing):
    """A fresh report is still no guarantee while the sidecar is moving
    Thread: `current` is re-read from `.env` step by step, so it can name
    the old stick while Thread is on its way to the one being chosen here.
    `request_radios` refuses a second request in exactly these two states,
    and this refuses a Zigbee choice in them too.

    Fault to prove it: leave `change_in_progress` out of
    `read_thread_stick` (the status reads `known` and the PUT is 202)."""
    client, update_dir, harness = api
    if changing == "job running":
        _radios_heartbeat(update_dir, id="job-1", phase="apply_thread")
    else:
        (update_dir / "radios-request.json").write_text(
            json.dumps({"id": "job-2", "thread": None, "bluetooth": {"adapter": 0}}),
            encoding="utf-8",
        )

    body = (await client.get("/api/zigbee/radio")).json()
    assert body["thread_status"] == "changing"
    assert body["thread_refusal"] == i18n.t("api.errors.zigbee_thread_changing")
    assert [stick["selectable"] for stick in body["serial"]] == [False, False]
    refused = await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})
    assert refused.status_code == 503
    assert refused.json()["detail"] == i18n.t("api.errors.zigbee_thread_changing")
    assert harness.built == []


async def test_a_current_report_carries_no_refusal(api):
    """The other side of the fail-safe, so it cannot pass by refusing
    everything always: a fresh, settled report makes `thread_status`
    `known`, `thread_refusal` `null`, and only the Thread stick unselectable.

    Fault to prove it: make `selection_refusal()` always answer."""
    client, _update_dir, _harness = api
    body = (await client.get("/api/zigbee/radio")).json()
    assert (body["thread_status"], body["thread_refusal"]) == ("known", None)
    assert {stick["path"]: stick["selectable"] for stick in body["serial"]} == {
        MG24_PATH: False,
        ITEAD_PATH: True,
    }


# --- Opening the STORED stick: `thread_lockout.open_refusal` --------------
#
# Asked by `ZigbeeSource.connect()` on every open - after a reboot, on each
# supervisor retry, on each apply - against the same two-stick host tree.


def _open_refusal(tmp_path: Path, path: str) -> str | None:
    from loxmatter.radios.thread_lockout import open_refusal

    message = open_refusal(
        path, update_dir=tmp_path / "update", host_dev=tmp_path / "dev", sys_root=tmp_path / "sys"
    )
    return None if message is None else message.key


async def test_a_stored_stick_is_not_opened_without_any_thread_report(api, tmp_path):
    """No report at all - the sidecar never ran, or the file is gone or
    damaged - and nothing says the stored stick is not Thread's, so it stays
    closed, with a reason. The supervisor retries, so it opens by itself on
    the first attempt after the sidecar reports.

    Fault to prove it: answer `None` when `thread.reported` is false."""
    _client, update_dir, _harness = api
    (update_dir / "radios-state.json").unlink()
    assert _open_refusal(tmp_path, ITEAD_PATH) == "api.errors.zigbee_open_thread_unknown"
    (update_dir / "radios-state.json").write_text("not json", encoding="utf-8")
    assert _open_refusal(tmp_path, ITEAD_PATH) == "api.errors.zigbee_open_thread_unknown"
    _radios_heartbeat(update_dir)
    assert _open_refusal(tmp_path, ITEAD_PATH) is None


async def test_a_stored_stick_still_opens_on_a_stale_report_after_a_reboot(api, tmp_path):
    """THE BOOT DECISION. After a reboot the bridge commonly starts before
    the sidecar's first pass, so the report on disk is stale - and it is
    also the last good report, kept on the same volume as the store. A
    working Zigbee installation must not go dark for that: the stored ITEAD
    stick opens, because the report names the MG24 as Thread's, not it.

    Fault to prove it: require `report_is_fresh` in `open_refusal` (the
    ITEAD stick is refused)."""
    _client, update_dir, _harness = api
    _radios_heartbeat(update_dir, seen_at=_stale())
    assert _open_refusal(tmp_path, ITEAD_PATH) is None


async def test_a_stored_stick_that_thread_now_runs_on_is_not_opened(api, tmp_path):
    """The Thread row refuses the Zigbee stick, but `.env` can be edited by
    hand: a stored Zigbee setting naming the stick Thread now runs on must
    not be opened, fresh report or stale. And the escape hatch holds here
    too - with Thread off, the same stick opens.

    Fault to prove it: drop the `is_thread_stick` check from
    `open_refusal`."""
    _client, update_dir, _harness = api
    assert _open_refusal(tmp_path, MG24_PATH) == "api.errors.zigbee_open_is_thread_stick"
    _radios_heartbeat(update_dir, seen_at=_stale())
    assert _open_refusal(tmp_path, MG24_PATH) == "api.errors.zigbee_open_is_thread_stick"
    _radios_heartbeat(
        update_dir, current={**_current(), "thread_enabled": False, "otbr_running": False}
    )
    assert _open_refusal(tmp_path, MG24_PATH) is None


# --- What the change does, and does not, touch ----------------------------


async def test_choosing_a_stick_does_not_touch_thread_or_bluetooth(api):
    """The null-half guarantee 2a-1 built, honoured completely: this setting
    never reaches `radios-request.json` at all, so there is no half that
    could be misread as "Thread off". No request file is written and no
    container is recreated.

    Fault to prove it: route the change through `request_radios`."""
    client, update_dir, _harness = api
    assert (await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})).status_code == 202
    assert not (update_dir / "radios-request.json").exists()


async def test_the_setting_survives_a_restart(api, tmp_path):
    """It lives in loxmatter's own `setting` table, so a container
    recreation - an ordinary update - keeps it.

    Fault to prove it: hold it on the source object in memory. The second
    `Store` below is what a recreated container opens, and it would find
    nothing."""
    client, _update_dir, _harness = api
    await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})

    reopened = Store(tmp_path / "t.sqlite")
    try:
        stored = reopened.zigbee_settings.get()
        assert stored.path == ITEAD_PATH
        # And with the parameters the fingerprint table supplied, not a
        # guess: the ITEAD V2 is an EZSP stick at 115200 with hardware flow
        # control.
        assert stored.radio_type == "ezsp"
        assert stored.saved_at is not None
    finally:
        reopened.close()


async def test_an_unknown_stick_is_accepted_with_an_explicit_radio_type(api, tmp_path):
    """Refusing to work with an unlisted stick would be worse than letting
    the user say what it is (design section 7). The Advanced disclosure
    supplies radio type and baud rate; the fingerprint supplies them when it
    can.

    Fault to prove it: reject a path with no fingerprint match."""
    client, _update_dir, harness = api
    # An unrecognised stick: the by-id name matches no row, so
    # `match_fingerprint` answers `None` even though the vid:pid is known.
    host_dev = tmp_path / "dev"
    unknown = "usb-Some_Other_CP210x_Bridge-if00"
    (host_dev / "serial" / "by-id" / unknown).symlink_to(Path("../..") / "ttyUSB1")

    body = (await client.get("/api/zigbee/radio")).json()
    entry = next(s for s in body["serial"] if s["path"].endswith(unknown))
    assert entry["fingerprint"] is None
    assert entry["selectable"] is True

    response = await client.put(
        "/api/zigbee/radio",
        json={
            "path": f"/dev/serial/by-id/{unknown}",
            "radio_type": "znp",
            "baudrate": 38400,
            "flow_control": "hardware",
        },
    )
    assert response.status_code == 202
    stored = harness.store.zigbee_settings.get()
    assert (stored.radio_type, stored.baudrate, stored.flow_control) == (
        "znp",
        38400,
        "hardware",
    )


async def test_a_recognised_stick_ignores_the_advanced_fields(api):
    """The table is the measured answer. A stale value left in a
    disclosure the user never opened must not silently override it and open
    a coordinator at the wrong speed.

    Fault to prove it: let the request's own fields win whenever they are
    present."""
    client, _update_dir, harness = api
    await client.put(
        "/api/zigbee/radio",
        json={"path": ITEAD_PATH, "radio_type": "deconz", "baudrate": 9600},
    )
    stored = harness.store.zigbee_settings.get()
    assert stored.radio_type == "ezsp"
    assert stored.baudrate != 9600


async def test_a_path_that_is_not_a_detected_stick_is_refused(api):
    """Same rule `POST /api/radios` already applies to the Thread device:
    the bridge validates what it can see.

    Fault to prove it: accept any string. A typo then becomes a zigpy
    startup failure with a confusing message instead of a 400."""
    client, _update_dir, harness = api
    response = await client.put(
        "/api/zigbee/radio", json={"path": "/dev/serial/by-id/usb-Typo-if00"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]
    assert harness.store.zigbee_settings.get().path is None


async def test_clearing_the_setting_is_never_refused_and_releases_the_stick(api):
    """ "No Zigbee stick" must actually release the port - otherwise the
    stick stays locked by this process and a second loxmatter instance, or
    a deliberate switch to another tool, fails with EBUSY for no visible
    reason. And clearing can never be refused, or a user whose stick has
    been reassigned to Thread could not get out of the conflict.

    Fault to prove it: only clear the stored value."""
    client, _update_dir, harness = api
    await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})
    await harness.holder.wait_for_apply()
    source = harness.built[0]
    await asyncio.wait_for(source.connected_event.wait(), timeout=2)

    assert (await client.put("/api/zigbee/radio", json={"path": None})).status_code == 202
    await harness.holder.wait_for_apply()

    assert source.disconnects == 1
    assert harness.store.zigbee_settings.get().path is None
    assert harness.holder.current() is None


# --- The answer must be fast, and honest ----------------------------------


async def test_applying_a_new_stick_answers_at_once_and_reconnects_in_the_background(api):
    """The point of Spec Correction 3: no container is recreated, so the
    Matter link and every Matter device are untouched, and the request that
    asked for the change survives to answer.

    It answers 202 WITHOUT waiting for the radio. A first-ever Zigbee
    configuration is a 9-15 s quirks warm-up on a Pi, plus
    `startup(auto_form=True)`, plus a possible 7.5 s timeout on a silent
    port - all of which would otherwise sit on this request with nothing on
    screen moving. The reconnection runs in the background and its progress
    is readable from `GET /api/zigbee/radio`.

    Fault to prove it: `await` the reconnection inside the handler. The
    test's fake source blocks for longer than the request's own timeout, so
    the PUT never returns - which is precisely what a Pi user would see."""
    client, _update_dir, harness = api
    harness.connect_delay[0] = 5.0  # far longer than the timeout below

    response = await asyncio.wait_for(
        client.put("/api/zigbee/radio", json={"path": ITEAD_PATH}), timeout=1.0
    )

    assert response.status_code == 202
    # The reconnection really does happen, it just happens afterwards.
    await harness.holder.wait_for_apply()
    await asyncio.sleep(0)
    assert harness.built[0].connect_started is True
    assert harness.built[0].connected is False, "still in flight, as designed"
    assert harness.sources.get("zigbee") is harness.built[0]


async def test_the_apply_request_does_not_wait_for_the_quirks_warm_up(api):
    """The Global Constraint, stated three times in this plan: the warm-up
    never sits on a request path. This is the one route that could put it
    there, because it is the only one that can cause a first-ever connect.

    `ensure_quirks_loaded()` lives INSIDE `ZigbeeSource.connect()`, so what
    is measured is the request's own duration against that call's cost. The
    background task does get its first turn while the response travels back
    - that is the point, it runs CONCURRENTLY - so "connect has not been
    entered" is not the distinguishing fact and an earlier version of this
    test that asserted it was measuring nothing. The distinguishing fact is
    that the request finished in a fraction of the time the connection
    takes, with the connection still outstanding.

    Fault to prove it: call `ensure_quirks_loaded()` from the handler, or
    await `source.connect()` there - either puts the whole cost on the
    request, and `wait_for` below expires."""
    client, _update_dir, harness = api
    harness.connect_delay[0] = 5.0

    started = time.monotonic()
    response = await asyncio.wait_for(
        client.put("/api/zigbee/radio", json={"path": ITEAD_PATH}), timeout=1.0
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 202
    assert elapsed < 1.0, "the request paid part of the radio's cost"
    # The cost the handler did not pay is still outstanding, and visibly so.
    assert harness.built[0].connected is False
    body = (await client.get("/api/zigbee/radio")).json()
    assert body["progress"]["state"] == "loading_quirks"


async def test_the_swap_window_reports_applying_and_never_idle(api):
    """The contract Task 13's card is built against, checked where the card
    reads it: the 202's own body and the `GET` that follows it.

    `_swap` releases the old source before it builds the new one, so for the
    length of that window there is no `ZigbeeSource` to ask - on a Pi, a
    bellows `app.shutdown(db=True)` plus `current_thread_channel()`'s
    up-to-5 s HTTP timeout. Both readings below used to say `idle`, the one
    state that means "no radio is configured in this installation", and the
    card's rule is to poll while the state is a working one: it would have
    decided nothing was happening and never polled at all. "A running job
    the user cannot tell from a dead one" is the Global Constraint this
    plan states three times.

    Every other progress test in this file calls `wait_for_apply()` first
    and therefore looks only AFTER the window. This one holds the window
    open and looks inside it.

    Fault to prove it: drop the `self._applying` branch from
    `ZigbeeRuntime.progress()` - both readings go back to `idle`. Or set
    the marker inside `_apply_in_background` instead of synchronously in
    `apply()`: `ensure_future` runs no line of the coroutine before the
    handler returns, so the 202 alone goes back to `idle`."""
    client, _update_dir, harness = api
    # A stick is already configured and connected, so `idle` cannot be
    # confused with "this installation never had a radio".
    await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})
    await harness.holder.wait_for_apply()
    await asyncio.sleep(0)
    assert (await client.get("/api/zigbee/radio")).json()["progress"]["state"] == "connected"

    harness.hold_the_build.clear()  # the swap now stops mid-window
    put = await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})

    assert put.status_code == 202
    assert put.json()["progress"]["state"] == "applying"
    # And it keeps saying so for as long as the window lasts, across as many
    # polls as the card cares to make.
    for _ in range(3):
        body = (await client.get("/api/zigbee/radio")).json()
        assert body["progress"]["state"] == "applying"
        assert body["configured_path"] == ITEAD_PATH
    # The window really is the one with no source behind it.
    assert harness.holder.current() is None
    assert harness.built[0].disconnects == 1

    harness.hold_the_build.set()
    await harness.holder.wait_for_apply()
    await asyncio.sleep(0)
    assert (await client.get("/api/zigbee/radio")).json()["progress"]["state"] != "applying"


async def test_the_progress_of_a_running_attempt_is_readable(api):
    """What makes the asynchronous answer honest rather than merely fast: a
    PUT that returns immediately and reports nothing afterwards is a job the
    user cannot distinguish from a dead one, which is the 2a-1 lesson this
    plan records as a Global Constraint.

    Fault to prove it: return a bare `connected` boolean. A 15 s warm-up
    then reads as "not connected", identical to a stick that is broken."""
    client, _update_dir, harness = api
    harness.connect_delay[0] = 5.0
    await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})
    await harness.holder.wait_for_apply()
    await asyncio.sleep(0)

    body = (await client.get("/api/zigbee/radio")).json()
    assert body["progress"]["state"] == "loading_quirks"
    assert body["configured_path"] == ITEAD_PATH

    # And the failing case says how often, and why - the supervisor never
    # gives up, so the card must be able to say so.
    harness.built[0].set_progress(
        "failed", attempts=4, error=i18n.Message.of("api.errors.zigbee_stick_missing")
    )
    body = (await client.get("/api/zigbee/radio")).json()
    assert body["progress"]["state"] == "failed"
    assert body["progress"]["attempts"] == 4
    assert body["progress"]["error"] == i18n.t("api.errors.zigbee_stick_missing")


async def test_a_configured_stick_that_is_gone_is_reported_as_missing(api, tmp_path):
    """The in-process design's worst failure mode, and the one nothing else
    surfaces: the stored by-id path names a node that will never come back -
    the stick was pulled, or it came back under a different name - so the
    supervisor retries forever on its 60 s ceiling and the card says only
    "not connected". The user has no way to learn that the device they
    chose is simply absent.

    Sub-project 2a-1 solved exactly this for Thread: `GET /api/radios`
    returns `thread_device_present`, computed by `match_current_device`
    against the live scan. This is the same answer for the same question.

    Fault to prove it: report only the stored path. A stick that was
    unplugged is then indistinguishable from one that is present and
    refusing to open, and the two need opposite actions from the user."""
    client, _update_dir, _harness = api
    await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})
    body = (await client.get("/api/zigbee/radio")).json()
    assert body["configured_device_present"] is True

    (tmp_path / "dev" / "serial" / "by-id" / REAL_ITEAD).unlink()
    body = (await client.get("/api/zigbee/radio")).json()
    assert body["configured_path"] == ITEAD_PATH
    assert body["configured_device_present"] is False


async def test_nothing_configured_is_not_an_error(api):
    """A fresh install: no path, `configured_device_present` `False`, and
    an `idle` progress. None of that is a failure and none of it may read
    as one."""
    client, _update_dir, _harness = api
    body = (await client.get("/api/zigbee/radio")).json()
    assert body["configured_path"] is None
    assert body["configured_device_present"] is False
    assert body["progress"]["state"] == "idle"


async def test_the_coordinator_firmware_is_reported_once_the_stick_has_said_it(api):
    """`coordinator` carries what the current stick reported about itself on
    its last successful connect - the firmware above all, which bellows
    logs only at DEBUG. `null` while there is no source, and read from the
    CURRENT source on every answer, so a radio change cannot leave the old
    stick's firmware on the card.

    Fault to prove it: leave `coordinator` out of the answer, or capture it
    once instead of asking `zigbee_runtime.coordinator()` per request."""
    from loxmatter.zigbee.source import CoordinatorInfo

    client, _update_dir, harness = api
    assert (await client.get("/api/zigbee/radio")).json()["coordinator"] is None

    assert (await client.put("/api/zigbee/radio", json={"path": ITEAD_PATH})).status_code == 202
    await harness.holder.wait_for_apply()
    harness.built[-1].coordinator_info = CoordinatorInfo(
        radio_type="ezsp", manufacturer="ITEAD", model="Dongle-E", firmware="7.4.4.0 build 0"
    )
    assert (await client.get("/api/zigbee/radio")).json()["coordinator"] == {
        "radio_type": "ezsp",
        "manufacturer": "ITEAD",
        "model": "Dongle-E",
        "firmware": "7.4.4.0 build 0",
    }

    assert (await client.put("/api/zigbee/radio", json={"path": None})).status_code == 202
    await harness.holder.wait_for_apply()
    assert (await client.get("/api/zigbee/radio")).json()["coordinator"] is None


async def test_the_stored_radio_parameters_are_reported_back(api):
    """The card's Advanced disclosure shows what an unrecognised stick is
    being opened WITH. Without these two keys it could only show
    `DEFAULT_UNKNOWN` - EZSP at 115200 - while the bridge was retrying the
    stick as ZNP at 38400 (measured in the card's browser harness, 12
    September 2026).

    Stored directly rather than through `PUT`: both sticks in this fixture
    are recognised, and for a recognised stick `settings_for_path` ignores
    the Advanced values, so no `PUT` here could store a non-default pair.
    ZNP at 38400 is chosen because it differs from the defaults in both
    fields - a route that echoed `DEFAULT_UNKNOWN` would fail on each.

    Fault to prove it: drop `configured_radio_type`/`configured_baudrate`
    from the `GET` body, or report `DEFAULT_UNKNOWN`'s values instead of the
    stored ones."""
    client, _update_dir, harness = api
    harness.store.zigbee_settings.save(
        replace(
            settings_for_path(None, []),
            path="/dev/serial/by-id/usb-Some_Other_CP210x_Bridge-if00",
            radio_type="znp",
            baudrate=38400,
        )
    )
    body = (await client.get("/api/zigbee/radio")).json()
    assert body["configured_radio_type"] == "znp"
    assert body["configured_baudrate"] == 38400


async def test_thread_cannot_be_put_on_the_zigbee_stick(api):
    """The reverse half of the exclusion. `PUT /api/zigbee/radio` refuses
    the Thread stick; until this, `POST /api/radios` accepted the Zigbee
    stick for Thread without a word, and the sidecar would have recreated
    the border router on a port zigpy holds - two failing radios.

    The stored setting spells the stick `/dev/ttyUSB1`, the way
    `--zigbee-device` may have seeded it, while the card sends the by-id
    path: two strings, one stick. A string compare would let it through.

    And `GET /api/radios` says so up front (`is_zigbee`), so the Thread row
    can show the stick as taken instead of letting the user find out after
    the confirmation dialog.

    Fault to prove it: drop the `_zigbee_stick` check from `post_radios`
    (the POST is accepted), or compare the path strings (the by-id request
    no longer matches the stored `/dev/ttyUSB1`)."""
    client, update_dir, harness = api
    (update_dir / "state.json").write_text(
        json.dumps(
            {
                "id": None,
                "phase": "idle",
                "updater_seen_at": _now(),
                "updater_stack_host_path": "/home/pi/stack",
            }
        ),
        encoding="utf-8",
    )
    harness.store.zigbee_settings.save(replace(settings_for_path(None, []), path="/dev/ttyUSB1"))

    listed = (await client.get("/api/radios")).json()
    flags = {radio["path"]: radio["is_zigbee"] for radio in listed["serial"]}
    assert flags == {ITEAD_PATH: True, MG24_PATH: False}

    body = {"thread": {"enabled": True, "device": ITEAD_PATH}, "bluetooth": None}
    refused = await client.post("/api/radios", json=body)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == i18n.t("api.radios.fail_zigbee_stick")
    assert not (update_dir / "radios-request.json").exists()

    allowed = await client.post(
        "/api/radios", json={"thread": {"enabled": True, "device": MG24_PATH}, "bluetooth": None}
    )
    assert allowed.status_code == 202, allowed.text


# --- Pairing ---------------------------------------------------------------
#
# These drive the REAL `ZigbeeSource` rather than a stand-in for it. The
# pairing routes are thin on purpose - they overlay two facts onto a row and
# serialise it - and a fake source would let every one of those facts be
# whatever the test wanted, which is precisely how a suite comes to be green
# about behaviour that cannot happen. What is faked here is one layer lower
# and is the layer that needs a radio: zigpy's `ControllerApplication` and
# its devices, in the smallest shape the source actually reaches into.
#
# `tests/zigbee/fakes.py` is the fuller version of the same idea and cannot
# be imported here: `tests/api` is run on its own, so nothing puts
# `tests/zigbee` on the path.

ON_OFF_CLUSTER = 0x0006
ON_OFF_ATTRIBUTE = 0x0000
LAMP_IEEE = "00:12:4b:00:1c:a1:b2:c3"
SENSOR_IEEE = "00:15:8d:00:02:aa:bb:cc"


@dataclass(frozen=True)
class _StubNodeDescriptor:
    """`zigpy.zdo.types.NodeDescriptor`, as far as anything here reads it.

    All THREE flags the Zigbee package reads, not only the one the pairing
    row needs: `availability.py` asks a device for
    `is_receiver_on_when_idle` and `is_coordinator` from its background
    sweep, and a stub missing either of them turns into an `AttributeError`
    from a task nothing in the test is looking at. Measured: it happened
    here, and it surfaced only when an unrelated fault injection changed
    the timing enough for the sweep to run."""

    is_mains_powered: bool
    is_receiver_on_when_idle: bool = True
    is_coordinator: bool = False


class _StubCluster:
    """One ZCL cluster, read the way the source reads one: through
    `attributes` (id -> definition) and `get(definition)`.

    The DEFINITION and never the bare id - `Cluster.get(int)` raises for a
    duplicate attribute id on the real thing, which is why the source hands
    it the definition, and a fake that accepted an int here would hide that
    the day somebody changed it back."""

    def __init__(self, cluster_id: int, values: dict[int, Any]) -> None:
        self.cluster_id = cluster_id
        self.attributes = {attribute_id: object() for attribute_id in values}
        self._by_definition = {
            self.attributes[attribute_id]: value for attribute_id, value in values.items()
        }

    def get(self, definition: Any, default: Any = None) -> Any:
        return self._by_definition.get(definition, default)


class _StubEndpoint:
    def __init__(self, endpoint_id: int, *, device_type: int, clusters: list[_StubCluster]) -> None:
        self.endpoint_id = endpoint_id
        self.profile_id = 0x0104
        self.device_type = device_type
        self.in_clusters = {cluster.cluster_id: cluster for cluster in clusters}


class _StubDevice:
    """`zigpy.device.Device`, in the shape `ZigbeeSource` reaches into."""

    def __init__(
        self,
        ieee: str,
        *,
        manufacturer: str = "IKEA of Sweden",
        model: str = "TRADFRI bulb",
        mains: bool = True,
        quirk_applied: bool = False,
        endpoints: list[_StubEndpoint] | None = None,
    ) -> None:
        self.ieee = ieee
        self.nwk = 0x1234
        self.manufacturer = manufacturer
        self.model = model
        self.node_desc = _StubNodeDescriptor(mains, is_receiver_on_when_idle=mains)
        self.last_seen = time.time()
        # A device that failed its interview is NOT initialized, which is
        # what makes a Retry a real second interview - see
        # `schedule_initialize`.
        self.is_initialized = True
        self.initializations = 0
        self.application: Any = None
        self._endpoints = list(endpoints or [])
        self.endpoints: dict[int, Any] = {0: object()}
        self.endpoints.update({endpoint.endpoint_id: endpoint for endpoint in self._endpoints})
        if quirk_applied:
            # The attribute `zha.quirks.DeviceRegistry.resolve` sets on a
            # device it transformed.
            self._quirk_registry_entry = object()

    @property
    def non_zdo_endpoints(self) -> list[_StubEndpoint]:
        return list(self._endpoints)

    def schedule_initialize(self) -> None:
        """zigpy's own entry point for (re-)interviewing a device: it
        re-announces an already-initialized one and otherwise starts an
        interview as a task."""
        if self.is_initialized:
            if self.application is not None:
                self.application.device_initialized(self)
            return
        self.initializations += 1


class _StubApplication:
    """`zigpy.application.ControllerApplication`, as far as the source
    reaches into it."""

    def __init__(self, devices: list[_StubDevice] | None = None) -> None:
        self.devices = {device.ieee: device for device in devices or []}
        for device in self.devices.values():
            device.application = self
        self.listeners: list[Any] = []
        self.permits: list[tuple[int, Any]] = []
        self.removed: list[str] = []
        self.permit_error: BaseException | None = None

    def add_listener(self, listener: Any) -> int:
        self.listeners.append(listener)
        return id(listener)

    def remove_listener(self, listener: Any) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    async def startup(self, *, auto_form: bool = False) -> None:
        return None

    async def shutdown(self, *, db: bool = True) -> None:
        return None

    async def permit(self, time_s: int = 60, node: Any = None) -> None:
        if self.permit_error is not None:
            raise self.permit_error
        self.permits.append((time_s, node))

    async def remove(self, ieee: Any, remove_children: bool = True, rejoin: bool = False) -> None:
        """zigpy deletes the device from its database whether or not the
        leave request is ever delivered, and announces it at once. It never
        waits for a confirmation, and this stub deliberately never sends
        one - a sleeping device would not either."""
        self.removed.append(str(ieee))
        device = self.devices.pop(str(ieee), None)
        if device is not None:
            self.listener_event("device_removed", device)

    def listener_event(self, method_name: str, *args: Any) -> None:
        """`zigpy.util.ListenableMixin.listener_event`: every listener, by
        METHOD NAME, with each one's exception caught and logged rather
        than propagated (verified against zigpy 2.2.0).

        The swallowing is the property worth imitating exactly - it is what
        makes a misspelled event name the most silently dead thing in this
        package, and a fake that let the exception through would make a
        typo look like a loud failure instead of the quiet one it is."""
        for listener in list(self.listeners):
            method = getattr(listener, method_name, None)
            if method is None:
                continue
            try:
                method(*args)
            except Exception:
                logging.getLogger(__name__).debug(
                    "listener %r raised on %s", listener, method_name, exc_info=True
                )

    def device_initialized(self, device: _StubDevice) -> None:
        self.devices[device.ieee] = device
        device.application = self
        self.listener_event("device_initialized", device)

    def join(self, device: _StubDevice) -> None:
        self.devices[device.ieee] = device
        device.application = self
        self.listener_event("device_joined", device)

    def init_failure(self, device: _StubDevice) -> None:
        self.devices[device.ieee] = device
        device.application = self
        device.is_initialized = False
        self.listener_event("device_init_failure", device)


def _lamp(ieee: str = LAMP_IEEE, **fields: Any) -> _StubDevice:
    """An on/off lamp with one readable attribute, so its snapshot carries a
    real Matter path and `register_signals` has a row to create."""
    return _StubDevice(
        ieee,
        endpoints=[
            _StubEndpoint(
                1,
                device_type=0x0100,
                clusters=[_StubCluster(ON_OFF_CLUSTER, {ON_OFF_ATTRIBUTE: False})],
            )
        ],
        **fields,
    )


@dataclass
class _PairingHarness:
    store: Store
    holder: ZigbeeRuntime
    source: ZigbeeSource
    app: _StubApplication
    runtime: _AttachableRuntime


@pytest.fixture
async def pairing(tmp_path, no_invoke, monkeypatch) -> AsyncIterator[Any]:
    """A real `ZigbeeSource`, connected to a stubbed application, behind the
    real routes.

    No supervisor: `ZigbeeRuntime` takes one as a parameter, and a test that
    let the real one run would have a background loop reconnecting a radio
    underneath every assertion. The source is connected here instead, once,
    through its own public `connect()`."""
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    host_dev, sys_root = _host_two_sticks(tmp_path)
    store = Store(tmp_path / "t.sqlite")
    runtime = _AttachableRuntime(store)
    sources = Sources([_Matter()])  # type: ignore[list-item]
    app = _StubApplication()

    # The real warm-up imports 462 quirk modules and costs seconds. Every
    # Zigbee suite replaces it for that reason; nothing here depends on the
    # registry.
    async def no_warm_up() -> float:
        return 0.0

    monkeypatch.setattr(source_module, "ensure_quirks_loaded", no_warm_up)

    async def application_factory(config: dict[str, Any]) -> Any:
        return app

    source = ZigbeeSource(
        path=ITEAD_PATH,
        fingerprint=Fingerprint(
            name="ITEAD V2", radio_type="ezsp", baudrate=115200, flow_control="hardware"
        ),
        database=tmp_path / "zigbee.sqlite",
        application_factory=application_factory,
    )

    async def build(settings: Any) -> Any:
        return None if settings.path is None else source

    async def never_supervise(*args: Any, **kwargs: Any) -> None:
        return None

    holder = ZigbeeRuntime(
        store,
        runtime,  # type: ignore[arg-type]
        sources,
        build_source=build,
        supervise=never_supervise,
    )
    settings = settings_for_path(ITEAD_PATH, scan_serial(host_dev, sys_root))
    store.zigbee_settings.save(settings)
    await holder.open()
    await source.connect()
    # The same subscription `sources.supervisor.attach()` makes in
    # production, and the reason it is here: `follow` is what reaches
    # `Runtime.on_node_snapshot`, and a source with no handler would
    # deliver into a void - an adoption that never seeded its device would
    # then look exactly like one that did.
    await source.subscribe(partial(store.device_id_for, "zigbee"), runtime)  # type: ignore[arg-type]

    fastapi_app = build_app(
        store,
        no_invoke,
        runtime,
        sources=sources,
        update_dir=update_dir,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
        zigbee_runtime=holder,
    )
    _radios_heartbeat(update_dir)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        await authenticate(store, client)
        yield client, _PairingHarness(store, holder, source, app, runtime)
    await source.disconnect()
    await holder.stop()
    store.close()


async def _rows(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    response = await client.get("/api/zigbee/pairing")
    assert response.status_code == 200, response.text
    return {row["ieee"]: row for row in response.json()["rows"]}


async def test_the_permit_window_is_the_protocol_maximum_and_has_a_server_side_end(pairing):
    """254 s is the maximum zigpy will accept - it asserts
    `0 <= t <= 254` - and the response carries the END TIMESTAMP, not the
    duration, so a page that is reloaded halfway through still counts down
    to the truth rather than restarting at 254.

    Fault to prove it: return the duration and let the page count from
    there.

    The reload is the half that matters and it is checked here: the second
    `GET` reports the SAME end the `POST` did, not a fresh window.

    **The second between the two reads is load-bearing**, and it is the
    reason this test sleeps at all. `permit_until` is reported to the
    second, so a route that recomputed the end on every read - which is
    what "the page counts from the duration" is, moved to the server -
    would answer with a string identical to the `POST`'s for as long as
    both land inside the same second. Measured: with that fault injected
    and no sleep here, this test passed. A reload one second later is the
    smallest input that tells a pinned end from a recomputed one."""
    client, harness = pairing
    before = datetime.now(UTC)

    response = await client.post("/api/zigbee/permit", json={"duration": 254})

    assert response.status_code == 200, response.text
    permit_until = response.json()["permit_until"]
    assert harness.app.permits == [(254, None)]
    ends = datetime.fromisoformat(permit_until)
    assert 253 <= (ends - before).total_seconds() <= 256, permit_until

    # What a reloaded page reads. The same end, to the second - not a new
    # window, and not a duration it would have to start counting from now.
    await asyncio.sleep(1.1)
    reloaded = (await client.get("/api/zigbee/pairing")).json()
    assert reloaded["permit_until"] == permit_until


async def test_no_unlimited_join_mode_is_offered(pairing):
    """Z2M REMOVED its permanent permit_join option in 2.0 as a security
    concern and sometimes unstable. A network left open forever is one any
    passing device can join.

    Fault to prove it: accept `duration: 0xFF` as "forever".

    `0xFF` is the traditional spelling of it, and it is refused as an
    out-of-range value like any other - in the SCHEMA, so it never reaches
    zigpy's own `assert`, which under `python -O` is no check at all."""
    client, harness = pairing

    for forever in (0xFF, 255, 3600, -1):
        response = await client.post("/api/zigbee/permit", json={"duration": forever})
        assert response.status_code == 422, (forever, response.text)

    assert harness.app.permits == [], "the radio was asked to open anyway"
    assert (await client.get("/api/zigbee/pairing")).json()["permit_until"] is None


async def test_stopping_closes_the_window_immediately(pairing):
    """`permit(0)`. Leaving the tab does the same - ZHA's failure to close
    its window is a standing complaint.

    Fault to prove it: ignore a duration of 0."""
    client, harness = pairing
    await client.post("/api/zigbee/permit", json={"duration": 254})
    assert (await client.get("/api/zigbee/pairing")).json()["permit_until"] is not None

    response = await client.post("/api/zigbee/permit", json={"duration": 0})

    assert response.status_code == 200, response.text
    assert harness.app.permits[-1] == (0, None), "the radio was never told to close"
    assert response.json()["permit_until"] is None
    assert (await client.get("/api/zigbee/pairing")).json()["permit_until"] is None


async def test_a_row_is_keyed_by_ieee_and_carries_what_the_tab_shows(pairing):
    """One row per device, keyed by IEEE (design 3.1).

    Fault to prove it: key rows by NWK. A rejoining device then appears
    twice, once under each address it has had."""
    client, harness = pairing
    await client.post("/api/zigbee/permit", json={"duration": 254})
    lamp = _lamp()
    harness.app.join(lamp)
    harness.app.device_initialized(lamp)

    # The same device again under a new short address, which is what a
    # rejoin gives it.
    rejoined = _lamp()
    rejoined.nwk = 0x4321
    harness.app.device_initialized(rejoined)

    # The LIST, not a dict keyed by IEEE: two rows for one device would
    # collapse into one when indexed by the very field under test, and the
    # duplicate is the whole failure - measured, with the fault injected.
    body = (await client.get("/api/zigbee/pairing")).json()
    assert [row["ieee"] for row in body["rows"]] == [LAMP_IEEE]
    rows = await _rows(client)
    assert rows[LAMP_IEEE]["state"] == "ready"
    assert rows[LAMP_IEEE]["discovered"] is False


async def test_a_device_discovered_without_a_join_window_is_not_reported_as_just_joined(pairing):
    """zigpy interviews devices of an ADOPTED network on its own
    (`_discover_unknown_device`), so devices can appear when nobody opened a
    window. Telling the user "a device joined just now" would be false.

    Fault to prove it: report every new device as a join."""
    client, harness = pairing
    stranger = _lamp("00:12:4b:00:1c:00:00:11")
    harness.app.join(stranger)

    await client.post("/api/zigbee/permit", json={"duration": 254})
    invited = _lamp("00:12:4b:00:1c:00:00:12")
    harness.app.join(invited)

    rows = await _rows(client)
    assert rows[stranger.ieee]["discovered"] is True
    assert rows[invited.ieee]["discovered"] is False


async def test_an_interview_that_fails_says_so_and_offers_retry_and_remove(pairing):
    """ZHA has NO failure state at all - a hung interview sits on "Starting
    interview" forever (HA core issues 124114, 99497, 123136, 162426). That
    is the single thing this tab exists to do better.

    Fault to prove it: drop the `device_init_failure` handler.

    Both actions the failed row offers are exercised, because a failure
    state with no way out of it is a dead end rather than an improvement."""
    client, harness = pairing
    await client.post("/api/zigbee/permit", json={"duration": 254})
    sensor = _StubDevice(SENSOR_IEEE, manufacturer="", model="", mains=False)
    harness.app.join(sensor)
    assert (await _rows(client))[SENSOR_IEEE]["state"] == "joined"

    harness.app.init_failure(sensor)

    assert (await _rows(client))[SENSOR_IEEE]["state"] == "failed"

    # Retry asks the device again and puts the row back on the clock.
    retry = await client.post(f"/api/zigbee/pairing/{SENSOR_IEEE}/retry")
    assert retry.status_code == 202, retry.text
    assert sensor.initializations == 1
    assert (await _rows(client))[SENSOR_IEEE]["state"] == "interviewing"

    # And Remove is there for the device that never answers at all.
    removed = await client.delete(f"/api/zigbee/pairing/{SENSOR_IEEE}")
    assert removed.status_code == 204
    assert harness.app.removed == [SENSOR_IEEE]
    assert await _rows(client) == {}


async def test_a_row_with_no_progress_becomes_stuck_and_names_the_real_cause(pairing):
    """60 s for a mains device, 90 s for a battery one. A stuck row is NOT
    an error row: it keeps waiting and keeps offering Retry and Remove,
    because the usual cause is a battery device that fell asleep and the
    usual fix is pressing its button - which the copy says.

    Fault to prove it: mark it failed instead. The user then removes a
    device that was about to finish.

    The two thresholds are both measured, and against each other: at 75 s a
    mains device is stuck and a battery one is not, which is the only input
    that tells one threshold from one number used for both."""
    client, harness = pairing
    await client.post("/api/zigbee/permit", json={"duration": 254})
    mains = _lamp("00:12:4b:00:1c:00:00:21")
    battery = _StubDevice("00:15:8d:00:02:00:00:22", mains=False)
    harness.app.join(mains)
    harness.app.join(battery)

    rows = await _rows(client)
    assert rows[mains.ieee]["state"] == "joined"
    assert rows[battery.ieee]["state"] == "joined"

    # 75 s of no progress: past the mains threshold, short of the battery
    # one. Written onto the rows rather than slept for, because the two
    # thresholds are a minute and a half of wall clock.
    stalled = (datetime.now(UTC) - timedelta(seconds=75)).isoformat(timespec="microseconds")
    harness.source._pairing = {
        ieee: replace(row, changed_at=stalled) for ieee, row in harness.source._pairing.items()
    }

    rows = await _rows(client)
    assert rows[mains.ieee]["state"] == "stuck"
    assert rows[battery.ieee]["state"] == "joined", "a sleeping sensor accused 15 s early"

    # A stuck row is not an error row: both actions are still there, and
    # Retry restarts the clock rather than the row being written off.
    assert (await client.post(f"/api/zigbee/pairing/{mains.ieee}/retry")).status_code == 202
    assert (await _rows(client))[mains.ieee]["state"] == "interviewing"

    # Past 90 s the battery device is stuck too - the threshold is longer,
    # not absent.
    long_stalled = (datetime.now(UTC) - timedelta(seconds=95)).isoformat(timespec="microseconds")
    harness.source._pairing[battery.ieee] = replace(
        harness.source._pairing[battery.ieee], changed_at=long_stalled
    )
    assert (await _rows(client))[battery.ieee]["state"] == "stuck"


async def test_the_quirk_hint_reports_what_zigpy_actually_resolved(pairing):
    """The resolved device carries `_quirk_registry_entry`. This is
    loxmatter's equivalent of Z2M's "Unsupported" badge, and it explains
    missing values before the user asks - users routinely confuse a failed
    interview with an unsupported device, so the two are shown as different
    things in different places.

    Fault to prove it: report "quirk applied" unconditionally."""
    client, harness = pairing
    quirked = _lamp("00:12:4b:00:1c:00:00:31", quirk_applied=True)
    plain = _lamp("00:12:4b:00:1c:00:00:32")
    harness.app.device_initialized(quirked)
    harness.app.device_initialized(plain)

    rows = await _rows(client)
    assert rows[quirked.ieee]["quirk_applied"] is True
    assert rows[plain.ieee]["quirk_applied"] is False
    # And it is a different thing in a different place from a failure: the
    # unquirked device is perfectly ready.
    assert rows[plain.ieee]["state"] == "ready"


async def test_the_name_is_prefilled_from_manufacturer_and_model_not_the_ieee(pairing):
    """Z2M prefills the IEEE, which nobody keeps.

    Fault to prove it: prefill the IEEE."""
    client, harness = pairing
    lamp = _lamp()
    harness.app.device_initialized(lamp)

    row = (await _rows(client))[LAMP_IEEE]

    assert row["suggested_name"] == "IKEA of Sweden TRADFRI bulb"
    assert LAMP_IEEE not in row["suggested_name"]


async def test_naming_a_ready_row_adopts_the_device_with_its_room(pairing):
    """Naming a device is what makes it one of the bridge's own: zigpy knows
    it the moment its interview finishes, but nothing in loxmatter's store
    does until the user says what to call it - which keeps a device somebody
    is still deciding about out of the device list, the export template and
    Loxone.

    Fault to prove it: register the device but skip `register_signals` /
    `follow`. The tile then exists with no signals under it, which is the
    shape of the bug `commission_device` records for Matter. Both halves
    were injected separately, and both failed this test.

    **What this does NOT measure**, recorded rather than left to be
    rediscovered: `seed_even_without_new_paths`. Dropping it was injected
    too and this test stayed green, correctly - a device being adopted for
    the first time has no delivered paths yet, so a plain `follow` seeds it
    anyway. The flag only separates the two on a SECOND adoption, where
    nothing is owed either. It is kept because the sibling Matter route
    keeps it for a case that route can genuinely reach; here it is
    belt-and-braces, and no test should claim otherwise."""
    client, harness = pairing
    lamp = _lamp()
    harness.app.device_initialized(lamp)
    assert (await _rows(client))[LAMP_IEEE]["device_id"] is None

    response = await client.patch(
        f"/api/zigbee/pairing/{LAMP_IEEE}", json={"name": "Reading lamp", "room": "Study"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["room"]) == ("Reading lamp", "Study")
    device_id = body["device_id"]
    assert device_id is not None
    stored = harness.store.device(device_id)
    assert (stored.label, stored.room, stored.technology) == ("Reading lamp", "Study", "zigbee")
    # The signals exist - a named device with no signals is a tile with
    # nothing under it.
    assert [signal.key for signal in harness.store.signals(device_id)]
    # And the runtime was told about it, which is what `follow` is for: the
    # signal rows alone are rows with no values in them, and a Zigbee lamp
    # that is simply off never reports anything by itself.
    assert [seeded for seeded, _ in harness.runtime.snapshots] == [device_id]

    # A row that is not ready is refused rather than half-registered.
    sensor = _StubDevice("00:15:8d:00:02:00:00:41", mains=False)
    harness.app.join(sensor)
    refused = await client.patch(
        f"/api/zigbee/pairing/{sensor.ieee}", json={"name": "Door", "room": ""}
    )
    assert refused.status_code == 409, refused.text


async def test_removal_forgets_the_device_even_when_the_leave_is_never_delivered(pairing):
    """`remove()` deletes the device from zigpy's database whether or not
    the leave request arrives, and nothing stops the device rejoining. The
    confirmation copy says exactly that, and a sleeping device must be
    factory-reset before it can be paired elsewhere.

    Fault to prove it: keep the row when the leave is not acknowledged. The
    UI then shows a device the bridge has already forgotten.

    The stub never sends a leave confirmation, exactly like a device that
    was asleep or already gone; the timeout below is what turns a wait for
    one into a failure rather than a hung suite."""
    client, harness = pairing
    lamp = _lamp()
    harness.app.device_initialized(lamp)
    adopted = await client.patch(f"/api/zigbee/pairing/{LAMP_IEEE}", json={"name": "Lamp"})
    device_id = adopted.json()["device_id"]

    response = await asyncio.wait_for(client.delete(f"/api/zigbee/pairing/{LAMP_IEEE}"), timeout=5)

    assert response.status_code == 204
    assert harness.app.removed == [LAMP_IEEE]
    assert await _rows(client) == {}
    # And the device the user had already adopted is gone from the store
    # too, rather than left behind as a tile nothing will ever update.
    assert all(device.id != device_id for device in harness.store.devices())


async def test_a_ready_row_shows_configuring_while_configure_on_join_is_still_running(pairing):
    """Design 3.1's table: "configuring - `device_initialized`, our
    configure-on-join running - Setting it up". The row has been set to
    "ready" on `device_initialized` since the source was written, with
    `configure_device` only starting in the background afterwards, so a
    device that takes the better part of 30 s to bind a sleepy cluster
    reported "Ready to use" a whole configuration pass before it deserved
    to.

    Fault to prove it: return `row.state` unchanged instead of overlaying
    `configuring_addresses()`.

    That the set really covers the whole pass is measured against the real
    `configure_device` in
    `tests/zigbee/test_source.py::test_configuring_addresses_covers_the_whole_configuration_pass`;
    what is measured here is the overlay."""
    client, harness = pairing
    lamp = _lamp()
    harness.app.device_initialized(lamp)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "ready"

    harness.source._configuring.add(LAMP_IEEE)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "configuring"

    # And a row that has not finished interviewing is not "configuring" no
    # matter what the set says - a device cannot be mid configure-on-join
    # before it has been read.
    joining = _lamp("00:12:4b:00:1c:00:00:51")
    harness.app.join(joining)
    harness.source._configuring.add(joining.ieee)
    assert (await _rows(client))[joining.ieee]["state"] == "joined"

    harness.source._configuring.discard(LAMP_IEEE)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "ready"


async def test_a_ready_row_with_a_deferred_cluster_shows_waiting_to_wake(pairing):
    """Design 3.1's table: "waiting to wake - configuration deferred -
    Waiting for the device to wake up - press its button".

    Read from the pending table FRESH on every request, not cached on the
    row or on the source: a bridge restart between the deferral and the
    next page load must still show it, because the pending row itself is
    what survives the restart (`zigbee_pending_config` being on disk is the
    entire point of it).

    Fault to prove it: compute this from an in-memory flag set only inside
    `ZigbeeSource._configure_then_deliver` instead of from
    `store.zigbee_pending.addresses_with_pending()`. The state is right
    until the next restart and silently wrong - back to a bare "ready" -
    after one, for exactly the device it matters most for: a battery sensor
    that was asleep when the bridge went down and is still owed a cluster
    when it comes back up.

    The restart is what this test actually stages: the pending row is
    written straight into the store, with nothing in this process ever
    having run a configuration pass - which is precisely the state a
    restarted bridge wakes up in."""
    client, harness = pairing
    lamp = _lamp()
    harness.app.device_initialized(lamp)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "ready"

    harness.store.zigbee_pending.mark_pending(LAMP_IEEE, 1, ON_OFF_CLUSTER)

    assert (await _rows(client))[LAMP_IEEE]["state"] == "waiting_wake"
    # A live configuration pass wins over a cluster still owed: the device
    # is being worked on right now, which is the more specific truth.
    harness.source._configuring.add(LAMP_IEEE)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "configuring"


async def test_a_row_returns_to_ready_once_the_last_pending_cluster_clears(pairing):
    """The wake-up path clears `zigbee_pending_config` one row at a time as
    each deferred cluster finally succeeds. Reading the pending table fresh
    is what makes this transition need no code of its own: once
    `addresses_with_pending()` no longer names the device, the overlay stops
    applying and the row reports the "ready" state it has held since
    `device_initialized`.

    Fault to prove it: cache the "waiting to wake" overlay the first time it
    is computed instead of recomputing it on every request. A device that
    finishes configuring on its next wake-up then shows "waiting to wake"
    forever - on the one tab whose entire reason for existing is not doing
    what ZHA does."""
    client, harness = pairing
    lamp = _lamp()
    harness.app.device_initialized(lamp)
    harness.store.zigbee_pending.mark_pending(LAMP_IEEE, 1, ON_OFF_CLUSTER)
    harness.store.zigbee_pending.mark_pending(LAMP_IEEE, 1, 0x0402)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "waiting_wake"

    # One cluster succeeds on the next wake-up. One is still owed, so the
    # row has not finished waiting.
    harness.store.zigbee_pending.clear(LAMP_IEEE, 1, ON_OFF_CLUSTER)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "waiting_wake"

    harness.store.zigbee_pending.clear(LAMP_IEEE, 1, 0x0402)
    assert (await _rows(client))[LAMP_IEEE]["state"] == "ready"


async def test_an_open_window_and_its_rows_do_not_survive_the_radio_going_away(pairing):
    """The failure this branch keeps producing, in the shape pairing takes:
    something disappears while a window is still open.

    A permit lives in the COORDINATOR. A stick that was unplugged, or one
    the user swapped for another, is not holding a network open for
    anybody - so a tab still counting down is counting down to a fiction,
    and the rows it is showing belong to a radio that is gone.

    Fault to prove it: leave `_permit_until` alone on link loss, and read
    the source once at router-build time instead of per request. The
    countdown then runs on a dead radio, and after a swap the tab keeps
    answering for the stick the user stopped using.

    The swap window itself - the seconds in which the old source is gone
    and the new one does not exist yet - answers 503 rather than an empty
    list: "there is no Zigbee radio right now" is the truth, and an empty
    list would read as "your devices are gone"."""
    client, harness = pairing
    harness.app.device_initialized(_lamp())
    await client.post("/api/zigbee/permit", json={"duration": 254})
    assert (await client.get("/api/zigbee/pairing")).json()["permit_until"] is not None

    # The stick dies under the open window - bellows' single
    # `connection_lost`, which is all a lost link ever produces.
    harness.app.listener_event("connection_lost", OSError("link lost"))
    await asyncio.sleep(0)

    body = (await client.get("/api/zigbee/pairing")).json()
    assert body["permit_until"] is None, "the tab would count down on a dead radio"
    # The rows are still there - the devices did not go anywhere, the radio
    # did - and a permit cannot be opened on it.
    assert [row["ieee"] for row in body["rows"]] == [LAMP_IEEE]
    refused = await client.post("/api/zigbee/permit", json={"duration": 254})
    assert refused.status_code == 502
    assert refused.json()["detail"]

    # And the swap window, where there is no source at all to ask.
    harness.holder._source = None
    for route, method in (
        ("/api/zigbee/pairing", client.get),
        (f"/api/zigbee/pairing/{LAMP_IEEE}/retry", client.post),
        (f"/api/zigbee/pairing/{LAMP_IEEE}", client.delete),
    ):
        response = await method(route)
        assert response.status_code == 503, (route, response.text)
        assert response.json()["detail"]


async def test_a_row_that_is_gone_is_a_404_rather_than_a_silent_success(pairing):
    """A row can disappear between the page being rendered and a button on
    it being pressed: the device was removed from another tab, or the radio
    was swapped and took every row with it.

    Fault to prove it: let Retry and Remove answer 2xx for a device nothing
    has heard of. The user then watches a row that will never change."""
    client, _harness = pairing
    missing = "00:12:4b:00:1c:00:00:99"

    assert (await client.post(f"/api/zigbee/pairing/{missing}/retry")).status_code == 404
    assert (await client.delete(f"/api/zigbee/pairing/{missing}")).status_code == 404
    patched = await client.patch(f"/api/zigbee/pairing/{missing}", json={"name": "Ghost"})
    assert patched.status_code == 404
    assert patched.json()["detail"]


# ------------------------------------- what can change while a name is saved --
#
# The adopt route checks the row, then AWAITS the device's snapshot, then
# writes the store. That await is not a formality: a colour lamp whose
# `ColorCapabilities` are not cached yet sends a real ZCL read to the air
# inside it (`ZigbeeSource._read_colour_capabilities`), which takes as long
# as the device takes to answer. Anything the check established can stop
# being true in that time, and these tests hold the read open - through the
# real routes, with the real source - while it does.

COLOR_CONTROL_CLUSTER = 0x0300
COLOR_CAPABILITIES_ATTRIBUTE = 0x400A
COLOUR_LAMP_IEEE = "00:17:88:01:0b:cc:dd:ee"


class _HeldColourCluster(_StubCluster):
    """A Color Control cluster with `ColorCapabilities` DECLARED but not yet
    cached, so the source asks the device for it - and a read that does not
    answer until the test says so.

    zigpy's `Cluster.read_attributes` is a network round trip; `reading` is
    set the moment it starts and `answer` is what ends it. Waiting on
    `reading` rather than on a count of `sleep(0)` rounds is what guarantees
    the interference below lands INSIDE the await rather than before or
    after it."""

    def __init__(self) -> None:
        super().__init__(COLOR_CONTROL_CLUSTER, {COLOR_CAPABILITIES_ATTRIBUTE: None})
        self.reading = asyncio.Event()
        self.answer = asyncio.Event()

    async def read_attributes(self, attributes: list[int], **kwargs: Any) -> Any:
        self.reading.set()
        await self.answer.wait()
        return {}, {}


def _colour_lamp() -> tuple[_StubDevice, _HeldColourCluster]:
    held = _HeldColourCluster()
    lamp = _StubDevice(
        COLOUR_LAMP_IEEE,
        manufacturer="Signify Netherlands B.V.",
        model="LCA001",
        endpoints=[
            _StubEndpoint(
                11,
                device_type=0x010D,
                clusters=[_StubCluster(ON_OFF_CLUSTER, {ON_OFF_ATTRIBUTE: True}), held],
            )
        ],
    )
    return lamp, held


async def _save_a_name_while(
    client: httpx.AsyncClient, held: _HeldColourCluster, interfere: Any
) -> tuple[list[str], httpx.Response]:
    """Sends the name, runs `interfere` while the snapshot read is open, then
    lets the read answer. Returns the order things happened in, so a test can
    assert the interleaving it claims actually occurred."""
    order: list[str] = []
    saving = asyncio.ensure_future(
        client.patch(f"/api/zigbee/pairing/{COLOUR_LAMP_IEEE}", json={"name": "Lamp"})
    )
    await asyncio.wait_for(held.reading.wait(), timeout=5)
    order.append("read started")
    await interfere()
    order.append("interfered")
    # The proof the interference landed inside the await: the save has not
    # answered yet, and cannot, until the read does.
    assert not saving.done(), "the save finished before the interference - nothing interleaved"
    held.answer.set()
    response = await asyncio.wait_for(saving, timeout=5)
    order.append("save answered")
    return order, response


def _zigbee_devices(store: Store) -> list[Any]:
    return [device for device in store.devices() if device.technology == "zigbee"]


async def test_a_device_removed_while_its_name_is_being_saved_is_not_adopted(pairing):
    """Remove pressed in a second tab while the first tab's save is reading
    the device.

    Without a second look at the row after the read, the save went on to
    register a device the radio had already forgotten: DELETE answered 204,
    the save answered 404, and the store still held the lamp. That device
    reached the device list, the export and Loxone - the three places the
    adopt route exists to keep an unnamed device out of - and could not be
    removed from either tab: `DELETE /api/devices/{id}` asks the source
    first, and the source answers "unknown Zigbee device".

    Fault to prove it: drop the re-check of the row after `snapshot_for`."""
    client, harness = pairing
    lamp, held = _colour_lamp()
    harness.app.device_initialized(lamp)
    assert (await _rows(client))[COLOUR_LAMP_IEEE]["state"] == "ready"

    removals: list[int] = []

    async def remove_from_another_tab() -> None:
        removals.append(
            (await client.delete(f"/api/zigbee/pairing/{COLOUR_LAMP_IEEE}")).status_code
        )

    order, response = await _save_a_name_while(client, held, remove_from_another_tab)

    assert order == ["read started", "interfered", "save answered"]
    assert removals == [204]
    assert response.status_code == 404, response.text
    assert _zigbee_devices(harness.store) == [], "an orphan nothing can remove"
    assert harness.store.device_id_for("zigbee", COLOUR_LAMP_IEEE) is None
    assert harness.runtime.snapshots == [], "an orphan was seeded into the runtime"
    assert await _rows(client) == {}


async def test_a_device_that_rejoins_while_its_name_is_being_saved_is_refused(pairing):
    """The device rejoins under a new short address while the save is
    reading it. zigpy answers that with `device_joined` and a fresh
    interview (`ControllerApplication.handle_join`, zigpy 2.2.0), so the row
    is back at "joined" by the time the read returns.

    Without the second look, the save registered it anyway and answered 200
    with `"state": "joined"` in its own body - the half-interviewed
    registration the 409 exists to refuse, let through by nothing more than
    timing.

    Fault to prove it: re-check that the row still exists, but not that it
    is still ready."""
    client, harness = pairing
    lamp, held = _colour_lamp()
    harness.app.device_initialized(lamp)
    assert (await _rows(client))[COLOUR_LAMP_IEEE]["state"] == "ready"

    async def rejoin() -> None:
        lamp.nwk = 0x7A11
        harness.app.join(lamp)

    order, response = await _save_a_name_while(client, held, rejoin)

    assert order == ["read started", "interfered", "save answered"]
    assert response.status_code == 409, response.text
    assert _zigbee_devices(harness.store) == []
    assert harness.runtime.snapshots == []
    assert (await _rows(client))[COLOUR_LAMP_IEEE]["state"] == "joined"


async def test_a_radio_released_while_a_name_is_being_saved_adopts_nothing(pairing):
    """The radio setting is cleared while the save is reading the device.

    The rows survive a `disconnect()` on purpose - the devices did not go
    anywhere - so the row check alone passes on the released source, and the
    save would register a device off a stick the bridge has just let go of.
    The live source is read again after the await, and a source that is no
    longer the live one adopts nothing.

    Fault to prove it: re-check the row against the source captured before
    the await, without asking whether it is still the live one."""
    client, harness = pairing
    lamp, held = _colour_lamp()
    harness.app.device_initialized(lamp)

    async def clear_the_radio_setting() -> None:
        harness.holder.apply(settings_for_path(None, []))
        await harness.holder.wait_for_apply()
        assert harness.holder.current() is None

    order, response = await _save_a_name_while(client, held, clear_the_radio_setting)

    assert order == ["read started", "interfered", "save answered"]
    assert response.status_code == 503, response.text
    assert _zigbee_devices(harness.store) == []
    assert harness.runtime.snapshots == []


async def test_a_ready_row_is_never_stuck_however_long_it_waits_for_a_name(pairing):
    """A "ready" row is waiting for the USER, not for the device, and design
    3.1's stall thresholds are about the device. A lamp that finished its
    interview before lunch and is named after it has not stopped
    progressing; calling it stuck would put Retry next to a device that
    needs nothing and invite a pointless re-interview.

    The same holds for the two states overlaid onto a ready row: a
    configuration pass and a cluster owed to a sleeping device have clocks
    of their own, and neither is a stalled interview.

    Fault to prove it: let `_stuck` apply to a ready row as well. The input
    that tells the two apart is a ready row older than BOTH thresholds."""
    client, harness = pairing
    ready = _lamp("00:12:4b:00:1c:00:00:61")
    configuring = _lamp("00:12:4b:00:1c:00:00:62")
    waiting = _StubDevice("00:15:8d:00:02:00:00:63", mains=False)
    for device in (ready, configuring, waiting):
        harness.app.device_initialized(device)
    harness.source._configuring.add(configuring.ieee)
    harness.store.zigbee_pending.mark_pending(waiting.ieee, 1, ON_OFF_CLUSTER)

    long_ago = (datetime.now(UTC) - timedelta(seconds=600)).isoformat(timespec="microseconds")
    harness.source._pairing = {
        ieee: replace(row, changed_at=long_ago) for ieee, row in harness.source._pairing.items()
    }

    rows = await _rows(client)
    assert rows[ready.ieee]["state"] == "ready"
    assert rows[configuring.ieee]["state"] == "configuring"
    assert rows[waiting.ieee]["state"] == "waiting_wake"


async def test_a_device_with_no_node_descriptor_yet_waits_the_battery_threshold(pairing):
    """zigpy emits `device_joined` BEFORE it has asked the device anything
    (`handle_join` fires the event, then `schedule_initialize()`), so a row
    that is still "joined" belongs to a device whose `node_desc` is `None` -
    whether it is a mains lamp or a sleeping sensor is not known yet.

    Not known is treated as battery powered: the longer threshold. Guessing
    mains would declare a sleepy sensor stuck 30 s before it deserves it,
    on the one row where the user cannot yet tell which kind it is.

    Fault to prove it: read a missing node descriptor as mains powered. At
    75 s - past the mains threshold, short of the battery one - the row then
    shows "stuck"."""
    client, harness = pairing
    unknown = _StubDevice("00:15:8d:00:02:00:00:71", manufacturer="", model="")
    unknown.node_desc = None  # type: ignore[assignment]
    harness.app.join(unknown)
    assert harness.source._pairing[unknown.ieee].is_mains_powered is False

    stalled = (datetime.now(UTC) - timedelta(seconds=75)).isoformat(timespec="microseconds")
    harness.source._pairing[unknown.ieee] = replace(
        harness.source._pairing[unknown.ieee], changed_at=stalled
    )
    assert (await _rows(client))[unknown.ieee]["state"] == "joined"

    long_stalled = (datetime.now(UTC) - timedelta(seconds=95)).isoformat(timespec="microseconds")
    harness.source._pairing[unknown.ieee] = replace(
        harness.source._pairing[unknown.ieee], changed_at=long_stalled
    )
    assert (await _rows(client))[unknown.ieee]["state"] == "stuck"


async def test_saving_a_room_again_moves_a_device_that_is_already_adopted(pairing):
    """A second save on a row that is already one of the bridge's own - the
    tab keeps the name and room fields on it. `register_device` returns the
    known id WITHOUT touching the row, its `room` argument included, so the
    new room is written by `set_room` or not at all.

    And a save that sends no room leaves the room alone: `None` is
    "unchanged", the convention `PATCH /api/devices/{id}` already uses.

    Fault to prove it: skip `set_room` for a device the store already knows
    - the room then stays "Study" - or call it with `None` as well, which
    clears it."""
    client, harness = pairing
    harness.app.device_initialized(_lamp())
    first = await client.patch(
        f"/api/zigbee/pairing/{LAMP_IEEE}", json={"name": "Reading lamp", "room": "Study"}
    )
    device_id = first.json()["device_id"]

    moved = await client.patch(f"/api/zigbee/pairing/{LAMP_IEEE}", json={"room": "Kitchen"})

    assert moved.status_code == 200, moved.text
    assert moved.json()["device_id"] == device_id
    assert harness.store.device(device_id).room == "Kitchen"
    assert harness.store.device(device_id).label == "Reading lamp"

    renamed = await client.patch(f"/api/zigbee/pairing/{LAMP_IEEE}", json={"name": "Desk lamp"})

    assert renamed.status_code == 200, renamed.text
    assert harness.store.device(device_id).room == "Kitchen"
    assert harness.store.device(device_id).label == "Desk lamp"


async def test_stop_after_the_link_was_lost_is_not_an_error(pairing):
    """Stop, sent after the stick went away underneath an open window.

    The window closed with the radio (`_close_window`), so there is nothing
    to stop - and the pairing tab sends Stop every time the user leaves it.
    A 502 "could not be opened" here would put an error in front of the user
    on every tab change after one radio blip, about a window that is
    already shut. The honest answer is the one a successful Stop gives.

    Fault to prove it: let a failed Stop answer 502 like a failed open.

    The rule is "nothing is open", not "Stop never fails": a Stop the radio
    does not acknowledge while a window IS still open is still a 502,
    because the coordinator may still be letting devices in. Fault for that
    half: answer every Stop with 200."""
    client, harness = pairing
    await client.post("/api/zigbee/permit", json={"duration": 254})
    harness.app.permit_error = TimeoutError()

    unacknowledged = await client.post("/api/zigbee/permit", json={"duration": 0})

    assert unacknowledged.status_code == 502, unacknowledged.text
    assert (await client.get("/api/zigbee/pairing")).json()["permit_until"] is not None

    harness.app.permit_error = None
    harness.app.listener_event("connection_lost", OSError("link lost"))
    await asyncio.sleep(0)

    stopped = await client.post("/api/zigbee/permit", json={"duration": 0})

    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {"permit_until": None}


async def test_a_stop_the_radio_does_not_acknowledge_says_it_could_not_close(pairing):
    """The pairing tab sends Stop when the button is pressed and when the tab
    is left. A refused Stop used to answer with the refusal of an OPEN - "The
    network could not be opened for new devices" - in front of a user who
    had just asked to close it, and whose network may in fact still be open.

    Fault to prove it: answer a failed Stop with `api.zigbee.permit_failed`."""
    client, harness = pairing
    await client.post("/api/zigbee/permit", json={"duration": 254})
    harness.app.permit_error = TimeoutError()

    refused = await client.post("/api/zigbee/permit", json={"duration": 0})

    assert refused.status_code == 502, refused.text
    detail = refused.json()["detail"]
    assert detail.startswith("The network could not be closed for new devices"), detail
    assert "could not be opened" not in detail


async def test_a_failure_message_follows_a_language_switch(pairing, monkeypatch):
    """Fail in one language, switch, read in the other.

    The card shows a failure for as long as the supervisor retries. Its
    sentence used to be translated when the link was lost and stored as
    text, so after a switch to English the line kept its German sentence -
    inside the English "(attempt N - the bridge keeps trying)" wrapper the
    browser renders. It is translated when this route answers now.

    Fault to prove it: store `i18n.t("api.errors.zigbee_not_connected")` in
    `_handle_connection_lost` instead of the message."""
    monkeypatch.delenv("LOXMATTER_LANG", raising=False)
    client, harness = pairing
    harness.store.locale.set_language("de")
    # A request in German first, so the process language really is German
    # at the moment the link goes.
    assert (await client.get("/api/zigbee/radio")).status_code == 200
    harness.app.listener_event("connection_lost", OSError("link lost"))
    await asyncio.sleep(0)
    german = (await client.get("/api/zigbee/radio")).json()["progress"]

    harness.store.locale.set_language("en")
    english = (await client.get("/api/zigbee/radio")).json()["progress"]

    assert german["state"] == english["state"] == "failed"
    i18n.set_language("de")
    assert german["error"] == i18n.t("api.errors.zigbee_not_connected")
    i18n.set_language("en")
    assert english["error"] == i18n.t("api.errors.zigbee_not_connected")
    assert english["error"] != german["error"]


async def test_a_window_that_cannot_be_opened_says_so_in_one_clean_sentence(pairing):
    """The refusal after a lost link carries the source's own sentence, which
    already ends in a full stop and already says the bridge is reconnecting.
    The wrapper used to add a second full stop and say "reconnecting" a
    second time, in both languages.

    Fault to prove it: put the old wrapper text back."""
    client, harness = pairing
    harness.app.listener_event("connection_lost", OSError("link lost"))
    await asyncio.sleep(0)

    english = (await client.post("/api/zigbee/permit", json={"duration": 254})).json()["detail"]
    harness.store.locale.set_language("de")
    german = (await client.post("/api/zigbee/permit", json={"duration": 254})).json()["detail"]

    assert ".." not in english, english
    assert ".." not in german, german
    assert english.lower().count("reconnect") == 1, english
    assert german.count("Brücke") == 1, german


async def test_the_swap_window_says_the_radio_is_changing_not_that_none_is_set_up(pairing):
    """During a radio change there is briefly no source at all: the old one
    is being shut down, and the new one may still be waiting up to 5 s for
    the Thread channel. Every pairing route answers 503 in that window, and
    it used to say "No Zigbee stick is set up... Pick one under Settings" -
    inside a tab that is only visible BECAUSE a stick is set up, to a user
    who has just picked one.

    `ZigbeeRuntime.progress()` already tells the two apart with `applying`;
    this is the same distinction in the pairing routes' words.

    Fault to prove it: answer the not-configured message whenever there is
    no source."""
    client, harness = pairing
    shutting_down = asyncio.Event()
    finish_shutdown = asyncio.Event()

    async def slow_shutdown(*, db: bool = True) -> None:
        shutting_down.set()
        await finish_shutdown.wait()

    harness.app.shutdown = slow_shutdown  # type: ignore[method-assign]
    harness.holder.apply(settings_for_path(None, []))
    await asyncio.wait_for(shutting_down.wait(), timeout=5)
    assert harness.holder.current() is None
    assert harness.holder.progress().state == "applying"

    swapping = await client.get("/api/zigbee/pairing")

    finish_shutdown.set()
    await harness.holder.wait_for_apply()
    assert harness.holder.progress().state == "idle"

    unconfigured = await client.get("/api/zigbee/pairing")

    assert swapping.status_code == 503, swapping.text
    assert unconfigured.status_code == 503, unconfigured.text
    assert swapping.json()["detail"] == i18n.t("api.zigbee.radio_changing")
    assert unconfigured.json()["detail"] == i18n.t("api.errors.zigbee_not_configured")
    assert swapping.json()["detail"] != unconfigured.json()["detail"]


async def test_the_pairing_routes_need_a_login(tmp_path, no_invoke):
    """The same guard every other `/api` router carries. A join window is
    the most privileged thing this bridge can open."""
    store = Store(tmp_path / "t.sqlite")
    host_dev, sys_root = _host_two_sticks(tmp_path)
    runtime = _AttachableRuntime(store)
    sources = Sources([_Matter()])  # type: ignore[list-item]

    async def build(settings: Any) -> Any:
        return None

    holder = ZigbeeRuntime(store, runtime, sources, build_source=build)  # type: ignore[arg-type]
    app = build_app(
        store,
        no_invoke,
        runtime,
        sources=sources,
        update_dir=tmp_path,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
        zigbee_runtime=holder,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/zigbee/pairing")).status_code == 401
        assert (await client.post("/api/zigbee/permit", json={"duration": 254})).status_code == 401
        assert (await client.post("/api/zigbee/pairing/x/retry")).status_code == 401
        assert (await client.delete("/api/zigbee/pairing/x")).status_code == 401
        assert (await client.patch("/api/zigbee/pairing/x", json={})).status_code == 401
    store.close()


async def test_the_routes_need_a_login(tmp_path, no_invoke):
    store = Store(tmp_path / "t.sqlite")
    host_dev, sys_root = _host_two_sticks(tmp_path)
    runtime = _AttachableRuntime(store)
    sources = Sources([_Matter()])  # type: ignore[list-item]

    async def build(settings: Any) -> Any:
        return None

    holder = ZigbeeRuntime(store, runtime, sources, build_source=build)  # type: ignore[arg-type]
    app = build_app(
        store,
        no_invoke,
        runtime,
        sources=sources,
        update_dir=tmp_path,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
        zigbee_runtime=holder,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/zigbee/radio")).status_code == 401
        assert (await client.put("/api/zigbee/radio", json={"path": None})).status_code == 401
    store.close()
