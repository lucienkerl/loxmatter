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
        """Baut den UDP-Socket auf. Ein rate_limit von 0 oder darunter bedeutet: kein Rate-Limit."""
        self._target = (host, port)
        self._interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._socket: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._last_sent: dict[str, str] = {}
        self._next_send_time = 0.0
        self._lock = asyncio.Lock()

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        """Sendet, wenn sich der Wert geaendert hat oder force gesetzt ist."""
        if self._socket is None:
            raise RuntimeError("UdpSender ist geschlossen")

        packet = datagram(key, value)
        text = packet.decode()
        if not force and self._last_sent.get(key) == text:
            return False

        async with self._lock:
            if self._socket is None:
                raise RuntimeError("UdpSender ist geschlossen")
            loop = asyncio.get_running_loop()
            wait_time = self._next_send_time - loop.time()
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._socket.sendto(packet, self._target)
            self._next_send_time = loop.time() + self._interval

        self._last_sent[key] = text
        return True

    async def close(self) -> None:
        """Schliesst den Socket. Nimmt dieselbe Sperre wie send(), damit ein
        Sendevorgang, der gerade im Rate-Limit-Schlaf steckt, nicht auf einen
        bereits geschlossenen Socket trifft. Mehrfacher Aufruf bleibt unschaedlich.
        """
        async with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
