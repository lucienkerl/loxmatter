# Commissioning Feedback - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While a device is commissioned, the web UI shows the real phase,
the nearby Matter devices, Bluetooth faults and, on failure, a concrete reason.

**Architecture:** Two read-only host sources become Python modules in
`src/loxmatter/radios/`: `bluez.py` reads BlueZ over D-Bus (Matter BLE
advertisements, adapter state), `bluetooth_health.py` reads `/dev/kmsg` and
counts Bluetooth/power faults. `matter/commissioning_progress.py` holds a
`CommissioningTracker` that `POST /api/devices/commission` drives and
`GET /api/devices/commission/status` reports. The browser decodes the pairing
code's discriminator, sends it along, polls the status route every 2 s and
renders phases, the nearby list, warnings and reasons.

**Tech Stack:** Python 3.12, FastAPI, `dbus-fast` (new), Alpine.js, pytest,
node (for the web helper tests).

**Spec:** `docs/superpowers/specs/2026-09-22-commissioning-feedback-design.md`

**Worktree:** `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/commissioning-feedback`
(branch `claude/commissioning-feedback`). All paths are relative to it.
Subagents: `cd` there first and use absolute paths.

## Global Constraints

- Everything in the repository is English; user-facing text goes through
  `src/loxmatter/i18n/strings.yaml` with an `en` and a `de` value, resolved with
  `i18n.t(...)` at call time. `de:` values are German by design.
- Raw kernel lines never leave `radios/bluetooth_health.py`: the API carries
  categories, counts and times only.
- The bridge never starts a Bluetooth scan of its own.
- Nothing makes commissioning itself depend on BlueZ or `/dev/kmsg`: each source
  degrades to "not available" on its own.
- Phases: `searching` → `found` → `connected` → `joined` → `done` | `failed`;
  phases only move forward.
- Failure reasons: `not_found`, `connection_lost`, `no_thread_network`,
  `matter_server_unreachable`, `other`; plus `bridge_restarted`, decided in the
  browser only.
- Kernel categories: `transport`, `stuck`, `power`.
- The status route and the browser poll interval are 2 s; the BlueZ sample
  interval during an attempt is 2 s.
- Matter BLE service UUID: `0000fff6-0000-1000-8000-00805f9b34fb`.
- Commit messages: Conventional Commits, English, ending with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Tests: run only the files a task names, in the foreground. Never
  `run_in_background`, never `Monitor`, never end a turn waiting for a
  notification. The whole suite does not fit one Bash call (~11 min). Run
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  `uv run python scripts/check_language.py` before every commit.

## Decision recorded while planning

