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

"""Diagnosing an unfamiliar installation (Spec 10.5).

Four tools, one shared purpose: a person reports "it doesn't work", and
someone else - a fellow developer, a first responder in the forum - has
to find out why without access to the house. Without this page, "it
doesn't work" is the entire fault description.

**The record of sent datagrams hangs off `UdpSender`, not beside it.** A
recording that hooks in BEFORE sending (e.g. in `Runtime.on_attribute`)
shows what SHOULD be sent. A recording in `UdpSender.send` itself shows
what actually went over the wire - after debouncing, after rate limiting,
after every silent "skipped because unchanged". Exactly the cases where
intent and reality diverge are the interesting ones for a diagnosis - a
recording beside it would hide them, not show them. That is why
`loxone.sender` imports `RingBuffer` from here (see there) rather than
the other way round: this module is the place, named in the interface
contract, for the generic, runtime-independent ring buffer that both the
datagram and the command recording (server.py) need - a reversal of the
otherwise usual direction "api depends on loxone" (see e.g. api/live.py,
which imports `Runtime`), deliberately accepted here because `RingBuffer`
itself carries no API-specific knowledge whatsoever (no FastAPI import at
module level before either of the two use sites needs it) and the
alternative - a third, dedicated module just for a 15-line class - would
have cost more indirection than it saved.

**Every red line in the system check carries a concrete pointer.** A red
dot with no explanation only shifts the puzzle from "it doesn't work" to
"the system check says red, but not why" - the same dead end, just one
level deeper. `_run_check` below therefore additionally wraps EVERY check
in its own try/except: a check that itself contains a program bug (not
just an expected failure case like "Miniserver unreachable") turns into
exactly one red line pointing at the server log - not a 500 for the
entire system check. A diagnosis that fails at its own checking would be
worse than none at all (see
`test_a_check_that_raises_unexpectedly_fails_gracefully`).

**The backup is not a side point.** Spec 4.1 calls the matter-server data
directory (containing the fabric credentials) the one irreplaceable
piece of state in the whole system - if it is lost, every device has to
be re-commissioned, and for Thread devices that means: reset, evict from
the old network, re-pair. `GET /api/diagnostics/fabric-backup` delivers
the content of this directory as a ZIP.

This file is key material, not a log - whoever possesses it can take
over the fabric. Two consequences, both documented below at the route:

- **Protected since Task 8 (Phase 5, Spec 9).** Not via an additional
  `Depends(...)` parameter on this function itself, but uniformly for the
  entire router: `loxone.server.build_app` wires
  `build_diagnostics_router(...)` in (like all five `/api` routers) via
  `app.include_router(..., dependencies=[Depends(guard)])`, `guard` from
  `build_api_guard` (see there). That protects every route of this router
  alike - and without the risk of accidentally leaving a future sixth
  diagnostics route unprotected, as a per-function parameter could have
  allowed.
- **None of it is logged** - neither the resolved path nor the file
  names it contains. A server log is no place for hints about key
  material, not even at `debug` level.

For the same reason - a command log that anyone who can open the
diagnostics page reads along with - `GET /api/diagnostics/commands`
deliberately NEVER carries a query string, only the path. A
`/cmd/{key}/{value}` call deliberately exposes its value openly in the
path (that is the purpose of this log: to see which value arrived) - a
query string, on the other hand, is not intended for any of today's
routes and therefore only rides along as a precaution: Task 8's token
explicitly does NOT travel as a query parameter, but as an
`Authorization` header or - for the browser WebSocket, which cannot set
its own headers - as the subprotocol `bearer, <token>` (see
`loxone.server.build_api_guard`), precisely because a secret has no
business in this log, readable by every diagnostics viewer. For the
same, more practical reason (a tight ring buffer that a polling
diagnostics tab should not flood with itself), `server.py` does not even
add calls to `/api/diagnostics/*` itself to the command log in the first
place - see there.
"""

