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

"""Diagnose einer fremden Installation (Spec 10.5).

Vier Werkzeuge, ein gemeinsamer Zweck: eine Person meldet "es geht nicht",
und jemand anderes - ein Mitentwickler, ein Ersthelfer im Forum - muss ohne
Zugriff auf das Haus herausfinden, warum. Ohne diese Seite bleibt nur "es
geht nicht" als gesamte Fehlerbeschreibung.

**Der Mitschnitt gesendeter Datagramme haengt in `UdpSender`, nicht daneben.**
Ein Mitschnitt, der VOR dem Senden ansetzt (z. B. in `Runtime.on_attribute`),
zeigt, was gesendet werden SOLLTE. Ein Mitschnitt in `UdpSender.send` selbst
zeigt, was tatsaechlich ueber den Draht ging - nach Entprellung, nach
Rate-Limit, nach jedem stillen "wurde uebersprungen, weil unveraendert".
Genau die Faelle, in denen Absicht und Wirklichkeit auseinanderlaufen, sind
die interessanten fuer eine Diagnose - ein Mitschnitt daneben wuerde sie
verstecken, nicht zeigen. Deshalb importiert `loxone.sender` `RingBuffer`
von hier (siehe dort) statt umgekehrt: dieses Modul ist der im Interface-
Vertrag benannte Ort fuer den generischen, laufzeitunabhaengigen Ringpuffer,
den sowohl der Datagramm- als auch der Kommando-Mitschnitt (server.py)
brauchen - eine Umkehrung der sonst ueblichen Richtung "api haengt von
loxone ab" (siehe z. B. api/live.py, das `Runtime` importiert), hier bewusst
in Kauf genommen, weil `RingBuffer` selbst keinerlei API-spezifisches
Wissen traegt (kein FastAPI-Import auf Modulebene bevor jede der beiden
Nutzstellen ihn braucht) und die Alternative - ein drittes, eigenes Modul
nur fuer eine 15-zeilige Klasse - mehr Indirektion gekostet haette, als sie
eingespart haette.

**Jede rote Zeile im Systemcheck traegt einen konkreten Hinweis.** Ein roter
Punkt ohne Erklaerung verschiebt das Raetsel nur von "es geht nicht" zu "der
Systemcheck sagt rot, aber nicht wieso" - dieselbe Sackgasse, nur eine Ebene
tiefer. `_run_check` unten fasst deshalb JEDE Pruefung zusaetzlich in ein
eigenes try/except: eine Pruefung, die selbst einen Programmfehler enthaelt
(nicht nur einen erwarteten Fehlerfall wie "Miniserver nicht erreichbar"),
wird zu genau einer roten Zeile mit Hinweis auf das Server-Log - nicht zu
einem 500 fuer den gesamten Systemcheck. Eine Diagnose, die an ihrer eigenen
Pruefung scheitert, waere schlimmer als gar keine (siehe
`test_a_check_that_raises_unexpectedly_fails_gracefully`).

**Die Sicherung ist kein Nebenpunkt.** Spec 4.1 nennt das matter-server-
Datenverzeichnis (darin: die Fabric-Credentials) den einzigen unersetzlichen
Zustand des ganzen Systems - geht es verloren, muss jedes Geraet neu
eingelernt werden, bei Thread-Geraeten heisst das: zuruecksetzen, aus dem
alten Netz werfen, neu koppeln. `GET /api/diagnostics/fabric-backup`
liefert den Inhalt dieses Verzeichnisses als ZIP.

Diese Datei ist Schluesselmaterial, kein Protokoll - wer sie besitzt, kann
die Fabric uebernehmen. Zwei Konsequenzen, beide unten an der Route
dokumentiert:

- **Geschuetzt seit Task 8 (Phase 5, Spec 9).** Nicht ueber einen
  zusaetzlichen `Depends(...)`-Parameter an dieser Funktion selbst, sondern
  einheitlich fuer den gesamten Router: `loxone.server.build_app` bindet
  `build_diagnostics_router(...)` (wie alle fuenf `/api`-Router) ueber
  `app.include_router(..., dependencies=[Depends(guard)])` ein, `guard` aus
  `build_api_guard` (siehe dort). Das schuetzt jede Route dieses Routers
  gleich - und ohne das Risiko, eine kuenftige sechste Diagnose-Route
  versehentlich ungeschuetzt zu lassen, wie es ein Parameter je Funktion
  haette zulassen koennen.
- **Nichts davon wird geloggt** - weder der aufgeloeste Pfad noch die darin
  enthaltenen Dateinamen. Ein Server-Log ist kein Ort fuer Hinweise auf
  Schluesselmaterial, selbst nicht auf `debug`-Ebene.

Aus demselben Grund - ein Kommando-Log, das fuer jeden mitliest, der die
Diagnoseseite oeffnen kann - traegt `GET /api/diagnostics/commands` bewusst
NIE eine Query-Zeichenkette, nur den Pfad. Ein `/cmd/{key}/{value}`-Aufruf
legt seinen Wert absichtlich offen im Pfad ab (das ist der Zweck dieses
Logs: zu sehen, welcher Wert ankam) - eine Query-Zeichenkette dagegen ist
fuer keine der heutigen Routen vorgesehen und faehrt deshalb ausschliesslich
als Vorsichtsmassnahme mit: Task 8s Token laeuft ausdruecklich NICHT als
Query-Parameter, sondern als `Authorization`-Header bzw. - beim
Browser-WebSocket, der keine eigenen Kopfzeilen setzen kann - als
Subprotokoll `bearer, <Token>` (siehe `loxone.server.build_api_guard`),
und zwar genau deshalb: in diesem fuer jeden Diagnose-Betrachter lesbaren
Log hat ein Geheimnis nichts zu suchen. Aus demselben,
eher praktischen Grund (ein knapper Ringpuffer, den ein pollender
Diagnose-Tab nicht mit sich selbst fluten soll) nimmt `server.py` Aufrufe
von `/api/diagnostics/*` selbst gar nicht erst in den Kommando-Log auf -
siehe dort.
"""

