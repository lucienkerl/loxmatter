# Radios in the Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user choose, in the web UI, which USB stick the Thread border router uses (or none) and which Bluetooth adapter matter-server uses, applied by a strictly validated job in the updater sidecar that recreates only `otbr` or `matter-server`, verifies the result and rolls back on failure.

**Architecture:** The bridge detects radios from `/host/dev/serial/by-id` and `/sys` (`loxmatter/radios/inventory.py`), reads the sidecar's report and writes requests as files (`loxmatter/radios/sidecar.py`), and serves `GET/POST /api/radios` (`api/radios.py`) to a new settings card. A new sidecar script `deploy/updater/radios-once.sh`, run by `entrypoint.sh` after `update-once.sh` in the same loop, reports the effective configuration from `.env` on every pass and executes requests. `update-once.sh` is not modified.

**Tech Stack:** Python 3.12, FastAPI, POSIX `sh` + `jq` in Alpine (busybox), Docker Compose, Alpine.js (vendored), pytest with fake-binary harnesses, node for `app.js` tests, Playwright for the visual check.

**Spec:** `docs/superpowers/specs/2026-09-11-radios-in-the-web-ui-design.md` — read sections 2, 4–9 before starting any task.

## Global Constraints

- Worktree (absolute path, use it in every command): `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003`, branch `claude/radios-in-web-ui`.
- Everything is written in English: code, comments, docstrings, test names, commit messages. German appears only as `de:` values in `src/loxmatter/i18n/strings.yaml`.
- Every string a user can see goes through i18n with an `en` and a `de` value (`i18n.t(...)` in Python, `t(...)` in `app.js`).
- New source, script and test files start with the 15-line GPL header copied verbatim from `src/loxmatter/profiles/categories.py` lines 1–15 (in shell scripts, after the `#!/bin/sh` line, as `update-once.sh` does).
- `deploy/updater/update-once.sh` is not modified by any task.
- The sidecar runs Alpine busybox `sh`: no bashisms (`[[`, arrays, `local` is avoided, `pipefail` does not exist). `jq`, `curl`, `readlink -f`, `cksum`, `date -r`, `timeout` (coreutils) are available.
- Every new test that names a protection must be shown to catch it: introduce the stated fault, run the test, see it FAIL, revert, see it PASS. Paste both outputs into the task report. A test that stays green with the fault in place is wrong and must be rewritten.
- Temporary behaviour a later task replaces is marked `# TRANSITIONAL (Task N)`; Task 9 greps that none remain.
- Run the full suite in the FOREGROUND with a Bash timeout of 600000 ms (about eight minutes). Never start it in the background and wait for a notification — none will reach you.
- Run `uv run ruff format .` before the checks. Checks CI runs (all must pass at the end of every task): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -q`, `uv run python scripts/check_language.py`.
- Commit messages: Conventional Commits, English, ending with your own model's `Co-Authored-By:` line.
- Sidecar file names are fixed: `radios-request.json`, `radios-state.json`, `radios-log.txt`, handled markers in `radios-handled/`, all in the update directory (`/data/update`).
- Terminal radio phases are exactly `done`, `failed`, `rejected`, `unchanged` (and `idle` when no job ever ran).

## File Map

| File | Responsibility | Task |
|---|---|---|
| `src/loxmatter/radios/__init__.py` (new) | package docstring | 1 |
| `src/loxmatter/radios/inventory.py` (new) | `SerialRadio`, `BluetoothAdapter`, `scan_serial`, `scan_bluetooth`, `match_current_device` | 1 |
| `src/loxmatter/radios/sidecar.py` (new) | `RadioConfig`, `RadiosState`, `read_radios_state`, `sidecar_status`, `request_radios`, `RadiosBusyError` | 2 |
| `deploy/updater/radios-once.sh` (new) | the sidecar job | 3, 4 |
| `deploy/updater/entrypoint.sh`, `deploy/updater/Dockerfile` | run and ship the job | 5 |
| `deploy/testhost/docker-compose.yml` | `/dev:/host/dev:ro` for bridge and sidecar | 5 |
| `src/loxmatter/api/radios.py` (new), `src/loxmatter/loxone/server.py` | API and wiring | 6 |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | settings card | 7 |
| `src/loxmatter/i18n/strings.yaml` | `api.radios.*`, `web.radios.*` | 6, 7 |
| `README.md`, update design spec, first-run checklist, `CHANGELOG.md` | documentation | 8 |
| tests: `tests/radios/`, `tests/test_updater_radios_script.py`, `tests/test_updater_entrypoint.py`, `tests/test_updater_image.py`, `tests/test_compose_profiles.py`, `tests/api/test_radios_api.py`, `tests/api/test_web.py` | | 1–7 |

---

### Task 1: Radio Detection

**Files:**
- Create: `src/loxmatter/radios/__init__.py`, `src/loxmatter/radios/inventory.py`
- Test: `tests/radios/test_inventory.py`

**Interfaces:**
- Produces:
  - `SerialRadio(path: str, tty: str, manufacturer: str | None, product: str | None, serial: str | None, vid_pid: str | None)` (frozen dataclass)
  - `BluetoothAdapter(index: int, name: str, bus: Literal["uart", "usb", "other"], product: str | None, rfkill_blocked: bool)` (frozen dataclass)
  - `scan_serial(host_dev: Path, sys_root: Path) -> list[SerialRadio]` — sorted by `path`
  - `scan_bluetooth(sys_root: Path) -> list[BluetoothAdapter]` — sorted by `index`
  - `match_current_device(device: str | None, radios: Sequence[SerialRadio]) -> tuple[str | None, bool]` — `(path to show, present)`

- [ ] **Step 0: Baseline.** `uv run pytest -q` in the foreground (timeout 600000). All green, or stop and report BLOCKED.

- [ ] **Step 1: Write the failing tests** `tests/radios/test_inventory.py` (GPL header first; no `__init__.py` in `tests/radios/`, like `tests/matter/`):

```python
"""Detecting USB sticks and Bluetooth adapters from /dev and sysfs (design
2026-09-11 "Radios in the Web UI", section 4). The trees are built in
tmp_path the way the test Pi showed them on 11 September 2026."""

from __future__ import annotations

from pathlib import Path

from loxmatter.radios.inventory import (
    BluetoothAdapter,
    SerialRadio,
    match_current_device,
    scan_bluetooth,
    scan_serial,
)

SONOFF = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"


def _usb_serial(root: Path, tty: str, by_id: str, attrs: dict[str, str], interface_depth: int = 1):
    """host_dev/serial/by-id/<by_id> -> ../../<tty>, and sysfs with
    class/tty/<tty>/device pointing into a USB device whose attributes sit
    `interface_depth` levels above the resolved device path (1 for ttyUSB,
    whose device is `…/1-1.2:1.0/ttyUSB0`; 0 for ttyACM, whose device is the
    interface `…/1-1.2:1.0` itself)."""
    host_dev, sys_root = root / "dev", root / "sys"
    (host_dev / "serial" / "by-id").mkdir(parents=True, exist_ok=True)
    (host_dev / tty).write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / by_id).symlink_to(Path("../..") / tty)
    usb = sys_root / "devices" / "usb1" / f"1-{tty}"
    interface = usb / f"1-{tty}:1.0"
    device = interface / tty if interface_depth == 1 else interface
    device.mkdir(parents=True, exist_ok=True)
    for name, value in attrs.items():
        (usb / name).write_text(value + "\n", encoding="utf-8")
    cls = sys_root / "class" / "tty" / tty
    cls.mkdir(parents=True, exist_ok=True)
    (cls / "device").symlink_to(device)
    return host_dev, sys_root


def test_a_usb_serial_stick_is_found_with_its_details(tmp_path):
    host_dev, sys_root = _usb_serial(
        tmp_path,
        "ttyUSB0",
        SONOFF,
        {
            "manufacturer": "SONOFF",
            "product": "SONOFF Dongle Plus MG24",
            "serial": "e26a7d9118f9ef118f7767135c2a50c9",
            "idVendor": "10c4",
            "idProduct": "ea60",
        },
    )

    assert scan_serial(host_dev, sys_root) == [
        SerialRadio(
            path=f"/dev/serial/by-id/{SONOFF}",
            tty="ttyUSB0",
            manufacturer="SONOFF",
            product="SONOFF Dongle Plus MG24",
            serial="e26a7d9118f9ef118f7767135c2a50c9",
            vid_pid="10c4:ea60",
        )
    ]


def test_an_acm_stick_whose_device_is_the_interface_itself_is_found(tmp_path):
    """Fault to prove it: only look one level above the resolved device."""
    host_dev, sys_root = _usb_serial(
        tmp_path, "ttyACM0", "usb-Nabu_Casa_ZBT-1-if00", {"idVendor": "303a", "idProduct": "4001"},
        interface_depth=0,
    )
    (radio,) = scan_serial(host_dev, sys_root)
    assert radio.tty == "ttyACM0"
    assert radio.vid_pid == "303a:4001"
    assert radio.product is None


def test_missing_by_id_directory_means_no_sticks(tmp_path):
    assert scan_serial(tmp_path / "dev", tmp_path / "sys") == []


def test_a_by_id_entry_that_is_not_a_usb_or_acm_tty_is_ignored(tmp_path):
    host_dev = tmp_path / "dev"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    (host_dev / "sda").write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / "usb-Disk").symlink_to(Path("../..") / "sda")
    assert scan_serial(host_dev, tmp_path / "sys") == []


def _bluetooth(root: Path, name: str, device_target: str, rfkill_soft: str | None = None):
    sys_root = root / "sys"
    target = sys_root / device_target
    target.mkdir(parents=True, exist_ok=True)
    entry = sys_root / "class" / "bluetooth" / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "device").symlink_to(target)
    if rfkill_soft is not None:
        (entry / "rfkill0").mkdir()
        (entry / "rfkill0" / "soft").write_text(rfkill_soft + "\n", encoding="utf-8")
    return sys_root, target


def test_the_built_in_uart_adapter_of_the_pi(tmp_path):
    sys_root, _ = _bluetooth(
        tmp_path, "hci0", "devices/platform/soc/fe201000.serial/serial0/serial0-0", rfkill_soft="0"
    )
    assert scan_bluetooth(sys_root) == [
        BluetoothAdapter(index=0, name="hci0", bus="uart", product=None, rfkill_blocked=False)
    ]


def test_a_usb_adapter_reports_its_product_and_a_soft_block(tmp_path):
    """Fault to prove it: read `soft` as blocked when it is "0"."""
    sys_root, target = _bluetooth(
        tmp_path, "hci1", "devices/usb1/1-1.3/1-1.3:1.0", rfkill_soft="1"
    )
    (target.parent / "idVendor").write_text("0bda\n", encoding="utf-8")
    (target.parent / "product").write_text("Bluetooth Radio\n", encoding="utf-8")
    assert scan_bluetooth(sys_root) == [
        BluetoothAdapter(index=1, name="hci1", bus="usb", product="Bluetooth Radio", rfkill_blocked=True)
    ]


def test_adapters_are_sorted_by_number_not_by_name(tmp_path):
    for n in (10, 2):
        _bluetooth(tmp_path, f"hci{n}", f"devices/other/{n}")
    assert [a.index for a in scan_bluetooth(tmp_path / "sys")] == [2, 10]
    assert {a.bus for a in scan_bluetooth(tmp_path / "sys")} == {"other"}


def _radio(path: str, tty: str) -> SerialRadio:
    return SerialRadio(path=path, tty=tty, manufacturer=None, product=None, serial=None, vid_pid=None)


def test_a_legacy_tty_name_is_matched_to_its_by_id_entry():
    """The installer wrote `/dev/ttyUSB0`; the card must show it as the
    by-id stick. Fault to prove it: compare the whole path instead of the
    tty name for non-by-id values."""
    radios = [_radio(f"/dev/serial/by-id/{SONOFF}", "ttyUSB0")]
    assert match_current_device("/dev/ttyUSB0", radios) == (f"/dev/serial/by-id/{SONOFF}", True)


def test_a_by_id_value_that_is_attached_is_present():
    radios = [_radio(f"/dev/serial/by-id/{SONOFF}", "ttyUSB0")]
    assert match_current_device(f"/dev/serial/by-id/{SONOFF}", radios) == (
        f"/dev/serial/by-id/{SONOFF}",
        True,
    )


def test_a_configured_stick_that_is_gone_is_reported_missing():
    assert match_current_device("/dev/ttyUSB3", []) == ("/dev/ttyUSB3", False)
    assert match_current_device(None, []) == (None, False)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/radios/test_inventory.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.radios'`.

- [ ] **Step 3: Implement.** `src/loxmatter/radios/__init__.py` (GPL header, then):

```python
"""Radios attached to the host: detection for the settings card, and the
file protocol to the updater sidecar that applies a change (design
2026-09-11 "Radios in the Web UI")."""
```

`src/loxmatter/radios/inventory.py` (GPL header, then):

```python
"""Which USB serial sticks and Bluetooth adapters the host has.

Read fresh on every request - no background scan, no cache. The roots are
parameters so tests can hand in directory trees; in the container they are
`/host/dev` (the host's /dev, mounted read-only for exactly this, see
docker-compose.yml) and `/sys`.

Measured on the test Pi on 11 September 2026 (design section 3): the
container sees `/sys/class/tty/ttyUSB0` and `/sys/class/bluetooth/hci0`
without any extra mount, but not `/dev/serial/by-id`. The USB attributes
(`manufacturer`, `product`, `serial`, `idVendor`, `idProduct`) sit on the
USB device, a varying number of levels above the tty's `device` link - one
for `ttyUSB` (the link ends in `…/1-1.2:1.0/ttyUSB0`), none for `ttyACM`
(it ends in the interface) - so the lookup walks up until it finds
`idVendor`, at most `_MAX_WALK` levels.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

_TTY_NAME: Final = re.compile(r"tty(USB|ACM)\d+")
_HCI_NAME: Final = re.compile(r"hci(\d+)")
_MAX_WALK: Final = 4


@dataclass(frozen=True)
class SerialRadio:
    path: str  # /dev/serial/by-id/… as the host sees it, never /host/dev/…
    tty: str  # ttyUSB0
    manufacturer: str | None
    product: str | None
    serial: str | None
    vid_pid: str | None  # "10c4:ea60"


@dataclass(frozen=True)
class BluetoothAdapter:
    index: int
    name: str  # hci0
    bus: Literal["uart", "usb", "other"]
    product: str | None
    rfkill_blocked: bool


def _read(directory: Path | None, name: str) -> str | None:
    if directory is None:
        return None
    try:
        value = (directory / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _usb_device(start: Path) -> Path | None:
    """The first directory at or above `start` that carries `idVendor`."""
    try:
        current = start.resolve(strict=True)
    except OSError:
        return None
    for _ in range(_MAX_WALK + 1):
        if (current / "idVendor").is_file():
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


def scan_serial(host_dev: Path, sys_root: Path) -> list[SerialRadio]:
    by_id = host_dev / "serial" / "by-id"
    if not by_id.is_dir():
        return []
    radios: list[SerialRadio] = []
    for entry in sorted(by_id.iterdir()):
        if not entry.is_symlink():
            continue
        tty = Path(os.readlink(entry)).name
        if not _TTY_NAME.fullmatch(tty):
            continue
        usb = _usb_device(sys_root / "class" / "tty" / tty / "device")
        vendor, product_id = _read(usb, "idVendor"), _read(usb, "idProduct")
        radios.append(
            SerialRadio(
                path=f"/dev/serial/by-id/{entry.name}",
                tty=tty,
                manufacturer=_read(usb, "manufacturer"),
                product=_read(usb, "product"),
                serial=_read(usb, "serial"),
                vid_pid=f"{vendor}:{product_id}" if vendor and product_id else None,
            )
        )
    return radios


def scan_bluetooth(sys_root: Path) -> list[BluetoothAdapter]:
    base = sys_root / "class" / "bluetooth"
    if not base.is_dir():
        return []
    adapters: list[BluetoothAdapter] = []
    for entry in base.iterdir():
        match = _HCI_NAME.fullmatch(entry.name)
        if match is None:
            continue
        try:
            target = str((entry / "device").resolve(strict=True))
        except OSError:
            target = ""
        bus: Literal["uart", "usb", "other"]
        if "/usb" in target:
            bus = "usb"
        elif "serial" in target:
            bus = "uart"
        else:
            bus = "other"
        product = _read(_usb_device(entry / "device"), "product") if bus == "usb" else None
        blocked = any(_read(rfkill, "soft") == "1" for rfkill in entry.glob("rfkill*"))
        adapters.append(
            BluetoothAdapter(
                index=int(match.group(1)),
                name=entry.name,
                bus=bus,
                product=product,
                rfkill_blocked=blocked,
            )
        )
    return sorted(adapters, key=lambda adapter: adapter.index)


def match_current_device(
    device: str | None, radios: Sequence[SerialRadio]
) -> tuple[str | None, bool]:
    """Maps the sidecar's `RADIO_DEVICE` onto what the card shows.

    A by-id value is present when a detected stick has that path. Any other
    value (the installer writes `/dev/ttyUSB0`) is matched by tty name, and
    shown as the stick's by-id path when one matches."""
    if device is None:
        return None, False
    if device.startswith("/dev/serial/by-id/"):
        return device, any(radio.path == device for radio in radios)
    tty = Path(device).name
    for radio in radios:
        if radio.tty == tty:
            return radio.path, True
    return device, False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/radios/test_inventory.py -q` — Expected: PASS (10 tests).