from __future__ import annotations

import collections
import io
import logging
import socket
import sqlite3
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from loxmatter import i18n
from loxmatter.model.store import Store

if TYPE_CHECKING:
    # Exclusively for type annotations - see module docstring for why
    # `loxone.sender` is NOT imported at module level (that would be a
    # genuine circular import: sender.py imports `RingBuffer` from HERE).
    # Thanks to `from __future__ import annotations`, Python evaluates
    # annotations only as strings anyway - this block exists solely for
    # mypy.
    from loxmatter.loxone.sender import UdpSender
    from loxmatter.matter.client import BridgeMatterClient

logger = logging.getLogger(__name__)

# Public, because the UI has to assign the same file name: ever since
# downloads run via `fetch` instead of a link, the browser names the file
# itself (see `web/app.js`, `download`).
FABRIC_BACKUP_NAME = "matter-fabric-backup.zip"


class RingBuffer[T]:
    """Holds the last N entries, older ones fall out.

    A bridge runs for months. A recording that keeps growing eventually
    becomes the largest object in the process - and the interesting part
    is only the last few minutes/hours anyway. `collections.deque(maxlen=
    ...)` already handles dropping the oldest entries natively in O(1);
    this class only adds the narrow, deliberately MINIMAL surface the
    diagnostics routes need (append, iterate, count, observe) - no
    `clear()`, no index access, nothing a caller could use to manipulate
    entries after the fact.

    **A reader that could loop `for entry in ring:` while another thread
    is concurrently appending MUST first call `list(ring)` to get a
    snapshot.** Thanks to the GIL, `append` is a single, atomic C call
    (see the `diagnostics.logbuffer` module docstring) - `__iter__`
    below, however, is not: it returns a live `deque` iterator, and
    `deque` detects a mutation during an ongoing iteration. Once the ring
    is full, every further `append` evicts the oldest entry - that is
    exactly a mutation as far as this detection is concerned, and a `for`
    loop running concurrently then aborts with
    `RuntimeError('deque mutated during iteration')`. As long as each
    ring is written to from only a single path (the case so far: one
    event loop per ring), this never occurs. `diagnostics.logbuffer.
    LogBufferHandler` is the first writer that can append concurrently
    from ARBITRARY threads - for a ring it fills, a plain `for` loop is
    therefore no longer safe; `list(ring)`, however, still is, because
    that too is a single, atomic C call.

    **Observers (Task 4, Phase 5, Spec 10.5).** `add_observer`/
    `remove_observer` notify on every `append` - the same register/
    deregister shape as `LogBufferHandler.add_observer`, deliberately HERE
    rather than in yet another, dedicated class: the command log ring in
    `loxone.server` needs an observer chain (for `api.diagnostics_live`),
    but - unlike `LogBufferHandler` - has no owner type of its own that it
    could otherwise hang off (it is a mere local variable there).
    `UdpSender.add_datagram_observer`/`remove_datagram_observer` are, as
    of follow-up Task 7 (Fix 2), no longer a second, separate
    implementation of the same mechanism, but thin forwards straight to
    `add_observer`/`remove_observer` here - see the `loxone.sender`
    module docstring, section "Observer chain". An observer error is
    therefore logged and skipped at ONE place (below in `append`), no
    longer at two different ones - unlike with `LogBufferHandler`, there
    is no recursion risk here from the error logging itself, because no
    ring in this project produces log lines itself.

    **Warning for future callers:** `LogBufferHandler.entries` is also a
    `RingBuffer`, publicly readable for the snapshot - NEVER call
    `add_observer` directly on `log_handler.entries`. An observer
    registered there would run synchronously inside
    `LogBufferHandler.emit()`, while `logging.Handler.lock` is held and
    WITHOUT the reentrancy lock in place there - if this observer itself
    logs via the same logger, that is genuine, unbounded recursion (see
    the `diagnostics.logbuffer` module docstring). `LogBufferHandler.
    add_observer` is the only safe way to observe new log lines.

    **This warning does NOT apply symmetrically to `UdpSender.datagram_log`
    - an earlier version of this docstring wrongly claimed it did**
    (review fix Minor #3, 2026-09-03; recognised as wrong and corrected in
    follow-up Task 7, Fix 2). Ever since `UdpSender.add_datagram_observer`
    became a thin forward to `self._datagram_log.add_observer` (see
    above), `sender.add_datagram_observer(cb)` and
    `sender.datagram_log.add_observer(cb)` are THE SAME call on the same
    ring - there is no longer an "unsafe" and a "safe" path to
    distinguish between. An observer registered there runs synchronously
    inside `UdpSender.send`'s `async with self._lock` either way (see
    there) - a slow or hanging observer thereby slows down every
    subsequent send over the same `UdpSender`, regardless of which of the
    two (identical) paths it registered through. `UdpSender.
    add_datagram_observer`/`remove_datagram_observer` still remain as
    their own, public methods nonetheless - not for safety reasons, but
    so the type `DatagramLogEntry` stays visible in their signature and a
    caller does not need to know that the recording is internally a
    `RingBuffer` (see the `loxone.sender` module docstring)."""

    def __init__(self, maxlen: int = 500) -> None:
        self._items: collections.deque[T] = collections.deque(maxlen=maxlen)
        self._observers: list[Callable[[T], None]] = []

    def append(self, item: T) -> None:
        self._items.append(item)
        for observer in list(self._observers):
            # Iterate over a copy of the list - an observer that
            # deregisters itself during its own call must not disrupt the
            # ongoing notification of the rest (the same pattern as
            # `Runtime._notify_observers`; `UdpSender.
            # add_datagram_observer`/`remove_datagram_observer` have, as
            # of follow-up Task 7, Fix 2, hung directly off THIS `append`,
            # no separate copy of their own any more).
            try:
                observer(item)
            except Exception:
                logger.exception("Observer for a new ring-buffer entry failed - skipping it")

    def add_observer(self, callback: Callable[[T], None]) -> None:
        """Registers an observer that sees every NEW entry - not the ones
        already present (see class docstring). The observer must not
        block: `append` runs in the call path of the respective writer
        (see there)."""
        self._observers.append(callback)

    def remove_observer(self, callback: Callable[[T], None]) -> None:
        """Deregisters an observer again. An unknown observer (e.g.
        deregistered twice) is not an error but is silently ignored - the
        same rule as for `Runtime.remove_observer`."""
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True)
class DatagramLogEntry:
    """A datagram actually sent over the UDP socket - see `UdpSender.send`
    (module docstring there) for the exact recording point.

    `value` is already the finished text form (see `loxone.values.
    format_value`), not the raw `float | bool` value - the same form that
    was actually on the wire.

    `forced` takes over unchanged the `force` argument `send()` was called
    with (follow-up Task 6, 2026-09-03): `True` means "sent even though
    the value did not change" - in this project that applies to EXACTLY
    three callers, `Runtime.resend_all()`, `Runtime.resend_marked()` and
    the heartbeat (`Runtime._heartbeat_loop`). `False`, on the other hand,
    means "a genuine value change" - a pulse (`Runtime.on_event`) and its
    counter count as that too, even if both are sent within microseconds
    of each other. Exactly this distinction replaces the WebUI's earlier
    noise-filter heuristic (`app.js`, `DATAGRAM_BURST_GAP_MS`), which
    measured solely by arrival rate in the browser and thereby wrongly
    classified every rapid succession of genuine value changes as
    noise."""

    key: str
    value: str
    timestamp: str
    forced: bool


