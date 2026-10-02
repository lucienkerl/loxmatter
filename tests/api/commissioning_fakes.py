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

"""Stand-ins for everything a `CommissioningSession` talks to.

Shared by the session's own tests and the `/api/commissioning` route tests:
both import from here (`tests/api/` is on `sys.path`, see `conftest.py`).
Nothing here sleeps for real - time is a number the test moves, and the
session's `sleep` only hands the loop over."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loxmatter.commissioning.session import CommissioningSession
from loxmatter.matter.commissioning_progress import CommissioningTracker
from loxmatter.matter.dcl import DclDirectory
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.radios.bluetooth_health import KernelFinding
from loxmatter.radios.bluez import BluezSnapshot, MatterAdvert

BASE = "https://on.dcl.csa-iot.org"
IKEA = 4476
KAJPLATS_E27 = 36865
KAJPLATS_E14 = 36870
BILRESA = 32769

DCL_ANSWERS: dict[str, dict[str, Any]] = {
    f"{BASE}/dcl/vendorinfo/vendors/{IKEA}": {
        "vendorInfo": {"vendorID": IKEA, "vendorName": "IKEA of Sweden"}
    },
    f"{BASE}/dcl/model/models/{IKEA}/{KAJPLATS_E27}": {
        "model": {
            "vid": IKEA,
            "pid": KAJPLATS_E27,
            "productName": "KAJPLATS E27 WS globe 1055lm",
            "partNumber": "LED2407G8",
            "commissioningModeInitialStepsHint": 1,
            "commissioningModeInitialStepsInstruction": "",
        }
    },
    f"{BASE}/dcl/model/models/{IKEA}/{KAJPLATS_E14}": {
        "model": {
            "vid": IKEA,
            "pid": KAJPLATS_E14,
            "productName": "KAJPLATS E14 CWS globe 806lm",
            "partNumber": "LED2410G5",
            "commissioningModeInitialStepsHint": 1,
            "commissioningModeInitialStepsInstruction": "",
        }
    },
    f"{BASE}/dcl/model/models/{IKEA}/{BILRESA}": {
        "model": {
            "vid": IKEA,
            "pid": BILRESA,
            "productName": "BILRESA dual button",
            "partNumber": "E2489",
            "commissioningModeInitialStepsHint": 0,
            "commissioningModeInitialStepsInstruction": "Press the pairing button four times.",
        }
    },
}

# Example codes from the Matter specification (vendor 0xFFF1, a test
# vendor the DCL is never asked about): discriminator 3840, BLE; the same
# with the on-network bit only; and the 11-digit manual code (short
# discriminator 15, which matches 3840 and 3841).
QR_CODE = "MT:Y.K9042C00KA0648G00"
QR_CODE_ON_NETWORK = "MT:-24J0AFN00KA0648G00"
MANUAL_CODE = "34970112332"


def advert(address: str, discriminator: int, pid: int, rssi: int) -> MatterAdvert:
    return MatterAdvert(
        address=address,
        name=None,
        rssi=rssi,
        discriminator=discriminator,
        vendor_id=IKEA,
        product_id=pid,
        connected=False,
        adapter="/org/bluez/hci0",
    )


class Fetcher:
    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    async def __call__(self, url: str) -> dict[str, Any] | None:
        self.urls.append(url)
        return self.answers.get(url)


class Clock:
    """`time.monotonic` the test moves by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def no_sleep(seconds: float) -> None:
    """The session's sleep: hands the loop over once, never waits."""
    await asyncio.sleep(0)


class FakeScanner:
    def __init__(self) -> None:
        self.scans: list[float] = []
        self.hold: asyncio.Event | None = None
        self.fail_with: Exception | None = None

    async def scan(self, adapter_path: str = "/org/bluez/hci0", seconds: float = 10.0) -> None:
        self.scans.append(seconds)
        if self.fail_with is not None:
            raise self.fail_with
        if self.hold is not None:
            await self.hold.wait()


class FakeReader:
    def __init__(self) -> None:
        self.adverts: list[MatterAdvert] = []

    async def snapshot(self) -> BluezSnapshot | None:
        return BluezSnapshot(adverts=list(self.adverts), adapter=None)