- [ ] **Step 5: Prove the protections.** Introduce each fault named in the docstrings (walk only one level; `soft` compared against `"0"`; compare full path for legacy values), see the matching test FAIL, revert, see PASS. Paste outputs.

- [ ] **Step 6: Checks and commit**

Run the check chain from the Global Constraints (full suite in the foreground). Then:

```bash
git add src/loxmatter/radios tests/radios
git commit -m "feat(radios): detect USB serial sticks and Bluetooth adapters

Reads /host/dev/serial/by-id and sysfs on each request so the settings
card can list what the host has, and maps the installer's legacy
/dev/ttyUSB0 value onto its stable by-id path."
```

---

### Task 2: The Bridge Side of the Sidecar File Protocol

**Files:**
- Create: `src/loxmatter/radios/sidecar.py`
- Test: `tests/radios/test_sidecar.py`

**Interfaces:**
- Consumes: `loxmatter.update.read_state`, `loxmatter.update.updater_present`, `loxmatter.update._RUNNING_PHASES`, `loxmatter.update._TIMESTAMP_FORMAT`, `loxmatter.update._MAX_SILENT_SECONDS` (all existing).
- Produces:
  - `RadioConfig(thread_enabled: bool, thread_device: str | None, bluetooth_adapter: int, otbr_running: bool)`
  - `RadiosState(id: str | None, phase: str, steps: tuple[str, ...], error: str | None, rolled_back: bool, healthy: bool | None, current: RadioConfig | None, capable: bool, capable_reason: str | None, seen_at: str | None)`
  - `TERMINAL_PHASES: frozenset[str]` = `{"idle", "done", "failed", "rejected", "unchanged"}`
  - `read_radios_state(update_dir: Path) -> RadiosState | None`
  - `SidecarStatus = Literal["ready", "missing", "outdated", "unmounted"]`
  - `sidecar_status(update_state: UpdateState | None, radios_state: RadiosState | None, *, now: datetime) -> SidecarStatus`
  - `class RadiosBusyError(RuntimeError)`
  - `request_radios(update_dir: Path, *, thread_enabled: bool, thread_device: str | None, bluetooth_adapter: int) -> str` (returns the job id)

- [ ] **Step 1: Write the failing tests** `tests/radios/test_sidecar.py` (GPL header first):

```python
"""The bridge's half of the radios file protocol (design 2026-09-11
"Radios in the Web UI", sections 6.2, 6.5, 7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from loxmatter.radios.sidecar import (
    RadioConfig,
    RadiosBusyError,
    read_radios_state,
    request_radios,
    sidecar_status,
)
from loxmatter.update import read_state

NOW = datetime(2026, 9, 11, 20, 0, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_state(update_dir, phase="idle", seen=NOW):
    body = {"id": None, "phase": phase, "updater_seen_at": _stamp(seen)}
    (update_dir / "state.json").write_text(json.dumps(body), encoding="utf-8")


def _radios_state(update_dir, **fields):
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
        "seen_at": _stamp(NOW),
    }
    body.update(fields)
    (update_dir / "radios-state.json").write_text(json.dumps(body), encoding="utf-8")


def test_a_full_state_is_read(tmp_path):
    _radios_state(tmp_path, id="j1", phase="verify_thread", steps=["validate", "verify_thread"])
    state = read_radios_state(tmp_path)
    assert state is not None
    assert (state.id, state.phase, state.steps) == ("j1", "verify_thread", ("validate", "verify_thread"))
    assert state.current == RadioConfig(
        thread_enabled=True, thread_device="/dev/ttyUSB0", bluetooth_adapter=0, otbr_running=True
    )


@pytest.mark.parametrize("content", ["", "{", "[]", "null"])
def test_an_unreadable_state_reads_as_none(tmp_path, content):
    (tmp_path / "radios-state.json").write_text(content, encoding="utf-8")
    assert read_radios_state(tmp_path) is None


def test_a_malformed_current_block_reads_as_no_current(tmp_path):
    _radios_state(tmp_path, current={"thread_enabled": "yes"})
    state = read_radios_state(tmp_path)
    assert state is not None and state.current is None


def test_no_updater_means_missing(tmp_path):
    assert sidecar_status(None, None, now=NOW) == "missing"


def test_an_updater_without_the_radios_job_is_outdated(tmp_path):
    """Fault to prove it: treat a missing radios-state.json as ready."""
    _update_state(tmp_path)
    assert sidecar_status(read_state(tmp_path), None, now=NOW) == "outdated"


def test_a_stale_radios_heartbeat_is_outdated(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path, seen_at=_stamp(NOW - timedelta(seconds=31)))
    assert sidecar_status(read_state(tmp_path), read_radios_state(tmp_path), now=NOW) == "outdated"


def test_a_sidecar_without_the_dev_mount_is_unmounted(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path, capable=False, capable_reason="host_dev_not_mounted")
    assert sidecar_status(read_state(tmp_path), read_radios_state(tmp_path), now=NOW) == "unmounted"


def test_a_fresh_capable_sidecar_is_ready(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path)
    assert sidecar_status(read_state(tmp_path), read_radios_state(tmp_path), now=NOW) == "ready"


def _request(update_dir):
    return request_radios(
        update_dir, thread_enabled=True, thread_device="/dev/serial/by-id/x", bluetooth_adapter=0
    )


def test_a_request_is_written_with_exactly_the_protocol_keys(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path)
    job_id = _request(tmp_path)
    body = json.loads((tmp_path / "radios-request.json").read_text(encoding="utf-8"))
    assert set(body) == {"id", "thread", "bluetooth", "requested_at"}
    assert body["id"] == job_id
    assert body["thread"] == {"enabled": True, "device": "/dev/serial/by-id/x"}
    assert body["bluetooth"] == {"adapter": 0}
    assert not (tmp_path / "radios-request.json.tmp").exists()


def test_no_request_while_an_update_runs(tmp_path):
    """Fault to prove it: drop the update-phase check."""
    _update_state(tmp_path, phase="recreate")
    _radios_state(tmp_path)
    with pytest.raises(RadiosBusyError):
        _request(tmp_path)
    assert not (tmp_path / "radios-request.json").exists()


def test_no_request_while_a_radio_job_runs(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path, id="j1", phase="verify_thread")
    with pytest.raises(RadiosBusyError):
        _request(tmp_path)


def test_no_request_while_the_previous_one_was_not_picked_up(tmp_path):
    """A request the sidecar has not handled yet must not be overwritten.
    Fault to prove it: drop the pending-request check."""
    _update_state(tmp_path)
    _radios_state(tmp_path, id="old", phase="done")
    first = _request(tmp_path)
    with pytest.raises(RadiosBusyError):
        _request(tmp_path)
    (tmp_path / "radios-handled").mkdir()
    (tmp_path / "radios-handled" / first).touch()
    _request(tmp_path)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/radios/test_sidecar.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.radios.sidecar'`.

- [ ] **Step 3: Implement** `src/loxmatter/radios/sidecar.py` (GPL header, then):

```python
"""The bridge's half of the radios job in the updater sidecar (design
2026-09-11 "Radios in the Web UI", sections 6 and 7).

The same shape as `loxmatter.update`: the bridge writes one request file
atomically, the sidecar writes a state file atomically, and nothing else
connects them. Every read failure folds into `None`, because the settings
card polls this and a momentary read hiccup must not look like a broken
sidecar.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from loxmatter import update

TERMINAL_PHASES: Final = frozenset({"idle", "done", "failed", "rejected", "unchanged"})

SidecarStatus = Literal["ready", "missing", "outdated", "unmounted"]


class RadiosBusyError(RuntimeError):
    """An update or another radio job is running, or a request is still
    waiting to be picked up."""


@dataclass(frozen=True)
class RadioConfig:
    thread_enabled: bool
    thread_device: str | None
    bluetooth_adapter: int
    otbr_running: bool


@dataclass(frozen=True)
class RadiosState:
    id: str | None
    phase: str
    steps: tuple[str, ...]
    error: str | None
    rolled_back: bool
    healthy: bool | None
    current: RadioConfig | None
    capable: bool
    capable_reason: str | None
    seen_at: str | None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _config(raw: object) -> RadioConfig | None:
    if not isinstance(raw, dict):
        return None
    enabled, device = raw.get("thread_enabled"), raw.get("thread_device")
    adapter, running = raw.get("bluetooth_adapter"), raw.get("otbr_running")
    if not isinstance(enabled, bool) or not isinstance(running, bool):
        return None
    if isinstance(adapter, bool) or not isinstance(adapter, int):
        return None
    if device is not None and not isinstance(device, str):
        return None
    return RadioConfig(
        thread_enabled=enabled,
        thread_device=device or None,
        bluetooth_adapter=adapter,
        otbr_running=running,
    )


def read_radios_state(update_dir: Path) -> RadiosState | None:
    try:
        raw = json.loads((update_dir / "radios-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    steps = raw.get("steps")
    healthy = raw.get("healthy")
    return RadiosState(
        id=_opt_str(raw.get("id")),
        phase=_opt_str(raw.get("phase")) or "idle",
        steps=tuple(s for s in steps if isinstance(s, str)) if isinstance(steps, list) else (),
        error=_opt_str(raw.get("error")),
        rolled_back=raw.get("rolled_back") is True,
        healthy=healthy if isinstance(healthy, bool) else None,
        current=_config(raw.get("current")),
        capable=raw.get("capable") is True,
        capable_reason=_opt_str(raw.get("capable_reason")),
        seen_at=_opt_str(raw.get("seen_at")),
    )


def _seen_recently(seen_at: str | None, now: datetime) -> bool:
    """The same window and the same both-directions rule as
    `update.updater_present` - see its docstring for why a timestamp ahead
    of `now` counts as stale too."""
    if not seen_at:
        return False
    try:
        seen = datetime.strptime(seen_at, update._TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return False
    return abs((now - seen).total_seconds()) <= update._MAX_SILENT_SECONDS


def sidecar_status(
    update_state: update.UpdateState | None,
    radios_state: RadiosState | None,
    *,
    now: datetime,
) -> SidecarStatus:
    if not update.updater_present(update_state, now=now):
        return "missing"
    if radios_state is None or not _seen_recently(radios_state.seen_at, now):
        return "outdated"
    if not radios_state.capable:
        return "unmounted"
    return "ready"


def _pending(update_dir: Path, state: RadiosState | None) -> bool:
    try:
        raw = json.loads((update_dir / "radios-request.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    request_id = raw.get("id") if isinstance(raw, dict) else None
    if not isinstance(request_id, str) or not request_id:
        return False
    if state is not None and state.id == request_id:
        return False
    return not (update_dir / "radios-handled" / request_id).exists()


def request_radios(
    update_dir: Path,
    *,
    thread_enabled: bool,
    thread_device: str | None,
    bluetooth_adapter: int,
) -> str:
    update_state = update.read_state(update_dir)
    if update_state is not None and update_state.phase in update._RUNNING_PHASES:
        raise RadiosBusyError(update_state.phase)
    state = read_radios_state(update_dir)
    if state is not None and state.phase not in TERMINAL_PHASES:
        raise RadiosBusyError(state.phase)
    if _pending(update_dir, state):
        raise RadiosBusyError("pending")

    job_id = str(uuid.uuid4())
    body = {
        "id": job_id,
        "thread": {"enabled": thread_enabled, "device": thread_device},
        "bluetooth": {"adapter": bluetooth_adapter},
        "requested_at": datetime.now(UTC).strftime(update._TIMESTAMP_FORMAT),
    }
    update_dir.mkdir(parents=True, exist_ok=True)
    temp = update_dir / "radios-request.json.tmp"
    temp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(temp, update_dir / "radios-request.json")
    return job_id
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/radios/test_sidecar.py -q` — Expected: PASS (14 tests).

- [ ] **Step 5: Prove the protections** named in the docstrings (missing radios state treated as ready; update-phase check dropped; pending check dropped). Paste fail and pass outputs.

- [ ] **Step 6: Checks and commit**

```bash
git add src/loxmatter/radios/sidecar.py tests/radios/test_sidecar.py
git commit -m "feat(radios): read the sidecar's radio report and write radio requests

Tells a ready sidecar from a missing, outdated or unmounted one, and never
overwrites a request while an update or another radio job is running."
```

---

### Task 3: The Sidecar Job — Reporting and Validation

**Files:**
- Create: `deploy/updater/radios-once.sh`
- Test: `tests/test_updater_radios_script.py`

**Interfaces:**
- Consumes: the file protocol of Task 2 (keys and phase names).
- Produces: `deploy/updater/radios-once.sh` with environment overrides `LOXMATTER_UPDATE_DIR`, `LOXMATTER_STACK`, `LOXMATTER_STACK_HOST_PATH`, `LOXMATTER_HOST_DEV`, `LOXMATTER_SYS_BLUETOOTH`, `LOXMATTER_MATTER_SERVER_URL`, `LOXMATTER_RADIOS_BLUETOOTH_TIMEOUT`, `LOXMATTER_RADIOS_THREAD_TIMEOUT`, `LOXMATTER_RADIOS_THREAD_FIX_AFTER`, `LOXMATTER_RADIOS_POLL_SECONDS`; error keys `request_malformed`, `host_dev_not_mounted`, `stack_host_path_unknown`, `thread_device_required`, `thread_device_not_found`, `thread_device_not_a_serial_port`, `bluetooth_adapter_not_found`, `backbone_interface_missing`, `env_file_missing`.

