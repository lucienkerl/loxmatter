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

"""The commissioning sequence, shared by the route and the queue.

Moved out of `POST /api/devices/commission` unchanged (design 2026-10-02,
section 8.3): the Thread dataset, the tracker attempt, the commissioning
itself and the follow-up work. The route maps `CommissionFailed` to the
`HTTPException` it used to raise directly; a queue worker reads the same
exception to fill a card.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loxmatter import i18n
from loxmatter.export.commands import extract_commands
from loxmatter.matter.client import BridgeMatterClient, CommissioningError, MatterUnavailableError
from loxmatter.matter.commissioning_progress import (
    CommissioningTracker,
    Discriminator,
    Reason,
    classify_failure,
)
from loxmatter.matter.otbr import ThreadDatasetUnavailableError, validated_dataset
from loxmatter.model.store import Store

if TYPE_CHECKING:
    from loxmatter.api.devices import RuntimeValues
    from loxmatter.matter.models import NodeSnapshot

logger = logging.getLogger(__name__)

# Where the Thread dataset comes from when matter-server does not (or no
# longer) have it. A dedicated type instead of the bare call, so `build_app`
# can replace it in tests with a source that has no network - the same seam
# pattern as `session_factory` in `matter/client.py`.
ThreadDatasetSource = Callable[[], Awaitable[str]]

# Where the checked dataset came from - `validated_dataset` prepends this to
# its messages. The fetched one names the URL of the Border Router there,
# the manually entered one this line; it ends up exclusively in the log,
# never in the response (see `commission_device`).
#
# A key instead of a finished sentence, and resolved only at call time: the
# messages from `validated_dataset` have run through `i18n.t()` since the
# i18n phase, a fixed German chunk in the middle of an English sentence
# would be half a translation. At import time the language is also not yet
# determined at all (cli.py only sets it afterwards).
MANUAL_DATASET_ORIGIN_KEY = "api.devices.manual_dataset_origin"


@dataclass(frozen=True)
class CommissionResult:
    device_id: int
    snapshot: NodeSnapshot


class CommissionFailed(Exception):
    """Commissioning did not happen. `reason` is the tracker's classification,
    `detail` the translated sentence for the UI, `status` the HTTP status the
    route answers with (422: the request or the device refused; 502: the
    matter-server could not be reached)."""

    def __init__(self, reason: Reason, detail: str, status: int) -> None:
        super().__init__(detail)
        self.reason: Reason = reason
        self.detail = detail
        self.status = status


def _commissioning_detail(exc: CommissioningError, missing_dataset_reason: str | None) -> str:
    """The message that reaches the UI.

    Without the addition, it only stated what matter-server itself says -
    "Commission with code failed for node 7." The actual reason ("Required
    network information not provided in commissioning parameters") lives
    exclusively in matter-server's log, and without access to it the
    message cannot be interpreted: BLE, pairing code, and the secured
    session to the device were all fine, only the network the device
    should have belonged to was missing.
    """
    detail = str(exc)
    if missing_dataset_reason is None:
        return detail
    return i18n.t(
        "api.devices.commissioning_thread_cause",
        detail=detail,
        reason=missing_dataset_reason,
    )


def _reason_detail(
    reason: Reason, exc: CommissioningError, discriminator: Discriminator | None
) -> str:
    """The `detail` matter-server's own text becomes once the tracker has
    classified why an attempt failed (design 2026-09-22, section 7.2) - the
    reason itself travels separately, in the status route's `attempt.reason`
    (section 7.2, amended).

    Resolves the `web.devices.commission_reason_*` keys directly (final
    review item 4) rather than a separate `api.devices.*` copy of the same
    three sentences: server code can resolve any key regardless of
    namespace, `GET /api/i18n` only ever ships the `web.*` slice to the
    browser (`api/language.py:_web_strings()`), and a restored attempt
    (`finishRestoredCommission` in app.js) needs these exact sentences too -
    keeping two verbatim copies in sync by hand is exactly the drift this
    removes."""
    if reason == "not_found":
        if discriminator is None:
            return i18n.t("web.devices.commission_reason_not_found_any")
        return i18n.t("web.devices.commission_reason_not_found", discriminator=discriminator.value)
    if reason == "connection_lost":
        return i18n.t("web.devices.commission_reason_connection_lost")
    return str(exc)


async def commission(
    *,
    code: str,
    room: str | None,
    discriminator: Discriminator | None,
    client: BridgeMatterClient,
    store: Store,
    runtime: RuntimeValues,
    tracker: CommissioningTracker,
    fetch_dataset: ThreadDatasetSource,
    manual_dataset: str | None = None,
) -> CommissionResult:
    """Commissions one device from its pairing code and registers it."""
    # Why this even exists: matter-server holds the Thread credentials
    # ONLY in memory and forgets them on every restart (the full
    # rationale, including a recorded real-world incident, is in
    # `matter/otbr.py`). The input field alone did not catch this - it
    # is optional and is cleared after every commissioning, so it was
    # empty the next time.
    missing_dataset_reason: str | None = None

    if manual_dataset is not None:
        # A manually entered dataset takes priority over the one from
        # the host: it is the path for a Thread network that does not
        # come from this Border Router.
        #
        # It is checked the same way as the fetched one -
        # `validated_dataset` from `matter/otbr.py`, deliberately the
        # same function and not a second replica of the same rule
        # (hence the import of a module-private name). Passed through
        # unchecked, a dataset inserted with a line break or as a JSON
        # structure would trigger a `bytes.fromhex` failure at
        # matter-server that comes back as a `FailedCommand` - not a
        # `MatterUnavailableError`, so a 500 "Internal Server Error",
        # the most meaningless of all responses.
        try:
            dataset = validated_dataset(manual_dataset, i18n.t(MANUAL_DATASET_ORIGIN_KEY))
        except ThreadDatasetUnavailableError as exc:
            # 422 like a rejected pairing code: the request is
            # well-formed, but its content is unusable. The reason
            # goes in the response, the dataset itself does NOT - it
            # contains the network key of the Thread network (see
            # `matter/otbr.py`). `str(exc)` also stays out: its wording
            # asks about the Border Router, and that has nothing to do
            # with an input field. It may go into the log, where it
            # names the length.
            logger.warning("Entered Thread dataset rejected: %s", exc)
            raise CommissionFailed(
                "other", i18n.t("api.devices.fail_manual_thread_dataset"), 422
            ) from exc
        try:
            await client.set_thread_dataset(dataset)
        except MatterUnavailableError as exc:
            raise CommissionFailed("matter_server_unreachable", str(exc), 502) from exc
    elif not client.thread_dataset_set:
        try:
            dataset = await fetch_dataset()
        except ThreadDatasetUnavailableError as exc:
            # NO abort: a WiFi device needs no Thread dataset at all,
            # and a setup without its own Border Router remains usable
            # as a result. The reason is only remembered in case
            # commissioning fails right afterwards - then it is the
            # likely cause and belongs in the message.
            missing_dataset_reason = str(exc)
            logger.warning("No Thread dataset available, commissioning proceeds without: %s", exc)
        else:
            try:
                await client.set_thread_dataset(dataset)
            except MatterUnavailableError as exc:
                raise CommissionFailed("matter_server_unreachable", str(exc), 502) from exc

    # `start()` returns a token identifying THIS attempt (final review
    # item 2): two POSTs in flight otherwise let the older one's
    # `finish()` reach into the newer attempt `start()` already replaced
    # it with, forcing it to `failed` with the older request's reason.
    # Every `finish()`/`sample()` call below is scoped to this token, so
    # a call that lands after a newer attempt has started is a no-op
    # instead of corrupting it.
    token = tracker.start(discriminator)
    # A closure bound to THIS attempt's token (final review item 1),
    # not `tracker.node_added` itself: matter-server's `NODE_ADDED`
    # listeners stay registered for as long as this call's own task
    # does, so with two POSTs in flight, device A's `NODE_ADDED` must
    # not advance attempt B just because B has since become the
    # tracker's current attempt.
    unsubscribe = client.add_node_added_listener(
        lambda node_id: tracker.node_added(node_id, token=token)
    )

    async def sample_while_waiting() -> None:
        # The tracker never samples itself (Task 3): this function owns the
        # cadence, because it is the only one that runs for as long as the
        # attempt does. Driving it from the status route instead would tie
        # the phases to a browser polling, and a failure whose `found` or
        # `connected` phase nobody observed would be classified `other`
        # instead of `connection_lost`.
        #
        # Non-raising at the source (review fix): `sample()` reads BlueZ
        # over D-Bus, and a single flaky call there must not end this
        # task - only `cancel()` below may. A `sample()` that raised
        # would otherwise propagate out of this loop into the `finally`
        # below, right into the exact re-raise this fix removes there.
        while True:
            try:
                await tracker.sample(token=token)
            except Exception:
                logger.exception("Sampling the commissioning attempt failed")
            await asyncio.sleep(tracker.sample_interval)

    sampler = asyncio.create_task(sample_while_waiting())
    try:
        snapshot = await client.commission_with_code(code)
    except CommissioningError as exc:
        # 422: the request itself was well-formed, but the device
        # rejected commissioning (wrong code, already in another
        # ecosystem, timeout during the interview) - see
        # CommissioningError.
        if missing_dataset_reason is not None:
            reason: Reason = "no_thread_network"
            await tracker.finish(reason, token=token)
            detail = _commissioning_detail(exc, missing_dataset_reason)
        else:
            reason = classify_failure(
                str(exc),
                tracker.phase_for(token) or "searching",
                discriminator=discriminator is not None,
                saw_match=tracker.saw_match(token),
            )
            await tracker.finish(reason, token=token)
            detail = _reason_detail(reason, exc, discriminator)
        raise CommissionFailed(reason, detail, 422) from exc
    except MatterUnavailableError as exc:
        await tracker.finish("matter_server_unreachable", token=token)
        raise CommissionFailed("matter_server_unreachable", str(exc), 502) from exc
    except Exception:
        # Any other failure (a bug, a matter-server response none of the
        # cases above anticipated) must still end the attempt - without
        # this, `phase` stays "searching" forever and the status route
        # never reports the failure, even though the request itself
        # does. Unlike the two branches above, this one does not build
        # an HTTPException: the exception here is unrecognised, so its
        # HTTP handling (whatever that is today) must stay unchanged -
        # only the tracker gets closed out.
        await tracker.finish("other", token=token)
        raise
    finally:
        sampler.cancel()
        # `gather(..., return_exceptions=True)` rather than
        # `suppress(CancelledError): await sampler` (review fix): a
        # sampler that already died on its own (see the comment in
        # `sample_while_waiting` above - now unreachable, but this stays
        # defensive) must not re-raise here, or the caller would report a
        # failure for a device that is actually commissioned. `suppress`
        # would also swallow a cancellation aimed at the REQUEST itself
        # (e.g. the client disconnecting), not only the one `cancel()`
        # just issued for the sampler.
        await asyncio.gather(sampler, return_exceptions=True)
        unsubscribe()

    # Everything from here on is follow-up work AFTER `commission_with_code`
    # already succeeded - the device is in the fabric. It must still be
    # guarded (review fix, final review 2026-09-22): the try/except/
    # finally above ends at `unsubscribe()`, and an unguarded exception
    # from `register_device`, `set_room`, `register_signals` or
    # `register_commands` used to leave the tracker's attempt stuck at
    # `joined`/`searching` forever - the status route then never
    # reported the failure the HTTP response itself already carried.
    # Unlike the two swallowed try/except blocks further down
    # (`set_online`, `follow` - deliberately non-fatal, see their own
    # comments: the device is already committed by that point), a
    # failure here is a genuine bug and must still surface as an error;
    # this guard only closes the tracker out, it does not change what
    # the caller sees - the `raise` below is unchanged and unwrapped.
    try:
        # The same sequence as in the CLI export (cli.py): register_device
        # before register_signals before register_commands, because both
        # need the freshly assigned device_id.
        device_id = store.register_device(snapshot, room=room)
        # Per its docstring, `register_device`'s `room` argument only takes
        # effect on a newly inserted row - an already known, active device
        # is caught there before the INSERT and simply keeps its previous
        # room, the selection from this request would be discarded without
        # comment (review finding, Finding 4). That is correct when
        # re-commissioning an unchanged, already known device WITHOUT a
        # chosen room - a re-commissioning must not silently clear a
        # maintained room. But if, as here, a room was explicitly selected
        # (the tile in the commissioning dialog offers it), that exact
        # choice should apply, whether the device was new or already
        # known - so it is additionally applied here via `set_room`
        # afterwards. `set_room`, not `rename_device`: the room ends up in
        # no export template, so re-commissioning with a chosen room must
        # not mark the device as "changed since" as a result.
        #
        # Deliberately UNCONDITIONAL, not only for the early-return case
        # (review finding, Finding 5): for a new device, `register_device`
        # has already set the room the same way through the INSERT row, so
        # the second write here is a no-op in that case (same
        # normalisation, `updated_at` remains untouched in both cases). A
        # case distinction "was the device new?" would need either a
        # return value from `register_device`, which its signature does
        # not provide today, or a second query before the call - the no-op
        # is the simpler and more robust choice.
        if room is not None:
            store.set_room(device_id, room)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))

        # The reachability of the new device MUST be seeded here, from
        # `snapshot.available` - exactly as `Runtime.seed_from_snapshot`
        # does for the already known devices when the bridge starts.
        #
        # The reason is an ordering that cannot be influenced from here
        # (recorded on 2026-09-04): matter-server reports `NODE_ADDED`
        # already WHILE `commission_with_code` is running
        # (`device_controller._setup_node` calls `signal_event(
        # EventType.NODE_ADDED, ...)` before the call even returns). At
        # that point, `register_device` above has not yet given the node a
        # device_id, and `BridgeMatterClient._dispatch_loop` accordingly
        # discards the notification ("update for unknown node ...
        # discarded") - the one opportunity at which `d<id>_online` would
        # have arisen by itself is thus gone before this function even gets
        # its turn again.
        #
        # For a device sitting quietly on the network, no further
        # `NODE_ADDED`/`NODE_UPDATED` notification follows after that, and
        # `_device_out` reads a missing key as `False`. The device
        # therefore showed as "offline" after commissioning and stayed
        # that way until the bridge's next restart - even though
        # matter-server had long since interviewed it and built a
        # subscription for it.
        #
        # From here on only follow-up work runs, and follow-up work must
        # not retroactively cancel the process: BEFORE `register_device`,
        # an error is a cancellation - the device is then not commissioned,
        # and an error message is the right response. AFTER it, the device
        # is in the fabric AND in the store, and an error message would
        # simply be wrong. It would lead into a dead end: the UI would
        # show "commissioning failed" and no device tile, the operator
        # would press "commission" again, and the printed code would
        # already be used up (422). The failure therefore belongs in the
        # log, not in the response. Concretely reachable via
        # `UdpSender.send` -> `socket.sendto`, which throws `OSError` when
        # the Miniserver's network is briefly down - hence `Exception` and
        # not just a single type.
        try:
            await runtime.set_online(device_id, snapshot.available)
        except Exception:
            logger.exception(
                "Could not seed reachability of freshly commissioned device %s - the "
                "device is commissioned, but its tile shows offline until the next "
                "notification from matter-server",
                device_id,
            )

        # Only now, after `register_device`: `follow` resolves the
        # node ID via the store, and before that there would be nothing to
        # resolve there - the same race that `NODE_ADDED` already lost
        # (see the comment above and the docstring of `_follow_node`).
        # Creates the attribute subscriptions for this device and seeds
        # its values, so the signals show numbers immediately instead of
        # dashes - previously this required a restart of the bridge.
        #
        # `seed_even_without_new_paths`, because the subscriptions are, as
        # a rule, already in place by this point: the same `NODE_ADDED`
        # run that lost the reachability above has already had
        # `BridgeMatterClient`'s dispatch loop subscribe to every path of
        # this node - just without a device_id, i.e. without seeding.
        # Without the flag, this call here would find an empty diff and
        # turn back before seeding; the initial values would then never
        # arrive, and a static path (voltage with no load, battery level,
        # the off state of a plug) would remain a dash, because
        # matter-server suppresses unchanged values.
        #
        # Also follow-up work, also safeguarded (see above): the most
        # likely scenario is a matter-server that restarts immediately
        # after commissioning - then `follow` runs into
        # `_require_upstream` and throws `MatterUnavailableError`, even
        # though the device is fully commissioned. Without values, but
        # commissioned: the signal rows exist (they are created by
        # `register_signals` above).
        #
        # That they also fill in again is NOT carried by the next
        # `NODE_ADDED`/`NODE_UPDATED` alone - its diff is empty for a
        # device that has long been subscribed, and without the flag the
        # call would not even get to the seeding. It is carried by
        # `_seed_pending` in `BridgeMatterClient`: the bridge remembers
        # every node it still owes a snapshot - whether because the store
        # did not know it yet, or because the handler threw during
        # seeding -, and the next `_follow_node` from the dispatch loop
        # catches up on it. That is why the assurance here holds for BOTH
        # cases: a failure before subscribing as well as one after (say, a
        # `sqlite3.OperationalError` under concurrent write load from the
        # resend loop).
        try:
            await client.follow(snapshot.address, seed_even_without_new_paths=True)
        except Exception:
            logger.exception(
                "Could not catch up on subscriptions of freshly commissioned device %s "
                "- the device is commissioned, but its signals remain without values "
                "until the next notification from matter-server",
                device_id,
            )
        await tracker.finish(None, token=token)
        return CommissionResult(device_id=device_id, snapshot=snapshot)
    except Exception:
        await tracker.finish("other", token=token)
        raise