from __future__ import annotations

import collections
import io
import logging
import socket
import sqlite3
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from loxmatter.model.store import Store

if TYPE_CHECKING:
    # Ausschliesslich fuer Typannotationen - siehe Moduldocstring, warum
    # `loxone.sender` NICHT auf Modulebene importiert wird (das waere ein
    # echter Ringimport: sender.py importiert `RingBuffer` von HIER). Dank
    # `from __future__ import annotations` wertet Python Annotationen ohnehin
    # nur als Zeichenketten aus - dieser Block existiert einzig fuer mypy.
    from loxmatter.loxone.sender import UdpSender
    from loxmatter.matter.client import BridgeMatterClient

logger = logging.getLogger(__name__)

# Oeffentlich, weil die Oberflaeche denselben Dateinamen vergeben muss:
# seit die Downloads ueber `fetch` statt ueber einen Link laufen, benennt
# der Browser die Datei selbst (siehe `web/app.js`, `download`).
FABRIC_BACKUP_NAME = "matter-fabric-backup.zip"


class RingBuffer[T]:
    """Haelt die letzten N Eintraege, aeltere fallen heraus.

    Eine Bruecke laeuft monatelang. Ein Mitschnitt, der mitwaechst, ist
    irgendwann das groesste Objekt im Prozess - und der interessante Teil
    ist ohnehin nur die letzten Minuten/Stunden. `collections.deque(maxlen=
    ...)` erledigt das Verwerfen der aeltesten Eintraege bereits nativ in
    O(1); diese Klasse fuegt nur die schmale, absichtlich MINIMALE
    Oberflaeche hinzu, die die Diagnose-Routen brauchen (anhaengen,
    iterieren, zaehlen, beobachten) - kein `clear()`, kein Indexzugriff,
    nichts, das ein Aufrufer nutzen koennte, um Eintraege nachtraeglich zu
    manipulieren.

    **Ein Leser, der `for entry in ring:` durchlaufen koennte, waehrend aus
    einem anderen Thread gleichzeitig angehaengt wird, MUSS zuerst
    `list(ring)` aufrufen, um eine Momentaufnahme zu bekommen.** `append` ist dank der
    GIL ein atomarer, einzelner C-Aufruf (siehe
    `diagnostics.logbuffer`-Moduldocstring) - `__iter__` unten dagegen
    nicht: er gibt einen lebenden `deque`-Iterator zurueck, und `deque`
    erkennt eine Mutation waehrend einer laufenden Iteration. Sobald der
    Ring einmal voll ist, verdraengt jedes weitere `append` den aeltesten
    Eintrag - genau das ist eine Mutation im Sinne dieser Erkennung, und
    eine parallel laufende `for`-Schleife bricht dann mit
    `RuntimeError('deque mutated during iteration')` ab. Solange jeder Ring
    nur aus einem einzigen Pfad heraus beschrieben wird (bislang der Fall:
    ein Event-Loop je Ring), tritt das nie auf. `diagnostics.logbuffer.
    LogBufferHandler` ist der erste Schreiber, der aus BELIEBIGEN Threads
    gleichzeitig anhaengen kann - fuer einen Ring, den er fuellt, ist eine
    blosse `for`-Schleife deshalb nicht mehr sicher; `list(ring)` dagegen
    schon, weil auch das ein einziger, atomarer C-Aufruf ist.

    **Beobachter (Task 4, Phase 5, Spec 10.5).** `add_observer`/
    `remove_observer` benachrichtigen bei jedem `append` - dieselbe
    Anmelde-/Abmelde-Form wie `LogBufferHandler.add_observer`, absichtlich
    HIER statt in einer weiteren, eigenen Klasse: der Kommando-Log-Ring in
    `loxone.server` braucht eine Beobachterkette (fuer `api.diagnostics_
    live`), hat aber - anders als `LogBufferHandler` - keinen eigenen
    Besitzer-Typ, an dem sie sonst haengen koennte (er ist dort eine blosse
    lokale Variable). `UdpSender.add_datagram_observer`/
    `remove_datagram_observer` sind seit Nachbesserung Task 7 (Fix 2) KEINE
    zweite, eigene Umsetzung derselben Mechanik mehr, sondern duenne
    Weiterleitungen genau auf `add_observer`/`remove_observer` hier - siehe
    `loxone.sender`-Moduldocstring, Abschnitt "Beobachterkette". Ein
    Beobachterfehler wird deshalb an EINER Stelle geloggt und uebersprungen
    (unten in `append`), nicht mehr an zwei verschiedenen - anders als bei
    `LogBufferHandler` gibt es hier kein Rekursionsrisiko durch die eigene
    Fehlerprotokollierung, weil kein Ring dieses Projekts Logzeilen selbst
    erzeugt.

    **Warnung fuer kuenftige Aufrufer:** `LogBufferHandler.entries` ist
    ebenfalls ein `RingBuffer`, oeffentlich lesbar fuer die Momentaufnahme -
    `add_observer` NIEMALS direkt auf `log_handler.entries` aufrufen. Ein
    dort registrierter Beobachter liefe synchron innerhalb von
    `LogBufferHandler.emit()`, waehrend `logging.Handler.lock` gehalten
    wird und OHNE die dortige Wiedereintrittssperre - protokolliert dieser
    Beobachter selbst ueber denselben Logger, ist das eine echte,
    unbegrenzte Rekursion (siehe `diagnostics.logbuffer`-Moduldocstring).
    `LogBufferHandler.add_observer` ist der einzige sichere Weg, neue
    Logzeilen zu beobachten.

    **Diese Warnung gilt NICHT spiegelbildlich fuer `UdpSender.datagram_log`
    - eine fruehere Fassung dieses Docstrings behauptete das
    faelschlich** (Review-Fix Kleinigkeit #3, 2026-09-03; als falsch erkannt
    und richtiggestellt in der Nachbesserung Task 7, Fix 2). Seit
    `UdpSender.add_datagram_observer` eine duenne Weiterleitung auf
    `self._datagram_log.add_observer` ist (siehe oben), sind `sender.
    add_datagram_observer(cb)` und `sender.datagram_log.add_observer(cb)`
    DERSELBE Aufruf auf demselben Ring - es gibt keinen "unsicheren" und
    keinen "sicheren" Weg mehr, zwischen denen zu unterscheiden waere. Ein
    dort registrierter Beobachter laeuft so oder so synchron innerhalb von
    `UdpSender.send`s `async with self._lock` (siehe dort) - ein langsamer
    oder haengender Beobachter bremst dadurch jeden nachfolgenden Versand
    ueber denselben `UdpSender`, unabhaengig davon, ueber welchen der beiden
    (identischen) Wege er sich angemeldet hat. `UdpSender.
    add_datagram_observer`/`remove_datagram_observer` bleiben trotzdem als
    eigene, oeffentliche Methoden bestehen - nicht aus Sicherheitsgruenden,
    sondern damit der Typ `DatagramLogEntry` in ihrer Signatur sichtbar
    bleibt und ein Aufrufer nicht wissen muss, dass der Mitschnitt intern
    ein `RingBuffer` ist (siehe `loxone.sender`-Moduldocstring)."""

    def __init__(self, maxlen: int = 500) -> None:
        self._items: collections.deque[T] = collections.deque(maxlen=maxlen)
        self._observers: list[Callable[[T], None]] = []

    def append(self, item: T) -> None:
        self._items.append(item)
        for observer in list(self._observers):
            # Kopie der Liste iterieren - ein Beobachter, der sich selbst
            # waehrend seines Aufrufs abmeldet, darf die laufende
            # Benachrichtigung der uebrigen nicht stoeren (dasselbe Muster
            # wie `Runtime._notify_observers`; `UdpSender.
            # add_datagram_observer`/`remove_datagram_observer` haengen seit
            # Nachbesserung Task 7, Fix 2 direkt an DIESEM `append`, keine
            # eigene Kopie mehr).
            try:
                observer(item)
            except Exception:
                logger.exception(
                    "Beobachter fuer einen neuen Ringpuffer-Eintrag ist fehlgeschlagen - "
                    "wird uebersprungen"
                )

    def add_observer(self, callback: Callable[[T], None]) -> None:
        """Meldet einen Beobachter an, der jeden NEUEN Eintrag sieht - nicht
        die bereits vorhandenen (siehe Klassendocstring). Der Beobachter
        darf nicht blockieren: `append` laeuft im Aufrufpfad des
        jeweiligen Schreibers (siehe dort)."""
        self._observers.append(callback)

    def remove_observer(self, callback: Callable[[T], None]) -> None:
        """Meldet einen Beobachter wieder ab. Ein unbekannter Beobachter
        (z. B. doppelt abgemeldet) ist kein Fehler, sondern wird still
        ignoriert - dieselbe Regel wie bei `Runtime.remove_observer`."""
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True)
class DatagramLogEntry:
    """Ein tatsaechlich ueber den UDP-Socket verschicktes Datagramm - siehe
    `UdpSender.send` (Moduldocstring dort) fuer die genaue Aufzeichnungsstelle.

    `value` ist bereits die fertige Textform (siehe `loxone.values.
    format_value`), nicht der rohe `float | bool`-Wert - dieselbe Form, die
    auch tatsaechlich auf der Leitung stand.

    `forced` uebernimmt unveraendert das `force`-Argument, mit dem `send()`
    aufgerufen wurde (Nachbesserung Task 6, 2026-09-03): `True` heisst
    "gesendet, obwohl sich der Wert nicht geaendert hat" - das trifft in
    diesem Projekt auf GENAU zwei Aufrufer zu, `Runtime.resend_all()` und
    den Heartbeat (`Runtime._heartbeat_loop`). `False` heisst dagegen "eine
    echte Wertaenderung" - ein Impuls (`Runtime.on_event`) und sein Zaehler
    zaehlen dazu, auch wenn beide binnen Mikrosekunden hintereinander
    gesendet werden. Genau diese Unterscheidung ersetzt die fruehere
    Rauschfilter-Heuristik der WebUI (`app.js`, `DATAGRAM_BURST_GAP_MS`),
    die ausschliesslich an der Ankunftsrate im Browser gemessen hatte und
    damit jeden schnell aufeinanderfolgenden, aber echten Wertewechsel
    faelschlich als Rauschen einstufte."""

    key: str
    value: str
    timestamp: str
    forced: bool


