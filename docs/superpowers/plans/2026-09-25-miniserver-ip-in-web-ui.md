# The Miniserver's Address in the Web Interface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Miniserver's IP is set, checked and applied live from Settings → Miniserver connection, and the bridge sends to the IP and UDP port saved there instead of to its start arguments.

**Architecture:** The IP becomes one more key in the `setting` table behind `BridgeSettingsStore`. `UdpSender` gains an optional target and `set_target`; `PATCH /api/settings` retargets it, starts a background full resend, and probes `/jdev/cfg/api`. `cli.run` seeds the store from `--miniserver`/`--port` once and then builds the sender from the store.

**Tech Stack:** Python 3.12, FastAPI, pydantic, aiohttp (probe), sqlite, Alpine.js (vendored), POSIX sh (installer), pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-09-25-miniserver-ip-in-web-ui-design.md`

## Global Constraints

- Everything is written in English — code, comments, docstrings, test names, commit messages. German appears only as `de:` values in `src/loxmatter/i18n/strings.yaml` and as quoted data in tests (see `CLAUDE.md`).
- Every user-visible string (web UI, `HTTPException` detail, CLI log line) goes through `i18n.t(...)` with an `en` and a `de` value. German uses the impersonal form or "Sie", never "du", like the neighbouring settings strings.
- Commit messages: Conventional Commits, English, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Store changes stay additive: new `setting` keys only, no schema change, no column removed.
- No new dependency. The probe uses `aiohttp`, which is already a runtime dependency.
- `docker-compose.yml` keeps passing `--miniserver ${MINISERVER_IP}`. Do not remove it: it seeds existing installations and keeps an updater rollback to an older version working.
- Tests are never started in the background. No `run_in_background`, no `Monitor`, no "waiting for a notification" — every command runs in the foreground and you return only when all have finished. The full suite exceeds the 10-minute Bash limit; for a task, run the files named in the task. The final task runs the suite in its four measured parts.
- `tests/api` and `tests/projectsync` must not be passed to one pytest call (conftest name collision); run them separately.
- The checks CI runs: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/loxmatter/model/settings_store.py` | modify | `miniserver_ip` key, `save(..., miniserver_ip)`, `seed_miniserver` |
| `src/loxmatter/loxone/sender.py` | modify | optional target, `set_target` |
| `src/loxmatter/api/diagnostics.py` | modify | "no Miniserver address" check |
| `src/loxmatter/loxone/probe.py` | create | `probe_miniserver` against `/jdev/cfg/api` |
| `src/loxmatter/api/models.py` | modify | `miniserver_ip`, `MiniserverCheckOut` |
| `src/loxmatter/api/settings.py` | modify | validation, retarget, background resend, probe |
| `src/loxmatter/loxone/server.py` | modify | pass `sender`/`runtime` to the settings router |
| `src/loxmatter/cli.py` | modify | optional `--miniserver`, seeding, sender from store |
| `src/loxmatter/api/project_sync.py` | modify | stored IP resolves a multi-Miniserver file |
| `src/loxmatter/web/index.html`, `app.js` | modify | new field, probe banner, export read-only field |
| `src/loxmatter/i18n/strings.yaml` | modify | every new string, rewritten explanation |
| `install.sh` | modify | no Miniserver question, no probe |
| `deploy/testhost/.env.example`, `deploy/testhost/README.md`, `README.md`, `CHANGELOG.md`, `scripts/dev_web_server.py` | modify | documentation and demo data |

---

### Task 1: Store the Miniserver's IP

**Files:**
- Modify: `src/loxmatter/model/settings_store.py`
- Modify: `scripts/dev_web_server.py:357`
- Test: `tests/model/test_settings_store.py`

**Interfaces:**
- Produces:
  - `BridgeSettings.miniserver_ip: str | None` — a new field between `listen_port` and `saved_at`. Only `settings_store.py` constructs `BridgeSettings`, and it uses keywords.
  - `BridgeSettingsStore.save(*, bridge_ip: str, udp_port: int, listen_port: int, miniserver_ip: str | None) -> BridgeSettings` — `miniserver_ip` is required; `None` deletes the key.
  - `BridgeSettingsStore.seed_miniserver(ip: str, udp_port: int) -> bool` — `True` when the IP was written.

- [ ] **Step 1: Write the failing tests**

Append to `tests/model/test_settings_store.py`, and change the three existing `store.settings.save(...)` calls in that file to pass `miniserver_ip=None` as a fourth keyword:

```python
def test_a_fresh_store_has_no_miniserver_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.settings.get().miniserver_ip is None
    finally:
        store.close()


def test_save_persists_the_miniserver_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        saved = store.settings.save(
            bridge_ip="192.168.1.20", udp_port=7000, listen_port=8080, miniserver_ip="192.168.1.77"
        )
        assert saved.miniserver_ip == "192.168.1.77"
        assert store.settings.get().miniserver_ip == "192.168.1.77"
    finally:
        store.close()


def test_saving_none_removes_a_stored_miniserver_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(
            bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080, miniserver_ip="10.0.0.9"
        )
        store.settings.save(bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080, miniserver_ip=None)
        assert store.settings.get().miniserver_ip is None
    finally:
        store.close()


def test_seed_writes_ip_and_port_into_an_empty_store_and_leaves_saved_at_alone(tmp_path):
    """Seeding is not a save in the interface: `saved_at` must keep saying
    "not saved yet"."""
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.settings.seed_miniserver("10.0.1.99", 7005) is True
        settings = store.settings.get()
        assert settings.miniserver_ip == "10.0.1.99"
        assert settings.udp_port == 7005
        assert settings.saved_at is None
    finally:
        store.close()


def test_seed_does_not_overwrite_a_stored_ip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(
            bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080, miniserver_ip="10.0.0.9"
        )
        assert store.settings.seed_miniserver("10.0.1.99", 7000) is False
        assert store.settings.get().miniserver_ip == "10.0.0.9"
    finally:
        store.close()


def test_seed_keeps_a_udp_port_saved_in_the_interface(tmp_path):
    """Spec section 3: a port saved in the card is what the Loxone project's
    templates say. `--port` must not overwrite it, or the bridge keeps
    sending past the virtual input."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(bridge_ip="10.0.0.1", udp_port=7001, listen_port=8080, miniserver_ip=None)
        assert store.settings.seed_miniserver("10.0.1.99", 7000) is True
        settings = store.settings.get()
        assert settings.miniserver_ip == "10.0.1.99"
        assert settings.udp_port == 7001
    finally:
        store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/model/test_settings_store.py`
Expected: FAIL — `AttributeError: 'BridgeSettings' object has no attribute 'miniserver_ip'` and `TypeError` on the unexpected `miniserver_ip` keyword.

- [ ] **Step 3: Implement**

In `src/loxmatter/model/settings_store.py`:

Add the key next to the others and to `_ALL_KEYS`:

```python
_BRIDGE_IP_KEY = "bridge_ip"
_BRIDGE_UDP_PORT_KEY = "bridge_udp_port"
_BRIDGE_LISTEN_PORT_KEY = "bridge_listen_port"
_BRIDGE_SETTINGS_SAVED_AT_KEY = "bridge_settings_saved_at"
# Since 2026-09-25 (design "The Miniserver's address is set in the web
# interface"): before that, the address only reached the bridge as
# `--miniserver`.
_MINISERVER_IP_KEY = "miniserver_ip"

_ALL_KEYS = (
    _BRIDGE_IP_KEY,
    _BRIDGE_UDP_PORT_KEY,
    _BRIDGE_LISTEN_PORT_KEY,
    _BRIDGE_SETTINGS_SAVED_AT_KEY,
    _MINISERVER_IP_KEY,
)
```

Replace the dataclass:

```python
@dataclass(frozen=True)
class BridgeSettings:
    """`bridge_ip`/`miniserver_ip`/`saved_at` are `None` as long as nobody has
    saved (or, for `miniserver_ip`, seeded) anything - the ports fall back in
    that case to the default values that were set when the store was
    created."""

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    miniserver_ip: str | None
    saved_at: str | None
```

In `get()`, add `miniserver_ip=values.get(_MINISERVER_IP_KEY),` before `saved_at=...`.

Replace `save` and add `seed_miniserver` below it:

