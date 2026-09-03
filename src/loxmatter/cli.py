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

"""Kommandozeile der Bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import typer
import uvicorn
from matter_server.client.exceptions import CannotConnect

from loxmatter import i18n
from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from loxmatter.commands.translate import MatterCall
from loxmatter.devtools.fake_miniserver import FakeMiniserver
from loxmatter.diagnostics.logbuffer import LogBufferHandler, install_log_buffer
from loxmatter.export.commands import extract_commands
from loxmatter.export.documents import (
    filename_for,
    render_system_templates,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.outputs import to_outputs
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
from loxmatter.model.locale_store import LocaleStore
from loxmatter.model.store import Store
from loxmatter.profiles.table import is_exportable

logger = logging.getLogger(__name__)


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


def _resolve_cli_language(store_path: Path, env: Mapping[str, str]) -> str:
    """Bestimmt die Sprache fuer GENAU diesen Prozess, aufgerufen einmal
    beim Modulimport (siehe unten, vor `app = typer.Typer(...)`) - siehe
    Spec-Abschnitt 4.

    Rangfolge: `LOXMATTER_LANG` (dieser Aufruf, ohne die gespeicherte
    Einstellung zu aendern) > gespeicherte Einstellung > `DEFAULT_LANGUAGE`.
    Ein ungueltiger `LOXMATTER_LANG`-Wert warnt auf stderr und faellt auf
    die naechste Stufe zurueck - diese eine Warnung bleibt zwangslaeufig
    Englisch, die Sprache steht an dieser Stelle noch nicht fest.

    `store_path` ist NICHT `--store-path` (das ist zu diesem Zeitpunkt noch
    nicht geparst, siehe den Abschnitt "Deviation from the spec" im
    Implementierungsplan dieser Aufgabe) - der Aufrufer uebergibt
    `_resolve_store_path(None)`, also `LOXMATTER_STORE` oder den
    Standardpfad.

    Oeffnet die Datenbank nur lesend und nur fuer diese eine Abfrage - NICHT
    ueber `Store(...)`, das bei jedem Aufruf `CREATE TABLE IF NOT EXISTS`
    und Migrationen ausfuehrt und damit einen Schreibzugriff braucht, den
    ein blosses `--help` nie voraussetzen darf."""
    override = env.get("LOXMATTER_LANG")
    if override:
        candidate = override.strip().lower()
        if candidate in i18n.SUPPORTED_LANGUAGES:
            return candidate
        typer.echo(
            f"Warning: LOXMATTER_LANG={override!r} is not supported "
            f"(expected one of: {', '.join(sorted(i18n.SUPPORTED_LANGUAGES))}) - "
            "falling back to the stored or default language.",
            err=True,
        )
    if store_path.is_file():
        try:
            conn = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
        except (sqlite3.Error, OSError):
            return i18n.DEFAULT_LANGUAGE
        try:
            conn.row_factory = sqlite3.Row
            return LocaleStore(conn).get_language()
        except sqlite3.Error:
            return i18n.DEFAULT_LANGUAGE
        finally:
            conn.close()
    return i18n.DEFAULT_LANGUAGE


# Einmal beim Modulimport aufgeloest, vor jeder Kommandodefinition unten -
# `help=`-Texte sind Typer-Konstruktionsargumente und damit an dieser Stelle
# eingefroren (siehe Spec-Abschnitt 5). `typer.echo`/`_fail`-Aufrufe IN den
# Kommandos lesen `i18n.current_language()` dagegen bei jedem Aufruf frisch
# ueber `t()` - fuer sie ist dieser eine Bootstrap-Aufruf kein Einfrieren,
# nur der Startwert.
i18n.set_language(_resolve_cli_language(_resolve_store_path(None), os.environ))

app = typer.Typer(help=i18n.t("cli.app.help"))


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

    lines.append(i18n.t("cli.inspect.report_attributes", count=len(attributes)))
    for ref in attributes:
        lines.append(f"  {ref.path:<16} = {snapshot.attributes.get(ref.path)!r}")

    lines.append("")
    lines.append(i18n.t("cli.inspect.report_events", count=len(events)))
    for ref in events:
        lines.append(f"  {ref.path}")

    missing = find_unreported_attributes(snapshot)
    if missing:
        lines += [
            "",
            i18n.t("cli.inspect.report_missing", count=len(missing)),
        ]
        lines += [f"  {ref.path}" for ref in missing]

    broken = find_unparsable_paths(snapshot)
    if broken:
        lines += ["", i18n.t("cli.inspect.report_unparsable", count=len(broken))] + [
            f"  {p}" for p in broken
        ]

    undiscoverable = find_clusters_with_undiscoverable_events(snapshot)
    if undiscoverable:
        header = i18n.t("cli.inspect.report_undiscoverable", count=len(undiscoverable))
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
        _fail(i18n.t("cli.common.fail_target_dir", dir=out, exc=exc))


def _load_fixture(path: Path) -> NodeSnapshot:
    """Lädt eine Fixture-Datei; meldet kaputten Inhalt als CLI-Fehler statt
    mit einem rohen KeyError/JSONDecodeError abzubrechen."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(i18n.t("cli.common.fail_fixture_invalid_json", path=path, exc=exc))
    try:
        node_id = raw["node_id"]
    except (KeyError, TypeError):
        _fail(i18n.t("cli.common.fail_fixture_missing_node_id", path=path))
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
        raise typer.BadParameter(i18n.t("cli.common.error_need_node_or_fixture"))

    async def run() -> NodeSnapshot:
        client = _build_client(url)
        try:
            await client.connect()
        except CannotConnect:
            _fail(i18n.t("cli.common.fail_matter_unreachable", url=url))
        except MatterUnavailableError as exc:
            _fail(i18n.t("cli.common.fail_matter_not_ready", url=url, exc=exc))
        try:
            return await client.snapshot(node)
        except MatterUnavailableError:
            _fail(i18n.t("cli.common.fail_node_unknown", node=node, url=url))
        finally:
            await client.disconnect()

    return asyncio.run(run())