@dataclass(frozen=True)
class CommandLogEntry:
    """Ein eingehender HTTP-Aufruf mit seinem Ergebnis - siehe `server.py`,
    Middleware `_record_command`."""

    method: str
    path: str
    status: int
    timestamp: str


class DatagramLogEntryOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    value: str
    timestamp: str


class CommandLogEntryOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: str
    path: str
    status: int
    timestamp: str


class SystemCheckOut(BaseModel):
    """Eine Zeile im Systemcheck - IMMER mit `detail`, ob gruen oder rot.
    Siehe Moduldocstring, "Jede rote Zeile...", und `test_system_check_
    reports_each_line_with_a_verdict`, das `detail` fuer JEDE Zeile prueft,
    nicht nur fuer fehlgeschlagene."""

    model_config = ConfigDict(frozen=True)

    name: str
    ok: bool
    detail: str


_CheckFn = Callable[[], tuple[bool, str]]


def _run_check(name: str, check: _CheckFn) -> SystemCheckOut:
    """Fuehrt eine einzelne Pruefung aus und wandelt JEDE Ausnahme - nicht
    nur die von der jeweiligen Pruefung selbst schon abgefangenen - in eine
    rote Zeile um, statt den kompletten `GET /api/diagnostics/system`-Aufruf
    mit 500 abbrechen zu lassen. Siehe Moduldocstring."""
    try:
        ok, detail = check()
    except Exception as exc:
        logger.exception("Systemcheck %r ist an einem unerwarteten Fehler gescheitert", name)
        return SystemCheckOut(
            name=name,
            ok=False,
            detail=(
                f"Diese Pruefung selbst ist fehlgeschlagen ({exc}) - das ist ein Fehler in "
                "der Pruefung, nicht zwangslaeufig im gepruerften System. Der volle "
                "Traceback steht im Server-Log."
            ),
        )
    return SystemCheckOut(name=name, ok=ok, detail=detail)


