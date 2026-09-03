"""Kommandozeile der Bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import typer
import uvicorn
from matter_server.client.exceptions import CannotConnect

from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from loxmatter.commands.translate import MatterCall
from loxmatter.devtools.fake_miniserver import FakeMiniserver
from loxmatter.export.commands import extract_commands
from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_system_templates,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.signals import to_inputs
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.sender import UdpSender
from loxmatter.loxone.server import build_app
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.discovery import (
    extract_signals,
    find_clusters_with_undiscoverable_events,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind
from loxmatter.model.store import Store
from loxmatter.profiles.table import is_exportable

logger = logging.getLogger(__name__)

app = typer.Typer(help="Matter → Loxone Bridge")


@app.callback()
def main() -> None:
    """Ohne diesen Callback macht Typer bei genau einem Kommando aus
    `loxmatter inspect ...` ein `loxmatter ...` — der Unterbefehl verschwindet."""


def render_report(snapshot: NodeSnapshot) -> str:
    lines = [
        f"Node {snapshot.node_id}: {snapshot.vendor_name} {snapshot.product_name}".rstrip(),
        f"Unique ID: {snapshot.unique_id or '—'}",
        "",
    ]

    signals = extract_signals(snapshot)
    attributes = [s for s in signals if s.kind is SignalKind.ATTRIBUTE]
    events = [s for s in signals if s.kind is SignalKind.EVENT]

    lines.append(f"Attribute ({len(attributes)}):")
    for ref in attributes:
        lines.append(f"  {ref.path:<16} = {snapshot.attributes.get(ref.path)!r}")

    lines.append("")
    lines.append(f"Events ({len(events)}):")
    for ref in events:
        lines.append(f"  {ref.path}")

    missing = find_unreported_attributes(snapshot)
    if missing:
        lines += [
            "",
            f"NICHT GELIEFERT ({len(missing)}) — vom Gerät gelistet, aber ohne Wert:",
        ]
        lines += [f"  {ref.path}" for ref in missing]

    broken = find_unparsable_paths(snapshot)
    if broken:
        lines += ["", f"NICHT LESBAR ({len(broken)}):"] + [f"  {p}" for p in broken]

    undiscoverable = find_clusters_with_undiscoverable_events(snapshot)
    if undiscoverable:
        header = (
            f"NICHT ABLEITBAR ({len(undiscoverable)}) \u2014 weder EventList noch "
            "FeatureMap-Tabelleneintrag, ob dieser Cluster Events hat, ist unbekannt:"
        )
        lines += ["", header]
        lines += [f"  {endpoint}/{cluster_id}" for endpoint, cluster_id in undiscoverable]

    return "\n".join(lines)


def _fail(message: str) -> NoReturn:
    """Meldet einen erwarteten CLI-Fehler: eine Zeile auf stderr, danach
    Programmende mit Exit-Code ≠ 0 — statt eines Tracebacks."""
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _ensure_out_dir(out: Path) -> None:
    """Legt das Zielverzeichnis an; meldet einen Fehlschlag als CLI-Fehler
    statt eines Tracebacks.

    `export` ruft dies an zwei Stellen auf — einmal vor den Systemvorlagen,
    einmal vor den Gerätevorlagen (`mkdir(exist_ok=True)` verträgt den
    zweiten Aufruf) — statt einmal ganz am Anfang. So entsteht das
    Verzeichnis erst, wenn feststeht, dass das Kommando tatsächlich etwas
    schreibt: ein Aufruf ohne `--system`, `--node` oder `--fixture` scheitert
    an der Parametervalidierung in `_load_snapshot`, bevor hier irgendetwas
    angelegt wird (Review-Fix Minor #3, 2026-09-02).
    """
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(
            f"Zielverzeichnis {out} konnte nicht angelegt werden: {exc}. Ist der Pfad beschreibbar?"
        )


def _load_fixture(path: Path) -> NodeSnapshot:
    """Lädt eine Fixture-Datei; meldet kaputten Inhalt als CLI-Fehler statt
    mit einem rohen KeyError/JSONDecodeError abzubrechen."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"Fixture {path} enthält kein gültiges JSON: {exc}")
    try:
        node_id = raw["node_id"]
    except (KeyError, TypeError):
        _fail(f"Fixture {path} hat kein Feld 'node_id'.")
    return NodeSnapshot.from_raw(node_id, raw)


def _build_client(url: str) -> BridgeMatterClient:
    """Eigener Konstruktions-Schritt, damit Tests den Client per Monkeypatch
    durch eine mit Fake-Factories bestückte Instanz ersetzen können — ohne
    Netzwerk zu berühren (siehe BridgeMatterClient.session_factory)."""
    return BridgeMatterClient(url)


def _load_snapshot(fixture: Path | None, node: int | None, url: str) -> NodeSnapshot:
    """Lädt ein Node-Abbild aus einer Datei oder von einem laufenden matter-server.

    Gemeinsam von `inspect` und `export` genutzt, damit die vier deutschen
    Fehlermeldungen dieses Pfads nur an einer Stelle stehen, statt in zwei
    Kommandos auseinanderzudriften.
    """
    if fixture is not None:
        return _load_fixture(fixture)
    if node is None:
        raise typer.BadParameter("entweder --node oder --fixture angeben")

    async def run() -> NodeSnapshot:
        client = _build_client(url)
        try:
            await client.connect()
        except CannotConnect:
            _fail(f"matter-server unter {url} nicht erreichbar — läuft der Dienst?")
        except MatterUnavailableError as exc:
            _fail(
                f"matter-server unter {url} hat sich verbunden, aber keine "
                f"Bereitschaft gemeldet: {exc}"
            )
        try:
            return await client.snapshot(node)
        except MatterUnavailableError:
            _fail(f"Node {node} ist am matter-server ({url}) nicht bekannt — kommissioniert?")
        finally:
            await client.disconnect()

    return asyncio.run(run())


def _resolve_store_path(explicit: Path | None) -> Path:
    """Ermittelt den Pfad der Signalschlüssel-Datenbank.

    Rangfolge: `--store-path` schlägt die Umgebungsvariable `LOXMATTER_STORE`,
    die wiederum den Standard `~/.loxmatter/loxmatter.sqlite` schlägt.

    Der Standard ist absichtlich vom Arbeitsverzeichnis unabhängig. Die
    Datenbank hält die Signalschlüssel — und die Schlüssel *sind* die
    Verdrahtung in Loxone (Spec 6.2): sobald ein Nutzer einen exportierten
    Eingang auf einen Funktionsbaustein gezogen hat, verbindet nur noch der
    Schlüsseltext den Baustein mit der Bridge. Läge der Standard relativ zum
    Arbeitsverzeichnis (z. B. `loxmatter.sqlite`), würde ein Export aus einem
    anderen Verzeichnis — heute `~/exports`, morgen der Desktop, oder ein
    Cron-Job mit eigenem Arbeitsverzeichnis — die vorhandene Datenbank
    verfehlen. Das Werkzeug hielte das Gerät dann für neu, vergäbe eine neue
    `device_id` und damit einen komplett neuen Satz Schlüssel. Der Nutzer
    importiert die neue Vorlage, und jeder bisher verdrahtete Baustein wird
    stillschweigend tot — ohne Fehlermeldung. NICHT wieder auf einen
    relativen Pfad vereinfachen.

    `LOXMATTER_STORE` erlaubt einen abweichenden, festen Ort — etwa ein
    eingehängtes Volume in einer Container-Bereitstellung.
    """
    if explicit is not None:
        return explicit
    override = os.environ.get("LOXMATTER_STORE")
    if override:
        return Path(override)
    return Path.home() / ".loxmatter" / "loxmatter.sqlite"


@app.command()
def inspect(
    node: int | None = typer.Option(None, help="Node-ID am laufenden matter-server"),
    fixture: Path | None = typer.Option(  # noqa: B008 — typer-Idiom, `Path` gilt Ruff nicht als unveränderlich
        None, help="Statt matter-server ein gespeichertes Abbild"
    ),
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
) -> None:
    """Listet alle Attribute und Events eines Geräts auf."""
    snapshot = _load_snapshot(fixture, node, url)
    typer.echo(render_report(snapshot))


@app.command()
def export(
    fixture: Path | None = typer.Option(  # noqa: B008
        None, help="Gespeichertes Abbild statt eines laufenden matter-server"
    ),
    node: int | None = typer.Option(None, help="Node-ID am laufenden matter-server"),
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
    bridge_ip: str = typer.Option(..., help="IP dieser Bridge, aus Sicht des Miniservers"),
    port: int = typer.Option(7000, help="UDP-Port, auf dem der Miniserver lauscht"),
    listen: int = typer.Option(
        8080,
        help="HTTP-Port in der erzeugten Kommando-URL (VO-Vorlage). Muss mit dem "
        "--listen übereinstimmen, mit dem `loxmatter run` später gestartet wird — "
        "sonst laufen die Ausgangsbefehle ins Leere, ohne dass der Miniserver das meldet.",
    ),
    out: Path = typer.Option(Path("."), help="Zielverzeichnis für die Vorlagen"),  # noqa: B008
    store_path: Path | None = typer.Option(  # noqa: B008
        None,
        help="Datenbank mit den Signalschlüsseln. Standard: "
        "~/.loxmatter/loxmatter.sqlite — bewusst unabhängig vom "
        "Arbeitsverzeichnis. Die Schlüssel darin sind die Verdrahtung in "
        "Loxone; ein relativer Pfad würde bei einem Aufruf aus einem anderen "
        "Verzeichnis die Datenbank verfehlen, dem Gerät eine neue device_id "
        "zuweisen und damit jede bestehende Verdrahtung stillschweigend "
        "zerstören. Alternative über die Umgebungsvariable LOXMATTER_STORE, "
        "etwa für ein eingehängtes Volume im Container.",
    ),
    raw_commands: bool = typer.Option(
        False,
        "--raw-commands",
        help="Auch Kommandos unbekannter Cluster exportieren. "
        "Verwaltungscluster bleiben in jedem Fall gesperrt.",
    ),
    system: bool = typer.Option(
        False,
        "--system",
        help="Erzeugt zusätzlich die geräteunabhängigen Vorlagen "
        "(bridge_alive, /resync). Einmalig zu importieren.",
    ),
) -> None:
    """Erzeugt die Loxone-Vorlagen für ein Gerät.

    Der Ort der Signalschlüssel-Datenbank entscheidet über die
    Schlüsselstabilität — siehe `_resolve_store_path` und die Hilfe zu
    `--store-path`. Der verwendete Pfad wird ausgegeben, damit ein Nutzer,
    der versehentlich zwei Datenbanken erzeugt hat, das an der Ausgabe sieht
    statt es aus toten Bausteinen in Loxone zu erschließen.

    `bridge_alive` und `/resync` gehören zu keinem Gerät (Spec 6.2, 6.4, 6.5)
    — deshalb prüft `--system` hier zuerst, vor dem Laden des Abbilds: sonst
    verlangte der Aufbau des Kommandos immer `--node` oder `--fixture`, auch
    wenn nur die Systemvorlagen gebraucht werden.
    """
    if system:
        _ensure_out_dir(out)
        viu_sys, vo_sys = render_system_templates(bridge_ip, port, listen)
        viu_sys_path = out / "VIU_Matter_System.xml"
        vo_sys_path = out / "VO_Matter_System.xml"
        try:
            viu_sys_path.write_bytes(viu_sys)
        except OSError as exc:
            _fail(
                f"{viu_sys_path} konnte nicht geschrieben werden: {exc}. "
                "Es wurde noch keine Datei angelegt."
            )
        try:
            vo_sys_path.write_bytes(vo_sys)
        except OSError as exc:
            _fail(
                f"{vo_sys_path} konnte nicht geschrieben werden: {exc}. "
                f"Geschrieben wurde bereits {viu_sys_path.name}, es fehlt {vo_sys_path.name}."
            )
        typer.echo("VIU_Matter_System.xml, VO_Matter_System.xml: Heartbeat und /resync")
        if fixture is None and node is None:
            return

    snapshot = _load_snapshot(fixture, node, url)

    resolved_store_path = _resolve_store_path(store_path)
    typer.echo(f"Datenbank: {resolved_store_path.resolve()}")
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(
            f"Verzeichnis {resolved_store_path.parent} konnte nicht angelegt werden: {exc}. "
            "Ist der Pfad beschreibbar?"
        )

    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(f"Datenbank {resolved_store_path} konnte nicht geöffnet werden: {exc}")
    try:
        device_id = store.register_device(snapshot)
        stored = store.register_signals(device_id, snapshot)
        # Ausgangsbefehle kommen aus AcceptedCommandList, nicht aus den Attributen:
        # Matter-Attribute sind fast alle nur lesbar (Task 6).
        stored_commands = store.register_commands(
            device_id, extract_commands(snapshot, raw=raw_commands), snapshot.node_id
        )
    finally:
        store.close()

    label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or f"Node {snapshot.node_id}"
    inputs = to_inputs(stored, device_id, label)
    # Der Schluessel kommt ausschliesslich vom Store (siehe register_commands):
    # so stammen der Schluessel in der Vorlage und der in der Datenbank aus
    # einer Quelle statt aus zwei unabhaengigen Zusammensetzungen, die
    # auseinanderdriften koennten, ohne dass ein Fehler es meldet.
    commands = [
        LoxoneCommand(
            key=c.key,
            title=c.slug,
            path=f"/cmd/{c.key}/" + ("<v>" if c.takes_value else "1"),
            analog=c.takes_value,
        )
        for c in stored_commands
    ]

    _ensure_out_dir(out)
    viu = out / filename_for("VIU", device_id, label)
    vo = out / filename_for("VO", device_id, label)

    try:
        viu.write_bytes(render_virtual_in_udp(label, bridge_ip, port, inputs))
    except OSError as exc:
        _fail(f"{viu} konnte nicht geschrieben werden: {exc}. Es wurde noch keine Datei angelegt.")
    try:
        vo.write_bytes(render_virtual_out(label, f"http://{bridge_ip}:{listen}", commands))
    except OSError as exc:
        _fail(
            f"{vo} konnte nicht geschrieben werden: {exc}. "
            f"Geschrieben wurde bereits {viu}, es fehlt {vo.name}."
        )

    # Text zaehlt mit: der virtuelle Texteingang ist ein eigener Vorlagentyp
    # und kommt in einer spaeteren Ausbaustufe (Spec 6.6). Die Entscheidung
    # faellt `profiles.table.is_exportable` und sonst niemand (Review-Fix
    # Fix 8, 2026-09-03) - vorher stand hier eine von Hand kopierte
    # Umkehrung `(Exportability.NONE, Exportability.TEXT)`, eine zweite in
    # `api/export.py`, und beide neben genau dem Helfer, der diese
    # Verdopplung schon einmal beenden sollte.
    skipped = sum(1 for s in stored if not is_exportable(s.exportability))
    typer.echo(f"{viu.name}: {len(inputs)} Eingänge")
    typer.echo(f"{vo.name}: {len(commands)} Ausgangsbefehle")
    typer.echo(f"{skipped} Signale nicht exportierbar (Listen, Strukturen, Texte, Nullwerte)")

    # exported_at (Task 5, Phase 5): `GET /api/export/status` der WebUI muss
    # "wann zuletzt exportiert" unabhaengig davon beantworten, ob der letzte
    # Export per CLI oder per API lief - beide schreiben dieselbe Datenbank
    # (siehe Store.mark_exported). Oben bereits geschlossen, hier bewusst
    # erst NACH beiden erfolgreichen write_bytes-Aufrufen wieder geoeffnet:
    # ein fehlgeschlagener Schreibvorgang (siehe die beiden _fail-Aufrufe
    # oben, die das Kommando vorher beenden) darf das Geraet nicht
    # faelschlich als exportiert markieren.
    store = Store(resolved_store_path)
    try:
        store.mark_exported(device_id)
    finally:
        store.close()


def _warn_if_no_password(store: Store) -> None:
    """Warnt beim Start deutlich, solange kein Passwort vergeben ist.

    Nimmt den bereits geoeffneten `Store` entgegen, nicht dessen Pfad: `run`
    unten oeffnet ihn ohnehin schon (in einem `try`/`except`, das einen
    unbeschreibbaren Pfad als klaren CLI-Fehler beendet) und reicht ihn drei
    Zeilen spaeter an `_run` weiter. Eine zweite `Store(store_path)` hier
    haette dieselbe Datei ein zweites Mal geoeffnet - ein zweiter
    `_migrate`-Lauf, eine zweite Sperrdomaene auf derselben SQLite-Datei, und
    ohne den Schutz des `try`/`except` von `run`, das nur die ERSTE Oeffnung
    umgibt.

    Die Warnung gilt seit dem WebUI-Login dem Passwort und NICHT mehr dem
    Token: ein konfiguriertes Token bringt sie nicht zum Schweigen, denn es
    ist der Weg fuer Skripte und kein Ersatz fuer die Ersteinrichtung.

    Der Zustand, vor dem sie warnt, ist ein anderer als frueher. Bis hierher
    lief ein Dienst ohne Token vollstaendig offen. Jetzt liefert er ohne
    Passwort gar nichts mehr aus - dafuer kann bis zur Passwortvergabe jeder,
    der ihn erreicht, ihn uebernehmen, indem er die Ersteinrichtung
    abschliesst (Spec 5, bewusst so entschieden). Genau darauf zielt dieser
    Text.

    Eigene Funktion statt einer Zeile inline in `run`/`_run`, damit ein Test
    sie ohne laufenden Server aufrufen kann - siehe
    `tests/api/test_security.py`."""
    if store.auth.password_hash() is not None:
        return
    logger.warning(
        "Für diese Brücke ist noch kein Passwort vergeben. Bis das geschehen ist, "
        "lässt sich niemand über die Oberfläche anmelden — und jeder, der den Port "
        "erreicht, kann die Ersteinrichtung abschließen und die Brücke damit "
        "übernehmen. Öffne die Oberfläche jetzt und vergib ein Passwort."
    )


@app.command()
def run(
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
    miniserver: str = typer.Option(..., help="IP des Miniservers"),
    port: int = typer.Option(7000, help="UDP-Port, auf dem der Miniserver lauscht"),
    listen: int = typer.Option(8080, help="Port für die HTTP-Kommandos aus Loxone"),
    host: str = typer.Option(
        "0.0.0.0",
        help="Adresse, an die der HTTP-Dienst bindet. Standard 0.0.0.0, weil der "
        "Miniserver den Dienst erreichen muss — siehe --api-token für die "
        "dazugehörige Absicherung der `/api`-Routen (Spec 9, Task 8).",
    ),
    api_token: str | None = typer.Option(
        None,
        "--api-token",
        envvar="LOXMATTER_API_TOKEN",
        help="Schützt die `/api`-Routen der WebUI (Einlernen, Entfernen, "
        "Fabric-Sicherung) mit `Authorization: Bearer <Token>` — alternativ "
        "zur angemeldeten Sitzung, nicht zusätzlich zu ihr erforderlich "
        "(Spec 9, Task 8; Spec 11). `/cmd` und `/resync` bleiben immer "
        "offen — der Miniserver kann keinen Header mitschicken. Nur "
        "Zeichen verwenden, die in einem HTTP-Header stehen dürfen — keine "
        "Leerzeichen, kein Komma, ASCII; `openssl rand -hex 32` erfüllt "
        "das. Alternative über die Umgebungsvariable LOXMATTER_API_TOKEN.",
    ),
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help="Datenbank mit den Signalschlüsseln. Siehe --store-path bei `export`."
    ),
    matter_data_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--matter-data-dir",
        help="matter-server-Datenverzeichnis (storage-path), read-only in diesen "
        "Dienst eingehängt — Grundlage für `GET /api/diagnostics/fabric-backup` "
        "(Spec 4.1, Task 6, Phase 5). Ohne Angabe antwortet die Route mit 503 "
        "statt einer Sicherung. Siehe deploy/testhost/docker-compose.yml für die "
        "dazugehörige Volume-Einhängung.",
    ),
) -> None:
    """Verbindet Matter und Loxone dauerhaft: Werte raus, Kommandos rein.

    Öffnet die Datenbank schon hier, synchron — ein unbeschreibbarer Pfad
    soll als klarer CLI-Fehler enden (wie bei `export`), nicht als
    Traceback aus dem Inneren von `asyncio.run`.
    """
    resolved_store_path = _resolve_store_path(store_path)
    # Wie bei `export` ausgegeben (Review-Fix M10, 2026-09-02): die
    # wahrscheinlichste Fehlkonfiguration ist eine `export`- und eine
    # `run`-Datenbank, die auseinanderlaufen — exportiert mit
    # `--store-path`, gestartet ohne (oder umgekehrt). Ohne diese Zeile
    # zeigt sich das erst als 404 in einem Log, das niemand liest, weil
    # `run` den verwendeten Pfad bislang nie nannte.
    typer.echo(f"Datenbank: {resolved_store_path.resolve()}")
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(
            f"Verzeichnis {resolved_store_path.parent} konnte nicht angelegt werden: {exc}. "
            "Ist der Pfad beschreibbar?"
        )
    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(f"Datenbank {resolved_store_path} konnte nicht geöffnet werden: {exc}")

    _warn_if_no_password(store)
    asyncio.run(_run(store, url, miniserver, port, listen, matter_data_dir, host, api_token))


