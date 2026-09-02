"""Verbindet Matter-Subscriptions mit dem UDP-Sender.

Hier stehen die drei Dinge, die ein virtueller UDP-Eingang von sich aus nicht
kann:

Events (Spec 6.3) - ein Eingang traegt Werte, kein "etwas ist passiert". Jedes
Event wird zu einem Impuls, der eine Flanke erzeugt, und einem monotonen
Zaehler, der ein verlorenes UDP-Paket ueberlebt.

Erreichbarkeit (Spec 6.5) - je Geraet ein digitales Signal, dazu ein globaler
Heartbeat, der in Loxone als Watchdog dient und "Container tot" wie "Netz weg"
gleichermassen abdeckt. Ein Heartbeat, der beim ersten Sendefehler stirbt,
waere fuer genau diesen Zweck nutzlos - siehe `_heartbeat_schleife`.

Zustands-Wiederherstellung (Spec 6.4) - UDP ist zustandslos. Nach einem
Neustart des Miniservers stehen alle Eingaenge auf ihrem Defaultwert, bis das
naechste Update kommt; bei einem Temperatursensor koennen das Stunden sein.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from loxmatter.loxone.values import to_loxone_value
from loxmatter.matter.models import SignalKind
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
        self._letzte_werte: dict[str, float | bool] = {}
        self._zaehler: dict[str, int] = {}
        self._heartbeat_an = False
        # Dauerhafte Hintergrund-Tasks (Heartbeat- und Resend-Schleife).
        self._aufgaben: list[asyncio.Task[None]] = []
        # Kurzlebige Impuls-Tasks, je einer pro `on_event`-Aufruf. Ein
        # done_callback wirft jeden fertigen Task sofort wieder raus, sonst
        # waechst die Menge mit jedem Event unbegrenzt weiter (Review-Fix
        # Minor #1) - nur `stop()` haette sie sonst je geleert.
        self._impuls_aufgaben: set[asyncio.Task[None]] = set()
        # Schluessel, deren Impuls gerade auf True steht. `stop()` senkt sie
        # explizit, denn eine Cancellation waehrend des Impuls-Schlafs
        # ueberspringt sonst den `send(key, False)` in
        # `_impuls_zuruecknehmen` und das digitale Signal bleibt bis zum
        # naechsten Ereignis auf diesem Schluessel haengen (Review-Fix
        # Important #2).
        self._hohe_impulse: set[str] = set()
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
        self._signale: dict[tuple[int, str, str], StoredSignal] = {}
        self._indiziert: set[int] = set()

    def _signal_fuer(self, device_id: int, path: str, kind: SignalKind) -> StoredSignal | None:
        """Findet das gespeicherte Signal zu einem Matter-Pfad, ohne bei
        jedem Aufruf erneut die Datenbank zu befragen."""
        if device_id not in self._indiziert:
            for eintrag in self._store.signals(device_id):
                self._signale[(device_id, eintrag.ref.path, eintrag.ref.kind.value)] = eintrag
            self._indiziert.add(device_id)
        signal = self._signale.get((device_id, path, kind.value))
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
        betroffene Geraet aufrufen. Ohne das bleibt `_signal_fuer` bei seinem
        einmal geladenen Stand: das neue Signal existiert in der Datenbank,
        aber Updates dazu laufen fuer den Rest des Prozesses ins Leere - ohne
        Fehler, ohne Log-Eintrag ausser dem `debug`-Eintrag in `_signal_fuer`.
        """
        if device_id is None:
            self._signale.clear()
            self._indiziert.clear()
            return
        self._indiziert.discard(device_id)
        for schluessel in [k for k in self._signale if k[0] == device_id]:
            del self._signale[schluessel]

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
        # Der Zaehler dient dem Erkennen von Paketverlust, nicht einem
        # exakten Protokoll - er zaehlt deshalb bewusst hoch, bevor gesendet
        # wird. Ein Zaehler, der bei einem fehlgeschlagenen send() haengen
        # bliebe, waere fuer diesen Zweck kein Gewinn (Review-Fix Minor #2).
        self._zaehler[key] = self._zaehler.get(key, 0) + 1
        await self._sender.send(key, True)
        self._hohe_impulse.add(key)
        await self._sender.send(f"{key}_n", self._zaehler[key])
        self._letzte_werte[f"{key}_n"] = self._zaehler[key]
        aufgabe = asyncio.create_task(self._impuls_zuruecknehmen(key))
        aufgabe.add_done_callback(self._impuls_aufgaben.discard)
        self._impuls_aufgaben.add(aufgabe)

    async def _impuls_zuruecknehmen(self, key: str) -> None:
        await asyncio.sleep(PULSE_MILLISECONDS / 1000)
        await self._sender.send(key, False)
        self._hohe_impulse.discard(key)

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
        # Jeden gerade high stehenden Impuls senken, BEVOR die dazugehoerigen
        # Tasks abgebrochen werden - sonst ueberspringt die Cancellation den
        # `send(key, False)` in `_impuls_zuruecknehmen` und das Signal bleibt
        # bis zum naechsten Ereignis auf 1 haengen (Review-Fix Important #2).
        for key in list(self._hohe_impulse):
            await self._sender.send(key, False)
        self._hohe_impulse.clear()

        aufgaben: list[asyncio.Task[None]] = [*self._aufgaben, *self._impuls_aufgaben]
        for aufgabe in aufgaben:
            aufgabe.cancel()
        # gather(..., return_exceptions=True) statt eines
        # contextlib.suppress(CancelledError) je Task: Letzteres unterdrueckt
        # nur eine Cancellation, keine Exception, an der ein Task schon vor
        # `stop()` gestorben ist - die wuerde erneut ausgeloest, die Schleife
        # ueber die Tasks abbrechen und `clear()` ueberspringen (Review-Fix
        # Important #1, Begleitfehler).
        await asyncio.gather(*aufgaben, return_exceptions=True)
        self._aufgaben.clear()
        self._impuls_aufgaben.clear()

    async def _heartbeat_schleife(self) -> None:
        while True:
            try:
                self._heartbeat_an = not self._heartbeat_an
                await self._sender.send(HEARTBEAT_KEY, self._heartbeat_an, force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Genau der Fehlerfall, den der Heartbeat melden soll, darf
                # ihn nicht zum Schweigen bringen - sonst friert der
                # Loxone-Watchdog auf dem letzten Wert ein, waehrend nichts
                # mehr laeuft (Review-Fix Important #1).
                logger.exception("Heartbeat konnte nicht gesendet werden - Schleife laeuft weiter")
            await asyncio.sleep(self._heartbeat_seconds)

    async def _resend_schleife(self) -> None:
        while True:
            await asyncio.sleep(self._resend_seconds)
            try:
                await self.resend_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Full-Resend fehlgeschlagen - Schleife laeuft weiter")
