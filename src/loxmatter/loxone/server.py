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
  faehrt nur als Vorsichtsmassnahme gegen ein kuenftiges, als
  Query-Parameter uebergebenes Token (Task 8) mit.

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
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response as StarletteResponse

from loxmatter.api.control import build_control_router
from loxmatter.api.devices import build_device_router
from loxmatter.api.diagnostics import (
    CommandLogEntry,
    RingBuffer,
    build_diagnostics_router,
)
from loxmatter.api.export import build_export_router
from loxmatter.api.live import build_live_router
from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.loxone.runtime import Runtime
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


def build_app(
    store: Store,
    invoke: Invoker,
    runtime: Runtime,
    client: BridgeMatterClient | None = None,
    sender: UdpSender | None = None,
    matter_data_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)
    command_log: RingBuffer[CommandLogEntry] = RingBuffer(maxlen=COMMAND_LOG_SIZE)

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

    app.include_router(build_device_router(store, client, runtime))
    app.include_router(build_export_router(store))
    app.include_router(build_live_router(runtime))
    # Derselbe `invoke` wie unten bei `/cmd/{key}/{value}` - siehe
    # api/control.py Moduldocstring: eine Uebersetzung, zwei Aufrufer, sonst
    # driften sie (Spec 4.2, test_the_same_translation_as_the_loxone_endpoint).
    app.include_router(build_control_router(store, invoke))
    app.include_router(
        build_diagnostics_router(store, command_log, client, sender, matter_data_dir)
    )

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
                status_code=502, detail=f"Full-Resend fehlgeschlagen: {exc}"
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
            raise HTTPException(status_code=502, detail=f"Geraet nicht erreichbar: {exc}") from exc

        return {"status": "ok", "key": key}

    return app