async def _run(
    store: Store,
    url: str,
    miniserver: str,
    port: int,
    listen: int,
    matter_data_dir: Path | None = None,
    host: str = "0.0.0.0",  # Standard wie in `run` — der Miniserver muss den Dienst erreichen
    api_token: str | None = None,
) -> None:
    """Baut Sender, Laufzeit und Client auf `store` auf und hält sie am Laufen.

    `store` kommt bereits geöffnet herein (siehe `run` oben). `UdpSender`,
    `Runtime` und `_build_client` führen in ihren Konstruktoren keine E/A
    aus, die scheitern könnte — anders als `Store(...)` selbst. Ab hier sind
    also garantiert alle vier Ressourcen vorhanden, wenn `finally` sie
    schließt: kein Leck durch einen fehlgeschlagenen Konstruktor irgendwo
    zwischen `try` und dem ersten `await`.

    Jeder Aufräumschritt in `finally` steht in seinem eigenen `try`/`except`:
    scheitert einer (z. B. `runtime.stop()`, weil der letzte Full-Resend
    mitten in einem Sendefehler steckte), dürfen die folgenden trotzdem
    laufen — sonst bliebe je nach Fehlerort der UDP-Socket offen oder die
    matter-server-Verbindung hängen. `asyncio.CancelledError` fließt an all
    dem vorbei ungefangen durch: ein Strg-C soll den Abbruch weiterreichen,
    nicht als Aufräumfehler verschluckt werden.

    Zum eigentlichen Abbruchverhalten: `uvicorn.Server.serve()` fängt
    SIGINT/SIGTERM selbst ab (`Server.capture_signals`) und kehrt bei einem
    ersten Strg-C geordnet zurück, statt eine Ausnahme zu werfen — der
    `finally`-Block unten läuft in diesem Fall wie bei jedem anderen reguären
    Ende auch. `asyncio.run()` selbst installiert seit Python 3.11 zusätzlich
    einen eigenen SIGINT-Handler, der bei einem Strg-C außerhalb von
    `serve()` (z. B. während `client.connect()`) den gesamten `_run`-Task
    abbricht — auch das erreicht `finally` als normale Abbruch-Ausnahme.
    """
    sender = UdpSender(miniserver, port)
    runtime = Runtime(store, sender)
    client = _build_client(url)

    async def invoke(call: MatterCall) -> None:
        await client.send_command(call)

    try:
        try:
            await client.connect()
        except CannotConnect:
            _fail(f"matter-server unter {url} nicht erreichbar — läuft der Dienst?")
        except MatterUnavailableError as exc:
            _fail(
                f"matter-server unter {url} hat sich verbunden, aber keine "
                f"Bereitschaft gemeldet: {exc}"
            )
        await client.subscribe(store.device_id_for_node, runtime)
        await runtime.start()
        # Startwerte aus dem aktuellen Geraetezustand laden, BEVOR der Resend
        # unten sie verschickt (Spec 6.4, Live-Lauf vom 2026-09-02): ohne das
        # faende `resend_all()` einen leeren Cache vor, weil ein Wert dort nur
        # ueber eine sich aendernde Subscription landet - siehe
        # `Runtime.seed_from_snapshot`.
        await runtime.seed_from_snapshot(await client.snapshots())
        # Ein Neustart der Bridge soll wirken wie /resync (Spec 6.4).
        await runtime.resend_all()

        config = uvicorn.Config(
            build_app(
                store,
                invoke,
                runtime,
                client=client,
                sender=sender,
                matter_data_dir=matter_data_dir,
                api_token=api_token,
            ),
            host=host,
            port=listen,
            log_level="info",
        )
        await uvicorn.Server(config).serve()
    finally:
        try:
            await runtime.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Laufzeit konnte beim Beenden nicht sauber gestoppt werden")
        try:
            await sender.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("UDP-Sender konnte beim Beenden nicht sauber geschlossen werden")
        try:
            await client.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Verbindung zu matter-server konnte beim Beenden nicht sauber getrennt werden"
            )
        store.close()


