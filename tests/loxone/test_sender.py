# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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
    """Ein UDP-Socket auf 127.0.0.1 - verlaesst die Maschine nicht."""
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
    """Entprellung: ein Sensor, der jede Sekunde denselben Wert meldet, flutet nicht."""
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
    """Der Full-Resend nach einem Miniserver-Neustart muss die Entprellung umgehen."""
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.5, force=True) is True
    await sender.close()


async def test_rate_limit_staggers_a_burst(receiver):
    """Spec 6.4: gestaffelt auf etwa 50 Datagramme pro Sekunde."""
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
    with pytest.raises(RuntimeError, match="geschlossen"):
        await sender.send("d1_1_temp", 21.5)


async def test_close_during_in_flight_send_does_not_crash(receiver):
    """Ein close() waehrend eines im Rate-Limit-Schlaf parkierten Sendevorgangs
    darf niemals einen AttributeError durch einen bereits geschlossenen Socket
    ausloesen - entweder schliesst der Sendevorgang sauber ab, oder er sieht das
    dokumentierte RuntimeError."""
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
    """Auch das, was die Laufzeit-Beobachter auslassen: den Full-Resend und
    das Absenken eines Impulses. Das ist der Grund, warum der Mitschnitt am
    Sender haengt und nicht an der Laufzeit."""
    sender = UdpSender("127.0.0.1", 7000)
    seen: list[str] = []

    def observer(entry: DatagramLogEntry) -> None:
        seen.append(f"{entry.key}={entry.value}")

    sender.add_datagram_observer(observer)

    asyncio.run(sender.send("d1_1_onoff", True))
    asyncio.run(sender.send("d1_1_onoff", False, force=True))

    assert seen == ["d1_1_onoff=1", "d1_1_onoff=0"]


def test_a_throwing_observer_does_not_break_the_send_path():
    """Ein Diagnosewerkzeug, das den Pfad anhaelt, den es beobachtet, waere
    schlimmer als gar keins - dieselbe Begruendung wie beim Mitschreiben
    selbst (siehe `_record_sent`)."""

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