```python
    def save(
        self, *, bridge_ip: str, udp_port: int, listen_port: int, miniserver_ip: str | None
    ) -> BridgeSettings:
        """Writes all four values and the timestamp in one transaction -
        no partial update: the fields belong together by domain. A
        `miniserver_ip` of `None` removes the key, so `get()` reports no
        address rather than an empty string."""
        saved_at = now_iso()
        for key, value in (
            (_BRIDGE_IP_KEY, bridge_ip),
            (_BRIDGE_UDP_PORT_KEY, str(udp_port)),
            (_BRIDGE_LISTEN_PORT_KEY, str(listen_port)),
            (_BRIDGE_SETTINGS_SAVED_AT_KEY, saved_at),
        ):
            self._upsert(key, value)
        if miniserver_ip is None:
            self._db.execute("DELETE FROM setting WHERE key = ?", (_MINISERVER_IP_KEY,))
        else:
            self._upsert(_MINISERVER_IP_KEY, miniserver_ip)
        self._db.commit()
        return self.get()

    def seed_miniserver(self, ip: str, udp_port: int) -> bool:
        """Takes `loxmatter run --miniserver`/`--port` over on an installation
        that has no Miniserver address yet. Each key is written only where it
        is missing - a UDP port saved in the interface is what the Loxone
        project's templates say, and stays. `saved_at` is not touched:
        nobody saved anything in the interface. Returns whether the IP was
        written."""
        if self.get().miniserver_ip is not None:
            return False
        for key, value in ((_MINISERVER_IP_KEY, ip), (_BRIDGE_UDP_PORT_KEY, str(udp_port))):
            self._db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        self._db.commit()
        return True

    def _upsert(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
```

Update the module docstring's first line to say "Access to the connection data of the bridge and the Miniserver - IPs and ports".

In `scripts/dev_web_server.py:357`, change the demo call to:

```python
        store.settings.save(
            bridge_ip="192.168.1.50", udp_port=7000, listen_port=8080, miniserver_ip="192.168.1.10"
        )
```

- [ ] **Step 4: Fix the one production caller so the tree type-checks**

In `src/loxmatter/api/settings.py`, the existing `save_settings` passes three keywords. Add `miniserver_ip=store.settings.get().miniserver_ip,` as the fourth, so this task leaves the stored address untouched. Task 4 replaces this route body.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/model/test_settings_store.py tests/api/test_settings_api.py`
Expected: PASS.
Run: `uv run mypy`
Expected: `Success`.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/model/settings_store.py src/loxmatter/api/settings.py scripts/dev_web_server.py tests/model/test_settings_store.py
git commit -m "feat(store): keep the Miniserver's address beside the bridge settings

The address only existed as a start argument. It becomes a setting key,
and seed_miniserver takes --miniserver over once, without overwriting a
UDP port saved in the interface.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A sender without a target, and the diagnostics line for it

**Files:**
- Modify: `src/loxmatter/loxone/sender.py:90-170`
- Modify: `src/loxmatter/api/diagnostics.py:517-535`
- Modify: `src/loxmatter/i18n/strings.yaml` (after `api.diagnostics.no_udp_sender`)
- Test: `tests/loxone/test_sender.py`, `tests/api/test_diagnostics.py`

**Interfaces:**
- Produces:
  - `UdpSender(host: str | None, port: int, *, rate_limit=..., log_size=...)`
  - `UdpSender.set_target(host: str | None, port: int) -> None`
  - `UdpSender.target -> tuple[str, int] | None`
  - `send()` returns `False` and records nothing while the target is `None`.

- [ ] **Step 1: Write the failing sender tests**

Append to `tests/loxone/test_sender.py`:

```python
async def test_without_a_target_nothing_is_sent_or_recorded(receiver):
    """A fresh installation has no Miniserver address yet (spec section 5)."""
    sender = UdpSender(None, 7000)
    assert sender.target is None
    assert await sender.send("d1_1_temp", 21.5) is False
    assert list(sender.datagram_log) == []
    await sender.close()


async def test_set_target_sends_the_next_value_to_the_new_address(receiver):
    host, port = receiver.getsockname()
    sender = UdpSender(None, 7000)
    sender.set_target(host, port)
    assert sender.target == (host, port)
    await sender.send("d1_1_temp", 21.5)
    await asyncio.sleep(0.05)
    assert received(receiver) == [b"d1_1_temp:21.5"]
    await sender.close()


async def test_a_value_suppressed_without_a_target_goes_out_once_there_is_one(receiver):
    """Recording it as sent while there was nowhere to send it would
    debounce it forever: a light that does not change would never reach the
    Miniserver."""
    host, port = receiver.getsockname()
    sender = UdpSender(None, 7000)
    await sender.send("d1_1_onoff", True)
    sender.set_target(host, port)
    assert await sender.send("d1_1_onoff", True) is True
    await asyncio.sleep(0.05)
    assert received(receiver) == [b"d1_1_onoff:1"]
    await sender.close()


async def test_set_target_none_stops_sending(receiver):
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    sender.set_target(None, port)
    assert await sender.send("d1_1_temp", 21.5) is False
    await asyncio.sleep(0.05)
    assert received(receiver) == []
    await sender.close()
```

Before running, check what `datagram("d1_1_onoff", True)` produces (`src/loxmatter/loxone/values.py`, functions `datagram` and `format_value`) and use that exact byte string in the third test if it is not `b"d1_1_onoff:1"`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/loxone/test_sender.py`
Expected: FAIL — `AttributeError: 'UdpSender' object has no attribute 'set_target'`, and `socket.gaierror`/`TypeError` for the `None` host.

- [ ] **Step 3: Implement in `sender.py`**

```python
    def __init__(
        self,
        host: str | None,
        port: int,
        *,
        rate_limit: float = RATE_LIMIT_PER_SECOND,
        log_size: int = DATAGRAM_LOG_SIZE,
    ) -> None:
        """Sets up the UDP socket. A rate_limit of 0 or below means: no rate
        limit. A `host` of `None` is an installation without a Miniserver
        address yet - see `set_target`."""
        self._target: tuple[str, int] | None = None
        self.set_target(host, port)
        # ... the remaining lines of __init__ stay as they are
```

(Delete the old `self._target = (host, port)` line.)

```python
    @property
    def target(self) -> tuple[str, int] | None:
        """Target host/port, `None` while no Miniserver address is set - for
        the diagnostics system check (`api.diagnostics._check_miniserver`)
        and the settings route, otherwise purely internal."""
        return self._target

    def set_target(self, host: str | None, port: int) -> None:
        """Points the sender at a new Miniserver address, or at none.

        The debounce cache is deliberately kept: whoever changes the target
        also calls `Runtime.resend_all`, whose `force=True` bypasses it. A
        value that was never sent is not in the cache in the first place -
        see `send`."""
        self._target = (host, port) if host else None
```

In `send()`, right after the first `if self._socket is None: raise ...` block and **before** the debounce check:

```python
        if self._target is None:
            # No Miniserver address yet. Returning before anything is
            # recorded is the point: a value marked as sent here would be
            # debounced once an address exists, and never arrive.
            return False
```

Inside the lock, replace `self._socket.sendto(packet, self._target)` with:

```python
            target = self._target
            if target is None:  # cleared while this call waited for the lock
                return False
            self._socket.sendto(packet, target)
```

- [ ] **Step 4: Run the sender tests**

Run: `uv run pytest -q tests/loxone/test_sender.py`
Expected: PASS.

- [ ] **Step 5: Write the failing diagnostics tests**

Append to `tests/api/test_diagnostics.py` (add `from loxmatter.api.diagnostics import _check_miniserver` and `from loxmatter.loxone.sender import UdpSender` to the imports if they are not there):

```python
async def test_the_miniserver_check_fails_without_an_address():
    sender = UdpSender(None, 7000)
    ok, detail = _check_miniserver(sender)
    assert not ok
    assert "Settings" in detail and "Miniserver connection" in detail
    await sender.close()


async def test_the_miniserver_check_without_an_address_in_german():
    i18n.set_language("de")
    sender = UdpSender(None, 7000)
    ok, detail = _check_miniserver(sender)
    assert not ok
    assert "Verbindung zum Miniserver" in detail
    await sender.close()
```

- [ ] **Step 6: Run to verify they fail**

Run: `uv run pytest -q tests/api/test_diagnostics.py -k "miniserver_check"`
Expected: FAIL — `TypeError: cannot unpack non-iterable NoneType object`.

- [ ] **Step 7: Implement the diagnostics line**

In `_check_miniserver`, after the `sender is None` branch:

```python
    target = sender.target
    if target is None:
        return False, i18n.t("api.diagnostics.no_miniserver_address")
    host, port = target
```

(Replace `host, port = sender.target`.)

Add to `strings.yaml`, directly after `api.diagnostics.no_udp_sender`:

```yaml
api.diagnostics.no_miniserver_address:
  en: "No Miniserver address is set, so the bridge sends no values. Enter it under Settings → Miniserver connection."
  de: "Es ist keine Miniserver-Adresse eingetragen, deshalb sendet die Brücke keine Werte. Tragen Sie sie unter Einstellungen → Verbindung zum Miniserver ein."
```

