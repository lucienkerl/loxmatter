"""Verbindet Matter-Subscriptions mit dem UDP-Sender.

Hier stehen die drei Dinge, die ein virtueller UDP-Eingang von sich aus nicht
kann:

Events (Spec 6.3) - ein Eingang traegt Werte, kein "etwas ist passiert". Jedes
Event wird zu einem Impuls, der eine Flanke erzeugt, und einem monotonen
Zaehler, der ein verlorenes UDP-Paket ueberlebt.

Erreichbarkeit (Spec 6.5) - je Geraet ein digitales Signal, dazu ein globaler
Heartbeat, der in Loxone als Watchdog dient und "Container tot" wie "Netz weg"
gleichermassen abdeckt. Ein Heartbeat, der beim ersten Sendefehler stirbt,
waere fuer genau diesen Zweck nutzlos - siehe `_heartbeat_loop`.

Zustands-Wiederherstellung (Spec 6.4) - UDP ist zustandslos. Nach einem
Neustart des Miniservers stehen alle Eingaenge auf ihrem Defaultwert, bis das
naechste Update kommt; bei einem Temperatursensor koennen das Stunden sein.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Protocol

from loxmatter.loxone.values import to_loxone_value
from loxmatter.matter.models import NodeSnapshot, SignalKind
from loxmatter.model.store import Store, StoredSignal

PULSE_MILLISECONDS = 200
HEARTBEAT_KEY = "bridge_alive"

logger = logging.getLogger(__name__)


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
        self._last_values: dict[str, float | bool] = {}
        self._counters: dict[str, int] = {}
        self._heartbeat_on = False
        # Dauerhafte Hintergrund-Tasks (Heartbeat- und Resend-Schleife).
        self._tasks: list[asyncio.Task[None]] = []
        # Kurzlebige Impuls-Tasks, je einer pro `on_event`-Aufruf. Ein
        # done_callback wirft jeden fertigen Task sofort wieder raus, sonst
        # waechst die Menge mit jedem Event unbegrenzt weiter (Review-Fix
        # Minor #1) - nur `stop()` haette sie sonst je geleert.
        self._pulse_tasks: set[asyncio.Task[None]] = set()
        # Schluessel, deren Impuls gerade auf True steht. `stop()` senkt sie
        # explizit, denn eine Cancellation waehrend des Impuls-Schlafs
        # ueberspringt sonst den `send(key, False)` in `_release_pulse` und
        # das digitale Signal bleibt bis zum naechsten Ereignis auf diesem
        # Schluessel haengen (Review-Fix Important #2).
        self._pulses_high: set[str] = set()
        # Index (device_id, path, kind) -> StoredSignal, pro Geraet einmalig
        # aus der Datenbank geladen. `on_attribute` und `on_event` laufen bei
        # jedem gemeldeten Wert eines Geraets - ohne diesen Cache waere das
        # eine frische Abfrage ueber ~160 Zeilen pro Aufruf, und der
        # Ur-Entwurf fragte sogar zweimal: einmal fuer den Schluessel, ein
        # zweites Mal fuer den SignalRef. Hier wird pro Geraet genau einmal
        # gelesen; jeder weitere Pfad desselben Geraets ist ein Dict-Zugriff.
        # Wer nach dem ersten Indizieren erneut `Store.register_signals` fuer
        # dasselbe Geraet aufruft, muss danach `invalidate_index` aufrufen -
        # sonst bleibt ein neu hinzugekommenes Signal fuer diese Laufzeit
        # unsichtbar (Review-Fix Important #3).
        self._signals: dict[tuple[int, str, str], StoredSignal] = {}
        self._indexed: set[int] = set()

    def _signal_for(self, device_id: int, path: str, kind: SignalKind) -> StoredSignal | None:
        """Findet das gespeicherte Signal zu einem Matter-Pfad, ohne bei
        jedem Aufruf erneut die Datenbank zu befragen."""
        if device_id not in self._indexed:
            for stored in self._store.signals(device_id):
                self._signals[(device_id, stored.ref.path, stored.ref.kind.value)] = stored
            self._indexed.add(device_id)
        signal = self._signals.get((device_id, path, kind.value))
        if signal is None:
            logger.debug(
                "Kein Signal fuer Geraet %s, Pfad %s, Art %s - Update wird verworfen",
                device_id,
                path,
                kind.value,
            )
        return signal

    def invalidate_index(self, device_id: int | None = None) -> None:
        """Verwirft den Signal-Cache eines Geraets, oder - ohne Angabe - aller Geraete.

        Wer zur Laufzeit erneut `Store.register_signals` fuer ein bereits
        laufendes Geraet aufruft (z. B. nach einem Firmware-Update, das einen
        neuen Cluster freischaltet), MUSS diese Methode danach fuer das
        betroffene Geraet aufrufen. Ohne das bleibt `_signal_for` bei seinem
        einmal geladenen Stand: das neue Signal existiert in der Datenbank,
        aber Updates dazu laufen fuer den Rest des Prozesses ins Leere - ohne
        Fehler, ohne Log-Eintrag ausser dem `debug`-Eintrag in `_signal_for`.
        """
        if device_id is None:
            self._signals.clear()
            self._indexed.clear()
            return
        self._indexed.discard(device_id)
        for cache_key in [k for k in self._signals if k[0] == device_id]:
            del self._signals[cache_key]

    def _cache_attribute(self, device_id: int, path: str, raw: object) -> str | None:
        """Wandelt einen rohen Matter-Wert in den Cache um und liefert den
        dabei benutzten Schluessel zurueck - oder `None`, wenn der Store kein
        Signal fuer diesen Pfad kennt oder der Wert nicht exportierbar ist
        (Liste, Struktur, Text; siehe `to_loxone_value`).

        Diese eine Stelle entscheidet, was aus einem rohen Matter-Wert wird -
        sowohl fuer eine echte Aktualisierung (`on_attribute`) als auch fuers
        Saeen aus dem aktuellen Geraetezustand (`seed_from_snapshot`). Eine
        zweite Stelle, die dieselbe Umrechnung noch einmal nachbaut, wuerde
        ueber kurz oder lang von dieser hier abweichen."""
        signal = self._signal_for(device_id, path, SignalKind.ATTRIBUTE)
        if signal is None:
            return None
        value = to_loxone_value(signal.ref, raw)
        if value is None:
            return None
        self._last_values[signal.key] = value
        return signal.key

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        key = self._cache_attribute(device_id, path, raw)
        if key is None:
            return
        await self._sender.send(key, self._last_values[key])

    async def seed_from_snapshot(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Fuellt den Cache aus dem aktuellen Geraetezustand (Spec 6.4).

        Ein Live-Lauf am 2026-09-02 zeigte die Luecke: `resend_all()` iteriert
        `_last_values`, und das ist beim Start leer - ein Wert landet dort nur
        ueber eine Subscription, die sich *aendernde* Werte meldet. Ein
        Stecker ohne Last meldet z. B. nie eine sich aendernde Spannung, also
        blieb der Cache nach dem Start leer und der erste Resend schickte
        nichts, obwohl genau er nach einem Neustart der Bruecke die Rolle von
        `/resync` uebernehmen soll. Diese Methode holt die fehlenden
        Startwerte aus `BridgeMatterClient.snapshots()` - demselben Bild, aus
        dem auch `loxmatter export` liest.

        Sendet dabei bewusst nichts selbst: sie fuellt nur `_last_values`
        ueber `_cache_attribute` (denselben Weg, den auch `on_attribute`
        nimmt), und der eine `resend_all()`-Aufruf direkt nach dem Saeen
        (siehe `_run`) verschickt dann alles zusammen mit `force=True`. Wuerde
        das Saeen selbst schon senden, entstuende bei jedem Start ein Doppel-
        Versand fuer jedes Signal - einmal hier, einmal durch den Resend
        gleich danach - unabhaengig davon, ob die Entprellung des Senders
        gerade leer ist oder nicht.

        Ein Node, den `Store` nicht kennt (noch nie exportiert, oder
        inzwischen entfernt), bricht das Saeen nicht ab - er wird
        uebersprungen, alle anderen Nodes werden trotzdem gesaet. Ein
        Attribut, fuer das der Store kein Signal kennt, wird - wie bei jeder
        Aktualisierung zur Laufzeit auch - stillschweigend verworfen.

        Liefert die Anzahl gesaeter Signale zurueck (fuers Log in `_run`)."""
        count = 0
        for snapshot in snapshots:
            device_id = self._store.device_id_for_node(snapshot.node_id)
            if device_id is None:
                logger.info(
                    "Kein bekanntes Geraet fuer Node %s - Snapshot wird beim Saeen uebersprungen",
                    snapshot.node_id,
                )
                continue
            for path, raw in snapshot.attributes.items():
                if self._cache_attribute(device_id, path, raw) is not None:
                    count += 1
        return count

    async def on_event(self, device_id: int, path: str) -> None:
        signal = self._signal_for(device_id, path, SignalKind.EVENT)
        if signal is None:
            return
        key = signal.key
        # Der Zaehler dient dem Erkennen von Paketverlust, nicht einem
        # exakten Protokoll - er zaehlt deshalb bewusst hoch, bevor gesendet
        # wird. Ein Zaehler, der bei einem fehlgeschlagenen send() haengen
        # bliebe, waere fuer diesen Zweck kein Gewinn (Review-Fix Minor #2).
        self._counters[key] = self._counters.get(key, 0) + 1
        await self._sender.send(key, True)
        self._pulses_high.add(key)
        await self._sender.send(f"{key}_n", self._counters[key])
        self._last_values[f"{key}_n"] = self._counters[key]
        task = asyncio.create_task(self._release_pulse(key))
        task.add_done_callback(self._pulse_tasks.discard)
        self._pulse_tasks.add(task)

    async def _release_pulse(self, key: str) -> None:
        await asyncio.sleep(PULSE_MILLISECONDS / 1000)
        await self._sender.send(key, False)
        self._pulses_high.discard(key)

    async def set_online(self, device_id: int, online: bool) -> None:
        key = f"d{device_id}_online"
        self._last_values[key] = online
        await self._sender.send(key, online)

    async def resend_all(self) -> int:
        """Schickt jeden bekannten Wert erneut, an der Entprellung vorbei."""
        count = 0
        for key, value in list(self._last_values.items()):
            await self._sender.send(key, value, force=True)
            count += 1
        return count

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._resend_loop()))

    async def stop(self) -> None:
        # Jeden gerade high stehenden Impuls senken, BEVOR die dazugehoerigen
        # Tasks abgebrochen werden - sonst ueberspringt die Cancellation den
        # `send(key, False)` in `_release_pulse` und das Signal bleibt bis
        # zum naechsten Ereignis auf 1 haengen (Review-Fix Important #2).
        for key in list(self._pulses_high):
            await self._sender.send(key, False)
        self._pulses_high.clear()

        tasks: list[asyncio.Task[None]] = [*self._tasks, *self._pulse_tasks]
        for task in tasks:
            task.cancel()
        # gather(..., return_exceptions=True) statt eines
        # contextlib.suppress(CancelledError) je Task: Letzteres unterdrueckt
        # nur eine Cancellation, keine Exception, an der ein Task schon vor
        # `stop()` gestorben ist - die wuerde erneut ausgeloest, die Schleife
        # ueber die Tasks abbrechen und `clear()` ueberspringen (Review-Fix
        # Important #1, Begleitfehler).
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pulse_tasks.clear()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                self._heartbeat_on = not self._heartbeat_on
                await self._sender.send(HEARTBEAT_KEY, self._heartbeat_on, force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Genau der Fehlerfall, den der Heartbeat melden soll, darf
                # ihn nicht zum Schweigen bringen - sonst friert der
                # Loxone-Watchdog auf dem letzten Wert ein, waehrend nichts
                # mehr laeuft (Review-Fix Important #1).
                logger.exception("Heartbeat konnte nicht gesendet werden - Schleife laeuft weiter")
            await asyncio.sleep(self._heartbeat_seconds)

    async def _resend_loop(self) -> None:
        while True:
            await asyncio.sleep(self._resend_seconds)
            try:
                await self.resend_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Full-Resend fehlgeschlagen - Schleife laeuft weiter")