def _check_matter_server(client: BridgeMatterClient | None) -> tuple[bool, str]:
    if client is None:
        return False, (
            "Kein matter-server-Client konfiguriert - die Bruecke laeuft ohne Matter-"
            "Anbindung. Das ist bei `loxmatter run` immer gesetzt; fehlt es hier, "
            "wurde dieser Dienst mit einem unvollstaendigen Aufbau gestartet."
        )
    if not client.connected:
        return False, (
            "Keine aktive Verbindung zu matter-server. Laeuft der Dienst "
            "(z. B. `docker compose ps matter-server`)? Ist die --url-Adresse aus "
            "`loxmatter run` noch erreichbar?"
        )
    return True, "Verbunden."


def _check_store(store: Store) -> tuple[bool, str]:
    # sqlite3.Error, nicht blind `Exception` - eine unerwartete Fehlerart
    # (ein echter Bug statt einer nicht beschreibbaren Datenbank) faellt
    # bewusst durch bis zum Sicherheitsnetz in `_run_check`, das dann seine
    # eigene, generischere rote Zeile erzeugt (siehe Moduldocstring).
    try:
        store.check_writable()
    except sqlite3.Error as exc:
        return False, (
            f"Die Signalschluessel-Datenbank ist nicht beschreibbar ({exc}). Pruefen Sie "
            "Speicherplatz und Dateirechte des eingehaengten Datenvolumes - ohne "
            "Schreibzugriff kann kein neues Geraet eingelernt und kein Export vermerkt "
            "werden."
        )
    return True, "Beschreibbar."


