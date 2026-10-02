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

"""The commissioning session (design 2026-10-02, section 8).

In memory only: cards for devices in pairing mode (from a Bluetooth scan)
and for scanned pairing codes, a queue the worker commissions one card at a
time, and the naming line in which exactly one finished device blinks.

**A pairing code never leaves this module.** It lives in `_codes`, keyed by
card id, never on `Card`, is never logged, and is deleted as soon as its card
is done or removed. `view()` - the JSON of `GET /api/commissioning` - only
says whether a card has one.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from loxmatter import i18n
from loxmatter.commissioning.run import (
    MANUAL_DATASET_ORIGIN_KEY,
    CommissionFailed,
    ThreadDatasetSource,
    commission,
)
from loxmatter.matter.commissioning_progress import CommissioningTracker
from loxmatter.matter.dcl import DclDirectory, ProductLabel
from loxmatter.matter.otbr import ThreadDatasetUnavailableError, validated_dataset
from loxmatter.matter.setup_payload import (
    SetupPayload,
    TypoError,
    UnreadableCodeError,
    decode,
    discriminator_for,
)
from loxmatter.model.store import Store
from loxmatter.radios.bluetooth_health import KernelLog, counts_since
from loxmatter.radios.bluez import BluezReader, BluezScanError, BluezScanner, MatterAdvert

if TYPE_CHECKING:
    from loxmatter.api.devices import RuntimeValues
    from loxmatter.commissioning.identify import IdentifyCoordinator
    from loxmatter.matter.client import BridgeMatterClient
    from loxmatter.matter.models import NodeSnapshot

logger = logging.getLogger(__name__)

SCAN_SECONDS: Final = 10.0
AUTO_SCAN_MIN_INTERVAL: Final = 60.0
EARLY_WARNING_SECONDS: Final = 20.0
CHECK_BLINK_SECONDS: Final = 3
# How often the worker looks at the tracker and BlueZ while a card runs -
# the tracker's own sampling cadence (design 2026-09-22, section 5.1).
_MONITOR_INTERVAL: Final = 2.0
# How often the worker looks again while matter-server is away.
_RECONNECT_POLL: Final = 1.0
# Basic Information (cluster 0x0028) on endpoint 0: VendorID, ProductID.
_VENDOR_ID_PATH: Final = "0/40/2"
_PRODUCT_ID_PATH: Final = "0/40/4"

CardState = Literal["found", "ready", "queued", "running", "naming", "done", "not_nearby", "failed"]

# The order of the cards in the dialog (design 4.2); within a group by RSSI.
_STATE_ORDER: Final[dict[str, int]] = {
    "naming": 0,
    "running": 1,
    "queued": 2,
    "ready": 3,
    "found": 4,
    "not_nearby": 5,
    "failed": 5,
    "done": 6,
}


class Unset(enum.Enum):
    """`update_card(room=...)` left out - `None` means "no room"."""

    TOKEN = 0


UNSET: Final = Unset.TOKEN


@dataclass
class Card:
    id: int
    state: CardState
    product: str
    detail: str
    pairing_hint: str
    rssi: int | None
    advert_address: str | None
    payload: SetupPayload | None
    name: str = ""
    room: str | None = None
    phase: str | None = None
    note: str | None = None
    device_id: int | None = None
    candidates: list[str] = field(default_factory=list)


class CodeRejected(Exception):
    """A request the session refuses. `detail` is translated, `status` the
    HTTP status the route answers with: 422 for an unusable code or a
    missing name, 409 for a duplicate code, a refused scan or a card that is
    being commissioned, 404 for a card that no longer exists."""

    def __init__(self, key: str, status: int) -> None:
        self.detail = i18n.t(key)
        self.status = status
        super().__init__(self.detail)


class CommissioningSession:
    def __init__(
        self,
        *,
        store: Store,
        client_for: Callable[[], BridgeMatterClient | None],
        runtime: RuntimeValues,
        tracker: CommissioningTracker,
        fetch_dataset: ThreadDatasetSource,
        reader: BluezReader,
        scanner: BluezScanner,
        kernel: KernelLog | None,
        dcl: DclDirectory,
        identify: IdentifyCoordinator,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._client_for = client_for
        self._runtime = runtime
        self._tracker = tracker
        self._fetch_dataset = fetch_dataset
        self._reader = reader
        self._scanner = scanner
        self._kernel = kernel
        self._dcl = dcl
        self._identify = identify
        self._clock = clock
        self._sleep = sleep

        self._cards: dict[int, Card] = {}
        self._codes: dict[int, str] = {}
        # The advert each scanned card came from, for matching a code
        # (discriminator) and adopting a numeric-code card (vendor/product).
        self._adverts: dict[int, MatterAdvert] = {}
        self._next_id = 1
        self._queue: list[int] = []
        self._naming: list[int] = []
        # "Try anyway": queued without the advertising check.
        self._forced: set[int] = set()
        # One lock for the radio: a scan and a commissioning never overlap.
        self._radio = asyncio.Lock()
        self._scanning = False
        self._last_scan_at: float | None = None
        self.bluetooth_warning = False
        # A Thread dataset entered by hand ("Different Thread network"):
        # handed to every commissioning instead of the Border Router's.
        # In memory only - it holds the network key, so it is never
        # stored, logged or part of `view()`.
        self._thread_dataset: str | None = None
        self._worker: asyncio.Task[None] | None = None
        self._side_tasks: set[asyncio.Task[None]] = set()

    # --- properties ---------------------------------------------------

    @property
    def busy(self) -> bool:
        """Whether the worker holds the radio - for a commissioning or the
        follow-up work after it. A scan is refused for exactly as long."""
        return self._radio.locked() and not self._scanning

    @property
    def has_work(self) -> bool:
        return any(card.state in ("queued", "running", "naming") for card in self._cards.values())

    def _worker_active(self) -> bool:
        return self._worker is not None and not self._worker.done()

    def _startable(self) -> CardState:
        """A card that could run: queued while the worker runs, ready
        otherwise (design 4.3: codes scanned while it runs queue at the end)."""
        return "queued" if self._worker_active() else "ready"

    def _enqueue(self, card: Card) -> None:
        """Puts a `queued` card at the end of the queue and makes sure a
        worker runs it - a queued card nobody runs would wait forever."""
        if card.state == "queued" and card.id not in self._queue:
            self._queue.append(card.id)
        self._ensure_worker()

    def _matter_connected(self) -> bool:
        client = self._client_for()
        return client is not None and bool(client.connected)

    def set_thread_dataset(self, dataset: str | None) -> None:
        """Sets (or with `None` removes) the hand-entered Thread dataset.

        Checked with the same `validated_dataset` that `commission()` uses,
        so a wrong one is refused here, at the field, rather than failing
        every card later. A refused one leaves the previous one in place."""
        if dataset is None:
            self._thread_dataset = None
            return
        try:
            self._thread_dataset = validated_dataset(dataset, i18n.t(MANUAL_DATASET_ORIGIN_KEY))
        except ThreadDatasetUnavailableError as exc:
            # The reason names the length only, never the value.
            logger.warning("Entered Thread dataset rejected: %s", exc)
            raise CodeRejected("api.devices.fail_manual_thread_dataset", 422) from exc

    # --- scanning -----------------------------------------------------

    async def scan(self, *, automatic: bool = False) -> bool:
        """A short Bluetooth scan of our own. False when it was skipped (an
        automatic scan within `AUTO_SCAN_MIN_INTERVAL`), refused (a device
        is being commissioned, or a scan already runs) or failed."""
        if self._radio.locked() or self._scanning:
            return False
        now = self._clock()
        if (
            automatic
            and self._last_scan_at is not None
            and now - self._last_scan_at < AUTO_SCAN_MIN_INTERVAL
        ):
            return False
        async with self._radio:
            self._scanning = True
            self._last_scan_at = now
            start_usec = self._kernel.now_usec() if self._kernel is not None else None
            try:
                await self._scanner.scan(seconds=SCAN_SECONDS)
            except BluezScanError as exc:
                logger.warning("Bluetooth scan failed: %s", exc)
                self.bluetooth_warning = True
                return False
            finally:
                self._scanning = False
        snapshot = await self._reader.snapshot()
        if snapshot is not None:
            for advert in snapshot.adverts:
                await self._take_advert(advert)
            await self._rematch_not_nearby({advert.address for advert in snapshot.adverts})
        if self._kernel is not None and start_usec is not None:
            findings = await self._kernel.findings_async()
            counts = counts_since(findings or [], start_usec)
            self.bluetooth_warning = bool(counts["stuck"] or counts["transport"])
        else:
            self.bluetooth_warning = False
        return True

    async def _take_advert(self, advert: MatterAdvert) -> None:
        for card_id, known in self._adverts.items():
            if known.address == advert.address and card_id in self._cards:
                self._adverts[card_id] = advert
                self._cards[card_id].rssi = advert.rssi
                return
        label = await self._dcl.product_label(advert.vendor_id, advert.product_id)
        card = self._new_card("found", label, payload=None)
        card.rssi = advert.rssi
        card.advert_address = advert.address
        self._adverts[card.id] = advert

    async def _rematch_not_nearby(self, advertising: set[str]) -> None:
        """ "Scan again" for a `not_nearby` card: a skipped card whose device
        advertises again can run again; a code-only card that matches
        exactly one device advertising in this scan moves onto its card.
        Either is queued while the worker runs, ready otherwise."""
        for card in list(self._cards.values()):
            if card.state != "not_nearby" or card.payload is None:
                continue
            if card.advert_address is not None:
                if card.advert_address in advertising:
                    card.state = self._startable()
                    card.note = None
                    self._enqueue(card)
                continue
            matches = self._matching_found(card.payload, advertising)
            if len(matches) != 1:
                continue
            target = matches[0]
            target.payload = card.payload
            target.name = card.name
            target.room = card.room
            target.state = self._startable()
            self._codes[target.id] = self._codes.pop(card.id)
            self._drop(card.id)
            self._enqueue(target)

    # --- codes --------------------------------------------------------

    def _matching_found(
        self, payload: SetupPayload, advertising: set[str] | None = None
    ) -> list[Card]:
        """The `found` cards whose advert fits the code's discriminator -
        with `advertising`, only those whose device is in that scan."""
        discriminator = discriminator_for(payload)
        return [
            card
            for card in self._cards.values()
            if card.state == "found"
            and card.id in self._adverts
            and (advertising is None or self._adverts[card.id].address in advertising)
            and discriminator.matches(self._adverts[card.id].discriminator)
        ]

    async def add_code(self, code: str, room: str | None) -> Card:
        text = code.strip()
        try:
            payload = decode(text)
        except TypoError as exc:
            raise CodeRejected("api.commissioning.fail_typo", 422) from exc
        except UnreadableCodeError as exc:
            raise CodeRejected("api.commissioning.fail_unreadable_code", 422) from exc
        if text in self._codes.values():
            raise CodeRejected("api.commissioning.fail_duplicate", 409)

        # Queued if the worker ran when the code was scanned or runs once the
        # lookup below is back; `_enqueue` then makes sure a worker runs it,
        # even if the one that ran has finished during the lookup.
        worker_ran = self._worker_active()
        matches = self._matching_found(payload)
        if len(matches) == 1:
            card = matches[0]
            card.payload = payload
            card.room = room
            card.state = self._startable()
        else:
            label = await self._dcl.product_label(payload.vendor_id, payload.product_id)
            startable: CardState = "queued" if worker_ran else self._startable()
            if len(matches) > 1:
                card = self._new_card(startable, label, payload=payload)
                card.candidates = [match.advert_address or "" for match in matches]
                card.note = i18n.t("web.commissioning.note_several", count=len(matches))
            elif payload.on_network and not payload.ble:
                card = self._new_card(startable, label, payload=payload)
                card.note = i18n.t("web.commissioning.note_wifi")
            else:
                card = self._new_card("not_nearby", label, payload=payload)
                card.note = i18n.t("web.commissioning.note_not_nearby", hint=label.pairing_hint)
            card.room = room
        self._codes[card.id] = text
        if card.state == "queued":
            self._enqueue(card)
        return card

    def _new_card(
        self, state: CardState, label: ProductLabel, *, payload: SetupPayload | None
    ) -> Card:
        card = Card(
            id=self._next_id,
            state=state,
            product=label.product,
            detail=label.detail,
            pairing_hint=label.pairing_hint,
            rssi=None,
            advert_address=None,
            payload=payload,
        )
        self._next_id += 1
        self._cards[card.id] = card
        return card

    def _card(self, card_id: int) -> Card:
        card = self._cards.get(card_id)
        if card is None:
            raise CodeRejected("api.commissioning.fail_unknown_card", 404)
        return card

    # --- editing ------------------------------------------------------

    async def update_card(
        self, card_id: int, *, name: str | None = None, room: str | None | Unset = UNSET
    ) -> Card:
        """Name and room stay editable in every state (design 4.2). A `done`
        card writes them to its device at once; a card in the naming line
        writes its name on `confirm_name`, so "continue without a name" can
        still keep the default label."""
        card = self._card(card_id)
        if name is not None:
            card.name = name
        if not isinstance(room, Unset):
            card.room = room
        if card.device_id is not None and card.state == "done":
            if name is not None and name.strip():
                self._store.rename_device(card.device_id, name.strip())
            if not isinstance(room, Unset):
                self._store.set_room(card.device_id, room)
        elif card.device_id is not None and not isinstance(room, Unset):
            self._store.set_room(card.device_id, room)
        return card

    def remove(self, card_id: int) -> None:
        card = self._card(card_id)
        if card.state == "running":
            raise CodeRejected("api.commissioning.fail_card_running", 409)
        self._drop(card_id)

    def clear(self) -> None:
        """Every card that is not being commissioned right now."""
        for card_id in [cid for cid, card in self._cards.items() if card.state != "running"]:
            self._drop(card_id)

    def _drop(self, card_id: int) -> None:
        card = self._cards.pop(card_id)
        self._codes.pop(card_id, None)
        self._adverts.pop(card_id, None)
        self._forced.discard(card_id)
        if card_id in self._queue:
            self._queue.remove(card_id)
        if card_id in self._naming:
            was_first = self._naming[0] == card_id
            self._naming.remove(card_id)
            self._spawn(self._after_naming_left(card, was_first))

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    # --- the worker ---------------------------------------------------

    async def start(self) -> None:
        """Queues every ready card, in card order, and starts the worker."""
        for card in sorted(self._cards.values(), key=lambda c: c.id):
            if card.state == "ready":
                card.state = "queued"
                self._queue.append(card.id)
        self._ensure_worker()

    async def force(self, card_id: int) -> Card:
        """ "Try anyway": queues a card that was skipped or failed, without
        checking that its device advertises."""
        card = self._card(card_id)
        if card.state == "running":
            raise CodeRejected("api.commissioning.fail_card_running", 409)
        if card.state in ("not_nearby", "failed") and card_id in self._codes:
            card.state = "queued"
            card.note = None
            self._forced.add(card_id)
            self._queue.append(card_id)
            self._ensure_worker()
        return card

    def _ensure_worker(self) -> None:
        if self._queue and not self._worker_active():
            self._worker = asyncio.ensure_future(self._work())

    async def _work(self) -> None:
        while self._queue:
            client = self._client_for()
            if client is None or not client.connected:
                await self._sleep(_RECONNECT_POLL)
                continue
            card_id = self._queue[0]
            card = self._cards.get(card_id)
            if card is None or card.state != "queued" or card_id not in self._codes:
                self._queue.pop(0)
                continue
            if (
                card.advert_address is not None
                and card_id not in self._forced
                and not await self._still_advertising(card.advert_address)
            ):
                # Removed or cleared while BlueZ was read.
                if not self._queue or self._queue[0] != card_id or card_id not in self._cards:
                    continue
                self._queue.pop(0)
                card.state = "not_nearby"
                card.note = i18n.t("web.commissioning.note_not_nearby", hint=card.pairing_hint)
                continue
            async with self._radio:
                # Removed or cleared while a scan held the radio.
                if not self._queue or self._queue[0] != card_id or card_id not in self._cards:
                    continue
                self._queue.pop(0)
                self._forced.discard(card_id)
                await self._run(card, client)

    async def _still_advertising(self, address: str) -> bool:
        try:
            snapshot = await self._reader.snapshot()
        except Exception as exc:  # noqa: BLE001 - no evidence is no reason to skip
            logger.info("Reading BlueZ before commissioning failed: %s", exc)
            return True
        if snapshot is None:
            return True
        return any(advert.address == address for advert in snapshot.adverts)

    async def _run(self, card: Card, client: BridgeMatterClient) -> None:
        code = self._codes[card.id]
        room = card.room
        card.state = "running"
        card.phase = None
        monitor = asyncio.ensure_future(self._monitor(card))
        try:
            result = await commission(
                code=code,
                room=room,
                discriminator=discriminator_for(card.payload) if card.payload else None,
                client=client,
                store=self._store,
                runtime=self._runtime,
                tracker=self._tracker,
                fetch_dataset=self._fetch_dataset,
                manual_dataset=self._thread_dataset,
            )
        except CommissionFailed as exc:
            card.state = "failed"
            card.note = exc.detail
            return
        except Exception as exc:  # noqa: BLE001 - the worker goes on
            # The type only, no message and no traceback: an unrecognised
            # exception's text is not known to be free of the code.
            logger.error(
                "Commissioning card %s failed unexpectedly: %s", card.id, type(exc).__name__
            )
            card.state = "failed"
            # The type only: an unrecognised exception's text is not known
            # to be free of the code, and the note reaches the API.
            card.note = i18n.t("api.errors.commissioning_failed", exc=type(exc).__name__)
            return
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            card.phase = None
        try:
            await self._finish(card, result.device_id, result.snapshot, room)
        except Exception as exc:  # noqa: BLE001 - commissioned anyway
            # The device is commissioned and registered; a failed rename or
            # lookup afterwards must neither stop the worker nor mark it failed.
            # Logged by type only, like a failed commissioning above.
            logger.error(
                "Follow-up work for commissioned card %s failed: %s", card.id, type(exc).__name__
            )
            card.device_id = result.device_id
            if card.state == "running":
                card.state = "naming"
                self._naming.append(card.id)
                if self._naming[0] == card.id:
                    await self._quietly(self._identify.start(result.device_id, renew=True))

    async def _monitor(self, card: Card) -> None:
        """Phase and early warning while a card runs (design 8.3)."""
        payload = card.payload
        discriminator = discriminator_for(payload) if payload is not None else None
        watch = discriminator is not None and not (
            payload is not None and payload.on_network and not payload.ble
        )
        last_seen = self._clock()
        warned = False
        while True:
            phase = self._tracker.phase
            card.phase = phase
            if watch and discriminator is not None:
                try:
                    snapshot = await self._reader.snapshot()
                except Exception as exc:  # noqa: BLE001 - only a hint
                    logger.info("Reading BlueZ during commissioning failed: %s", exc)
                    snapshot = None
                if snapshot is not None and any(
                    discriminator.matches(advert.discriminator) for advert in snapshot.adverts
                ):
                    last_seen = self._clock()
                if phase == "searching" and self._clock() - last_seen >= EARLY_WARNING_SECONDS:
                    card.note = i18n.t("web.commissioning.note_no_signal_yet")
                    warned = True
                elif warned and phase != "searching":
                    card.note = None
                    warned = False
            await self._sleep(_MONITOR_INTERVAL)

    async def _finish(
        self, card: Card, device_id: int, snapshot: NodeSnapshot, room: str | None
    ) -> None:
        card.device_id = device_id
        if card.room != room:
            # Changed while the card ran.
            self._store.set_room(device_id, card.room)
        if (
            card.advert_address is None
            and card.payload is not None
            and card.payload.vendor_id is None
        ):
            await self._adopt(card, snapshot)
        if card.name.strip():
            self._store.rename_device(device_id, card.name.strip())
            self._done(card)
            await self._quietly(self._identify.start_if_idle(device_id, CHECK_BLINK_SECONDS))
            return
        card.state = "naming"
        self._naming.append(card.id)
        if self._naming[0] == card.id:
            # The front of the naming line always blinks (design 4.4): it
            # takes over from a blink started on a tile or card.
            await self._quietly(self._identify.start(device_id, renew=True))

    async def _adopt(self, card: Card, snapshot: NodeSnapshot) -> None:
        """A numeric-code card learns which device it was: the product from
        the device's Basic Information, and the first `found` card among its
        candidates with the same vendor and product goes away."""
        vendor = snapshot.attributes.get(_VENDOR_ID_PATH)
        product = snapshot.attributes.get(_PRODUCT_ID_PATH)
        card.note = None
        if not isinstance(vendor, int) or not isinstance(product, int):
            return
        label = await self._dcl.product_label(vendor, product)
        card.product, card.detail, card.pairing_hint = (
            label.product,
            label.detail,
            label.pairing_hint,
        )
        for other in list(self._cards.values()):
            advert = self._adverts.get(other.id)
            if (
                other.state == "found"
                and advert is not None
                and (not card.candidates or advert.address in card.candidates)
                and advert.vendor_id == vendor
                and advert.product_id == product
            ):
                card.rssi = advert.rssi
                self._drop(other.id)
                break
        card.candidates = []

    def _done(self, card: Card) -> None:
        card.state = "done"
        self._codes.pop(card.id, None)

    # --- naming -------------------------------------------------------

    async def confirm_name(self, card_id: int) -> Card:
        card = self._card(card_id)
        if card.state != "naming" or card.device_id is None:
            return card
        name = card.name.strip()
        if not name:
            raise CodeRejected("api.commissioning.fail_name_missing", 422)
        self._store.rename_device(card.device_id, name)
        await self._leave_naming(card)
        return card

    async def skip_name(self, card_id: int) -> Card:
        card = self._card(card_id)
        if card.state != "naming":
            return card
        await self._leave_naming(card)
        return card

    async def _leave_naming(self, card: Card) -> None:
        was_first = bool(self._naming) and self._naming[0] == card.id
        if card.id in self._naming:
            self._naming.remove(card.id)
        self._done(card)
        await self._after_naming_left(card, was_first)

    async def _after_naming_left(self, card: Card, was_first: bool) -> None:
        if card.device_id is not None and self._identify.blinking == card.device_id:
            await self._quietly(self._identify.stop())
        if was_first and self._naming:
            following = self._cards[self._naming[0]]
            if following.device_id is not None:
                await self._quietly(self._identify.start(following.device_id, renew=True))

    # --- identify -----------------------------------------------------

    async def identify_card(self, card_id: int, on: bool) -> None:
        """Identify from a card: 30 s without renewal, or stop. Errors reach
        the caller - this is a user's click, not the worker."""
        card = self._card(card_id)
        if card.device_id is None:
            raise CodeRejected("api.commissioning.fail_no_identify", 409)
        if on:
            await self._identify.start(card.device_id, renew=False)
        elif self._identify.blinking == card.device_id:
            await self._identify.stop()

    async def _quietly(self, call: Awaitable[Any]) -> None:
        """An identify call from the worker or the naming line: a device
        that cannot blink, or does not answer, never stops the queue."""
        try:
            await call
        except Exception as exc:  # noqa: BLE001
            logger.info("Identify failed: %s", exc)

    def _can_identify(self, card: Card) -> bool:
        if card.device_id is None:
            return False
        client = self._client_for()
        if client is None:
            return False
        try:
            return bool(client.supports_identify(self._store.device(card.device_id).address))
        except Exception:  # noqa: BLE001 - removed meanwhile, or the source is away
            return False

    # --- the view -----------------------------------------------------

    def view(self) -> dict[str, Any]:
        """The JSON of `GET /api/commissioning`. Never contains a code."""
        if self._scanning:
            scan_state = "scanning"
        elif self._radio.locked():
            scan_state = "blocked"
        else:
            scan_state = "idle"
        blinking = self._identify.blinking
        blinking_card = next(
            (
                card.id
                for card in self._cards.values()
                if blinking is not None and card.device_id == blinking
            ),
            None,
        )
        return {
            "scan": {
                "state": scan_state,
                "last_scan_age": (
                    None if self._last_scan_at is None else self._clock() - self._last_scan_at
                ),
            },
            "bluetooth_warning": self.bluetooth_warning,
            "thread_dataset_set": self._thread_dataset is not None,
            "matter_connected": self._matter_connected(),
            "naming": list(self._naming),
            "blinking_card": blinking_card,
            "cards": [
                self._card_view(card) for card in sorted(self._cards.values(), key=self._order)
            ],
        }

    def _order(self, card: Card) -> tuple[int, int, int, int]:
        if card.state == "naming" and card.id in self._naming:
            within = self._naming.index(card.id)
        elif card.state == "queued" and card.id in self._queue:
            within = self._queue.index(card.id)
        else:
            within = 0
        rssi = -card.rssi if card.rssi is not None else 1_000
        return (_STATE_ORDER[card.state], within, rssi, card.id)

    def card_view(self, card_id: int) -> dict[str, Any]:
        """One card as `view()` shows it - the body of the card routes."""
        return self._card_view(self._card(card_id))

    def _card_view(self, card: Card) -> dict[str, Any]:
        payload = card.payload
        return {
            "id": card.id,
            "state": card.state,
            "product": card.product,
            "detail": card.detail,
            "pairing_hint": card.pairing_hint,
            "rssi": card.rssi,
            "has_code": card.id in self._codes,
            "name": card.name,
            "room": card.room,
            "phase": card.phase,
            "note": card.note,
            "device_id": card.device_id,
            "queue_position": (
                self._queue.index(card.id) + 1
                if card.state == "queued" and card.id in self._queue
                else None
            ),
            "on_network": bool(payload is not None and payload.on_network and not payload.ble),
            "can_identify": self._can_identify(card),
        }

    # --- lifetime -----------------------------------------------------

    async def aclose(self) -> None:
        tasks = [task for task in (self._worker, *self._side_tasks) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._worker = None
        self._side_tasks.clear()
        await self._quietly(self._identify.aclose())
