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
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.sources import Sources
from loxmatter.zigbee.runtime import ZigbeeRuntime

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

    def progress(self) -> Any:
        return self._progress

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

    async def seed_from_snapshot(self, snapshots: Any) -> None:
        self.seeded += 1

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


async def test_with_no_sidecar_state_nothing_is_claimed_to_be_in_use(api, tmp_path):
    """No state means nothing is KNOWN to be using anything. That is not a
    licence to open a stick blindly - it is the same "the bridge validates
    what it can see" position `POST /api/radios` already takes, and the
    stick still has to be one the scan found.

    Fault to prove it: treat a missing state file as "Thread is using
    everything". Every stick is then permanently unselectable on any
    installation without the radios sidecar, which is most of them."""
    client, update_dir, _harness = api
    (update_dir / "radios-state.json").unlink()
    body = (await client.get("/api/zigbee/radio")).json()
    assert [stick["selectable"] for stick in body["serial"]] == [True, True]
    assert (await client.put("/api/zigbee/radio", json={"path": MG24_PATH})).status_code == 202


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
    harness.built[0].set_progress("failed", attempts=4, error="the stick is not there")
    body = (await client.get("/api/zigbee/radio")).json()
    assert body["progress"]["state"] == "failed"
    assert body["progress"]["attempts"] == 4
    assert body["progress"]["error"] == "the stick is not there"


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