- [ ] **Step 1: Write the harness and the failing tests** `tests/test_updater_radios_script.py` (GPL header first):

```python
"""Behavioural tests for deploy/updater/radios-once.sh (design 2026-09-11
"Radios in the Web UI", section 6).

The same approach as tests/test_updater_script.py: a sealed PATH of fake
binaries, and what is checked is which commands the script chooses. The
rejection tests carry the security claim of section 6.6: a rejected request
leaves `.env` byte-identical and runs no Docker command that changes
anything - the only Docker call allowed is the read-only `ps` of the
per-pass report."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "radios-once.sh"
SONOFF = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"
SYSTEM_TOOLS = (
    "sh", "cat", "grep", "sed", "awk", "tr", "printf", "mkdir", "rm", "mv", "cp", "date",
    "head", "tail", "jq", "cut", "wc", "readlink", "cksum",
)

DOCKER_STUB = r"""#!/bin/sh
printf 'docker %s\n' "$*" >> "$STUB_LOG"
case "$1" in
  ps)
    [ -f "$FAKE/otbr_state" ] && cat "$FAKE/otbr_state"
    exit 0 ;;
  exec)
    case "$*" in
      *"ot-ctl state"*)
        mode="$(cat "$FAKE/thread_mode" 2>/dev/null || echo leader)"
        ups="$(cat "$FAKE/otbr_ups" 2>/dev/null || echo 0)"
        case "$mode" in
          leader) echo leader ;;
          needs_fix) if [ -f "$FAKE/pid_cleared" ]; then echo leader; else echo detached; fi ;;
          second_up) if [ "$ups" -ge 2 ]; then echo leader; else echo detached; fi ;;
          *) echo detached ;;
        esac ;;
      *"rm -f /run/otbr-agent.pid"*) : > "$FAKE/pid_cleared" ;;
    esac
    exit 0 ;;
  compose)
    case "$*" in *"$(cat "$FAKE/compose_fail" 2>/dev/null || echo __never__)"*) exit 1 ;; esac
    case "$*" in
      *" up "*otbr*)
        echo running > "$FAKE/otbr_state"
        echo $(( $(cat "$FAKE/otbr_ups" 2>/dev/null || echo 0) + 1 )) > "$FAKE/otbr_ups" ;;
      *" rm "*otbr*) rm -f "$FAKE/otbr_state" ;;
    esac
    exit 0 ;;
esac
exit 0
"""

CURL_STUB = r"""#!/bin/sh
printf 'curl %s\n' "$*" >> "$STUB_LOG"
[ "$(cat "$FAKE/matter_up" 2>/dev/null || echo yes)" = yes ] && exit 0
exit 7
"""


@pytest.fixture
def radios(tmp_path):
    """Returns run() -> (result, calls, state). Attributes expose the paths
    tests arrange: env_file, update_dir, fake (the stub control dir),
    host_dev, sys_bluetooth."""
    bindir, sysdir, fake = tmp_path / "bin", tmp_path / "sys-tools", tmp_path / "fake"
    for d in (bindir, sysdir, fake):
        d.mkdir()
    log = tmp_path / "stub.log"
    update_dir = tmp_path / "data" / "update"
    update_dir.mkdir(parents=True)
    stack = tmp_path / "repo" / "deploy" / "testhost"
    stack.mkdir(parents=True)
    env_file = stack / ".env"
    env_file.write_text(
        "LOXMATTER_IMAGE_TAG=0.3.10\nRADIO_DEVICE=/dev/ttyUSB0\nRADIO_BAUDRATE=460800\n"
        "BACKBONE_IF=wlan0\nBLUETOOTH_ADAPTER=0\n",
        encoding="utf-8",
    )
    host_dev = tmp_path / "host" / "dev"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    (host_dev / "ttyUSB0").write_text("", encoding="utf-8")
    (host_dev / "sda").write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / SONOFF).symlink_to(Path("../..") / "ttyUSB0")
    (host_dev / "serial" / "by-id" / "usb-Disk").symlink_to(Path("../..") / "sda")
    sys_bluetooth = tmp_path / "sysfs" / "class" / "bluetooth"
    (sys_bluetooth / "hci0").mkdir(parents=True)
    (fake / "otbr_state").write_text("running\n", encoding="utf-8")

    for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB)):
        (bindir / name).write_text(body, encoding="utf-8")
        (bindir / name).chmod(0o755)
    (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bindir / "sleep").chmod(0o755)
    for tool in SYSTEM_TOOLS:
        real = subprocess.run(["which", tool], capture_output=True, text=True, check=False).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(**extra_env):
        env = {
            "PATH": f"{bindir}:{sysdir}",
            "STUB_LOG": str(log),
            "FAKE": str(fake),
            "LOXMATTER_UPDATE_DIR": str(update_dir),
            "LOXMATTER_STACK": str(stack),
            "LOXMATTER_STACK_HOST_PATH": str(stack),
            "LOXMATTER_HOST_DEV": str(host_dev),
            "LOXMATTER_SYS_BLUETOOTH": str(sys_bluetooth),
            "LOXMATTER_RADIOS_BLUETOOTH_TIMEOUT": "3",
            "LOXMATTER_RADIOS_THREAD_TIMEOUT": "4",
            "LOXMATTER_RADIOS_THREAD_FIX_AFTER": "2",
            "LOXMATTER_RADIOS_POLL_SECONDS": "1",
            **extra_env,
        }
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env, check=False, timeout=30)
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        state_file = update_dir / "radios-state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else None
        return result, calls, state

    run.env_file, run.update_dir, run.fake = env_file, update_dir, fake
    run.host_dev, run.sys_bluetooth, run.log = host_dev, sys_bluetooth, log
    return run


def _request(radios, **overrides):
    body = {
        "id": "job-1",
        "thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}"},
        "bluetooth": {"adapter": 0},
        "requested_at": "2026-09-11T20:00:00Z",
    }
    body.update(overrides)
    (radios.update_dir / "radios-request.json").write_text(json.dumps(body), encoding="utf-8")


def _mutating_docker_calls(calls: str) -> list[str]:
    return [
        line for line in calls.splitlines()
        if line.startswith("docker ") and line.split()[1] != "ps"
    ]


def test_without_a_request_it_reports_the_current_configuration(radios):
    result, calls, state = radios()
    assert result.returncode == 0, result.stderr
    assert state["phase"] == "idle"
    assert state["current"] == {
        "thread_enabled": True,
        "thread_device": "/dev/ttyUSB0",
        "bluetooth_adapter": 0,
        "otbr_running": True,
    }
    assert state["capable"] is True and state["capable_reason"] is None
    assert state["seen_at"]
    assert _mutating_docker_calls(calls) == []


def test_thread_counts_as_enabled_from_the_profile_alone(radios):
    """Fault to prove it: derive thread_enabled only from the container."""
    (radios.fake / "otbr_state").unlink()
    radios.env_file.write_text(radios.env_file.read_text() + "COMPOSE_PROFILES=foo,thread\n")
    _, _, state = radios()
    assert state["current"]["thread_enabled"] is True
    assert state["current"]["otbr_running"] is False


def test_thread_counts_as_enabled_from_an_existing_container_without_a_profile(radios):
    """The test Pi: no COMPOSE_PROFILES line, otbr runs (design 12.1).
    Fault to prove it: derive thread_enabled only from the profile."""
    _, _, state = radios()
    assert state["current"]["thread_enabled"] is True


def test_no_profile_and_no_container_means_thread_disabled(radios):
    (radios.fake / "otbr_state").unlink()
    _, _, state = radios()
    assert state["current"]["thread_enabled"] is False


def test_a_missing_bluetooth_adapter_line_reads_as_zero(radios):
    radios.env_file.write_text("RADIO_DEVICE=/dev/ttyUSB0\n")
    _, _, state = radios()
    assert state["current"]["bluetooth_adapter"] == 0


def test_a_zero_padded_bluetooth_adapter_does_not_break_the_report(radios):
    """Fault to prove it: remove the leading-zero strip - jq rejects "01"."""
    radios.env_file.write_text("RADIO_DEVICE=/dev/ttyUSB0\nBLUETOOTH_ADAPTER=01\n")
    result, _, state = radios()
    assert result.returncode == 0, result.stderr
    assert state["current"]["bluetooth_adapter"] == 1


def test_without_the_dev_mount_it_is_not_capable_and_rejects(radios, tmp_path):
    before = radios.env_file.read_bytes()
    _request(radios)
    _, calls, state = radios(LOXMATTER_HOST_DEV=str(tmp_path / "nowhere"))
    assert state["capable"] is False
    assert state["capable_reason"] == "host_dev_not_mounted"
    assert (state["phase"], state["error"]) == ("rejected", "host_dev_not_mounted")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"thread": {"enabled": True, "device": "/dev/ttyUSB0"}}, "request_malformed"),
        ({"thread": {"enabled": True, "device": "/dev/serial/by-id/../../sda"}}, "request_malformed"),
        ({"thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}\nx"}}, "request_malformed"),
        ({"thread": {"enabled": True, "device": "/dev/serial/by-id/usb-Gone"}}, "thread_device_not_found"),
        ({"thread": {"enabled": True, "device": "/dev/serial/by-id/usb-Disk"}}, "thread_device_not_a_serial_port"),
        ({"thread": {"enabled": True, "device": None}}, "thread_device_required"),
        ({"thread": {"enabled": "yes", "device": None}}, "request_malformed"),
        ({"thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}", "baud": 1}}, "request_malformed"),
        ({"bluetooth": {"adapter": 16}}, "request_malformed"),
        ({"bluetooth": {"adapter": "0"}}, "request_malformed"),
        ({"bluetooth": {"adapter": 1.5}}, "request_malformed"),
        ({"bluetooth": {"adapter": 1}}, "bluetooth_adapter_not_found"),
        ({"command": "rm -rf /"}, "request_malformed"),
    ],
)
def test_a_bad_request_is_rejected_without_any_effect(radios, overrides, error):
    """Fault to prove it (run once, on the traversal case): replace the jq
    `test("\\A/dev/serial/by-id/[A-Za-z0-9._:+-]+\\z")` with
    `startswith("/dev/serial/by-id/")`."""
    before = radios.env_file.read_bytes()
    _request(radios, **overrides)
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("rejected", error)
    assert state["id"] == "job-1"
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_an_unreadable_request_is_rejected_once(radios):
    (radios.update_dir / "radios-request.json").write_text("{not json", encoding="utf-8")
    _, _, first = radios()
    assert (first["phase"], first["error"]) == ("rejected", "request_malformed")
    log_lines = (radios.update_dir / "radios-log.txt").read_text().count("rejected")
    radios()
    assert (radios.update_dir / "radios-log.txt").read_text().count("rejected") == log_lines


def test_enabling_thread_requires_a_backbone_interface(radios):
    radios.env_file.write_text("RADIO_DEVICE=/dev/ttyUSB0\nBLUETOOTH_ADAPTER=0\n")
    before = radios.env_file.read_bytes()
    _request(radios)
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("rejected", "backbone_interface_missing")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_the_same_job_is_not_handled_twice(radios):
    _request(radios, bluetooth={"adapter": 1})
    radios()
    radios.log.unlink()
    _, calls, state = radios()
    assert state["phase"] == "rejected"
    assert _mutating_docker_calls(calls) == []
    assert (radios.update_dir / "radios-log.txt").read_text().count("job-1") == 1


def test_a_request_waits_while_an_update_runs(radios):
    """Fault to prove it: drop the update-phase check."""
    (radios.update_dir / "state.json").write_text(json.dumps({"phase": "recreate"}))
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "idle"
    assert not (radios.update_dir / "radios-handled" / "job-1").exists()
    assert _mutating_docker_calls(calls) == []


def test_a_request_that_changes_nothing_ends_unchanged(radios):
    # A legacy /dev/ttyUSB0 value cannot be requested (pattern), so .env
    # already holds the by-id path the request names.
    radios.env_file.write_text(radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}"))
    _request(radios)
    before = radios.env_file.read_bytes()
    _, calls, state = radios()
    assert state["phase"] == "unchanged"
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_a_changing_request_is_not_applied_yet(radios):
    """TRANSITIONAL (Task 4): replaced by the flow tests of Task 4."""
    _request(radios, bluetooth={"adapter": 0})
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "apply_not_implemented")
    assert _mutating_docker_calls(calls) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_updater_radios_script.py -q`
Expected: FAIL — the script does not exist (`FileNotFoundError` / `PermissionError`).

- [ ] **Step 3: Implement** `deploy/updater/radios-once.sh` (`#!/bin/sh`, then the GPL header as `#` lines, then the code below), and `chmod +x` it:

