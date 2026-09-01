"""Kommandozeile der Bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import NoReturn

import typer
from matter_server.client.exceptions import CannotConnect

from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind

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


@app.command()
def inspect(
    node: int | None = typer.Option(None, help="Node-ID am laufenden matter-server"),
    fixture: Path | None = typer.Option(  # noqa: B008 — typer-Idiom, `Path` gilt Ruff nicht als unveränderlich
        None, help="Statt matter-server ein gespeichertes Abbild"
    ),
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
) -> None:
    """Listet alle Attribute und Events eines Geräts auf."""
    if fixture is not None:
        typer.echo(render_report(_load_fixture(fixture)))
        return

    if node is None:
        raise typer.BadParameter("entweder --node oder --fixture angeben")

    async def run() -> str:
        client = _build_client(url)
        try:
            await client.connect()
        except CannotConnect:
            _fail(f"matter-server unter {url} nicht erreichbar — läuft der Dienst?")
        try:
            snapshot = await client.snapshot(node)
        except MatterUnavailableError:
            _fail(f"Node {node} ist am matter-server ({url}) nicht bekannt — kommissioniert?")
        finally:
            await client.disconnect()
        return render_report(snapshot)

    typer.echo(asyncio.run(run()))
