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

"""Haelt die Verbindung zu matter-server aufrecht.

Am 8. September 2026 starb das Websocket zu matter-server, und loxmatter
verband sich nicht neu - es bemerkte es nicht einmal. Von aussen sah alles
gesund aus: die WebUI antwortete, der Heartbeat pulste weiter, die Diagnose
meldete "Connected". Tatsaechlich kam kein Geraetewert mehr an, und jeder
Loxone-Befehl scheiterte mit 502. Einziger Ausweg war ein Neustart des
Containers, und dasselbe passierte am selben Abend ein zweites Mal.

Dieses Modul liegt bewusst NICHT im Client: der Wiederaufbau braucht
`Store` und `Runtime`, und ein Client, der die halbe Anwendung kennt, waere
die schlechtere Grenze. Der Client meldet nur, dass die Verbindung weg ist
(`wait_for_link_loss`); was danach zu geschehen hat, weiss diese Ebene.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0


async def attach(client: Any, store: Any, runtime: Any) -> int:
    """Bindet eine bestehende Verbindung an Store und Laufzeit.

    Genau die Sequenz, die bis zum 8. September 2026 nur in `cli.serve()`
    stand. Sie hier zu buendeln ist keine Aufraeumarbeit: Start und
    Wiederaufbau MUESSEN dasselbe tun, sonst driften sie auseinander - und
    das faellt erst auf, wenn nach einem Reconnect etwas fehlt, das beim
    Start selbstverstaendlich war.

    `resend_all()` am Ende ist der eigentliche Gewinn. Die bestehende
    Zusicherung "ein Neustart der Bruecke wirkt wie /resync" (Spec 6.4) gilt
    damit auch fuer eine Wiederverbindung: Loxone bekommt den vollstaendigen
    Zustand zurueck, statt auf den Werten sitzenzubleiben, die beim Abriss
    zufaellig die letzten waren.

    Liefert die Anzahl nachgetragener Kommandos zurueck - `cli.serve()`
    meldet sie beim Start auf der Konsole, der Verwalter protokolliert sie.
    Ein `typer.echo` gehoert nicht hierher: dieses Modul laeuft auch dann,
    wenn niemand auf ein Terminal sieht.

    `runtime.start()` gehoert BEWUSST NICHT hierher. Es startet die
    Heartbeat- und Resend-Schleifen, und die sollen einen Ausfall
    ueberdauern, nicht mit ihm neu beginnen.
    """
    await client.subscribe(store.device_id_for_node, runtime)
    snapshots = await client.snapshots()
    await runtime.seed_from_snapshot(snapshots)
    store.backfill_device_types(snapshots)
    gained: int = store.backfill_commands(snapshots)
    await runtime.resend_all()
    return gained


async def supervise(
    client: Any,
    store: Any,
    runtime: Any,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    backoff_start: float = BACKOFF_START_SECONDS,
    backoff_max: float = BACKOFF_MAX_SECONDS,
) -> None:
    """Baut die Verbindung nach jedem Abriss wieder auf - endlos.

    **Ohne Aufgabegrenze**, und das ist eine Entscheidung: eine Bruecke, die
    nach zehn Versuchen aufgibt, ist schlechter als eine, die alle sechzig
    Sekunden weiter anklopft. matter-server kann beliebig lange weg sein -
    ein Update, ein Neustart des Hosts, ein Container, der erst nach dem
    Netz hochkommt -, und niemand steht daneben, um von Hand nachzuhelfen.

    `sleep` ist einsetzbar, damit die Tests die Wartezeiten MESSEN koennen,
    statt sie abzuwarten. Ein Test, der acht Sekunden schlaeft, wird beim
    naechsten Umbau als "langsam" uebersprungen und prueft dann nichts mehr.
    """
    while True:
        await client.wait_for_link_loss()
        logger.warning("Verbindung zu matter-server verloren - baue sie neu auf")
        delay = backoff_start
        while True:
            try:
                await client.connect()
                gained = await attach(client, store, runtime)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — jede Ursache soll denselben Backoff
                # ausloesen: ein nicht erreichbares matter-server ist ebenso ein Grund zum
                # erneuten Versuch wie ein Zustandsfehler beim Wiederaufbau. Eine engere
                # Ausnahme wuerde einen dieser Faelle unbemerkt durchlassen.
                logger.warning(
                    "Wiederaufbau gescheitert (%s) - naechster Versuch in %.0f s", exc, delay
                )
                await sleep(delay)
                delay = min(delay * 2, backoff_max)
            else:
                logger.info(
                    "Verbindung zu matter-server wiederhergestellt (%d Kommandos nachgetragen)",
                    gained,
                )
                break