```sh
#
# The radios job of the updater sidecar - design "Radios in the Web UI"
# (2026-09-11), section 6. One pass: report the effective radio
# configuration from .env, and when radios-request.json holds a request not
# handled yet, validate it. update-once.sh runs before this in the same
# entrypoint loop, so the two never run at the same time.

set -eu

UPDATE_DIR="${LOXMATTER_UPDATE_DIR:-/data/update}"
STACK="${LOXMATTER_STACK:-/repo/deploy/testhost}"
STACK_HOST_PATH="${LOXMATTER_STACK_HOST_PATH:-}"
HOST_DEV="${LOXMATTER_HOST_DEV:-/host/dev}"
SYS_BLUETOOTH="${LOXMATTER_SYS_BLUETOOTH:-/sys/class/bluetooth}"
MATTER_SERVER_URL="${LOXMATTER_MATTER_SERVER_URL:-http://host.docker.internal:5580/}"
BLUETOOTH_TIMEOUT="${LOXMATTER_RADIOS_BLUETOOTH_TIMEOUT:-60}"
THREAD_TIMEOUT="${LOXMATTER_RADIOS_THREAD_TIMEOUT:-90}"
THREAD_FIX_AFTER="${LOXMATTER_RADIOS_THREAD_FIX_AFTER:-30}"
POLL_SECONDS="${LOXMATTER_RADIOS_POLL_SECONDS:-5}"

REQUEST="$UPDATE_DIR/radios-request.json"
STATE="$UPDATE_DIR/radios-state.json"
LOG="$UPDATE_DIR/radios-log.txt"
UPDATE_STATE="$UPDATE_DIR/state.json"
HANDLED_DIR="$UPDATE_DIR/radios-handled"
ENV_FILE="$STACK/.env"

NEWLINE='
'

mkdir -p "$UPDATE_DIR" "$HANDLED_DIR"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log() {
  { printf '%s %s\n' "$(now)" "$*" >> "$LOG"
    tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  } 2>/dev/null || true
}

# -------------------------------------------------------------------- .env --

env_value() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

# ----------------------------------------------------------------- report --

# thread_enabled is "the profile says so, or an otbr container exists" -
# the test Pi runs otbr without a COMPOSE_PROFILES line (design 12.1).
# BLUETOOTH_ADAPTER defaults to 0 because docker-compose.yml does.
read_current() {
  CUR_DEVICE="$(env_value RADIO_DEVICE)"
  CUR_BLUETOOTH="$(env_value BLUETOOTH_ADAPTER)"
  case "$CUR_BLUETOOTH" in ''|*[!0-9]*) CUR_BLUETOOTH=0 ;; esac
  # "01" is not valid JSON for jq --argjson; strip leading zeros, keep one digit.
  CUR_BLUETOOTH="$(printf '%s' "$CUR_BLUETOOTH" | sed 's/^0*\([0-9]\)/\1/')"
  otbr_state="$(docker ps -a --filter 'name=^otbr$' --format '{{.State}}' 2>/dev/null | head -n 1 || true)"
  if [ "$otbr_state" = running ]; then CUR_OTBR_RUNNING=true; else CUR_OTBR_RUNNING=false; fi
  if env_value COMPOSE_PROFILES | tr ',' '\n' | grep -qx thread || [ -n "$otbr_state" ]; then
    CUR_ENABLED=true
  else
    CUR_ENABLED=false
  fi
}

CAPABLE=true
CAPABLE_REASON=""
if [ ! -d "$HOST_DEV" ]; then
  CAPABLE=false
  CAPABLE_REASON=host_dev_not_mounted
elif [ -z "$STACK_HOST_PATH" ]; then
  CAPABLE=false
  CAPABLE_REASON=stack_host_path_unknown
fi

JOB_ID=""
JOB_PHASE=idle
JOB_STEPS='[]'
JOB_ERROR=""
ROLLED=false
HEALTHY=null

load_previous_state() {
  [ -f "$STATE" ] || return 0
  jq -e 'type == "object"' "$STATE" >/dev/null 2>&1 || return 0
  JOB_ID="$(jq -r '.id // empty' "$STATE")"
  JOB_PHASE="$(jq -r '.phase // "idle"' "$STATE")"
  JOB_STEPS="$(jq -c 'if (.steps | type) == "array" then .steps else [] end' "$STATE")"
  JOB_ERROR="$(jq -r '.error // empty' "$STATE")"
  ROLLED="$(jq -c 'if .rolled_back == true then true else false end' "$STATE")"
  HEALTHY="$(jq -c 'if (.healthy | type) == "boolean" then .healthy else null end' "$STATE")"
}

write_state() {
  JOB_PHASE="$1"
  JOB_ERROR="${2:-}"
  if [ -e "$STATE" ] && [ ! -f "$STATE" ]; then
    printf 'write_state: %s exists and is not a regular file - refusing to write\n' "$STATE" >&2
    return 1
  fi
  read_current
  jq -n \
    --arg id "$JOB_ID" --arg phase "$JOB_PHASE" --argjson steps "$JOB_STEPS" \
    --arg error "$JOB_ERROR" --argjson rolled "$ROLLED" --argjson healthy "$HEALTHY" \
    --argjson enabled "$CUR_ENABLED" --arg device "$CUR_DEVICE" \
    --argjson bluetooth "$CUR_BLUETOOTH" --argjson running "$CUR_OTBR_RUNNING" \
    --argjson capable "$CAPABLE" --arg reason "$CAPABLE_REASON" --arg seen "$(now)" \
    '{id: (if $id == "" then null else $id end), phase: $phase, steps: $steps,
      error: (if $error == "" then null else $error end), rolled_back: $rolled,
      healthy: $healthy,
      current: {thread_enabled: $enabled,
                thread_device: (if $device == "" then null else $device end),
                bluetooth_adapter: $bluetooth, otbr_running: $running},
      capable: $capable, capable_reason: (if $reason == "" then null else $reason end),
      seen_at: $seen}' > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

reject() {
  write_state rejected "$1"
  log "radios request ${JOB_ID:-?} rejected: $1"
  exit 0
}

load_previous_state
write_state "$JOB_PHASE" "$JOB_ERROR"

# ---------------------------------------------------------------- request --

[ -f "$REQUEST" ] || exit 0

REQUEST_ID="$(jq -r 'if type == "object" and (.id | type) == "string" then .id else empty end' "$REQUEST" 2>/dev/null || true)"
MARKER="$REQUEST_ID"
case "$REQUEST_ID" in
  ''|.|..|*"$NEWLINE"*|*[!A-Za-z0-9._-]*)
    MARKER="invalid-$(cat "$REQUEST" | cksum | cut -d ' ' -f 1)" ;;
esac
if [ "${#MARKER}" -gt 128 ]; then
  MARKER="invalid-$(cat "$REQUEST" | cksum | cut -d ' ' -f 1)"
fi
if [ "$MARKER" = "$JOB_ID" ] || [ -e "$HANDLED_DIR/$MARKER" ]; then
  exit 0
fi

UPDATE_PHASE="$(jq -r '.phase // "idle"' "$UPDATE_STATE" 2>/dev/null || echo idle)"
case "$UPDATE_PHASE" in
  queued|backup|pull|build|recreate|health|rollback)
    log "radios request $MARKER waits: an update is in phase $UPDATE_PHASE"
    exit 0 ;;
esac

JOB_ID="$MARKER"
JOB_STEPS='["validate"]'
ROLLED=false
HEALTHY=null
: > "$HANDLED_DIR/$MARKER"
write_state validate ""

[ "$MARKER" = "$REQUEST_ID" ] || reject request_malformed
[ "$CAPABLE" = true ] || reject "$CAPABLE_REASON"

jq -e '
  type == "object"
  and (keys | sort) == ["bluetooth", "id", "requested_at", "thread"]
  and (.thread | type) == "object" and (.thread | keys | sort) == ["device", "enabled"]
  and (.bluetooth | type) == "object" and (.bluetooth | keys | sort) == ["adapter"]
  and (.thread.enabled | type) == "boolean"
  and ((.thread.device == null)
       or ((.thread.device | type) == "string"
           and (.thread.device | test("\\A/dev/serial/by-id/[A-Za-z0-9._:+-]+\\z"))))
  and (.bluetooth.adapter | type) == "number"
  and .bluetooth.adapter == (.bluetooth.adapter | floor)
  and .bluetooth.adapter >= 0 and .bluetooth.adapter <= 15
' "$REQUEST" >/dev/null 2>&1 || reject request_malformed

WANT_ENABLED="$(jq -r '.thread.enabled' "$REQUEST")"
WANT_DEVICE="$(jq -r '.thread.device // empty' "$REQUEST")"
WANT_BLUETOOTH="$(jq -r '.bluetooth.adapter' "$REQUEST")"

if [ "$WANT_ENABLED" = true ]; then
  [ -n "$WANT_DEVICE" ] || reject thread_device_required
  device_link="$HOST_DEV${WANT_DEVICE#/dev}"
  [ -L "$device_link" ] || reject thread_device_not_found
  host_dev_real="$(readlink -f "$HOST_DEV" 2>/dev/null || printf '%s' "$HOST_DEV")"
  device_target="$(readlink -f "$device_link" 2>/dev/null || true)"
  device_name="${device_target#"$host_dev_real"/}"
  [ "$device_name" != "$device_target" ] || reject thread_device_not_a_serial_port
  case "$device_name" in
    ttyUSB*|ttyACM*) ;;
    *) reject thread_device_not_a_serial_port ;;
  esac
  device_number="${device_name#tty???}"
  case "$device_number" in ''|*[!0-9]*) reject thread_device_not_a_serial_port ;; esac
  [ -n "$(env_value BACKBONE_IF)" ] || reject backbone_interface_missing
fi

[ -e "$SYS_BLUETOOTH/hci$WANT_BLUETOOTH" ] || reject bluetooth_adapter_not_found

# ---------------------------------------------------------------- changes --

read_current
ORIG_ENABLED="$CUR_ENABLED"
BLUETOOTH_CHANGE=false
if [ "$WANT_BLUETOOTH" != "$CUR_BLUETOOTH" ]; then BLUETOOTH_CHANGE=true; fi
THREAD_ACTION=none
if [ "$WANT_ENABLED" = true ]; then
  if [ "$CUR_ENABLED" = false ] || [ "$WANT_DEVICE" != "$CUR_DEVICE" ]; then THREAD_ACTION=up; fi
elif [ "$CUR_ENABLED" = true ]; then
  THREAD_ACTION=down
fi

if [ "$BLUETOOTH_CHANGE" = false ] && [ "$THREAD_ACTION" = none ]; then
  write_state unchanged ""
  log "radios request $JOB_ID changes nothing"
  exit 0
fi

[ -f "$ENV_FILE" ] || reject env_file_missing

write_state failed apply_not_implemented # TRANSITIONAL (Task 4)
log "radios request $JOB_ID: applying is not implemented yet" # TRANSITIONAL (Task 4)
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/test_updater_radios_script.py -q` — Expected: PASS. If `readlink -f` or `date` behave differently on your machine, report the difference; do not weaken an assertion.

- [ ] **Step 5: Prove the protections** named in the docstrings (thread_enabled from container only / profile only; traversal regex replaced by `startswith`; update-phase check dropped). Paste fail and pass outputs.

- [ ] **Step 6: Checks and commit**

```bash
git add deploy/updater/radios-once.sh tests/test_updater_radios_script.py
git commit -m "feat(updater): report radio configuration and validate radio requests

A new sidecar script reports the effective Thread and Bluetooth settings
from .env on every pass and rejects any radio request that is not an
existing by-id serial device or Bluetooth adapter, without touching .env or
running a changing Docker command."
```

---

### Task 4: The Sidecar Job — Apply, Verify, Roll Back

**Files:**
- Modify: `deploy/updater/radios-once.sh` (replace the two `# TRANSITIONAL (Task 4)` lines)
- Modify: `tests/test_updater_radios_script.py` (delete `test_a_changing_request_is_not_applied_yet`, add the tests below)

**Interfaces:**
- Consumes: Task 3's variables `WANT_*`, `CUR_*`, `ORIG_ENABLED`, `BLUETOOTH_CHANGE`, `THREAD_ACTION`, functions `write_state`, `read_current`, `env_value`, `log`.
- Produces: phases `backup`, `write`, `apply_bluetooth`, `verify_bluetooth`, `apply_thread`, `verify_thread`, `rollback`, `done`, `failed`; error keys `env_backup_failed`, `apply_bluetooth_failed`, `verify_bluetooth_failed`, `apply_thread_failed`, `verify_thread_failed`.

- [ ] **Step 1: Write the failing tests.** Delete `test_a_changing_request_is_not_applied_yet` and append:

```python
def _compose(calls: str, *words: str) -> list[str]:
    return [
        line for line in calls.splitlines()
        if line.startswith("docker compose") and all(word in line.split() for word in words)
    ]


def test_switching_the_bluetooth_adapter_recreates_only_matter_server(radios):
    (radios.sys_bluetooth / "hci1").mkdir()
    radios.env_file.write_text(radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}"))
    _request(radios, bluetooth={"adapter": 1})
    _, calls, state = radios()
    assert (state["phase"], state["healthy"], state["rolled_back"]) == ("done", True, False)
    assert state["steps"] == ["validate", "backup", "write", "apply_bluetooth", "verify_bluetooth"]
    assert "BLUETOOTH_ADAPTER=1\n" in radios.env_file.read_text()
    assert len(_compose(calls, "up", "--force-recreate", "matter-server")) == 1
    assert _compose(calls, "otbr") == []
    assert "curl " in calls
    assert list(radios.env_file.parent.glob(".env.radios-*"))


def test_switching_the_thread_stick_writes_the_by_id_path_and_recreates_otbr(radios):
    radios.env_file.write_text(radios.env_file.read_text() + "COMPOSE_PROFILES=foo\n")
    _request(radios)
    _, calls, state = radios()
    text = radios.env_file.read_text()
    assert state["phase"] == "done"
    assert f"RADIO_DEVICE=/dev/serial/by-id/{SONOFF}\n" in text
    assert "COMPOSE_PROFILES=foo,thread\n" in text
    assert len(_compose(calls, "up", "--force-recreate", "otbr")) == 1
    assert _compose(calls, "matter-server") == []
    assert "ot-ctl state" in calls


def test_enabling_thread_adds_a_missing_baud_rate_and_keeps_an_existing_one(radios):
    (radios.fake / "otbr_state").unlink()
    radios.env_file.write_text("RADIO_DEVICE=\nBACKBONE_IF=wlan0\nBLUETOOTH_ADAPTER=0\n")
    _request(radios)
    _, _, state = radios()
    assert state["phase"] == "done"
    assert "RADIO_BAUDRATE=460800\n" in radios.env_file.read_text()
    assert "COMPOSE_PROFILES=thread\n" in radios.env_file.read_text()


def test_disabling_thread_removes_otbr_and_only_the_thread_profile(radios):
    radios.env_file.write_text(radios.env_file.read_text() + "COMPOSE_PROFILES=foo,thread\n")
    _request(radios, thread={"enabled": False, "device": None})
    _, calls, state = radios()
    assert state["phase"] == "done"
    assert "COMPOSE_PROFILES=foo\n" in radios.env_file.read_text()
    assert len(_compose(calls, "rm", "otbr")) == 1
    assert state["current"]["otbr_running"] is False


def test_a_hanging_agent_gets_the_watchdog_fix_and_recovers(radios):
    (radios.fake / "thread_mode").write_text("needs_fix")
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "done"
    assert calls.count("rm -f /run/otbr-agent.pid") == 1
    assert len(_compose(calls, "restart", "otbr")) == 1


def test_a_thread_stick_that_never_forms_the_network_is_rolled_back(radios):
    """Two faults to prove it, one at a time: comment out the line that
    copies the backup back over .env (the byte comparison fails); remove
    the `fixed=1` guard (the pid fix then repeats within one verification,
    and the count of two - one per verification, apply and rollback -
    fails)."""
    before = radios.env_file.read_bytes()
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "verify_thread_failed")
    assert (state["rolled_back"], state["healthy"]) == (True, False)
    assert radios.env_file.read_bytes() == before
    assert len(_compose(calls, "up", "otbr")) == 2
    assert calls.count("rm -f /run/otbr-agent.pid") == 2


def test_a_rollback_that_brings_thread_back_reports_healthy(radios):
    (radios.fake / "thread_mode").write_text("second_up")
    _request(radios)
    _, _, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "verify_thread_failed")
    assert (state["rolled_back"], state["healthy"]) == (True, True)


def test_enabling_thread_that_fails_rolls_back_to_disabled(radios):
    (radios.fake / "otbr_state").unlink()
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "failed"
    assert len(_compose(calls, "rm", "otbr")) == 1
    assert state["current"]["otbr_running"] is False


def test_a_matter_server_that_does_not_come_back_is_rolled_back(radios):
    (radios.sys_bluetooth / "hci1").mkdir()
    radios.env_file.write_text(radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}"))
    before = radios.env_file.read_bytes()
    (radios.fake / "matter_up").write_text("no")
    _request(radios, bluetooth={"adapter": 1})
    _, calls, state = radios()
    assert (state["phase"], state["error"], state["rolled_back"]) == ("failed", "verify_bluetooth_failed", True)
    assert state["healthy"] is False
    assert radios.env_file.read_bytes() == before
    assert len(_compose(calls, "up", "matter-server")) == 2


def test_a_failing_compose_call_is_rolled_back_too(radios):
    (radios.fake / "compose_fail").write_text("--force-recreate otbr")
    before = radios.env_file.read_bytes()
    _request(radios)
    _, _, state = radios()
    assert (state["phase"], state["error"], state["rolled_back"]) == ("failed", "apply_thread_failed", True)
    assert radios.env_file.read_bytes() == before
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_updater_radios_script.py -q`
Expected: the new tests FAIL with phase `failed` / error `apply_not_implemented`.

