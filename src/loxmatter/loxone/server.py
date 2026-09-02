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
einer `AttributeError` auf `None` (siehe dort)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException

from loxmatter.api.control import build_control_router
from loxmatter.api.devices import build_device_router
from loxmatter.api.live import build_live_router
from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.loxone.runtime import Runtime
from loxmatter.matter.client import BridgeMatterClient
from loxmatter.model.store import Store

Invoker = Callable[[MatterCall], Awaitable[None]]

logger = logging.getLogger(__name__)


def build_app(
    store: Store,
    invoke: Invoker,
    runtime: Runtime,
    client: BridgeMatterClient | None = None,
) -> FastAPI:
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)
    app.include_router(build_device_router(store, client, runtime))
    app.include_router(build_live_router(runtime))
    # Derselbe `invoke` wie unten bei `/cmd/{key}/{value}` - siehe
    # api/control.py Moduldocstring: eine Uebersetzung, zwei Aufrufer, sonst
    # driften sie (Spec 4.2, test_the_same_translation_as_the_loxone_endpoint).
    app.include_router(build_control_router(store, invoke))

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