- [ ] **Step 8: Run the tests and the checks**

Run: `uv run pytest -q tests/loxone/test_sender.py tests/loxone/test_runtime.py`
Run: `uv run pytest -q tests/api/test_diagnostics.py`
Run: `uv run mypy`
Expected: all PASS / `Success`.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/loxone/sender.py src/loxmatter/api/diagnostics.py src/loxmatter/i18n/strings.yaml tests/loxone/test_sender.py tests/api/test_diagnostics.py
git commit -m "feat(sender): run without a Miniserver address and retarget live

A fresh installation will have no address until one is entered in the
interface. The sender then drops values without recording them, so they
still go out once set_target gives it somewhere to send them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Probe the Miniserver

**Files:**
- Create: `src/loxmatter/loxone/probe.py`
- Test: `tests/loxone/test_probe.py`

**Interfaces:**
- Produces:
  - `class ProbeOutcome(StrEnum)`: `FOUND = "found"`, `TIMEOUT = "timeout"`, `NO_CONNECTION = "no_connection"`, `HTTP_ERROR = "http_error"`, `NOT_A_MINISERVER = "not_a_miniserver"`
  - `@dataclass(frozen=True) class MiniserverProbe`: `outcome: ProbeOutcome`, `serial: str | None = None`, `firmware: str | None = None`, `http_status: int | None = None`; property `found -> bool`
  - `async def probe_miniserver(ip: str, *, port: int = 80, timeout: float = 3.0) -> MiniserverProbe` — never raises for network failures.

- [ ] **Step 1: Write the failing tests**

Create `tests/loxone/test_probe.py` with the GPL header copied from `tests/loxone/test_sender.py` lines 1-15, then:

```python
"""Tests for `loxone/probe.py` - design "The Miniserver's address is set in
the web interface" (2026-09-25), section 8. A real aiohttp server on
127.0.0.1 stands in for the Miniserver; nothing leaves the machine."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from loxmatter.loxone.probe import ProbeOutcome, probe_miniserver

FIXTURE = Path(__file__).parents[1] / "fixtures" / "miniserver" / "jdev_cfg_api.json"

Handler = Callable[[web.Request], "asyncio.Future[web.StreamResponse] | web.StreamResponse"]


@pytest.fixture
async def serve() -> AsyncIterator[Callable[[Handler], "asyncio.Future[int]"]]:
    servers: list[TestServer] = []

    async def start(handler: Handler) -> int:
        app = web.Application()
        app.router.add_get("/jdev/cfg/api", handler)
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        servers.append(server)
        assert server.port is not None
        return server.port

    yield start  # type: ignore[misc]
    for server in servers:
        await server.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_a_miniserver_answer_is_found_with_serial_and_firmware(serve):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=FIXTURE.read_text(), content_type="application/json")

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port)
    assert result.outcome is ProbeOutcome.FOUND
    assert result.found
    assert result.serial == "50:4F:94:00:00:01"
    assert result.firmware == "17.3.9.18"


async def test_something_else_answering_is_not_a_miniserver(serve):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="<html>router login</html>", content_type="text/html")

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port)
    assert result.outcome is ProbeOutcome.NOT_A_MINISERVER
    assert not result.found


async def test_an_http_error_status_is_reported_with_its_code(serve):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=503)

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port)
    assert result.outcome is ProbeOutcome.HTTP_ERROR
    assert result.http_status == 503


async def test_a_slow_answer_is_a_timeout(serve):
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(2)
        return web.Response(text=FIXTURE.read_text())

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port, timeout=0.2)
    assert result.outcome is ProbeOutcome.TIMEOUT


async def test_a_closed_port_is_no_connection():
    result = await probe_miniserver("127.0.0.1", port=_free_port(), timeout=1.0)
    assert result.outcome is ProbeOutcome.NO_CONNECTION
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/loxone/test_probe.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.loxone.probe'`.

- [ ] **Step 3: Implement**

Create `src/loxmatter/loxone/probe.py` with the GPL header from `src/loxmatter/loxone/sender.py` lines 1-15, then:

```python
"""Asks an address whether a Miniserver answers there - design "The
Miniserver's address is set in the web interface" (2026-09-25), section 8.

`/jdev/cfg/api` is the one request a Miniserver answers without signing in,
with its serial number and firmware version. The answer's `value` is not
JSON but a Python-style dict with single quotes, so the two fields are read
with the same patterns `install.sh`'s `check_miniserver` used on the same
shape (`tests/fixtures/miniserver/jdev_cfg_api.json`).

Never raises for a network failure: a Miniserver that is switched off right
now is a normal state, and the caller saves the address regardless."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import aiohttp

_SERIAL: Final = re.compile(r"'snr':\s*'([^']*)'")
_FIRMWARE: Final = re.compile(r"'version':\s*'([^']*)'")


class ProbeOutcome(StrEnum):
    FOUND = "found"
    TIMEOUT = "timeout"
    NO_CONNECTION = "no_connection"
    HTTP_ERROR = "http_error"
    NOT_A_MINISERVER = "not_a_miniserver"


@dataclass(frozen=True)
class MiniserverProbe:
    outcome: ProbeOutcome
    serial: str | None = None
    firmware: str | None = None
    http_status: int | None = None

    @property
    def found(self) -> bool:
        return self.outcome is ProbeOutcome.FOUND


async def probe_miniserver(ip: str, *, port: int = 80, timeout: float = 3.0) -> MiniserverProbe:
    """`port` exists for the tests; a Miniserver answers on 80."""
    url = f"http://{ip}:{port}/jdev/cfg/api"
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(url) as response,
        ):
            if response.status >= 400:
                return MiniserverProbe(ProbeOutcome.HTTP_ERROR, http_status=response.status)
            body = await response.text(errors="replace")
    # TimeoutError first: aiohttp's ServerTimeoutError is also a
    # ClientConnectionError, and a timeout is the more useful thing to say.
    except TimeoutError:
        return MiniserverProbe(ProbeOutcome.TIMEOUT)
    except (aiohttp.ClientError, OSError):
        return MiniserverProbe(ProbeOutcome.NO_CONNECTION)

    serial = _first(_SERIAL, body)
    firmware = _first(_FIRMWARE, body)
    if serial is None and firmware is None:
        return MiniserverProbe(ProbeOutcome.NOT_A_MINISERVER)
    return MiniserverProbe(ProbeOutcome.FOUND, serial=serial, firmware=firmware)


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest -q tests/loxone/test_probe.py`
Expected: PASS (5 tests).
Run: `uv run mypy && uv run ruff check src/loxmatter/loxone/probe.py tests/loxone/test_probe.py`
Expected: clean. If mypy objects to the fixture's typing, simplify the annotations to `Callable[..., Any]` rather than adding `type: ignore`.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/loxone/probe.py tests/loxone/test_probe.py
git commit -m "feat(loxone): ask an address whether a Miniserver answers there

The installer's /jdev/cfg/api check, in Python, so the settings route can
report the serial and firmware of the Miniserver it was pointed at.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Save, apply and check the address through the API

**Files:**
- Modify: `src/loxmatter/api/models.py:584-596`
- Modify: `src/loxmatter/api/settings.py`
- Modify: `src/loxmatter/loxone/server.py:555`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_settings_api.py`

**Interfaces:**
- Consumes: `BridgeSettingsStore.save(..., miniserver_ip)` (Task 1), `UdpSender.set_target`/`.target` (Task 2), `probe_miniserver`, `MiniserverProbe`, `ProbeOutcome` (Task 3).
- Produces:
  - `BridgeSettingsIn.miniserver_ip: str | None = None` — absent keeps the stored value; `null` or `""` clears it.
  - `BridgeSettingsOut.miniserver_ip: str | None`, `BridgeSettingsOut.miniserver_check: MiniserverCheckOut | None`
  - `MiniserverCheckOut(found: bool, serial: str | None, firmware: str | None, message: str)`
  - `build_settings_router(store: Store, *, sender: UdpSender | None = None, runtime: Resender | None = None) -> APIRouter`
  - `api.settings.probe_miniserver` is looked up at call time, so tests monkeypatch `loxmatter.api.settings.probe_miniserver`.
  - Web strings used by Task 7: `web.settings.miniserver_ip_label`, `web.settings.miniserver_ip_placeholder`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_settings_api.py`. Add these imports at the top: `import asyncio`, `from loxmatter.api import settings as settings_api`, `from loxmatter.loxone.probe import MiniserverProbe, ProbeOutcome`, `from loxmatter.loxone.sender import UdpSender`.