class FakeKernel:
    def __init__(self) -> None:
        self.usec = 1_000_000
        self.findings: list[KernelFinding] = []

    def now_usec(self) -> int | None:
        return self.usec

    async def findings_async(self) -> list[KernelFinding] | None:
        return list(self.findings)


class FakeIdentify:
    """The `IdentifyCoordinator` interface. `calls` holds `("start",
    device_id, renew, seconds)` and `("stop",)`; a blink lasts until it is
    stopped or replaced - the fake has no timer."""

    def __init__(self) -> None:
        self.blinking: int | None = None
        self.calls: list[tuple[Any, ...]] = []
        self.closed = False

    async def start(self, device_id: int, *, renew: bool, seconds: int = 30) -> None:
        self.calls.append(("start", device_id, renew, seconds))
        self.blinking = device_id

    async def start_if_idle(self, device_id: int, seconds: int) -> bool:
        if self.blinking is not None:
            return False
        await self.start(device_id, renew=False, seconds=seconds)
        return True

    async def stop(self) -> None:
        self.calls.append(("stop",))
        self.blinking = None

    async def aclose(self) -> None:
        self.closed = True
        await self.stop()

    def starts(self) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == "start"]


class Gate:
    """Makes `fake_client.commission_with_code` wait per call.

    Every call appends an `asyncio.Event` to `events` and waits on it while
    `held` is set; `failures` hands out an exception per call (in order,
    `None` for success) and `snapshots` a snapshot per call."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.held = False
        self.events: list[asyncio.Event] = []
        self.failures: list[Exception | None] = []
        self.snapshots: list[NodeSnapshot] = []
        self.calls = 0
        self._original = client.commission_with_code
        client.commission_with_code = self._commission

    async def _commission(self, code: str) -> NodeSnapshot:
        self.calls += 1
        event = asyncio.Event()
        self.events.append(event)
        if self.held:
            await event.wait()
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        if self.snapshots:
            self.client.snapshot_to_return = self.snapshots.pop(0)
        else:
            self.client.snapshot_to_return = None
        snapshot: NodeSnapshot = await self._original(code)
        return snapshot

    def release_all(self) -> None:
        for event in self.events:
            event.set()


def snapshot_of(node_id: int, vendor_id: int, product_id: int) -> NodeSnapshot:
    return NodeSnapshot(
        technology="matter",
        address=str(node_id),
        vendor_name="IKEA of Sweden",
        product_name="KAJPLATS",
        unique_id=f"kajplats-{node_id}",
        attributes={"0/40/2": vendor_id, "0/40/4": product_id},
        available=True,
    )


@dataclass
class Harness:
    session: CommissioningSession
    store: Store
    client: Any
    reader: FakeReader
    scanner: FakeScanner
    kernel: FakeKernel
    identify: FakeIdentify
    clock: Clock
    tracker: CommissioningTracker
    gate: Gate


def build_session(tmp_path: Any, fake_client: Any, fake_runtime: Any, fake_otbr: Any) -> Harness:
    store = Store(tmp_path / "session.sqlite")
    fake_client.store = store
    reader = FakeReader()
    scanner = FakeScanner()
    kernel = FakeKernel()
    identify = FakeIdentify()
    clock = Clock()
    tracker = CommissioningTracker()
    gate = Gate(fake_client)
    session = CommissioningSession(
        store=store,
        client_for=lambda: fake_client,
        runtime=fake_runtime(store),
        tracker=tracker,
        fetch_dataset=fake_otbr,
        reader=reader,  # type: ignore[arg-type]
        scanner=scanner,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
        dcl=DclDirectory(store.dcl, Fetcher(DCL_ANSWERS)),
        identify=identify,  # type: ignore[arg-type]
        clock=clock,
        sleep=no_sleep,
    )
    return Harness(
        session=session,
        store=store,
        client=fake_client,
        reader=reader,
        scanner=scanner,
        kernel=kernel,
        identify=identify,
        clock=clock,
        tracker=tracker,
        gate=gate,
    )
