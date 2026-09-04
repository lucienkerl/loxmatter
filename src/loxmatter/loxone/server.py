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

"""Nimmt die HTTP-Aufrufe der virtuellen Ausgaenge entgegen - und, seit Task 2
(Phase 5), auch die der WebUI.

Der Miniserver wertet die Antwort eines virtuellen Ausgangs nicht aus - er
schickt und vergisst. Die Statuscodes der Loxone-Routen unten sind also nicht
fuer Loxone da, sondern fuer den Menschen, der im Log nachsieht, warum ein
Baustein nichts bewirkt. Entsprechend muessen sie unterscheidbar sein: 404
fuer einen unbekannten Schluessel, 400 fuer einen unpassenden Wert, 502 fuer
ein Geraet, das nicht antwortet.

`client` ist neu gegenueber Phase 4: die WebUI-Routen unter `/api` brauchen
`BridgeMatterClient` zum Einlernen und Entfernen von Geraeten (Task 1), die
Loxone-Routen hier brauchen ihn nicht. Der Parameter ist deshalb optional
und defaultet auf `None` - genau dafuer, dass die drei bestehenden
Phase-4-Aufrufe von `build_app(store, invoke, runtime)` unveraendert
weiterlaufen. `None` bedeutet nicht "WebUI fehlt"; es bedeutet "die Bruecke
laeuft ohne Matter-Verbindung" - `build_device_router` beantwortet die beiden
Routen, die `client` brauchen (Einlernen, Entfernen), dann mit 503 statt mit
einer `AttributeError` auf `None` (siehe dort).

`sender` und `matter_data_dir` sind neu in Task 6 (Diagnose, Spec 10.5),
aus demselben Grund optional mit Default `None`: die Diagnose-Routen
brauchen sie (Mitschnitt gesendeter Datagramme, Sicherung der Fabric-
Credentials), die uebrigen Routen dieser Datei nicht. `cli.py`s `_run`
reicht beide inzwischen durch; jeder aeltere Aufruf ohne sie laeuft
unveraendert weiter, nur eben ohne diese beiden Diagnose-Faehigkeiten
(siehe `api.diagnostics.build_diagnostics_router`, dort auch, was `None`
fuer jeden der beiden Faelle konkret bedeutet).

**`log_handler` ist neu in Task 4 dieser Phase (Diagnose-Livestream,
Spec 10.5).** Aus demselben Grund optional mit Default `None`: nicht jeder
Aufrufer hat `diagnostics.logbuffer.install_log_buffer()` bereits
aufgerufen. `cli.py`s `run()` tut das inzwischen (Task 5, Phase 5; seit
Nachbesserung Task 7, Fix 1 als dessen allererste Anweisung, VOR `_run()`)
und reicht den entstandenen Handler als Parameter an `_run()` durch, das ihn
unveraendert hier bei `build_app()` einreicht - ein Aufrufer, der `build_app`
direkt nutzt (z. B. ein Test), bekommt weiterhin `None`, solange er nicht
selbst `install_log_buffer()` aufruft und durchreicht. `None` bedeutet hier "kein
Log-Zweig im Livestream", nicht "der Livestream insgesamt fehlt" - die
WebSocket-Route `/api/diagnostics/live` (unten, `build_diagnostics_live_router`)
antwortet trotzdem, nur ohne Logzeilen darin (siehe dort).

**`api_token` ist neu in Task 8 (Absicherung, Spec 9).** Bis hierher bot
dieser Dienst nur `/cmd` und `/resync` - erreichbar zu sein bedeutete
hoechstens, ein Geraet schalten zu koennen. Seit Task 1 (Einlernen) und
Task 2 (Entfernen) bedeutet es mehr: wer den Port erreicht, kann ein Geraet
aus der Fabric werfen, und seit Task 6 kann er zusaetzlich die komplette
Fabric-Sicherung herunterladen (`GET /api/diagnostics/fabric-backup`,
Spec 4.1). `build_api_guard` (siehe dort) schuetzt deshalb ab hier jede
Route unter `/api` - lesend UND schreibend, denn eine reine
Schreibsperre haette den Lesezugriff auf Signalwerte und die
Fabric-Sicherung selbst offen gelassen, und genau die ist das eigentliche
Risiko. `/cmd` und `/resync` bleiben bewusst aussen vor: der Miniserver
ruft virtuelle Ausgaenge ohne Header auf, ein Token dort wuerde die
Loxone-Integration schlicht abschalten. `api_token` ist deshalb optional
mit Default `None` - derselbe Grund wie bei `client`/`sender` oben: jeder
bisherige Aufruf ohne das Argument laeuft unveraendert weiter, nur eben
ohne den Token-Weg in den Waechter. Seit dem WebUI-Login (docs/superpowers/
specs/2026-09-03-webui-login-design.md, Abschnitt 4) gibt es zwei Nachweise
statt einem, und keinen offenen Zustand mehr: `None` heisst
seither NICHT mehr "diese `/api`-Route ist unbewacht", sondern nur noch
"kein Bearer-Token akzeptiert" - die angemeldete Sitzung (Passwort, Cookie
`loxmatter_session`) bleibt der Weg des Browsers und deckt den Normalbetrieb
ab; ist gar kein Passwort vergeben, antwortet die Route trotzdem mit 401,
nicht offen (siehe `build_api_guard`, dort ausfuehrlich). Die Warnung im Log
gilt seither dem fehlenden Passwort, nicht mehr dem fehlenden Token (siehe
`cli._warn_if_no_password`).

**Kommando-Log (Spec 10.5).** Die Middleware `_record_command` unten
zeichnet JEDEN eingehenden HTTP-Aufruf auf dieser App auf - Methode, Pfad,
Statuscode, Zeitstempel - fuer `GET /api/diagnostics/commands`. Zwei
bewusste Einschraenkungen, beide in `api.diagnostics`s Moduldocstring
ausfuehrlicher begruendet:

- Aufrufe unter `/api/diagnostics/*` selbst werden NICHT mitgeschnitten -
  sonst wuerde ein offen gelassener, pollender Diagnose-Tab den knappen
  Ringpuffer mit sich selbst fluten statt mit den eigentlich interessanten
  `/cmd`-Aufrufen.
- Es wird ausschliesslich `request.url.path` aufgezeichnet, NIE die
  Query-Zeichenkette - ein `/cmd/{key}/{value}`-Aufruf legt seinen Wert
  absichtlich im Pfad ab (das ist der Zweck dieses Logs), eine
  Query-Zeichenkette dagegen ist fuer keine heutige Route vorgesehen und
  faehrt nur als Vorsichtsmassnahme mit: Task 8s Token laeuft ausdruecklich
  NICHT als Query-Parameter, sondern als `Authorization`-Header bzw. (fuer
  den Browser-WebSocket) als Subprotokoll - genau damit es nicht in diesem
  Log landet (siehe `build_api_guard`).

Die Middleware haengt ein eigenes try/except um das Anhaengen an den
Ringpuffer selbst (`_append_command_log`) - ein Fehler beim Mitschreiben
darf niemals die eigentliche Antwort verhindern, dieselbe Regel wie beim
Datagramm-Mitschnitt in `loxone.sender.UdpSender._record_sent`. Das ist
etwas anderes als der Aufruf von `call_next`: der liegt seit Review-Fix
Important (2026-09-02) SEHR WOHL in einem try/except, denn eine
unbehandelte Ausnahme aus einer Route soll trotzdem im Kommando-Log
erscheinen - vermerkt mit `_CRASHED_STATUS` -, bevor sie unveraendert
weitergereicht wird (siehe `_record_command`). Vorher verliess eine
abstuerzende Route `call_next`, ohne dass das try/except je erreicht
wurde - der Aufruf, der den Dienst zu Fall bringt, fehlte deshalb genau
dort, wo ein Diagnostiker ihn am dringendsten braucht."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection
from starlette.responses import Response as StarletteResponse

from loxmatter import i18n
from loxmatter.api.auth import build_auth_router
from loxmatter.api.control import build_control_router
from loxmatter.api.devices import RuntimeValues, ThreadDatasetSource, build_device_router
from loxmatter.api.diagnostics import (
    CommandLogEntry,
    RingBuffer,
    build_diagnostics_router,
)
from loxmatter.api.diagnostics_live import build_diagnostics_live_router
from loxmatter.api.export import build_export_router
from loxmatter.api.language import build_i18n_router, build_language_router
from loxmatter.api.live import BEARER_SUBPROTOCOL, ObservableRuntime, build_live_router
from loxmatter.api.project_sync import build_project_sync_router
from loxmatter.api.settings import build_settings_router
from loxmatter.auth.sessions import SESSION_COOKIE, session_is_valid
from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.diagnostics.logbuffer import LogBufferHandler
from loxmatter.loxone.sender import UdpSender
from loxmatter.matter.client import BridgeMatterClient
from loxmatter.model.store import Store
from loxmatter.timestamps import now_iso

Invoker = Callable[[MatterCall], Awaitable[None]]

logger = logging.getLogger(__name__)

COMMAND_LOG_SIZE = 500

# Aufrufe unter diesem Praefix werden nicht in den Kommando-Log
# aufgenommen - siehe Moduldocstring.
_DIAGNOSTICS_PREFIX = "/api/diagnostics"

# Kein echter HTTP-Statuscode (die liegen alle zwischen 100 und 599) -
# markiert einen Kommando-Log-Eintrag, bei dem die Route selbst abgestuerzt
# ist (eine unbehandelte Ausnahme aus `call_next`), statt tatsaechlich mit
# diesem Code geantwortet zu haben. Unterscheidbar von jedem echten
# Statuscode, siehe `_record_command` (Review-Fix Important, 2026-09-02).
_CRASHED_STATUS = 0

# Task 7, Phase 5: die Oberflaeche liegt als statisches Verzeichnis neben
# diesem Modul, nicht in einem eigenen Paket - `src/loxmatter/web/`, eine
# Ebene ueber `loxone/` (daher `.parents[1]`). Kein Build-Schritt, kein
# Bundler: `index.html`, `app.js`, `style.css` und das vendorte Alpine.js
# unter `web/vendor/` werden unveraendert ausgeliefert (siehe dort, warum
# Alpine vendort statt von einem CDN eingebunden ist - `web/index.html`s
# Kopfkommentar).
_WEB_DIR = Path(__file__).parents[1] / "web"


def normalize_api_token(token: str | None) -> str | None:
    """Die EINE Stelle, an der entschieden wird, ob ein Token gesetzt ist
    (Review-Fix Fix 2, 2026-09-03).

    `build_api_guard` fragt hier - bis Task 8 fragte auch die Startwarnung
    hier (damals `cli._warn_if_missing_api_token`); seit sie sich um das
    Passwort statt um das Token dreht, fragt sie ausschliesslich den Store
    und heisst entsprechend `cli._warn_if_no_password`. Der urspruenglich
    gemeldete Fehler bleibt trotzdem der Grund fuer diese Funktion: ein
    Token, das nur aus Leerraum besteht (ein abgeschnittener Zeilenumbruch
    aus einer kopierten `.env`, `--api-token " "`), galt dem Waechter als
    echtes Geheimnis, war aber ueber einen HTTP-Header gar nicht korrekt
    sendbar - HTTP-Headerwerte enthalten keine Zeilenumbrueche, und
    fuehrender/abschliessender
    Leerraum wird beim Parsen ohnehin verworfen (RFC 9110). Der Token-Pfad
    war damit dauerhaft unbenutzbar, ohne dass irgendetwas darauf
    hingewiesen haette, weil das Token ja "nicht None" war.

    Deshalb zwei Schritte, beide hier und nirgends sonst:

    - Aeusserer Leerraum wird abgeschnitten. Ein `LOXMATTER_API_TOKEN` mit
      angehaengtem Zeilenumbruch soll das Geheimnis sein, das ohne den
      Zeilenumbruch dasteht - alles andere waere ein Geheimnis, das niemand
      je uebertragen kann.
    - Bleibt nichts uebrig, ist das Ergebnis `None` - also exakt derselbe
      Fall wie "kein Token gesetzt", mit denselben Folgen: der Token-Pfad
      im Waechter (`build_api_guard`) bleibt verschlossen, und die
      Fabric-Sicherung bleibt gesperrt (siehe `api.diagnostics`) - beides
      unabhaengig davon, ob daneben noch eine Sitzung greift."""
    if token is None:
        return None
    stripped = token.strip()
    return stripped or None


def _token_from_authorization(header: str | None) -> str | None:
    """Zieht das Token aus `Authorization: Bearer <Token>` - der Hauptweg."""
    if header is None:
        return None
    prefix = "Bearer "
    if not header.startswith(prefix):
        return None
    return header[len(prefix) :]


def _token_from_websocket_subprotocol(header: str | None) -> str | None:
    """Zieht das Token aus `Sec-WebSocket-Protocol: bearer, <Token>` - der
    Ausnahmeweg fuer den Browser-WebSocket (siehe `build_api_guard`).

    Akzeptiert ausschliesslich genau zwei Werte, deren erster
    `api.live.BEARER_SUBPROTOCOL` ist - dieselbe Konstante, die `api.live`
    im Accept zurueckgibt, damit Lese- und Antwortseite nicht
    auseinanderlaufen koennen. Alles andere - ein einzelner Wert, drei
    Werte, ein anderer Marker - ergibt `None` und damit eine Ablehnung:
    diese Form ist die einzige, die `app.js` sendet, und eine grosszuegigere
    Auslegung wuerde nur zusaetzliche, ungetestete Wege in den Waechter
    lassen."""
    if header is None:
        return None
    values = [value.strip() for value in header.split(",")]
    if len(values) != 2 or values[0] != BEARER_SUBPROTOCOL:
        return None
    return values[1] or None


def _tokens_match(presented: str, expected: str) -> bool:
    """Zeitkonstanter Vergleich (Review-Fix Fix 2, 2026-09-03).

    Vergleicht die UTF-8-Bytes, nicht die `str`-Objekte: `compare_digest`
    wirft bei `str`-Argumenten `TypeError`, sobald auch nur eines davon ein
    Zeichen ausserhalb von ASCII enthaelt. Ein Angreifer koennte damit sonst
    mit einem einzigen Umlaut im Header einen 500er statt eines 401 ausloesen -
    ein selbst gebautes Orakel ("hier laeuft ein Vergleich") und ein
    unnoetiger Traceback im Log. Auf `bytes` kennt `compare_digest` diese
    Einschraenkung nicht und vergleicht jede Byte-Folge zeitkonstant, also
    faellt der Sonderfall ersatzlos weg, statt mit einem try/except behandelt
    zu werden."""
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def build_api_guard(token: str | None, store: Store) -> Callable[..., Awaitable[None]]:
    """Schuetzt die `/api`-Routen, nicht die des Miniservers (Task 8, Phase 5).

    Der Miniserver ruft virtuelle Ausgaenge ohne Header auf - er kann kein
    Token mitschicken. `/cmd` und `/resync` muessen deshalb offen bleiben, und
    das ist eine bewusste Grenze, keine Nachlaessigkeit: wer den Port
    erreicht, kann weiterhin Geraete schalten. Was das Token verhindert,
    ist das Einlernen, das Entfernen und der Download der
    Fabric-Sicherung - also alles, was den Bestand veraendert (Spec 9).

    **`Authorization: Bearer <Token>` ist der Hauptweg** und wird immer
    zuerst gelesen. Zusaetzlich wird das Token aus dem Handshake-Header
    `Sec-WebSocket-Protocol` akzeptiert, in der Form `bearer, <Token>`
    (siehe `_token_from_websocket_subprotocol`). Gedacht ist dieser zweite
    Weg allein fuer den Browser-WebSocket; technisch liegt er, weil dieser
    eine Wächter alle `/api`-Router bedient, auch auf den gewoehnlichen
    HTTP-Routen. Das ist harmlos und bewusst nicht ausgeschlossen: `Sec-`
    ist ein verbotener Kopfzeilenname, kein Browser-Skript kann ihn setzen,
    und ein zweiter, nach Router getrennter Wächter waere genau die Art
    Verdopplung, aus der spaeter ein ungeschuetzter Router entsteht
    (Review-Fix Minor #2, 2026-09-03). Grund fuer den Weg ueberhaupt:
    die Browser-`WebSocket`-API kennt keinen Parameter fuer eigene Header,
    `Authorization` ist dort schlicht unmoeglich. Der einzige vom Browser
    beeinflussbare Kanal im Handshake ist das Subprotokoll-Argument
    (`new WebSocket(url, ["bearer", token])`). Das ist einem Query-Parameter
    vorzuziehen, weil ein Query-Parameter in Server-Logs, Proxy-Logs und der
    Browser-History landet, ein Header nicht (dieselbe Ueberlegung, aus der
    `api.diagnostics` die Query-Zeichenkette bewusst NICHT ins Kommando-Log
    schreibt). Folge fuer das Token selbst: es muss als HTTP-Token
    uebertragbar sein - keine Leerzeichen, kein Komma, ASCII. Das von
    `.env.example` und README empfohlene `openssl rand -hex 32` liefert nur
    `[0-9a-f]` und erfuellt das von sich aus.

    Die WebSocket-Routen `/api/live` UND `/api/diagnostics/live` muessen das
    gewaehlte Subprotokoll im Accept zurueckgeben, sonst bricht der Browser
    den Handshake nach RFC 6455 ab - das erledigt fuer beide dieselbe
    Funktion `api.streaming.accepted_subprotocol` (siehe dort; zurueckgegeben
    wird `"bearer"`, niemals das Token).

    **Es gibt keinen offenen Zustand mehr.** Bis hierher liess ein Dienst
    ohne konfiguriertes Token jede `/api`-Route durch und begnuegte sich mit
    einer Warnung im Log - wer die Warnung ueberlas, betrieb eine offene
    Bruecke, ohne es zu merken. Seit dem WebUI-Login gilt: ohne gueltiges
    Cookie und ohne gueltiges Token endet jede Anfrage hier mit 401, auch
    wenn weder Passwort noch Token eingerichtet sind. Der Weg hinein ist
    dann ausschliesslich die Ersteinrichtung unter `/auth/setup`, die
    ausserhalb dieses Waechters haengt (siehe `api/auth.py`).

    Gilt fuer HTTP-Routen UND fuer die WebSocket-Routen `/api/live` und
    `/api/diagnostics/live` gleichermassen:
    `app.include_router(..., dependencies=[Depends(guard)])` loest diese
    Abhaengigkeit vor JEDER Route des jeweiligen Routers auf, auch vor einem
    WebSocket-Handshake - FastAPI/uvicorn lehnen eine `HTTPException` aus
    einer WebSocket-Abhaengigkeit ueber die ASGI-„Denial Response"-Erweiterung
    ab (HTTP-Statuscode vor dem Accept), statt die Verbindung erst anzunehmen
    und dann zu schliessen (verifiziert in `tests/api/test_security.py`).

    **Seit dem WebUI-Login gibt es zwei Nachweise statt einem.** Zuerst das
    Sitzungs-Cookie (`loxmatter_session`, siehe `auth.sessions`), dann das
    Bearer-Token. Das Cookie ist der Weg des Browsers, das Token der von
    Skripten und `curl` - deshalb wird das Cookie zuerst geprueft: es ist
    der haeufigere Fall, und es kostet einen SELECT statt eines
    Hash-Vergleichs.

    `HTTPConnection` statt `Request`: es ist der gemeinsame Basistyp von
    `Request` und `WebSocket`, und dieselbe Abhaengigkeit haengt an beiden
    Sorten von Routen - `/api/live` und `/api/diagnostics/live` sind
    WebSocket-Routen, in denen ein `Request`-Parameter gar nicht aufloesbar
    waere. Das Cookie reist beim
    WebSocket-Handshake von selbst mit (gleicher Ursprung), weshalb der
    Browser dort seit dem Login kein Subprotokoll mehr braucht."""
    # Einmal beim Bauen normalisiert, nicht bei jeder Anfrage: `None` und ein
    # reines Leerraum-Token sind derselbe Fall - siehe `normalize_api_token`.
    expected = normalize_api_token(token)

    async def guard(
        conn: HTTPConnection,
        authorization: str | None = Header(default=None),
        sec_websocket_protocol: str | None = Header(default=None),
    ) -> None:
        session_id = conn.cookies.get(SESSION_COOKIE)
        if session_id is not None and session_is_valid(store.auth, session_id):
            return
        if expected is not None:
            presented = _token_from_authorization(authorization)
            if presented is None:
                presented = _token_from_websocket_subprotocol(sec_websocket_protocol)
            if presented is not None and _tokens_match(presented, expected):
                return
        raise HTTPException(
            status_code=401,
            detail=i18n.t("api.server.fail_login_required"),
        )

    return guard


class _RuntimeDependency(RuntimeValues, ObservableRuntime, Protocol):
    """Was `build_app` selbst von `runtime` braucht - zusaetzlich zu den
    schmaleren Protokollen der einzelnen Router (`RuntimeValues` fuer
    `build_device_router`, `ObservableRuntime` fuer `build_live_router`):
    `resend_all` fuer `/resync` weiter unten (Spec 6.4). `loxone.runtime.
    Runtime` erfuellt das bereits unveraendert; ein Double (siehe
    `scripts/dev_web_server.py`, `_SeededRuntime`) muss dafuer keine echte
    `Runtime` mehr aufbauen."""

    async def resend_all(self) -> int: ...


def build_app(
    store: Store,
    invoke: Invoker,
    runtime: _RuntimeDependency,
    client: BridgeMatterClient | None = None,
    sender: UdpSender | None = None,
    matter_data_dir: Path | None = None,
    api_token: str | None = None,
    log_handler: LogBufferHandler | None = None,
    thread_dataset_source: ThreadDatasetSource | None = None,
) -> FastAPI:
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)
    command_log: RingBuffer[CommandLogEntry] = RingBuffer(maxlen=COMMAND_LOG_SIZE)
    api_guard = [Depends(build_api_guard(api_token, store))]

    def _append_command_log(*, method: str, path: str, status: int) -> None:
        """Haengt einen Eintrag an - in ein eigenes try/except gekapselt, ein
        Fehler beim Mitschreiben selbst darf weder die Antwort noch (im
        Absturz-Zweig unten) die weitergereichte Ausnahme verhindern."""
        try:
            command_log.append(
                CommandLogEntry(method=method, path=path, status=status, timestamp=now_iso())
            )
        except Exception:
            logger.exception(
                "Kommando-Mitschnitt fuer %s %s fehlgeschlagen - Antwort wird trotzdem "
                "ausgeliefert",
                method,
                path,
            )

    @app.middleware("http")
    async def _record_command(
        request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        """Zeichnet JEDEN Aufruf ausserhalb von `/api/diagnostics/*` auf -
        auch den, der die Route selbst zum Absturz bringt (Review-Fix
        Important, 2026-09-02).

        `call_next` lag bislang UNGESCHUETZT in dieser Funktion: eine
        unbehandelte Ausnahme aus einer Route (kein `HTTPException`, ein
        echter Programmfehler) verliess `call_next`, BEVOR das
        try/except unten je erreicht wurde - genau der Aufruf, der den
        Dienst zu Fall bringt, tauchte deshalb nie in `GET
        /api/diagnostics/commands` auf. Das `try` hier faengt deshalb den
        Absturz selbst ab, vermerkt ihn mit `_CRASHED_STATUS` (kein echter
        Statuscode, siehe dort) und wirft die Ausnahme dann UNVERAENDERT
        erneut (`raise` ohne Argument haelt den urspruenglichen Traceback) -
        Middleware darf eine Ausnahme nicht schlucken, Starlettes eigene
        Fehlerbehandlung (`ServerErrorMiddleware`) muss sie weiterhin sehen,
        um z. B. die 500-Antwort zu erzeugen. Das Mitschreiben selbst kostet
        dabei nichts zusaetzlich."""
        record = not request.url.path.startswith(_DIAGNOSTICS_PREFIX)
        try:
            response = await call_next(request)
        except Exception:
            if record:
                _append_command_log(
                    method=request.method,
                    path=request.url.path,
                    status=_CRASHED_STATUS,
                )
            raise
        if record:
            _append_command_log(
                method=request.method,
                path=request.url.path,
                status=response.status_code,
            )
        return response

    @app.middleware("http")
    async def _sync_language(
        request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        """Liest die gespeicherte Spracheinstellung bei JEDER Anfrage frisch
        (Spec-Abschnitt 4) - registriert als ALLERLETZTE Middleware, damit sie
        vor `_record_command` und vor jeder Route (einschliesslich des
        Anmelde-Waechters, dessen 401-Text ebenfalls uebersetzt ist) laeuft.
        Middleware-Registrierungsreihenfolge in Starlette (per
        `@app.middleware("http")`, aequivalent zu `add_middleware`): Starlette
        fuegt jede neu registrierte Schicht VORNE in `app.user_middleware` ein
        (`insert(0, ...)`) und baut den tatsaechlichen Stack anschliessend aus
        `reversed(user_middleware)` auf - die ZULETZT registrierte Funktion
        landet dadurch auf Index 0 und wird zur AEUSSEREN Schicht, sieht eine
        Anfrage also zuerst (durch `Middleware.__call__` von aussen nach innen
        durchgereicht). Verifiziert per `TestClient`-Probe (zwei Middlewares,
        Aufrufreihenfolge geloggt): die zuletzt per `@app.middleware("http")`
        dekorierte Funktion lief zuerst. Ein Test unten
        (`test_sync_language_is_the_outermost_middleware` in
        `tests/loxone/test_server.py`) haelt genau diese Reihenfolge fest -
        siehe auch die korrigierte Herleitung im Implementierungsplan dieser
        Aufgabe, Abschnitt "Middleware-Registrierungsreihenfolge".

        `store.locale.get_language()` wirft nie (Phase A) - kein try/except
        noetig, anders als `_append_command_log` weiter oben, das einen
        echten Fehlschlag beim Schreiben in einen fremden Ringpuffer
        abfaengt.

        Ausnahme: ist `LOXMATTER_LANG` gesetzt (CLI-Override, siehe
        `cli.py`), ueberschreibt diese Middleware die Prozess-Sprache NICHT
        aus dem Store - sonst wuerde der allererste eingehende Request den
        vom CLI-Bootstrap gesetzten Override sofort wieder verwerfen (Review-
        Fix Important, 2026-09-04)."""
        if os.environ.get("LOXMATTER_LANG") is None:
            i18n.set_language(store.locale.get_language())
        return await call_next(request)

    # `dependencies=api_guard` auf jedem der neun `/api`-Router (Task 8,
    # Phase 5, siehe `build_api_guard` oben; achter seit `POST
    # /api/export/project-sync`, Task 11, Phase 6, neunter seit
    # `build_language_router`, dieser Aufgabe): das schuetzt ausnahmslos
    # jede Route dieser neun Router, inklusive der WebSocket-Routen
    # `/api/live` und `/api/diagnostics/live` - und ausdruecklich NICHT
    # `/cmd`, `/resync`, `/health`, `/` und `/static`, die weiter unten ohne
    # `dependencies` eingehaengt werden.
    app.include_router(
        build_device_router(store, client, runtime, thread_dataset_source),
        dependencies=api_guard,
    )
    app.include_router(build_export_router(store), dependencies=api_guard)
    app.include_router(build_project_sync_router(store), dependencies=api_guard)
    app.include_router(build_settings_router(store), dependencies=api_guard)
    app.include_router(build_language_router(store), dependencies=api_guard)
    app.include_router(build_live_router(runtime), dependencies=api_guard)
    # Derselbe `invoke` wie unten bei `/cmd/{key}/{value}` - siehe
    # api/control.py Moduldocstring: eine Uebersetzung, zwei Aufrufer, sonst
    # driften sie (Spec 4.2, test_the_same_translation_as_the_loxone_endpoint).
    app.include_router(build_control_router(store, invoke), dependencies=api_guard)
    app.include_router(
        build_diagnostics_router(
            store,
            command_log,
            client,
            sender,
            matter_data_dir,
        ),
        dependencies=api_guard,
    )
    # Task 4 dieser Phase: der laufende Diagnose-Livestream neben den drei
    # einmalig abrufbaren Diagnose-Routen oben - siehe
    # `api.diagnostics_live`-Moduldocstring fuer die Begruendung, warum das
    # ein EIGENER Router ist statt einer weiteren Route auf
    # `build_diagnostics_router` (dieselbe `/api/diagnostics`-Vorsilbe,
    # zwei Router: FastAPI fasst gleiche Praefixe klaglos zusammen).
    app.include_router(
        build_diagnostics_live_router(sender, command_log, log_handler),
        dependencies=api_guard,
    )

    # OHNE `dependencies=api_guard` - genau wie `/health`, `/cmd` und
    # `/resync` weiter unten. Wer sich noch nicht angemeldet hat, muss diese
    # vier Routen erreichen koennen, sonst gibt es keinen Weg hinein
    # (siehe api/auth.py, Moduldocstring).
    app.include_router(build_auth_router(store))
    # Siehe api/language.py-Moduldocstring: die Ersteinrichtungs-/
    # Anmeldeseite braucht diese Uebersetzungen, um sich ueberhaupt
    # anzuzeigen, bevor jemand angemeldet sein kann.
    app.include_router(build_i18n_router(store))

    # Task 7, Phase 5: die WebUI selbst. `StaticFiles` weist einen Zugriff,
    # der ueber `_WEB_DIR` hinaus will (z. B. `/static/../../../etc/passwd`),
    # bereits selbst mit 404 zurueck - ein eigener Schutz waere hier nur eine
    # zweite, driftende Kopie derselben Pruefung
    # (test_static_files_do_not_escape_their_directory).
    app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """Liefert die Oberflaeche aus. Kein Build-Schritt, keine CDN-Abhaengigkeit."""
        return FileResponse(_WEB_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/resync")
    async def resync() -> dict[str, int]:
        """Spec 6.4: haengt im Config-Projekt am Systemstart-Baustein."""
        try:
            count = await runtime.resend_all()
        except Exception as exc:  # z. B. UdpSender, dessen Socket schon zu ist
            # logger.exception schreibt den vollen Traceback ins Server-Log,
            # NICHT in die HTTP-Antwort - dieselbe Begruendung wie bei
            # /cmd oben: der Unterschied zwischen einem toten Sender und
            # einem Programmfehler in resend_all soll im Log erhalten
            # bleiben, auch wenn beides fuer den Aufrufer nur noch
            # "Full-Resend fehlgeschlagen: <Meldung>" ohne Traceback ist.
            logger.exception("Full-Resend ueber /resync fehlgeschlagen")
            raise HTTPException(
                status_code=502, detail=i18n.t("api.server.fail_resync", exc=exc)
            ) from exc
        # Englischer Schluessel im Wire-Format (Review-Fix M9, 2026-09-02):
        # Bezeichner in Antworten sind wie Code-Bezeichner Englisch, auch
        # wenn Prosa/Fehlermeldungen Deutsch bleiben - "gesendet" war hier
        # der einzige deutsche Schluessel in einer JSON-Antwort.
        return {"sent": count}

    @app.get("/cmd/{key}/{value}")
    async def command(key: str, value: str) -> dict[str, str]:
        try:
            stored = store.resolve_command(key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            call = to_matter_call(stored, value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            await invoke(call)
        except Exception as exc:  # jedes Geraeteproblem wird zu 502
            # logger.exception schreibt den vollen Traceback ins Server-Log,
            # NICHT in die HTTP-Antwort (siehe
            # test_a_failing_matter_call_yields_502_not_a_traceback). Ohne
            # das saehe ein echter Programmfehler im Invoker im Log genauso
            # aus wie ein Geraet, das gerade nicht antwortet - beides waere
            # nur noch "Geraet nicht erreichbar: <Meldung>" ohne Traceback,
            # und der Unterschied zwischen "Zigbee-Mesh weg" und "Tippfehler
            # im Invoker" ginge verloren.
            logger.exception("Matter-Aufruf fuer Schluessel %r fehlgeschlagen", key)
            raise HTTPException(
                status_code=502, detail=i18n.t("api.errors.device_unreachable", exc=exc)
            ) from exc

        return {"status": "ok", "key": key}

    return app