@app.command(help=i18n.t("cli.inspect.help"))
def inspect(
    node: int | None = typer.Option(None, help=i18n.t("cli.common.help_node")),
    fixture: Path | None = typer.Option(  # noqa: B008 — typer-Idiom, `Path` gilt Ruff nicht als unveränderlich
        None,
        help=i18n.t("cli.inspect.help_fixture"),  # noqa: B008
    ),
    url: str = typer.Option("ws://localhost:5580/ws", help=i18n.t("cli.common.help_matter_url")),
) -> None:
    snapshot = _load_snapshot(fixture, node, url)
    typer.echo(render_report(snapshot))


@app.command(help=i18n.t("cli.export.help"))
def export(
    fixture: Path | None = typer.Option(None, help=i18n.t("cli.export.help_fixture")),  # noqa: B008
    node: int | None = typer.Option(None, help=i18n.t("cli.common.help_node")),
    url: str = typer.Option("ws://localhost:5580/ws", help=i18n.t("cli.common.help_matter_url")),
    bridge_ip: str = typer.Option(..., help=i18n.t("cli.export.help_bridge_ip")),
    port: int = typer.Option(7000, help=i18n.t("cli.common.help_udp_port")),
    listen: int = typer.Option(8080, help=i18n.t("cli.export.help_listen")),
    out: Path = typer.Option(Path("."), help=i18n.t("cli.export.help_out")),  # noqa: B008
    store_path: Path | None = typer.Option(  # noqa: B008
        None,
        help=i18n.t("cli.export.help_store_path"),  # noqa: B008
    ),
    raw_commands: bool = typer.Option(
        False, "--raw-commands", help=i18n.t("cli.export.help_raw_commands")
    ),
    system: bool = typer.Option(False, "--system", help=i18n.t("cli.export.help_system")),
) -> None:
    if system:
        _ensure_out_dir(out)
        viu_sys, vo_sys = render_system_templates(bridge_ip, port, listen)
        viu_sys_path = out / "VIU_Matter_System.xml"
        vo_sys_path = out / "VO_Matter_System.xml"
        try:
            viu_sys_path.write_bytes(viu_sys)
        except OSError as exc:
            _fail(i18n.t("cli.export.fail_write_first_file", path=viu_sys_path, exc=exc))
        try:
            vo_sys_path.write_bytes(vo_sys)
        except OSError as exc:
            _fail(
                i18n.t(
                    "cli.export.fail_write_second_file",
                    path=vo_sys_path,
                    exc=exc,
                    written=viu_sys_path.name,
                    missing=vo_sys_path.name,
                )
            )
        typer.echo(i18n.t("cli.export.echo_system_templates"))
        if fixture is None and node is None:
            return

    snapshot = _load_snapshot(fixture, node, url)

    resolved_store_path = _resolve_store_path(store_path)
    typer.echo(i18n.t("cli.common.echo_database_path", path=resolved_store_path.resolve()))
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(i18n.t("cli.common.fail_store_dir", dir=resolved_store_path.parent, exc=exc))

    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(i18n.t("cli.common.fail_store_open", path=resolved_store_path, exc=exc))
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
    commands = to_outputs(stored_commands)

    _ensure_out_dir(out)
    viu = out / filename_for("VIU", device_id, label)
    vo = out / filename_for("VO", device_id, label)

    try:
        viu.write_bytes(render_virtual_in_udp(label, bridge_ip, port, inputs))
    except OSError as exc:
        _fail(i18n.t("cli.export.fail_write_first_file", path=viu, exc=exc))
    # Ohne Ausgangsbefehle waere die VO-Vorlage leer bis auf ihr Grundgeruest -
    # ein Import in Loxone Config braechte nichts ausser eine leere Vorlage im
    # Baum. Das Online-Signal macht die VIU-Vorlage dagegen nie leer (siehe
    # `to_inputs`), sie entsteht deshalb immer.
    if commands:
        try:
            vo.write_bytes(render_virtual_out(label, f"http://{bridge_ip}:{listen}", commands))
        except OSError as exc:
            _fail(
                i18n.t(
                    "cli.export.fail_write_second_file",
                    path=vo,
                    exc=exc,
                    written=viu.name,
                    missing=vo.name,
                )
            )

    # Text zaehlt mit: der virtuelle Texteingang ist ein eigener Vorlagentyp
    # und kommt in einer spaeteren Ausbaustufe (Spec 6.6). Die Entscheidung
    # faellt `profiles.table.is_exportable` und sonst niemand (Review-Fix
    # Fix 8, 2026-09-03) - vorher stand hier eine von Hand kopierte
    # Umkehrung `(Exportability.NONE, Exportability.TEXT)`, eine zweite in
    # `api/export.py`, und beide neben genau dem Helfer, der diese
    # Verdopplung schon einmal beenden sollte.
    skipped = sum(1 for s in stored if not is_exportable(s.exportability))
    # hidden_count (Nachbesserung Fix 3, Phase 6): dieselbe Zahl, die
    # `api/export.py`s `_device_preview` als `ExportDeviceOut.hidden_count`
    # ausliefert (`StoredSignal.functional`, aus
    # `profiles.relevance.is_functional` - keine zweite Berechnung hier,
    # nur derselbe Ausdruck auf denselben Zeilen). Vorher blieb die Rechnung
    # auf der Kommandozeile sichtbar unvollstaendig: "6 Eingaenge" und "49
    # Signale nicht exportierbar" bei 159 Signalen liessen die restlichen
    # 104 unerwaehnt.
    hidden_count = sum(1 for s in stored if not s.functional)
    typer.echo(i18n.t("cli.export.echo_viu_summary", filename=viu.name, count=len(inputs)))
    if commands:
        typer.echo(i18n.t("cli.export.echo_vo_summary", filename=vo.name, count=len(commands)))
    else:
        typer.echo(i18n.t("cli.export.echo_vo_skipped", filename=vo.name))
    typer.echo(i18n.t("cli.export.echo_skipped_signals", count=skipped))
    typer.echo(i18n.t("cli.export.echo_hidden_signals", count=hidden_count))

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
    logger.warning(i18n.t("cli.run.warn_no_password"))


