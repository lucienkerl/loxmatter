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

"""`ZigbeeSource` - zigpy behind the `DeviceSource` boundary.

Satisfies the protocol without an adapter, exactly as `BridgeMatterClient`
does (boundary design section 3.3). One class per technology, nothing
wrapped around it.

**bellows keeps its own event loop in its own thread** (`use_thread=True`,
the default) and proxies calls both ways. That is kept deliberately: ASH
acknowledges frames on a deadline and loxmatter's main loop does synchronous
SQLite work, so a blocked main loop would otherwise cause NCP resets
(research E.1). Home Assistant runs it the same way. The consequence is that
`disconnect()` MUST always run - the thread is a non-daemon worker, and
without the shutdown it leaks and the stick is left mid-frame.

**zigpy and bellows never reconnect by themselves.** A lost link surfaces
exactly once, as the listener event `connection_lost(exc)`; bellows' watchdog
(every 10 s, four consecutive failures) turns a wedged NCP into the same
event. So `sources/supervisor.py`'s existing loop is exactly right, and the
only work here is making that event reach it.

This module and `configure.py` are the only files in loxmatter that reach
into zigpy, and they do it **lazily, inside functions**: importing the radio
libraries costs real time, and an installation with no Zigbee stick should
never pay it. Two consequences of that rule are worth stating, because both
were decided rather than fallen into:

- **Failures are matched by NAME, over the whole MRO** (`_is_unreachable`).
  Naming `zigpy.exceptions.DeliveryError` in an `except` clause would drag
  the import to module level, and the fake-driven suite could not exercise
  the mapping at all. The price is that a typo in a name would be invisible
  to a test that only ever sees the fakes - so
  `tests/zigbee/test_zigpy_names.py` checks every name here against the
  installed library, and that is the test that has to stay.
- **zigpy's configuration keys are spelled out as constants below** rather
  than imported from `zigpy.config`, for the same reason and with the same
  guard: the same test builds a real config from them and runs it through
  zigpy's own schema.

Everything this module reads out of a zigpy device goes through the public
surface - `Device.non_zdo_endpoints`, `Endpoint.in_clusters`,
`Cluster.attributes`, `Cluster.get` - and never through `Cluster._attr_cache`,
which is an `AttributeCache` object in zigpy 2.2.0 and offers no way to
enumerate what it holds. `Cluster.get` is always handed the attribute
DEFINITION and never the bare id; `_endpoint_facts` says why, and the reason
is a `KeyError` that would stop the bridge from starting.

**`snapshots()` can be slow, once per connection.** `_snapshot` reads
`ColorCapabilities` over the air for any colour endpoint that has none
cached, and `snapshots()` walks the devices serially - so a reconnect with
several unresponsive colour endpoints pays one zigpy timeout after another
before `attach()` returns. It is bounded: `_capabilities_asked` makes it at
most one read per endpoint per connection, and a lamp switched off at the
wall costs that once rather than on every report.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot, Technology
from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.radios.inventory import under_host_dev
from loxmatter.sources import DeviceCall, DeviceUnreachableError, RuntimeEventHandler
from loxmatter.timestamps import now_iso
from loxmatter.zigbee.availability import AvailabilityChecker, is_available
from loxmatter.zigbee.configure import (
    PollingLoop,
    PollingSchedule,
    configure_device,
    watch_for_wakeups,
)
from loxmatter.zigbee.quirks import ensure_quirks_loaded
from loxmatter.zigbee.translate import DeviceFacts, EndpointFacts, build_snapshot, rename_payload

logger = logging.getLogger(__name__)

__all__ = [
    "PERMIT_JOIN_GRACE_SECONDS",
    "PERMIT_MAX_SECONDS",
    "ZIGBEE_CHANNELS",
    "ApplicationFactory",
    "ConnectionProgress",
    "ConnectionState",
    "CoordinatorInfo",
    "PairingRow",
    "PairingState",
    "ZigbeeSource",
    "ZigbeeUnavailableError",
    "channels_excluding",
]


# Asked with the stick's path before `connect()` touches the port; a
# message is the reason not to open it. See `radios/thread_lockout.py`.
OpenGuard = Callable[[str], "i18n.Message | None"]


class _OpenRefusedError(RuntimeError):
    """The open guard said no. Carries its sentence to `_startup_message`,
    so a refusal travels the same path - progress, attempt count,
    supervisor backoff - as a stick that would not open."""

    def __init__(self, message: i18n.Message) -> None:
        super().__init__(message.key)
        self.message = message


class ZigbeeUnavailableError(RuntimeError):
    """The radio could not be brought up.

    Carries a translated message, resolved when it is raised: the one
    reader of this exception is the supervisor's log line, right then.
    `progress().error` - the copy the radios card polls for as long as the
    supervisor keeps retrying - is kept as an `i18n.Message` instead and
    resolved when `GET /api/zigbee/radio` answers, so a language switch
    reaches it at once. Deliberately not a `DeviceUnreachableError` - no
    device was asked; there is no radio to ask one with."""


# --------------------------------------------------------------- constants --

# zigpy's configuration keys. Spelled out rather than imported so that
# building a config costs no zigpy import (see the module docstring);
# `tests/zigbee/test_zigpy_names.py` asserts each one equals the
# corresponding `zigpy.config.CONF_*` and that the result validates against
# `ControllerApplication.SCHEMA`.
CONF_DEVICE: Final = "device"
CONF_DEVICE_PATH: Final = "path"
CONF_DEVICE_BAUDRATE: Final = "baudrate"
CONF_DEVICE_FLOW_CONTROL: Final = "flow_control"
CONF_DATABASE: Final = "database_path"
CONF_NWK: Final = "network"
CONF_NWK_CHANNELS: Final = "channels"
CONF_NWK_VALIDATE_SETTINGS: Final = "validate_network_settings"
CONF_OTA: Final = "ota"
CONF_OTA_ENABLED: Final = "enabled"
CONF_TOPO_SCAN_ENABLED: Final = "topology_scan_enabled"

# The four ZigBee channels a coordinator may form on. zigpy's own default
# set; the Thread channel is removed from it in `channels_excluding`.
ZIGBEE_CHANNELS: Final[tuple[int, ...]] = (11, 15, 20, 25)

# zigpy asserts `0 <= time_s <= 254` in `ControllerApplication.permit`, and
# no unlimited mode is offered: Zigbee2MQTT removed its permanent option in
# 2.0 as a security concern (design 3.1).
PERMIT_MAX_SECONDS: Final = 254

# How long after a window has closed a device that turns up is still counted
# as a join rather than as a device that was already on the network.
#
# A device announces itself through its parent, and the announcement crosses
# the mesh before it reaches the coordinator, so one sent inside the window
# can arrive after it. Without this, the device the user is standing in
# front of - the last one to squeeze into a 254 s window - is the one the
# tab labels "was already here", which is both false and exactly backwards:
# `discovered` exists to keep the bridge from claiming a join it did not
# see, not to deny one it did.
PERMIT_JOIN_GRACE_SECONDS: Final = 10

# The four attribute events a cluster emits (`zigpy.zcl`,
# `AttributeReportedEvent` and its siblings). All four, not just the report:
# a value read during configure-on-join and a value written by this bridge
# are just as much news for Loxone as one the device sent by itself.
ATTRIBUTE_EVENTS: Final[tuple[str, ...]] = (
    "attribute_report",
    "attribute_read",
    "attribute_updated",
    "attribute_written",
)

# ColorCapabilities. The one attribute the interview does not read and the
# colour controls depend on - see `_read_colour_capabilities`.
_COLOR_CONTROL_CLUSTER: Final = 0x0300
_COLOR_CAPABILITIES_ATTRIBUTE: Final = 0x400A

# A ZCL status of 0 is SUCCESS; everything else is a device that was reached
# and refused.
_ZCL_SUCCESS: Final = 0

# The exception names that mean "asked, got nothing back", matched against
# every class in a raised exception's MRO. `ZigbeeException` and
# `RadioException` are zigpy's two roots and would be enough on their own;
# the three leaves are named as well because they are the ones the design
# and every bug report will call the failure by.
_UNREACHABLE_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ZigbeeException",
        "RadioException",
        "DeliveryError",
        "ControllerException",
        "InvalidResponse",
        "TimeoutError",
    }
)

# Which radio library opens which stick. Imported by name, at call time:
# `bellows` and `zigpy_znp` each pull in a serial stack, and an installation
# without a Zigbee stick never calls this.
_RADIO_MODULES: Final[dict[str, str]] = {
    "ezsp": "bellows.zigbee.application",
    "znp": "zigpy_znp.zigbee.application",
    "deconz": "zigpy_deconz.zigbee.application",
}


def _named(exc: BaseException, name: str) -> bool:
    """Whether `name` appears anywhere in the exception's class hierarchy."""
    return any(cls.__name__ == name for cls in type(exc).__mro__)


def _is_unreachable(exc: BaseException) -> bool:
    return any(_named(exc, name) for name in _UNREACHABLE_EXCEPTION_NAMES)


