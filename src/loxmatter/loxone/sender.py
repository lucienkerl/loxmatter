"""Verschickt Werte als UDP-Datagramme an den Miniserver.

Kennt kein Matter. Er bekommt fertige Schluessel und fertige Werte.

Zwei Eigenschaften sind nicht optional:

Entprellung - ein Matter-Geraet meldet einen Messwert gerne im Sekundentakt,
auch wenn er sich nicht aendert. Unveraenderte Werte erneut zu schicken kostet
nur Last, und der Miniserver mag keinen UDP-Sturm.

Rate-Limit - beim Full-Resend nach einem Miniserver-Neustart stehen hunderte
Datagramme gleichzeitig an. Gestaffelt kommen sie an, im Schwall nicht
(Spec 6.4).
"""

from __future__ import annotations

import asyncio
import socket

from loxmatter.loxone.values import datagram

RATE_LIMIT_PER_SECOND = 50.0


class UdpSender:
    def __init__(self, host: str, port: int, *, rate_limit: float = RATE_LIMIT_PER_SECOND) -> None:
        self._target = (host, port)
        self._interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._socket: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._letzte: dict[str, str] = {}
        self._naechster_sendezeitpunkt = 0.0
        self._sperre = asyncio.Lock()

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        """Sendet, wenn sich der Wert geaendert hat oder force gesetzt ist."""
        if self._socket is None:
            raise RuntimeError("UdpSender ist geschlossen")

        paket = datagram(key, value)
        text = paket.decode()
        if not force and self._letzte.get(key) == text:
            return False

        async with self._sperre:
            if self._socket is None:
                raise RuntimeError("UdpSender ist geschlossen")
            loop = asyncio.get_running_loop()
            wartezeit = self._naechster_sendezeitpunkt - loop.time()
            if wartezeit > 0:
                await asyncio.sleep(wartezeit)
            self._socket.sendto(paket, self._target)
            self._naechster_sendezeitpunkt = loop.time() + self._interval

        self._letzte[key] = text
        return True

    async def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
