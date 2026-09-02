import asyncio
import socket

import pytest

from loxmatter.loxone.sender import UdpSender


@pytest.fixture
def empfaenger():
    """Ein UDP-Socket auf 127.0.0.1 - verlaesst die Maschine nicht."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    yield sock
    sock.close()


def empfangen(sock: socket.socket) -> list[bytes]:
    pakete = []
    while True:
        try:
            pakete.append(sock.recv(4096))
        except BlockingIOError:
            return pakete


async def test_sends_the_expected_datagram(empfaenger):
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_2_power", 0.0003)
    await asyncio.sleep(0.05)
    assert empfangen(empfaenger) == [b"d1_2_power:0.0003"]
    await sender.close()


async def test_unchanged_value_is_not_resent(empfaenger):
    """Entprellung: ein Sensor, der jede Sekunde denselben Wert meldet, flutet nicht."""
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    assert await sender.send("d1_1_temp", 21.5) is True
    assert await sender.send("d1_1_temp", 21.5) is False
    await asyncio.sleep(0.05)
    assert len(empfangen(empfaenger)) == 1
    await sender.close()


async def test_changed_value_is_sent(empfaenger):
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.6) is True
    await asyncio.sleep(0.05)
    assert len(empfangen(empfaenger)) == 2
    await sender.close()


async def test_force_resends_an_unchanged_value(empfaenger):
    """Der Full-Resend nach einem Miniserver-Neustart muss die Entprellung umgehen."""
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port)
    await sender.send("d1_1_temp", 21.5)
    assert await sender.send("d1_1_temp", 21.5, force=True) is True
    await sender.close()


async def test_rate_limit_staggers_a_burst(empfaenger):
    """Spec 6.4: gestaffelt auf etwa 50 Datagramme pro Sekunde."""
    host, port = empfaenger.getsockname()
    sender = UdpSender(host, port, rate_limit=100.0)
    start = asyncio.get_running_loop().time()
    for i in range(10):
        await sender.send(f"d1_1_a{i}", i)
    dauer = asyncio.get_running_loop().time() - start
    assert dauer >= 0.09
    await sender.close()


async def test_send_after_close_raises():
    sender = UdpSender("127.0.0.1", 7000)
    await sender.close()
    with pytest.raises(RuntimeError, match="geschlossen"):
        await sender.send("d1_1_temp", 21.5)
