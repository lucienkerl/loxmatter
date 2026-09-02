"""Verbindet Matter-Subscriptions mit dem UDP-Sender.

Hier stehen die drei Dinge, die ein virtueller UDP-Eingang von sich aus nicht
kann:

Events (Spec 6.3) - ein Eingang traegt Werte, kein "etwas ist passiert". Jedes
Event wird zu einem Impuls, der eine Flanke erzeugt, und einem monotonen
Zaehler, der ein verlorenes UDP-Paket ueberlebt.

Erreichbarkeit (Spec 6.5) - je Geraet ein digitales Signal, dazu ein globaler
Heartbeat, der in Loxone als Watchdog dient und "Container tot" wie "Netz weg"
gleichermassen abdeckt.

Zustands-Wiederherstellung (Spec 6.4) - UDP ist zustandslos. Nach einem
Neustart des Miniservers stehen alle Eingaenge auf ihrem Defaultwert, bis das
naechste Update kommt; bei einem Temperatursensor koennen das Stunden sein.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol

from loxmatter.loxone.values import to_loxone_value
from loxmatter.matter.models import SignalKind
from loxmatter.model.store import Store, StoredSignal

PULSE_MILLISECONDS = 200
HEARTBEAT_KEY = "bridge_alive"


class Sender(Protocol):
    """Was die Laufzeit vom Sender braucht - damit Tests ihn ersetzen koennen."""

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool: ...

    async def close(self) -> None: ...


class Runtime:
    PULSE_MILLISECONDS = PULSE_MILLISECONDS

    def __init__(
        self,
        store: Store,
        sender: Sender,
        *,
        heartbeat_seconds: float = 30.0,
        resend_seconds: float = 300.0,
    ) -> None:
        self._store = store
        self._sender = sender
        self._heartbeat_seconds = heartbeat_seconds
        self._resend_seconds = resend_seconds
        self._letzte_werte: dict[str, float | bool] = {}
        self._zaehler: dict[str, int] = {}
        self._heartbeat_an = False
        self._aufgaben: list[asyncio.Task[None]] = []
        # Index (device_id, path, kind) -> StoredSignal, pro Geraet einmalig
        # aus der Datenbank geladen. `on_attribute` und `on_event` laufen bei
        # jedem gemeldeten Wert eines Geraets - ohne diesen Cache waere das
        # eine frische Abfrage ueber ~160 Zeilen pro Aufruf, und der
        # Ur-Entwurf fragte sogar zweimal: einmal fuer den Schluessel, ein
        # zweites Mal fuer den SignalRef. Hier wird pro Geraet genau einmal
        # gelesen; jeder weitere Pfad desselben Geraets ist ein Dict-Zugriff.
        self._signale: dict[tuple[int, str, str], StoredSignal] = {}
        self._indiziert: set[int] = set()

    def _signal_fuer(self, device_id: int, path: str, kind: SignalKind) -> StoredSignal | None:
        """Findet das gespeicherte Signal zu einem Matter-Pfad, ohne bei
        jedem Aufruf erneut die Datenbank zu befragen."""
        if device_id not in self._indiziert:
            for signal in self._store.signals(device_id):
                self._signale[(device_id, signal.ref.path, signal.ref.kind.value)] = signal
            self._indiziert.add(device_id)
        return self._signale.get((device_id, path, kind.value))

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        signal = self._signal_fuer(device_id, path, SignalKind.ATTRIBUTE)
        if signal is None:
            return
        wert = to_loxone_value(signal.ref, raw)
        if wert is None:
            return
        self._letzte_werte[signal.key] = wert
        await self._sender.send(signal.key, wert)

    async def on_event(self, device_id: int, path: str) -> None:
        signal = self._signal_fuer(device_id, path, SignalKind.EVENT)
        if signal is None:
            return
        key = signal.key
        self._zaehler[key] = self._zaehler.get(key, 0) + 1
        await self._sender.send(key, True)
        await self._sender.send(f"{key}_n", self._zaehler[key])
        self._letzte_werte[f"{key}_n"] = self._zaehler[key]
        self._aufgaben.append(asyncio.create_task(self._impuls_zuruecknehmen(key)))

    async def _impuls_zuruecknehmen(self, key: str) -> None:
        await asyncio.sleep(PULSE_MILLISECONDS / 1000)
        await self._sender.send(key, False)

    async def set_online(self, device_id: int, online: bool) -> None:
        key = f"d{device_id}_online"
        self._letzte_werte[key] = online
        await self._sender.send(key, online)

    async def resend_all(self) -> int:
        """Schickt jeden bekannten Wert erneut, an der Entprellung vorbei."""
        anzahl = 0
        for key, wert in list(self._letzte_werte.items()):
            await self._sender.send(key, wert, force=True)
            anzahl += 1
        return anzahl

    async def start(self) -> None:
        self._aufgaben.append(asyncio.create_task(self._heartbeat_schleife()))
        self._aufgaben.append(asyncio.create_task(self._resend_schleife()))

    async def stop(self) -> None:
        for aufgabe in self._aufgaben:
            aufgabe.cancel()
        for aufgabe in self._aufgaben:
            with contextlib.suppress(asyncio.CancelledError):
                await aufgabe
        self._aufgaben.clear()

    async def _heartbeat_schleife(self) -> None:
        while True:
            self._heartbeat_an = not self._heartbeat_an
            await self._sender.send(HEARTBEAT_KEY, self._heartbeat_an, force=True)
            await asyncio.sleep(self._heartbeat_seconds)

    async def _resend_schleife(self) -> None:
        while True:
            await asyncio.sleep(self._resend_seconds)
            await self.resend_all()