def _check_ipv6() -> tuple[bool, str]:
    """Matter/Thread braucht IPv6 - ein Host ohne globalen IPv6-Pfad kann kein
    Thread-Geraet erreichen, selbst wenn der Border Router laeuft.

    Verbindet sich dabei nirgendwohin: `2001:db8::1` ist die von RFC 3849
    fuer genau diesen Zweck reservierte, garantiert nie geroutete
    Dokumentations-Adresse, und `connect()` auf einem UDP-Socket sendet
    ohnehin kein einziges Paket - er traegt nur die lokale Routing-
    Entscheidung des Kernels ein (welche eigene Adresse waere die Quelle,
    gaebe es ein Ziel dort). Kein Netzwerkkontakt, keine Wartezeit."""
    if not socket.has_ipv6:
        return False, "Diese Python-Installation wurde ohne IPv6-Unterstuetzung gebaut."
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
            probe.connect(("2001:db8::1", 80))
            local_address = probe.getsockname()[0]
    except OSError as exc:
        return False, (
            f"Kein lokaler IPv6-Pfad gefunden ({exc}). Matter/Thread-Geraete sind ohne "
            "IPv6 nicht erreichbar - pruefen Sie die Netzwerkkonfiguration dieses Hosts "
            "(z. B. `ip -6 route`) und den Thread-Border-Router."
        )
    if local_address in ("::1",) or local_address.startswith("fe80"):
        return False, (
            f"Nur eine link-lokale/Loopback-IPv6-Adresse gefunden ({local_address}). "
            "Matter/Thread braucht eine geroutete IPv6-Adresse - pruefen Sie den Thread-"
            "Border-Router bzw. die Netzwerkkonfiguration dieses Hosts."
        )
    return True, f"Lokale, geroutete IPv6-Adresse gefunden: {local_address}."