# Spec 4.6's table, in order. `OSError` is the base class of both
# `FileNotFoundError` and `PermissionError`, so the EBUSY row must match on
# `errno` and must come after the two specific ones; `TimeoutError` is an
# `OSError` as well, with `errno is None`, which is why the busy row cannot
# swallow it.
_STARTUP_MESSAGES: Final[tuple[tuple[Callable[[BaseException], bool], str], ...]] = (
    (lambda exc: isinstance(exc, FileNotFoundError), "api.errors.zigbee_stick_missing"),
    (lambda exc: isinstance(exc, PermissionError), "api.errors.zigbee_no_device_permission"),
    (
        lambda exc: isinstance(exc, OSError) and exc.errno == errno.EBUSY,
        "api.errors.zigbee_stick_busy",
    ),
    (lambda exc: isinstance(exc, TimeoutError), "api.errors.zigbee_not_a_coordinator"),
    (
        lambda exc: _named(exc, "NetworkSettingsInconsistent"),
        "api.errors.zigbee_network_mismatch",
    ),
)


def channels_excluding(thread_channel: int | None) -> list[int]:
    """The channels a Zigbee network may form on, minus the one OTBR is
    already using.

    Zigbee and Thread share the 2.4 GHz band and, on the test Pi, the same
    host. Forming on the border router's channel is the one collision here
    that costs nothing to avoid.

    A missing border router leaves the list whole - it must never stop
    Zigbee from forming - and so does a Thread channel that is not one of
    the four candidates anyway.

    The input comes from `matter/otbr.py`'s `current_thread_channel`, which
    reads the channel out of the border router's active dataset. The source
    asks it once, on its first connect (`ZigbeeSource._learn_thread_channel`),
    before any application exists and so before anything can form."""
    if thread_channel is None:
        return list(ZIGBEE_CHANNELS)
    return [channel for channel in ZIGBEE_CHANNELS if channel != thread_channel]


# `applying` is the ONE state no `ZigbeeSource` ever sets. It belongs to
# `ZigbeeRuntime` and covers the window in which there is no source to ask:
# the old one has been disconnected and the new one is still being built.
# It lives in this Literal rather than in a second enum beside it because
# the card reads ONE `progress` object and switches on ONE field; a parallel
# "is a change in flight" boolean would be a second thing to forget.
ConnectionState = Literal[
    "idle", "applying", "loading_quirks", "opening_radio", "connected", "failed"
]


@dataclass(frozen=True)
class ConnectionProgress:
    """How far the current or last connection attempt got.

    Read by `GET /api/zigbee/radio` so the radios card can show
    what is happening while the supervisor works its 1 s -> 60 s backoff.
    `attempts` counts FAILED attempts, so the card can say "still trying, 4
    attempts" rather than implying a first try that is about to succeed.

    `error` is the sentence of the most recent failure, as an
    `i18n.Message` and NOT as text: the card polls it for as long as the
    supervisor keeps retrying, and a sentence translated when the attempt
    failed stayed in the language of that moment after the user switched -
    next to the browser's own wrapper around it, already in the new one.
    `as_json()` translates it when the route answers. It is kept through
    the retry that follows a failure (`attempts > 0` in a working state),
    so the card can say why it is trying again, and dropped once the stick
    connects or a new setting starts from scratch.

    **The card keeps polling while the state is `applying`,
    `loading_quirks` or `opening_radio`** - three, not the plan's two. See
    `ZigbeeRuntime.progress()` for what the third one is and why leaving it
    out reported a running radio change as `idle`."""

    state: ConnectionState
    attempts: int
    error: i18n.Message | None
    changed_at: str

    def as_json(self) -> dict[str, object]:
        """The body the radios card reads, with `error` translated NOW."""
        return {
            "state": self.state,
            "attempts": self.attempts,
            "error": None if self.error is None else self.error.text(),
            "changed_at": self.changed_at,
        }


@dataclass(frozen=True)
class CoordinatorInfo:
    """What the stick said about itself on the last successful connect.

    Read from `app.state.node_info` after `startup()`, which each radio
    library fills while loading the network: bellows puts the EmberZNet
    stack version into `version` ("7.4.4.0 build 0", with the build string
    where the firmware offers one) and the manufacturing tokens into
    `manufacturer` and `model`. bellows logs the stack version itself only
    at DEBUG, and the bridge logs at INFO, so without this nobody could
    read the firmware off a running bridge. Any field a library leaves
    unset is `None`, never a guess."""

    radio_type: str
    manufacturer: str | None
    model: str | None
    firmware: str | None

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def _node_info_text(node_info: Any, name: str) -> str | None:
    value = getattr(node_info, name, None)
    return value if isinstance(value, str) and value else None


def _network_text(network_info: Any, name: str) -> str:
    """One field of `zigpy.state.NetworkInfo` for the connect line. The PAN
    ID is shown the way zigpy's own `PanId` prints (`0x1A62`); a field a
    library leaves unset reads "unknown"."""
    value = getattr(network_info, name, None)
    if value is None:
        return "unknown"
    if name == "pan_id" and isinstance(value, int):
        return f"0x{int(value):04X}"
    return str(value)


def _network_backup_known(app: Any) -> bool | None:
    """Whether zigpy's database already holds a network backup, or `None`
    for an application that offers no backup manager."""
    most_recent = getattr(getattr(app, "backups", None), "most_recent_backup", None)
    if most_recent is None:
        return None
    return most_recent() is not None


PairingState = Literal["joined", "interviewing", "ready", "failed"]


@dataclass(frozen=True)
class PairingRow:
    """One device on the pairing tab, keyed by IEEE (design 3.1).

    Keyed by IEEE and not by NWK: a rejoining device gets a new short
    address and would otherwise appear twice, once under each.

    `discovered` is the distinction section 4.8 insists on: zigpy interviews
    devices of an ADOPTED network on its own, so a device can appear when
    nobody opened a join window, and calling that "a device joined just now"
    would be false.

    The states this file produces are the four the source itself can see.
    The two beside them that design 3.1's table promises - "configuring" and
    "waiting to wake" - are NOT stored here and deliberately so: they are
    computed per request by `api/zigbee.py`'s pairing route, which overlays
    them onto a `"ready"` row from `configuring_addresses()` (a live
    configuration pass) and from `store.zigbee_pending.addresses_with_pending()`
    (a cluster still owed after one). The second one has to be read from
    disk on every request rather than remembered here, because the pending
    row it comes from is written BEFORE the attempt and outlives a bridge
    restart - a flag on this row would be silently wrong after one, for
    exactly the sleeping battery sensor it matters most for. "stuck" is not
    a state at all but an age, computed by that same route from `changed_at`
    against `is_mains_powered`.

    `is_mains_powered` is read the same conservative way `_facts` reads it:
    a device with no node descriptor yet counts as battery powered, which
    buys it the longer of the two stuck thresholds instead of calling a
    sleepy device stuck 30 s early."""

    ieee: str
    state: PairingState
    manufacturer: str
    model: str
    quirk_applied: bool
    discovered: bool
    changed_at: str
    is_mains_powered: bool = False


# What `connect()` needs to build an application. One argument, the
# validated config; everything else about the radio is already in it.
ApplicationFactory = Callable[[dict[str, Any]], Awaitable[Any]]


def _radio_module(radio_type: str) -> ModuleType:
    """The radio library for a fingerprinted stick, imported on demand.

    `importlib` rather than a plain import, and not only for laziness:
    `bellows` and `zha` ship no `py.typed`, so a direct import would need a
    mypy exception per module, while the application object is `Any` to this
    file either way.

    SYNCHRONOUS and blocking - measured at 0.44 s for `bellows` on an M1, so
    roughly 2-3 s on a Pi 4. `_default_application` therefore calls it
    through the default executor, for the reason `quirks.py` gives for the
    registry warm-up: this lands on the reconnect path, and the first time a
    radio is configured from the web UI the event loop would otherwise stop
    answering `/health` for the duration."""
    import importlib

    try:
        module_name = _RADIO_MODULES[radio_type]
    except KeyError:  # pragma: no cover - `RadioType` has no fourth value
        raise ValueError(f"unknown radio type {radio_type!r}") from None
    return importlib.import_module(module_name)


async def _default_application(config: dict[str, Any]) -> Any:
    """Builds a `ControllerApplication` WITHOUT starting its radio.

    `start_radio=False` loads zigpy's database and nothing else (research
    E.2), so the catalogue is known even when the stick is missing; the
    radio is opened by the separate `startup(auto_form=True)` in `connect()`,
    which is the call whose failure has to be told apart.

    The device resolver is `zhaquirks.ZHA_DEVICE_REGISTRY.resolve` and NOT
    the `DEVICE_REGISTRY.resolve` the design names: in the installed
    zha-quirks, `DEVICE_REGISTRY` is the legacy v1 registry and has no
    `resolve` at all - the unified one that applies both quirk generations,
    and that marks what it transformed with `_quirk_registry_entry`, is
    `ZHA_DEVICE_REGISTRY`. Verified against the installed package, not read
    off the document.

    The radio library is imported OFF the loop (`run_in_executor`), the way
    `quirks.py` warms the registry up: `import bellows.zigbee.application`
    costs 0.44 s on an M1 and several times that on a Pi, and this runs on
    every reconnect attempt."""
    import zhaquirks

    application_class = (
        await asyncio.get_running_loop().run_in_executor(None, _radio_module, config["_radio_type"])
    ).ControllerApplication
    return await application_class.new(
        {key: value for key, value in config.items() if not key.startswith("_")},
        start_radio=False,
        device_resolver=zhaquirks.ZHA_DEVICE_REGISTRY.resolve,
    )


