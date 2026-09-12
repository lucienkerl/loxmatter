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

"""The Thread dataset from the border router (`matter/otbr.py`).

The recorded real-world case: matter-server keeps the Thread credentials
ONLY in memory (`_thread_credentials_set: bool = False` in
`matter_server/server/device_controller.py`, set solely by
`set_thread_operational_dataset`). After every restart of the service they
are gone, and every Thread device fails commissioning with "Required network
information not provided in commissioning parameters" - visible in the UI
only as "Commission with code failed for node N".

This test covers the source from which the bridge has since fetched the
dataset itself, instead of waiting for it to be entered by hand.
"""

from __future__ import annotations

from typing import Any, Self

import pytest

from loxmatter import i18n
from loxmatter.matter.otbr import (
    DEFAULT_OTBR_URL,
    ThreadDatasetUnavailableError,
    current_thread_channel,
    fetch_active_dataset,
    thread_channel_from_dataset,
)

# A recorded but unusable dataset: the same shape as a real one (hex TLV),
# but no network key that exists anywhere. A real dataset is a credential
# and belongs neither in the repository nor in a log (see
# deploy/testhost/README.md).
FAKE_DATASET = "0e080000000000010000" + "00" * 30


class FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class FakeSession:
    """Stands in for `aiohttp.ClientSession` - only `get()` and `close()`,
    which is all `fetch_active_dataset` needs (same pattern as `FakeSession`
    in `test_client_commissioning.py`)."""

    def __init__(self, status: int = 200, body: str = FAKE_DATASET) -> None:
        self.status = status
        self.body = body
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.closed = False
        self.raise_on_get: Exception | None = None

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.requests.append((url, headers or {}))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return FakeResponse(self.status, self.body)

    async def close(self) -> None:
        self.closed = True


async def test_reads_the_active_dataset_from_the_border_router() -> None:
    session = FakeSession()

    dataset = await fetch_active_dataset(
        "http://otbr.example:8081", session_factory=lambda: session
    )

    assert dataset == FAKE_DATASET
    url, headers = session.requests[0]
    assert url == "http://otbr.example:8081/node/dataset/active"
    # Without this header, OTBR's REST interface returns the dataset as a
    # JSON structure instead of hex TLV - and only the latter is accepted by
    # `set_thread_operational_dataset`.
    assert headers["Accept"] == "text/plain"


async def test_closes_the_session_even_when_the_request_fails() -> None:
    session = FakeSession()
    session.raise_on_get = OSError("Netz weg")

    with pytest.raises(ThreadDatasetUnavailableError):
        await fetch_active_dataset(session_factory=lambda: session)

    assert session.closed


async def test_a_border_router_without_a_thread_network_is_reported_as_such() -> None:
    """OTBR responds with 409 as long as no active dataset exists - the
    border router is running, but has not formed a network."""
    session = FakeSession(status=409, body="")

    with pytest.raises(ThreadDatasetUnavailableError) as excinfo:
        await fetch_active_dataset(session_factory=lambda: session)

    assert "409" in str(excinfo.value)


async def test_a_response_that_is_not_hex_is_refused() -> None:
    """Otherwise an HTML error page would land at matter-server as the
    "dataset", which it rejects only much later, via `bytes.fromhex`, with
    no relation to the actual cause."""
    session = FakeSession(body="<html>Not Found</html>")

    with pytest.raises(ThreadDatasetUnavailableError):
        await fetch_active_dataset(session_factory=lambda: session)


async def test_an_odd_number_of_hex_characters_is_refused() -> None:
    """A hex TLV consists of bytes - an odd number of hex characters cannot
    be one. Each character on its own is hex, so the character-class check
    let it through; at matter-server, `bytes.fromhex` then failed with
    "odd-length string", which comes back as an `UnknownError` - not a
    `MatterUnavailableError`, so 500 instead of a usable message."""
    session = FakeSession(body=FAKE_DATASET[:-1])

    with pytest.raises(ThreadDatasetUnavailableError) as excinfo:
        await fetch_active_dataset(session_factory=lambda: session)

    message = str(excinfo.value)
    # The message names the length, never the dataset: it contains the
    # network key of the Thread network, and even part of it would be too much.
    assert str(len(FAKE_DATASET) - 1) in message
    assert FAKE_DATASET[:12] not in message


async def test_the_dataset_stays_out_of_the_message_in_german_too() -> None:
    """This module's messages have run through `i18n.t()` since the i18n
    phase. The reason they name only length, address and status holds
    regardless of language: the dataset carries the network key of the
    Thread network. A translation that inserted it would be a leak - so
    this explicitly checks the second language too."""
    i18n.set_language("de")
    session = FakeSession(body=FAKE_DATASET[:-1])

    with pytest.raises(ThreadDatasetUnavailableError) as excinfo:
        await fetch_active_dataset(session_factory=lambda: session)

    message = str(excinfo.value)
    assert str(len(FAKE_DATASET) - 1) in message
    assert FAKE_DATASET[:12] not in message


async def test_an_empty_response_is_refused() -> None:
    session = FakeSession(body="   \n")

    with pytest.raises(ThreadDatasetUnavailableError):
        await fetch_active_dataset(session_factory=lambda: session)


async def test_falls_back_to_the_border_router_on_this_host() -> None:
    """The normal case for the stack from `deploy/testhost/docker-compose.yml`:
    OTBR and this bridge share the same network namespace via
    `network_mode: host`, where OTBR's REST interface listens on
    127.0.0.1:8081."""
    session = FakeSession()

    await fetch_active_dataset(session_factory=lambda: session)

    assert session.requests[0][0].startswith(DEFAULT_OTBR_URL)