def _check_miniserver(sender: UdpSender | None) -> tuple[bool, str]:
    """Der Miniserver wertet UDP-Antworten nicht aus (Spec 6.1, siehe
    server.py-Moduldocstring: "er schickt und vergisst") - eine echte
    Erreichbarkeitspruefung gibt es fuer ein Fire-and-Forget-Protokoll ohne
    ICMP-Auswertung (Root-Rechte, hier bewusst vermieden) nicht. Diese
    Pruefung bestaetigt deshalb nur: es gibt einen lokalen Routing-Pfad zum
    konfigurierten Ziel (dieselbe verbindungslose, netzwerkfreie Technik wie
    `_check_ipv6` oben, nur mit dem tatsaechlichen Ziel statt einer
    Dokumentations-Adresse) - keine Zustellung."""
    if sender is None:
        return False, (
            "Kein UDP-Sender konfiguriert - die Bruecke sendet keine Werte an den "
            "Miniserver. Das ist bei `loxmatter run` immer gesetzt; fehlt es hier, wurde "
            "dieser Dienst mit einem unvollstaendigen Aufbau gestartet."
        )
    host, port = sender.target
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, port))
    except OSError as exc:
        return False, (
            f"Kein Netzwerkpfad zu {host}:{port} gefunden ({exc}). Ist der Miniserver "
            "eingeschaltet und im selben Netz erreichbar? Stimmen IP und Port aus "
            "`loxmatter run --miniserver`/`--port`?"
        )
    return True, (
        f"Ein Netzwerkpfad zu {host}:{port} existiert. Das bestaetigt nur Routing, keine "
        "tatsaechliche Zustellung - der Miniserver wertet UDP-Antworten nicht aus."
    )