class _ApplicationListener:
    """What zigpy calls on `add_listener()`, by method name.

    A separate object rather than the source itself: `ControllerApplication`
    calls whatever method matches the event's name, and `DeviceSource`'s own
    surface (`connect`, `remove`, `send`, ...) must not be reachable that
    way by accident.

    Every method here is called from zigpy's thread-proxied event path and
    must return at once - so they only record and enqueue."""

    def __init__(self, source: ZigbeeSource) -> None:
        self._source = source
        self.application: Any = None

    def connection_lost(self, exc: BaseException | None = None) -> None:
        self._source._handle_connection_lost(self.application, exc)

    def device_joined(self, device: Any) -> None:
        self._source._handle_device_event(device, "joined")

    def raw_device_initialized(self, device: Any) -> None:
        self._source._handle_device_event(device, "interviewing")

    def device_initialized(self, device: Any) -> None:
        self._source._handle_device_event(device, "ready")

    def device_init_failure(self, device: Any) -> None:
        """An interview that gave up - the one state ZHA does not have.

        **Dispatched to the APPLICATION's listeners, not to the device's**,
        even though it is `zigpy.device.Device.initialize` that emits it:
        that method calls `self.application.listener_event(
        "device_init_failure", self)` in both of its `except` branches
        (verified against the installed zigpy 2.2.0, and pinned by
        `tests/zigbee/test_zigpy_names.py` - which for this one event has to
        read `zigpy.device` rather than `zigpy.application`). An earlier
        note in this file claimed the opposite, and a handler placed on the
        device where that note said would never have been called once.

        Routed through the ordinary event path rather than through a
        "mark the row failed" helper, so that a device whose very first
        interview fails still gets a row with its `discovered` flag decided
        the same way every other row's is."""
        self._source._handle_device_event(device, "failed")

    def device_reinterviewed(self, device: Any) -> None:
        """A device that was interviewed AGAIN - a new object, same IEEE.

        zigpy's `_device_reinterviewed` calls `old_device.on_remove()` and
        then `_finalize_device(shadow)`, which builds a brand-new device with
        brand-new cluster objects, and then emits THIS event deliberately
        instead of `device_initialized` (its own comment says so). Without a
        method by this name nothing re-binds the cluster listeners: the
        replacement's clusters have none, the original's are on an object
        zigpy has dropped, and the device reports nothing until the next
        `connect()` while `connected` still says the link is fine.

        Handled as "ready" - which it is - so the pairing row, the
        re-listening and the fresh snapshot all go through the one path that
        already does those three things."""
        self._source._handle_device_event(device, "ready")

    def device_removed(self, device: Any) -> None:
        self._source._handle_device_removed(device)