@app.command()
def set_password(
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help="Datenbank mit den Signalschlüsseln. Siehe --store-path bei `export`."
    ),
) -> None:
    """Setzt das Passwort der Oberfläche neu — der Notausgang für den Fall,
    dass es vergessen wurde.

    Ohne diesen Befehl wäre eine headless aufgesetzte Installation mit
    vergessenem Passwort endgültig verloren: die Ersteinrichtung ist nach
    der ersten Passwortvergabe dauerhaft geschlossen (409), und einen
    zweiten Weg hinein gibt es nicht. Wer diesen Befehl ausführen kann, hat
    Zugriff auf die Datenbankdatei selbst — der Befehl macht daraus nur
    einen benutzbaren Weg statt eines Bastelns am SQLite.

    Verlangt deshalb eine VORHANDENE Datenbank (siehe die Prüfung unten,
    Notausgang-Fund 2026-09-03): dieser Befehl setzt ein Passwort ZURÜCK,
    eine neue Datenbank anzulegen ist in keinem seiner Anwendungsfälle
    gewollt — im Referenz-Deployment
    (`deploy/testhost/docker-compose.yml`) liegt sie in einem benannten
    Docker-Volume, das ausschließlich INNERHALB des Containers unter
    `LOXMATTER_STORE` erreichbar ist. Auf dem Host träfe `Store(...)` sonst
    kommentarlos eine neue, leere Fremddatenbank, schriebe den Hash
    hinein und meldete Erfolg — während die eigentliche Brücke unverändert
    gesperrt bliebe. Diese Prüfung ist die einzige Stelle, die einen
    falschen Pfad überhaupt sichtbar macht, statt ihn lautlos zu
    verschlucken.

    Meldet dabei alle offenen Sitzungen ab — in DERSELBEN Transaktion wie
    den neuen Hash (siehe `AuthStore.reset_password`): wer das Passwort
    zurücksetzt, will nicht, dass eine alte Sitzung weiterläuft, und ein
    Fehlschlag zwischen zwei getrennt committenden Schritten dürfte diesen
    Zustand nicht halb herstellen.
    """
    resolved_store_path = _resolve_store_path(store_path)
    if not resolved_store_path.is_file():
        _fail(
            f"Datenbank {resolved_store_path} wurde nicht gefunden. `set-password` "
            "setzt ein Passwort ZURÜCK und legt deshalb absichtlich keine neue "
            "Datenbank an — das würde eine leere Fremddatenbank erzeugen und Erfolg "
            "melden, während die eigentliche Brücke gesperrt bliebe. Prüfe den "
            "Pfad, gib ihn über --store-path an, oder führe den Befehl — im "
            "Referenz-Deployment — im laufenden Container aus: `docker compose "
            "exec loxmatter loxmatter set-password`."
        )
    password = typer.prompt("Neues Passwort", hide_input=True, confirmation_prompt=True)
    if len(password) < MIN_PASSWORD_LENGTH:
        _fail(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben.")
    store = Store(resolved_store_path)
    try:
        store.auth.reset_password(hash_password(password))
    finally:
        store.close()
    # Bewusst ohne das Passwort in der Ausgabe - auch nicht verkuerzt.
    typer.echo("Passwort gesetzt. Alle offenen Sitzungen wurden abgemeldet.")


@app.command(name="fake-miniserver")
def fake_miniserver_cmd(
    port: int = typer.Option(7000, help="UDP-Port, auf dem gelauscht wird"),
    template: Path | None = typer.Option(  # noqa: B008
        None, help="Erzeugte VIU_-Vorlage: nennt am Ende die Signale, die nie feuerten"
    ),
) -> None:
    """Ersetzt den Miniserver: schreibt jedes Datagramm mit.

    `--template` wird bereits hier geprueft, statt den Nutzer erst nach dem
    Warten auf Strg-C (der Pfad wird erst im `finally` von `_fake_miniserver`
    gelesen) mit einem Fehler zu ueberraschen — wie bei den uebrigen Kommandos
    dieses Moduls soll ein falscher Pfad sofort als CLI-Fehler enden (Review-Fix
    Minor #5).
    """
    if template is not None and not template.is_file():
        _fail(f"Vorlage {template} wurde nicht gefunden.")
    asyncio.run(_fake_miniserver(port, template))


def _silent_keys_report(template_name: str, announced: set[str], silent: list[str]) -> str:
    """Formuliert die Abschlussmeldung von `fake-miniserver --template`.

    Drei zu unterscheidende Faelle: `announced` leer heisst, die Vorlage traegt
    gar kein `Check`-Attribut (z. B. eine VO_-Datei oder eine leere Vorlage) —
    dann gibt es nichts zu pruefen, und das ist etwas anderes als "alles wurde
    gesehen". Nur wenn `announced` nicht leer und `silent` leer ist, war die
    Pruefung tatsaechlich erfolgreich (Review-Fix Minor #4).
    """
    if not announced:
        return f"{template_name} enthält keine Check-Signale — nichts zu prüfen."
    if not silent:
        return (
            f"Alle {len(announced)} Signale aus {template_name} wurden mindestens einmal gesehen."
        )
    lines = [f"{len(silent)} Signale aus {template_name} nie gesehen:"]
    lines += [f"  {key}" for key in silent]
    return "\n".join(lines)


async def _fake_miniserver(port: int, template: Path | None) -> None:
    # datetime.now() ohne tz ist hier Absicht: das ist die Ortszeit fuer einen
    # Menschen, der dem Terminal beim Draufschauen zusieht - keine
    # gespeicherte oder verglichene Zeit.
    def announce(key: str, value: str) -> None:
        typer.echo(f"{datetime.now():%H:%M:%S} {key} = {value}")  # noqa: DTZ005

    def announce_malformed(data: bytes) -> None:
        typer.echo(
            f"{datetime.now():%H:%M:%S} KAPUTT (kein Doppelpunkt): {data!r}",  # noqa: DTZ005
            err=True,
        )

    fake = FakeMiniserver(port=port, on_received=announce, on_malformed=announce_malformed)
    await fake.start()
    typer.echo(f"fake-miniserver lauscht auf UDP-Port {fake.port} — Strg-C zum Beenden")
    try:
        await asyncio.Event().wait()  # blockiert, bis Strg-C den Task abbricht
    finally:
        await fake.stop()
        if template is not None:
            announced = fake.announced_keys(template)
            silent = fake.silent_keys(template)
            typer.echo(f"\n{_silent_keys_report(template.name, announced, silent)}")