def build_diagnostics_router(
    store: Store,
    command_log: RingBuffer[CommandLogEntry],
    client: BridgeMatterClient | None,
    sender: UdpSender | None,
    matter_data_dir: Path | None,
) -> APIRouter:
    """Baut den `APIRouter` fuer `/api/diagnostics/*` (Spec 10.5).

    `client`, `sender` und `matter_data_dir` duerfen `None` sein - `build_app`
    gibt fuer `client`/`sender` bereits `None` als Default vor (aus demselben
    Grund, den `loxone.server` dort dokumentiert: bestehende Aufrufer sollen
    unveraendert weiterlaufen). `None` bedeutet hier jeweils "dieser Teil der
    Diagnose ist fuer diesen Lauf nicht verfuegbar", nicht "die Diagnose
    insgesamt fehlt" - `/datagrams` liefert dann eine leere Liste, `/system`
    eine rote Zeile mit Hinweis, `/fabric-backup` einen 503 statt eines
    500/leeren ZIPs."""
    router = APIRouter(prefix="/api/diagnostics")

    @router.get("/datagrams")
    async def datagrams(
        device_id: int | None = Query(
            None, description="Nur Datagramme dieses Geraets (Schluessel-Praefix d<id>_)"
        ),
    ) -> list[DatagramLogEntryOut]:
        if sender is None:
            return []
        prefix = f"d{device_id}_" if device_id is not None else None
        return [
            DatagramLogEntryOut(key=entry.key, value=entry.value, timestamp=entry.timestamp)
            for entry in sender.datagram_log
            if prefix is None or entry.key.startswith(prefix)
        ]

    @router.get("/commands")
    async def commands() -> list[CommandLogEntryOut]:
        return [
            CommandLogEntryOut(
                method=entry.method, path=entry.path, status=entry.status, timestamp=entry.timestamp
            )
            for entry in command_log
        ]

    @router.get("/system")
    async def system() -> list[SystemCheckOut]:
        return [
            _run_check("matter-server", lambda: _check_matter_server(client)),
            _run_check("store", lambda: _check_store(store)),
            _run_check("ipv6", _check_ipv6),
            _run_check("miniserver", lambda: _check_miniserver(sender)),
        ]

    @router.get("/fabric-backup")
    async def fabric_backup() -> Response:
        """**WER DIESE ROUTE ABRUFEN KANN, KANN DIE FABRIC UEBERNEHMEN.** Das
        ist der erste Satz dieses Docstrings mit Absicht.

        Der Schutz sitzt nicht an dieser Funktion, sondern einheitlich am
        gesamten Router (`loxone.server.build_api_guard`): ohne gueltiges
        Sitzungs-Cookie und ohne gueltiges Bearer-Token endet der Aufruf mit
        401, bevor diese Funktion ueberhaupt laeuft.

        **Der frueher hier stehende 403-Zweig ist entfallen** (WebUI-Login,
        Spec 11). Er verteidigte den Fall "der Dienst laeuft ohne jedes
        Zugangsmittel, also sind alle `/api`-Routen offen" - genau diesen
        Fall gibt es nicht mehr: ohne gesetztes Passwort laesst der Waechter
        keine `/api`-Route zu, und wer hier ankommt, hat einen Nachweis
        vorgezeigt. Ein unerreichbarer Zweig, dessen Docstring eine Lage
        beschreibt, die es nicht mehr gibt, waere schlimmer als kein Zweig:
        der naechste Leser verliesse sich auf eine Bedingung, die nichts
        mehr prueft. Dass der Waechter tatsaechlich an JEDEM der fuenf Router
        haengt, prueft `tests/api/test_security.py` Router fuer Router
        einzeln, statt sich auf den gemeinsamen Praefix zu verlassen.

        503 bleibt fuer "das Datenverzeichnis ist nicht eingehaengt bzw.
        existiert nicht" (unten) - eine Konfigurationsluecke, die diese
        Faehigkeit ueberhaupt erst herstellen wuerde.

        Sicherung des matter-server-Datenverzeichnisses (Spec 4.1, 8) als
        Download.

        Loggt bewusst NICHTS - weder den aufgeloesten Pfad noch die
        enthaltenen Dateinamen (siehe Moduldocstring)."""
        # Absichtlich kein `logger`-Aufruf in dieser ganzen Funktion, auch
        # nicht in den beiden Fehlerzweigen unten: schon der konfigurierte
        # PFAD ist ein Hinweis auf das Speicherlayout der Fabric-Credentials
        # (siehe Moduldocstring) - selbst ein scheiternder Aufruf soll ihn
        # nicht ins Log schreiben.
        if matter_data_dir is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Das matter-server-Datenverzeichnis ist fuer diesen Dienst nicht "
                    "eingehaengt - eine Sicherung kann deshalb nicht erstellt werden. "
                    "Siehe die Bereitstellung (docker-compose.yml, --matter-data-dir)."
                ),
            )
        if not matter_data_dir.is_dir():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Das konfigurierte matter-server-Datenverzeichnis existiert nicht "
                    "oder ist kein Verzeichnis. Pruefen Sie die Einhaengung."
                ),
            )

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(matter_data_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(matter_data_dir)))

        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{FABRIC_BACKUP_NAME}"'},
        )

    return router