class ZigbeeSource:
    """Zigbee as a `DeviceSource`.

    Everything zigpy-shaped stops here: what leaves this class is a
    `NodeSnapshot` with Matter paths, a `DeviceUnreachableError`, or a
    `ZigbeeUnavailableError` with a sentence a person can act on."""

    def __init__(
        self,
        *,
        path: str,
        fingerprint: Fingerprint,
        database: Path,
        application_factory: ApplicationFactory = _default_application,
        on_connection_change: Callable[[bool], Awaitable[None]] | None = None,
        thread_channel: int | None = None,
        store: Any | None = None,
        open_guard: OpenGuard | None = None,
        host_dev: Path | None = None,
        thread_channel_lookup: Callable[[], Awaitable[int | None]] | None = None,
    ) -> None:
        # The stick as the HOST names it - what the Thread guard compares,
        # what the log line prints and what the API reports. Never what zigpy
        # opens: see `_open_path`.
        self._path = path
        # Where the host's `/dev` is mounted inside this container, or `None`
        # where nothing is mounted (tests about other things, and a bridge
        # started by hand outside the container).
        self._host_dev = host_dev
        # `None` only where nothing is guarding - tests about other things.
        # Production passes `thread_lockout.open_refusal` through
        # `build_zigbee_source`, so the stored stick is checked against the
        # Thread report on every open, not only when it was chosen: the
        # choice may be months old, and Thread may have moved since.
        self._open_guard = open_guard
        self._fingerprint = fingerprint
        self._database = Path(database)
        self._application_factory = application_factory
        self._on_connection_change = on_connection_change
        self._thread_channel = thread_channel
        # Asks the border router for the channel to keep a new network off,
        # once per source, on the first connect - see `_learn_thread_channel`.
        # `None` once it has answered, and in tests that pass the channel
        # itself.
        self._thread_channel_lookup = thread_channel_lookup
        # loxmatter's own store, for configure-on-join's pending table
        # (`zigbee/configure.py`). Optional, and `None` in every test that
        # is not about configuration: without it a joining device is
        # delivered to the handler exactly as before and nothing is bound -
        # which is honest, because with no table to defer into there would
        # be nowhere to record a sleeping device's unfinished business.
        self._store = store
        self._polling = PollingSchedule()

        self._app: Any | None = None
        self._coordinator: CoordinatorInfo | None = None
        self._connected = False
        self._link_lost = asyncio.Event()
        self._progress = ConnectionProgress(
            state="idle", attempts=0, error=None, changed_at=now_iso()
        )

        self._queue: asyncio.Queue[str] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._handler: RuntimeEventHandler | None = None
        self._resolve_device_id: Callable[[str], int | None] | None = None
        # Rebuilt on every `subscribe()`, the same as the cluster listeners
        # just below: `attach()` runs on every reconnect, and a fresh
        # checker starts every device's grace counters clean rather than
        # carrying stale counts across a rebuild it had no part in. Started
        # there too, and stopped by `disconnect()` and by the next
        # `subscribe()` before it builds its replacement - whoever starts a
        # background loop owns its stop.
        self._availability_checker: AvailabilityChecker | None = None
        # The availability sweep's sibling: reads `PollingSchedule.due` for
        # the clusters that refused a reporting configuration. Same
        # lifecycle, for the same reason.
        self._polling_loop: PollingLoop | None = None
        # Per device, so one device can be re-bound without disturbing the
        # rest - a reinterview replaces exactly one device object.
        self._unsubscribers: dict[str, list[Callable[[], None]]] = {}
        # Address -> the device OBJECT the listeners sit on. Not a set of
        # addresses: a reinterviewed device keeps its IEEE and gets new
        # clusters, so the address alone cannot answer "already listening".
        self._listening: dict[str, Any] = {}
        # The last set of paths and values each device was told to the
        # handler with. A path that is not in here has no signal row yet,
        # and a value that is has to go through `on_attribute` rather than
        # through a fresh snapshot - see `_deliver`.
        self._delivered: dict[str, dict[str, Any]] = {}
        # Devices whose ColorCapabilities were asked for on this connection.
        # Cleared on every connect, so a lamp that was switched off at the
        # wall is asked again after a reconnect, and not before.
        self._capabilities_asked: set[tuple[str, int]] = set()

        self._pairing: dict[str, PairingRow] = {}
        # The END of the last join window that was opened, or `None` if none
        # ever was. NOT "the window is open": a window that has run out, and
        # one the user stopped, both leave their end time here, because
        # `_join_came_from_a_window` needs to know when it was for
        # `PERMIT_JOIN_GRACE_SECONDS`. "Is it open" is a comparison against
        # the clock (`_window_is_open`), and `permit_until()` is what the
        # API reports.
        self._permit_until: datetime | None = None
        # One join window at a time. Two overlapping `permit()` calls -
        # "Keep open longer" pressed while a "Stop" is still in flight, two
        # tabs, a phone and a laptop - each await a broadcast plus a call
        # into the NCP, and whichever one the radio finished LAST is the one
        # the network is actually in. Without this lock the loser can still
        # write its own end time afterwards, and the tab then counts down
        # four minutes on a network that is shut.
        self._permit_lock = asyncio.Lock()
        # Every address `_configure_then_deliver` is currently running
        # configure-on-join for. The pairing route in `api/zigbee.py` overlays
        # "configuring" onto a "ready" row for exactly these addresses
        # (design 3.1's table) - nothing else needs to know about this set.
        self._configuring: set[str] = set()
        # Tasks a synchronous listener starts. Held so the garbage collector
        # cannot take one mid-flight (the `_pulse_tasks` pattern in
        # `loxone/runtime.py`).
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------ identity --

    @property
    def technology(self) -> Technology:
        return "zigbee"

    @property
    def connected(self) -> bool:
        """Whether the link to the coordinator currently HOLDS.

        An explicit flag, set in `connect()` and cleared by the
        `connection_lost` listener and by `disconnect()`. Deliberately NOT
        `bellows.is_controller_running`, which is never cleared on a lost
        link (R1 section 2) and would report a dead radio as healthy for as
        long as the process lives - the 8 September outage, one layer
        down."""
        return self._connected

    def progress(self) -> ConnectionProgress:
        return self._progress

    def coordinator(self) -> CoordinatorInfo | None:
        """The stick's own description from the last successful connect,
        or `None` before there was one."""
        return self._coordinator

    def _note_coordinator(self, app: Any, *, network_was_known: bool | None) -> None:
        """Records and logs, once per successful connect, what the radio
        reported about itself - the firmware version above all, which the
        hardware checklist asks for and which bellows would otherwise log
        only at DEBUG.

        **And which network it is running.** zigpy adopts a network a stick
        already carries - its channel and key, past the Thread-channel
        exclusion - and says whether it formed or adopted only at INFO on
        its own logger, which the bridge does not show. The line therefore
        carries the channel and PAN IDs from `app.state.network_info`, and
        whether zigpy's database held a network backup before the port was
        opened (`network_was_known`, read by `connect()`): a network known
        from the database was restored or validated against it, one that was
        not is new to this bridge - formed during this connect or adopted
        from the stick, which zigpy's public state does not tell apart.
        Never the network key."""
        state = getattr(app, "state", None)
        node_info = getattr(state, "node_info", None)
        network_info = getattr(state, "network_info", None)
        self._coordinator = CoordinatorInfo(
            radio_type=self._fingerprint.radio_type,
            manufacturer=_node_info_text(node_info, "manufacturer"),
            model=_node_info_text(node_info, "model"),
            firmware=_node_info_text(node_info, "version"),
        )
        if network_was_known is None:
            known = "whether this bridge's database knew it is unknown"
        elif network_was_known:
            known = "already in this bridge's database"
        else:
            known = "not in this bridge's database before (formed now, or adopted from the stick)"
        logger.info(
            "Zigbee coordinator connected on %s: radio type %s, firmware %s, "
            "manufacturer %s, model %s; network on channel %s, PAN ID %s, "
            "extended PAN ID %s, %s",
            self._path,
            self._coordinator.radio_type,
            self._coordinator.firmware or "unknown",
            self._coordinator.manufacturer or "unknown",
            self._coordinator.model or "unknown",
            _network_text(network_info, "channel"),
            _network_text(network_info, "pan_id"),
            _network_text(network_info, "extended_pan_id"),
            known,
        )

    def pairing_rows(self) -> list[PairingRow]:
        """The pairing tab's rows, oldest first."""
        return list(self._pairing.values())

    # ----------------------------------------------------------- lifecycle --

    def _set_progress(
        self,
        state: ConnectionState,
        *,
        error: i18n.Message | None = None,
        attempts: int | None = None,
        count_attempt: bool = False,
    ) -> None:
        if count_attempt:
            attempts = self._progress.attempts + 1
        elif attempts is None:
            attempts = self._progress.attempts
        self._progress = ConnectionProgress(
            state=state, attempts=attempts, error=error, changed_at=now_iso()
        )

    def _open_path(self) -> str:
        """The name zigpy opens the stick by.

        The stored path is the HOST's `/dev/serial/by-id/...`, and the
        bridge's container has no such directory: its own `/dev` is
        Docker's private tmpfs, and the host's `/dev` is mounted at
        `host_dev`. Handed the host path, zigpy failed every open with
        `FileNotFoundError`, which the card reported as a stick that is no
        longer there while listing it as present. So the node under the
        mount is opened - rewritten by `radios.inventory.under_host_dev`,
        the same function the Thread guard resolves through.

        **The given path is kept when the mapped node does not exist.** A
        bridge run outside the container has no mount, and
        `--zigbee-device /dev/ttyUSB0` there names a node that is really at
        `/dev/ttyUSB0`. A stick that is missing under the mount and at its
        own path fails either way, with the same "stick missing" sentence.

        Decided on every open rather than once, because a stick plugged in
        after the source was built appears under the mount later."""
        if self._host_dev is None:
            return self._path
        mapped = under_host_dev(self._path, self._host_dev)
        return mapped if mapped != self._path and os.path.exists(mapped) else self._path

    def _config(self) -> dict[str, Any]:
        """The zigpy configuration, every non-default value with its reason.

        `_radio_type` is this module's own key, stripped again by
        `_default_application` before zigpy's schema ever sees the dict; it
        travels here so a test can read which stick the source would have
        opened without opening one."""
        return {
            "_radio_type": self._fingerprint.radio_type,
            CONF_DEVICE: {
                CONF_DEVICE_PATH: self._open_path(),
                CONF_DEVICE_BAUDRATE: self._fingerprint.baudrate,
                # "hardware" or "software", straight from the fingerprint
                # table: zigpy maps them to rtscts and xonxoff respectively
                # (`zigpy.serial`), and probing for it is exactly what the
                # table exists to avoid.
                CONF_DEVICE_FLOW_CONTROL: self._fingerprint.flow_control,
            },
            # Next to the store in the same volume (design 8.5).
            CONF_DATABASE: str(self._database),
            # A stick that already carries a network is adopted by zigpy
            # unless this is on; with it, a mismatch against the stored
            # backup raises `NetworkSettingsInconsistent` and the bridge
            # stops instead of overwriting somebody's network (section 4.8).
            CONF_NWK_VALIDATE_SETTINGS: True,
            CONF_NWK: {CONF_NWK_CHANNELS: channels_excluding(self._thread_channel)},
            # OTA is ON by default, with three internet providers and a
            # broadcast every 3.9 h. A bridge that silently updates the
            # user's lamps from the internet is not what this project
            # promises (design 8.5, G9).
            CONF_OTA: {CONF_OTA_ENABLED: False},
            # Kept at its 4 h default: it is what makes neighbour tables
            # and, later, "join via this router" possible.
            CONF_TOPO_SCAN_ENABLED: True,
        }

    async def _new_application(self) -> Any:
        application = await self._application_factory(self._config())
        listener = _ApplicationListener(self)
        listener.application = application
        application.add_listener(listener)
        return application

    async def _new_application_even_if_cancelled(self) -> Any:
        """`_new_application()`, with an application that finishes being
        built while its caller is cancelled shut down rather than dropped.

        **Why cancellation reaches here at all.** The radios card lets the
        user change the stick while the supervisor is retrying a failing
        one, and `ZigbeeRuntime._release` cancels that supervisor wherever
        it is - including inside this `await`. zigpy's
        `ControllerApplication.new(start_radio=False)` opens the network
        database before it returns, so an application abandoned mid-build
        kept that database open behind the back of the source being built
        next, on the same file.

        **Why the wait loop, not a bare `await build`.** `Task.cancel()`
        can keep delivering a fresh `CancelledError` at every await point
        until this coroutine actually returns, and a bare `await build`
        right after the first `except` is itself such a point - a second
        cancellation there abandons the wait on `build` exactly as the
        first one would have, leaving its eventual application (and the
        network database it opened) built and never shut down. Looping on
        a fresh `asyncio.shield(build)` until `build` actually reports done
        closes that: only then is the plain `await build` below guaranteed
        not to suspend, and so guaranteed safe from a further
        cancellation."""
        build = asyncio.ensure_future(self._new_application())
        try:
            return await asyncio.shield(build)
        except asyncio.CancelledError:
            while not build.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(build)
            try:
                application = await build
            except Exception:
                logger.debug(
                    "a Zigbee application cancelled mid-build failed anyway", exc_info=True
                )
            else:
                await _shutdown_even_if_cancelled(application)
            raise

    def _startup_message(self, exc: BaseException) -> i18n.Message:
        if isinstance(exc, _OpenRefusedError):
            return exc.message
        for matches, key in _STARTUP_MESSAGES:
            if matches(exc):
                return i18n.Message.of(key)
        return i18n.Message.of("api.errors.zigbee_radio_failed", exc=_describe(exc))

    def _retry_error(self) -> i18n.Message | None:
        """The previous attempt's reason, for as long as this is a retry.

        A retry (`attempts > 0`) keeps it, so the card can say "trying
        again - the previous attempt failed because ..." instead of a reason
        that vanishes for the length of every attempt and comes back when
        that one fails too. A first attempt has no previous one to explain;
        the reconnect after a lost link (`attempts` is still 0 there) starts
        clean as well, because "the link is down" is exactly what it is
        busy fixing."""
        return self._progress.error if self._progress.attempts > 0 else None

    async def connect(self) -> None:
        """Opens the radio, from scratch, every time.

        **`ensure_quirks_loaded()` stays inside this method**, so the
        ordering guarantee - quirks in the registry before zigpy builds a
        single device object - lives in exactly one place and cannot be
        forgotten by a second caller. That is safe only because `connect()`
        is never called on a request path: its one caller is a background
        worker, `sources/supervisor.py`'s loop, which performs the FIRST
        connect as well as every reconnection - `wait_for_link_loss()`
        returns at once for a source that was never connected - including
        the first one after a radio is configured from the web UI. Putting
        this behind an HTTP handler would put a 9-15 s warm-up, plus
        `startup()`, plus a possible 7.5 s silent-port timeout on that
        request.

        **A second caller at startup would not merely be redundant.** This
        method has no reentrancy guard: two overlapping calls both see
        `self._app is None`, both open the one serial port, and the loser's
        application is leaked with its non-daemon bellows serial thread
        still running. `cli._run` therefore starts the supervisor and
        connects nothing itself."""
        self._set_progress("loading_quirks", error=self._retry_error())
        try:
            if self._app is not None:
                app, self._app = self._app, None
                self._connected = False
                await _shutdown_even_if_cancelled(app)
            # Before the quirks warm-up and before the port: a stick that
            # must not be opened costs neither, and is never touched.
            self._refuse_if_guarded()
            await self._learn_thread_channel()
            await ensure_quirks_loaded()
            self._set_progress("opening_radio", error=self._retry_error())
            app = await self._new_application_even_if_cancelled()
            try:
                # AGAIN, immediately before the port opens. The answer above
                # was given before a warm-up of 9-15 s on a Pi 4 and before
                # the application was built, and Thread can move onto this
                # stick in that time. The application built for nothing is
                # shut down unstarted by the `except` below.
                self._refuse_if_guarded()
                network_was_known = _network_backup_known(app)
                await app.startup(auto_form=True)
            except BaseException:
                # Never keep an object whose startup() failed - see
                # `test_a_failed_startup_is_shut_down_and_not_kept`.
                await _shutdown_even_if_cancelled(app)
                raise
        except Exception as exc:
            # `attempts` counts FAILED attempts, so the card can say "still
            # trying, 4 attempts" rather than implying a first try that is
            # about to succeed. `progress().error` keeps the message itself,
            # not its text - see `ConnectionProgress`.
            message = self._startup_message(exc)
            self._set_progress("failed", error=message, count_attempt=True)
            raise ZigbeeUnavailableError(message.text()) from exc
        self._app = app
        self._connected = True
        self._link_lost.clear()
        self._capabilities_asked.clear()
        # zigpy built new device objects while loading its database, so the
        # listeners of the previous connection point at objects that no
        # longer exist. Registering them again here rather than in
        # `subscribe()` is what keeps a reconnected bridge from going
        # silent while looking perfectly healthy (R1 section 6).
        self._register_cluster_listeners()
        self._set_progress("connected", attempts=0)
        self._note_coordinator(app, network_was_known=network_was_known)
        if self._on_connection_change is not None:
            await self._on_connection_change(True)

    def _refuse_if_guarded(self) -> None:
        refusal = None if self._open_guard is None else self._open_guard(self._path)
        if refusal is not None:
            raise _OpenRefusedError(refusal)

    async def _learn_thread_channel(self) -> None:
        """Asks the border router, once, which channel a new network must
        stay off.

        **Here, and not where the source is built.** The build runs at
        bridge startup, ahead of matter-server's connection and the web UI,
        and `current_thread_channel()` may spend its whole 5 s timeout on a
        border router that is still starting; this runs inside `connect()`,
        whose one caller is the supervisor's background task. It runs before
        any application exists, so nothing can form on a channel that is
        not known yet.

        **Once per source, not once per attempt**, which is what the build
        used to buy: the supervisor retries every 60 s for as long as a
        stick fails, and a border router that answered once is not asked
        again. `None` - no border router, or no channel in its dataset - is
        an answer like any other: the network may use every channel.

        An exception is not an answer. `current_thread_channel` already
        turns every way the border router can be missing into `None`, so
        anything it raises is a defect - it must not keep Zigbee from
        connecting, and must not be remembered: this attempt goes ahead on
        every channel, as with no border router, and the next one asks
        again."""
        lookup = self._thread_channel_lookup
        if lookup is None:
            return
        try:
            channel = await lookup()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "could not read the Thread channel from the border router; this attempt "
                "may form a Zigbee network on any channel, and the next one asks again",
                exc_info=True,
            )
            return
        self._thread_channel = channel
        self._thread_channel_lookup = None

    async def disconnect(self) -> None:
        """Shuts the application down - always.

        bellows' serial thread is a non-daemon worker: without this it
        leaks, and the stick is left mid-frame for the next start to pay
        for (research E.1, G14). Idempotent, and it reports `False` to the
        connection hook either way, because the caller that clears the radio
        setting entirely needs the badge to say so.

        `_link_lost` is SET here, not merely left alone. A supervisor parked
        in `wait_for_link_loss()` holds a reference to that event and nothing
        else; clearing `_connected` without setting it would leave the
        "the user removed the radio" path waiting for a link that is already
        gone and will never be lost again. Setting it is idempotent, and
        `connect()` clears it before anyone can wait on it afresh."""
        app, self._app = self._app, None
        self._connected = False
        self._link_lost.set()
        # The same reason as in `_handle_connection_lost`: the stick this
        # window lived in is being given up, and a radio swap builds a
        # different source anyway. A window left behind here would be a
        # countdown with no coordinator under it.
        self._close_window()
        self._release_cluster_listeners()
        self._delivered.clear()
        dispatch_task, self._dispatch_task = self._dispatch_task, None
        self._queue = None
        if dispatch_task is not None:
            dispatch_task.cancel()
            try:
                await dispatch_task
            except asyncio.CancelledError:
                pass
        # Every device goes offline with the radio that was reaching it -
        # the same promise `_handle_connection_lost` keeps for a link that
        # dies by itself, and it matters more here, because this is the
        # radio change, "No Zigbee stick" and the bridge's shutdown: without
        # it every tile kept "online" and its last value, and Loxone kept
        # `d<id>_online` true, until a restart. The catalogue is the one
        # taken from the application above, which this object no longer
        # holds. Guarded the way `ZigbeeRuntime._release` guards this whole
        # method: a report that cannot be sent - a UDP sender already
        # closed on shutdown - must not keep the stick from being released.
        checker = self._availability_checker
        if checker is not None and app is not None:
            try:
                await checker.mark_all_offline(list(app.devices.values()))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("marking the Zigbee devices offline on disconnect failed")
        # The availability checker goes down with the rest of the wiring,
        # for the same reason bellows' serial thread does: it is a task that
        # outlives nothing here, and a `disconnect()` that left it running
        # would leave a sweeper asking a source that has no application at
        # all. The next `subscribe()` builds a fresh one. The polling loop
        # is its sibling and goes down for the identical reason.
        await self._stop_availability_checker()
        await self._stop_polling_loop()
        try:
            if app is not None:
                await app.shutdown(db=True)
        finally:
            self._set_progress("idle", attempts=0)
            if self._on_connection_change is not None:
                await self._on_connection_change(False)

    async def wait_for_link_loss(self) -> None:
        """Returns as soon as the link is gone - or at once when it never
        existed.

        The second half is the contract `BridgeMatterClient.wait_for_link_loss`
        already has, and it is what puts `supervise()` into its 1 s -> 60 s
        backoff loop for a source that was never connected at all: the
        supervisor opens with this call, and a radio that is missing at
        startup is retried on exactly the same schedule as one that dies an
        hour later."""
        if not self._connected:
            return
        await self._link_lost.wait()

    def _handle_connection_lost(self, application: Any, exc: BaseException | None) -> None:
        if application is not self._app:
            # A late event from an application that has already been
            # replaced or shut down. Acting on it would tear down a
            # connection that is fine.
            logger.debug("ignoring connection_lost from a replaced application: %r", exc)
            return
        logger.warning("the link to the Zigbee coordinator was lost: %s", _describe(exc))
        self._connected = False
        self._link_lost.set()
        # An open join window does not survive the radio that was holding
        # it - see `_close_window`. Without this the pairing tab keeps
        # counting down on a network no coordinator is opening, and the next
        # device to appear after the reconnect is reported as a join that
        # nobody permitted.
        self._close_window()
        self._set_progress("failed", error=i18n.Message.of("api.errors.zigbee_not_connected"))
        if self._on_connection_change is not None:
            self._spawn(self._on_connection_change(False))
        if self._availability_checker is not None:
            # THE reason this module exists (see `availability.py`): every
            # device is pushed offline at once, rather than left showing
            # whatever it last reported while the radio that would ever
            # update it is gone.
            self._spawn(self._availability_checker.mark_all_offline())

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ----------------------------------------------------------- catalogue --

    def _devices(self) -> list[Any]:
        app = self._app
        if app is None:
            return []
        return list(app.devices.values())

    def _device_or_none(self, address: str) -> Any | None:
        """The zigpy device whose IEEE reads as `address`.

        Compared as text rather than converted: `app.devices` is keyed by
        zigpy's `EUI64`, the store speaks the string form, and building an
        `EUI64` here would be a zigpy import on the command path for no
        gain."""
        for device in self._devices():
            if str(device.ieee) == address:
                return device
        return None

    async def _read_colour_capabilities(self, device: Any) -> None:
        """Makes sure `ColorCapabilities` is in the cache before the facts
        are read out of it.

        `profiles.capabilities` gates the colour controls on `FeatureMap`
        (0xFFFC) with a fallback to `ColorCapabilities` (0x400A), and zigpy
        offers no FeatureMap at all - so without this attribute every Zigbee
        lamp loses its colour picker. zigpy's interview does not read it
        (it reads Basic's manufacturer and model and nothing else), so it is
        read here, once per connection and only when the cache does not
        already have it.

        A failure is swallowed on purpose: a lamp switched off at the wall
        must not empty the whole device catalogue. It is logged, and the
        colour picker is missing until the lamp answers - which is the
        fail-safe direction, and still the wrong outcome, which is why the
        read is attempted at all."""
        if not self._connected:
            return
        address = str(device.ieee)
        for endpoint in device.non_zdo_endpoints:
            cluster = endpoint.in_clusters.get(_COLOR_CONTROL_CLUSTER)
            if cluster is None:
                continue
            # The definition, for the reason `_endpoint_facts` spells out;
            # and `Cluster.get` raises `KeyError` for an id the cluster does
            # not declare at all, which a quirk's own Color subclass could
            # one day be. No installed quirk drops 0x400A today - checked -
            # but neither case may reach `snapshots()` as an exception.
            definition = cluster.attributes.get(_COLOR_CAPABILITIES_ATTRIBUTE)
            if definition is None:
                continue
            try:
                cached = cluster.get(definition)
            except KeyError:
                cached = None
            if cached is not None:
                continue
            key = (address, endpoint.endpoint_id)
            if key in self._capabilities_asked:
                continue
            self._capabilities_asked.add(key)
            try:
                await cluster.read_attributes([_COLOR_CAPABILITIES_ATTRIBUTE])
            except Exception as exc:  # noqa: BLE001 — see the docstring above
                logger.info(
                    "could not read the colour capabilities of %s endpoint %s: %s",
                    address,
                    endpoint.endpoint_id,
                    _describe(exc),
                )

    def _endpoint_facts(self, endpoint: Any) -> EndpointFacts:
        attributes: dict[tuple[int, int], object] = {}
        for cluster_id, cluster in endpoint.in_clusters.items():
            # The DEFINITION, never the bare id. `Cluster.get(int)` calls
            # `find_attribute(key)` OUTSIDE its own `try`, and that raises
            # `KeyError("Multiple definitions exist for attribute ID 0x...,
            # please specify a manufacturer code")` for every cluster class
            # that declares two attributes with the same id - one standard,
            # one manufacturer-specific. `cluster.attributes` is keyed by id,
            # so the duplicate is invisible there while `_attributes_by_id`
            # keeps both. zha-quirks 2.2.2 has 28 such pairs (measured, not
            # assumed: `zhaquirks/ubisys/{dimmer_d1,cover_j1,trv_h1}.py`,
            # `zhaquirks/philips/__init__.py`, `zhaquirks/innr/innr_sp120_plug.py`),
            # among them `UbisysLevelControl` 0x0000. `find_attribute` returns
            # at once for a NON-integer lookup, so handing it the definition
            # never reaches the ambiguity check.
            #
            # The residual, written down rather than left to be rediscovered:
            # on a duplicate id `attributes` has kept exactly ONE of the two
            # definitions, and that is the one whose cached value comes back.
            # Which one survives is a matter of declaration order - for
            # `UbisysLevelControl` 0x0000 it is the manufacturer-specific
            # `minimum_on_level`, so a Ubisys D1 would export its minimum-on
            # level where `current_level` belongs; for the Ubisys H1's
            # `ThermostatCluster` it is the standard name instead. Losing one
            # path on one device beats a bridge that does not start at all.
            for attribute_id, definition in cluster.attributes.items():
                try:
                    value = cluster.get(definition)
                except KeyError:
                    # A future shape of the same problem must degrade to a
                    # missing path, not take `snapshots()` down - and with it
                    # `cli._run`, which calls `attach()` unguarded before
                    # uvicorn ever starts.
                    logger.debug(
                        "skipping attribute %#06x of cluster %#06x on endpoint %s: "
                        "zigpy cannot resolve it",
                        attribute_id,
                        cluster_id,
                        endpoint.endpoint_id,
                    )
                    continue
                if value is not None:
                    attributes[(cluster_id, attribute_id)] = value
        return EndpointFacts(
            endpoint=endpoint.endpoint_id,
            # Both are `None` until the endpoint has been interviewed. 0
            # matches no row of the device-type table, which is exactly the
            # right outcome: no device type is written for it yet.
            profile_id=endpoint.profile_id or 0,
            device_type=endpoint.device_type or 0,
            in_cluster_ids=frozenset(endpoint.in_clusters),
            attributes=attributes,
        )

    def _facts(self, device: Any) -> DeviceFacts:
        node_desc = device.node_desc
        return DeviceFacts(
            ieee=str(device.ieee),
            manufacturer=device.manufacturer or "",
            model=device.model or "",
            # A device with no node descriptor yet is treated as battery
            # powered: that is the conservative half of the pair, since it
            # buys a sleeping device six hours of silence instead of two
            # (the availability sweep's thresholds) before it is declared dead.
            is_mains_powered=bool(node_desc is not None and node_desc.is_mains_powered),
            # `self._connected` alone used to be "the truth" here (see
            # `snapshots`'s own docstring): with the whole radio down every
            # device really is unreachable, and that half stays. What
            # `is_available` adds is the per-device half - zigpy's own
            # persisted `last_seen` against `availability.py`'s thresholds
            # - so a reconnect reports the state the database already
            # knows instead of seeding every device as freshly online (see
            # `test_the_online_state_is_seeded_from_the_database_after_a_restart`).
            available=self._connected and is_available(device),
            # The attribute `zha.quirks.DeviceRegistry.resolve` sets on a
            # device it transformed. This is loxmatter's equivalent of Z2M's
            # "Unsupported" badge, and it explains missing values before the
            # user asks.
            quirk_applied=hasattr(device, "_quirk_registry_entry"),
            # Endpoint 0 is zigpy's ZDO and carries no ZCL clusters.
            endpoints=tuple(
                self._endpoint_facts(endpoint) for endpoint in device.non_zdo_endpoints
            ),
        )

    async def _snapshot(self, device: Any) -> NodeSnapshot:
        await self._read_colour_capabilities(device)
        return build_snapshot(self._facts(device))

    async def snapshots(self) -> list[NodeSnapshot]:
        """The device catalogue, whether or not the stick is there.

        `sources.supervisor.attach()` calls this unconditionally and
        `cli._run` runs `attach` for every source at startup, so raising
        here would take the whole bridge down because a USB stick was
        unplugged - and Matter is the mandatory source, not this one.

        zigpy loaded its database without touching the radio
        (`new(start_radio=False)`), so the devices are known even when the
        link is gone; they simply carry `available=False`, which is the
        truth. Before the first application exists at all, the honest answer
        is an empty list - nothing has read the database yet."""
        return [await self._snapshot(device) for device in self._devices()]

    async def snapshot_for(self, address: str) -> NodeSnapshot | None:
        """One device's snapshot, or `None` if zigpy does not know it.

        For the pairing route, which registers a named device into the
        store and needs the snapshot `Store.register_device` is keyed on.
        One device rather than `snapshots()`'s whole catalogue, because
        `_snapshot` can go to the air for `ColorCapabilities` and a route
        that adopts one lamp must not pay that for every other lamp in the
        house as well."""
        device = self._device_or_none(address)
        if device is None:
            return None
        return await self._snapshot(device)

    # -------------------------------------------------------------- events --

    async def subscribe(
        self,
        resolve_device_id: Callable[[str], int | None],
        handler: RuntimeEventHandler,
    ) -> None:
        """Reports attribute changes to `handler`, and tolerates a radio
        that is not there.

        Unlike `BridgeMatterClient.subscribe`, a second call is not an
        error: `attach()` runs on every reconnect, and for this source the
        cluster listeners are rebuilt by `connect()` anyway - the handler
        and the dispatch task are meant to outlive an outage, the way
        `Runtime`'s loops are."""
        self._handler = handler
        self._resolve_device_id = resolve_device_id
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(self._dispatch_loop(self._queue))
        self._register_cluster_listeners()
        # **The previous one is stopped first, and awaited.** Assigning over
        # it would leave its `_run()` task pending on a checker nobody can
        # reach any more, holding the device ids of a connection that is
        # gone; `attach()` runs on every reconnect, so that is one leaked
        # sweeper per outage rather than a one-off - and now that `start()`
        # is called below, that leaked sweeper is a LIVE task reading a
        # progressively staler application object.
        await self._stop_availability_checker()
        # Rebuilt fresh on every call - and STARTED here: this is where a
        # source is run through a real subscribe-to-disconnect service
        # lifecycle, so it is where the periodic sweep
        # (`CHECK_INTERVAL_SECONDS`) actually begins running. By the time it
        # runs live it already gates every report on `self.connected` and
        # excludes the coordinator (`availability.py`'s `_check_one` and
        # `_devices_to_check`), or the sweep would contradict
        # `mark_all_offline()` a tick later - see
        # `test_a_device_marked_offline_by_link_loss_does_not_flip_back_on_the_next_sweep`.
        self._availability_checker = AvailabilityChecker(self, handler, resolve_device_id)
        self._availability_checker.start()
        await self._seed_baseline()
        await self._stop_polling_loop()
        self._polling_loop = PollingLoop(self, self._polling)
        self._polling_loop.start()
        self._resume_pending_devices()

    async def _stop_availability_checker(self) -> None:
        checker, self._availability_checker = self._availability_checker, None
        if checker is not None:
            await checker.stop()

    async def _stop_polling_loop(self) -> None:
        loop, self._polling_loop = self._polling_loop, None
        if loop is not None:
            await loop.stop()

    def _resume_pending_devices(self) -> None:
        """Re-installs `configure.py`'s wake-up watcher on every device the
        pending table still owes work to, after a restart.

        `ZigbeePendingStore.addresses_with_pending()` exists for exactly
        this moment - its own docstring says "for a bridge that has just
        started and wants to know which devices to watch for"
        (`model/zigbee_pending_store.py`) - and nothing called it before.
        Without this, a device interrupted mid-configuration by a bridge
        restart keeps a correct row in `zigbee_pending_config` and gets no
        watcher: `device_last_seen_updated` and `checkin` on it are never
        noticed again, and the row sits there, truthful and useless, until
        the device is factory-reset and re-paired.

        Called from `subscribe()`, which already runs after the catalogue
        is loaded (`_seed_baseline()`, just above, already relies on the
        same ordering) - so `_device_or_none` has something to find."""
        if self._store is None:
            return
        for address in self._store.zigbee_pending.addresses_with_pending():
            device = self._device_or_none(address)
            if device is not None:
                watch_for_wakeups(device, store=self._store, polling=self._polling)

    async def _seed_baseline(self) -> None:
        """Remembers what each known device currently reads as, WITHOUT
        telling the handler.

        `attach()` calls `snapshots()` right after this and seeds the
        runtime from that, so announcing the same thing here would write
        every signal row twice. A device the store does not know yet is
        deliberately left out: its first update then arrives as a full
        snapshot, which is the only thing that creates its rows."""
        resolve = self._resolve_device_id
        if resolve is None:
            return
        for device in self._devices():
            address = str(device.ieee)
            if resolve(address) is None:
                continue
            snapshot = await self._snapshot(device)
            self._delivered[address] = dict(snapshot.attributes)

    def _release_cluster_listeners(self) -> None:
        for address in list(self._unsubscribers):
            self._release_device_listeners(address)
        self._unsubscribers = {}
        self._listening.clear()

    def _release_device_listeners(self, address: str) -> None:
        for unsubscribe in self._unsubscribers.pop(address, []):
            unsubscribe()
        self._listening.pop(address, None)

    def _register_cluster_listeners(self) -> None:
        self._release_cluster_listeners()
        for device in self._devices():
            self._listen_to_device(device)

    def _listen_to_device(self, device: Any) -> None:
        """Registers the four attribute events on every cluster of one
        device.

        The callbacks do NOTHING but `put_nowait`. zigpy emits cluster
        events synchronously through `EventBase.emit`, which catches no
        exception at all, so anything that can fail - resolving a device id
        against SQLite, building a snapshot, the handler itself - has to
        happen on the dispatch task instead. A raising callback would
        otherwise escape into whatever zigpy was doing when the attribute
        arrived, and event delivery would stop for every device.

        **The guard compares the device OBJECT, not its address.** zigpy's
        `_device_reinterviewed` builds a replacement device - new object, new
        clusters - under the same IEEE, and an address-keyed guard would then
        return early and leave the replacement with no listeners at all while
        the bridge kept reporting a healthy link."""
        queue = self._queue
        if queue is None:
            return
        address = str(device.ieee)
        if self._listening.get(address) is device:
            return
        # A different object under the same address: drop the old object's
        # subscriptions before binding the new one, so a device that is
        # reinterviewed repeatedly does not accumulate dead closures.
        self._release_device_listeners(address)
        self._listening[address] = device
        unsubscribers = self._unsubscribers.setdefault(address, [])
        for endpoint in device.non_zdo_endpoints:
            for cluster in endpoint.in_clusters.values():
                for event_name in ATTRIBUTE_EVENTS:
                    # The default argument binds this device's address per
                    # loop iteration instead of reading the name from the
                    # enclosing scope too late.
                    def on_attribute_event(_event: Any, address: str = address) -> None:
                        queue.put_nowait(address)

                    unsubscribers.append(cluster.on_event(event_name, on_attribute_event))

    async def _dispatch_loop(self, queue: asyncio.Queue[str]) -> None:
        while True:
            address = await queue.get()
            try:
                await self._deliver(address)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failure on a single update must not end delivery as a
                # whole - the same stance as
                # `BridgeMatterClient._dispatch_loop` and
                # `Runtime._heartbeat_loop`.
                logger.exception("delivery of a Zigbee update failed")
            finally:
                queue.task_done()

    async def _deliver(self, address: str, *, force_snapshot: bool = False) -> None:
        """Tells the handler what changed about one device.

        Two shapes, and which one is used is not a matter of taste:
        `Store.register_signals` computes `exported` only when a row is
        CREATED, and only `on_node_snapshot` creates rows. So a path nobody
        has seen before goes out as a whole snapshot, and a path that
        already has a row goes out as the single value it is. Delivering
        everything as an attribute would silently discard the first reading
        of every sensor; delivering everything as a snapshot would rewrite
        every signal row on every report."""
        handler = self._handler
        resolve = self._resolve_device_id
        if handler is None or resolve is None:
            return
        device = self._device_or_none(address)
        if device is None:
            logger.debug("update for a device zigpy no longer knows: %s", address)
            return
        device_id = resolve(address)
        if device_id is None:
            logger.debug("discarding an update for a device the store does not know: %s", address)
            return

        snapshot = await self._snapshot(device)
        attributes = dict(snapshot.attributes)
        previous = self._delivered.get(address)
        if previous is None or force_snapshot or set(attributes) - set(previous):
            await handler.on_node_snapshot(device_id, snapshot)
            # Only after the handler returned: if it raised - it writes into
            # the store - the debt stays outstanding and the next update
            # catches it up.
            self._delivered[address] = attributes
            return
        for path, value in attributes.items():
            if previous.get(path) != value:
                await handler.on_attribute(device_id, path, value)
        self._delivered[address] = attributes

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None:
        """Re-reads one device and hands the result to the handler.

        The commissioning route's counterpart to the dispatch loop: after a
        device has been registered in the store, this is what gives it its
        signal rows and its first values, without waiting for the next
        report - which for a quiet device may never come."""
        await self._deliver(address, force_snapshot=seed_even_without_new_paths)

    # ------------------------------------------------------------- commands --

    def _require_app(self) -> Any:
        app = self._app
        if app is None or not self._connected:
            raise DeviceUnreachableError(i18n.t("api.errors.zigbee_not_connected"))
        return app

    def _require_device(self, address: str) -> Any:
        self._require_app()
        device = self._device_or_none(address)
        if device is None:
            raise DeviceUnreachableError(
                i18n.t(
                    "api.errors.device_unreachable",
                    exc=i18n.t("api.errors.zigbee_device_not_in_network", address=address),
                )
            )
        return device

    async def send(self, call: DeviceCall) -> None:
        """Executes one translated `DeviceCall` on one cluster.

        The payload is renamed at this edge and NOT passed through: the
        field names `commands/translate.py` produces are Matter's, zigpy's
        command schema declares its own, and the two differ in more than
        case (`colorTemperatureMireds` is `color_temp_mireds`). A field the
        command does not declare is dropped rather than sent -
        `move_to_level_with_on_off` carries no options fields at all, and
        offering them would make zigpy reject the whole command."""
        device = self._require_device(call.address)
        endpoint = device.endpoints.get(call.endpoint)
        cluster = None if endpoint is None else endpoint.in_clusters.get(call.cluster_id)
        command = None if cluster is None else cluster.server_commands.get(call.command_id)
        if cluster is None or command is None:
            raise DeviceUnreachableError(
                i18n.t(
                    "api.errors.device_unreachable",
                    exc=i18n.t(
                        "api.errors.zigbee_no_such_command",
                        endpoint=call.endpoint,
                        address=call.address,
                        command_id=call.command_id,
                        cluster_id=call.cluster_id,
                    ),
                )
            )
        allowed = frozenset(field.name for field in command.schema.fields)
        payload = rename_payload(call.cluster_id, call.command_id, call.payload, allowed)
        with _as_device_error():
            result = await cluster.command(call.command_id, **payload)
        status = getattr(result, "status", None)
        if status is not None and int(status) != _ZCL_SUCCESS:
            # Delivered and refused. From the caller's point of view that is
            # the same outcome as no answer at all, and the same 502.
            raise DeviceUnreachableError(
                i18n.t(
                    "api.errors.device_unreachable",
                    exc=i18n.t(
                        "api.errors.zigbee_command_refused",
                        command_id=call.command_id,
                        status=int(status),
                    ),
                )
            )

    async def remove(self, address: str) -> None:
        """Asks the device to leave, and forgets it either way.

        `app.remove()` deletes the device from zigpy's database whether or
        not the leave request is ever delivered (R1 section 4), and nothing
        waits for a confirmation here: a sleeping battery device never sends
        one, and holding the DELETE open for it would be the same bug
        `bounded_source_call` exists to prevent. The removal copy in the web
        UI says exactly this."""
        device = self._require_device(address)
        app = self._require_app()
        with _as_device_error():
            await app.remove(device.ieee)
        self._forget(address)

    def _forget(self, address: str) -> None:
        self._pairing.pop(address, None)
        self._delivered.pop(address, None)
        self._polling.forget(address)
        self._release_device_listeners(address)

    async def permit(self, seconds: int) -> datetime:
        """Opens the network for new devices, and answers when it closes.

        The END TIMESTAMP, not the duration: a page reloaded halfway through
        the window then counts down to the truth rather than starting over.
        `seconds` is bounded by the protocol maximum zigpy itself asserts,
        and a value outside it is REFUSED rather than clamped - "forever"
        must not look like it worked. `permit(0)` is Stop, and it closes the
        window at once.

        **Serialised, and the end time is written under the same lock as
        the radio call.** The two are one fact - what the network is doing,
        and what the tab counts down to - and they are told apart by nothing
        but this lock: `app.permit` awaits a broadcast and a call into the
        NCP, so a Stop issued while a "Keep open longer" is still in flight
        can reach the radio first and then have the older call write its own
        four-minute end time over the closure. The window would be shut and
        the page would keep counting."""
        if not 0 <= seconds <= PERMIT_MAX_SECONDS:
            raise ValueError(
                f"a join window lasts between 0 and {PERMIT_MAX_SECONDS} seconds, not {seconds}"
            )
        async with self._permit_lock:
            app = self._require_app()
            with _as_device_error():
                await app.permit(time_s=seconds)
            # The link is looked at AGAIN after the await. bellows resolves
            # the command's future when the NCP's answer arrives, and this
            # coroutine resumes one loop iteration later - so a
            # `connection_lost` (or a `disconnect()` for a radio swap) can
            # run in between, close the window, and be undone by the write
            # below: a countdown on a dead radio, and a join grace no
            # coordinator is holding. The radio that answered is gone, so
            # the window it opened is gone with it.
            if self._app is not app or not self._connected:
                raise DeviceUnreachableError(i18n.t("api.errors.zigbee_not_connected"))
            until = datetime.now(UTC) + timedelta(seconds=seconds)
            # Kept even for a Stop, and even once it is in the past: the end
            # of the LAST window is what `_join_came_from_a_window` measures
            # its grace against. `permit_until()` is where "is it open" is
            # answered, and it answers `None` for both of those cases.
            self._permit_until = until
        return until

    def permit_until(self) -> datetime | None:
        """When the OPEN join window closes, or `None` if none is open.

        What `GET /api/zigbee/pairing` reports and the tab counts down to.
        `None` covers all four ways there is nothing to count: no window was
        ever opened, one ran out, the user pressed Stop, and the radio went
        away underneath an open one."""
        return self._permit_until if self._window_is_open() else None

    def _close_window(self) -> None:
        """Forgets the join window - the radio that was holding it is gone.

        A permit lives in the coordinator, not in this object: a stick that
        was unplugged, wedged, or swapped for another one is not holding a
        network open for anybody, so a tab still counting down would be
        counting down to a fiction. Clearing the end time also ends the join
        grace, which is right for the same reason - the next device to
        appear after the radio comes back was not let in by a window nobody
        has open."""
        self._permit_until = None

    # -------------------------------------------------------------- pairing --

    def _window_is_open(self) -> bool:
        return self._permit_until is not None and datetime.now(UTC) < self._permit_until

    def _join_came_from_a_window(self) -> bool:
        """Whether a device turning up right now was let in by the user.

        The open window plus `PERMIT_JOIN_GRACE_SECONDS`, for the reason
        that constant gives: an announcement sent inside the window can
        reach the coordinator after it, and labelling that device "was
        already on the network" would be a lie about the device the user is
        holding."""
        if self._permit_until is None:
            return False
        return datetime.now(UTC) < self._permit_until + timedelta(seconds=PERMIT_JOIN_GRACE_SECONDS)

    def _handle_device_event(self, device: Any, state: PairingState) -> None:
        address = str(device.ieee)
        existing = self._pairing.get(address)
        row = PairingRow(
            ieee=address,
            state=state,
            manufacturer=device.manufacturer or "",
            model=device.model or "",
            quirk_applied=hasattr(device, "_quirk_registry_entry"),
            # Decided once, when the row is created: zigpy interviews
            # devices of an adopted network on its own, so a device that
            # appeared without a join window must not later be relabelled a
            # join just because its interview finished while one was open.
            discovered=(
                existing.discovered if existing is not None else not self._join_came_from_a_window()
            ),
            changed_at=now_iso(),
            # The same conservative reading as `_facts`: no node descriptor
            # yet means battery powered, and battery powered means the
            # longer stuck threshold rather than the shorter one.
            is_mains_powered=bool(
                device.node_desc is not None and device.node_desc.is_mains_powered
            ),
        )
        self._pairing[address] = row
        if state == "ready":
            self._listen_to_device(device)
            if self._store is not None:
                # Configure-on-join, and only THEN the snapshot: zigpy
                # configures nothing by itself, so a device delivered before
                # this ran would arrive with an empty attribute cache and
                # get no signal rows at all (`zigbee/configure.py`).
                # Deliberately not awaited here - this method is called
                # synchronously from zigpy's event path and must return at
                # once.
                self._spawn(self._configure_then_deliver(device))
                return
            queue = self._queue
            if queue is not None:
                queue.put_nowait(address)

    async def _configure_then_deliver(self, device: Any) -> None:
        """Runs configure-on-join and announces the device either way.

        Either way, because a sleeping device that deferred every cluster is
        still a device the user has just paired and expects to see. What it
        will not have yet is values - which is exactly what the pending
        table and `watch_for_wakeups` exist to fix later.

        **`_deliver` is awaited here rather than enqueued**, and that is the
        whole ordering guarantee: a `put_nowait` would only schedule the
        announcement, and whether the dispatch loop got to it before or
        after the configuration finished would be a matter of when the
        routine happened to yield. Awaiting it makes "configured, then
        announced" a property of this code instead of of the scheduler."""
        address = str(device.ieee)
        self._configuring.add(address)
        try:
            await configure_device(device, store=self._store, polling=self._polling)
        except Exception:
            logger.exception("configuring %s after it joined failed", address)
        finally:
            # Cleared before delivery, not after: a snapshot arriving while
            # this address still counted as "configuring" would have the
            # pairing tab and the device's own first values disagree about
            # whether it was ready.
            self._configuring.discard(address)
        try:
            await self._deliver(address)
        except Exception:
            # The same stance as `_dispatch_loop`: one device's delivery
            # must not take the join path down.
            logger.exception("delivery of a freshly joined Zigbee device failed")

    def configuring_addresses(self) -> frozenset[str]:
        """Every address currently mid configure-on-join, for the pairing
        route in `api/zigbee.py` to overlay onto a `"ready"` row. A snapshot, not a
        live view: the set backing it can change under the caller between
        one call and the next, which is fine - a request that catches the
        tail end of a configuration pass and reports "ready" a beat early
        is not the failure mode this exists to prevent; reporting "ready"
        for the WHOLE ~30 s pass is."""
        return frozenset(self._configuring)

    def _handle_device_removed(self, device: Any) -> None:
        self._forget(str(device.ieee))

    def retry_interview(self, address: str) -> None:
        """Asks zigpy to interview this device again - the Retry the failed
        and stuck rows of design 3.1 offer.

        SYNCHRONOUS, and that is the whole shape of it: an interview is a
        sequence of ZDO requests to a device that may be asleep and is
        worth minutes on the pessimistic side, so the route schedules it and
        answers, exactly as `PUT /api/zigbee/radio` schedules a
        reconnection. `Device.schedule_initialize()` cancels any
        initialization still running and starts a fresh one (verified
        against zigpy 2.2.0); for a device that IS already fully
        interviewed it instead re-announces it through
        `ControllerApplication.device_initialized`, which lands back here as
        an ordinary "ready" - a harmless outcome for a button the user only
        sees on a row that is not ready.

        The row goes back to `"interviewing"` with a fresh `changed_at`, so
        that a retry restarts the stuck clock rather than leaving the row
        stuck the moment it is pressed."""
        device = self._require_device(address)
        device.schedule_initialize()
        row = self._pairing.get(address)
        if row is not None:
            self._pairing[address] = replace(row, state="interviewing", changed_at=now_iso())


