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

import asyncio
import socket

import pytest

from loxmatter.api.diagnostics import DatagramLogEntry
from loxmatter.loxone.sender import UdpSender


@pytest.fixture
def receiver():
    """A UDP socket on 127.0.0.1 - never leaves the machine."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    yield sock
    sock.close()


def received(sock: socket.socket) -> list[bytes]:
    packets = []
    while True:
        try:
            packets.append(sock.recv(4096))
        except BlockingIOError:
            return packets


async def test_sends_the_expected_datagram(receiver):
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_2_power", 0.0003)
    await asyncio.sleep(0.05)
    assert received(receiver) == [b"d1_2_power:0.0003"]
    await sender.close()


async def test_unchanged_value_is_not_resent(receiver):
    """Debouncing: a sensor that reports the same value every second does not flood."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    assert await sender.send("d1_1_temp", 21.5) is True
    assert await sender.send("d1_1_temp", 21.5) is False
    await asyncio.sleep(0.05)
    assert len(received(receiver)) == 1
    await sender.close()


async def test_changed_value_is_sent(receiver):
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.6) is True
    await asyncio.sleep(0.05)
    assert len(received(receiver)) == 2
    await sender.close()


async def test_force_resends_an_unchanged_value(receiver):
    """The full resend after a Miniserver restart must bypass debouncing."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.5, force=True) is True
    await sender.close()


async def test_the_forced_field_reflects_why_a_datagram_was_sent_not_when(receiver):
    """Records the case that disproved the WebUI's earlier time heuristic
    (follow-up fix task 6, 2026-09-03): `Runtime.on_event` (loxone/runtime.py)
    sends a pulse and its counter immediately one after the other, without
    `force` - two real value changes, microseconds apart. A heuristic based
    on arrival rate would have marked both as noise; `DatagramLogEntry.forced`
    instead correctly distinguishes by WHY it was sent: `False` for every
    real value change (even closely spaced ones), `True` only for the two
    `force=True` callers, heartbeat and full resend."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)

    # Pulse and counter, as Runtime.on_event sends them - directly one
    # after the other, with no wait in between.
    await sender.send("d1_1_press", True)
    await sender.send("d1_1_press_n", 1)
    # Heartbeat and full resend - the only two callers that set force=True.
    await sender.send("bridge_alive", True, force=True)
    await sender.send("d1_1_press", True, force=True)

    assert [entry.forced for entry in sender.datagram_log] == [False, False, True, True]
    await sender.close()


async def test_rate_limit_staggers_a_burst(receiver):
    """Spec 6.4: staggered to about 50 datagrams per second."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port, rate_limit=100.0)
    start = asyncio.get_running_loop().time()
    for i in range(10):
        await sender.send(f"d1_1_a{i}", i)
    duration = asyncio.get_running_loop().time() - start
    assert duration >= 0.09
    await sender.close()


async def test_send_after_close_raises():
    sender = UdpSender("127.0.0.1", 7000)
    await sender.close()
    with pytest.raises(RuntimeError, match="closed"):
        await sender.send("d1_1_temp", 21.5)


async def test_the_closed_message_follows_the_selected_language():
    """The "closed" message must run through i18n.t() like every other
    user-facing string - not sit hardcoded in German, unreachable by the
    language switcher."""
    from loxmatter import i18n

    sender = UdpSender("127.0.0.1", 7000)
    await sender.close()

    i18n.set_language("en")
    with pytest.raises(RuntimeError) as english:
        await sender.send("d1_1_temp", 21.5)

    i18n.set_language("de")
    with pytest.raises(RuntimeError) as german:
        await sender.send("d1_1_temp", 21.5)
    i18n.set_language("en")

    assert str(english.value) and str(german.value)
    assert str(english.value) != str(german.value)
    assert str(german.value) == "UdpSender ist geschlossen"


async def test_close_during_in_flight_send_does_not_crash(receiver):
    """A close() while a send is parked in the rate-limit sleep must never
    trigger an AttributeError from an already-closed socket - either the
    send completes cleanly, or it sees the documented RuntimeError."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port, rate_limit=10.0)
    await sender.send("d1_1_a", 1)

    async def delayed_send() -> bool | RuntimeError:
        try:
            return await sender.send("d1_1_b", 2)
        except RuntimeError as error:
            return error

    send_task = asyncio.create_task(delayed_send())
    await asyncio.sleep(0.02)
    close_task = asyncio.create_task(sender.close())

    result = await send_task
    await close_task

    assert result is True or isinstance(result, RuntimeError)


async def test_close_is_idempotent():
    sender = UdpSender("127.0.0.1", 7000)
    await sender.close()
    await sender.close()


def test_a_datagram_observer_sees_every_send():
    """Also what the runtime observers miss: the full resend and the
    falling edge of a pulse. That is why the recording hangs off the
    sender, not the runtime."""
    sender = UdpSender("127.0.0.1", 7000)
    seen: list[str] = []

    def observer(entry: DatagramLogEntry) -> None:
        seen.append(f"{entry.key}={entry.value}")

    sender.add_datagram_observer(observer)

    asyncio.run(sender.send("d1_1_onoff", True))
    asyncio.run(sender.send("d1_1_onoff", False, force=True))

    assert seen == ["d1_1_onoff=1", "d1_1_onoff=0"]


def test_a_throwing_observer_does_not_break_the_send_path():
    """A diagnostic tool that halts the path it observes would be worse
    than none at all - the same reasoning as for the recording itself
    (see `_record_sent`)."""

    def _throwing_observer(entry: DatagramLogEntry) -> None:
        raise RuntimeError("kaputt")

    sender = UdpSender("127.0.0.1", 7000)
    sender.add_datagram_observer(_throwing_observer)

    asyncio.run(sender.send("d1_1_onoff", True))

    assert [entry.key for entry in sender.datagram_log] == ["d1_1_onoff"]


def test_a_removed_observer_is_no_longer_called():
    sender = UdpSender("127.0.0.1", 7000)
    seen: list[str] = []

    def observer(entry: DatagramLogEntry) -> None:
        seen.append(entry.key)

    sender.add_datagram_observer(observer)
    sender.remove_datagram_observer(observer)

    asyncio.run(sender.send("d1_1_onoff", True))

    assert seen == []
