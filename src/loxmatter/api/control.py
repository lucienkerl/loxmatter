"""Bedienung eines Geraets aus der Oberflaeche.

Das ist kein Komfortmerkmal (Spec 8.1). Schaltet eine Lampe ueber Loxone
nicht, trennt ein Klick hier die beiden moeglichen Ursachen: reagiert das
Geraet, liegt der Fehler in der Loxone-Verdrahtung oder im Export; reagiert
es nicht, in Matter, Thread oder am Geraet.

Die Uebersetzung kommt aus `commands.translate` - derselben, die der
Loxone-Endpunkt benutzt. Eine eigene Kopie hier wuerde driften, und dann
haette die Diagnose genau den Fehler, den sie finden soll (Spec 4.2). Die
Statuscodes fuer `POST /api/commands/{key}` folgen deshalb wortwoertlich dem
Loxone-Endpunkt `/cmd/{key}/{value}` aus Phase 4 (`loxone/server.py`): 404
unbekannter Schluessel, 400 unpassender Wert, 502 Geraet antwortet nicht.

**Befund zur Schreibbarkeit eines Attributs (Task 4, 2026-09-02).** Der
Snapshot (`NodeSnapshot.attributes`) traegt nur Werte, keine Zugriffsrechte -
"beschreibbar" steht dort nirgends. Geprueft gegen die installierten
Pakete, nicht geraten:

- `chip.clusters.ClusterObjects.ClusterAttributeDescriptor` (die Basisklasse
  jeder generierten Attribut-Klasse wie `BasicInformation.Attributes.
  NodeLabel`) traegt `cluster_id`, `attribute_id`, `attribute_type`,
  `must_use_timed_write` - keine Eigenschaft, die Lese- von Schreibzugriff
  unterscheidet. `must_use_timed_write` ist etwas anderes: ob ein *erlaubter*
  Schreibzugriff ein Timed-Write-Envelope braucht, nicht ob er erlaubt ist.
- `matter_server.client.client.MatterClient.write_attribute(node_id,
  attribute_path, value)` fragt vorher nichts ab - der Aufruf geht
  ungeprueft an den Controller; eine Ablehnung kaeme, wenn ueberhaupt, als
  Fehler vom echten Geraet zurueck, nicht von matter-server selbst.
- Eine Volltextsuche nach "writable"/"Writable" ueber beide installierten
  Pakete (`chip`, `matter_server`, python-matter-server==8.1.2) traf auf
  keinen einzigen Fund.

Damit ist belegt, nicht vermutet: **weder python-matter-server noch die
zugrunde liegende chip-SDK machen die Schreibbarkeit eines Attributs
zugaenglich.** Ein roher Schreibversuch gegen ein nur lesbares Attribut faellt
folglich nicht hier auf, sondern - wenn ueberhaupt - erst am Geraet, in einer
Form, die diese Bruecke nicht zuverlaessig von einem Verbindungsfehler
unterscheiden koennte. Genau das darf bei einem Diagnosewerkzeug nicht
passieren: ein Klick, der nichts bewirkt, muss als klare Absage ankommen,
nicht als stiller Fehlschlag irgendwo zwischen Bruecke und Geraet.

Also gilt hier dieselbe Asymmetrie wie bei Kommandos (Spec 6.7): eine
**Erlaubnisliste** statt der grosszuegigen Durchreiche, die fuer den
*Export* von Signalen gilt (Spec 3.5). `_WRITABLE_ATTRIBUTES` unten ist
bewusst klein und ausschliesslich mit Eintraegen belegt, die sich entweder
gegen ein echtes, in dieser Testsuite eingechecktes Geraet nachweisen lassen
(IKEA GRILLPLATS, `tests/fixtures/nodes/ikea_grillplats_plug.json`) oder,
klar so gekennzeichnet, ausschliesslich auf der Matter-Spezifikation
beruhen, ohne dass ein passendes Geraet zum Gegenpruefen vorlag - dieselbe
Zurueckhaltung wie bei `commands/color.py`. Ein zu Unrecht gesperrtes
Attribut kostet eine fehlende Bedienmoeglichkeit; ein zu Unrecht
freigegebenes kann ein Geraet fehlkonfigurieren.

**Offener Punkt, hier bewusst nicht geloest (siehe Spec, Abschnitt 12):**
selbst ein Attribut auf der Erlaubnisliste laesst sich mit dem heutigen
Stand nicht tatsaechlich schreiben - `BridgeMatterClient` (matter/client.py)
hat kein `write_attribute`, und die Schnittstelle dieses Moduls
(`build_control_router(store, invoke)`) nimmt dafuer auch keinen zweiten
Aufrufer entgegen; `invoke` ist ausschliesslich fuer Kommandos typisiert
(`Callable[[MatterCall], Awaitable[None]]`), und ein Attribut-Schreibzugriff
ist kein Kommando. `POST /api/signals/{key}/write` antwortet fuer ein
erlaubtes Attribut deshalb ehrlich mit 501 statt mit einem Erfolg, der
nichts bewirkt - dieselbe Haltung wie oben, nur eine Stufe weiter: eine
Antwort, die stillschweigend nichts tut, ist genau der Fehler, den dieses
Werkzeug aufdecken soll, nicht erzeugen.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException

from loxmatter.api.models import CommandOut, ValueIn
from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.model.store import Store, UnknownCommandError, UnknownDeviceError
from loxmatter.profiles.table import command_slug

Invoker = Callable[[MatterCall], Awaitable[None]]

logger = logging.getLogger(__name__)

# Siehe Moduldocstring "Befund zur Schreibbarkeit eines Attributs" fuer die
# Begruendung dieser Liste und warum sie eine Erlaubnisliste ist, keine
# Sperrliste. (Cluster-ID, Attribut-ID)-Paare.
_WRITABLE_ATTRIBUTES: frozenset[tuple[int, int]] = frozenset(
    {
        # BasicInformation (0/40) - belegt gegen die eingecheckte IKEA-
        # GRILLPLATS-Vorlage (0/40/5 = "", 0/40/6 = "XX", 0/40/16 = False):
        # alle drei Pfade sind dort tatsaechlich vorhanden, nicht nur laut
        # Spezifikation vermutet.
        (40, 5),  # NodeLabel
        (40, 6),  # Location
        (40, 16),  # LocalConfigDisabled
    }
)


def _is_writable(cluster_id: int, attribute_id: int) -> bool:
    return (cluster_id, attribute_id) in _WRITABLE_ATTRIBUTES


def build_control_router(store: Store, invoke: Invoker) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _require_device(device_id: int) -> None:
        try:
            store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/devices/{device_id}/controls")
    async def controls(device_id: int) -> list[CommandOut]:
        """Nur benannte Kommandos werden zu einem Bedienelement (Spec 6.7:
        Ausgangsbefehle stammen aus AcceptedCommandList, nicht aus
        Attributen).

        Der Rohexport (`loxmatter export --raw`, `export.commands.
        extract_commands(..., raw=True)`) kann Kommandos ohne Eintrag in
        `clusters.yaml` in den Store schreiben - ihr Slug ist dann ein
        generischer Platzhalter wie `c4_cmd0` (siehe dort). Ein Knopf mit
        dieser Beschriftung waere fuer die Bedienoberflaeche nutzlos: niemand
        weiss, was `c4_cmd0` bewirkt, ohne die Vorlage zu lesen - und ein
        Klick auf einen Knopf ohne erkennbare Bedeutung ist das Gegenteil
        von Spec 8.1s "ein Klick trennt die beiden moeglichen Ursachen".
        `command_slug` ist dieselbe Quelle, die auch `to_matter_call`
        letztlich bedient (ueber `commands.translate._PAYLOAD_BUILDERS`,
        gegen `clusters.yaml` synchron gehalten von
        `profiles.table.known_command_pairs` - siehe dort) - ein hier
        gefilterter Rohbefehl war ohnehin nie ausfuehrbar, sondern haette
        sofort mit 400 quittiert. Diese Route zeigt deshalb nur, was ein
        Klick tatsaechlich ausloesen kann.
        """
        _require_device(device_id)
        return [
            CommandOut(key=command.key, slug=command.slug, takes_value=command.takes_value)
            for command in store.commands(device_id)
            if command_slug(command.cluster_id, command.command_id) is not None
        ]

    @router.post("/commands/{key}")
    async def execute_command(key: str, body: ValueIn) -> dict[str, str]:
        try:
            stored = store.resolve_command(key)
        except UnknownCommandError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            call = to_matter_call(stored, body.value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            await invoke(call)
        except Exception as exc:  # jedes Geraeteproblem wird zu 502
            # logger.exception schreibt den vollen Traceback ins Server-Log,
            # NICHT in die HTTP-Antwort - dieselbe Begruendung wie beim
            # Loxone-Endpunkt in loxone/server.py.
            logger.exception("Matter-Aufruf fuer Schluessel %r fehlgeschlagen", key)
            raise HTTPException(status_code=502, detail=f"Geraet nicht erreichbar: {exc}") from exc

        return {"status": "ok", "key": key}

    @router.post("/signals/{key}/write")
    async def write_signal(key: str, body: ValueIn) -> dict[str, str]:
        """Setzt ein Attribut roh (Spec 8, Ansicht 2). Siehe Moduldocstring
        fuer den Befund zur Schreibbarkeit und den offenen Punkt, dass ein
        erlaubtes Attribut heute noch nicht tatsaechlich geschrieben wird."""
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"unbekannter Signal-Schluessel {key!r}")
        try:
            # Dieselbe Pruefung wie bei PATCH /api/signals/{key}
            # (api/devices.py, Review-Fix Important #4): ein Signal eines
            # entfernten Geraets bleibt ueber seinen Schluessel auffindbar,
            # soll aber nicht mehr bedienbar sein.
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Signal {key!r} gehoert zu Geraet {stored.device_id}, das entfernt wurde",
            ) from exc

        if not _is_writable(stored.ref.cluster_id, stored.ref.element_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Attribut {key!r} steht nicht auf der Erlaubnisliste beschreibbarer "
                    "Attribute - siehe Moduldocstring von api/control.py"
                ),
            )

        # Erlaubt, aber noch nicht verdrahtet - siehe Moduldocstring, Absatz
        # "Offener Punkt". Eine 200-Antwort waere hier schlimmer als dieser
        # ehrliche Fehler: sie taeuschte eine Wirkung vor, die nicht
        # eintritt, und genau das soll dieses Werkzeug sichtbar machen, nicht
        # verstecken.
        raise HTTPException(
            status_code=501,
            detail=(
                f"Attribut {key!r} ist beschreibbar, aber das rohe Schreiben ist noch nicht "
                "an matter-server angebunden - siehe Moduldocstring von api/control.py"
            ),
        )

    return router
