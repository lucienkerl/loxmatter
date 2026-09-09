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

"""Stands in for the Loxone Miniserver during development.

The third point below is the real payoff: it compares which signals a
generated template announces against the ones that actually sent a
datagram. An exported signal that never fires is a mapping bug - and
without this comparison it would only show up in Loxone, where it looks
like a device fault.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

# Reads exactly the attribute that render_virtual_in_udp writes (see
# export/documents.py): Check="<key>:\v".
_CHECK = re.compile(r'Check="([^:"]+):\\v"')


class _DatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: FakeMiniserver) -> None:
        self._server = server

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        text = data.decode(errors="replace")
        key, sep, value = text.partition(":")
        if not sep:
            self._server.malformed.append(data)
            if self._server.on_malformed is not None:
                self._server.on_malformed(data)
            return
        self._server.received.append((key, value))
        if self._server.on_received is not None:
            self._server.on_received(key, value)


class FakeMiniserver:
    """Accepts UDP datagrams like the real Miniserver - without needing one.

    `on_received`/`on_malformed` are meant for `loxmatter fake-miniserver`
    (real-time output with timestamps) - `received`/`malformed` remain the
    primary source queried in tests, and keep growing regardless of
    whether a callback is set.
    """

    def __init__(
        self,
        port: int = 7000,
        host: str = "127.0.0.1",
        *,
        on_received: Callable[[str, str], None] | None = None,
        on_malformed: Callable[[bytes], None] | None = None,
    ) -> None:
        self._host, self._port = host, port
        self.received: list[tuple[str, str]] = []
        self.malformed: list[bytes] = []
        self.on_received = on_received
        self.on_malformed = on_malformed
        self._transport: asyncio.DatagramTransport | None = None

    @property
    def port(self) -> int:
        if self._transport is None:
            return self._port
        return int(self._transport.get_extra_info("sockname")[1])

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DatagramProtocol(self), local_addr=(self._host, self._port)
        )
        self._transport = transport

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def announced_keys(self, template: Path) -> set[str]:
        """Signals the template announces via the `Check` attribute.

        Kept separate from `silent_keys` so a caller (see `loxmatter
        fake-miniserver`) can distinguish a template that simply carries NO
        Check attribute at all (e.g. a VO_ file or an empty template) -
        where there is nothing to check - from being confused with the
        case where all announced signals were seen.
        """
        return set(_CHECK.findall(template.read_text(encoding="utf-8-sig")))

    def silent_keys(self, template: Path) -> list[str]:
        """Signals the template announces but that never sent a datagram."""
        seen = {key for key, _ in self.received}
        return sorted(self.announced_keys(template) - seen)