Spec section 7.2 says "the reason key is added to the response body". The
existing error path (`readError` in `web/app.js`) reads only a string
`detail`. The plan keeps `detail` a string and exposes the reason through the
status route (`attempt.reason`), which the dialog reads after a failure
anyway. Task 5 amends the spec sentence accordingly.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/loxmatter/radios/bluez.py` (create) | parse Matter service data; read `GetManagedObjects`; `BluezReader` |
| `src/loxmatter/radios/bluetooth_health.py` (create) | read `/dev/kmsg`, classify lines, count per window; `KernelLog` |
| `src/loxmatter/matter/commissioning_progress.py` (create) | `Discriminator`, `classify_failure`, `CommissioningTracker` |
| `src/loxmatter/matter/client.py` (modify) | `add_node_added_listener` |
| `src/loxmatter/api/models.py`, `src/loxmatter/api/devices.py` (modify) | `discriminator` in the request, tracker in the route, status route, reason texts |
| `src/loxmatter/api/diagnostics.py` (modify) | `bluetooth` check |
| `src/loxmatter/loxone/server.py`, `src/loxmatter/cli.py` (modify) | pass tracker and kernel log |
| `src/loxmatter/web/app.js`, `src/loxmatter/web/index.html`, `src/loxmatter/i18n/strings.yaml` (modify) | decoding, polling, dialog |
| `deploy/testhost/docker-compose.yml`, `deploy/testhost/README.md`, `CHANGELOG.md` (modify) | mounts, notes |
| `tests/fixtures/kmsg/pi3-andi-2026-09-22.txt` (already committed with the plan) | 1131 real Bluetooth/hwmon kernel lines |

---

### Task 1: Matter advertisements from BlueZ

**Files:**
- Create: `src/loxmatter/radios/bluez.py`
- Modify: `pyproject.toml`, `uv.lock` (add `dbus-fast`)
- Test: `tests/radios/test_bluez.py`

**Interfaces:**
- Produces:
  - `MATTER_SERVICE_UUID: Final = "0000fff6-0000-1000-8000-00805f9b34fb"`
  - `@dataclass(frozen=True) class MatterAdvert: address: str; name: str | None; rssi: int | None; discriminator: int; vendor_id: int; product_id: int; connected: bool; adapter: str | None`
  - `@dataclass(frozen=True) class AdapterState: name: str; powered: bool; discovering: bool`
  - `@dataclass(frozen=True) class BluezSnapshot: adverts: list[MatterAdvert]; adapter: AdapterState | None`
  - `def parse_matter_service_data(data: bytes) -> tuple[int, int, int] | None` → `(discriminator, vendor_id, product_id)`
  - `def snapshot_from_objects(objects: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> BluezSnapshot`
  - `ObjectsFetcher = Callable[[], Awaitable[Mapping[str, Mapping[str, Mapping[str, Any]]]]]`
  - `class BluezReader(fetch: ObjectsFetcher | None = None)` with `async def snapshot(self) -> BluezSnapshot | None`

- [ ] **Step 1: Add the dependency**

Run: `uv add "dbus-fast>=2.24"`
Expected: `pyproject.toml` lists `dbus-fast>=2.24` under `dependencies`, `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

Create `tests/radios/test_bluez.py` (GPL header copied from any file in `tests/radios/`):

```python
"""Matter commissioning advertisements read from BlueZ (design 2026-09-22,
section 6). The objects are shaped like `GetManagedObjects` after the reader
unwrapped D-Bus variants into plain values. The first device is the lamp
measured on pi3-andi on 21 September 2026 (`bluetoothctl info FB:73:82:07:E3:AD`)."""

from __future__ import annotations

import pytest

from loxmatter.radios.bluez import (
    MATTER_SERVICE_UUID,
    AdapterState,
    BluezReader,
    MatterAdvert,
    parse_matter_service_data,
    snapshot_from_objects,
)

LAMP_SERVICE_DATA = bytes.fromhex("0023047c11019000")

OBJECTS = {
    "/org/bluez/hci0": {
        "org.bluez.Adapter1": {"Address": "B8:27:EB:11:D8:9A", "Powered": True, "Discovering": True},
    },
    "/org/bluez/hci0/dev_FB_73_82_07_E3_AD": {
        "org.bluez.Device1": {
            "Address": "FB:73:82:07:E3:AD",
            "Name": "LED Light0x07C2",
            "RSSI": -60,
            "Connected": False,
            "Adapter": "/org/bluez/hci0",
            "ServiceData": {MATTER_SERVICE_UUID: LAMP_SERVICE_DATA},
        },
    },
    # A phone: manufacturer data only, no Matter service data.
    "/org/bluez/hci0/dev_79_BF_58_D2_5B_80": {
        "org.bluez.Device1": {"Address": "79:BF:58:D2:5B:80", "RSSI": -64, "Connected": False},
    },
    # A Matter advertisement BlueZ has cached but no longer hears: no RSSI.
    "/org/bluez/hci0/dev_E0_55_02_06_D3_79": {
        "org.bluez.Device1": {
            "Address": "E0:55:02:06:D3:79",
            "Connected": False,
            "ServiceData": {MATTER_SERVICE_UUID: bytes.fromhex("0001017c11079000")},
        },
    },
}


def test_the_measured_lamp_advertisement_decodes() -> None:
    assert parse_matter_service_data(LAMP_SERVICE_DATA) == (1059, 4476, 36865)


@pytest.mark.parametrize("data", [b"", bytes(6), bytes.fromhex("0123047c110190")])
def test_short_or_foreign_service_data_reads_as_none(data: bytes) -> None:
    """Fewer than 7 bytes, or an opcode other than 0x00 (commissionable)."""
    assert parse_matter_service_data(data) is None


def test_only_heard_matter_devices_are_listed() -> None:
    snapshot = snapshot_from_objects(OBJECTS)
    assert snapshot.adverts == [
        MatterAdvert(
            address="FB:73:82:07:E3:AD",
            name="LED Light0x07C2",
            rssi=-60,
            discriminator=1059,
            vendor_id=4476,
            product_id=36865,
            connected=False,
            adapter="/org/bluez/hci0",
        )
    ]
    assert snapshot.adapter == AdapterState(name="hci0", powered=True, discovering=True)


def test_adverts_are_sorted_by_signal_strength() -> None:
    objects = dict(OBJECTS)
    objects["/org/bluez/hci0/dev_AA"] = {
        "org.bluez.Device1": {
            "Address": "AA:AA:AA:AA:AA:AA",
            "RSSI": -40,
            "Connected": True,
            "ServiceData": {MATTER_SERVICE_UUID: bytes.fromhex("00d00d7c11079000")},
        }
    }
    addresses = [advert.address for advert in snapshot_from_objects(objects).adverts]
    assert addresses == ["AA:AA:AA:AA:AA:AA", "FB:73:82:07:E3:AD"]


def test_the_discovering_adapter_is_reported_when_there_are_several() -> None:
    objects = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": True, "Discovering": False}},
        "/org/bluez/hci1": {"org.bluez.Adapter1": {"Powered": True, "Discovering": True}},
    }
    assert snapshot_from_objects(objects).adapter == AdapterState(
        name="hci1", powered=True, discovering=True
    )


async def test_the_reader_returns_the_snapshot() -> None:
    async def fetch():
        return OBJECTS

    snapshot = await BluezReader(fetch).snapshot()
    assert snapshot is not None
    assert [advert.discriminator for advert in snapshot.adverts] == [1059]


async def test_an_unreachable_bus_reads_as_none() -> None:
    """No /run/dbus socket, BlueZ not running, a denied call: all the same
    "not available" (design section 8)."""

    async def fetch():
        raise FileNotFoundError("/run/dbus/system_bus_socket")

    assert await BluezReader(fetch).snapshot() is None
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/radios/test_bluez.py -v`
Expected: `ModuleNotFoundError: loxmatter.radios.bluez`.

- [ ] **Step 4: Implement**

Create `src/loxmatter/radios/bluez.py` (GPL header copied from `src/loxmatter/radios/inventory.py`):

```python
"""Matter commissioning advertisements and adapter state, read from BlueZ.

Design 2026-09-22, sections 3 and 6. matter-server scans over BlueZ while it
commissions; BlueZ keeps every advertisement it received as an
`org.bluez.Device1` object. This module reads those objects - it never starts
a scan itself: on 21 September 2026 the Raspberry Pi 3's on-board adapter
wedged after a single commissioning attempt, and a second scanning client on
the same chip is the last thing it needs.

A Matter device in commissioning mode advertises service data under
`MATTER_SERVICE_UUID`: byte 0 is the opcode (0x00 = commissionable), bytes 1-2
little-endian hold the 12-bit discriminator in their low bits, bytes 3-4 the
vendor id, bytes 5-6 the product id. Measured: `00 23 04 7c 11 01 90 00` is
discriminator 1059, vendor 4476 (0x117C, IKEA), product 36865 (0x9001).
Nothing in it is secret.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

MATTER_SERVICE_UUID: Final = "0000fff6-0000-1000-8000-00805f9b34fb"
_DEVICE: Final = "org.bluez.Device1"
_ADAPTER: Final = "org.bluez.Adapter1"
_COMMISSIONABLE_OPCODE: Final = 0x00

ObjectsFetcher = Callable[[], Awaitable[Mapping[str, Mapping[str, Mapping[str, Any]]]]]


@dataclass(frozen=True)
class MatterAdvert:
    address: str
    name: str | None
    rssi: int | None
    discriminator: int
    vendor_id: int
    product_id: int
    connected: bool
    adapter: str | None


@dataclass(frozen=True)
class AdapterState:
    name: str
    powered: bool
    discovering: bool


@dataclass(frozen=True)
class BluezSnapshot:
    adverts: list[MatterAdvert]
    adapter: AdapterState | None


def parse_matter_service_data(data: bytes) -> tuple[int, int, int] | None:
    """`(discriminator, vendor_id, product_id)`, or `None` for anything that is
    not a commissionable Matter advertisement."""
    if len(data) < 7 or data[0] != _COMMISSIONABLE_OPCODE:
        return None
    discriminator = int.from_bytes(data[1:3], "little") & 0x0FFF
    vendor_id = int.from_bytes(data[3:5], "little")
    product_id = int.from_bytes(data[5:7], "little")
    return discriminator, vendor_id, product_id


def _advert(properties: Mapping[str, Any]) -> MatterAdvert | None:
    service_data = properties.get("ServiceData") or {}
    raw = service_data.get(MATTER_SERVICE_UUID)
    rssi = properties.get("RSSI")
    if raw is None or not isinstance(rssi, int):
        # No RSSI: BlueZ remembers the device but has not heard it lately.
        return None
    parsed = parse_matter_service_data(bytes(raw))
    if parsed is None:
        return None
    discriminator, vendor_id, product_id = parsed
    name = properties.get("Name")
    adapter = properties.get("Adapter")
    return MatterAdvert(
        address=str(properties.get("Address", "")),
        name=name if isinstance(name, str) else None,
        rssi=rssi,
        discriminator=discriminator,
        vendor_id=vendor_id,
        product_id=product_id,
        connected=bool(properties.get("Connected", False)),
        adapter=adapter if isinstance(adapter, str) else None,
    )


def snapshot_from_objects(objects: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> BluezSnapshot:
    adverts: list[MatterAdvert] = []
    adapters: list[AdapterState] = []
    for path, interfaces in objects.items():
        if _DEVICE in interfaces:
            advert = _advert(interfaces[_DEVICE])
            if advert is not None:
                adverts.append(advert)
        if _ADAPTER in interfaces:
            properties = interfaces[_ADAPTER]
            adapters.append(
                AdapterState(
                    name=path.rsplit("/", 1)[-1],
                    powered=bool(properties.get("Powered", False)),
                    discovering=bool(properties.get("Discovering", False)),
                )
            )
    adverts.sort(key=lambda advert: -(advert.rssi or -1000))
    # The adapter that scans is the one matter-server uses; with none
    # scanning, the first one.
    adapters.sort(key=lambda adapter: adapter.name)
    scanning = [adapter for adapter in adapters if adapter.discovering]
    adapter = (scanning or adapters or [None])[0]
    return BluezSnapshot(adverts=adverts, adapter=adapter)


def _unwrap(value: Any) -> Any:
    """dbus-fast `Variant`s into plain Python values, recursively."""
    from dbus_fast import Variant

    if isinstance(value, Variant):
        return _unwrap(value.value)
    if isinstance(value, dict):
        return {key: _unwrap(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap(item) for item in value]
    return value


async def _fetch_managed_objects() -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path="/",
                interface="org.freedesktop.DBus.ObjectManager",
                member="GetManagedObjects",
            )
        )
    finally:
        bus.disconnect()
    if reply is None or reply.message_type == MessageType.ERROR:
        raise RuntimeError("BlueZ refused GetManagedObjects")
    result: Mapping[str, Mapping[str, Mapping[str, Any]]] = _unwrap(reply.body[0])
    return result


class BluezReader:
    """One `GetManagedObjects` per `snapshot()`. `None` whenever BlueZ cannot be
    read - no socket, no BlueZ, a refused call - because a missing source must
    read like "not available", never like an error (design section 8)."""

    def __init__(self, fetch: ObjectsFetcher | None = None) -> None:
        self._fetch = fetch or _fetch_managed_objects

    async def snapshot(self) -> BluezSnapshot | None:
        try:
            objects = await self._fetch()
        except Exception as exc:
            logger.debug("BlueZ not readable: %s", exc)
            return None
        return snapshot_from_objects(objects)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/radios -v`
Expected: all PASS.

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add pyproject.toml uv.lock src/loxmatter/radios/bluez.py tests/radios/test_bluez.py
git commit -m "feat(radios): read Matter advertisements and adapter state from BlueZ

The commissioning dialog needs to know which Matter devices advertise nearby
and whether the adapter still scans. BlueZ already holds both while
matter-server commissions; this reads them over D-Bus without scanning.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

If mypy lacks stubs for `dbus_fast`, it ships `py.typed`; if it still
complains, add `[[tool.mypy.overrides]] module = ["dbus_fast.*"]
ignore_missing_imports = true` in `pyproject.toml` next to the existing
overrides, and mention it in the report.

---

### Task 2: Bluetooth health from the kernel log

**Files:**
- Create: `src/loxmatter/radios/bluetooth_health.py`
- Test: `tests/radios/test_bluetooth_health.py`
- Fixture (exists): `tests/fixtures/kmsg/pi3-andi-2026-09-22.txt`

**Interfaces:**
- Produces:
  - `Category = Literal["transport", "stuck", "power"]`
  - `CATEGORIES: Final[tuple[Category, ...]] = ("transport", "stuck", "power")`
  - `@dataclass(frozen=True) class KernelFinding: category: Category; usec: int`
  - `def classify_kmsg_record(record: str) -> KernelFinding | None`
  - `def counts_since(findings: Iterable[KernelFinding], since_usec: int) -> dict[str, int]` (all three keys always present)
  - `class KernelLog(path: Path = Path("/dev/kmsg"), uptime_path: Path = Path("/proc/uptime"))` with `def findings(self) -> list[KernelFinding] | None` and `def now_usec(self) -> int | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/radios/test_bluetooth_health.py`:

```python
"""Bluetooth and power faults from the kernel log (design 2026-09-22,
section 7.1). The fixture holds the 1131 `Bluetooth: hci` and `hwmon` records
read from `/dev/kmsg` on pi3-andi on 22 September 2026, uptime 31536.15 s."""

from __future__ import annotations

from pathlib import Path

import pytest

from loxmatter.radios.bluetooth_health import (
    KernelFinding,
    KernelLog,
    classify_kmsg_record,
    counts_since,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kmsg" / "pi3-andi-2026-09-22.txt"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ("3,1019,1019389516,-;Bluetooth: hci0: Frame reassembly failed (-84)", KernelFinding("transport", 1019389516)),
        ("3,20,77,-;Bluetooth: hci0: Received unexpected HCI Event 0x00", KernelFinding("transport", 77)),
        ("3,21,78,-;Bluetooth: hci0: unexpected event 0x00 length: 2 < 3", KernelFinding("transport", 78)),
        ("3,22,79,-;Bluetooth: hci0: ACL packet for unknown connection handle 64", KernelFinding("transport", 79)),
        ("3,1,5,-;Bluetooth: hci0: Unable to disable scanning: -16", KernelFinding("stuck", 5)),
        ("3,2,6,-;Bluetooth: hci0: stop background scanning failed: -16", KernelFinding("stuck", 6)),
        ("3,3,7,-;Bluetooth: hci0: command 0x200c tx timeout", KernelFinding("stuck", 7)),
        ("3,4,8,-;Bluetooth: hci0: Opcode 0x200c failed: -110", KernelFinding("stuck", 8)),
        ("2,5,9,-;hwmon hwmon1: Undervoltage detected!", KernelFinding("power", 9)),
        ("6,6,10,-;hwmon hwmon1: Voltage normalised", None),
        ("6,390,17931589,-;Bluetooth: hci0: BCM: chip id 94", None),
        ("not a kmsg record", None),
    ],
)
def test_records_are_classified(record: str, expected: KernelFinding | None) -> None:
    assert classify_kmsg_record(record) == expected


def test_the_measured_log_counts() -> None:
    findings = KernelLog(FIXTURE).findings()
    assert findings is not None
    assert counts_since(findings, 0) == {"transport": 435, "stuck": 97, "power": 296}


def test_counts_respect_the_window() -> None:
    findings = [KernelFinding("transport", 100), KernelFinding("transport", 300), KernelFinding("power", 50)]
    assert counts_since(findings, 200) == {"transport": 1, "stuck": 0, "power": 0}


def test_now_comes_from_proc_uptime(tmp_path: Path) -> None:
    uptime = tmp_path / "uptime"
    uptime.write_text("31536.15 105813.72\n", encoding="utf-8")
    assert KernelLog(FIXTURE, uptime).now_usec() == 31_536_150_000


def test_an_unreadable_log_reads_as_not_available(tmp_path: Path) -> None:
    assert KernelLog(tmp_path / "missing").findings() is None
    assert KernelLog(tmp_path / "missing", tmp_path / "missing").now_usec() is None
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/radios/test_bluetooth_health.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/loxmatter/radios/bluetooth_health.py` (GPL header):

```python
"""Bluetooth and power faults, counted from the kernel log.

Design 2026-09-22, section 7.1. On 21 September 2026 every commissioning
failure on pi3-andi had its cause in the kernel log and nowhere else:
`Frame reassembly failed` while PASE ran, `Unable to disable scanning` while
matter-server waited for advertisements, and the next morning `Undervoltage
detected!`. The bridge container reads `/dev/kmsg` (mounted read-only,
section 8); `dmesg` itself is blocked by Docker's seccomp profile.

Raw lines never leave this module. Callers get a category and the kernel's
timestamp (microseconds since boot), nothing else.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

Category = Literal["transport", "stuck", "power"]
CATEGORIES: Final[tuple[Category, ...]] = ("transport", "stuck", "power")

# `prio,seq,usec,flags;message` - the record format of /dev/kmsg.
_RECORD: Final = re.compile(r"^\d+,\d+,(?P<usec>\d+),[^;]*;(?P<message>.*)$")
_PATTERNS: Final[tuple[tuple[Category, re.Pattern[str]], ...]] = (
    (
        "transport",
        re.compile(
            r"^Bluetooth: hci\d+: (Frame reassembly failed|Received unexpected HCI Event"
            r"|unexpected event 0x|\w+ packet for unknown connection handle)"
        ),
    ),
    (
        "stuck",
        re.compile(
            r"^Bluetooth: hci\d+: (Unable to disable scanning|stop background scanning failed"
            r"|command 0x[0-9a-f]+ tx timeout|Opcode 0x[0-9a-f]+ failed)"
        ),
    ),
    ("power", re.compile(r"^hwmon hwmon\d+: Undervoltage detected!")),
)
_READ_SIZE: Final = 8192


@dataclass(frozen=True)
class KernelFinding:
    category: Category
    usec: int


def classify_kmsg_record(record: str) -> KernelFinding | None:
    match = _RECORD.match(record.strip())
    if match is None:
        return None
    message = match.group("message")
    for category, pattern in _PATTERNS:
        if pattern.match(message):
            return KernelFinding(category, int(match.group("usec")))
    return None


def counts_since(findings: Iterable[KernelFinding], since_usec: int) -> dict[str, int]:
    counts = dict.fromkeys(CATEGORIES, 0)
    for finding in findings:
        if finding.usec >= since_usec:
            counts[finding.category] += 1
    return counts


def _records(path: Path) -> Iterator[str]:
    """Every record currently in the ring buffer. `/dev/kmsg` returns one
    record per `read()` and `EAGAIN` at the end when opened non-blocking; a
    regular file (the test fixture) is read line by line."""
    if not stat.S_ISCHR(path.stat().st_mode):
        yield from path.read_text(encoding="utf-8", errors="replace").splitlines()
        return
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        while True:
            try:
                chunk = os.read(fd, _READ_SIZE)
            except BlockingIOError:
                return
            except BrokenPipeError:
                # The record we were about to read was overwritten; the next
                # read continues with the oldest one still there.
                continue
            if not chunk:
                return
            yield chunk.decode("utf-8", errors="replace")
    finally:
        os.close(fd)


class KernelLog:
    def __init__(
        self, path: Path = Path("/dev/kmsg"), uptime_path: Path = Path("/proc/uptime")
    ) -> None:
        self._path = path
        self._uptime_path = uptime_path

    def findings(self) -> list[KernelFinding] | None:
        """`None` when the log cannot be read (not mounted, `dmesg_restrict`)."""
        try:
            return [
                finding
                for record in _records(self._path)
                if (finding := classify_kmsg_record(record)) is not None
            ]
        except OSError:
            return None

    def now_usec(self) -> int | None:
        try:
            seconds = float(self._uptime_path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return round(seconds * 1_000_000)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/radios/test_bluetooth_health.py -v`
Expected: all PASS. If the fixture counts differ, do NOT change the expected
numbers without checking: recount with
`grep -c` per pattern on the fixture and report the difference.

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/radios/bluetooth_health.py tests/radios/test_bluetooth_health.py
git commit -m "feat(radios): count Bluetooth and power faults from the kernel log

Every commissioning failure on the Raspberry Pi 3 test host had its cause in
the kernel log only. This reads /dev/kmsg and reduces it to three categories,
so no raw kernel line ever reaches the web UI.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `CommissioningTracker`

**Files:**
- Create: `src/loxmatter/matter/commissioning_progress.py`
- Test: `tests/matter/test_commissioning_progress.py`

**Interfaces:**
- Consumes (Tasks 1-2): `BluezReader.snapshot() -> BluezSnapshot | None`, `MatterAdvert`, `AdapterState`, `KernelLog.findings()`, `KernelLog.now_usec()`, `counts_since`.
- Produces:
  - `Phase = Literal["searching", "found", "connected", "joined", "done", "failed"]`
  - `Reason = Literal["not_found", "connection_lost", "no_thread_network", "matter_server_unreachable", "other"]`
  - `@dataclass(frozen=True) class Discriminator: value: int; kind: Literal["short", "long"]` with `def matches(self, advertised: int) -> bool`
  - `def classify_failure(text: str, reached: Phase) -> Reason`
  - `class CommissioningTracker(*, bluez: BluezReader | None = None, kernel: KernelLog | None = None, clock: Callable[[], datetime] = ..., sample_interval: float = 2.0, stuck_after: float = 10.0)` with:
    - `started_at: datetime`
    - `def start(self, discriminator: Discriminator | None) -> None`
    - `def node_added(self, node_id: int) -> None`
    - `async def sample(self) -> None`
    - `async def finish(self, reason: Reason | None) -> None`
    - `@property def phase(self) -> Phase | None`
    - `def status(self) -> dict[str, object]`

- [ ] **Step 1: Write the failing tests**

Create `tests/matter/test_commissioning_progress.py`:

```python
"""The phases of a commissioning attempt (design 2026-09-22, section 5) and
the failure reasons (section 7.2). BlueZ and the kernel log are fakes; the
matter-server texts are the ones logged on pi3-andi on 21 September 2026."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from loxmatter.matter.commissioning_progress import (
    CommissioningTracker,
    Discriminator,
    classify_failure,
)
from loxmatter.radios.bluetooth_health import KernelFinding
from loxmatter.radios.bluez import AdapterState, BluezSnapshot, MatterAdvert


def _advert(discriminator: int, *, connected: bool = False, address: str = "E0:55:02:06:D3:79") -> MatterAdvert:
    return MatterAdvert(
        address=address, name=None, rssi=-60, discriminator=discriminator,
        vendor_id=4476, product_id=36871, connected=connected, adapter="/org/bluez/hci0",
    )


class FakeBluez:
    def __init__(self) -> None:
        self.snapshots: list[BluezSnapshot | None] = []
        self.last: BluezSnapshot | None = None

    async def snapshot(self) -> BluezSnapshot | None:
        if self.snapshots:
            self.last = self.snapshots.pop(0)
        return self.last


class FakeKernel:
    def __init__(self) -> None:
        self.now = 1_000_000_000
        self.log: list[KernelFinding] | None = []

    def findings(self) -> list[KernelFinding] | None:
        return self.log

    def now_usec(self) -> int | None:
        return self.now


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


SCANNING = AdapterState(name="hci0", powered=True, discovering=True)


def _tracker() -> tuple[CommissioningTracker, FakeBluez, FakeKernel, Clock]:
    bluez, kernel, clock = FakeBluez(), FakeKernel(), Clock()
    return CommissioningTracker(bluez=bluez, kernel=kernel, clock=clock), bluez, kernel, clock


def test_a_short_discriminator_matches_the_top_four_bits() -> None:
    assert Discriminator(4, "short").matches(1059)
    assert not Discriminator(9, "short").matches(1059)
    assert Discriminator(1059, "long").matches(1059)
    assert not Discriminator(1058, "long").matches(1059)


@pytest.mark.parametrize(
    ("text", "reached", "reason"),
    [
        ("Commissioning failed: Commission failed: discovery of node with discriminator 9 failed: No commissionable device was discovered", "searching", "not_found"),
        ("Commissioning failed: Commission failed: discovery of node with discriminator 1 failed: No device could be commissioned (1 of 1 started attempt(s) failed, 1 discovered)", "searching", "connection_lost"),
        ("Commissioning failed: something else", "connected", "connection_lost"),
        ("Commissioning failed: something else", "searching", "other"),
    ],
)
def test_failures_are_classified(text: str, reached: str, reason: str) -> None:
    assert classify_failure(text, reached) == reason  # type: ignore[arg-type]


async def test_the_phases_follow_bluez_and_node_added() -> None:
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(1, "short"))
    assert tracker.phase == "searching"

    bluez.snapshots = [BluezSnapshot([_advert(0x100 | 5)], SCANNING)]  # short discriminator 1
    await tracker.sample()
    assert tracker.phase == "found"

    bluez.snapshots = [BluezSnapshot([_advert(0x105, connected=True)], SCANNING)]
    await tracker.sample()
    assert tracker.phase == "connected"

    tracker.node_added(3)
    assert tracker.phase == "joined"

    await tracker.finish(None)
    assert tracker.phase == "done"


async def test_phases_never_move_backwards() -> None:
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(1, "short"))
    tracker.node_added(3)
    bluez.snapshots = [BluezSnapshot([_advert(0x105)], SCANNING)]
    await tracker.sample()
    assert tracker.phase == "joined"


async def test_an_ip_attempt_goes_from_searching_to_joined() -> None:
    tracker, _, _, _ = _tracker()
    tracker.start(None)
    await tracker.sample()
    assert tracker.phase == "searching"
    tracker.node_added(4)
    assert tracker.phase == "joined"


async def test_the_status_carries_nearby_devices_and_marks_the_match() -> None:
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(9, "short"))
    bluez.snapshots = [BluezSnapshot([_advert(1059, address="FB:73:82:07:E3:AD")], SCANNING)]
    await tracker.sample()
    nearby = tracker.status()["attempt"]["nearby"]  # type: ignore[index]
    assert nearby == [
        {
            "address": "FB:73:82:07:E3:AD", "name": None, "rssi": -60, "discriminator": 1059,
            "vendor_id": 4476, "product_id": 36871, "connected": False, "matches": False,
        }
    ]


async def test_bluetooth_findings_are_counted_during_the_attempt_and_the_hour() -> None:
    tracker, bluez, kernel, _ = _tracker()
    kernel.log = [KernelFinding("power", kernel.now - 30 * 60 * 1_000_000)]  # before the attempt
    tracker.start(Discriminator(1, "short"))
    kernel.log = kernel.log + [KernelFinding("transport", kernel.now + 5)]
    bluez.snapshots = [BluezSnapshot([], SCANNING)]
    await tracker.sample()
    bluetooth = tracker.status()["attempt"]["bluetooth"]  # type: ignore[index]
    assert bluetooth["available"] is True
    assert bluetooth["during_attempt"] == {"transport": 1, "stuck": 0, "power": 0}
    assert bluetooth["last_hour"] == {"transport": 1, "stuck": 0, "power": 1}
    assert bluetooth["stuck_now"] is False


async def test_an_adapter_that_stops_scanning_while_searching_is_stuck() -> None:
    """Measured 21 September 22:26: `Discovering: false` while matter-server
    waited for an advertisement. Only after `stuck_after` seconds, because an
    attempt's first sample can come before matter-server has started to scan."""
    tracker, bluez, _, clock = _tracker()
    tracker.start(Discriminator(4, "short"))
    bluez.snapshots = [BluezSnapshot([], AdapterState("hci0", True, False))]
    await tracker.sample()
    assert tracker.status()["attempt"]["bluetooth"]["stuck_now"] is False  # type: ignore[index]
    clock.now += timedelta(seconds=11)
    await tracker.sample()
    assert tracker.status()["attempt"]["bluetooth"]["stuck_now"] is True  # type: ignore[index]


async def test_without_sources_bluetooth_is_not_available() -> None:
    tracker = CommissioningTracker()
    tracker.start(Discriminator(4, "short"))
    await tracker.sample()
    assert tracker.status()["attempt"]["bluetooth"]["available"] is False  # type: ignore[index]


async def test_a_failure_keeps_the_last_attempt_with_its_reason() -> None:
    tracker, _, _, _ = _tracker()
    assert tracker.status()["attempt"] is None
    tracker.start(Discriminator(9, "short"))
    await tracker.finish("not_found")
    attempt = tracker.status()["attempt"]
    assert attempt["phase"] == "failed"  # type: ignore[index]
    assert attempt["reason"] == "not_found"  # type: ignore[index]
    assert attempt["discriminator"] == {"value": 9, "kind": "short"}  # type: ignore[index]
    assert tracker.status()["bridge_started_at"] == "2026-09-22T07:00:00Z"
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/matter/test_commissioning_progress.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/loxmatter/matter/commissioning_progress.py` (GPL header):

```python
"""What a commissioning attempt is doing (design 2026-09-22, section 5).

matter-server reports no commissioning steps. The phases come from what the
host can see: BlueZ holds an advertisement with the code's discriminator
(`found`), BlueZ holds a connection to that device (`connected`, PASE and the
setup run over it), matter-server announced the new node (`joined`).

One attempt at a time, as `POST /api/devices/commission` already runs them.
The last attempt stays readable after it ended, so a reloaded page can show
its result.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal

from loxmatter.radios.bluetooth_health import KernelLog, counts_since
from loxmatter.radios.bluez import AdapterState, BluezReader, MatterAdvert

Phase = Literal["searching", "found", "connected", "joined", "done", "failed"]
Reason = Literal[
    "not_found", "connection_lost", "no_thread_network", "matter_server_unreachable", "other"
]

_ORDER: Final[dict[str, int]] = {
    "searching": 0, "found": 1, "connected": 2, "joined": 3, "done": 4, "failed": 4,
}
_HOUR_USEC: Final = 3600 * 1_000_000
_NOT_FOUND: Final = "No commissionable device was discovered"
_CONNECTION_LOST: Final = ("No device could be commissioned", "unreachable")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Discriminator:
    value: int
    kind: Literal["short", "long"]

    def matches(self, advertised: int) -> bool:
        """A short (4-bit) discriminator names the top four of the 12 bits."""
        if self.kind == "short":
            return (advertised >> 8) == self.value
        return advertised == self.value


def classify_failure(text: str, reached: Phase) -> Reason:
    """matter-server's texts as logged on 21 September 2026 (design 7.2)."""
    if any(marker in text for marker in _CONNECTION_LOST) or reached in ("found", "connected"):
        return "connection_lost"
    if _NOT_FOUND in text:
        return "not_found"
    return "other"


@dataclass
class _Attempt:
    started_at: datetime
    started_usec: int | None
    discriminator: Discriminator | None
    phase: Phase = "searching"
    phase_since: datetime = field(default_factory=_utc_now)
    reason: Reason | None = None
    nearby: list[MatterAdvert] = field(default_factory=list)
    adapter: AdapterState | None = None
    matched_address: str | None = None
    stuck_now: bool = False


class CommissioningTracker:
    def __init__(
        self,
        *,
        bluez: BluezReader | None = None,
        kernel: KernelLog | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sample_interval: float = 2.0,
        stuck_after: float = 10.0,
    ) -> None:
        self._bluez = bluez
        self._kernel = kernel
        self._clock = clock
        self._interval = sample_interval
        self._stuck_after = stuck_after
        self.started_at = clock()
        self._attempt: _Attempt | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def phase(self) -> Phase | None:
        return self._attempt.phase if self._attempt is not None else None

    def start(self, discriminator: Discriminator | None) -> None:
        now = self._clock()
        self._attempt = _Attempt(
            started_at=now,
            started_usec=self._kernel.now_usec() if self._kernel is not None else None,
            discriminator=discriminator,
            phase_since=now,
        )
        if self._bluez is not None or self._kernel is not None:
            with contextlib.suppress(RuntimeError):  # no running loop (sync tests)
                self._task = asyncio.get_running_loop().create_task(self._sample_loop())

    def node_added(self, node_id: int) -> None:
        self._advance("joined")

    async def finish(self, reason: Reason | None) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._attempt is None:
            return
        self._attempt.reason = reason
        self._advance("failed" if reason is not None else "done", force=True)

    async def sample(self) -> None:
        attempt = self._attempt
        if attempt is None or self._bluez is None:
            return
        snapshot = await self._bluez.snapshot()
        if snapshot is None:
            return
        attempt.nearby = snapshot.adverts
        attempt.adapter = snapshot.adapter
        disc = attempt.discriminator
        if disc is not None:
            matching = [advert for advert in snapshot.adverts if disc.matches(advert.discriminator)]
            if matching and attempt.matched_address is None:
                attempt.matched_address = matching[0].address
                self._advance("found")
            if any(advert.connected and advert.address == attempt.matched_address for advert in matching):
                self._advance("connected")
        elapsed = (self._clock() - attempt.started_at).total_seconds()
        attempt.stuck_now = (
            attempt.phase == "searching"
            and snapshot.adapter is not None
            and not snapshot.adapter.discovering
            and elapsed >= self._stuck_after
        )

    async def _sample_loop(self) -> None:
        while True:
            await self.sample()
            await asyncio.sleep(self._interval)

    def _advance(self, phase: Phase, *, force: bool = False) -> None:
        attempt = self._attempt
        if attempt is None or attempt.phase in ("done", "failed"):
            return
        if force or _ORDER[phase] > _ORDER[attempt.phase]:
            attempt.phase = phase
            attempt.phase_since = self._clock()

    def _bluetooth(self, attempt: _Attempt) -> dict[str, object]:
        findings = self._kernel.findings() if self._kernel is not None else None
        now_usec = self._kernel.now_usec() if self._kernel is not None else None
        kernel_ok = findings is not None and now_usec is not None
        during = (
            counts_since(findings or [], attempt.started_usec)
            if kernel_ok and attempt.started_usec is not None
            else None
        )
        last_hour = counts_since(findings or [], (now_usec or 0) - _HOUR_USEC) if kernel_ok else None
        adapter = attempt.adapter
        return {
            "available": kernel_ok or adapter is not None,
            "adapter": adapter.name if adapter else None,
            "powered": adapter.powered if adapter else None,
            "discovering": adapter.discovering if adapter else None,
            "during_attempt": during,
            "last_hour": last_hour,
            "stuck_now": attempt.stuck_now,
        }

    def status(self) -> dict[str, object]:
        attempt = self._attempt
        body: dict[str, object] = {"bridge_started_at": _iso(self.started_at), "attempt": None}
        if attempt is None:
            return body
        disc = attempt.discriminator
        body["attempt"] = {
            "started_at": _iso(attempt.started_at),
            "discriminator": {"value": disc.value, "kind": disc.kind} if disc else None,
            "phase": attempt.phase,
            "phase_since": _iso(attempt.phase_since),
            "reason": attempt.reason,
            "nearby": [
                {
                    "address": advert.address,
                    "name": advert.name,
                    "rssi": advert.rssi,
                    "discriminator": advert.discriminator,
                    "vendor_id": advert.vendor_id,
                    "product_id": advert.product_id,
                    "connected": advert.connected,
                    "matches": disc.matches(advert.discriminator) if disc else False,
                }
                for advert in attempt.nearby
            ],
            "bluetooth": self._bluetooth(attempt),
        }
        return body
```

Note on `test_bluetooth_findings_are_counted_during_the_attempt_and_the_hour`:
the kernel fake's `now` stays fixed, so "last hour" includes the finding
30 minutes before `now` and the one 5 µs after. That is intended.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/matter/test_commissioning_progress.py -v`
Expected: all PASS.

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/matter/commissioning_progress.py tests/matter/test_commissioning_progress.py
git commit -m "feat(matter): follow a commissioning attempt through its phases

matter-server reports no commissioning steps. The tracker derives them from
what the host sees - the code's advertisement, a BLE connection to it, the
new node - and keeps nearby devices, Bluetooth faults and the failure reason
for the web UI.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `NODE_ADDED` reaches listeners

**Files:**
- Modify: `src/loxmatter/matter/client.py` (`BridgeMatterClient`: `__init__`, the `on_node_or_availability_event` callback around line 734)
- Modify: `tests/api/conftest.py` (`FakeMatterClient`)
- Test: `tests/matter/test_client.py`

**Interfaces:**
- Produces: `BridgeMatterClient.add_node_added_listener(self, listener: Callable[[int], None]) -> Callable[[], None]` (returns an unsubscribe function). `FakeMatterClient` in `tests/api/conftest.py` gets the same method plus `def emit_node_added(self, node_id: int) -> None` for tests.

- [ ] **Step 1: Read how `tests/matter/test_client.py` drives events**

Find the existing test that fires a `NODE_ADDED` through a fake upstream's
`subscribe_events` callback (search the file for `NODE_ADDED`). The new test
uses exactly that fake and the same way of triggering the callback.

- [ ] **Step 2: Write the failing test**

Append to `tests/matter/test_client.py`, modelled on that existing test:

```python
async def test_node_added_reaches_a_listener_until_it_unsubscribes(<same fixtures as the existing NODE_ADDED test>):
    """Design 2026-09-22, section 5.1: `joined` is the one intermediate signal
    matter-server gives. NODE_UPDATED is not a join."""
    seen: list[int] = []
    unsubscribe = client.add_node_added_listener(seen.append)
    <fire NODE_ADDED for node 7 exactly as the existing test does>
    <fire NODE_UPDATED for node 7>
    unsubscribe()
    <fire NODE_ADDED for node 8>
    assert seen == [7]
```

Replace the three `<…>` lines with the concrete calls from the existing test;
do not invent a new fake.

- [ ] **Step 3: Run and watch it fail**

Run: `uv run pytest tests/matter/test_client.py -v -k node_added_reaches`
Expected: `AttributeError: ... add_node_added_listener`.

- [ ] **Step 4: Implement**

In `BridgeMatterClient.__init__`, add `self._node_added_listeners: list[Callable[[int], None]] = []`.
Add the method:

```python
    def add_node_added_listener(self, listener: Callable[[int], None]) -> Callable[[], None]:
        """Call `listener(node_id)` on every `NODE_ADDED` from matter-server.

        For the commissioning tracker (design 2026-09-22, section 5.1): the
        event arrives before `commission_with_code` returns and is the only
        intermediate step matter-server reports. Returns the function that
        removes the listener again."""
        self._node_added_listeners.append(listener)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._node_added_listeners.remove(listener)

        return unsubscribe
```

In `on_node_or_availability_event`, inside the `elif event in (EventType.NODE_ADDED, EventType.NODE_UPDATED):`
branch, after the two `queue.put_nowait(...)` calls:

```python
                if event is EventType.NODE_ADDED:
                    for listener in list(self._node_added_listeners):
                        try:
                            listener(data.node_id)
                        except Exception:
                            logger.exception("A node-added listener failed")
```

(`contextlib` and `Callable` imports if not present.)

In `tests/api/conftest.py`, give `FakeMatterClient`:

```python
    def add_node_added_listener(self, listener):
        self._node_added_listeners = getattr(self, "_node_added_listeners", [])
        self._node_added_listeners.append(listener)
        return lambda: self._node_added_listeners.remove(listener)

    def emit_node_added(self, node_id: int) -> None:
        for listener in list(getattr(self, "_node_added_listeners", [])):
            listener(node_id)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/matter/test_client.py tests/matter/test_client_commissioning.py -v`
Expected: all PASS.

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/matter/client.py tests/matter/test_client.py tests/api/conftest.py
git commit -m "feat(matter): tell listeners when matter-server adds a node

NODE_ADDED is the one step of a commissioning attempt matter-server reports
before it returns; the commissioning tracker needs it for its joined phase.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The route drives the tracker; the status route

**Files:**
- Modify: `src/loxmatter/api/models.py` (`CommissionRequest`, new `DiscriminatorIn`)
- Modify: `src/loxmatter/api/devices.py` (`build_device_router`, `commission_device`, new status route)
- Modify: `src/loxmatter/loxone/server.py` (`build_app` passes the tracker)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `docs/superpowers/specs/2026-09-22-commissioning-feedback-design.md` (section 7.2, the "reason key" sentence)
- Test: `tests/api/test_devices.py` (or the file holding the existing commissioning route tests - search for `/api/devices/commission`)

**Interfaces:**
- Consumes (Tasks 3-4): `CommissioningTracker`, `Discriminator`, `classify_failure`, `Reason`; `add_node_added_listener`.
- Produces:
  - `class DiscriminatorIn(BaseModel): value: int = Field(ge=0, le=4095); kind: Literal["short", "long"]` (a `short` value above 15 → 422)
  - `CommissionRequest.discriminator: DiscriminatorIn | None = None`
  - `build_device_router(..., tracker: CommissioningTracker | None = None)`; `build_app(..., commissioning_tracker: CommissioningTracker | None = None)`
  - `GET /api/devices/commission/status` → `CommissioningTracker.status()`
  - i18n keys `api.devices.commission_reason_not_found`, `api.devices.commission_reason_not_found_any`, `api.devices.commission_reason_connection_lost`

- [ ] **Step 1: Write the failing tests**

In the file with the existing commissioning route tests, next to them, using
the same `api`/fake-client fixtures (read two existing tests first to copy the
fixture usage exactly):

```python
async def test_the_status_route_before_any_attempt(api):
    client, *_ = api  # unpack as the neighbouring tests do
    body = (await client.get("/api/devices/commission/status")).json()
    assert body["attempt"] is None
    assert body["bridge_started_at"].endswith("Z")


async def test_a_not_found_failure_names_the_discriminator_and_is_kept(api, <fake client fixture>):
    <make the fake client's commission_with_code raise CommissioningError with
     "Commissioning failed: Commission failed: discovery of node with discriminator 9 failed: No commissionable device was discovered">
    response = await client.post(
        "/api/devices/commission",
        json={"code": "34970112332", "discriminator": {"value": 9, "kind": "short"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == i18n.t("api.devices.commission_reason_not_found", discriminator=9)
    attempt = (await client.get("/api/devices/commission/status")).json()["attempt"]
    assert (attempt["phase"], attempt["reason"]) == ("failed", "not_found")


async def test_a_connection_loss_is_named(api, <fake client fixture>):
    <raise CommissioningError with "... No device could be commissioned (1 of 1 started attempt(s) failed, 1 discovered)">
    response = await client.post("/api/devices/commission", json={"code": "34970112332"})
    assert response.json()["detail"] == i18n.t("api.devices.commission_reason_connection_lost")


async def test_another_failure_keeps_matter_servers_text(api, <fake client fixture>):
    <raise CommissioningError("Commissioning failed: boom")>
    response = await client.post("/api/devices/commission", json={"code": "34970112332"})
    assert response.json()["detail"] == "Commissioning failed: boom"
    attempt = (await client.get("/api/devices/commission/status")).json()["attempt"]
    assert attempt["reason"] == "other"


async def test_a_success_ends_the_attempt_as_done_and_node_added_marks_joined(api, <fake client fixture>):
    <make commission_with_code call fake_client.emit_node_added(<node id>) before returning its snapshot, as a successful commissioning does>
    response = await client.post("/api/devices/commission", json={"code": "34970112332"})
    assert response.status_code == 201
    attempt = (await client.get("/api/devices/commission/status")).json()["attempt"]
    assert attempt["phase"] == "done"


async def test_a_short_discriminator_above_15_is_refused(api):
    response = await client.post(
        "/api/devices/commission", json={"code": "34970112332", "discriminator": {"value": 16, "kind": "short"}}
    )
    assert response.status_code == 422


async def test_the_status_route_is_guarded(<unauthenticated client fixture used by existing guard tests>):
    assert (await anonymous.get("/api/devices/commission/status")).status_code == 401
```

Replace every `<…>` with the concrete fixture code the neighbouring tests use.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/api/test_devices.py -v -k "status_route or not_found_failure or connection_loss or another_failure or success_ends or above_15"`
Expected: FAIL (404 on the status route, unknown field, wrong detail).

- [ ] **Step 3: Implement the model**

In `src/loxmatter/api/models.py`:

```python
class DiscriminatorIn(BaseModel):
    """The discriminator the browser decoded from the pairing code (design
    2026-09-22, section 4): 4 bits from a manual code, 12 from a QR payload.
    Only for showing progress - commissioning itself never depends on it."""

    model_config = ConfigDict(frozen=True)

    value: int = Field(ge=0, le=4095)
    kind: Literal["short", "long"]

    @model_validator(mode="after")
    def _short_fits_four_bits(self) -> DiscriminatorIn:
        if self.kind == "short" and self.value > 15:
            raise ValueError("a short discriminator has four bits")
        return self
```

and `discriminator: DiscriminatorIn | None = None` in `CommissionRequest`
(imports `Field`, `Literal`, `model_validator` as needed).

- [ ] **Step 4: Implement the route**

In `src/loxmatter/api/devices.py`:

1. `build_device_router(..., tracker: CommissioningTracker | None = None)`; at
   its top `progress = tracker or CommissioningTracker()`.
2. Add, above `commission_device`:

```python
    @router.get("/devices/commission/status")
    async def commission_status() -> dict[str, object]:
        """Design 2026-09-22, section 5.2. The dialog polls it every 2 s.

        Read-only: the POST route drives the sampling (see there), so a page
        that is not open costs nothing and changes nothing."""
        return progress.status()
```

3. Add a module-level helper next to `_commissioning_detail`:

```python
def _reason_detail(
    reason: Reason, exc: CommissioningError, discriminator: Discriminator | None
) -> str:
    if reason == "not_found":
        if discriminator is None:
            return i18n.t("api.devices.commission_reason_not_found_any")
        return i18n.t("api.devices.commission_reason_not_found", discriminator=discriminator.value)
    if reason == "connection_lost":
        return i18n.t("api.devices.commission_reason_connection_lost")
    return str(exc)
```

4. In `commission_device`, replace the `try: snapshot = await active_client.commission_with_code(...)`
   block with:

```python
        discriminator = (
            Discriminator(request.discriminator.value, request.discriminator.kind)
            if request.discriminator is not None
            else None
        )
        progress.start(discriminator)
        unsubscribe = active_client.add_node_added_listener(progress.node_added)

        async def sample_while_waiting() -> None:
            # The tracker never samples itself (Task 3): this route owns the
            # cadence, because it is the only one that runs for as long as the
            # attempt does. Driving it from the status route instead would tie
            # the phases to a browser polling, and a failure whose `found` or
            # `connected` phase nobody observed would be classified `other`
            # instead of `connection_lost`.
            while True:
                await progress.sample()
                await asyncio.sleep(progress.sample_interval)

        sampler = asyncio.ensure_future(sample_while_waiting())
        try:
            snapshot = await active_client.commission_with_code(request.code)
        except CommissioningError as exc:
            if missing_dataset_reason is not None:
                await progress.finish("no_thread_network")
                detail = _commissioning_detail(exc, missing_dataset_reason)
            else:
                reason = classify_failure(str(exc), progress.phase or "searching")
                await progress.finish(reason)
                detail = _reason_detail(reason, exc, discriminator)
            raise HTTPException(status_code=422, detail=detail) from exc
        except MatterUnavailableError as exc:
            await progress.finish("matter_server_unreachable")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            sampler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sampler
            unsubscribe()
```

5. At the very end of `commission_device`, directly before its `return`, add
   `await progress.finish(None)`.

- [ ] **Step 5: Pass the tracker through `build_app`**

`src/loxmatter/loxone/server.py`: `build_app(..., commissioning_tracker: CommissioningTracker | None = None)`
and pass `tracker=commissioning_tracker` into `build_device_router(...)`.

- [ ] **Step 6: The strings**

In `src/loxmatter/i18n/strings.yaml`, next to `api.devices.commissioning_thread_cause`:

```yaml
api.devices.commission_reason_not_found:
  en: "No device with discriminator {discriminator} is in range, or it is not ready for pairing. Check the code, put the device into pairing mode, and keep it close to the bridge."
  de: "Kein Gerät mit der Kennung {discriminator} ist in Reichweite, oder es ist nicht zum Einlernen bereit. Prüfen Sie den Code, versetzen Sie das Gerät in den Pairing-Modus und halten Sie es nahe an die Bridge."
api.devices.commission_reason_not_found_any:
  en: "No device with this code is in range, or it is not ready for pairing. Check the code, put the device into pairing mode, and keep it close to the bridge."
  de: "Kein Gerät mit diesem Code ist in Reichweite, oder es ist nicht zum Einlernen bereit. Prüfen Sie den Code, versetzen Sie das Gerät in den Pairing-Modus und halten Sie es nahe an die Bridge."
api.devices.commission_reason_connection_lost:
  en: "The device was found, but the Bluetooth connection to it broke off."
  de: "Das Gerät wurde gefunden, aber die Bluetooth-Verbindung zu ihm ist abgebrochen."
```

- [ ] **Step 7: Amend the spec**

In the spec, section 7.2, replace the sentence starting "The `POST` keeps its
status codes; its `detail` becomes…" with: "The `POST` keeps its status codes
and its string `detail`, now the text of the reason through `i18n.t(...)`. The
reason itself is read from the status route (`attempt.reason`), which the
dialog reads after a failure anyway; `readError` in `web/app.js` stays as it
is."

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/api/test_devices.py tests/test_i18n.py -v` (and any
other file under `tests/api/` that the search in Step 1 found)
Expected: all PASS.

- [ ] **Step 9: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/api src/loxmatter/loxone/server.py src/loxmatter/i18n/strings.yaml tests/api docs/superpowers/specs/2026-09-22-commissioning-feedback-design.md
git commit -m "feat(api): report a commissioning attempt's phase and name why it failed

POST /api/devices/commission now drives the tracker, answers 'not found' and
'connection lost' in the user's language, and GET
/api/devices/commission/status reports the running or last attempt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The diagnostics page gets a Bluetooth line

**Files:**
- Modify: `src/loxmatter/api/diagnostics.py` (`build_diagnostics_router`, `system()`, new `_check_bluetooth`)
- Modify: `src/loxmatter/loxone/server.py` (pass the kernel log)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: the existing diagnostics test file (search `tests/` for `/api/diagnostics/system`)

**Interfaces:**
- Consumes (Task 2): `KernelLog`, `counts_since`, `CATEGORIES`.
- Produces: `build_diagnostics_router(..., kernel_log: KernelLog | None = None)`; `build_app(..., kernel_log: KernelLog | None = None)`; check name `"bluetooth"`; i18n keys `api.diagnostics.bluetooth_ok`, `api.diagnostics.bluetooth_findings`, `api.diagnostics.bluetooth_not_available`, `api.diagnostics.bluetooth_category_transport`, `…_stuck`, `…_power`.

- [ ] **Step 1: Write the failing tests** (in the diagnostics test file, with its existing fixture)

```python
def _kernel(tmp_path, lines, uptime="100.0 0"):
    log = tmp_path / "kmsg"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    up = tmp_path / "uptime"
    up.write_text(uptime, encoding="utf-8")
    return KernelLog(log, up)


def test_bluetooth_without_findings_is_ok(tmp_path):
    ok, text = _check_bluetooth(_kernel(tmp_path, []))
    assert ok is True
    assert text == i18n.t("api.diagnostics.bluetooth_ok")


def test_bluetooth_findings_of_the_last_hour_are_a_warning(tmp_path):
    ok, text = _check_bluetooth(
        _kernel(tmp_path, ["3,1,90000000,-;Bluetooth: hci0: Frame reassembly failed (-84)",
                           "2,2,95000000,-;hwmon hwmon1: Undervoltage detected!"])
    )
    assert ok is False
    assert i18n.t("api.diagnostics.bluetooth_category_transport", count=1) in text
    assert i18n.t("api.diagnostics.bluetooth_category_power", count=1) in text


def test_bluetooth_is_not_available_without_the_kernel_log(tmp_path):
    ok, text = _check_bluetooth(KernelLog(tmp_path / "missing", tmp_path / "missing"))
    assert ok is True
    assert text == i18n.t("api.diagnostics.bluetooth_not_available")
    assert _check_bluetooth(None) == (True, i18n.t("api.diagnostics.bluetooth_not_available"))
```

and one route-level test asserting `"bluetooth"` appears among the check names
of `GET /api/diagnostics/system`, written like the file's existing route tests.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest <diagnostics test file> -v -k bluetooth`

- [ ] **Step 3: Implement**

```python
_HOUR_USEC: Final = 3600 * 1_000_000


def _check_bluetooth(kernel_log: KernelLog | None) -> tuple[bool, str]:
    """Bluetooth and power faults of the last hour (design 2026-09-22, 7.1).

    "Not available" is green: a host whose kernel log this bridge may not read
    is not broken. Only counted categories are shown, never a kernel line."""
    if kernel_log is None:
        return True, i18n.t("api.diagnostics.bluetooth_not_available")
    findings = kernel_log.findings()
    now = kernel_log.now_usec()
    if findings is None or now is None:
        return True, i18n.t("api.diagnostics.bluetooth_not_available")
    counts = counts_since(findings, now - _HOUR_USEC)
    parts = [
        i18n.t(f"api.diagnostics.bluetooth_category_{category}", count=counts[category])
        for category in CATEGORIES
        if counts[category]
    ]
    if not parts:
        return True, i18n.t("api.diagnostics.bluetooth_ok")
    return False, i18n.t("api.diagnostics.bluetooth_findings", findings="; ".join(parts))
```

`build_diagnostics_router(..., kernel_log: KernelLog | None = None)` and in
`system()` after the `thread` check:
`_run_check("bluetooth", lambda: _check_bluetooth(kernel_log)),` (a fixed English
id, like its neighbours). `build_app(..., kernel_log: KernelLog | None = None)`
passes it on.

Strings:

```yaml
api.diagnostics.bluetooth_ok:
  en: "No Bluetooth or power faults in the kernel log in the last hour."
  de: "Keine Bluetooth- oder Stromversorgungsfehler im Kernel-Log der letzten Stunde."
api.diagnostics.bluetooth_findings:
  en: "Kernel log, last hour: {findings}. A USB Bluetooth adapter or a stronger power supply usually helps."
  de: "Kernel-Log, letzte Stunde: {findings}. Ein USB-Bluetooth-Adapter oder ein stärkeres Netzteil hilft meist."
api.diagnostics.bluetooth_not_available:
  en: "Not available: this bridge cannot read the kernel log."
  de: "Nicht verfügbar: Diese Bridge kann das Kernel-Log nicht lesen."
api.diagnostics.bluetooth_category_transport:
  en: "{count} Bluetooth transmission errors"
  de: "{count} Bluetooth-Übertragungsfehler"
api.diagnostics.bluetooth_category_stuck:
  en: "{count} times the Bluetooth adapter stopped responding"
  de: "{count}-mal reagierte der Bluetooth-Adapter nicht mehr"
api.diagnostics.bluetooth_category_power:
  en: "{count} undervoltage warnings"
  de: "{count} Unterspannungswarnungen"
```

- [ ] **Step 4: Run the tests** — the diagnostics test file and `tests/test_i18n.py`. Expected: PASS.

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/api/diagnostics.py src/loxmatter/loxone/server.py src/loxmatter/i18n/strings.yaml tests
git commit -m "feat(diagnostics): report Bluetooth and power faults of the last hour

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Wire it into `loxmatter run` and the Compose file

**Files:**
- Modify: `src/loxmatter/cli.py` (`_run`, where `build_app(...)` is called)
- Modify: `deploy/testhost/docker-compose.yml` (service `loxmatter`)
- Test: `tests/test_cli.py`, `tests/test_compose_profiles.py`

- [ ] **Step 1: `cli._run`**

Import `BluezReader`, `KernelLog`, `CommissioningTracker`. Before `build_app(...)`:

```python
        # Design 2026-09-22: both read-only host sources the commissioning
        # dialog and the diagnostics page use. Each reads as "not available"
        # when its mount is missing.
        kernel_log = KernelLog()
        commissioning_tracker = CommissioningTracker(bluez=BluezReader(), kernel=kernel_log)
```

and pass `commissioning_tracker=commissioning_tracker, kernel_log=kernel_log`
to `build_app(...)`.

- [ ] **Step 2: Compose**

In `deploy/testhost/docker-compose.yml`, service `loxmatter`, add to its
`volumes:` list and a new `devices:` key, each with a comment in the file's
style:

```yaml
      # Read-only: BlueZ's Matter advertisements and adapter state for the
      # commissioning dialog (design 2026-09-22, section 6). The same socket
      # matter-server uses for BLE. Reading only - the bridge never scans.
      - /run/dbus:/run/dbus:ro
```

```yaml
    # Read-only: Bluetooth and power faults from the kernel log (design
    # 2026-09-22, section 7.1). `dmesg` is blocked by Docker's seccomp profile;
    # the device node is not. Only counted categories reach the web UI.
    devices:
      - /dev/kmsg:/dev/kmsg:r
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_cli.py tests/test_compose_profiles.py -v`
Expected: PASS. If `tests/test_compose_profiles.py` asserts the exact set of
keys or mounts of the `loxmatter` service, update that assertion to include
the two new entries and say so in the report.

Also validate the file: `docker compose -f deploy/testhost/docker-compose.yml config -q`
if Docker is available on the machine; otherwise note that it was not run.

- [ ] **Step 4: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/cli.py deploy/testhost/docker-compose.yml tests
git commit -m "feat(run): give the bridge read-only BlueZ and kernel-log access

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The browser decodes the pairing code

**Files:**
- Modify: `src/loxmatter/web/app.js` (top-level helpers next to `normalizePairingCode`, `commissionDevice`)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_web.py` (`_app_state` tail exports; new tests)

**Interfaces:**
- Produces: top-level `function decodePairingCode(raw)` → `{kind: "short"|"long", discriminator: number}` | `{kind: "typo"}` | `{kind: "unknown"}`; the `POST` body gains `discriminator: {value, kind}` when known; i18n key `web.devices.commission_code_typo`.

- [ ] **Step 1: Export the function to the node harness**

In `tests/api/test_web.py`, `_app_state`, change the `tail` line so the
function is reachable from `setup`:

```python
    tail = json.dumps(
        "\n" + fill_strings + "globalThis.t = t;\nglobalThis.decodePairingCode = decodePairingCode;\nreturn app();"
    )
```

- [ ] **Step 2: Write the failing tests**

```python
@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_pairing_code_names_its_discriminator():
    """Design 2026-09-22, section 4, with the Matter specification's own test
    vector: manual code 34970112332 and QR MT:Y.K9042C00KA0648G00 both name
    discriminator 3840 (short 15). Fault to prove it: shift chunk 2 by 13."""
    values = _app_state(
        """
        const codes = ["34970112332", "3497-011-2332", "34970112333",
                       "MT:Y.K9042C00KA0648G00", "mt:y.k9042c00ka0648g00", "abc", "1234", ""];
        console.log(JSON.stringify(Object.fromEntries(codes.map((c) => [c, decodePairingCode(c)]))));
        """
    )
    assert values["34970112332"] == {"kind": "short", "discriminator": 15}
    assert values["3497-011-2332"] == {"kind": "short", "discriminator": 15}
    assert values["34970112333"] == {"kind": "typo"}
    assert values["MT:Y.K9042C00KA0648G00"] == {"kind": "long", "discriminator": 3840}
    assert values["mt:y.k9042c00ka0648g00"] == {"kind": "long", "discriminator": 3840}
    assert values["abc"] == {"kind": "unknown"}
    assert values["1234"] == {"kind": "unknown"}
    assert values[""] == {"kind": "unknown"}


def test_the_typo_string_exists_in_both_languages():
    from loxmatter import i18n

    entry = i18n._STRINGS["web.devices.commission_code_typo"]
    assert entry.get("en") and entry.get("de") and entry["en"] != entry["de"]
```

- [ ] **Step 3: Run and watch them fail**

Run: `uv run pytest tests/api/test_web.py -v -k "names_its_discriminator or typo_string"`

- [ ] **Step 4: Implement**

In `src/loxmatter/web/app.js`, directly after `function normalizePairingCode(raw) {…}`
(this code was run against the specification's vectors while planning):

```js
// Pairing-code decoding (design 2026-09-22, section 4). Only the
// discriminator is read - it lets the dialog follow the device in BlueZ and
// name it in a "not found" message. The passcode is never decoded.
const VERHOEFF_D = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
  [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
  [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
  [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
  [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
];
const VERHOEFF_P = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
  [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
  [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
  [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
];
const BASE38_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.";

function verhoeffValid(digits) {
  let check = 0;
  const reversed = digits.split("").reverse();
  for (let i = 0; i < reversed.length; i++) {
    check = VERHOEFF_D[check][VERHOEFF_P[i % 8][Number(reversed[i])]];
  }
  return check === 0;
}

function base38Bytes(text) {
  const bytes = [];
  for (let i = 0; i < text.length; i += 5) {
    const chunk = text.slice(i, i + 5);
    const count = { 5: 3, 4: 2, 2: 1 }[chunk.length];
    if (count === undefined) return null;
    let value = 0;
    for (let j = chunk.length - 1; j >= 0; j--) {
      const digit = BASE38_ALPHABET.indexOf(chunk[j]);
      if (digit < 0) return null;
      value = value * 38 + digit;
    }
    for (let k = 0; k < count; k++) {
      bytes.push(value & 0xff);
      value = Math.floor(value / 256);
    }
  }
  return bytes;
}

function bitsAt(bytes, start, length) {
  let value = 0;
  for (let i = 0; i < length; i++) {
    const bit = start + i;
    if ((bytes[bit >> 3] >> (bit & 7)) & 1) value |= 1 << i;
  }
  return value;
}

function decodePairingCode(raw) {
  const text = String(raw ?? "").trim();
  if (/^MT:/i.test(text)) {
    // version 3, vendor 16, product 16, flow 2, capabilities 8,
    // discriminator 12 bits from bit 45 on.
    const bytes = base38Bytes(text.slice(3).toUpperCase());
    if (!bytes || bytes.length < 11) return { kind: "unknown" };
    return { kind: "long", discriminator: bitsAt(bytes, 45, 12) };
  }
  const digits = text.replace(/[\s-]/g, "");
  if (!/^\d+$/.test(digits) || (digits.length !== 11 && digits.length !== 21)) {
    return { kind: "unknown" };
  }
  if (!verhoeffValid(digits)) return { kind: "typo" };
  const chunk1 = Number(digits[0]);
  const chunk2 = Number(digits.slice(1, 6));
  if (((chunk1 >> 2) & 1) !== (digits.length === 21 ? 1 : 0)) return { kind: "unknown" };
  return { kind: "short", discriminator: ((chunk1 & 0x3) << 2) | (chunk2 >> 14) };
}
```

In `commissionDevice()`, directly after the empty-code check:

```js
      const decoded = decodePairingCode(this.commissionCode);
      if (decoded.kind === "typo") {
        this.commissionMessage = t("web.devices.commission_code_typo");
        this.commissionMessageIsError = true;
        return;
      }
```

and after `const body = { code };`:

```js
        if (decoded.kind === "short" || decoded.kind === "long") {
          body.discriminator = { value: decoded.discriminator, kind: decoded.kind };
        }
        this.commissionDiscriminator = body.discriminator ?? null;
```

Add `commissionDiscriminator: null,` next to `commissionRunCode: "",` in the
state object.

String:

```yaml
web.devices.commission_code_typo:
  en: "This code has a typo — check the digits."
  de: "Dieser Code enthält einen Tippfehler – prüfen Sie die Ziffern."
```

- [ ] **Step 5: Run the web tests for commissioning**

Run: `uv run pytest tests/api/test_web.py -v -k "commission or pairing or discriminator or typo"`
Expected: all PASS.

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
git add src/loxmatter/web/app.js src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): read the discriminator from the pairing code and catch typos

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The dialog shows phases, nearby devices, warnings and reasons

**Files:**
- Modify: `src/loxmatter/web/app.js` (state, `commissionDevice`, `commissionStepClass`, new helpers)
- Modify: `src/loxmatter/web/index.html` (the `commissionStep !== null` block, lines ~584-620)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Modify: `tests/api/test_web.py` (the tests pinning the old two-step list, around lines 3100-3160, and new tests)

**Interfaces:**
- Consumes (Tasks 5, 8): `GET /api/devices/commission/status`; `commissionDiscriminator`.
- Produces (app.js state/methods): `commissionStatus` (last status body or `null`), `commissionPollTimer`, `COMMISSION_POLL_MS = 2000`, `COMMISSION_PHASES = ["searching", "found", "connected", "joined", "done"]`, `commissionPhaseClass(phase)`, `commissionPhaseHint()`, `commissionNearby()`, `commissionBluetoothWarnings()`, `startCommissionPolling()`, `stopCommissionPolling()`.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_web.py`, a helper and tests (next to the other commissioning tests):

```python
def _commission_values(setup: str) -> dict:
    return _app_state(setup)


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_phase_list_follows_the_status_route():
    """Design 2026-09-22, section 5.3. Fault to prove it: mark the current
    phase `done` instead of `running`."""
    values = _commission_values(
        """
        state.commissionStep = 0;
        state.commissionStatus = { attempt: { phase: "connected", reason: null, nearby: [], bluetooth: { available: false } } };
        const out = Object.fromEntries(["searching","found","connected","joined","done"].map((p) => [p, state.commissionPhaseClass(p)]));
        state.commissionFailed = true;
        state.commissionStatus = { attempt: { phase: "failed", reason: "not_found", nearby: [], bluetooth: { available: false } } };
        out.failedSearching = state.commissionPhaseClass("searching");
        console.log(JSON.stringify(out));
        """
    )
    assert values["searching"] == "done"
    assert values["found"] == "done"
    assert values["connected"] == "running"
    assert values["joined"] == ""
    assert values["done"] == ""
    assert values["failedSearching"] == "failed"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_nearby_list_marks_the_device_the_code_names():
    values = _commission_values(
        """
        state.commissionStatus = { attempt: { phase: "searching", nearby: [
          { address: "FB", name: "LED Light0x07C2", rssi: -60, discriminator: 1059, vendor_id: 4476, product_id: 36865, connected: false, matches: false },
          { address: "E0", name: null, rssi: -70, discriminator: 261, vendor_id: 4476, product_id: 36871, connected: false, matches: true } ],
          bluetooth: { available: true } } };
        console.log(JSON.stringify(state.commissionNearby()));
        """
    )
    assert [entry["address"] for entry in values] == ["E0", "FB"]
    assert values[0]["matches"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_bluetooth_warnings_follow_the_attempts_findings():
    values = _commission_values(
        """
        const bt = (b) => { state.commissionStatus = { attempt: { phase: "searching", nearby: [], bluetooth: b } }; return state.commissionBluetoothWarnings(); };
        console.log(JSON.stringify({
          none: bt({ available: true, during_attempt: { transport: 0, stuck: 0, power: 0 }, stuck_now: false }),
          transport: bt({ available: true, during_attempt: { transport: 3, stuck: 0, power: 0 }, stuck_now: false }),
          stuckNow: bt({ available: true, during_attempt: { transport: 0, stuck: 0, power: 0 }, stuck_now: true }),
          power: bt({ available: true, during_attempt: { transport: 0, stuck: 0, power: 1 }, stuck_now: false }),
          unavailable: bt({ available: false }),
        }));
        """
    )
    assert values["none"] == []
    assert values["transport"] == ["web.devices.commission_bt_transport"]
    assert values["stuckNow"] == ["web.devices.commission_bt_stuck"]
    assert values["power"] == ["web.devices.commission_bt_power"]
    assert values["unavailable"] == []
```

and a markup test with the `api` fixture (served page, `_without_comments`),
asserting the five phase keys `web.devices.commission_phase_<phase>` appear,
`x-for="entry in commissionNearby()"` appears, and
`x-for="key in commissionBluetoothWarnings()"` appears.

Update the existing tests that pin the old list (around lines 3100-3160 of
`tests/api/test_web.py`: `commissionStepClass(0)`, `commission_step_joining`,
…) to the new structure instead of deleting them: they keep checking that the
progress block only shows while `commissionStep !== null` and that the retry
button still calls `resetCommission()`.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/api/test_web.py -v -k "phase_list or nearby_list or bluetooth_warnings or commission"`

- [ ] **Step 3: Implement the state and helpers**

In the state object next to `commissionDiscriminator`:

```js
    // Design 2026-09-22, section 5.3: the last answer of
    // GET /api/devices/commission/status while an attempt runs, and the
    // timer that polls it.
    commissionStatus: null,
    commissionPollTimer: null,
    commissionBridgeStartedAt: null,
```

Top-level constants next to the other commissioning constants:

```js
const COMMISSION_POLL_MS = 2000;
const COMMISSION_PHASES = ["searching", "found", "connected", "joined", "done"];
```

Methods (replace `commissionStepClass`; keep `commissionStep` as the
"progress block visible" flag it already is):

```js
    commissionPhaseClass(phase) {
      const attempt = this.commissionStatus?.attempt;
      const current = attempt?.phase === "failed" ? null : (attempt?.phase ?? "searching");
      const index = COMMISSION_PHASES.indexOf(phase);
      if (this.commissionFailed) {
        // The phase the attempt had reached is the one that failed.
        const reached = this.commissionReachedPhase ?? "searching";
        const reachedIndex = COMMISSION_PHASES.indexOf(reached);
        if (index < reachedIndex) return "done";
        return index === reachedIndex ? "failed" : "";
      }
      const currentIndex = COMMISSION_PHASES.indexOf(current);
      if (index < currentIndex) return "done";
      if (index === currentIndex) return current === "done" ? "done" : "running";
      return "";
    },

    commissionPhaseHint() {
      const phase = this.commissionStatus?.attempt?.phase;
      if (phase === "searching") return t("web.devices.commission_hint_searching");
      if (phase === "found" || phase === "connected") return t("web.devices.commission_hint_setup");
      return "";
    },

    commissionNearby() {
      const nearby = this.commissionStatus?.attempt?.nearby ?? [];
      return [...nearby].sort((a, b) => Number(b.matches) - Number(a.matches) || (b.rssi ?? -999) - (a.rssi ?? -999));
    },

    commissionBluetoothWarnings() {
      const bt = this.commissionStatus?.attempt?.bluetooth;
      if (!bt || !bt.available) return [];
      const during = bt.during_attempt ?? {};
      const keys = [];
      if (during.transport > 0) keys.push("web.devices.commission_bt_transport");
      if (during.stuck > 0 || bt.stuck_now) keys.push("web.devices.commission_bt_stuck");
      if (during.power > 0) keys.push("web.devices.commission_bt_power");
      return keys;
    },

    startCommissionPolling() {
      this.stopCommissionPolling();
      const poll = async () => {
        try {
          this.commissionStatus = await requestJson("GET", "/api/devices/commission/status");
          const phase = this.commissionStatus?.attempt?.phase;
          if (phase && phase !== "failed" && phase !== "done") this.commissionReachedPhase = phase;
        } catch {
          // The next poll tries again; a dead bridge is decided in commissionDevice.
        }
      };
      poll();
      this.commissionPollTimer = setInterval(poll, COMMISSION_POLL_MS);
    },

    stopCommissionPolling() {
      if (this.commissionPollTimer !== null) {
        clearInterval(this.commissionPollTimer);
        this.commissionPollTimer = null;
      }
    },
```

(add `commissionReachedPhase: null,` to the state next to `commissionStatus`.)

In `commissionDevice()`:

- before the `POST`: read the bridge start once and start polling:

```js
        try {
          const before = await requestJson("GET", "/api/devices/commission/status");
          this.commissionBridgeStartedAt = before?.bridge_started_at ?? null;
        } catch {
          this.commissionBridgeStartedAt = null;
        }
        this.commissionReachedPhase = "searching";
        this.startCommissionPolling();
```

- in `catch (error)`, before building the message:

```js
        this.stopCommissionPolling();
        let restarted = false;
        if (error.status === undefined || error.status >= 500) {
          try {
            const after = await requestJson("GET", "/api/devices/commission/status");
            restarted =
              this.commissionBridgeStartedAt !== null &&
              after?.bridge_started_at !== this.commissionBridgeStartedAt;
            this.commissionStatus = after;
          } catch {
            restarted = false;
          }
        } else {
          try {
            this.commissionStatus = await requestJson("GET", "/api/devices/commission/status");
          } catch {
            // Keep the last polled status.
          }
        }
        const message = String(error.message ?? "");
        this.commissionMessage = restarted
          ? t("web.devices.commission_bridge_restarted")
          : error.status === 422
            ? message
            : t("web.devices.commission_failed", { message });
```

- in `finally`: `this.stopCommissionPolling();`
- after the successful `POST`: `this.commissionReachedPhase = "done";` and one
  last `this.commissionStatus = await requestJson("GET", "/api/devices/commission/status").catch(() => this.commissionStatus);`

- [ ] **Step 4: Implement the markup**

Replace the `<ol class="commission-steps">…</ol>` inside
`<div x-show="commissionStep !== null" x-cloak>` with:

```html
            <!-- Design 2026-09-22, section 5.3: the phases the bridge derives
                 from BlueZ and NODE_ADDED, polled every 2 s. -->
            <p class="hint" x-show="commissionDiscriminator" x-cloak
               x-text="t('web.devices.commission_looking_for', { discriminator: commissionDiscriminator?.value })"></p>
            <ol class="commission-steps">
              <template x-for="phase in ['searching', 'found', 'connected', 'joined', 'done']" :key="phase">
                <li class="commission-step" :class="commissionPhaseClass(phase)">
                  <span class="step-marker" aria-hidden="true"></span>
                  <span x-text="t('web.devices.commission_phase_' + phase)"></span>
                </li>
              </template>
            </ol>
            <p class="hint" x-show="commissionPhaseHint()" x-cloak x-text="commissionPhaseHint()"></p>
            <template x-for="key in commissionBluetoothWarnings()" :key="key">
              <p class="banner warn" x-text="t(key)"></p>
            </template>
            <div x-show="commissionNearby().length" x-cloak>
              <p class="hint" x-text="t('web.devices.commission_nearby_heading')"></p>
              <ul class="commission-nearby">
                <template x-for="entry in commissionNearby()" :key="entry.address">
                  <li :class="entry.matches ? 'match' : ''"
                      x-text="t('web.devices.commission_nearby_entry', { name: entry.name ?? entry.address, discriminator: entry.discriminator, rssi: entry.rssi })"></li>
                </template>
              </ul>
            </div>
```

Add minimal CSS in `src/loxmatter/web/style.css` next to `.commission-steps`:
`.commission-nearby li.match { font-weight: 600; }`.

- [ ] **Step 5: The strings**

```yaml
web.devices.commission_looking_for:
  en: "Looking for the device with discriminator {discriminator}…"
  de: "Suche nach dem Gerät mit der Kennung {discriminator}…"
web.devices.commission_phase_searching:
  en: "Searching for the device"
  de: "Gerät wird gesucht"
web.devices.commission_phase_found:
  en: "Device found"
  de: "Gerät gefunden"
web.devices.commission_phase_connected:
  en: "Connected — setting the device up"
  de: "Verbunden – Gerät wird eingerichtet"
web.devices.commission_phase_joined:
  en: "Device joined — loading signals and commands"
  de: "Gerät beigetreten – Signale und Befehle werden geladen"
web.devices.commission_phase_done:
  en: "Done"
  de: "Fertig"
web.devices.commission_hint_searching:
  en: "Searching over Bluetooth usually takes seconds, at most 3 minutes."
  de: "Die Suche über Bluetooth dauert meist Sekunden, höchstens 3 Minuten."
web.devices.commission_hint_setup:
  en: "Setting a device up over Bluetooth usually takes 1–2 minutes."
  de: "Die Einrichtung über Bluetooth dauert meist 1–2 Minuten."
web.devices.commission_nearby_heading:
  en: "Matter devices advertising nearby:"
  de: "Matter-Geräte in der Nähe:"
web.devices.commission_nearby_entry:
  en: "{name} — discriminator {discriminator}, signal {rssi} dBm"
  de: "{name} – Kennung {discriminator}, Signal {rssi} dBm"
web.devices.commission_bt_transport:
  en: "The Bluetooth chip reports transmission errors. A USB Bluetooth adapter usually fixes this; select it on the radios card."
  de: "Der Bluetooth-Chip meldet Übertragungsfehler. Ein USB-Bluetooth-Adapter behebt das meist; wählen Sie ihn auf der Funkkarte aus."
web.devices.commission_bt_stuck:
  en: "The Bluetooth adapter stopped scanning. Restarting the bridge host usually clears it; a USB Bluetooth adapter avoids it."
  de: "Der Bluetooth-Adapter sucht nicht mehr. Ein Neustart des Bridge-Rechners behebt das meist; ein USB-Bluetooth-Adapter vermeidet es."
web.devices.commission_bt_power:
  en: "The Raspberry Pi reports undervoltage. Use the official power supply for this Raspberry Pi model."
  de: "Der Raspberry Pi meldet Unterspannung. Verwenden Sie das offizielle Netzteil für dieses Raspberry-Pi-Modell."
web.devices.commission_bridge_restarted:
  en: "The bridge restarted during commissioning. Check whether the device appears in the list; otherwise try again."
  de: "Die Bridge wurde während des Einlernens neu gestartet. Prüfen Sie, ob das Gerät in der Liste erscheint; sonst versuchen Sie es erneut."
```

Remove `web.devices.commission_step_joining` and `…_step_loading` only if no
other markup or test uses them after this task (search first).

- [ ] **Step 6: Run the web tests**

Run: `uv run pytest tests/api/test_web.py -v -k "commission or phase or nearby or bluetooth"`
then `uv run pytest tests/api/test_web.py -q` (the whole file, ~2 min) and
`uv run pytest tests/test_i18n.py -q`.
Expected: all PASS.

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): show commissioning phases, nearby devices and Bluetooth faults

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 8 (controller): browser harness**

The controller renders the dialog against a stub status route in a throwaway
harness (not committed) and checks the phase list, the nearby list and a
warning banner.

---

### Task 10: Documentation

**Files:** `deploy/testhost/README.md`, `CHANGELOG.md`

- [ ] **Step 1: README** — in the section describing the `loxmatter` service's
  mounts and its security reasoning (search for `fabric-backup` or
  `network_mode: host` rationale), add a paragraph: the bridge reads BlueZ over
  `/run/dbus` (read-only, never scans) and the kernel log through `/dev/kmsg`
  (read-only), only to show commissioning progress and Bluetooth faults; only
  counted categories reach the web UI; without either mount the dialog still
  works and those parts read "not available".

- [ ] **Step 2: CHANGELOG** — under `## [Unreleased]`, `### Added`, in the
  file's bold-lead style:

```markdown
- **Commissioning shows what it is doing.** The dialog follows the device
  from "searching" to "found", "connected" and "joined", lists the Matter
  devices advertising nearby, and warns when the Bluetooth chip reports
  errors or the Raspberry Pi reports undervoltage.
- **A failed commissioning says why.** "No device with discriminator 9 is in
  range" or "the Bluetooth connection broke off" instead of the raw error, and
  a code with a typo is caught before anything is sent.
```

- [ ] **Step 3: Commit**

```bash
uv run ruff format --check . && uv run python scripts/check_language.py
git add deploy/testhost/README.md CHANGELOG.md
git commit -m "docs: the bridge's read-only Bluetooth access, and the change notes

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: On hardware (pi3-andi) — needs the owner's go-ahead

Run the branch's bridge on `pi3-andi` (build on the Pi with
`docker compose build loxmatter` from the branch checkout, or a `dev` image),
with the new mounts, and record in the spec as a dated note:

1. A code for no device in range → `not_found` after ~180 s; the nearby list.
2. A real device → the phases; time per phase.
3. The Bluetooth warning when the on-board chip misbehaves; the diagnostics
   line (undervoltage was already present on 22 September).
4. The bridge restarted mid-attempt → the `bridge_restarted` text.

Restore the stable image afterwards.

---

## Self-review

- Spec 4 → Task 8; 5.1 → Tasks 3-4; 5.2 → Task 5; 5.3 → Task 9; 6 → Tasks 1, 3, 9;
  7.1 → Tasks 2, 3, 6, 9; 7.2 → Tasks 3, 5, 9; 8 → Tasks 1, 2, 7, 10; 9 → every
  task's tests; 10 → Task 11.
- Names across tasks: `BluezReader.snapshot`, `BluezSnapshot`, `MatterAdvert`,
  `AdapterState`, `KernelLog.findings/now_usec`, `counts_since`, `CATEGORIES`,
  `Discriminator.matches`, `classify_failure`, `CommissioningTracker.start/sample/node_added/finish/status/phase`,
  `add_node_added_listener`, `DiscriminatorIn`, `decodePairingCode`,
  `commissionPhaseClass`, `commissionNearby`, `commissionBluetoothWarnings`.