async def test_an_explicit_address_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """For setups with a border router on a different host."""
    monkeypatch.setenv("LOXMATTER_OTBR_URL", "http://10.0.1.99:8081")
    session = FakeSession()

    await fetch_active_dataset(session_factory=lambda: session)

    assert session.requests[0][0] == "http://10.0.1.99:8081/node/dataset/active"


# --- The channel Zigbee has to stay off ------------------------------------

# A well-formed active dataset carrying two TLVs: an Active Timestamp
# (type 0x0e, ignored by this parser) ahead of a Channel TLV (type 0x00,
# length 3: one byte of channel page, then the channel itself as a 2-byte
# big-endian integer - MeshCoP TLV numbering, Thread 1.3 "Network
# Management TLVs"). Not first in the stream on purpose: a parser that
# assumed the Channel TLV came first would pass against a fixture shaped
# like this one and fail against a real border router that orders its TLVs
# differently.
DATASET_ON_CHANNEL_15 = "0e08" + "00" * 8 + "000300000f"


def test_the_channel_tlv_is_found_regardless_of_where_it_sits_in_the_dataset():
    """MeshCoP TLVs are a flat, ordered stream with no fixed layout beyond
    "type, length, value, repeat" - OTBR is free to write them in any
    order.

    Fault to prove it: read the first three value bytes of the dataset as
    the Channel TLV unconditionally, instead of scanning for type `0x00`.
    This test's fixture, with the Channel TLV second, then reads as channel
    0 - or raises, depending on how the first TLV's bytes are misread."""
    assert thread_channel_from_dataset(DATASET_ON_CHANNEL_15) == 15


def test_a_dataset_with_no_channel_tlv_answers_none_not_an_error():
    """Every reason this parser cannot name a channel must read exactly
    like "no border router at all" to `channels_excluding` - a courtesy
    lost is not a reason to stop Zigbee from forming.

    Fault to prove it: raise instead of returning `None` when the stream
    runs out without a type-`0x00` TLV. Building a `ZigbeeSource` then
    fails outright against a real, valid dataset that simply omits the
    Channel TLV (permitted by the TLV format itself)."""
    assert thread_channel_from_dataset("0e08" + "00" * 8) is None


def test_a_truncated_tlv_stream_answers_none_rather_than_indexing_past_the_end():
    """The shape of a response cut off mid-transfer, or simply corrupt: a
    length byte claiming more value bytes than remain in the string.

    Fault to prove it: slice the value out of the stream without first
    checking that the claimed length fits. This test then fails with an
    `IndexError`/`ValueError` instead of reading `None` - turning a
    malformed dataset into a crash on the path that builds every
    `ZigbeeSource`."""
    assert thread_channel_from_dataset("0e08" + "00" * 2) is None


async def test_current_thread_channel_answers_none_when_the_border_router_is_absent():
    """The common case on any bridge with no Thread border router at all:
    `fetch_active_dataset` raises `ThreadDatasetUnavailableError` the
    instant the connection is refused. That must read as "nothing to
    avoid", never propagate - a missing OPTIONAL border router must not
    stop `ZigbeeRuntime` from building a `ZigbeeSource` at all, the same
    rule Task 10 already applies to a missing Zigbee radio itself.

    Fault to prove it: let `ThreadDatasetUnavailableError` escape instead of
    catching it. Building a `ZigbeeSource` then fails on every installation
    without a Thread border router configured - most of them."""
    session = FakeSession()
    session.raise_on_get = OSError("Connection refused")
    assert await current_thread_channel(session_factory=lambda: session) is None


async def test_current_thread_channel_reads_the_real_fetch_and_parse_path():
    """The end-to-end call `ZigbeeRuntime`'s builder actually makes: fetch,
    then parse, through the same fake session `fetch_active_dataset`'s own
    tests already use.

    Fault to prove it: answer `None` without parsing what was fetched -
    which is what the whole chain degrades to if the parse is dropped or
    its result discarded. The exclusion then goes silently inert again,
    which is precisely the state Task 7 left `channels_excluding` in.

    The plan's own suggested fault for this test - parsing the raw,
    unvalidated body rather than `fetch_active_dataset`'s return value -
    was INJECTED AND DID NOT FAIL, so it is not the fault this test
    catches. `bytes.fromhex` skips ASCII whitespace on its own (measured
    against the installed interpreter, see the test below), so a padded
    body parses either way."""
    session = FakeSession(status=200, body=DATASET_ON_CHANNEL_15)
    assert await current_thread_channel(session_factory=lambda: session) == 15


async def test_a_padded_response_still_yields_its_channel():
    """A border router whose response carries a trailing newline.

    MEASURED, and not what it first looks like: this passes because
    `bytes.fromhex` ignores ASCII whitespace, NOT because
    `validated_dataset` stripped it first. Both hold, so the padding is
    harmless twice over - the point of pinning it here is that neither
    layer may start rejecting it.

    There is therefore no fault in `current_thread_channel` that this test
    alone catches; it is a behaviour pin, not a guard, and saying so is
    better than claiming a protection it does not provide."""
    session = FakeSession(status=200, body=DATASET_ON_CHANNEL_15 + "\n")
    assert await current_thread_channel(session_factory=lambda: session) == 15
    assert thread_channel_from_dataset(DATASET_ON_CHANNEL_15 + "\n") == 15
