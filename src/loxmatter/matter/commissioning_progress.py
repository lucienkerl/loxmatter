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
    "searching": 0,
    "found": 1,
    "connected": 2,
    "joined": 3,
    "done": 4,
    "failed": 4,
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
    stuck_since: datetime | None = None
    stuck_now: bool = False


class CommissioningTracker:
    """Tracks one commissioning attempt at a time (design 2026-09-22, section 5).

    `sample()` is driven by the caller - the route that runs the `POST` - so
    this class stays synchronous apart from reading `bluez`; it starts no
    background task of its own.
    """

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
        self.sample_interval = sample_interval
        self._stuck_after = stuck_after
        self.started_at = clock()
        self._attempt: _Attempt | None = None

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

    def node_added(self, node_id: int) -> None:
        self._advance("joined")

    async def finish(self, reason: Reason | None) -> None:
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
            if any(
                advert.connected and advert.address == attempt.matched_address
                for advert in matching
            ):
                self._advance("connected")
        self._update_stuck(attempt, snapshot.adapter)

    def _update_stuck(self, attempt: _Attempt, adapter: AdapterState | None) -> None:
        """`discovering: false` while `searching` is itself a `stuck` finding
        (design 7.1) - but only after `stuck_after`, because an attempt's
        first sample can land before matter-server has started to scan."""
        not_scanning = (
            attempt.phase == "searching" and adapter is not None and not adapter.discovering
        )
        if not not_scanning:
            attempt.stuck_since = None
            attempt.stuck_now = False
            return
        if attempt.stuck_since is None:
            attempt.stuck_since = self._clock()
        elapsed = (self._clock() - attempt.stuck_since).total_seconds()
        attempt.stuck_now = elapsed >= self._stuck_after

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
        last_hour = (
            counts_since(findings or [], (now_usec or 0) - _HOUR_USEC) if kernel_ok else None
        )
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