@dataclass(frozen=True)
class CommandLogEntry:
    """An incoming HTTP call with its result - see `server.py`, middleware
    `_record_command`."""

    method: str
    path: str
    status: int
    timestamp: str


class DatagramLogEntryOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    value: str
    timestamp: str


class CommandLogEntryOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: str
    path: str
    status: int
    timestamp: str


class SystemCheckOut(BaseModel):
    """A row in the system check - ALWAYS with `detail`, whether green or
    red. See module docstring, "Every red line...", and
    `test_system_check_reports_each_line_with_a_verdict`, which checks
    `detail` for EVERY row, not only for failed ones."""

    model_config = ConfigDict(frozen=True)

    name: str
    ok: bool
    detail: str


_CheckFn = Callable[[], tuple[bool, str]]


def _run_check(name: str, check: _CheckFn) -> SystemCheckOut:
    """Runs a single check and turns EVERY exception - not only the ones
    already caught by the check itself - into a red line, instead of
    letting the whole `GET /api/diagnostics/system` call abort with 500.
    See module docstring."""
    try:
        ok, detail = check()
    except Exception as exc:
        logger.exception("System check %r failed with an unexpected error", name)
        return SystemCheckOut(
            name=name,
            ok=False,
            detail=i18n.t("api.diagnostics.check_failed", exc=exc),
        )
    return SystemCheckOut(name=name, ok=ok, detail=detail)