- [ ] **Step 3: Implement.** In `radios-once.sh`, add below `env_value()`:

```sh
env_target() {
  target="$ENV_FILE"
  if [ -L "$target" ]; then
    resolved="$(readlink -f "$target" 2>/dev/null || true)"
    if [ -n "$resolved" ]; then target="$resolved"; fi
  fi
  printf '%s' "$target"
}

# Rewrites through `cat >`, not `mv`, so a symlinked .env and the file's
# inode stay what the operator set up - the same care as set_tag() in
# update-once.sh. Values reaching here passed validation: a by-id path
# ([A-Za-z0-9._:+-]), a number, or a profile list built below.
env_set() {
  target="$(env_target)"
  if grep -q "^$1=" "$target" 2>/dev/null; then
    escaped="$(printf '%s' "$2" | sed 's/[\\&|]/\\&/g')"
    sed "s|^$1=.*|$1=$escaped|" "$target" > "$target.radios-tmp"
    cat "$target.radios-tmp" > "$target"
    rm -f "$target.radios-tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> "$target"
  fi
}

profiles_with_thread() {
  others="$(env_value COMPOSE_PROFILES | tr ',' '\n' | awk 'NF && $0 != "thread" { printf "%s%s", sep, $0; sep = "," }')"
  if [ "$1" = on ]; then
    if [ -n "$others" ]; then printf '%s,thread' "$others"; else printf 'thread'; fi
  else
    printf '%s' "$others"
  fi
}

compose() {
  log "\$ docker compose -f $STACK/docker-compose.yml --project-directory $STACK_HOST_PATH --env-file $ENV_FILE $*"
  docker compose -f "$STACK/docker-compose.yml" --project-directory "$STACK_HOST_PATH" \
    --env-file "$ENV_FILE" "$@" >> "$LOG" 2>&1
}
```

Replace the two `# TRANSITIONAL (Task 4)` lines at the end with:

```sh
JOB_STEPS='["validate","backup","write"]'
if [ "$BLUETOOTH_CHANGE" = true ]; then
  JOB_STEPS="$(printf '%s' "$JOB_STEPS" | jq -c '. + ["apply_bluetooth","verify_bluetooth"]')"
fi
if [ "$THREAD_ACTION" != none ]; then
  JOB_STEPS="$(printf '%s' "$JOB_STEPS" | jq -c '. + ["apply_thread","verify_thread"]')"
fi

if [ "$THREAD_ACTION" = up ] && [ "$ORIG_ENABLED" = true ]; then
  ROLLBACK_THREAD=up
elif [ "$THREAD_ACTION" = up ]; then
  ROLLBACK_THREAD=down
elif [ "$THREAD_ACTION" = down ]; then
  ROLLBACK_THREAD=up
else
  ROLLBACK_THREAD=none
fi

BACKUP="$(env_target).radios-$(date -u +%Y%m%d%H%M%S)"
write_state backup ""
if ! cp "$(env_target)" "$BACKUP"; then
  write_state failed env_backup_failed
  log "radios request $JOB_ID: could not back up .env"
  exit 0
fi

write_state write ""
if [ "$THREAD_ACTION" = up ]; then
  env_set RADIO_DEVICE "$WANT_DEVICE"
  env_set COMPOSE_PROFILES "$(profiles_with_thread on)"
  if [ -z "$(env_value RADIO_BAUDRATE)" ]; then env_set RADIO_BAUDRATE 460800; fi
elif [ "$THREAD_ACTION" = down ]; then
  env_set COMPOSE_PROFILES "$(profiles_with_thread off)"
fi
if [ "$BLUETOOTH_CHANGE" = true ]; then env_set BLUETOOTH_ADAPTER "$WANT_BLUETOOTH"; fi

verify_bluetooth() {
  waited=0
  while [ "$waited" -lt "$BLUETOOTH_TIMEOUT" ]; do
    if curl -s -o /dev/null --max-time 3 "$MATTER_SERVER_URL"; then return 0; fi
    sleep "$POLL_SECONDS"
    waited=$((waited + POLL_SECONDS))
  done
  return 1
}

apply_thread() {
  if [ "$1" = up ]; then
    compose up -d --no-deps --force-recreate otbr
  else
    compose rm -s -f otbr
  fi
}

# The watchdog's known fix (scripts/otbr-watchdog.sh): on the Pi kernel
# start-stop-daemon can leave a stale pid file and otbr-agent never runs.
# Applied at most once per verification.
verify_thread() {
  if [ "$1" = down ]; then
    [ -z "$(docker ps -a --filter 'name=^otbr$' --format '{{.Names}}' 2>/dev/null)" ]
    return
  fi
  waited=0
  fixed=0
  while [ "$waited" -lt "$THREAD_TIMEOUT" ]; do
    case "$(docker exec otbr ot-ctl state 2>/dev/null | tr -d '\r' | head -n 1)" in
      leader|router|child) return 0 ;;
    esac
    if [ "$fixed" -eq 0 ] && [ "$waited" -ge "$THREAD_FIX_AFTER" ]; then
      fixed=1
      log "no Thread state after ${waited}s - clearing the stale pid file and restarting otbr"
      docker exec otbr rm -f /run/otbr-agent.pid >/dev/null 2>&1 || true
      compose restart otbr || true
    fi
    sleep "$POLL_SECONDS"
    waited=$((waited + POLL_SECONDS))
  done
  return 1
}

ROLLING=false
FAILED_STEP=""

step() {
  if [ "$ROLLING" = false ]; then write_state "$1" ""; fi
}

apply_and_verify() {
  if [ "$BLUETOOTH_CHANGE" = true ]; then
    step apply_bluetooth
    compose up -d --no-deps --force-recreate matter-server || { FAILED_STEP=apply_bluetooth; return 1; }
    step verify_bluetooth
    verify_bluetooth || { FAILED_STEP=verify_bluetooth; return 1; }
  fi
  if [ "$THREAD_ACTION" != none ]; then
    step apply_thread
    apply_thread "$THREAD_ACTION" || { FAILED_STEP=apply_thread; return 1; }
    step verify_thread
    verify_thread "$THREAD_ACTION" || { FAILED_STEP=verify_thread; return 1; }
  fi
  return 0
}

if apply_and_verify; then
  HEALTHY=true
  write_state done ""
  log "radios request $JOB_ID applied"
  exit 0
fi

ERROR_KEY="${FAILED_STEP}_failed"
log "radios request $JOB_ID: $FAILED_STEP failed - restoring $BACKUP"
write_state rollback "$ERROR_KEY"
cat "$BACKUP" > "$(env_target)"
ROLLED=true
ROLLING=true
THREAD_ACTION="$ROLLBACK_THREAD"
if apply_and_verify; then HEALTHY=true; else HEALTHY=false; fi
write_state failed "$ERROR_KEY"
log "radios request $JOB_ID rolled back, healthy after rollback: $HEALTHY"
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/test_updater_radios_script.py -q` — Expected: PASS.

- [ ] **Step 5: Prove the protections** named in `test_a_thread_stick_that_never_forms_the_network_is_rolled_back` (remove the `fixed=1` guard; comment out `cat "$BACKUP" > "$(env_target)"`). Paste fail and pass outputs. Also run `sh -n deploy/updater/radios-once.sh` and, if available, `shellcheck -s sh deploy/updater/radios-once.sh`; report its output (do not add suppressions without saying why).

- [ ] **Step 6: Checks and commit**

```bash
git add deploy/updater/radios-once.sh tests/test_updater_radios_script.py
git commit -m "feat(updater): apply radio changes, verify them and roll back on failure

Writes only RADIO_DEVICE, the thread profile entry and BLUETOOTH_ADAPTER,
recreates only otbr or matter-server, waits for the Thread network or the
matter-server port, applies the watchdog's pid fix at most once, and
restores the previous .env when verification fails."
```

---

### Task 5: Run and Ship the Job

**Files:**
- Modify: `deploy/updater/entrypoint.sh`, `deploy/updater/Dockerfile`, `deploy/testhost/docker-compose.yml`
- Test: `tests/test_updater_entrypoint.py`, `tests/test_updater_image.py`, `tests/test_compose_profiles.py`

**Interfaces:**
- Consumes: `deploy/updater/radios-once.sh` (Tasks 3–4).
- Produces: entrypoint variable `RADIOS_WORKER` (default `/opt/loxmatter/radios-once.sh`); the image contains `/opt/loxmatter/radios-once.sh`; Compose mounts `/dev:/host/dev:ro` into `loxmatter` and `loxmatter-updater`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_updater_entrypoint.py`:

```python
def test_the_radios_worker_runs_after_the_update_worker_in_one_pass(tmp_path: Path) -> None:
    """Fault to prove it: remove the radios worker call from the loop."""
    order = tmp_path / "order.log"
    update = _script(tmp_path, "update.sh", f'echo update >> "{order}"')
    radios = _script(tmp_path, "radios.sh", f'echo radios >> "{order}"')

    result = _run(tmp_path, update, RADIOS_WORKER=str(radios), WORKER_TIMEOUT_SECONDS="5")

    assert result.returncode == 0, result.stderr
    assert order.read_text().split() == ["update", "radios"]


def test_a_failing_update_worker_does_not_skip_the_radios_worker(tmp_path: Path) -> None:
    order = tmp_path / "order.log"
    update = _script(tmp_path, "update.sh", "exit 7")
    radios = _script(tmp_path, "radios.sh", f'echo radios >> "{order}"')

    result = _run(tmp_path, update, RADIOS_WORKER=str(radios), WORKER_TIMEOUT_SECONDS="5")

    assert result.returncode == 0, result.stderr
    assert order.read_text().split() == ["radios"]


def test_a_missing_radios_worker_is_skipped_quietly(tmp_path: Path) -> None:
    update = _script(tmp_path, "update.sh", "exit 0")

    result = _run(tmp_path, update, RADIOS_WORKER=str(tmp_path / "absent.sh"), WORKER_TIMEOUT_SECONDS="5")

    assert result.returncode == 0, result.stderr
    assert "absent.sh" not in result.stderr
```

Append to `tests/test_updater_image.py`:

```python
def test_the_image_ships_the_radios_job() -> None:
    """Fault to prove it: remove radios-once.sh from the COPY line."""
    source = DOCKERFILE.read_text(encoding="utf-8")
    copy = next(line for line in source.splitlines() if line.startswith("COPY "))
    chmod = next(line for line in source.splitlines() if "chmod +x" in line)
    assert "radios-once.sh" in copy.split()
    assert "/opt/loxmatter/radios-once.sh" in chmod.split()
```

Append to `tests/test_compose_profiles.py`:

```python
def test_only_the_bridge_and_the_updater_see_the_host_dev_tree_read_only() -> None:
    """Design 2026-09-11 "Radios in the Web UI", section 5: names under
    /dev/serial/by-id only, never device access. Fault to prove it: mount
    `/dev:/host/dev` without `:ro` on one of them."""
    for name, service in _stack()["services"].items():
        mounts = [str(v) for v in service.get("volumes", []) if str(v).startswith("/dev:")]
        if name in ("loxmatter", "loxmatter-updater"):
            assert mounts == ["/dev:/host/dev:ro"], name
        else:
            assert mounts == [], name
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_updater_entrypoint.py tests/test_updater_image.py tests/test_compose_profiles.py -q`
Expected: the three new groups FAIL (order file missing, `radios-once.sh` not in COPY, no `/dev` mounts).

- [ ] **Step 3: Implement.**

`deploy/updater/entrypoint.sh`: add below `WORKER_TIMEOUT_SECONDS=…`:

```sh
# The radios job (design "Radios in the Web UI", 2026-09-11, section 6.1)
# runs right after update-once.sh in the same pass, so the two never
# overlap. Skipped when absent, so a test or a trimmed image without it
# keeps working.
RADIOS_WORKER="${RADIOS_WORKER:-/opt/loxmatter/radios-once.sh}"
```

Replace the body of the `while [ "$terminated" -eq 0 ]; do … done` loop by a function and two calls. Define above the loop:

```sh
run_worker() {
  timeout "$WORKER_TIMEOUT_SECONDS" "$1" &
  child_pid=$!
  [ "$terminated" -eq 1 ] && kill -TERM "$child_pid" 2>/dev/null
  wait "$child_pid"
  status=$?
  while [ "$terminated" -eq 1 ] && kill -0 "$child_pid" 2>/dev/null; do
    wait "$child_pid"
    status=$?
  done
  child_pid=""
  if [ "$status" -eq 124 ]; then
    echo "entrypoint: ${1##*/} exceeded ${WORKER_TIMEOUT_SECONDS}s and was killed" >&2
  fi
}
```

and make the loop:

```sh
while [ "$terminated" -eq 0 ]; do
  run_worker "$WORKER"
  [ "$terminated" -eq 1 ] && break
  if [ -x "$RADIOS_WORKER" ]; then
    run_worker "$RADIOS_WORKER"
    [ "$terminated" -eq 1 ] && break
  fi
  if [ "${LOOP_ONCE:-0}" = "1" ]; then
    break
  fi
  sleep 2