async def _shutdown_even_if_cancelled(application: Any) -> None:
    """`application.shutdown(db=True)`, carried to its end even when the
    task awaiting it is cancelled halfway - possibly more than once, while
    that wait is itself still under way - and the cancellation re-raised
    afterwards.

    A shutdown interrupted halfway is the worst outcome of a radio change:
    the application is already detached from the source, so nothing would
    ever call it again, and the serial port it holds stays open - for the
    very stick the user may pick again a moment later, which then fails as
    busy. `ZigbeeRuntime._release` cancels the supervisor exactly once and
    waits for it, so finishing the shutdown first costs that wait and
    nothing else.

    A single `except` that falls through to a bare `await shutdown` is not
    enough: `Task.cancel()` can keep delivering a fresh `CancelledError` at
    every await point until this coroutine actually returns, and that bare
    `await` is itself such a point - a second cancellation right there
    abandons the wait exactly as the first one would have. The loop below
    keeps re-shielding `shutdown` until it actually reports done - only
    then is a plain `await` on it guaranteed not to suspend, and so
    guaranteed safe from a further cancellation."""
    shutdown = asyncio.ensure_future(application.shutdown(db=True))
    try:
        await asyncio.shield(shutdown)
    except asyncio.CancelledError:
        while not shutdown.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(shutdown)
        with contextlib.suppress(Exception):
            await shutdown
        raise


def _describe(exc: BaseException | None) -> str:
    """An exception as a sentence fragment that is never empty.

    `str(TimeoutError())` is the empty string, and a 502 whose detail is
    blank tells the person reading it nothing at all."""
    if exc is None:
        return "unknown cause"
    return str(exc) or type(exc).__name__


@contextlib.contextmanager
def _as_device_error() -> Iterator[None]:
    """zigpy's failure vocabulary, reduced to the one word shared code
    knows.

    `DeliveryError`, `ControllerException`, `ZigbeeException` and
    `TimeoutError` all mean the same thing to a caller: asked, no answer.
    They are translated HERE, so no `except` clause outside this package
    ever names a zigpy type - and so `api/devices.py`'s removal route, which
    caught `MatterUnavailableError` alone, does not turn a Zigbee removal
    failure into an unhandled 500.

    Anything else propagates unchanged: a `KeyError` from this file's own
    mistake is a bug, not an unreachable device, and dressing it up as one
    would hide it."""
    try:
        yield
    except Exception as exc:
        if _is_unreachable(exc):
            raise DeviceUnreachableError(
                i18n.t("api.errors.device_unreachable", exc=_describe(exc))
            ) from exc
        raise
