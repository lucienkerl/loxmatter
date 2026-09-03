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

"""Verschickt Werte als UDP-Datagramme an den Miniserver.

Kennt kein Matter. Er bekommt fertige Schluessel und fertige Werte.

Zwei Eigenschaften sind nicht optional:

Entprellung - ein Matter-Geraet meldet einen Messwert gerne im Sekundentakt,
auch wenn er sich nicht aendert. Unveraenderte Werte erneut zu schicken kostet
nur Last, und der Miniserver mag keinen UDP-Sturm.

Rate-Limit - beim Full-Resend nach einem Miniserver-Neustart stehen hunderte
Datagramme gleichzeitig an. Gestaffelt kommen sie an, im Schwall nicht
(Spec 6.4).

**Mitschnitt (Spec 10.5, Task 6, Phase 5).** `send()` haelt einen
Ringpuffer der zuletzt TATSAECHLICH ueber den Socket gegangenen Datagramme
(`datagram_log`) - fuer `GET /api/diagnostics/datagrams`. Bewusst HIER und
nicht in `Runtime.on_attribute`/`on_event` (die `send()` aufrufen): ein
Mitschnitt vor dieser Stelle zeigt, was gesendet werden SOLLTE; dieser
Mitschnitt, direkt neben dem `sendto()`-Aufruf, zeigt, was tatsaechlich
ging - nach Entprellung, nach Rate-Limit-Wartezeit. Ein Wert, der wegen
Entprellung uebersprungen wird (frueher `return False` oben in `send()`),
landet deshalb folgerichtig NICHT im Mitschnitt - er wurde ja nicht
gesendet. `RingBuffer`/`DatagramLogEntry` kommen aus `api.diagnostics`
(siehe dortiger Moduldocstring fuer die Begruendung dieser - fuer dieses
Projekt sonst unueblichen - Importrichtung).

Das Mitschreiben selbst ist in ein eigenes try/except gekapselt
(`_record_sent`): ein Diagnosewerkzeug, das den Pfad, den es beobachtet,
selbst zum Absturz bringen koennte, waere schlimmer als gar keins - siehe
Task-6-Report, Punkt 1 (Kosten im Hot Path). Die Kosten selbst sind minimal:
ein `deque.append` auf einen bereits begrenzten Puffer, kein I/O, keine
Allokation ausser dem einen `DatagramLogEntry`.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable

from loxmatter.api.diagnostics import DatagramLogEntry, RingBuffer
from loxmatter.loxone.values import datagram
from loxmatter.timestamps import now_iso

RATE_LIMIT_PER_SECOND = 50.0
DATAGRAM_LOG_SIZE = 500

logger = logging.getLogger(__name__)


class UdpSender:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        rate_limit: float = RATE_LIMIT_PER_SECOND,
        log_size: int = DATAGRAM_LOG_SIZE,
    ) -> None:
        """Baut den UDP-Socket auf. Ein rate_limit von 0 oder darunter bedeutet: kein Rate-Limit."""
        self._target = (host, port)
        self._interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._socket: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._last_sent: dict[str, str] = {}
        self._next_send_time = 0.0
        self._lock = asyncio.Lock()
        self._datagram_log: RingBuffer[DatagramLogEntry] = RingBuffer(maxlen=log_size)
        self._datagram_observers: list[Callable[[DatagramLogEntry], None]] = []

    @property
    def target(self) -> tuple[str, int]:
        """Ziel-Host/-Port - fuer den Systemcheck der Diagnose
        (`api.diagnostics._check_miniserver`), sonst rein intern."""
        return self._target

    @property
    def datagram_log(self) -> RingBuffer[DatagramLogEntry]:
        """Die zuletzt tatsaechlich gesendeten Datagramme - siehe
        Moduldocstring, Abschnitt "Mitschnitt". Nur lesbar von aussen: der
        Ringpuffer selbst bietet ohnehin kein `clear()`/keine Mutation
        ausser `append()` an (siehe `api.diagnostics.RingBuffer`), diese
        Property verhindert zusaetzlich, dass ein Aufrufer `self._datagram_log`
        durch ein komplett anderes Objekt ersetzt."""
        return self._datagram_log

    def add_datagram_observer(self, callback: Callable[[DatagramLogEntry], None]) -> None:
        """Meldet einen Beobachter an, der jedes tatsaechlich gesendete
        Datagramm sieht - auch die, die `Runtime._notify_observers` bewusst
        auslaesst (Full-Resend, Absenken eines Impulses; siehe dortiger
        Docstring). Genau deshalb haengt diese Kette hier am Sender und nicht
        an der Laufzeit: siehe Moduldocstring, Abschnitt "Mitschnitt".

        Der Beobachter wird aus `_record_sent` heraus aufgerufen, also NACH
        dem `sendto()` und NACH dem Anhaengen an `datagram_log`."""
        self._datagram_observers.append(callback)

    def remove_datagram_observer(self, callback: Callable[[DatagramLogEntry], None]) -> None:
        """Meldet einen Beobachter wieder ab. Ein unbekannter Beobachter
        (z. B. doppelt abgemeldet) ist kein Fehler, sondern wird still
        ignoriert - dieselbe Regel wie bei `Runtime.remove_observer`."""
        try:
            self._datagram_observers.remove(callback)
        except ValueError:
            pass

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
            # Erst NACH dem tatsaechlichen sendto() - siehe Moduldocstring.
            # Ein uebersprungener (entprellter) Wert oben erreicht diese
            # Zeile nie, ein force=True-Resend dagegen schon: beides ist
            # richtig, denn beides beschreibt, was wirklich ueber den Draht
            # ging. `force` wird unveraendert durchgereicht (Nachbesserung
            # Task 6, 2026-09-03): `DatagramLogEntry.forced` haelt damit
            # WARUM gesendet wurde fest - einzige verlaessliche Quelle
            # dieser Unterscheidung, siehe dortiger Docstring.
            self._record_sent(key, text, force)

        self._last_sent[key] = text
        return True

    def _record_sent(self, key: str, text: str, forced: bool) -> None:
        """Haengt einen Eintrag an `datagram_log` an und benachrichtigt
        danach die Beobachterkette - beides abgeschottet in einem eigenen
        try/except (Task-6-Report, Punkt 1): ein Fehler beim Mitschreiben
        (heute keiner ersichtlich, aber ein spaeterer Umbau koennte einen
        einschleppen) darf niemals den bereits erfolgten Versand rueckwirkend
        zu einem Fehlschlag machen - `send()` hat an dieser Stelle sein
        Datagramm laengst verschickt.

        `forced` ist das `force`-Argument von `send()`, unveraendert
        durchgereicht - siehe `DatagramLogEntry.forced` fuer die Begruendung,
        warum genau diese Stelle sie kennt und eine Zeitheuristik im Browser
        sie nicht ersetzen kann."""
        try:
            _, _, value_text = text.partition(":")
            entry = DatagramLogEntry(key=key, value=value_text, timestamp=now_iso(), forced=forced)
            self._datagram_log.append(entry)
        except Exception:
            logger.exception(
                "Mitschnitt des gesendeten Datagramms fuer Schluessel %r fehlgeschlagen - "
                "der Versand selbst ist davon nicht betroffen",
                key,
            )
            return
        self._notify_datagram_observers(entry)

    def _notify_datagram_observers(self, entry: DatagramLogEntry) -> None:
        """Ruft jeden Beobachter mit dem soeben aufgezeichneten Datagramm auf
        - nach demselben Muster wie `Runtime._notify_observers` (dort
        nachlesen).

        Eine Kopie der Liste iterieren statt des Originals: ein Beobachter,
        der sich selbst waehrend seines Aufrufs abmeldet
        (`remove_datagram_observer`), darf die laufende Benachrichtigung der
        uebrigen nicht stoeren.

        Ein Fehler eines Beobachters wird geloggt und uebersprungen - anders
        als beim Log-Handler (dort waere der Logeintrag selbst die
        Endlosschleife) protokolliert der UDP-Pfad hier ohnehin schon, eine
        stille Verschluckung waere die schlechtere Wahl."""
        for observer in list(self._datagram_observers):
            try:
                observer(entry)
            except Exception:
                logger.exception(
                    "Beobachter fuer gesendetes Datagramm %r ist fehlgeschlagen - "
                    "wird uebersprungen",
                    entry.key,
                )

    async def close(self) -> None:
        """Schliesst den Socket. Nimmt dieselbe Sperre wie send(), damit ein
        Sendevorgang, der gerade im Rate-Limit-Schlaf steckt, nicht auf einen
        bereits geschlossenen Socket trifft. Mehrfacher Aufruf bleibt unschaedlich.
        """
        async with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
