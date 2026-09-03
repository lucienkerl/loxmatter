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

Beobachter (Spec 8.3, Phase 5 Task 3) - die WebUI zeigt Live-Werte ueber
dieselbe Subscription an, die auch den UDP-Sender speist. Kein zweiter Pfad,
kein Polling: `add_observer` haengt eine Oberflaeche an denselben Strom von
Attribut-, Event- und Online-Aenderungen, der bereits an Loxone geht - siehe
`_notify_observers`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
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
        # Beobachter der WebUI (Spec 8.3) - siehe `add_observer`.
        self._observers: list[Callable[[str, object], None]] = []

    def add_observer(self, callback: Callable[[str, object], None]) -> None:
        """Meldet einen Beobachter an, der jeden Wert sieht, den auch der
        UDP-Sender sieht (Spec 8.3) - kein zweiter Pfad, kein Polling.

        Zwei Regeln, beide in `_notify_observers` umgesetzt:

        - Der Beobachter wird ERST NACH dem Senden aufgerufen. Die Bruecke
          zu Loxone ist der Zweck dieser Laufzeit; die Oberflaeche schaut
          nur zu. Schlaegt das Senden fehl, erfaehrt der Beobachter, was
          tatsaechlich geschah - nicht, was beabsichtigt war.
        - Ein Beobachter, der wirft, wird geloggt und uebersprungen. Er darf
          den UDP-Pfad nicht mitreissen - dieselbe Regel, die in Phase 4 die
          Heartbeat-Schleife gehaertet hat (siehe `_heartbeat_loop`): ein
          geschlossener Browser-Tab darf die Bruecke nicht anhalten."""
        self._observers.append(callback)

    def remove_observer(self, callback: Callable[[str, object], None]) -> None:
        """Meldet einen Beobachter wieder ab - z. B. wenn ein WebSocket
        getrennt wird. Ein unbekannter Beobachter (z. B. doppelt abgemeldet)
        ist kein Fehler, sondern wird still ignoriert."""
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def observer_count(self) -> int:
        """Anzahl aktuell angemeldeter Beobachter - fuer Tests, die pruefen
        wollen, dass ein getrennter Client tatsaechlich abgemeldet wurde und
        nicht als Leiche haengen bleibt."""
        return len(self._observers)

    def _notify_observers(self, key: str, value: object) -> None:
        """Ruft jeden Beobachter mit dem soeben gesendeten Schluessel/Wert-
        Paar auf - IMMER erst nachdem `self._sender.send(...)` zurueckkam
        (siehe Aufrufstellen in `on_attribute`, `on_event`, `_release_pulse`
        und `set_online`).

        Eine Kopie der Liste iterieren statt des Originals: ein Beobachter,
        der sich selbst waehrend seines Aufrufs abmeldet (`remove_observer`),
        darf die laufende Benachrichtigung der uebrigen nicht stoeren."""
        for observer in list(self._observers):
            try:
                observer(key, value)
            except Exception:
                # Dieselbe Begruendung wie bei `_heartbeat_loop`: ein
                # Beobachter-Fehler (z. B. ein Programmfehler in der WebUI)
                # darf den UDP-Pfad nicht mitreissen - geloggt, uebersprungen,
                # weiter geht's mit dem naechsten Beobachter.
                logger.exception(
                    "Beobachter fuer Schluessel %r ist fehlgeschlagen - wird uebersprungen", key
                )

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
        value = self._last_values[key]
        await self._sender.send(key, value)
        self._notify_observers(key, value)

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

        Saeet dabei auch `d<id>_online` aus `snapshot.available` (Review-Fix
        C1, 2026-09-02): der einzige Schreiber von `d<id>_online` ist sonst
        `set_online`, aufgerufen aus `BridgeMatterClient._dispatch_loop` bei
        NODE_ADDED/NODE_UPDATED/NODE_REMOVED - aber `start_listening()`
        fuellt den initialen Node-Cache OHNE NODE_ADDED zu feuern, und
        NODE_UPDATED kommt nur bei einer Node-Daten-Nachricht, nicht bei
        einer reinen Attribut-Aktualisierung. Ohne dieses Saeen bliebe
        `d<id>_online` nach jedem Bruecken-Start auf seinem `DefVal="0"` -
        also dauerhaft "nicht erreichbar" fuer ein Geraet, das einfach nur
        still ist, und `/resync` koennte das nicht heilen, weil der
        Schluessel nie in `_last_values` landet. Genau dieselbe Fehlerklasse,
        die Spec 6.4 fuer Attribute bereits verhindert.

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
            self._cache_online(device_id, snapshot.available)
            count += 1
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
        self._notify_observers(key, True)
        self._pulses_high.add(key)
        await self._sender.send(f"{key}_n", self._counters[key])
        self._notify_observers(f"{key}_n", self._counters[key])
        self._last_values[f"{key}_n"] = self._counters[key]
        task = asyncio.create_task(self._release_pulse(key))
        task.add_done_callback(self._pulse_tasks.discard)
        self._pulse_tasks.add(task)

    async def _release_pulse(self, key: str) -> None:
        await asyncio.sleep(PULSE_MILLISECONDS / 1000)
        await self._sender.send(key, False)
        self._notify_observers(key, False)
        self._pulses_high.discard(key)

    @staticmethod
    def _online_key(device_id: int) -> str:
        return f"d{device_id}_online"

    def _cache_online(self, device_id: int, online: bool) -> None:
        """Traegt die Erreichbarkeit eines Geraets in den Cache ein, ohne zu senden.

        Eigener Schritt, herausgezogen aus `set_online` (Review-Fix C1,
        2026-09-02): `seed_from_snapshot` braucht denselben Schluessel und
        denselben Cache-Eintrag, den ein spaeteres `set_online` ueber ein
        NODE_ADDED/NODE_UPDATED-Ereignis erzeugen wuerde - aber, wie bei
        jedem anderen gesaeten Signal auch, OHNE selbst zu senden. Der
        anschliessende `resend_all()` in `_run` verschickt alles gesaete
        gebuendelt mit `force=True`; wuerde das Saeen hier schon senden,
        entstuende fuer `d<id>_online` ein Doppel-Versand bei jedem Start
        (siehe Docstring von `seed_from_snapshot`)."""
        self._last_values[self._online_key(device_id)] = online

    async def set_online(self, device_id: int, online: bool) -> None:
        self._cache_online(device_id, online)
        key = self._online_key(device_id)
        await self._sender.send(key, online)
        self._notify_observers(key, online)

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        """Alle zuletzt bekannten Werte eines Geraets, indiziert nach
        Signal-Schluessel - fuer die Geraete- und Signal-API (Task 2, Phase
        5), die pro Signal einen Live-Wert anzeigen will, ohne selbst eine
        zweite Subscription zu fuehren.

        Reine Lesehilfe ueber `_last_values`: liefert nur, was schon einmal
        durch eine Subscription oder `seed_from_snapshot` hier ankam. Ein
        Signal, das die Bruecke noch nie gemeldet bekommen hat, taucht hier
        nicht auf - der Aufrufer behandelt das als "noch kein Wert bekannt"
        (`None`), nicht als Fehler. Textwerte tauchen hier grundsaetzlich nie
        auf: `_cache_attribute` speichert nur, was `to_loxone_value` liefert,
        und das ist fuer `Exportability.TEXT` immer `None` (siehe dort) - ein
        virtueller UDP-Eingang kennt keinen Text.

        Der Praefix-Vergleich ist sicher vor einer Verwechslung zwischen
        Geraeten mit Ziffern-Praefix eines anderen (z. B. Geraet 1 vs. Geraet
        12): der Schluessel traegt zwingend einen Unterstrich direkt nach der
        device_id (`d1_...` vs. `d12_...`), `"d12_1_temp".startswith("d1_")`
        ist deshalb `False`.
        """
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._last_values.items() if k.startswith(prefix)}

    async def resend_all(self) -> int:
        """Schickt jeden bekannten Wert erneut, an der Entprellung vorbei.

        Iteriert nur die Schluessel als Momentaufnahme, liest den Wert aber
        JE SCHLUESSEL erst unmittelbar vor dem Senden aus `_last_values`
        nach (Review-Fix I4, 2026-09-02). Der alte Code erfasste `(key,
        value)`-Paare gemeinsam als eine Momentaufnahme und wartete dann -
        durch die Entprellung im `UdpSender` - bis zu ein paar Sekunden fuer
        rund 110 Signale. Eine gleichzeitige Aktualisierung waehrend dieser
        Zeit schrieb ihren neuen Wert schon in `_last_values` und schickte
        ihn selbst sofort, aber der lang laufende Resend traf mit seiner
        laengst veralteten Momentaufnahme danach noch einmal ein und
        ueberschrieb den frischen Wert in Loxone wieder mit dem alten. Der
        Fehler heilt sich erst beim naechsten echten Update selbst - aber
        der Ausloeser hier ist `/resync`, verdrahtet an den
        Systemstart-Baustein, und feuert also genau dann, wenn jemand
        zusieht.
        """
        count = 0
        for key in list(self._last_values):
            value = self._last_values.get(key)
            if value is None:
                # Zwischen der Momentaufnahme der Schluessel oben und diesem
                # Zugriff kann ein Schluessel theoretisch verschwunden sein -
                # praktisch nie, aber `_last_values` kennt kein Loeschen, nur
                # Ueberschreiben. Sicherer Ueberspringen statt eines
                # `None`-Werts auf der Leitung.
                continue
            # Bewusst kein `_notify_observers(...)` hier (Review-Fix Minor
            # #3, 2026-09-02): ein Resend verschickt nur Werte, die ein
            # Beobachter (z. B. die WebUI) laengst als aktuell gesehen hat -
            # kein neuer Wert, also auch keine neue Benachrichtigung noetig.
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
        #
        # In eigenem try/finally (Review-Fix M11, 2026-09-02): ein bereits
        # toter Sender (z. B. ein `UdpSender`, dessen Socket schon zu ist -
        # siehe `test_a_failing_resend_yields_502...` in test_server.py fuer
        # denselben Fall bei `/resync`) liess diese Schleife ohne den Fix
        # unbedingt aufbrechen - und damit UEBERSPRANG SIE JEDES
        # `task.cancel()` unten und beide `.clear()`-Aufrufe. `stop()` ist
        # der Aufraeum-Pfad selbst; ein fehlgeschlagener Sendeversuch darf
        # nicht dazu fuehren, dass Hintergrund-Tasks weiterlaufen und die
        # beiden Mengen nie geleert werden.
        try:
            for key in list(self._pulses_high):
                # Bewusst kein `_notify_observers(...)` hier (Review-Fix
                # Minor #3, 2026-09-02): ein Beobachter hat den High-Wert
                # dieses Impulses bereits gesehen (siehe `on_event`) - das
                # Senken beim Beenden ist reines Aufraeumen fuer Loxone,
                # keine neue Information fuer die WebUI.
                await self._sender.send(key, False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Ein Impuls konnte beim Beenden nicht gesenkt werden - "
                "Aufraeumen laeuft trotzdem weiter"
            )
        finally:
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
                # Auch an die Oberflaeche (2026-09-03). Vorher ging der
                # Heartbeat nur an Loxone, und eine Bruecke, an der sich
                # gerade nichts aendert - eine Steckdose ohne Last meldet
                # weder Strom noch Leistung -, war in der Live-Ansicht von
                # einer abgestuerzten nicht zu unterscheiden: kein Wert
                # bewegte sich, und niemand konnte sagen, ob nichts passiert
                # oder nichts ankommt. Der Heartbeat ist genau das Signal,
                # das diese Frage beantwortet; ihn der Oberflaeche
                # vorzuenthalten war eine Luecke, keine Entscheidung.
                self._notify_observers(HEARTBEAT_KEY, self._heartbeat_on)
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