def _check_matter_server(client: BridgeMatterClient | None) -> tuple[bool, str]:
    if client is None:
        return False, i18n.t("api.diagnostics.matter_server_not_configured")
    if not client.connected:
        return False, i18n.t("api.diagnostics.matter_server_not_connected")
    return True, i18n.t("api.diagnostics.connected")


def _check_thread_credentials(client: BridgeMatterClient | None) -> tuple[bool, str]:
    """Whether matter-server currently has the Thread credentials.

    The check that would have shown the 2026-09-04 outage: matter-server
    had been restarted the previous day at 12:55 and had thereby lost them
    - it holds them exclusively in memory (see `matter/otbr.py`). Nothing
    reported it. It only became visible when three commissioning attempts
    in a row failed, and even then the message in the UI ("Commission
    with code failed for node 7") did not name the cause - that was only
    in matter-server's log.

    Since the fix, commissioning fetches the dataset from the Border
    Router itself (see `api/devices.py`), so this point is, as a rule, no
    longer the precondition for commissioning, but the answer to the
    question "is this stack ready for a Thread device?" - answered before
    anyone is standing in front of a device in pairing mode.

    **And therefore a state line, not an alarm.** Missing credentials are
    the healthy normal state: after every restart of the Pi, matter-server
    starts without them, no one commissions anything, and the state
    resolves itself on the next commissioning. A point that would stay
    permanently red in that situation, with text explaining that there is
    nothing to do, devalues the red points beside it - the same
    consideration that deliberately leaves the `client is None` case below
    green. The alarm that genuinely calls for action sits in the `thread`
    point: it turns red when no Border Router is running at all - and then
    the next commissioning too remains without a dataset.
    """
    if client is None or not client.connected:
        # Deliberately green: that matter-server is missing is already
        # clearly stated by the check next to it (`_check_matter_server`).
        # Two red points for the same cause would spread attention across
        # two places, of which only one actually gives something to do.
        return True, i18n.t("api.diagnostics.thread_credentials_not_determinable")
    if not client.thread_dataset_set:
        # Green even though the data is missing: that is the state after
        # every restart of matter-server, and on its own it calls for no
        # action (see docstring).
        return True, i18n.t("api.diagnostics.thread_credentials_not_set")
    return True, i18n.t("api.diagnostics.thread_credentials_set")


def _check_store(store: Store) -> tuple[bool, str]:
    # sqlite3.Error, not a blind `Exception` - an unexpected kind of error
    # (a genuine bug rather than a non-writable database) deliberately
    # falls through to the safety net in `_run_check`, which then produces
    # its own, more generic red line (see module docstring).
    try:
        store.check_writable()
    except sqlite3.Error as exc:
        return False, i18n.t("api.diagnostics.store_not_writable", exc=exc)
    return True, i18n.t("api.diagnostics.writable")


