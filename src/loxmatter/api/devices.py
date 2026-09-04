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

"""Geraete- und Signal-API der WebUI (Spec 8, Ansichten 1 und 2).

`build_device_router` baut einen `APIRouter` mit Praefix `/api` - eingebunden
in `loxone.server.build_app`, das dieselbe FastAPI-App auch fuer die
Loxone-seitigen Routen (`/cmd`, `/resync`, `/health`) nutzt.

`client` ist `None`, wenn die Bruecke ohne Verbindung zu `matter-server`
gestartet wurde (siehe `build_app`). Die beiden Routen, die den Matter-Client
brauchen - Einlernen und Entfernen - antworten dann mit 503, statt eine
`AttributeError` auf `None` zu werfen; alle anderen Routen (lesen, umbenennen,
Export-Flag setzen) kommen ganz ohne Matter-Verbindung aus und bleiben nutzbar.

**Entfernen (Task 2): erst `remove_node`, dann `forget_device`.** Ein Geraet
zu entfernen ist zwei Schritte, die nicht in einer Transaktion liegen koennen
(der eine ist ein Netzwerkaufruf an matter-server, der andere ein lokaler
SQLite-Schreibzugriff) - einer von beiden kann gelingen, waehrend der andere
scheitert. Die beiden moeglichen Reihenfolgen hinterlassen bei einem
Teilausfall unterschiedlich schlimme Zustaende:

- **`forget_device` zuerst, dann `remove_node` scheitert:** `Store` haelt das
  Geraet fuer entfernt, es verschwindet aus `GET /api/devices` - aber es
  haengt weiterhin in der Matter-Fabric. Die WebUI hat ab diesem Moment
  keine `device_id` mehr, unter der sich ein erneutes Entfernen anstossen
  liesse. Ein stiller, von der Oberflaeche aus nicht mehr erreichbarer Rest.
- **`remove_node` zuerst, dann `forget_device` scheitert:** das Geraet ist
  tatsaechlich aus der Fabric entfernt, aber `Store` fuehrt es noch als
  aktiv. Es bleibt in `GET /api/devices` sichtbar - und meldet sich, sobald
  `BridgeMatterClient.subscribe` das zugehoerige `NODE_REMOVED`-Ereignis
  zustellt, ueber `Runtime.set_online` korrekt als nicht mehr erreichbar
  (`d<id>_online = false`), genau wie jedes andere Geraet, das seine
  Verbindung verliert (Spec 9). Ein erneutes `DELETE` bleibt moeglich, und
  der fehlgeschlagene zweite Schritt ist ein gewoehnlicher, sichtbarer
  Serverfehler, kein verschwundenes Geraet.

Die zweite Reihenfolge hinterlaesst damit im Fehlerfall einen sichtbaren,
diagnostizierbaren Zustand statt eines stillen; `remove_device` unten setzt
sie deshalb um.

**Unverifizierte Annahme (Minor #3, Review 2026-09-02):** "Ein erneutes
`DELETE` bleibt moeglich" oben setzt voraus, dass `remove_node` gegen einen
Node erneut aufgerufen werden darf, der beim ersten (teilweise gescheiterten)
Versuch schon aus der Fabric entfernt wurde - also gegen `matter-server`
retry-sicher ist. `tests/api/conftest.py::FakeMatterClient.remove_node`
haengt jeden Aufruf lediglich an eine Liste an und kann diese Annahme nicht
pruefen; ob das echte `MatterClient.remove_node` einen bereits entfernten
Node mit einem Fehler quittiert oder ihn klaglos ignoriert, ist gegen die
installierte `python-matter-server`-Version bislang nicht belegt (anders als
die drei Methoden im Docstring von `matter/client.py`, die explizit gegen
den Quelltext geprueft sind). Ein Fehlschlag dort waere kein neues Problem -
er landete wie jeder andere `MatterUnavailableError` als 502 -, aber die
Zusicherung "bleibt moeglich" ist bis dahin eine Annahme, keine belegte
Tatsache.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, HTTPException

from loxmatter.api.models import (
    CommissionRequest,
    DeviceOut,
    DeviceRename,
    SignalOut,
    SignalPatch,
)
from loxmatter.export.commands import extract_commands
from loxmatter.export.signals import to_inputs
from loxmatter.matter.client import BridgeMatterClient, CommissioningError, MatterUnavailableError
from loxmatter.matter.otbr import (
    ThreadDatasetUnavailableError,
    fetch_active_dataset,
    validated_dataset,
)
from loxmatter.model.store import Store, StoredDevice, StoredSignal, UnknownDeviceError
from loxmatter.profiles.table import Exportability, is_exportable

logger = logging.getLogger(__name__)

# Woher der Thread-Datensatz kommt, wenn matter-server ihn nicht (mehr) hat.
# Ein eigener Typ statt des blanken Aufrufs, damit `build_app` ihn in Tests
# durch eine Quelle ohne Netzwerk ersetzen kann - dasselbe Seam-Muster wie
# `session_factory` in `matter/client.py`.
ThreadDatasetSource = Callable[[], Awaitable[str]]

# Woher der gepruefte Datensatz kam - `validated_dataset` stellt diese Angabe seinen
# Meldungen voran. Der geholte nennt dort die URL des Border Routers, der von
# Hand eingetragene diese Zeile; sie landet ausschliesslich im Log, nie in der
# Antwort (siehe `commission_device`).
_MANUAL_DATASET_ORIGIN = "Das Eingabefeld"

# Warum ein Signal nicht exportierbar ist (Spec 6.6) - nur fuer die beiden
# Faelle, die `Exportability` von ANALOG/DIGITAL unterscheidet. `NONE` deckt
# sowohl Listen/Structs als auch (still, siehe Spec 6.6) Nullwerte ab; die
# Tabelle kann diese beiden nicht auseinanderhalten, weil `classify()` selbst
# es nicht tut.
_UNEXPORTABLE_REASONS: dict[Exportability, str] = {
    Exportability.TEXT: "Text - ein virtueller UDP-Eingang kennt nur Zahlen und digitale Werte",
    Exportability.NONE: "kein abbildbarer Wert - Liste, Struktur, oder derzeit ohne Wert (null)",
}


class RuntimeValues(Protocol):
    """Was diese Route von `runtime` braucht - `loxone.runtime.Runtime`
    erfuellt das bereits unveraendert (siehe dort `last_values_for` und
    `set_online`), ein Test kann es mit einem einfachen Double erfuellen,
    ohne eine echte `Runtime` samt Sender aufzubauen.

    `set_online` kam dazu, als das Einlernen die Erreichbarkeit eines frisch
    eingelernten Geraets selbst saeen musste (siehe `commission_device`) -
    lesen allein reicht dafuer nicht."""

    def last_values_for(self, device_id: int) -> dict[str, float | bool]: ...

    async def set_online(self, device_id: int, online: bool) -> None: ...


def _signal_out(signal: StoredSignal, values: dict[str, float | bool]) -> SignalOut:
    """`functional` kommt unveraendert aus `StoredSignal.functional` -
    `profiles.relevance.is_functional` braucht die Geraetetypen je Endpunkt
    (`device_types_by_endpoint`), die diese Funktion hier gar nicht sieht
    (nur `signal` und die aktuellen Werte). `Store.register_signals`
    berechnet das Ergebnis bereits einmalig bei der Registrierung, mit dem
    echten Geraeteabbild zur Hand, und schreibt es in die Zeile - siehe dort
    und `_migrate_to_v4` fuer Bestandsgeraete. Eine zweite Berechnung hier
    (oder gar in der Oberflaeche) wuerde dieselbe Regel ein zweites Mal
    nachbilden, ohne das Abbild zu haben, das sie eigentlich braucht."""
    exportable = is_exportable(signal.exportability)
    reason = None if exportable else _UNEXPORTABLE_REASONS.get(signal.exportability)
    return SignalOut(
        key=signal.key,
        path=signal.ref.path,
        kind=signal.ref.kind.value,
        title=signal.title,
        unit=signal.unit,
        value=values.get(signal.key),
        exportable=exportable,
        reason=reason,
        exported=signal.exported,
        functional=signal.functional,
        resend=signal.resend,
    )


def _device_out(device: StoredDevice, store: Store, runtime: RuntimeValues) -> DeviceOut:
    # `store.signals(device.id)` holt hier die volle Zeile pro Signal, obwohl
    # `list_devices` (Minor #2, Review 2026-09-02) sie nur zaehlt - ein N+1-
    # Zugriff pro Geraet in `GET /api/devices`. Bewusst hingenommen statt
    # einer eigenen COUNT/SUM-Abfrage: die Zahl der Geraete einer Bruecke
    # bleibt klein (eine Loxone-Instanz, keine Flotte), `exportable_count`
    # braucht ohnehin `is_exportable` pro Zeile - eine SQL-Aggregation muesste
    # diese Regel ein zweites Mal in SQL nachbilden und liefe damit genau in
    # das Auseinanderdriften, das Important #2 oben erst behoben hat.
    signals = store.signals(device.id)
    values = runtime.last_values_for(device.id)
    online = bool(values.get(f"d{device.id}_online", False))
    exportable_count = sum(1 for s in signals if is_exportable(s.exportability))
    # next_export_count (Nachbesserung Fix 7, Phase 6): dieselbe Zusammensetzung
    # wie `ExportDeviceOut.inputs` in `api/export.py` (`to_inputs`, gefiltert
    # auf `exported`) - keine zweite, nur aehnliche Zaehlung hier. Die
    # Gerätekachel zeigte bisher "159 Signale, 110 exportierbar" ueber einer
    # Liste von fuenf - beide Zahlen stimmten, keine beantwortete, wie viele
    # Eingaenge der naechste Export tatsaechlich erzeugt.
    next_export_count = len(to_inputs(signals, device.id, device.label))
    return DeviceOut(
        id=device.id,
        node_id=device.node_id,
        label=device.label,
        online=online,
        signal_count=len(signals),
        exportable_count=exportable_count,
        next_export_count=next_export_count,
    )


def _commissioning_detail(exc: CommissioningError, missing_dataset_reason: str | None) -> str:
    """Die Meldung, die in der Oberflaeche ankommt.

    Ohne den Zusatz stand dort nur, was matter-server selbst sagt -
    "Commission with code failed for node 7." Der eigentliche Grund
    ("Required network information not provided in commissioning
    parameters") steht ausschliesslich im Log von matter-server, und ohne
    Zugriff darauf ist die Meldung nicht deutbar: BLE, Pairing-Code und die
    gesicherte Sitzung zum Geraet waren allesamt in Ordnung, es fehlte nur
    das Netz, in das das Geraet gehoert haette.
    """
    detail = str(exc)
    if missing_dataset_reason is None:
        return detail
    return (
        f"{detail} Moegliche Ursache: matter-server hat keine Thread-Zugangsdaten, und "
        f"diese Bruecke konnte auch keine holen ({missing_dataset_reason}). Ein "
        "Thread-Geraet laesst sich ohne sie nicht einlernen - ein WiFi-Geraet dagegen "
        "schon, dann liegt es woanders. Abhilfe: den Border Router erreichbar machen, "
        "oder den Thread-Datensatz von Hand in das Feld darunter eintragen."
    )


def build_device_router(
    store: Store,
    client: BridgeMatterClient | None,
    runtime: RuntimeValues,
    thread_dataset_source: ThreadDatasetSource | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    fetch_dataset = thread_dataset_source or fetch_active_dataset

    def _require_device(device_id: int) -> StoredDevice:
        try:
            return store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _require_client() -> BridgeMatterClient:
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="Matter-Client nicht verfuegbar - die Bruecke laeuft ohne Verbindung"
                " zu matter-server",
            )
        return client

    @router.get("/devices")
    async def list_devices() -> list[DeviceOut]:
        return [_device_out(device, store, runtime) for device in store.devices()]

    @router.get("/devices/{device_id}")
    async def get_device(device_id: int) -> DeviceOut:
        device = _require_device(device_id)
        return _device_out(device, store, runtime)

    @router.get("/devices/{device_id}/signals")
    async def get_signals(device_id: int) -> list[SignalOut]:
        _require_device(device_id)
        values = runtime.last_values_for(device_id)
        return [_signal_out(signal, values) for signal in store.signals(device_id)]

    @router.patch("/devices/{device_id}")
    async def rename_device(device_id: int, patch: DeviceRename) -> DeviceOut:
        device = _require_device(device_id)
        store.rename_device(device.id, patch.label)
        return _device_out(store.device(device.id), store, runtime)

    @router.patch("/signals/{key}")
    async def rename_signal(key: str, patch: SignalPatch) -> SignalOut:
        """Aendert Titel, Export- und Resend-Flag. Der Schluessel bleibt unberuehrt.

        Spec 6.2: der Schluessel ist die Verdrahtung in Loxone. Waere er hier
        aenderbar, koennte ein Klick in der Oberflaeche einen Baustein im Haus
        still totlegen. Das Modell `SignalPatch` kennt deshalb gar kein Feld
        dafuer - ein mitgeschicktes `key` wird verworfen, nicht angewendet.

        Anders als jede geraete-gebundene Route (`_require_device` oben)
        loeste diese Route bisher ausschliesslich ueber `signal_by_key` auf,
        ohne zu pruefen, ob das zugehoerige Geraet ueberhaupt noch aktiv ist
        (Review-Fix Important #4, 2026-09-02): nach `DELETE
        /api/devices/{id}` meldete `GET /api/devices/{id}` korrekt 404, aber
        `PATCH /api/signals/{key}` mutierte die Zeile eines entfernten
        Geraets weiterhin klaglos - eine Signalzeile, die nirgends mehr in
        der Oberflaeche sichtbar ist, aber ueber ihren Schluessel trotzdem
        noch aenderbar bleibt. Die Pruefung unten schliesst diese Luecke.
        """
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"unbekannter Signal-Schluessel {key!r}")
        try:
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Signal {key!r} gehoert zu Geraet {stored.device_id}, das entfernt wurde",
            ) from exc
        if patch.title is not None:
            store.set_title(key, patch.title)
        if patch.exported is not None:
            store.set_exported(key, patch.exported)
        if patch.resend is not None:
            store.set_resend(key, patch.resend)

        updated = store.signal_by_key(key)
        assert updated is not None  # eben noch gefunden, in derselben Anfrage nicht geloescht
        values = runtime.last_values_for(updated.device_id)
        return _signal_out(updated, values)

    @router.post("/devices/commission", status_code=201)
    async def commission_device(request: CommissionRequest) -> DeviceOut:
        active_client = _require_client()

        # Warum das hier ueberhaupt steht: matter-server haelt die
        # Thread-Zugangsdaten NUR im Arbeitsspeicher und vergisst sie bei
        # jedem Neustart (die ganze Begruendung samt aufgezeichnetem
        # Ernstfall steht in `matter/otbr.py`). Das Eingabefeld allein hat
        # das nicht aufgefangen - es ist optional und wird nach jedem
        # Einlernen geleert, war beim naechsten Mal also leer.
        missing_dataset_reason: str | None = None

        if request.thread_dataset is not None:
            # Ein von Hand eingetragener Datensatz sticht den vom Host: er
            # ist der Weg fuer ein Thread-Netz, das nicht von diesem Border
            # Router kommt.
            #
            # Geprueft wird er auf demselben Weg wie der geholte - `validated_dataset`
            # aus `matter/otbr.py`, absichtlich dieselbe Funktion und keine
            # zweite Nachbildung derselben Regel (deshalb der Import eines
            # modul-privaten Namens). Ungeprueft durchgereicht loeste ein mit
            # Zeilenumbruch oder als JSON-Struktur eingefuegter Datensatz bei
            # matter-server ein `bytes.fromhex`-Scheitern aus, das als
            # `FailedCommand` zurueckkommt - keine `MatterUnavailableError`,
            # also 500 "Internal Server Error", die nichtssagendste aller
            # Antworten.
            try:
                dataset = validated_dataset(request.thread_dataset, _MANUAL_DATASET_ORIGIN)
            except ThreadDatasetUnavailableError as exc:
                # 422 wie beim abgelehnten Pairing-Code: die Anfrage ist
                # wohlgeformt, ihr Inhalt aber nicht verwendbar. Der Grund
                # steht in der Antwort, der Datensatz selbst NICHT - er
                # enthaelt den Netzwerkschluessel des Thread-Netzes (siehe
                # `matter/otbr.py`). Auch `str(exc)` bleibt draussen: seine
                # Formulierung fragt nach dem Border Router, und der hat mit
                # einem Eingabefeld nichts zu tun. Ins Log darf er, dort
                # nennt er die Laenge.
                logger.warning("Eingetragener Thread-Datensatz abgelehnt: %s", exc)
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Der eingetragene Thread-Datensatz ist keiner. Erwartet wird das "
                        "Hex-TLV des aktiven Datensatzes - eine einzige Zeile aus 0-9 und "
                        "a-f, so wie `ot-ctl dataset active -x` sie ausgibt. Eine "
                        "JSON-Struktur, Anfuehrungszeichen oder Text darin gehoeren nicht "
                        "hinein. Der Wert selbst steht bewusst nicht in dieser Meldung: "
                        "er enthaelt den Netzwerkschluessel des Thread-Netzes."
                    ),
                ) from exc
            try:
                await active_client.set_thread_dataset(dataset)
            except MatterUnavailableError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        elif not active_client.thread_dataset_set:
            try:
                dataset = await fetch_dataset()
            except ThreadDatasetUnavailableError as exc:
                # KEIN Abbruch: ein WiFi-Geraet braucht gar keinen
                # Thread-Datensatz, und ein Aufbau ohne eigenen Border Router
                # ist damit weiterhin bedienbar. Der Grund wird nur gemerkt,
                # fuer den Fall, dass das Einlernen gleich scheitert - dann
                # ist er die wahrscheinliche Ursache und gehoert in die
                # Meldung.
                missing_dataset_reason = str(exc)
                logger.warning("Kein Thread-Datensatz verfuegbar, Einlernen laeuft ohne: %s", exc)
            else:
                try:
                    await active_client.set_thread_dataset(dataset)
                except MatterUnavailableError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc

        try:
            snapshot = await active_client.commission_with_code(request.code)
        except CommissioningError as exc:
            # 422: die Anfrage selbst war wohlgeformt, aber das Geraet hat das
            # Einlernen abgelehnt (falscher Code, schon in einem anderen
            # Oekosystem, Timeout beim Interview) - siehe CommissioningError.
            raise HTTPException(
                status_code=422, detail=_commissioning_detail(exc, missing_dataset_reason)
            ) from exc
        except MatterUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        # Derselbe Ablauf wie beim CLI-Export (cli.py): register_device vor
        # register_signals vor register_commands, denn beide brauchen die
        # frisch vergebene device_id.
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

        # Die Erreichbarkeit des neuen Geraets MUSS hier gesaet werden, aus
        # `snapshot.available` - genau wie `Runtime.seed_from_snapshot` es
        # beim Start der Bruecke fuer die bereits bekannten Geraete tut.
        #
        # Der Grund ist eine Reihenfolge, die sich von hier aus nicht
        # beeinflussen laesst (aufgezeichnet am 2026-09-04): matter-server
        # meldet `NODE_ADDED` bereits WAEHREND `commission_with_code` laeuft
        # (`device_controller._setup_node` ruft `signal_event(
        # EventType.NODE_ADDED, ...)` noch vor der Rueckkehr des Aufrufs).
        # Zu diesem Zeitpunkt hat `register_device` oben dem Node noch keine
        # device_id gegeben, und `BridgeMatterClient._dispatch_loop` verwirft
        # die Meldung folgerichtig ("Aktualisierung fuer unbekannte Node ...
        # verworfen") - die eine Gelegenheit, bei der `d<id>_online` von
        # selbst entstanden waere, ist damit vorbei, bevor diese Route
        # ueberhaupt wieder an die Reihe kommt.
        #
        # Fuer ein ruhig im Netz stehendes Geraet folgt danach keine weitere
        # `NODE_ADDED`/`NODE_UPDATED`-Meldung, und `_device_out` liest einen
        # fehlenden Schluessel als `False`. Das Geraet stand deshalb nach dem
        # Einlernen auf "offline" und blieb es bis zum naechsten Neustart der
        # Bruecke - obwohl matter-server es laengst interviewt und eine
        # Subscription darauf aufgebaut hatte.
        #
        # Ab hier laeuft nur noch Nachlauf, und Nachlauf darf den Vorgang
        # nicht nachtraeglich absagen: VOR `register_device` ist ein Fehler
        # eine Absage - das Geraet ist dann nicht eingelernt, und eine
        # Fehlermeldung ist die richtige Antwort. DANACH steht es in der
        # Fabric UND im Store, und eine Fehlermeldung waere schlicht falsch.
        # Sie fuehrte in eine Sackgasse: die Oberflaeche zeigte "Einlernen
        # fehlgeschlagen" und keine Geraetekachel, der Bedienende drueckte
        # erneut auf "Einlernen", und der aufgedruckte Code war laengst
        # verbraucht (422). Der Fehlschlag gehoert deshalb ins Log, nicht in
        # die Antwort. Konkret erreichbar ueber `UdpSender.send` ->
        # `socket.sendto`, das `OSError` wirft, wenn das Miniserver-Netz
        # kurz weg ist - deshalb `Exception` und nicht nur ein einzelner Typ.
        try:
            await runtime.set_online(device_id, snapshot.available)
        except Exception:
            logger.exception(
                "Erreichbarkeit des frisch eingelernten Geraets %s konnte nicht gesaet "
                "werden - das Geraet ist eingelernt, seine Kachel steht bis zur naechsten "
                "Meldung von matter-server aber auf offline",
                device_id,
            )

        # Erst jetzt, nach `register_device`: `follow_node` loest die Node-ID
        # ueber den Store auf, und vorher gaebe es dort nichts aufzuloesen -
        # dasselbe Wettrennen, das `NODE_ADDED` bereits verloren hat (siehe
        # den Kommentar oben und den Docstring von `follow_node`). Legt die
        # Attribut-Abonnements fuer dieses Geraet an und saeet seine Werte,
        # damit die Signale sofort Zahlen zeigen statt Striche - frueher
        # brauchte es dafuer einen Neustart der Bruecke.
        #
        # `seed_even_without_new_paths`, weil die Abonnements zu diesem
        # Zeitpunkt in aller Regel schon stehen: derselbe `NODE_ADDED`-Lauf,
        # der oben die Erreichbarkeit verloren hat, hat die
        # Dispatch-Schleife von `BridgeMatterClient` bereits jeden Pfad
        # dieses Node abonnieren lassen - nur eben ohne device_id, also ohne
        # zu saeen. Ohne den Schalter faende dieser Aufruf hier einen leeren
        # Diff vor und kehrte um, bevor er saet; die Startwerte kaemen dann
        # nie an, und ein statischer Pfad (Spannung ohne Last, Batteriestand,
        # der Aus-Zustand einer Steckdose) bliebe ein Strich, weil
        # matter-server unveraenderte Werte unterdrueckt.
        #
        # Ebenfalls Nachlauf, ebenfalls abgesichert (siehe oben): das
        # Szenario, um das dieser Zweig kreist, ist ein matter-server, der
        # unmittelbar nach dem Einlernen neu startet - dann laeuft
        # `follow_node` in `_require_upstream` und wirft
        # `MatterUnavailableError`, obwohl das Geraet vollstaendig eingelernt
        # ist. Ohne Werte, aber eingelernt: die Signalzeilen stehen (sie
        # entstehen aus `register_signals` oben), sie fuellen sich, sobald die
        # Verbindung zurueck ist und das naechste `NODE_ADDED`/`NODE_UPDATED`
        # die Dispatch-Schleife nachziehen laesst.
        try:
            await active_client.follow_node(snapshot.node_id, seed_even_without_new_paths=True)
        except Exception:
            logger.exception(
                "Abonnements des frisch eingelernten Geraets %s konnten nicht nachgezogen "
                "werden - das Geraet ist eingelernt, seine Signale bleiben bis zur "
                "naechsten Meldung von matter-server aber ohne Werte",
                device_id,
            )
        return _device_out(store.device(device_id), store, runtime)

    @router.delete("/devices/{device_id}", status_code=204)
    async def remove_device(device_id: int) -> None:
        device = _require_device(device_id)
        active_client = _require_client()
        try:
            # Reihenfolge siehe Modul-Docstring: erst die Fabric, dann Store.
            await active_client.remove_node(device.node_id)
        except MatterUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.forget_device(device.id)

    return router
