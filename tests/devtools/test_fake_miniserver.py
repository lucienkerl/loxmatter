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
from pathlib import Path

from loxmatter.devtools.fake_miniserver import FakeMiniserver

REFERENCE = Path(__file__).parents[1] / "fixtures" / "loxone" / "VIU_reference.xml"


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
    """A datagram without a colon is an error you want to see."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"kaputt", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    await fake.stop()
    sock.close()
    assert fake.malformed == [b"kaputt"]


async def test_silent_keys_names_signals_that_never_arrived():
    """The real point: find exported signals that never fire."""
    fake = FakeMiniserver(port=0)
    await fake.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"d1_1_beispiel:1", ("127.0.0.1", fake.port))
    await asyncio.sleep(0.1)
    silent = fake.silent_keys(REFERENCE)
    await fake.stop()
    sock.close()
    assert "d1_1_beispiel" not in silent
    assert silent  # the reference carries more than one command


def test_silent_keys_reads_the_check_attribute():
    fake = FakeMiniserver(port=0)
    assert all(not k.endswith(":\\v") for k in fake.silent_keys(REFERENCE))


def test_announced_keys_lists_every_check_attribute_of_the_template():
    fake = FakeMiniserver(port=0)
    assert fake.announced_keys(REFERENCE) == {"d1_1_beispiel1", "d1_1_beispiel2"}


def test_announced_keys_is_empty_for_a_template_without_check_attributes(tmp_path):
    """A VO_ template or an empty template carries no check attribute -
    that's something different from a template whose signals were all
    seen (see cli._silent_keys_report)."""
    empty = tmp_path / "VO_without_check.xml"
    empty.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<VirtualOut Title="Without Check" Comment="" Address="" Port="80">\n'
        "</VirtualOut>\n",
        encoding="utf-8",
    )
    fake = FakeMiniserver(port=0)
    assert fake.announced_keys(empty) == set()
    assert fake.silent_keys(empty) == []


async def test_on_received_callback_fires_for_well_formed_datagrams():
    """For `loxmatter fake-miniserver`, which is meant to print every
    datagram with a timestamp instead of polling received/malformed."""
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