# The Linux table of local IPv6 addresses. Columns: address (hex, without
# colons), interface index, prefix length, SCOPE, flags, name. Scope 0x00
# means "global" and includes the unique-local addresses (fd00::/8) - the
# very ones a Thread network runs on.
_IF_INET6 = Path("/proc/net/if_inet6")

# The name under which OTBR creates its Thread interface. Disappears along
# with the agent: if it dies (e.g. from an RCP timeout because the radio
# module stops responding), the interface is gone while the container keeps
# running - `restart: unless-stopped` does not kick in then.
_THREAD_INTERFACE_PREFIX = "wpan"


def _routed_ipv6_addresses() -> list[tuple[str, str]] | None:
    """All routed (not link-local, not loopback) IPv6 addresses of this
    host as (address, interface) - or None if this cannot be determined
    on this system.

    Reads `/proc/net/if_inet6` instead of querying a socket: a
    `connect()` test needs a TARGET, and which one you choose already
    decides the result. That is exactly what tripped up the earlier
    version of this check (see `_check_ipv6`).

    None (instead of an empty list) means "cannot be determined" - on a
    non-Linux system the file does not exist. That is different from
    "none found" and is also handled differently by the callers.
    """
    try:
        raw = _IF_INET6.read_text(encoding="ascii")
    except OSError:
        return None
    found: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        address, scope, interface = parts[0], parts[3], parts[5]
        # Only scope 00. Everything else is link-local (20), loopback (10)
        # or one of the rarer intermediate stages - none of which carries
        # a Thread network.
        if scope != "00":
            continue
        readable = ":".join(address[i : i + 4] for i in range(0, 32, 4))
        found.append((readable, interface))
    return found


def _check_ipv6() -> tuple[bool, str]:
    """Whether this host has a routed IPv6 address at all.

    **Deliberately requires NO global IPv6** (2026-09-03). The earlier
    version did: it asked the kernel for the source address for
    `2001:db8::1` and reported red if no route existed to it. On a
    healthy Thread setup that is the normal case - Thread runs over
    unique-local addresses (fd00::/8) from the prefix the Border Router
    announces, and most home networks have no global IPv6 at all. So the
    check reported an error where there was none.

    Now what actually exists counts: every address with scope "global" -
    ULA included.
    """
    if not socket.has_ipv6:
        return False, i18n.t("api.diagnostics.no_ipv6_support")
    addresses = _routed_ipv6_addresses()
    if addresses is None:
        return True, i18n.t("api.diagnostics.ipv6_not_determinable")
    if not addresses:
        return False, i18n.t("api.diagnostics.no_routed_ipv6")
    shown = ", ".join(
        i18n.t("api.diagnostics.ipv6_address_on_interface", address=address, interface=interface)
        for address, interface in addresses[:3]
    )
    more = (
        i18n.t("api.diagnostics.ipv6_more_addresses", count=len(addresses) - 3)
        if len(addresses) > 3
        else ""
    )
    return True, i18n.t("api.diagnostics.routed_ipv6_found", shown=shown, more=more)


def _check_thread() -> tuple[bool, str]:
    """Whether a Thread interface with a mesh address exists.

    This is the check that would have shown a genuine outage from
    2026-09-03: the radio module stopped responding at 14:57, the OTBR
    agent aborted with an RCP timeout, and `wpan0` disappeared - while
    the container kept running. `restart: unless-stopped` does not kick
    in in this case, because it is not the container that died, only a
    process inside it. For six and a half hours no device was reachable,
    and nothing reported it.

    Deliberately via the interface instead of via `ot-ctl`: this service
    runs in its own container and has no access to OTBR's. The
    interface, on the other hand, lives in the host's network namespace,
    which both share (`network_mode: host`), and its disappearance is
    the same signal - without this service needing privileges it does
    not otherwise need anywhere.
    """
    addresses = _routed_ipv6_addresses()
    if addresses is None:
        return True, i18n.t("api.diagnostics.thread_not_determinable")
    thread = [
        (address, interface)
        for address, interface in addresses
        if interface.startswith(_THREAD_INTERFACE_PREFIX)
    ]
    if not thread:
        return False, i18n.t("api.diagnostics.no_thread_interface", prefix=_THREAD_INTERFACE_PREFIX)
    return True, i18n.t(
        "api.diagnostics.thread_found",
        interface=thread[0][1],
        count=len(thread),
        example=thread[0][0],
    )


