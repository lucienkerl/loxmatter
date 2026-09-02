import asyncio
import socket
from pathlib import Path

from loxmatter.devtools.fake_miniserver import FakeMiniserver

REFERENCE = Path(__file__).parents[1] / "fixtures" / "loxone" / "VIU_Referenz.xml"


async def test_records_incoming_datagrams():
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"d1_1_temp:21.5", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert fake.received == [("d1_1_temp", "21.5")]


async def test_malformed_datagram_is_recorded_not_dropped():
    """Ein Datagramm ohne Doppelpunkt ist ein Fehler, den man sehen will."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"kaputt", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert fake.malformed == [b"kaputt"]


async def test_silent_keys_names_signals_that_never_arrived():
    """Der eigentliche Nutzen: exportierte Signale finden, die nie feuern."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"d1_1_beispiel:1", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    silent = fake.silent_keys(REFERENCE)
    await fake.stop()
    sock.close()
    assert "d1_1_beispiel" not in silent
    assert silent  # die Referenz traegt mehr als einen Befehl


def test_silent_keys_reads_the_check_attribute():
    fake = FakeMiniserver(port=0)
    assert all(not k.endswith(":\\v") for k in fake.silent_keys(REFERENCE))


def test_announced_keys_lists_every_check_attribute_of_the_template():
    fake = FakeMiniserver(port=0)
    assert fake.announced_keys(REFERENCE) == {"d1_1_beispiel1", "d1_1_beispiel2"}


def test_announced_keys_is_empty_for_a_template_without_check_attributes(tmp_path):
    """Eine VO_-Vorlage oder eine leere Vorlage traegt kein Check-Attribut - das
    ist etwas anderes als eine Vorlage, deren Signale alle gesehen wurden
    (siehe cli._silent_keys_report)."""
    empty = tmp_path / "VO_ohne_check.xml"
    empty.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<VirtualOut Title="Ohne Check" Comment="" Address="" Port="80">\n'
        "</VirtualOut>\n",
        encoding="utf-8",
    )
    fake = FakeMiniserver(port=0)
    assert fake.announced_keys(empty) == set()
    assert fake.silent_keys(empty) == []


async def test_on_received_callback_fires_for_well_formed_datagrams():
    """Fuer `loxmatter fake-miniserver`, das jedes Datagramm mit Zeitstempel
    drucken soll, statt received/malformed abzufragen."""
    seen: list[tuple[str, str]] = []
    fake = FakeMiniserver(port=0, on_received=lambda key, value: seen.append((key, value)))
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"d1_1_temp:21.5", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert seen == [("d1_1_temp", "21.5")]


async def test_on_malformed_callback_fires_for_broken_datagrams():
    seen: list[bytes] = []
    fake = FakeMiniserver(port=0, on_malformed=seen.append)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"kaputt", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert seen == [b"kaputt"]
