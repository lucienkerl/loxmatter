"""Ersetzt den Loxone Miniserver beim Entwickeln.

Der dritte Punkt unten ist der eigentliche Gewinn: er vergleicht, welche
Signale eine erzeugte Vorlage ankuendigt, mit denen, die tatsaechlich ein
Datagramm geschickt haben. Ein exportiertes Signal, das nie feuert, ist ein
Mapping-Fehler - und ohne diesen Abgleich faellt er erst in Loxone auf, wo er
wie ein Geraetefehler aussieht.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

# Liest genau das Attribut, das render_virtual_in_udp schreibt (siehe
# export/documents.py): Check="<schluessel>:\v".
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
    """Nimmt UDP-Datagramme entgegen wie der echte Miniserver - ohne ihn.

    `on_received`/`on_malformed` sind fuer `loxmatter fake-miniserver`
    gedacht (Echtzeit-Ausgabe mit Zeitstempel) - `received`/`malformed`
    bleiben die primaere, im Test abgefragte Quelle und wachsen immer,
    unabhaengig davon, ob ein Callback gesetzt ist.
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

    def silent_keys(self, template: Path) -> list[str]:
        """Signale, die die Vorlage ankuendigt, die aber nie ein Datagramm schickten."""
        announced = set(_CHECK.findall(template.read_text(encoding="utf-8-sig")))
        seen = {key for key, _ in self.received}
        return sorted(announced - seen)