done
```

Keep every existing comment in the file; move the comments that explained the old inline loop body onto `run_worker`. If an existing test asserts the literal text `update-once.sh exceeded`, check what `${1##*/}` yields for its worker path and report it instead of changing the assertion.

`deploy/updater/Dockerfile`:

```dockerfile
COPY entrypoint.sh update-once.sh radios-once.sh /opt/loxmatter/
RUN chmod +x /opt/loxmatter/entrypoint.sh /opt/loxmatter/update-once.sh /opt/loxmatter/radios-once.sh
```

`deploy/testhost/docker-compose.yml`: add `- /dev:/host/dev:ro` as the last entry of `volumes:` of both `loxmatter` and `loxmatter-updater`, each with this comment above it:

```yaml
      # The host's /dev, read-only, only to read the names under
      # /dev/serial/by-id (design "Radios in the Web UI", 2026-09-11,
      # section 5). Without a device cgroup rule no device node here can be
      # opened, so this grants no access to a radio.
```

- [ ] **Step 4: Run the tests** — same command as Step 2 — Expected: PASS.

- [ ] **Step 5: Prove the protections** (radios call removed from the loop; `radios-once.sh` removed from COPY; `:ro` removed on one service). Paste outputs.

- [ ] **Step 6: Checks and commit**

```bash
git add deploy/updater tests/test_updater_entrypoint.py tests/test_updater_image.py deploy/testhost/docker-compose.yml tests/test_compose_profiles.py
git commit -m "feat(updater): run the radios job and mount /dev read-only for detection

The sidecar loop runs radios-once.sh after update-once.sh, the image ships
it, and the bridge and the sidecar see the host's /dev read-only to read
/dev/serial/by-id names."
```

---

### Task 6: The Radios API

**Files:**
- Create: `src/loxmatter/api/radios.py`
- Modify: `src/loxmatter/loxone/server.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_radios_api.py`

**Interfaces:**
- Consumes: Task 1 (`scan_serial`, `scan_bluetooth`, `match_current_device`), Task 2 (`read_radios_state`, `sidecar_status`, `request_radios`, `RadiosBusyError`, `TERMINAL_PHASES`), `loxmatter.update.read_state`.
- Produces:
  - `build_radios_router(update_dir: Path, *, host_dev: Path, sys_root: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> APIRouter`
  - `build_app(..., radios_host_dev: Path = Path("/host/dev"), radios_sys_root: Path = Path("/sys"))`
  - `GET /api/radios` → `{sidecar, sidecar_stack_host_path, serial[], bluetooth[], current | null, job | null}`
  - `POST /api/radios` with `{thread: {enabled: bool, device: str | null}, bluetooth: {adapter: int}}` → `202 {id}`; 400/409/503 with i18n details

- [ ] **Step 1: Add the i18n keys** to `src/loxmatter/i18n/strings.yaml`, next to the other `api.update.*` keys:

```yaml
api.radios.fail_sidecar_not_ready:
  en: "Radios cannot be changed right now: the updater service is missing, outdated or lacks access to /dev"
  de: "Funkgeräte lassen sich gerade nicht ändern: Der Updater-Dienst fehlt, ist veraltet oder hat keinen Zugriff auf /dev"
api.radios.fail_thread_device_required:
  en: "Choose a USB stick to enable Thread"
  de: "Zum Einschalten von Thread einen USB-Stick auswählen"
api.radios.fail_unknown_device:
  en: "This USB stick is not attached to the host"
  de: "Dieser USB-Stick ist nicht am Host angeschlossen"
api.radios.fail_unknown_adapter:
  en: "This Bluetooth adapter does not exist on the host"
  de: "Diesen Bluetooth-Adapter gibt es am Host nicht"
api.radios.fail_busy:
  en: "An update or another radio change is still running"
  de: "Ein Update oder eine andere Funkgeräte-Änderung läuft noch"
api.radios.fail_unwritable:
  en: "The request could not be written: {exc}"
  de: "Der Auftrag konnte nicht geschrieben werden: {exc}"
```

- [ ] **Step 2: Write the failing tests** `tests/api/test_radios_api.py` (GPL header first):

```python
"""GET and POST /api/radios (design 2026-09-11 "Radios in the Web UI",
section 7)."""

from __future__ import annotations

import json
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
    body = {"id": None, "phase": phase, "updater_seen_at": _now(), "updater_stack_host_path": "/home/pi/stack"}
    (update_dir / "state.json").write_text(json.dumps(body), encoding="utf-8")


def _radios_heartbeat(update_dir: Path, **fields: Any) -> None:
    body = {
        "id": None, "phase": "idle", "steps": [], "error": None, "rolled_back": False, "healthy": None,
        "current": {"thread_enabled": True, "thread_device": "/dev/ttyUSB0", "bluetooth_adapter": 0, "otbr_running": True},
        "capable": True, "capable_reason": None, "seen_at": _now(),
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
        store, no_invoke, fake_runtime(store), update_dir=update_dir,
        radios_host_dev=host_dev, radios_sys_root=sys_root,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await authenticate(store, client)
        yield client, update_dir
    store.close()


async def test_without_a_sidecar_the_card_is_read_only_with_detection(api):
    client, _ = api
    body = (await client.get("/api/radios")).json()
    assert body["sidecar"] == "missing"
    assert [r["product"] for r in body["serial"]] == ["SONOFF Dongle Plus MG24"]
    assert body["bluetooth"] == [{"index": 0, "name": "hci0", "bus": "uart", "product": None, "rfkill_blocked": False}]
    assert body["current"] is None


async def test_a_ready_sidecar_reports_current_with_the_legacy_path_mapped(api):
    """Fault to prove it: return the sidecar's raw thread_device."""
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    body = (await client.get("/api/radios")).json()
    assert body["sidecar"] == "ready"
    assert body["sidecar_stack_host_path"] == "/home/pi/stack"
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
    _radios_heartbeat(update_dir, id="j1", phase="failed", steps=["validate", "verify_thread"],
                      error="verify_thread_failed", rolled_back=True, healthy=True)
    job = (await client.get("/api/radios")).json()["job"]
    assert job == {"id": "j1", "phase": "failed", "steps": ["validate", "verify_thread"],
                   "error": "verify_thread_failed", "rolled_back": True, "healthy": True}


def _body(device: str | None = f"/dev/serial/by-id/{SONOFF}", enabled: bool = True, adapter: int = 0):
    return {"thread": {"enabled": enabled, "device": device}, "bluetooth": {"adapter": adapter}}


async def test_a_valid_change_writes_a_request(api):
    client, update_dir = api
    _update_heartbeat(update_dir)
    _radios_heartbeat(update_dir)
    response = await client.post("/api/radios", json=_body())
    assert response.status_code == 202
    request = json.loads((update_dir / "radios-request.json").read_text(encoding="utf-8"))
    assert request["id"] == response.json()["id"]


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
    assert (await client.post("/api/radios", json=_body(device=None, enabled=False))).status_code == 202


async def test_a_running_update_is_a_409(api):
    client, update_dir = api
    _update_heartbeat(update_dir, phase="recreate")
    _radios_heartbeat(update_dir)
    assert (await client.post("/api/radios", json=_body())).status_code == 409


async def test_the_routes_need_a_login(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Without a password set and a session, no /api route answers with
        # content (README, "Locked down by default").
        assert (await client.get("/api/radios")).status_code != 200
    store.close()
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/api/test_radios_api.py -q`
Expected: FAIL — `build_app() got an unexpected keyword argument 'radios_host_dev'` / 404.

- [ ] **Step 4: Implement** `src/loxmatter/api/radios.py` (GPL header, then):

```python
"""`GET` and `POST /api/radios` (design 2026-09-11 "Radios in the Web UI",
section 7).

The bridge only ever reads and writes files here. It validates what it can
see - the sidecar is ready, the device and adapter exist in its own
inventory - so the UI gets an immediate answer; the sidecar validates again
against the host and has the last word (section 6.3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loxmatter import i18n
from loxmatter import update as update_files
from loxmatter.radios.inventory import match_current_device, scan_bluetooth, scan_serial
from loxmatter.radios.sidecar import (
    RadiosBusyError,
    read_radios_state,
    request_radios,
    sidecar_status,
)


class ThreadIn(BaseModel):
    enabled: bool
    device: str | None


class BluetoothIn(BaseModel):
    adapter: int


class RadiosIn(BaseModel):
    thread: ThreadIn
    bluetooth: BluetoothIn


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_radios_router(
    update_dir: Path,
    *,
    host_dev: Path,
    sys_root: Path,
    clock: Callable[[], datetime] = _utc_now,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/radios")
    async def get_radios() -> dict[str, object]:
        update_state = update_files.read_state(update_dir)
        radios_state = read_radios_state(update_dir)
        status = sidecar_status(update_state, radios_state, now=clock())
        serial = scan_serial(host_dev, sys_root)
        bluetooth = scan_bluetooth(sys_root)
        current: dict[str, object] | None = None
        if status in ("ready", "unmounted") and radios_state and radios_state.current:
            reported = radios_state.current
            device, present = match_current_device(reported.thread_device, serial)
            current = {
                "thread_enabled": reported.thread_enabled,
                "thread_device": device,
                "thread_device_present": present,
                "bluetooth_adapter": reported.bluetooth_adapter,
                "otbr_running": reported.otbr_running,
            }
        job: dict[str, object] | None = None
        if radios_state is not None and radios_state.id is not None:
            job = {
                "id": radios_state.id,
                "phase": radios_state.phase,
                "steps": list(radios_state.steps),
                "error": radios_state.error,
                "rolled_back": radios_state.rolled_back,
                "healthy": radios_state.healthy,
            }
        return {
            "sidecar": status,
            "sidecar_stack_host_path": (
                update_state.updater_stack_host_path if update_state is not None else None
            ),
            "serial": [asdict(radio) for radio in serial],
            "bluetooth": [asdict(adapter) for adapter in bluetooth],
            "current": current,
            "job": job,
        }

    @router.post("/radios", status_code=202)
    async def post_radios(body: RadiosIn) -> dict[str, str]:
        status = sidecar_status(
            update_files.read_state(update_dir), read_radios_state(update_dir), now=clock()
        )
        if status != "ready":
            raise HTTPException(status_code=503, detail=i18n.t("api.radios.fail_sidecar_not_ready"))
        if body.thread.enabled and body.thread.device is None:
            raise HTTPException(
                status_code=400, detail=i18n.t("api.radios.fail_thread_device_required")
            )
        if body.thread.enabled and body.thread.device not in {
            radio.path for radio in scan_serial(host_dev, sys_root)
        }:
            raise HTTPException(status_code=400, detail=i18n.t("api.radios.fail_unknown_device"))
        if body.bluetooth.adapter not in {adapter.index for adapter in scan_bluetooth(sys_root)}:
            raise HTTPException(status_code=400, detail=i18n.t("api.radios.fail_unknown_adapter"))
        try:
            job_id = request_radios(
                update_dir,
                thread_enabled=body.thread.enabled,
                thread_device=body.thread.device if body.thread.enabled else None,
                bluetooth_adapter=body.bluetooth.adapter,
            )
        except RadiosBusyError as exc:
            raise HTTPException(status_code=409, detail=i18n.t("api.radios.fail_busy")) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail=i18n.t("api.radios.fail_unwritable", exc=str(exc))
            ) from exc
        return {"id": job_id}

    return router
```

In `src/loxmatter/loxone/server.py`: import `from loxmatter.api.radios import build_radios_router`; add the parameters `radios_host_dev: Path = Path("/host/dev")` and `radios_sys_root: Path = Path("/sys")` after `update_dir`; and directly after `app.include_router(build_update_router(store, update_dir), dependencies=api_guard)`:

```python
    app.include_router(
        build_radios_router(update_dir, host_dev=radios_host_dev, sys_root=radios_sys_root),
        dependencies=api_guard,
    )
```

- [ ] **Step 5: Run the tests** — `uv run pytest tests/api/test_radios_api.py -q` — Expected: PASS.

- [ ] **Step 6: Prove the protections** (raw `thread_device` returned; readiness check dropped). Paste outputs.

- [ ] **Step 7: Checks and commit**

```bash
git add src/loxmatter/api/radios.py src/loxmatter/loxone/server.py src/loxmatter/i18n/strings.yaml tests/api/test_radios_api.py
git commit -m "feat(api): serve detected radios and accept radio change requests

GET /api/radios combines detection, the sidecar's report and the last job;
POST writes a request only when the sidecar is ready and the device and
adapter exist, and answers 409 while an update or radio job runs."
```

---

### Task 7: The Settings Card

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css` (only if a rule is missing), `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `GET/POST /api/radios` (Task 6).
- Produces (in `app()` of `app.js`): state `radios`, `radiosDraft: {threadDevice: string, bluetoothAdapter: number}`, `radiosDirty`, `radiosConfirming`, `radiosError`, `radiosBusy`, `radiosTimer`; methods `loadRadios()`, `radiosThreadOptions()`, `radiosBluetoothOptions()`, `radiosChanged()`, `radiosConfirmKeys()`, `radiosJobRunning()`, `radiosStepClass(step)`, `radiosResultKey()`, `radiosSidecarMessage()`, `askApplyRadios()`, `confirmApplyRadios()`, `cancelApplyRadios()`.

- [ ] **Step 1: Add the i18n keys** to `src/loxmatter/i18n/strings.yaml` next to the other `web.settings.*` keys:

```yaml
web.radios.heading:
  en: "Radios"
  de: "Funkgeräte"
web.radios.explanation:
  en: "Which USB stick the Thread border router uses, and which Bluetooth adapter commissions Matter devices. Changes are applied by the updater service."
  de: "Welchen USB-Stick der Thread-Border-Router nutzt und über welchen Bluetooth-Adapter Matter-Geräte angelernt werden. Änderungen setzt der Updater-Dienst um."
web.radios.thread_label:
  en: "Thread"
  de: "Thread"
web.radios.bluetooth_label:
  en: "Bluetooth"
  de: "Bluetooth"
web.radios.no_thread_stick:
  en: "No Thread stick (Thread off)"
  de: "Kein Thread-Stick (Thread aus)"
web.radios.in_use:
  en: "in use"
  de: "in Verwendung"
web.radios.missing:
  en: "{path} (missing)"
  de: "{path} (fehlt)"
web.radios.bus_uart:
  en: "{name} · built in (UART)"
  de: "{name} · eingebaut (UART)"
web.radios.bus_usb:
  en: "{name} · USB {product}"
  de: "{name} · USB {product}"
web.radios.bus_other:
  en: "{name}"
  de: "{name}"
web.radios.rfkill_blocked:
  en: "Blocked by rfkill - unblocking needs root on the host, see docs/SETUP.md"
  de: "Durch rfkill gesperrt – Entsperren braucht Root auf dem Host, siehe docs/SETUP.md"
web.radios.rescan:
  en: "Rescan"
  de: "Neu einlesen"
web.radios.apply:
  en: "Apply"
  de: "Übernehmen"
web.radios.confirm_title:
  en: "Change radios?"
  de: "Funkgeräte ändern?"
web.radios.confirm_thread_switch:
  en: "The Thread border router restarts with the new stick. Thread devices are unreachable for one to two minutes and rejoin on their own. The Thread network stays the same."
  de: "Der Thread-Border-Router startet mit dem neuen Stick neu. Thread-Geräte sind ein bis zwei Minuten nicht erreichbar und treten danach von selbst wieder bei. Das Thread-Netz bleibt dasselbe."
web.radios.confirm_thread_on:
  en: "The Thread border router starts with this stick."
  de: "Der Thread-Border-Router startet mit diesem Stick."
web.radios.confirm_thread_off:
  en: "The Thread border router stops. Thread devices stay unreachable until Thread is switched on again."
  de: "Der Thread-Border-Router wird beendet. Thread-Geräte bleiben unerreichbar, bis Thread wieder eingeschaltet wird."
web.radios.confirm_bluetooth:
  en: "matter-server restarts. A commissioning in progress is aborted."
  de: "matter-server startet neu. Ein laufendes Anlernen wird abgebrochen."
web.radios.confirm_rollback:
  en: "If it fails, the previous setting is restored automatically."
  de: "Schlägt es fehl, wird die vorherige Einstellung automatisch wiederhergestellt."
web.radios.confirm_apply:
  en: "Change"
  de: "Ändern"
web.radios.cancel:
  en: "Cancel"
  de: "Abbrechen"
web.radios.step_validate:
  en: "Check the setting"
  de: "Einstellung prüfen"
web.radios.step_backup:
  en: "Back up the configuration"
  de: "Konfiguration sichern"
web.radios.step_write:
  en: "Write the configuration"
  de: "Konfiguration schreiben"
web.radios.step_apply_bluetooth:
  en: "Restart matter-server"
  de: "matter-server neu starten"
web.radios.step_verify_bluetooth:
  en: "Wait for matter-server"
  de: "Auf matter-server warten"
web.radios.step_apply_thread:
  en: "Restart the Thread border router"
  de: "Thread-Border-Router neu starten"
web.radios.step_verify_thread:
  en: "Check the Thread network (up to 90 s)"
  de: "Thread-Netz prüfen (bis 90 s)"
web.radios.result_done:
  en: "Applied."
  de: "Übernommen."
web.radios.result_unchanged:
  en: "Nothing to change."
  de: "Nichts zu ändern."
web.radios.result_rejected:
  en: "Not applied: {reason}"
  de: "Nicht übernommen: {reason}"
web.radios.result_failed_restored:
  en: "Failed ({reason}), previous setting restored."
  de: "Fehlgeschlagen ({reason}), vorherige Einstellung wiederhergestellt."
web.radios.result_failed_unhealthy:
  en: "Failed ({reason}), and the previous setting did not come back up either. See radios-log.txt in the updater's data volume."
  de: "Fehlgeschlagen ({reason}), und auch die vorherige Einstellung kam nicht wieder hoch. Siehe radios-log.txt im Datenvolume des Updaters."
web.radios.sidecar_missing:
  en: "Changing radios needs the updater service, which is not running."
  de: "Zum Ändern der Funkgeräte braucht es den Updater-Dienst, und der läuft nicht."
web.radios.sidecar_refresh:
  en: "Changing radios needs a newer updater service. Run once on the host: cd {path} && docker compose pull loxmatter-updater && docker compose up -d --no-deps loxmatter-updater"
  de: "Zum Ändern der Funkgeräte braucht es einen neueren Updater-Dienst. Einmal auf dem Host ausführen: cd {path} && docker compose pull loxmatter-updater && docker compose up -d --no-deps loxmatter-updater"
web.radios.sidecar_refresh_unknown_path:
  en: "Changing radios needs a newer updater service. From your loxmatter checkout's deploy/testhost directory, run once: docker compose pull loxmatter-updater && docker compose up -d --no-deps loxmatter-updater"
  de: "Zum Ändern der Funkgeräte braucht es einen neueren Updater-Dienst. Im Verzeichnis deploy/testhost deines loxmatter-Checkouts einmal ausführen: docker compose pull loxmatter-updater && docker compose up -d --no-deps loxmatter-updater"
web.radios.reason.request_malformed:
  en: "the request was malformed"
  de: "der Auftrag war fehlerhaft"
web.radios.reason.host_dev_not_mounted:
  en: "the updater has no access to /dev"
  de: "der Updater hat keinen Zugriff auf /dev"
web.radios.reason.stack_host_path_unknown:
  en: "the updater could not find its stack directory on the host"
  de: "der Updater fand sein Stack-Verzeichnis auf dem Host nicht"
web.radios.reason.thread_device_required:
  en: "no USB stick was chosen"
  de: "kein USB-Stick gewählt"
web.radios.reason.thread_device_not_found:
  en: "the USB stick is not attached"
  de: "der USB-Stick steckt nicht"
web.radios.reason.thread_device_not_a_serial_port:
  en: "the device is not a USB serial port"
  de: "das Gerät ist kein serieller USB-Anschluss"
web.radios.reason.bluetooth_adapter_not_found:
  en: "the Bluetooth adapter does not exist"
  de: "den Bluetooth-Adapter gibt es nicht"
web.radios.reason.backbone_interface_missing:
  en: "BACKBONE_IF is not set in .env"
  de: "BACKBONE_IF ist in der .env nicht gesetzt"
web.radios.reason.env_file_missing:
  en: "the .env file is missing"
  de: "die .env-Datei fehlt"
web.radios.reason.env_backup_failed:
  en: "the .env file could not be backed up"
  de: "die .env-Datei ließ sich nicht sichern"
web.radios.reason.apply_bluetooth_failed:
  en: "matter-server could not be restarted"
  de: "matter-server ließ sich nicht neu starten"
web.radios.reason.verify_bluetooth_failed:
  en: "matter-server did not answer within 60 s"
  de: "matter-server antwortete nicht innerhalb von 60 s"
web.radios.reason.apply_thread_failed:
  en: "the Thread border router could not be restarted"
  de: "der Thread-Border-Router ließ sich nicht neu starten"
web.radios.reason.verify_thread_failed:
  en: "no Thread network within 90 s"
  de: "kein Thread-Netz innerhalb von 90 s"
web.radios.reason.unknown:
  en: "an unknown reason"
  de: "ein unbekannter Grund"
```

If a test in the suite rejects dotted `web.*` keys with three segments after `web.` or placeholders in `web.*` values, report it (existing keys such as `web.system.updater_behind` already use placeholders).

- [ ] **Step 2: Write the failing web tests.** Append to `tests/api/test_web.py`:

```python
RADIOS_READY = {
    "sidecar": "ready",
    "sidecar_stack_host_path": "/home/pi/stack",
    "serial": [
        {"path": "/dev/serial/by-id/usb-A", "tty": "ttyUSB0", "manufacturer": "SONOFF",
         "product": "SONOFF Dongle Plus MG24", "serial": "e26a50c9", "vid_pid": "10c4:ea60"},
        {"path": "/dev/serial/by-id/usb-B", "tty": "ttyACM0", "manufacturer": None,
         "product": None, "serial": None, "vid_pid": None},
    ],
    "bluetooth": [{"index": 0, "name": "hci0", "bus": "uart", "product": None, "rfkill_blocked": False}],
    "current": {"thread_enabled": True, "thread_device": "/dev/serial/by-id/usb-A",
                "thread_device_present": True, "bluetooth_adapter": 0, "otbr_running": True},
    "job": None,
}


def _radios_values(setup: str) -> dict:
    return _app_state(
        f"state.radios = {json.dumps(RADIOS_READY)};\n"
        "state.radiosDraft = { threadDevice: '/dev/serial/by-id/usb-A', bluetoothAdapter: 0 };\n"
        + setup
    )


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_radios_card_detects_a_change_and_picks_the_confirmation_text():
    """Runs the real helpers in node. Fault to prove it: return
    `confirm_thread_on` for a stick switch."""
    values = _radios_values(
        """
        const out = { unchanged: state.radiosChanged() };
        state.radiosDraft.threadDevice = '/dev/serial/by-id/usb-B';
        out.switch = [state.radiosChanged(), state.radiosConfirmKeys()];
        state.radiosDraft.threadDevice = '';
        out.off = state.radiosConfirmKeys();
        state.radiosDraft = { threadDevice: '/dev/serial/by-id/usb-A', bluetoothAdapter: 1 };
        out.bluetooth = state.radiosConfirmKeys();
        state.radios.current.thread_enabled = false;
        state.radios.current.thread_device = null;
        out.on = state.radiosConfirmKeys();
        console.log(JSON.stringify(out));
        """
    )
    assert values["unchanged"] is False
    assert values["switch"] == [True, ["web.radios.confirm_thread_switch"]]
    assert values["off"] == ["web.radios.confirm_thread_off"]
    assert values["bluetooth"] == ["web.radios.confirm_bluetooth"]
    assert values["on"] == ["web.radios.confirm_thread_on", "web.radios.confirm_bluetooth"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_thread_options_mark_the_current_stick_and_a_missing_one():
    values = _radios_values(
        """
        const out = { normal: state.radiosThreadOptions() };
        state.radios.current.thread_device = '/dev/ttyUSB7';
        state.radios.current.thread_device_present = false;
        out.missing = state.radiosThreadOptions();
        console.log(JSON.stringify(out));
        """
    )
    normal = values["normal"]
    assert [o["value"] for o in normal] == ["", "/dev/serial/by-id/usb-A", "/dev/serial/by-id/usb-B"]
    assert [o["inUse"] for o in normal] == [False, True, False]
    missing = values["missing"]
    assert missing[-1] == {"value": "/dev/ttyUSB7", "label": "web.radios.missing", "inUse": True, "missing": True}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_radios_job_states_map_to_step_classes_and_results():
    values = _radios_values(
        """
        state.radios.job = { id: 'j', phase: 'verify_thread', steps: ['validate','backup','write','apply_thread','verify_thread'],
                             error: null, rolled_back: false, healthy: null };
        const out = { running: state.radiosJobRunning(),
                      classes: state.radios.job.steps.map((s) => state.radiosStepClass(s)) };
        state.radios.job.phase = 'failed'; state.radios.job.error = 'verify_thread_failed';
        state.radios.job.rolled_back = true; state.radios.job.healthy = true;
        out.failed = [state.radiosJobRunning(), state.radiosResultKey()];
        state.radios.job.healthy = false;
        out.unhealthy = state.radiosResultKey();
        console.log(JSON.stringify(out));
        """
    )
    assert values["running"] is True
    assert values["classes"] == [
        {"done": True, "now": False}, {"done": True, "now": False}, {"done": True, "now": False},
        {"done": True, "now": False}, {"done": False, "now": True},
    ]
    assert values["failed"] == [False, "web.radios.result_failed_restored"]
    assert values["unhealthy"] == "web.radios.result_failed_unhealthy"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_sidecar_message_depends_on_the_sidecar_state():
    values = _radios_values(
        """
        const out = {};
        for (const s of ['ready', 'missing', 'outdated', 'unmounted']) {
          state.radios.sidecar = s; out[s] = state.radiosSidecarMessage();
        }
        state.radios.sidecar = 'outdated'; state.radios.sidecar_stack_host_path = null;
        out.nopath = state.radiosSidecarMessage();
        console.log(JSON.stringify(out));
        """
    )
    assert values["ready"] is None
    assert values["missing"] == "web.radios.sidecar_missing"
    assert values["outdated"] == values["unmounted"] == "web.radios.sidecar_refresh"
    assert values["nopath"] == "web.radios.sidecar_refresh_unknown_path"


async def test_the_radios_card_sits_in_the_settings_view(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    settings = page[page.index("view === 'settings'"):]
    card = settings[: settings.index("t('web.settings.language_heading')")]
    for marker in ("t('web.radios.heading')", "radiosThreadOptions()", "radiosBluetoothOptions()",
                   "askApplyRadios()", "confirmApplyRadios()", "radiosStepClass("):
        assert marker in card, marker


def test_the_radios_texts_exist_in_both_languages():
    """Reads the table directly: `i18n.raw_template` falls back to English
    when `de` is missing, so it could never see a missing translation.
    Fault to prove it: delete one `de:` line under `web.radios.*`."""
    from loxmatter import i18n

    keys = i18n.strings_with_prefix("web.radios.")
    assert "web.radios.confirm_thread_switch" in keys
    for key in keys:
        entry = i18n._STRINGS[key]
        assert entry.get("en") and entry.get("de"), key
```

(`json` is already imported in `test_web.py`; if not, add it.)

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/api/test_web.py -q -k radios`
Expected: FAIL — `state.radiosChanged is not a function`, markers missing.

- [ ] **Step 4: Implement `app.js`.** In `app()`'s state, next to `bridgeSettings`:

```javascript
    // The radios card (design "Radios in the Web UI", 2026-09-11, section
    // 8). `radios` is the last GET /api/radios body; `radiosDraft` what the
    // selects show. `radiosDirty` keeps a poll from overwriting a choice the
    // user has made but not applied yet.
    radios: null,
    radiosDraft: { threadDevice: "", bluetoothAdapter: 0 },
    radiosDirty: false,
    radiosConfirming: false,
    radiosError: null,
    radiosBusy: false,
    radiosTimer: null,
    // The id POST /api/radios returned, until GET shows that job - the
    // sidecar picks a request up within about 2 s, so the first poll after
    // POST can still show the PREVIOUS job's terminal phase.
    radiosPendingJobId: null,
```

Methods, next to `loadSettings()`:

```javascript
    async loadRadios() {
      this.radiosError = null;
      try {
        this.radios = await this.request("GET", "/api/radios");
      } catch (error) {
        this.radiosError = t("web.settings.load_error", { message: error.message });
        return;
      }
      const current = this.radios.current;
      if (current && !this.radiosDirty) {
        this.radiosDraft = {
          threadDevice: current.thread_enabled ? current.thread_device ?? "" : "",
          bluetoothAdapter: current.bluetooth_adapter,
        };
      }
      if (this.radiosPendingJobId !== null && this.radios.job?.id === this.radiosPendingJobId) {
        this.radiosPendingJobId = null;
      }
      const keepPolling = this.radiosPendingJobId !== null || this.radiosJobRunning();
      if (keepPolling && this.radiosTimer === null) {
        this.radiosTimer = setInterval(() => this.loadRadios(), 2000);
      } else if (!keepPolling && this.radiosTimer !== null) {
        clearInterval(this.radiosTimer);
        this.radiosTimer = null;
      }
    },

    radiosCurrentThread() {
      const current = this.radios?.current;
      return current && current.thread_enabled ? current.thread_device ?? "" : "";
    },

    radiosThreadOptions() {
      const current = this.radios?.current;
      const inUse = this.radiosCurrentThread();
      const options = [{ value: "", label: t("web.radios.no_thread_stick"), inUse: inUse === "", missing: false }];
      for (const radio of this.radios?.serial ?? []) {
        const name = radio.product || radio.manufacturer || radio.tty;
        const suffix = radio.serial ? ` · …${radio.serial.slice(-4)}` : "";
        options.push({ value: radio.path, label: name + suffix, inUse: radio.path === inUse, missing: false });
      }
      if (current && current.thread_enabled && current.thread_device && !current.thread_device_present) {
        options.push({
          value: current.thread_device,
          label: t("web.radios.missing", { path: current.thread_device }),
          inUse: true,
          missing: true,
        });
      }
      return options;
    },

    radiosBluetoothOptions() {
      const inUse = this.radios?.current?.bluetooth_adapter;
      return (this.radios?.bluetooth ?? []).map((adapter) => ({
        value: adapter.index,
        label:
          adapter.bus === "uart"
            ? t("web.radios.bus_uart", { name: adapter.name })
            : adapter.bus === "usb"
              ? t("web.radios.bus_usb", { name: adapter.name, product: adapter.product ?? "" })
              : t("web.radios.bus_other", { name: adapter.name }),
        inUse: adapter.index === inUse,
        blocked: adapter.rfkill_blocked,
      }));
    },

    radiosChanged() {
      const current = this.radios?.current;
      if (!current) return false;
      return (
        this.radiosDraft.threadDevice !== this.radiosCurrentThread() ||
        Number(this.radiosDraft.bluetoothAdapter) !== current.bluetooth_adapter
      );
    },

    /** Which confirmation paragraphs apply, in display order. */
    radiosConfirmKeys() {
      const current = this.radios?.current;
      if (!current) return [];
      const keys = [];
      const before = this.radiosCurrentThread();
      const after = this.radiosDraft.threadDevice;
      if (before !== after) {
        if (after === "") keys.push("web.radios.confirm_thread_off");
        else if (before === "") keys.push("web.radios.confirm_thread_on");
        else keys.push("web.radios.confirm_thread_switch");
      }
      if (Number(this.radiosDraft.bluetoothAdapter) !== current.bluetooth_adapter) {
        keys.push("web.radios.confirm_bluetooth");
      }
      return keys;
    },

    radiosJobRunning() {
      const phase = this.radios?.job?.phase;
      return Boolean(phase) && !["idle", "done", "failed", "rejected", "unchanged"].includes(phase);
    },

    radiosStepClass(step) {
      const job = this.radios?.job;
      if (!job) return { done: false, now: false };
      const steps = job.steps ?? [];
      const at = steps.indexOf(job.phase);
      const index = steps.indexOf(step);
      if (!this.radiosJobRunning()) return { done: job.phase === "done", now: false };
      return { done: index < at, now: index === at };
    },

    radiosResultKey() {
      const job = this.radios?.job;
      if (!job || this.radiosJobRunning()) return null;
      if (job.phase === "done") return "web.radios.result_done";
      if (job.phase === "unchanged") return "web.radios.result_unchanged";
      if (job.phase === "rejected") return "web.radios.result_rejected";
      if (job.phase === "failed") {
        return job.healthy === false ? "web.radios.result_failed_unhealthy" : "web.radios.result_failed_restored";
      }
      return null;
    },

    radiosReason() {
      const key = `web.radios.reason.${this.radios?.job?.error ?? "unknown"}`;
      const text = t(key);
      return text === key ? t("web.radios.reason.unknown") : text;
    },

    radiosSidecarMessage() {
      const status = this.radios?.sidecar;
      if (!status || status === "ready") return null;
      if (status === "missing") return t("web.radios.sidecar_missing");
      const path = this.radios.sidecar_stack_host_path;
      return path
        ? t("web.radios.sidecar_refresh", { path })
        : t("web.radios.sidecar_refresh_unknown_path");
    },

    askApplyRadios() {
      if (!this.radiosChanged()) return;
      this.radiosConfirming = true;
    },

    cancelApplyRadios() {
      this.radiosConfirming = false;
    },

    async confirmApplyRadios() {
      this.radiosConfirming = false;
      this.radiosBusy = true;
      this.radiosError = null;
      try {
        const response = await this.request("POST", "/api/radios", {
          thread: {
            enabled: this.radiosDraft.threadDevice !== "",
            device: this.radiosDraft.threadDevice || null,
          },
          bluetooth: { adapter: Number(this.radiosDraft.bluetoothAdapter) },
        });
        this.radiosPendingJobId = response.id;
        this.radiosDirty = false;
      } catch (error) {
        this.radiosError = error.message;
      } finally {
        this.radiosBusy = false;
      }
      await this.loadRadios();
    },
```

Add one more node test for the polling guard to the Step 2 block (it runs `loadRadios` with a stubbed `request` and `setInterval`):

```python
@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_polling_continues_until_the_posted_job_appears():
    """Fault to prove it: drop the `radiosPendingJobId` condition from
    `keepPolling` - the interval then stops on the old job's result."""
    values = _app_state(
        f"""
        let intervals = 0;
        globalThis.setInterval = () => {{ intervals += 1; return 1; }};
        globalThis.clearInterval = () => {{ intervals -= 1; }};
        const old = {json.dumps({**RADIOS_READY, "job": {"id": "old", "phase": "done", "steps": [], "error": None, "rolled_back": False, "healthy": True}})};
        const fresh = JSON.parse(JSON.stringify(old)); fresh.job.id = "new";
        let answer = old;
        state.request = async () => answer;
        (async () => {{
          state.radiosPendingJobId = "new";
          await state.loadRadios();
          const afterOld = intervals;
          answer = fresh;
          await state.loadRadios();
          console.log(JSON.stringify({{ afterOld, afterNew: intervals, pending: state.radiosPendingJobId }}));
        }})();
        """
    )
    assert values == {"afterOld": 1, "afterNew": 0, "pending": None}
```

If `app.js` captures `setInterval` at load time rather than at call time and the stub does not take effect, report it instead of changing the helper under test.

In `loadView` (the `view === "settings"` branch at the current `await this.loadSettings();`), add `await this.loadRadios();`.

- [ ] **Step 5: Implement the markup** in `index.html`, as a new `<div class="card">` directly after the connection card (before the language card):

```html
        <div class="card">
          <!-- Radios (design "Radios in the Web UI", 2026-09-11, section
               8). Detection is always shown; changing needs a ready
               sidecar, otherwise the message explains what to run. -->
          <h2 x-text="t('web.radios.heading')"></h2>
          <p class="hint" x-text="t('web.radios.explanation')"></p>
          <p class="hint" x-show="radiosSidecarMessage()" x-cloak x-text="radiosSidecarMessage()"></p>
          <template x-if="radios">
            <div>
              <div class="row">
                <label>
                  <span x-text="t('web.radios.thread_label')"></span>
                  <select
                    x-model="radiosDraft.threadDevice"
                    @change="radiosDirty = true"
                    :disabled="radios.sidecar !== 'ready' || radiosJobRunning() || radiosBusy"
                  >
                    <template x-for="option in radiosThreadOptions()" :key="option.value">
                      <option
                        :value="option.value"
                        x-text="option.label + (option.inUse ? ' · ' + t('web.radios.in_use') : '')"
                      ></option>
                    </template>
                  </select>
                </label>
                <label>
                  <span x-text="t('web.radios.bluetooth_label')"></span>
                  <select
                    x-model.number="radiosDraft.bluetoothAdapter"
                    @change="radiosDirty = true"
                    :disabled="radios.sidecar !== 'ready' || radiosJobRunning() || radiosBusy"
                  >
                    <template x-for="option in radiosBluetoothOptions()" :key="option.value">
                      <option
                        :value="option.value"
                        x-text="option.label + (option.inUse ? ' · ' + t('web.radios.in_use') : '')"
                      ></option>
                    </template>
                  </select>
                </label>
              </div>
              <template x-for="option in radiosBluetoothOptions().filter((o) => o.blocked)" :key="'blocked-' + option.value">
                <p class="hint warn" x-text="option.label + ': ' + t('web.radios.rfkill_blocked')"></p>
              </template>
              <div class="row">
                <button @click="loadRadios()" x-text="t('web.radios.rescan')"></button>
                <button
                  class="primary"
                  x-show="radios.sidecar === 'ready' && radiosChanged() && !radiosJobRunning()"
                  x-cloak
                  @click="askApplyRadios()"
                  :disabled="radiosBusy"
                  x-text="t('web.radios.apply')"
                ></button>
              </div>
              <div class="dialog-inline" x-show="radiosConfirming" x-cloak>
                <h3 x-text="t('web.radios.confirm_title')"></h3>
                <template x-for="key in radiosConfirmKeys()" :key="key">
                  <p x-text="t(key)"></p>
                </template>
                <p class="hint" x-text="t('web.radios.confirm_rollback')"></p>
                <div class="row">
                  <button class="primary" @click="confirmApplyRadios()" x-text="t('web.radios.confirm_apply')"></button>
                  <button @click="cancelApplyRadios()" x-text="t('web.radios.cancel')"></button>
                </div>
              </div>
              <template x-if="radios.job && (radiosJobRunning() || radiosResultKey())">
                <div>
                  <ul class="steps" x-show="radiosJobRunning()">
                    <template x-for="step in radios.job.steps" :key="step">
                      <li :class="radiosStepClass(step)" x-text="t('web.radios.step_' + step)"></li>
                    </template>
                  </ul>
                  <p
                    x-show="radiosResultKey()"
                    x-cloak
                    :class="radios.job.phase === 'done' || radios.job.phase === 'unchanged' ? 'hint' : 'banner danger'"
                    x-text="radiosResultKey() && t(radiosResultKey(), { reason: radiosReason() })"
                  ></p>
                </div>
              </template>
              <p x-show="radiosError" x-cloak class="banner danger" x-text="radiosError"></p>
            </div>
          </template>
        </div>
```

If `.dialog-inline` has no CSS rule yet, add to `style.css`:

```css
/* An inline confirmation inside a card (radios card, design 2026-09-11). */
.dialog-inline {
  margin-top: 0.75rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
}
```

The `<select>`s use `x-model`, not `:value`, because their `<option>`s come from `x-for` (see the memory note in the project on Alpine's directive order: `:value` on such a select never applies).

- [ ] **Step 6: Run the tests** — `uv run pytest tests/api/test_web.py -q -k "radios"` then the whole `tests/api/test_web.py` — Expected: PASS.

- [ ] **Step 7: Prove the protection** (return `confirm_thread_on` for a switch). Paste outputs.

- [ ] **Step 8: Look at it.** The unit tests cannot see layout or Alpine behaviour. Write `$SCRATCHPAD/radios_card_check.py` (session scratchpad, not the repo) using Playwright against `uv run python scripts/dev_web_server.py --demo` (port 8420, password `loxmatter-demo`, login via `page.fill` + `page.click('button:has-text("Log in")')`). Use `page.route("**/api/radios", ...)` to answer GET with four bodies in turn — `RADIOS_READY` from Step 2 (normal), the same with the Thread select changed and the Apply button clicked (confirmation visible), the same with a running `job` (`phase: "verify_thread"`), and `sidecar: "outdated"` — open `#/settings` for each, take a screenshot of the card per state into the scratchpad, and read back from the DOM: the selected option of each select, whether "Apply" is visible, the confirmation paragraphs, the step list classes, and the sidecar message. Read every screenshot. Report the DOM values and your description of each screenshot; do not commit the script or screenshots.

- [ ] **Step 9: Checks and commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): choose the Thread stick and Bluetooth adapter in the settings

