"""Nimmt die HTTP-Aufrufe der virtuellen Ausgaenge entgegen.

Der Miniserver wertet die Antwort eines virtuellen Ausgangs nicht aus - er
schickt und vergisst. Die Statuscodes hier sind also nicht fuer Loxone da,
sondern fuer den Menschen, der im Log nachsieht, warum ein Baustein nichts
bewirkt. Entsprechend muessen sie unterscheidbar sein: 404 fuer einen
unbekannten Schluessel, 400 fuer einen unpassenden Wert, 502 fuer ein Geraet,
das nicht antwortet.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException

from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.loxone.runtime import Runtime
from loxmatter.model.store import Store

Invoker = Callable[[MatterCall], Awaitable[None]]

logger = logging.getLogger(__name__)


def build_app(store: Store, invoke: Invoker, runtime: Runtime) -> FastAPI:
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/resync")
    async def resync() -> dict[str, int]:
        """Spec 6.4: haengt im Config-Projekt am Systemstart-Baustein."""
        return {"gesendet": await runtime.resend_all()}

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
