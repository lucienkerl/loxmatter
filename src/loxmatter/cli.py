"""Kommandozeile der Bridge."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import NoReturn

import typer
from matter_server.client.exceptions import CannotConnect

from loxmatter.export.commands import extract_commands
from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.signals import to_inputs
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.discovery import (
    extract_signals,
    find_clusters_with_undiscoverable_events,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind
from loxmatter.model.store import Store
from loxmatter.profiles.table import Exportability

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
) -> None:
    """Erzeugt die Loxone-Vorlagen für ein Gerät.

    Der Ort der Signalschlüssel-Datenbank entscheidet über die
    Schlüsselstabilität — siehe `_resolve_store_path` und die Hilfe zu
    `--store-path`. Der verwendete Pfad wird ausgegeben, damit ein Nutzer,
    der versehentlich zwei Datenbanken erzeugt hat, das an der Ausgabe sieht
    statt es aus toten Bausteinen in Loxone zu erschließen.
    """
    snapshot = _load_snapshot(fixture, node, url)

    resolved_store_path = _resolve_store_path(store_path)
    typer.echo(f"Datenbank: {resolved_store_path}")
    resolved_store_path.parent.mkdir(parents=True, exist_ok=True)

    store = Store(resolved_store_path)
    try:
        device_id = store.register_device(snapshot)
        stored = store.register_signals(device_id, snapshot)
    finally:
        store.close()

    label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or f"Node {snapshot.node_id}"
    inputs = to_inputs(stored, device_id, label)
    # Ausgangsbefehle kommen aus AcceptedCommandList, nicht aus den Attributen:
    # Matter-Attribute sind fast alle nur lesbar (Task 6).
    device_commands = extract_commands(snapshot, raw=raw_commands)
    commands = [
        LoxoneCommand(
            key=f"d{device_id}_{c.endpoint}_{c.slug}",
            title=c.slug,
            path=f"/cmd/d{device_id}_{c.endpoint}_{c.slug}/" + ("<v>" if c.takes_value else "1"),
            analog=c.takes_value,
        )
        for c in device_commands
    ]

    out.mkdir(parents=True, exist_ok=True)
    viu = out / filename_for("VIU", device_id, label)
    vo = out / filename_for("VO", device_id, label)

    try:
        viu.write_bytes(render_virtual_in_udp(label, bridge_ip, port, inputs))
    except OSError as exc:
        _fail(f"{viu} konnte nicht geschrieben werden: {exc}. Es wurde noch keine Datei angelegt.")
    try:
        vo.write_bytes(render_virtual_out(label, f"http://{bridge_ip}:8080", commands))
    except OSError as exc:
        _fail(
            f"{vo} konnte nicht geschrieben werden: {exc}. "
            f"Geschrieben wurde bereits {viu}, es fehlt {vo.name}."
        )

    # Text zaehlt mit: der virtuelle Texteingang ist ein eigener Vorlagentyp
    # und kommt in einer spaeteren Ausbaustufe (Spec 6.6).
    unexportable = (Exportability.NONE, Exportability.TEXT)
    skipped = sum(1 for s in stored if s.exportability in unexportable)
    typer.echo(f"{viu.name}: {len(inputs)} Eingänge")
    typer.echo(f"{vo.name}: {len(commands)} Ausgangsbefehle")
    typer.echo(f"{skipped} Signale nicht exportierbar (Listen, Strukturen, Texte, Nullwerte)")