@app.command(help=i18n.t("cli.run.help"))
def run(
    url: str = typer.Option("ws://localhost:5580/ws", help=i18n.t("cli.common.help_matter_url")),
    miniserver: str = typer.Option(..., help=i18n.t("cli.run.help_miniserver")),
    port: int = typer.Option(7000, help=i18n.t("cli.common.help_udp_port")),
    listen: int = typer.Option(8080, help=i18n.t("cli.run.help_listen")),
    host: str = typer.Option("0.0.0.0", help=i18n.t("cli.run.help_host")),
    api_token: str | None = typer.Option(
        None, "--api-token", envvar="LOXMATTER_API_TOKEN", help=i18n.t("cli.run.help_api_token")
    ),
    store_path: Path | None = typer.Option(  # noqa: B008
        None,
        help=i18n.t("cli.common.help_store_path_short"),  # noqa: B008
    ),
    matter_data_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--matter-data-dir",
        help=i18n.t("cli.run.help_matter_data_dir"),  # noqa: B008
    ),
) -> None:
    log_handler = install_log_buffer()
    resolved_store_path = _resolve_store_path(store_path)
    # Wie bei `export` ausgegeben (Review-Fix M10, 2026-09-02): die
    # wahrscheinlichste Fehlkonfiguration ist eine `export`- und eine
    # `run`-Datenbank, die auseinanderlaufen — exportiert mit
    # `--store-path`, gestartet ohne (oder umgekehrt). Ohne diese Zeile
    # zeigt sich das erst als 404 in einem Log, das niemand liest, weil
    # `run` den verwendeten Pfad bislang nie nannte.
    typer.echo(i18n.t("cli.common.echo_database_path", path=resolved_store_path.resolve()))
    try:
        resolved_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(i18n.t("cli.common.fail_store_dir", dir=resolved_store_path.parent, exc=exc))
    try:
        store = Store(resolved_store_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(i18n.t("cli.common.fail_store_open", path=resolved_store_path, exc=exc))

    _warn_if_no_password(store)
    asyncio.run(
        _run(store, url, miniserver, port, listen, matter_data_dir, host, api_token, log_handler)
    )


async def _run(
    store: Store,
    url: str,
    miniserver: str,
    port: int,
    listen: int,
    matter_data_dir: Path | None = None,
    host: str = "0.0.0.0",  # Standard wie in `run` — der Miniserver muss den Dienst erreichen
    api_token: str | None = None,
    log_handler: LogBufferHandler | None = None,
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

    **Log-Ring (Task 5, Phase 5; Aufrufstelle korrigiert in Task 7, Fix 1).**
    `install_log_buffer()` hängt einen `LogBufferHandler` an den Logger
    `loxmatter` und wird an GENAU EINER Stelle im gesamten Quelltext
    aufgerufen — in `run()` oben, als dessen allererste Anweisung, NICHT
    hier. `_run()` bekommt den fertigen Handler als Parameter `log_handler`
    herein und reicht ihn unten unverändert an `build_app()` weiter. Die
    Absicherung "genau einmal" hängt an der ZAHL der Aufrufstellen im
    Quelltext, nicht an ihrer Position: `run()` ruft `install_log_buffer()`
    selbst nur einmal auf und ist der einzige Aufrufer von `_run()` (über
    `asyncio.run(...)`, ebenfalls nur einmal je Prozess). Ein zweiter
    Aufruf von `install_log_buffer()` — gleich an welcher Stelle — hängte
    einen ZWEITEN `LogBufferHandler` an denselben, prozessweiten Logger
    `loxmatter`, und jede folgende Logzeile liefe zweimal in
    `Logger.callHandlers` ein und stünde doppelt im Ring (siehe
    `test_run_installs_the_log_buffer_exactly_once_and_passes_it_to__run`
    in `tests/test_cli.py`, das genau das mit einer Zeilenzählung belegt,
    NICHT bloß mit "ein Handler ist vorhanden").

    **Warum die Aufrufstelle ueberhaupt umzog.** Bis Task 7 hing der Aufruf
    hier in `_run()`, unmittelbar vor `uvicorn.Config(...)` — also NACH
    `client.connect()`, `subscribe()`, `runtime.start()`,
    `seed_from_snapshot()` und `resend_all()`, und nach der Warnung aus
    `_warn_if_no_password` in `run()`, die synchron läuft, bevor `_run()`
    überhaupt beginnt. Jede Zeile, die einer dieser Schritte protokollierte,
    war deshalb weg, bevor der Ring existierte — allen voran der
    Sicherheitshinweis zum fehlenden Passwort (siehe
    `test_run_installs_the_log_buffer_before_the_password_warning` in
    `tests/test_cli.py`, das genau diese Zeile nach einem `run()`-Lauf im
    Ring nachweist).

    Ohne die Weitergabe an `build_app()` unten bliebe `log_handler` dort
    auf seinem Vorgabewert `None` stehen, und der Log-Strom der Route
    `/api/diagnostics/live` (Task 4 dieser Phase) wäre im echten Lauf
    dauerhaft leer (siehe `loxone.server.build_app`-Moduldocstring,
    Abschnitt "`log_handler` ist neu...", das genau diese Lücke schon
    benannte — siehe dort auch für den umgekehrten Fall, ein `log_handler`
    von `None`, wie ihn jeder Aufrufer von `_run()` bekommt, der keinen
    übergibt, z. B. ein Test)."""
    sender = UdpSender(miniserver, port)
    runtime = Runtime(store, sender)
    client = _build_client(url)

    async def invoke(call: MatterCall) -> None:
        await client.send_command(call)

    try:
        try:
            await client.connect()
        except CannotConnect:
            _fail(i18n.t("cli.common.fail_matter_unreachable", url=url))
        except MatterUnavailableError as exc:
            _fail(i18n.t("cli.common.fail_matter_not_ready", url=url, exc=exc))
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

        # `log_handler` kommt bereits fertig herein (siehe Docstring oben,
        # Abschnitt "Log-Ring") - `install_log_buffer()` selbst steht seit
        # Task 7 (Fix 1) einzig in `run()`, VOR diesem gesamten Aufbau.
        config = uvicorn.Config(
            build_app(
                store,
                invoke,
                runtime,
                client=client,
                sender=sender,
                matter_data_dir=matter_data_dir,
                api_token=api_token,
                log_handler=log_handler,
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


@app.command(help=i18n.t("cli.set_password.help"))
def set_password(
    store_path: Path | None = typer.Option(  # noqa: B008
        None,
        help=i18n.t("cli.common.help_store_path_short"),  # noqa: B008
    ),
) -> None:
    resolved_store_path = _resolve_store_path(store_path)
    if not resolved_store_path.is_file():
        _fail(i18n.t("cli.set_password.fail_db_not_found", path=resolved_store_path))
    password = typer.prompt(
        i18n.t("cli.set_password.prompt"), hide_input=True, confirmation_prompt=True
    )
    if len(password) < MIN_PASSWORD_LENGTH:
        _fail(i18n.t("cli.set_password.fail_too_short", min_length=MIN_PASSWORD_LENGTH))
    store = Store(resolved_store_path)
    try:
        store.auth.reset_password(hash_password(password))
    finally:
        store.close()
    # Bewusst ohne das Passwort in der Ausgabe - auch nicht verkuerzt.
    typer.echo(i18n.t("cli.set_password.echo_success"))


@app.command(name="set-language", help=i18n.t("cli.set_language.help"))
def set_language_cmd(
    language: str = typer.Argument(..., help=i18n.t("cli.set_language.help_language")),
    store_path: Path | None = typer.Option(  # noqa: B008
        None,
        help=i18n.t("cli.common.help_store_path_short"),  # noqa: B008
    ),
) -> None:
    """Setzt die gemeinsame Spracheinstellung (CLI und, ab Phase B, WebUI).

    Verlangt wie `set_password` eine VORHANDENE Datenbank und aus demselben
    Grund: eine neue, leere Fremddatenbank auf dem Host anzulegen waere bei
    einer containerisierten Installation (`LOXMATTER_STORE` nur innerhalb des
    Containers erreichbar) ein stiller Fehlschlag mit gemeldetem Erfolg."""
    if language not in i18n.SUPPORTED_LANGUAGES:
        _fail(
            i18n.t(
                "cli.set_language.fail_unsupported",
                language=language,
                supported=", ".join(sorted(i18n.SUPPORTED_LANGUAGES)),
            )
        )
    resolved_store_path = _resolve_store_path(store_path)
    if not resolved_store_path.is_file():
        _fail(i18n.t("cli.set_language.fail_db_not_found", path=resolved_store_path))
    store = Store(resolved_store_path)
    try:
        store.locale.set_language(language)
    finally:
        store.close()
    i18n.set_language(language)
    typer.echo(i18n.t("cli.set_language.echo_success", language=language))


@app.command(name="fake-miniserver", help=i18n.t("cli.fake_miniserver.help"))
def fake_miniserver_cmd(
    port: int = typer.Option(7000, help=i18n.t("cli.fake_miniserver.help_port")),
    template: Path | None = typer.Option(  # noqa: B008
        None,
        help=i18n.t("cli.fake_miniserver.help_template"),  # noqa: B008
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
        _fail(i18n.t("cli.fake_miniserver.fail_template_not_found", path=template))
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
        return i18n.t("cli.fake_miniserver.report_no_check_signals", template=template_name)
    if not silent:
        return i18n.t(
            "cli.fake_miniserver.report_all_seen", count=len(announced), template=template_name
        )
    lines = [
        i18n.t(
            "cli.fake_miniserver.report_silent_header", count=len(silent), template=template_name
        )
    ]
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
            f"{datetime.now():%H:%M:%S} {i18n.t('cli.fake_miniserver.malformed')} ({data!r})",  # noqa: DTZ005
            err=True,
        )

    fake = FakeMiniserver(port=port, on_received=announce, on_malformed=announce_malformed)
    await fake.start()
    typer.echo(i18n.t("cli.fake_miniserver.echo_listening", port=fake.port))
    try:
        await asyncio.Event().wait()  # blockiert, bis Strg-C den Task abbricht
    finally:
        await fake.stop()
        if template is not None:
            announced = fake.announced_keys(template)
            silent = fake.silent_keys(template)
            typer.echo(f"\n{_silent_keys_report(template.name, announced, silent)}")