A radios card lists the detected sticks and adapters with the current ones
marked, asks for confirmation with a text that names what will be
interrupted, shows the sidecar's steps and result, and explains what to run
when the updater service cannot apply changes yet."
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md` (Updating section), `docs/superpowers/specs/2026-09-08-webui-updates-design.md` (section 10), `docs/superpowers/specs/2026-09-09-first-run-checklist.md`, `deploy/testhost/docker-compose.yml` (comment on `loxmatter-updater`), `CHANGELOG.md`

- [ ] **Step 1: README.** In `## Updating`, the paragraph that says what the sidecar can do ("What it can do is install a published loxmatter version, and nothing else."): replace that sentence with:

```markdown
What it can do is install a published loxmatter version, and change which
existing USB stick the Thread border router uses (or switch Thread off),
and which existing Bluetooth adapter matter-server uses — nothing else. It
checks each of those against the host's devices itself, touches no other
setting and no other service, and never runs text from a request as a
command.
```

- [ ] **Step 2: Update design spec, section 10.** After the paragraph starting "Together: even whoever takes over the bridge completely…", add:

```markdown
**Addendum, 11 September 2026 — a fourth rule.** The design "Radios in the
Web UI" (`2026-09-11-radios-in-the-web-ui-design.md`, section 6.6)
deliberately extends the statement above: through the same files, the
bridge can additionally change only which existing USB serial device `otbr`
uses, whether Thread runs, and which existing Bluetooth adapter
`matter-server` uses. The sidecar validates each value against the host's
devices, touches no other key and no other service, and executes no value
from a request as a command.
```