def _check_miniserver(sender: UdpSender | None) -> tuple[bool, str]:
    """The Miniserver does not evaluate UDP responses (Spec 6.1, see the
    server.py module docstring: "it sends and forgets") - there is no
    genuine reachability check for a fire-and-forget protocol without
    ICMP evaluation (root privileges, deliberately avoided here). This
    check therefore confirms only: there is a local routing path to the
    configured destination (the same connectionless, network-free
    technique as `_check_ipv6` above, just with the actual destination
    instead of a documentation address) - no delivery."""
    if sender is None:
        return False, i18n.t("api.diagnostics.no_udp_sender")
    host, port = sender.target
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, port))
    except OSError as exc:
        return False, i18n.t("api.diagnostics.no_network_path", host=host, port=port, exc=exc)
    return True, i18n.t("api.diagnostics.network_path_exists", host=host, port=port)


class ResendableRuntime(Protocol):
    """What this router needs from the runtime: a full resend.

    Kept narrow like `api.devices.RuntimeValues` and
    `api.live.ObservableRuntime` - each router describes its own need
    here instead of committing to `loxone.runtime.Runtime`.
    `loxone.server._RuntimeDependency` already carries the same method
    for `/resync`; both paths end up in the same implementation."""

    async def resend_all(self) -> int: ...