```python
@pytest.fixture
def probed(monkeypatch):
    """Replaces the network probe - `tests/loxone/test_probe.py` tests the
    real one against a local server. Returns the list of probed IPs and lets
    a test choose the answer."""
    calls: list[str] = []
    answer = {"probe": MiniserverProbe(ProbeOutcome.FOUND, serial="50:4F:94:00:00:01", firmware="17.3.9.18")}

    async def fake_probe(ip: str, **_: object) -> MiniserverProbe:
        calls.append(ip)
        return answer["probe"]

    monkeypatch.setattr(settings_api, "probe_miniserver", fake_probe)
    return calls, answer


@pytest.fixture
async def wired_api(tmp_path, no_invoke, fake_runtime, probed):
    """Like `api`, with a sender the route can retarget and the runtime it
    resends through."""
    store = Store(tmp_path / "t.sqlite")
    runtime = fake_runtime(store)
    sender = UdpSender(None, DEFAULT_UDP_PORT)
    app = build_app(store, no_invoke, runtime, sender=sender)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, sender, runtime
    await sender.close()
    store.close()


async def _until(condition, timeout: float = 1.0) -> None:
    """The resend runs as a background task; wait for it instead of sleeping
    a fixed time."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never became true"
        await asyncio.sleep(0.01)


def _body(**overrides):
    body = {"bridge_ip": "192.168.1.20", "udp_port": 7000, "listen_port": 8080}
    body.update(overrides)
    return body


async def test_a_fresh_installation_has_no_miniserver_ip(api):
    client, _ = api
    body = (await client.get("/api/settings")).json()
    assert body["miniserver_ip"] is None
    assert body["miniserver_check"] is None


async def test_saving_the_miniserver_ip_retargets_the_sender_and_resends(wired_api):
    client, store, sender, runtime = wired_api
    response = await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77", udp_port=7001))
    assert response.status_code == 200
    assert response.json()["miniserver_ip"] == "192.168.1.77"
    assert store.settings.get().miniserver_ip == "192.168.1.77"
    assert sender.target == ("192.168.1.77", 7001)
    await _until(lambda: runtime.resend_calls == 1)


async def test_saving_the_same_target_again_does_not_resend(wired_api):
    client, _, _, runtime = wired_api
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    await _until(lambda: runtime.resend_calls == 1)
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77", listen_port=8081))
    await asyncio.sleep(0.05)
    assert runtime.resend_calls == 1


async def test_the_response_carries_the_probe_result(wired_api, probed):
    client, _, _, _ = wired_api
    calls, _ = probed
    body = (await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))).json()
    assert calls == ["192.168.1.77"]
    check = body["miniserver_check"]
    assert check["found"] is True
    assert check["serial"] == "50:4F:94:00:00:01"
    assert check["firmware"] == "17.3.9.18"
    assert "192.168.1.77" in check["message"]


async def test_an_unreachable_miniserver_is_saved_anyway_with_a_warning(wired_api, probed):
    client, store, sender, _ = wired_api
    _, answer = probed
    answer["probe"] = MiniserverProbe(ProbeOutcome.TIMEOUT)
    response = await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    assert response.status_code == 200
    assert response.json()["miniserver_check"]["found"] is False
    assert store.settings.get().miniserver_ip == "192.168.1.77"
    assert sender.target == ("192.168.1.77", 7000)


async def test_an_invalid_miniserver_ip_yields_422_and_saves_nothing(wired_api):
    client, store, sender, _ = wired_api
    response = await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1"))
    assert response.status_code == 422
    assert "192.168.1" in response.json()["detail"]
    assert store.settings.get().saved_at is None
    assert sender.target is None


async def test_the_invalid_ip_message_is_german_under_the_german_locale(wired_api):
    client, store, _, _ = wired_api
    store.locale.set_language("de")
    response = await client.patch("/api/settings", json=_body(miniserver_ip="miniserver"))
    assert response.status_code == 422
    assert "IPv4" in response.json()["detail"]
    assert "IP des Miniservers" in response.json()["detail"]


async def test_an_empty_miniserver_ip_clears_the_address_and_the_target(wired_api, probed):
    client, store, sender, _ = wired_api
    calls, _ = probed
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    body = (await client.patch("/api/settings", json=_body(miniserver_ip=""))).json()
    assert body["miniserver_ip"] is None
    assert body["miniserver_check"] is None
    assert store.settings.get().miniserver_ip is None
    assert sender.target is None
    assert calls == ["192.168.1.77"]


async def test_a_body_without_the_field_keeps_the_stored_address(wired_api):
    """A browser tab still running the previous version's app.js after an
    update knows nothing of the field. Saving the bridge's IP from it must
    not erase the Miniserver's address (spec section 7)."""
    client, store, sender, _ = wired_api
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    await client.patch("/api/settings", json=_body(bridge_ip="192.168.1.21"))
    assert store.settings.get().miniserver_ip == "192.168.1.77"
    assert sender.target == ("192.168.1.77", 7000)


async def test_changing_only_the_udp_port_retargets_the_sender(wired_api):
    """The defect in spec section 1: the port in the card used to reach the
    templates only."""
    client, _, sender, runtime = wired_api
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    await client.patch("/api/settings", json=_body(udp_port=7009))
    assert sender.target == ("192.168.1.77", 7009)
    await _until(lambda: runtime.resend_calls == 2)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/api/test_settings_api.py`
Expected: the new tests FAIL (`KeyError: 'miniserver_ip'`, `AttributeError: module 'loxmatter.api.settings' has no attribute 'probe_miniserver'`); the old ones still pass.

- [ ] **Step 3: Models**

In `src/loxmatter/api/models.py`, replace `BridgeSettingsIn` and add `MiniserverCheckOut` directly above it. Find `BridgeSettingsOut` in the same file (`grep -n "class BridgeSettingsOut" src/loxmatter/api/models.py`) and add the two fields shown below to it.

```python
class MiniserverCheckOut(BaseModel):
    """What `PATCH /api/settings` found at the Miniserver's address
    (`loxone.probe`). `message` is already translated: the UI shows it as
    it is."""

    model_config = ConfigDict(frozen=True)

    found: bool
    serial: str | None
    firmware: str | None
    message: str


class BridgeSettingsIn(BaseModel):
    """Body of `PATCH /api/settings`. `bridge_ip` and the ports are sent
    together, no partial update: they belong together functionally (the
    same virtual connection), a partial update could otherwise leave a valid
    IP paired with a now-wrong port. `min_length=1` on `bridge_ip` yields
    422 for an empty field, without a dedicated validator.

    `miniserver_ip` is the one exception (design 2026-09-25, section 7): a
    body **without** it keeps the stored address, because a browser tab
    still running the previous version's `app.js` sends none. `null` or an
    empty string clears it."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str = Field(min_length=1)
    udp_port: int
    listen_port: int
    miniserver_ip: str | None = None
```

Fields to add to `BridgeSettingsOut`:

```python
    miniserver_ip: str | None
    # Only a `PATCH` that set an address probes it; `GET` never does.
    miniserver_check: MiniserverCheckOut | None = None
```

- [ ] **Step 4: Strings**

Add to `src/loxmatter/i18n/strings.yaml`, after `api.settings.resend_interval_too_small`:

```yaml
api.settings.invalid_ipv4:
  en: "{field}: \"{value}\" is not an IPv4 address like 192.168.1.10."
  de: "{field}: „{value}“ ist keine IPv4-Adresse wie 192.168.1.10."
api.settings.miniserver_found:
  en: "Miniserver found at {ip} (serial {serial}, firmware {firmware})."
  de: "Miniserver unter {ip} gefunden (Seriennummer {serial}, Firmware {firmware})."
api.settings.miniserver_timeout:
  en: "No Miniserver answers at {ip} (timeout). The address is saved; values go there as soon as the Miniserver is reachable."
  de: "Unter {ip} antwortet kein Miniserver (Zeitüberschreitung). Die Adresse ist gespeichert; die Werte gehen dorthin, sobald der Miniserver erreichbar ist."
api.settings.miniserver_no_connection:
  en: "No Miniserver answers at {ip} (could not connect). The address is saved; values go there as soon as the Miniserver is reachable."
  de: "Unter {ip} antwortet kein Miniserver (keine Verbindung). Die Adresse ist gespeichert; die Werte gehen dorthin, sobald der Miniserver erreichbar ist."
api.settings.miniserver_http_error:
  en: "No Miniserver answers at {ip} (HTTP {status}). The address is saved anyway."
  de: "Unter {ip} antwortet kein Miniserver (HTTP {status}). Die Adresse ist trotzdem gespeichert."
api.settings.miniserver_not_a_miniserver:
  en: "Something answers at {ip}, but it is not a Miniserver. The address is saved anyway - check it."
  de: "Unter {ip} antwortet etwas, aber kein Miniserver. Die Adresse ist trotzdem gespeichert – bitte prüfen."
```