- [ ] **Step 3: Compose comment.** In `deploy/testhost/docker-compose.yml`, directly above the line `  loxmatter-updater:`, add:

```yaml
  # Since design "Radios in the Web UI" (2026-09-11) this service also runs
  # radios-once.sh: besides installing a published loxmatter version, the
  # bridge can through it change only which existing USB serial device
  # otbr uses, whether Thread runs, and which existing Bluetooth adapter
  # matter-server uses. The script validates each value against the host's
  # devices (read through the /dev:/host/dev:ro mount below), touches no
  # other .env key and no other service, and executes no value from a
  # request as a command.
```

- [ ] **Step 4: First-run checklist.** Append a section to `docs/superpowers/specs/2026-09-09-first-run-checklist.md`:

```markdown
## 14. Radios (design "Radios in the Web UI", 2026-09-11)

0. Copy the stack's `.env` aside by hand before the first radio job.
1. After installing a build with this feature, refresh the sidecar from the
   console once. Before the refresh the Radios card is read-only and says
   what to run; afterwards it shows the selects.
2. The card lists the Thread stick and the Bluetooth adapter, both "in use".
3. Send an invalid Bluetooth index through the API
   (`POST /api/radios` with `{"thread": {…current…}, "bluetooth": {"adapter": 9}}`):
   400 from the bridge. Then write the same body as `radios-request.json`
   directly into the update directory: the sidecar rejects it with
   `bluetooth_adapter_not_found`, and no container restarts (compare
   `docker ps --format '{{.Names}} {{.RunningFor}}'` before and after).
4. **Interrupts Thread devices — agree a time first.** Apply the same stick
   by its by-id path (the installer wrote `/dev/ttyUSB0`). `otbr` is
   recreated, `docker exec otbr ot-ctl state` reports `leader`, and the
   Thread devices deliver values again within two minutes, read through the
   running bridge.
5. **Interrupts Thread devices.** Switch Thread off, then on again: `otbr`
   disappears and returns, the devices with it.
6. Apply without a change: "Nothing to change", no restart. A real
   Bluetooth adapter switch cannot be exercised on a host with one adapter;
   record that instead of counting it as passed.
```

- [ ] **Step 5: CHANGELOG.** Under `## [Unreleased]` → `### Added`:

```markdown
- **Choose the Thread stick and Bluetooth adapter in the settings.** The new
  Radios card lists the USB sticks and Bluetooth adapters the host has,
  shows which ones are in use, and lets you switch the Thread stick, turn
  Thread off or on, or pick another Bluetooth adapter — no more editing
  `.env` on the host. The updater service applies the change, checks that
  Thread or matter-server really come back, and restores the previous
  setting if they don't. The first time, the updater service itself needs
  one refresh from the console; the card shows the command.
```

- [ ] **Step 6: Checks and commit**

Run: `uv run python scripts/check_language.py` and `uv run pytest tests/test_compose_profiles.py -q`.

```bash
git add README.md docs/superpowers/specs/2026-09-08-webui-updates-design.md docs/superpowers/specs/2026-09-09-first-run-checklist.md deploy/testhost/docker-compose.yml CHANGELOG.md
git commit -m "docs: record the sidecar's radios rule and the radios checklist

The updater's security statement gains its fourth rule in the README, the
update design and the Compose comment; the first-run checklist gets the
hardware steps, with the two that interrupt Thread devices marked."
```

---

### Task 9: Whole-Branch Verification

**Files:** none unless a check fails.

- [ ] **Step 1: Leftovers.**

```bash
cd /Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003
grep -rn "TRANSITIONAL" src tests scripts deploy
git diff --stat origin/main -- deploy/updater/update-once.sh
sh -n deploy/updater/radios-once.sh && sh -n deploy/updater/entrypoint.sh
```

Expected: no `TRANSITIONAL`; no diff for `update-once.sh`; both syntax checks silent.

- [ ] **Step 2: Busybox reality check.** Run the radios script tests once inside Alpine to catch GNU-isms the macOS/Linux dev tools hide:

```bash
docker run --rm -v "$PWD":/w -w /w alpine:3.20 sh -c '
  apk add --no-cache jq python3 py3-pytest curl >/dev/null &&
  python3 -m pytest -q tests/test_updater_radios_script.py -p no:cacheprovider --noconftest' 2>&1 | tail -15
```

`--noconftest` skips `tests/conftest.py`, which imports `loxmatter`; the radios script tests import nothing from the project. If Docker is unavailable locally, say so and report the step as not run.

- [ ] **Step 3: Full checks** (foreground): `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py`.

- [ ] **Step 4: Report** the outputs. No commit unless a fix was needed (its own commit with its own reason).

---

### Task 10: Hardware Checklist on the Test Pi (human-gated, after merge)

**Not executed by an implementer subagent.** The controller runs this only after the branch is merged, the maintainer has installed it on the test Pi, and the maintainer has agreed a time for steps 4 and 5, which interrupt their Thread devices.

- [ ] Execute section 14 of `docs/superpowers/specs/2026-09-09-first-run-checklist.md` step by step, recording commands, outputs and container start times. Read values only through the running bridge, never through `snapshots()`.
- [ ] The sidecar image for a build that has not been released is not on `ghcr.io`: build it on the Pi from the checked-out repository (`docker build -t ghcr.io/lucienkerl/loxmatter-updater:stable deploy/updater`) before the refresh command, and after the checklist restore the published image (`docker compose pull loxmatter-updater && docker compose up -d --no-deps loxmatter-updater`) unless a release with this feature exists by then.
- [ ] Report which steps passed, which could not be exercised (a real Bluetooth adapter switch), and anything that behaved differently from the design.