def build_diagnostics_router(
    store: Store,
    command_log: RingBuffer[CommandLogEntry],
    client: BridgeMatterClient | None,
    sender: UdpSender | None,
    matter_data_dir: Path | None,
    runtime: ResendableRuntime,
) -> APIRouter:
    """Builds the `APIRouter` for `/api/diagnostics/*` (Spec 10.5).

    `client`, `sender` and `matter_data_dir` may be `None` - `build_app`
    already defaults `client`/`sender` to `None` (for the same reason
    documented there in `loxone.server`: existing callers should keep
    running unchanged). `None` here means in each case "this part of the
    diagnostics is not available for this run", not "the diagnostics as a
    whole is missing" - `/datagrams` then returns an empty list, `/system`
    a red line with a pointer, `/fabric-backup` a 503 instead of a
    500/empty ZIP."""
    router = APIRouter(prefix="/api/diagnostics")

    @router.get("/datagrams")
    async def datagrams(
        device_id: int | None = Query(
            None, description="Only datagrams of this device (key prefix d<id>_)"
        ),
    ) -> list[DatagramLogEntryOut]:
        if sender is None:
            return []
        prefix = f"d{device_id}_" if device_id is not None else None
        return [
            DatagramLogEntryOut(key=entry.key, value=entry.value, timestamp=entry.timestamp)
            for entry in sender.datagram_log
            if prefix is None or entry.key.startswith(prefix)
        ]

    @router.get("/commands")
    async def commands() -> list[CommandLogEntryOut]:
        return [
            CommandLogEntryOut(
                method=entry.method, path=entry.path, status=entry.status, timestamp=entry.timestamp
            )
            for entry in command_log
        ]

    @router.get("/system")
    async def system() -> list[SystemCheckOut]:
        return [
            _run_check("matter-server", lambda: _check_matter_server(client)),
            # Fixed id, no translation - like "matter-server", "store",
            # "ipv6", "thread" and "miniserver" next to it: the UI
            # displays `check.name` unchanged (index.html), and the line
            # below it refers to the neighbouring point `thread` in
            # running text. A name that changes with the language would
            # no longer be findable in a log or a bug report. English
            # instead of the earlier "thread-zugangsdaten", so the ids
            # all speak the same language among themselves.
            _run_check("thread-credentials", lambda: _check_thread_credentials(client)),
            _run_check("store", lambda: _check_store(store)),
            _run_check("ipv6", _check_ipv6),
            _run_check("thread", _check_thread),
            _run_check("miniserver", lambda: _check_miniserver(sender)),
        ]

    @router.post("/resync")
    async def resync() -> dict[str, int]:
        """The resync button in the system tab: resends all known values.

        The same effect as `GET /resync` in `loxone.server` (Spec 6.4) and
        as a bridge start - just a different trigger, hence the same
        response shape `{"sent": n}` and the same error message. Two
        separate routes, because the two callers differ in access and in
        nothing else: `/resync` must stay open because the Miniserver
        cannot send an `Authorization` header, while this route, like
        every `/api` route, sits behind the guard (see module docstring).
        A shared endpoint would have to give up one of the two
        properties.

        POST instead of GET: the route has an effect. That `/resync` is a
        GET is not a model to follow, but a limitation of the Miniserver.
        """
        try:
            count = await runtime.resend_all()
        except Exception as exc:  # e.g. a UdpSender whose socket is already closed
            # The same separation as for `/resync` and `/cmd`: the full
            # traceback into the server log, the response carries only
            # the message. The difference between a dead sender and a
            # program bug in `resend_all` would otherwise be preserved
            # nowhere.
            logger.exception("Full resend via /api/diagnostics/resync failed")
            raise HTTPException(
                status_code=502, detail=i18n.t("api.server.fail_resync", exc=exc)
            ) from exc
        # English key in the wire format, identical wording to `/resync`:
        # the UI reads `sent` and turns it into a toast.
        return {"sent": count}

    @router.get("/fabric-backup")
    async def fabric_backup() -> Response:
        """**WHOEVER CAN CALL THIS ROUTE CAN TAKE OVER THE FABRIC.** That
        is the first sentence of this docstring on purpose.

        The protection does not sit on this function but uniformly on the
        entire router (`loxone.server.build_api_guard`): without a valid
        session cookie and without a valid bearer token, the call ends
        with 401 before this function even runs.

        **The 403 branch that used to be here has been removed** (WebUI
        login, Spec 11). It defended against the case "the service runs
        with no access control at all, so every `/api` route is open" -
        that exact case no longer exists: without a password set, the
        guard allows no `/api` route through, and whoever arrives here has
        presented proof. An unreachable branch whose docstring describes a
        situation that no longer exists would be worse than no branch:
        the next reader would rely on a condition that checks nothing any
        more. That the guard actually hangs off EVERY one of the five
        routers is checked router by router by
        `tests/api/test_security.py`, instead of relying on the shared
        prefix.

        503 remains for "the data directory is not mounted or does not
        exist" (below) - a configuration gap that would only just create
        this capability in the first place.

        Backup of the matter-server data directory (Spec 4.1, 8) as a
        download.

        Deliberately logs NOTHING - neither the resolved path nor the
        file names it contains (see module docstring)."""
        # Deliberately no `logger` call anywhere in this whole function,
        # not even in the two error branches below: even the configured
        # PATH is a hint about the storage layout of the fabric
        # credentials (see module docstring) - even a failing call should
        # not write it to the log.
        if matter_data_dir is None:
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.diagnostics.fabric_backup_not_mounted"),
            )
        if not matter_data_dir.is_dir():
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.diagnostics.fabric_backup_dir_missing"),
            )

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(matter_data_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(matter_data_dir)))

        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{FABRIC_BACKUP_NAME}"'},
        )

    return router
