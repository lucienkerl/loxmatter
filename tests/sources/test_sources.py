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

"""The registry that dispatches by technology (design 2026-09-11,
section 3.1)."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter import i18n
from loxmatter.sources import (
    SOURCE_CALL_TIMEOUT_SECONDS,
    DeviceCall,
    DeviceUnreachableError,
    SourceNotConfiguredError,
    Sources,
    technology_display_name,
)


class _FakeSource:
    def __init__(self, technology: str, *, connected: bool = True) -> None:
        self.technology = technology
        self.connected = connected
        self.sent: list[DeviceCall] = []

    async def send(self, call: DeviceCall) -> None:
        self.sent.append(call)


def _call(technology: str) -> DeviceCall:
    return DeviceCall(
        technology=technology,
        address="1",
        endpoint=1,
        cluster_id=6,
        command_id=1,
    )


async def test_send_reaches_the_source_of_the_calls_technology():
    """Two sources, so a registry that always answers with the first one
    cannot pass. Fault to prove it: make `get` return
    `next(iter(self._by_technology.values()))`."""
    matter = _FakeSource("matter")
    zigbee = _FakeSource("zigbee")
    sources = Sources([matter, zigbee])

    await sources.send(_call("zigbee"))

    assert zigbee.sent == [_call("zigbee")]
    assert matter.sent == []


async def test_an_unconfigured_technology_raises_with_its_name():
    """`.technology` stays the raw, lowercase value callers compare
    against; the message text goes through `technology_display_name` (see
    that function's docstring) - hence the two different spellings below."""
    sources = Sources([_FakeSource("matter")])
    with pytest.raises(SourceNotConfiguredError) as caught:
        await sources.send(_call("zigbee"))
    assert caught.value.technology == "zigbee"
    assert str(caught.value) == i18n.t(
        "api.errors.source_not_configured", technology=technology_display_name("zigbee")
    )


def test_two_sources_of_one_technology_are_a_wiring_error():
    with pytest.raises(ValueError, match="matter"):
        Sources([_FakeSource("matter"), _FakeSource("matter")])


def test_all_connected_needs_every_source():
    """Fault to prove it: replace `all(` with `any(` in `all_connected`."""
    assert Sources([_FakeSource("matter"), _FakeSource("zigbee")]).all_connected()
    assert not Sources(
        [_FakeSource("matter"), _FakeSource("zigbee", connected=False)]
    ).all_connected()


def test_all_keeps_the_order_sources_were_given_in():
    matter = _FakeSource("matter")
    zigbee = _FakeSource("zigbee")
    assert Sources([matter, zigbee]).all() == [matter, zigbee]


def test_replace_swaps_the_source_of_one_technology():
    matter = _FakeSource("matter")
    old_zigbee = _FakeSource("zigbee")
    new_zigbee = _FakeSource("zigbee")
    sources = Sources([matter, old_zigbee])

    sources.replace("zigbee", new_zigbee)

    assert sources.get("zigbee") is new_zigbee
    assert sources.get("matter") is matter


def test_replace_with_none_removes_the_source():
    sources = Sources([_FakeSource("matter"), _FakeSource("zigbee")])

    sources.replace("zigbee", None)

    with pytest.raises(SourceNotConfiguredError):
        sources.get("zigbee")


def test_replace_rejects_a_source_for_a_different_technology():
    sources = Sources([_FakeSource("matter")])
    with pytest.raises(ValueError, match="zigbee"):
        sources.replace("matter", _FakeSource("zigbee"))


async def test_a_source_call_that_never_returns_is_abandoned(monkeypatch):
    """zigpy waits 5 s per attempt for a mains device and 28 s for an end
    device or one with no node descriptor, and retries twice (research E.6,
    R1 section 8). Nothing on the command path bounded that before, so a
    Loxone virtual output aimed at a sleeping button held an HTTP request
    for over a minute.

    Fault to prove it: remove the `asyncio.wait_for` in `Sources.send`. The
    test then hangs until pytest's own timeout rather than failing, which is
    itself the report - note it as such if it happens."""
    monkeypatch.setattr("loxmatter.sources.SOURCE_CALL_TIMEOUT_SECONDS", 0.05)
    started = asyncio.Event()

    class NeverAnswers:
        technology = "zigbee"
        connected = True

        async def send(self, call: DeviceCall) -> None:
            started.set()
            await asyncio.sleep(3600)

    sources = Sources([NeverAnswers()])
    with pytest.raises(DeviceUnreachableError):
        await asyncio.wait_for(sources.send(_call("zigbee")), timeout=5)
    assert started.is_set()


def test_the_bound_is_the_one_the_module_publishes():
    """Read from the module, not retyped: a test with its own copy of 10.0
    would keep passing after somebody changed the real bound."""
    assert SOURCE_CALL_TIMEOUT_SECONDS == 10.0