(`test_no_value_is_wrapped_in_typographic_quotes` only rejects a value wrapped *as a whole* in „…“; quotes inside the sentence are fine.)

Add to the `web.settings` block, directly after `web.settings.connection_explanation`'s entry:

```yaml
web.settings.miniserver_ip_label:
  en: "IP of the Miniserver"
  de: "IP des Miniservers"
web.settings.miniserver_ip_placeholder:
  en: "e.g. 192.168.1.10"
  de: "z. B. 192.168.1.10"
```

- [ ] **Step 5: The route**

Replace the body of `src/loxmatter/api/settings.py` below the module docstring (keep the GPL header; extend the docstring with a paragraph: "Since 2026-09-25 the route also holds the Miniserver's address: saving it retargets the sender, resends every value in the background and probes the address - design "The Miniserver's address is set in the web interface", sections 7 and 8."):

```python
from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Protocol

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.models import (
    BridgeSettingsIn,
    BridgeSettingsOut,
    MiniserverCheckOut,
    ResendIntervalIn,
    ResendIntervalOut,
)
from loxmatter.loxone.probe import MiniserverProbe, ProbeOutcome, probe_miniserver
from loxmatter.loxone.sender import UdpSender
from loxmatter.model.store import Store

logger = logging.getLogger(__name__)


class Resender(Protocol):
    async def resend_all(self) -> int: ...


def _settings_out(store: Store, check: MiniserverCheckOut | None = None) -> BridgeSettingsOut:
    settings = store.settings.get()
    return BridgeSettingsOut(
        bridge_ip=settings.bridge_ip,
        udp_port=settings.udp_port,
        listen_port=settings.listen_port,
        miniserver_ip=settings.miniserver_ip,
        saved_at=settings.saved_at,
        miniserver_check=check,
    )


def _resend_interval_out(store: Store) -> ResendIntervalOut:
    return ResendIntervalOut(interval_seconds=store.resend_settings.get_interval_seconds())


def _miniserver_ip(patch: BridgeSettingsIn, stored: str | None) -> str | None:
    """Absent keeps `stored`; `null` or blank clears; anything else must be
    an IPv4 address."""
    if "miniserver_ip" not in patch.model_fields_set:
        return stored
    value = (patch.miniserver_ip or "").strip()
    if not value:
        return None
    try:
        ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=i18n.t(
                "api.settings.invalid_ipv4",
                field=i18n.t("web.settings.miniserver_ip_label"),
                value=value,
            ),
        ) from exc
    return value


def _check_out(ip: str, probe: MiniserverProbe) -> MiniserverCheckOut:
    unknown = "-"
    if probe.outcome is ProbeOutcome.FOUND:
        message = i18n.t(
            "api.settings.miniserver_found",
            ip=ip,
            serial=probe.serial or unknown,
            firmware=probe.firmware or unknown,
        )
    elif probe.outcome is ProbeOutcome.HTTP_ERROR:
        message = i18n.t("api.settings.miniserver_http_error", ip=ip, status=probe.http_status)
    else:
        message = i18n.t(f"api.settings.miniserver_{probe.outcome.value}", ip=ip)
    return MiniserverCheckOut(
        found=probe.found, serial=probe.serial, firmware=probe.firmware, message=message
    )


def build_settings_router(
    store: Store,
    *,
    sender: UdpSender | None = None,
    runtime: Resender | None = None,
) -> APIRouter:
    """`sender`/`runtime` are `None` in tests that build the app without
    them: the route then saves without retargeting anything, the same
    reduced mode the diagnostics check reports for a missing sender."""
    router = APIRouter(prefix="/api")
    # Held so a running resend is not garbage-collected mid-flight.
    resend_tasks: set[asyncio.Task[int]] = set()

    def _resend_done(task: asyncio.Task[int]) -> None:
        resend_tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.error(
                "full resend after a Miniserver address change failed", exc_info=exc
            )

    def _resend_in_background() -> None:
        # Not awaited: at the sender's rate limit a full resend of a large
        # installation takes seconds, and the answer should not wait for it.
        assert runtime is not None
        task = asyncio.create_task(runtime.resend_all())
        resend_tasks.add(task)
        task.add_done_callback(_resend_done)

    @router.get("/settings")
    async def get_settings() -> BridgeSettingsOut:
        return _settings_out(store)

    @router.patch("/settings")
    async def save_settings(patch: BridgeSettingsIn) -> BridgeSettingsOut:
        previous = store.settings.get()
        miniserver_ip = _miniserver_ip(patch, previous.miniserver_ip)
        store.settings.save(
            bridge_ip=patch.bridge_ip,
            udp_port=patch.udp_port,
            listen_port=patch.listen_port,
            miniserver_ip=miniserver_ip,
        )
        if sender is not None:
            before = sender.target
            sender.set_target(miniserver_ip, patch.udp_port)
            if sender.target is not None and sender.target != before and runtime is not None:
                _resend_in_background()
        check = None
        if miniserver_ip is not None and "miniserver_ip" in patch.model_fields_set:
            check = _check_out(miniserver_ip, await probe_miniserver(miniserver_ip))
        return _settings_out(store, check)

    # ... the two resend-interval routes stay exactly as they are ...

    return router
```

Why the probe runs only when the field was in the body: a save from an old tab should not report on an address it never showed. Keep that condition.

- [ ] **Step 6: Wire it in `server.py`**

In `src/loxmatter/loxone/server.py` replace line 555:

```python
    app.include_router(
        build_settings_router(store, sender=sender, runtime=runtime), dependencies=api_guard
    )
```

- [ ] **Step 7: Run the tests and checks**

Run: `uv run pytest -q tests/api/test_settings_api.py tests/test_i18n.py`
Expected: PASS.
Run: `uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: clean. (Run `uv run ruff format` on the touched files if the check complains.)

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/api/models.py src/loxmatter/api/settings.py src/loxmatter/loxone/server.py src/loxmatter/i18n/strings.yaml tests/api/test_settings_api.py
git commit -m "feat(api): save, apply and check the Miniserver's address

PATCH /api/settings retargets the sender to the saved IP and UDP port,
resends every value in the background and reports what answers at the
address. A body without the field keeps the stored address, so a tab
running an older app.js cannot erase it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `loxmatter run` seeds the store and sends where it says

**Files:**
- Modify: `src/loxmatter/cli.py:554-760`
- Modify: `src/loxmatter/i18n/strings.yaml` (`cli.run.*`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `BridgeSettingsStore.seed_miniserver`, `.get()` (Task 1); `UdpSender(host: str | None, port)` (Task 2).
- Produces: `run(..., miniserver: str | None = None, ...)`; `_run(store, url, miniserver: str | None, port, listen, ...)` (positional order unchanged); `_apply_miniserver_argument(store: Store, miniserver: str | None, port: int) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (it already has `_install_run_spies`, `_SpyUvicornServer`, `Store`, `cli`, `pytest`, `caplog` is a pytest builtin):

```python
async def test_run_seeds_the_miniserver_address_on_first_start(monkeypatch, tmp_path):
    """An existing installation carries its .env address over on the first
    start of the version that stores it (spec section 4)."""
    senders, _, _, _ = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")
    path = store.path

    await cli._run(store, "ws://test/ws", "10.0.1.99", 7005, 8080)

    reopened = Store(path)
    try:
        assert reopened.settings.get().miniserver_ip == "10.0.1.99"
        assert reopened.settings.get().udp_port == 7005
    finally:
        reopened.close()
    assert (senders[0].host, senders[0].port) == ("10.0.1.99", 7005)


async def test_the_stored_address_wins_over_the_argument(monkeypatch, tmp_path, caplog):
    senders, _, _, _ = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")
    store.settings.save(
        bridge_ip="10.0.1.5", udp_port=7001, listen_port=8080, miniserver_ip="10.0.1.42"
    )

    await cli._run(store, "ws://test/ws", "10.0.1.99", 7000, 8080)

    assert (senders[0].host, senders[0].port) == ("10.0.1.42", 7001)
    assert any(
        "--miniserver" in r.getMessage() and "10.0.1.42" in r.getMessage() for r in caplog.records
    )


@pytest.mark.parametrize("argument", [None, ""])
async def test_an_absent_or_empty_argument_seeds_nothing(monkeypatch, tmp_path, argument):
    """docker-compose.yml passes ${MINISERVER_IP}, which is empty on every
    installation made after this change."""
    senders, _, _, _ = _install_run_spies(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "Server", _SpyUvicornServer)
    store = Store(tmp_path / "t.sqlite")
    path = store.path

    await cli._run(store, "ws://test/ws", argument, 7000, 8080)

    reopened = Store(path)
    try:
        assert reopened.settings.get().miniserver_ip is None
    finally:
        reopened.close()
    assert senders[0].host is None
```

Check `_SpySender`'s constructor in `tests/test_cli.py` (`grep -n "class _SpySender" -A15 tests/test_cli.py`). If it does not keep `host`/`port` as attributes, add `self.host = host` and `self.port = port` there, and widen its `host` annotation to `str | None`. Also widen `make_sender`'s `host: str` to `host: str | None` in `_install_run_spies`.

Also add a test that `run()` accepts no `--miniserver` at all, using typer's runner the way the file already invokes `run` (search `CliRunner` in `tests/test_cli.py` and follow the nearest existing test that stubs `_run`):

```python
def test_run_no_longer_requires_miniserver(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_run(store, url, miniserver, *args, **kwargs):
        captured["miniserver"] = miniserver
        store.close()

    monkeypatch.setattr(cli, "_run", fake_run)
    result = CliRunner().invoke(cli.app, ["run", "--store-path", str(tmp_path / "t.sqlite")])
    assert result.exit_code == 0, result.output
    assert captured["miniserver"] is None
```

Adapt the import of `CliRunner` and the way `store.close()` is handled to what the neighbouring test does; the assertion that matters is `exit_code == 0` without `--miniserver`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_cli.py -k "miniserver or stored_address or absent_or_empty"`
Expected: FAIL — the sender is built with the argument, nothing is stored, and `run` exits 2 with "Missing option '--miniserver'".

- [ ] **Step 3: Implement**

In `run()`:

```python
    miniserver: str | None = typer.Option(None, help=i18n.t("cli.run.help_miniserver")),
    port: int = typer.Option(7000, help=i18n.t("cli.run.help_port")),
```

Change `_run`'s parameter to `miniserver: str | None,`.

Add above `_run`:

```python
def _apply_miniserver_argument(store: Store, miniserver: str | None, port: int) -> None:
    """`--miniserver`/`--port` only seed an installation that has no
    Miniserver address yet (design 2026-09-25, section 4): once one is
    stored - seeded here or saved in the interface - the store wins, and a
    differing argument is named in a warning, as `--zigbee-device` is. An
    empty string counts as absent, because docker-compose.yml passes
    `${MINISERVER_IP}` and new installations leave it empty."""
    if not miniserver:
        return
    stored = store.settings.get().miniserver_ip
    if stored is None:
        store.settings.seed_miniserver(miniserver, port)
        logger.info(i18n.t("cli.run.info_miniserver_seeded", ip=miniserver))
    elif stored != miniserver:
        logger.warning(i18n.t("cli.run.warn_miniserver_ignored", flag=miniserver, stored=stored))
```

In `_run`, replace `sender = UdpSender(miniserver, port)` with:

```python
    # The store, not the arguments, says where values go - so the port saved
    # in Settings is the one the sender uses, and an address entered there
    # survives a restart. See `_apply_miniserver_argument`.
    _apply_miniserver_argument(store, miniserver, port)
    connection = store.settings.get()
    sender = UdpSender(connection.miniserver_ip, connection.udp_port)
```

Update the `_run` docstring's first paragraph with one sentence: "The sender's target comes from the stored settings; `miniserver`/`port` only seed them (see `_apply_miniserver_argument`)."

Strings — replace `cli.run.help_miniserver` and add the three new keys next to it:

```yaml
cli.run.help_miniserver:
  en: "IP of the Miniserver, used ONLY to set up an installation that has no Miniserver address yet. Once one is stored - here or in the interface, under Settings → Miniserver connection - the stored address wins and this flag is ignored with a warning."
  de: "IP des Miniservers, nur zur Ersteinrichtung einer Installation, die noch keine Miniserver-Adresse hat. Sobald eine gespeichert ist – hier oder in der Oberfläche unter Einstellungen → Verbindung zum Miniserver –, gilt die gespeicherte Adresse und diese Option wird mit einer Warnung übergangen."
cli.run.help_port:
  en: "UDP port the Miniserver listens on, used ONLY where no UDP port is stored yet. The port under Settings → Miniserver connection wins."
  de: "UDP-Port, auf dem der Miniserver lauscht, nur solange noch keiner gespeichert ist. Der Port unter Einstellungen → Verbindung zum Miniserver hat Vorrang."
cli.run.info_miniserver_seeded:
  en: "Took the Miniserver address {ip} over from --miniserver. From now on it is changed in the interface, under Settings → Miniserver connection."
  de: "Miniserver-Adresse {ip} aus --miniserver übernommen. Geändert wird sie ab jetzt in der Oberfläche unter Einstellungen → Verbindung zum Miniserver."
cli.run.warn_miniserver_ignored:
  en: "--miniserver names {flag}, but this bridge already has the Miniserver address {stored} and the stored one wins. Change it in the interface, under Settings → Miniserver connection."
  de: "--miniserver nennt {flag}, aber diese Brücke hat bereits die Miniserver-Adresse {stored}, und die gespeicherte gilt. Ändern Sie sie in der Oberfläche unter Einstellungen → Verbindung zum Miniserver."
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest -q tests/test_cli.py tests/test_cli_language.py`
Expected: PASS — including every existing `_run` test, which passes `"127.0.0.1", 7000` into a fresh store and therefore now seeds and sends to the same address.
Run: `uv run mypy`
Expected: `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/cli.py src/loxmatter/i18n/strings.yaml tests/test_cli.py
git commit -m "feat(cli): --miniserver only seeds, the stored address is used

run sends to the address and UDP port in the store, so the port saved in
Settings finally reaches the sender. --miniserver and --port become
starting values for an installation that has none.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Project sync picks the stored Miniserver

**Files:**
- Modify: `src/loxmatter/api/project_sync.py:108-135`
- Test: `tests/api/test_project_sync_api.py`

**Interfaces:**
- Consumes: `store.settings.get().miniserver_ip` (Task 1), `store.settings.save(..., miniserver_ip=...)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_project_sync_api.py` (uses the existing `api` fixture and `TWO_LOXLIVE_PROJECT` / `SAMPLE_PROJECT` constants):

```python
def _store_miniserver(store, ip):
    store.settings.save(bridge_ip="10.0.0.5", udp_port=7000, listen_port=8080, miniserver_ip=ip)


async def test_the_stored_miniserver_resolves_a_file_with_several(api):
    """Spec section 9: no selection field when the address in Settings
    already says which Miniserver this bridge talks to."""
    client, store = api
    _store_miniserver(store, "10.0.0.20")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("mehrere_ms.Loxone", TWO_LOXLIVE_PROJECT.encode("utf-8"), "application/xml")},
    )
    body = response.json()
    assert body["needs_miniserver_selection"] is False
    assert body["patched_base64"] is not None


async def test_a_stored_miniserver_not_in_the_file_still_asks(api):
    client, store = api
    _store_miniserver(store, "10.0.0.99")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("mehrere_ms.Loxone", TWO_LOXLIVE_PROJECT.encode("utf-8"), "application/xml")},
    )
    assert response.json()["needs_miniserver_selection"] is True


async def test_a_single_miniserver_file_ignores_a_stored_address_that_differs(api):
    """`build_index` rejects a `miniserver_ip` that does not match a single
    block, so the stored address may only be offered where the choice is
    open."""
    client, store = api
    _store_miniserver(store, "10.0.0.99")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("p.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 200
    assert response.json()["patched_base64"] is not None
```

If the `api` fixture in this file yields something other than `(client, store)`, adapt the unpacking.

- [ ] **Step 2: Run to verify the first fails**

Run: `uv run pytest -q tests/api/test_project_sync_api.py`
Expected: `test_the_stored_miniserver_resolves_a_file_with_several` FAILS (`needs_miniserver_selection` is `True`); the other two pass already and guard the boundary.

- [ ] **Step 3: Implement**

In `project_sync`, replace the `try: result = run_sync(...)` / `except AmbiguousMiniserverError` part with:

```python
        raw = await file.read()
        offset = None if utc_offset is None else timedelta(minutes=utc_offset)

        def sync(ip: str | None) -> SyncResult:
            return run_sync(
                raw,
                store,
                bridge_ip=bridge_ip,
                port=port,
                listen=listen,
                miniserver_ip=ip,
                utc_offset=offset,
            )

        try:
            try:
                result = sync(miniserver_ip)
            except AmbiguousMiniserverError as exc:
                # The address in Settings says which Miniserver this bridge
                # talks to (design 2026-09-25, section 9) - used only where
                # the file leaves the choice open and names that address.
                stored = store.settings.get().miniserver_ip
                offered = {c.int_addr for c in exc.candidates}
                if miniserver_ip is not None or stored is None or stored not in offered:
                    raise
                result = sync(stored)
        except AmbiguousMiniserverError as exc:
            if not exc.candidates:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return ProjectSyncPlanOut(
                needs_miniserver_selection=True,
                available_miniservers=[
                    ProjectSyncMiniserverOut(title=c.title, int_addr=c.int_addr)
                    for c in exc.candidates
                ],
            )
        except ProjectFormatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
```

`SyncResult` is the return type of `run_sync`; find its real name with `grep -n "def run_sync" -A12 src/loxmatter/projectsync/sync.py` and import it from there. If `run_sync` is imported under another name in this module, keep that name.

Update the `miniserver_ip` query parameter's description: append " Without it, the address saved under Settings → Miniserver connection is used when the file offers it."

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/api/test_project_sync_api.py`
Run: `uv run pytest -q tests/projectsync`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/project_sync.py tests/api/test_project_sync_api.py
git commit -m "feat(api): project sync takes the Miniserver from Settings

A project file with several Miniservers no longer asks which one when the
saved address names one of them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The field in the web interface

**Files:**
- Modify: `src/loxmatter/web/index.html` (settings card ~line 2641, export block ~line 2027)
- Modify: `src/loxmatter/web/app.js` (state ~line 1119, `loadSettings` ~5273, `saveSettings` ~6451)
- Modify: `src/loxmatter/i18n/strings.yaml` (`web.settings.connection_explanation`)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `GET/PATCH /api/settings` fields `miniserver_ip`, `miniserver_check.{found,message}` (Task 4); strings `web.settings.miniserver_ip_label`, `web.settings.miniserver_ip_placeholder` (Task 4).

- [ ] **Step 1: Write the failing markup tests**

Append to `tests/api/test_web.py` (uses the file's `api` fixture, `_without_comments` and `_label_around` helpers):

```python
async def test_the_settings_card_has_a_miniserver_ip_field(api):
    """Design 2026-09-25, section 9: the card named after the Miniserver
    finally holds its address - under its own label, never the bridge's."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    label = _label_around(markup, 'x-model="settingsDraft.miniserver_ip"')
    assert "x-text=\"t('web.settings.miniserver_ip_label')\"" in label, label
    assert "web.bridge_ip_label" not in label, label
    assert ":placeholder=\"t('web.settings.miniserver_ip_placeholder')\"" in label, label


async def test_the_settings_card_shows_the_probe_result(api):
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-text="bridgeSettings.miniserver_check?.message"' in markup
    assert "bridgeSettings.miniserver_check?.found ? 'ok' : 'warn'" in markup


async def test_the_export_block_shows_the_miniserver_ip_read_only(api):
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    label = _label_around(markup, ':value="bridgeSettings.miniserver_ip')
    assert "x-text=\"t('web.settings.miniserver_ip_label')\"" in label, label
    assert "readonly" in label, label


async def test_save_settings_sends_the_miniserver_ip(api):
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async saveSettings() {")
    body = script[start : script.index("\n    },", start)]
    assert "miniserver_ip: this.settingsDraft.miniserver_ip.trim() || null," in body
```

Check `_label_around`'s signature first (`grep -n "def _label_around" -A20 tests/api/test_web.py`); if it needs the marker in a different form, adapt the second argument, not the helper.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/api/test_web.py -k "miniserver"`
Expected: FAIL.

- [ ] **Step 3: Markup**

In the settings card, insert as the **first** `<label>` inside `<div class="row">`:

```html
            <label
              ><span x-text="t('web.settings.miniserver_ip_label')"></span>
              <input
                type="text"
                x-model="settingsDraft.miniserver_ip"
                :placeholder="t('web.settings.miniserver_ip_placeholder')"
            /></label>
```

Directly after the save `<div class="row">…</div>` and before the `settingsError` banner:

```html
          <p
            x-show="bridgeSettings.miniserver_check"
            x-cloak
            class="banner"
            :class="bridgeSettings.miniserver_check?.found ? 'ok' : 'warn'"
            x-text="bridgeSettings.miniserver_check?.message"
          ></p>
```

In the export block (`<h2 x-text="t('web.export.heading')">`), insert as the first `<label>` of its row:

```html
            <label
              ><span x-text="t('web.settings.miniserver_ip_label')"></span>
              <input type="text" :value="bridgeSettings.miniserver_ip || ''" readonly
            /></label>
```

- [ ] **Step 4: Script**

State:

```javascript
    bridgeSettings: {
      bridge_ip: null,
      udp_port: 7000,
      listen_port: 8080,
      miniserver_ip: null,
      miniserver_check: null,
      saved_at: null,
    },
    settingsDraft: { miniserver_ip: "", bridge_ip: "", udp_port: 7000, listen_port: 8080 },
```

Update the comment above it: "`settingsDraft` are the four input fields on this tab".

In `loadSettings`, add `miniserver_ip: this.bridgeSettings.miniserver_ip ?? "",` as the first draft field.

In `saveSettings`, the PATCH body becomes:

```javascript
        this.bridgeSettings = await this.request("PATCH", "/api/settings", {
          miniserver_ip: this.settingsDraft.miniserver_ip.trim() || null,
          bridge_ip: this.settingsDraft.bridge_ip.trim(),
          udp_port: Number(this.settingsDraft.udp_port),
          listen_port: Number(this.settingsDraft.listen_port),
        });
```

The toast stays; the banner carries the probe result.

- [ ] **Step 5: Rewrite the explanation**

Replace both values of `web.settings.connection_explanation` (keep `<strong>` and `<span class="key">`, which `test_the_settings_tab_connection_explanation_html_renders_inline_markup` requires):

```yaml
web.settings.connection_explanation:
  en: |
    The <strong>IP of the Miniserver</strong> is where loxmatter sends its values. The <strong>IP of this bridge</strong> is the address of the machine loxmatter runs on, as the Miniserver sees it: the virtual input only accepts datagrams from it, and the output commands call it as <span class="key">http://&lt;this IP&gt;:HTTP-port</span>. Swapping the two gives templates that look correct but stay silent, with no error message.
  de: |
    An die <strong>IP des Miniservers</strong> schickt loxmatter seine Werte. Die <strong>IP dieser Brücke</strong> ist die Adresse des Rechners, auf dem loxmatter läuft – so, wie der Miniserver ihn sieht: Der virtuelle Eingang nimmt Datagramme nur von dieser Adresse an, und die Ausgangsbefehle rufen sie als <span class="key">http://&lt;diese IP&gt;:HTTP-Port</span> auf. Wer die beiden vertauscht, bekommt Vorlagen, die richtig aussehen, aber stumm bleiben – ohne jede Fehlermeldung.
```

`test_web.py` asserts `"Gemeint ist die Adresse des Rechners" not in markup` — that still holds (the markup never contained it).

- [ ] **Step 6: Run the web tests**

Run: `uv run pytest -q tests/api/test_web.py tests/test_i18n.py`
Expected: PASS.

- [ ] **Step 7: Verify the binding in a browser**

A served-file test proves only delivery. Build a throwaway harness in the scratchpad directory:
1. Copy `src/loxmatter/web/style.css` and `src/loxmatter/web/vendor/alpine.min.js` next to a new `harness.html`.
2. With a short Python script, cut the settings card (`<section x-show="view === 'settings'"` up to the end of its first `.card` div) **out of** `index.html` — do not retype it.
3. Give the harness a minimal `app()` with `t(key)` returning the key, `formatTimestamp`, `bridgeSettings`, `settingsDraft`, `settingsBusy`, `settingsError`, and a `saveSettings()` that sets `this.bridgeSettings = {...this.bridgeSettings, miniserver_ip: this.settingsDraft.miniserver_ip, miniserver_check: {found: false, message: "probe says no"}}`.
4. Serve with `python3 -m http.server` (not `file://` — Alpine does not run there) and open it in a **new** tab of the built-in browser.
5. Type into the Miniserver field (with the `computer` `type` action — `form_input` does not fire Alpine's `input` event), click Save, and read back: the banner is visible, has classes `banner warn`, and shows "probe says no". Change the stub to `found: true` and check `banner ok`.

Report what the DOM showed. If a step could not be made meaningful in this environment, say so explicitly rather than passing it.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): enter the Miniserver's address under Settings

The Miniserver connection card gets the field it is named after, shows
what answered at the address after saving, and explains which of the two
addresses goes where. The export tab lists it with the other values.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The installer stops asking; documentation follows

**Files:**
- Modify: `install.sh` (lines ~138, 530-532, 561-563, 582, 630-645, 899-990, 1173, and `report()` ~1390)
- Modify: `tests/test_install_script.py`
- Modify: `deploy/testhost/.env.example:56-59`, `deploy/testhost/README.md:63-69`, `deploy/testhost/docker-compose.yml:25` (comment only), `README.md:198-201`, `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: Change the installer tests first**

In `tests/test_install_script.py`:

- **Delete** these tests, which describe the question and the probe that no longer exist: `test_without_a_miniserver_ip_and_without_a_terminal_it_aborts`, `test_a_miniserver_that_answers_is_named`, `test_an_unreachable_miniserver_without_a_terminal_becomes_a_finding`, `test_something_else_answering_is_not_taken_for_a_miniserver`, `test_the_miniserver_question_says_where_to_find_the_address`, `test_an_unreachable_miniserver_can_be_used_anyway`, the test at line ~785 that re-enters an address after a dead one, the test at line ~795 that rejects `10.0.1` then accepts `10.0.1.43`, `test_a_dry_run_does_not_contact_the_miniserver`, `test_a_second_run_does_not_check_the_miniserver_again`, `test_without_curl_the_miniserver_is_not_checked`, and `_fixture_serial` if nothing else uses it.
- **Keep** `test_an_invalid_miniserver_ip_aborts_before_cloning` and the two malformed-address tests at ~471/478: a malformed address passed in the environment still stops the run before anything is written.
- **Keep** `test_the_miniserver_ip_comes_from_the_environment`.
- **Rewrite** the tests around lines 1222-1240 and 1480-1520 that count or name the announced questions: with `MINISERVER_IP=""`, the Miniserver is no longer among them. Where a test fed an answer (`answers=["10.0.1.43"]`) only to satisfy the Miniserver question, drop that answer and assert on the remaining questions.
- **Add**:

```python
def test_without_a_miniserver_ip_it_installs_and_leaves_the_address_to_the_web_interface(installer):
    """Design 2026-09-25, section 10: the address is entered under
    Settings -> Miniserver connection, not asked for here."""
    result = installer(env={"MINISERVER_IP": ""})
    assert result.returncode == 0, result.output
    assert "Miniserver" not in _questions_announced(result.output)
    assert _env(result)["MINISERVER_IP"] == ""
    assert "Settings -> Miniserver connection" in result.output


def test_a_passed_miniserver_ip_is_written_without_contacting_it(installer):
    result = installer(env={"MINISERVER_IP": "10.0.1.77", "FAKE_MS_DEAD": "10.0.1.77"})
    assert result.returncode == 0, result.output
    assert _env(result)["MINISERVER_IP"] == "10.0.1.77"
    assert "No Miniserver answers" not in result.output
```

Check how the file inspects exit status and announced questions (`grep -n "returncode\|exit_code\|questions follow\|question follows" tests/test_install_script.py | head`) and replace `result.returncode` and `_questions_announced(...)` with what the file actually uses; if there is no helper for the announcement, assert `"the address of your Loxone Miniserver" not in result.output` instead.

Run: `uv run pytest -q tests/test_install_script.py -k "miniserver or question"`
Expected: the two new tests FAIL; rewritten question-count tests FAIL.

- [ ] **Step 2: Change `install.sh`**

- Help text (~138): `  MINISERVER_IP       address of the Loxone Miniserver; optional, also set later in the web interface`
- Delete `miniserver_question_expected()` and its use in `announce_questions` (the three lines with `aq_add "the address of your Loxone Miniserver"`).
- In `ask_questions`, delete the line `decide_miniserver`.
- In `check_config_source`, delete the `die "MINISERVER_IP is not set and there is no terminal to ask on. …"` block and keep the `valid_ipv4` check.
- Delete `check_miniserver`, `add_miniserver_finding` and `decide_miniserver` (lines ~899-990).
- At ~1173 replace `write_env_value MINISERVER_IP "$CHOSEN_MS" 1` with:

```sh
  # Optional since 2026-09-25: the address is entered in the web interface.
  # One passed in is still written, and the bridge takes it over on its first
  # start; check_config_source has already refused a malformed one.
  write_env_value MINISERVER_IP "${MINISERVER_IP:-}" 0
```

- In `report()`, after the password lines, add:

```sh
  printf '\n  Then enter the Miniserver'\''s address under Settings -> Miniserver connection.\n'
```

- `grep -n "CHOSEN_MS\|MS_PROBLEM" install.sh` must return nothing afterwards.

Run: `sh -n install.sh` (syntax) and, if installed, `shellcheck install.sh`.
Run: `uv run pytest -q tests/test_install_script.py`
Expected: PASS.

- [ ] **Step 3: Documentation**

`deploy/testhost/.env.example`, replace the `MINISERVER_IP` comment:

```sh
# IP of the Loxone Miniserver, as seen from this host. Optional: it is
# entered in the web interface under Settings -> Miniserver connection.
# A value here is taken over once, on the bridge's first start with an
# address-less database; after that the web interface's value wins. Keep
# the line even when empty - an update rolled back to an older version
# still passes it as --miniserver.
MINISERVER_IP=
```

`deploy/testhost/README.md` ~63-69: replace "MINISERVER_IP must be set, shipped empty." with "MINISERVER_IP is optional: enter the address in the web interface under Settings -> Miniserver connection." and drop the `sed -i "s|^MINISERVER_IP=…` line.

`deploy/testhost/docker-compose.yml:25`: change "Requires MINISERVER_IP in .env." to "MINISERVER_IP in .env is optional (a starting value; see .env.example)." Do not touch the `command:` list.

`README.md` ~198-201:

```markdown
It asks up to two things before it installs anything: which USB stick is your
Thread stick (or none), and which Bluetooth adapter to use when there is more
than one. After that you can walk away — it installs what is missing and starts
the containers. When it finishes it prints the address of the web interface.
**Open it and set a password**: until you do, no `/api` route answers. Then
enter your Miniserver's IP address under Settings → Miniserver connection; the
bridge checks it right away.
```

`CHANGELOG.md`, under `## [Unreleased]` → `### Added` (create `### Fixed` if absent):

```markdown
- **The Miniserver's address is set in the web interface.** Settings →
  Miniserver connection now has a field for it. Saving applies it at once,
  without a restart, sends every current value to the Miniserver, and says
  whether a Miniserver answered at that address. The installer no longer asks
  for it. An address from an earlier installation is taken over by itself.
```

```markdown
### Fixed

- **A UDP port changed in the settings is the one the bridge sends to.** It
  used to reach only the export templates, while values kept going to port
  7000 — so a Miniserver set up from those templates never received them.
```

Check `CHANGELOG.md` for the tone of neighbouring entries and match it.

- [ ] **Step 4: Run the checks**

Run: `uv run python scripts/check_language.py`
Run: `uv run pytest -q tests/test_compose_profiles.py`
Expected: "No German found." / PASS.

- [ ] **Step 5: Commit**

```bash
git add install.sh tests/test_install_script.py deploy/testhost/.env.example deploy/testhost/README.md deploy/testhost/docker-compose.yml README.md CHANGELOG.md
git commit -m "feat(install): leave the Miniserver's address to the web interface

The installer no longer asks for or probes the address; the bridge does
both under Settings. An address passed in the environment is still
written, and MINISERVER_IP stays in .env and compose for seeding and
rollbacks.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Whole-branch verification

- [ ] **Step 1: Static checks**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_language.py
```

Expected: all clean.

- [ ] **Step 2: The suite, in its four measured parts — each in the foreground, one after the other**

```bash
uv run pytest -q tests/api
```

```bash
uv run pytest -q tests/auth tests/commands tests/devtools tests/diagnostics tests/export tests/loxone tests/matter tests/model tests/profiles tests/projectsync tests/radios tests/sources tests/zigbee
```

```bash
uv run pytest -q tests/test_install_script.py tests/test_update_script.py tests/test_updater_script.py tests/test_updater_radios_script.py tests/test_updater_watchdog_once.py
```

```bash
uv run pytest -q tests/test_build_arguments.py tests/test_cli.py tests/test_cli_language.py tests/test_compose_profiles.py tests/test_export_cli.py tests/test_i18n.py tests/test_otbr_image.py tests/test_otbr_watchdog.py tests/test_store_path.py tests/test_update_check.py tests/test_update_module.py tests/test_updater_entrypoint.py tests/test_updater_image.py tests/test_version.py
```

Then compare the sum of the four "passed" counts with `uv run pytest --collect-only -q | tail -1`. If they differ, a file was left out: find it and run it.

- [ ] **Step 3: Look for leftovers**

```bash
git grep -n "UdpSender(miniserver" -- src
git grep -n "decide_miniserver\|check_miniserver\|CHOSEN_MS" -- install.sh tests
git grep -n "settings.save(" -- src scripts tests | grep -v miniserver_ip
```

Expected: no output from any of the three.

- [ ] **Step 4: Report** which checks ran, the four pass counts against the collected total, and the browser harness result from Task 7. Do not merge; the branch is handed back for review.
