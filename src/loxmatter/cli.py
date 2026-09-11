# loxmatter - connects Matter devices to a Loxone Miniserver.
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

"""The bridge's command line."""

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
from loxmatter.export.outputs import to_group_outputs, to_outputs
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
from loxmatter.matter.supervisor import attach, supervise
from loxmatter.model.locale_store import LocaleStore
from loxmatter.model.store import Store
from loxmatter.profiles.table import is_exportable

logger = logging.getLogger(__name__)


def _resolve_store_path(explicit: Path | None) -> Path:
    """Determines the path of the signal-key database.

    Precedence: `--store-path` beats the environment variable
    `LOXMATTER_STORE`, which in turn beats the default
    `~/.loxmatter/loxmatter.sqlite`.

    The default is deliberately independent of the working directory. The
    database holds the signal keys — and the keys *are* the wiring in
    Loxone (spec 6.2): once a user has dragged an exported input onto a
    function block, only the key text still connects the block to the
    bridge. If the default were relative to the working directory (e.g.
    `loxmatter.sqlite`), an export from a different directory — today
    `~/exports`, tomorrow the desktop, or a cron job with its own working
    directory — would miss the existing database. The tool would then
    consider the device new, assign a new `device_id`, and with it a
    completely new set of keys. The user imports the new template, and
    every previously wired block silently dies — with no error message.
    Do NOT simplify this back to a relative path.

    `LOXMATTER_STORE` allows a different, fixed location — such as a
    mounted volume in a containerised deployment.
    """
    if explicit is not None:
        return explicit
    override = os.environ.get("LOXMATTER_STORE")
    if override:
        return Path(override)
    return Path.home() / ".loxmatter" / "loxmatter.sqlite"


def _resolve_cli_language(store_path: Path, env: Mapping[str, str]) -> str:
    """Determines the language for EXACTLY this process, called once at
    module import (see below, before `app = typer.Typer(...)`) - see spec
    section 4.

    Precedence: `LOXMATTER_LANG` (this run, without changing the stored
    setting) > stored setting > `DEFAULT_LANGUAGE`. An invalid
    `LOXMATTER_LANG` value warns on stderr and falls back to the next
    tier - this one warning necessarily stays in English, since the
    language is not yet settled at this point.

    `store_path` is NOT `--store-path` (that has not been parsed yet at
    this point, see the "Deviation from the spec" section in this task's
    implementation plan) - the caller passes `_resolve_store_path(None)`,
    i.e. `LOXMATTER_STORE` or the default path.

    Opens the database read-only and only for this one query - NOT via
    `Store(...)`, which runs `CREATE TABLE IF NOT EXISTS` and migrations
    on every call and thus needs write access, which a plain `--help`
    must never require."""
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


# Resolved once at module import, before any command definition below -
# `help=` texts are Typer construction arguments and thus frozen at this
# point (see spec section 5). `typer.echo`/`_fail` calls INSIDE the
# commands, by contrast, read `i18n.current_language()` freshly on every
# call via `t()` - for them this one bootstrap call is not a freeze, only
# the starting value.
i18n.set_language(_resolve_cli_language(_resolve_store_path(None), os.environ))

app = typer.Typer(help=i18n.t("cli.app.help"))


@app.callback()
def main() -> None:
    """Without this callback, Typer turns `loxmatter inspect ...` into
    `loxmatter ...` when there is exactly one command — the subcommand
    disappears."""


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
    """Reports an expected CLI error: one line on stderr, then program end
    with a non-zero exit code — instead of a traceback."""
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _ensure_out_dir(out: Path) -> None:
    """Creates the target directory; reports a failure as a CLI error
    instead of a traceback.

    `export` calls this in two places — once before the system templates,
    once before the device templates (`mkdir(exist_ok=True)` tolerates the
    second call) — instead of once right at the start. That way the
    directory is only created once it is certain the command actually
    writes something: a call without `--system`, `--node` or `--fixture`
    fails at the parameter validation in `_load_snapshot` before anything
    is created here (review fix minor #3, 2026-09-02).
    """
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(i18n.t("cli.common.fail_target_dir", dir=out, exc=exc))


def _load_fixture(path: Path) -> NodeSnapshot:
    """Loads a fixture file; reports broken content as a CLI error instead
    of aborting with a raw KeyError/JSONDecodeError."""
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
    """A dedicated construction step so tests can replace the client via
    monkeypatch with an instance fitted with fake factories — without
    touching the network (see BridgeMatterClient.session_factory)."""
    return BridgeMatterClient(url)


def _load_snapshot(fixture: Path | None, node: int | None, url: str) -> NodeSnapshot:
    """Loads a node snapshot from a file or from a running matter-server.

    Shared by `inspect` and `export`, so the four error messages of this
    path live in one place instead of drifting apart across two commands.
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
    fixture: Path | None = typer.Option(  # noqa: B008 — typer idiom, Path is not Ruff-immutable
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
        # Output commands come from AcceptedCommandList, not from the
        # attributes: Matter attributes are almost all read-only (task 6).
        stored_commands = store.register_commands(
            device_id, extract_commands(snapshot, raw=raw_commands), snapshot.node_id
        )
    finally:
        store.close()

    label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or f"Node {snapshot.node_id}"
    inputs = to_inputs(stored, device_id, label)
    # The key comes exclusively from the store (see register_commands): so
    # the key in the template and the one in the database come from one
    # source instead of two independent compositions that could drift
    # apart without any error reporting it.
    commands = to_outputs(stored_commands)

    _ensure_out_dir(out)
    viu = out / filename_for("VIU", device_id, label)
    vo = out / filename_for("VO", device_id, label)

    try:
        viu.write_bytes(render_virtual_in_udp(label, bridge_ip, port, inputs))
    except OSError as exc:
        _fail(i18n.t("cli.export.fail_write_first_file", path=viu, exc=exc))
    # Without output commands the VO template would be empty apart from
    # its basic skeleton - an import into Loxone Config would bring
    # nothing but an empty template in the tree. The online signal, by
    # contrast, never lets the VIU template be empty (see `to_inputs`), so
    # it is always produced.
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

    # Text counts too: the virtual text input is its own template type and
    # comes in a later expansion stage (spec 6.6). The decision is made by
    # `profiles.table.is_exportable` and nobody else (review fix 8,
    # 2026-09-03) - previously a hand-copied inversion
    # `(Exportability.NONE, Exportability.TEXT)` stood here, a second one
    # in `api/export.py`, and both next to exactly the helper that was
    # meant to end this duplication once already.
    skipped = sum(1 for s in stored if not is_exportable(s.exportability))
    # hidden_count (fix 3 follow-up, phase 6): the same number that
    # `api/export.py`'s `_device_preview` delivers as
    # `ExportDeviceOut.hidden_count` (`StoredSignal.functional`, from
    # `profiles.relevance.is_functional` - no second computation here,
    # just the same expression on the same lines). Previously the
    # arithmetic on the command line visibly did not add up: "6 inputs"
    # and "49 signals not exportable" out of 159 signals left the
    # remaining 104 unmentioned.
    hidden_count = sum(1 for s in stored if not s.functional)
    typer.echo(i18n.t("cli.export.echo_viu_summary", filename=viu.name, count=len(inputs)))
    if commands:
        typer.echo(i18n.t("cli.export.echo_vo_summary", filename=vo.name, count=len(commands)))
    else:
        typer.echo(i18n.t("cli.export.echo_vo_skipped", filename=vo.name))
    typer.echo(i18n.t("cli.export.echo_skipped_signals", count=skipped))
    typer.echo(i18n.t("cli.export.echo_hidden_signals", count=hidden_count))

    # exported_at (task 5, phase 5): the WebUI's `GET /api/export/status`
    # must answer "when last exported" regardless of whether the last
    # export ran via CLI or via API - both write the same database (see
    # Store.mark_exported). Already closed above, deliberately reopened
    # here only AFTER both successful write_bytes calls: a failed write
    # (see the two _fail calls above, which end the command beforehand)
    # must not wrongly mark the device as exported.
    store = Store(resolved_store_path)
    try:
        # Unlike the device above, `export` never registers a group from
        # a snapshot - groups only ever come from the WebUI. So this is
        # not "the group of the device just exported", it is every group
        # the store currently knows, written alongside it every time the
        # command runs, the same way the API's `download` route does.
        #
        # `exported_group_ids` (final fix pass, review finding Important
        # #1): this loop has always written a group's `VO_g*.xml`, but
        # nothing ever called `Store.mark_group_exported` for it -
        # `GET /api/export/status` could then never answer "when last
        # exported" for a group exported only via the CLI, exactly the
        # gap `Store.mark_exported`'s own docstring already describes for
        # a device (design 8: a group's `exported_at` "behaves as on a
        # device"). Collected instead of marked inline, for the same
        # reason as the device's own deferred `mark_exported` call
        # (comment above): a write failure partway through this loop ends
        # the command via `_fail` before any group is marked.
        exported_group_ids: list[int] = []
        for group in store.groups():
            group_commands = to_group_outputs(store.group_commands(group.id))
            group_vo = out / filename_for("VO", group.id, group.label, kind="g")
            if not group_commands:
                # An emptied group has no outputs to offer. It keeps
                # existing (design 4.3); it just has nothing to export.
                typer.echo(i18n.t("cli.export.echo_group_skipped", filename=group_vo.name))
                continue
            try:
                group_vo.write_bytes(
                    render_virtual_out(
                        group.label, f"http://{bridge_ip}:{listen}", group_commands, is_group=True
                    )
                )
            except OSError as exc:
                # Its own `_fail`, distinct from the two above: by this
                # point the device's own VIU/VO have already been written
                # successfully (a failure there would have ended the
                # command before this loop began), so neither
                # `fail_write_first_file` ("no file has been created yet")
                # nor `fail_write_second_file` (a specific written/missing
                # pair) describes this situation honestly.
                _fail(i18n.t("cli.export.fail_write_group_file", path=group_vo, exc=exc))
            typer.echo(
                i18n.t(
                    "cli.export.echo_group_summary",
                    filename=group_vo.name,
                    count=len(group_commands),
                )
            )
            exported_group_ids.append(group.id)
        store.mark_exported(device_id)
        for group_id_written in exported_group_ids:
            store.mark_group_exported(group_id_written)
    finally:
        store.close()


def _warn_if_no_password(store: Store) -> None:
    """Warns clearly at startup for as long as no password has been set.

    Takes the already-opened `Store`, not its path: `run` below opens it
    anyway (in a `try`/`except` that ends an unwritable path as a clear
    CLI error) and passes it on to `_run` three lines later. A second
    `Store(store_path)` here would have opened the same file a second
    time - a second `_migrate` run, a second lock domain on the same
    SQLite file, and without the protection of `run`'s `try`/`except`,
    which only wraps the FIRST opening.

    Since the WebUI login, the warning is about the password and NO
    LONGER about the token: a configured token does not silence it,
    because it is the route for scripts, not a substitute for the initial
    setup.

    The state it warns about is different from before. Up to this point, a
    service without a token ran completely open. Now it delivers nothing
    at all without a password - but in exchange, until a password is set,
    anyone who can reach it can take it over by completing the initial
    setup (spec 5, a deliberate decision). That is exactly what this text
    targets.

    A dedicated function instead of one line inline in `run`/`_run`, so a
    test can call it without a running server - see
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
    update_dir: Path = typer.Option(  # noqa: B008
        Path("/data/update"),
        "--update-dir",
        envvar="LOXMATTER_UPDATE_DIR",
        help=i18n.t("cli.run.help_update_dir"),  # noqa: B008
    ),
) -> None:
    log_handler = install_log_buffer()
    resolved_store_path = _resolve_store_path(store_path)
    # Printed the same way as in `export` (review fix M10, 2026-09-02):
    # the most likely misconfiguration is an `export` database and a
    # `run` database drifting apart — exported with `--store-path`,
    # started without it (or the other way round). Without this line,
    # that only ever shows up as a 404 in a log nobody reads, because
    # `run` used to never name the path it was using.
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
        _run(
            store,
            url,
            miniserver,
            port,
            listen,
            matter_data_dir,
            host,
            api_token,
            log_handler,
            update_dir=update_dir,
        )
    )


async def _run(
    store: Store,
    url: str,
    miniserver: str,
    port: int,
    listen: int,
    matter_data_dir: Path | None = None,
    host: str = "0.0.0.0",  # Same default as `run` — the Miniserver must reach the service
    api_token: str | None = None,
    log_handler: LogBufferHandler | None = None,
    # Task 8, stage 2: the same default as `build_app`'s own - see there.
    # A keyword of its own instead of another positional parameter, so
    # that existing test calls to `_run(...)` keep working unchanged
    # without this argument.
    update_dir: Path = Path("/data/update"),
) -> None:
    """Builds sender, runtime and client on top of `store` and keeps them
    running.

    `store` comes in already open (see `run` above). `UdpSender`,
    `Runtime` and `_build_client` perform no I/O in their constructors
    that could fail — unlike `Store(...)` itself. So from here on it is
    guaranteed that all four resources exist by the time `finally` closes
    them: no leak from a failed constructor anywhere between `try` and the
    first `await`.

    Every cleanup step in `finally` sits in its own `try`/`except`: if one
    fails (e.g. `runtime.stop()`, because the last full resend was stuck
    in a send error), the following ones are still allowed to run —
    otherwise, depending on where the error occurred, the UDP socket
    could stay open or the matter-server connection could hang.
    `asyncio.CancelledError` flows past all of this uncaught: a Ctrl-C is
    meant to propagate the cancellation, not be swallowed as a cleanup
    error.

    On the actual cancellation behaviour: `uvicorn.Server.serve()` itself
    catches SIGINT/SIGTERM (`Server.capture_signals`) and returns cleanly
    on a first Ctrl-C instead of raising an exception — the `finally`
    block below runs in that case just as it would for any other regular
    end. Since Python 3.11, `asyncio.run()` itself additionally installs
    its own SIGINT handler, which, on a Ctrl-C outside `serve()` (e.g.
    during `client.connect()`), cancels the entire `_run` task — that too
    reaches `finally` as a normal cancellation exception.

    **Log ring (task 5, phase 5; call site corrected in task 7, fix 1).**
    `install_log_buffer()` attaches a `LogBufferHandler` to the logger
    `loxmatter` and is called at EXACTLY ONE place in the entire source
    tree — in `run()` above, as its very first statement, NOT here.
    `_run()` receives the finished handler as the parameter `log_handler`
    and passes it on unchanged to `build_app()` below. The "exactly once"
    guarantee depends on the NUMBER of call sites in the source, not on
    their position: `run()` itself calls `install_log_buffer()` only once
    and is the only caller of `_run()` (via `asyncio.run(...)`, likewise
    only once per process). A second call to `install_log_buffer()` —
    wherever it occurred — would attach a SECOND `LogBufferHandler` to the
    same, process-wide logger `loxmatter`, and every subsequent log line
    would arrive twice in `Logger.callHandlers` and appear twice in the
    ring (see
    `test_run_installs_the_log_buffer_exactly_once_and_passes_it_to__run`
    in `tests/test_cli.py`, which proves exactly that with a line count,
    NOT merely with "a handler is present").

    **Why the call site moved at all.** Until task 7, the call sat here in
    `_run()`, immediately before `uvicorn.Config(...)` — i.e. AFTER
    `client.connect()`, `subscribe()`, `runtime.start()`,
    `seed_from_snapshot()` and `resend_all()`, and after the warning from
    `_warn_if_no_password` in `run()`, which runs synchronously before
    `_run()` even begins. Every line any of these steps logged was
    therefore gone before the ring existed — first and foremost the
    security note about the missing password (see
    `test_run_installs_the_log_buffer_before_the_password_warning` in
    `tests/test_cli.py`, which proves exactly this line is in the ring
    after a `run()` run).

    Without passing it on to `build_app()` below, `log_handler` would stay
    at its default value of `None` there, and the log stream of the
    `/api/diagnostics/live` route (task 4 of this phase) would be
    permanently empty in a real run (see the `loxone.server.build_app`
    module docstring, the "`log_handler` is new..." section, which already
    named exactly this gap — see there also for the reverse case, a
    `log_handler` of `None`, as received by every caller of `_run()` that
    passes none, e.g. a test)."""
    sender = UdpSender(miniserver, port)
    client = _build_client(url)
    # `lambda: client.connected`, NOT `client.connected`: the second form
    # would be a bool evaluated once, and the heartbeat would thereby hang
    # forever on the state of the moment of startup. `mypy --strict`
    # rejects it.
    runtime = Runtime(store, sender, link_ok=lambda: client.connected)

    async def invoke(call: MatterCall) -> None:
        await client.send_command(call)

    supervisor_task: asyncio.Task[None] | None = None
    try:
        try:
            await client.connect()
        except CannotConnect:
            _fail(i18n.t("cli.common.fail_matter_unreachable", url=url))
        except MatterUnavailableError as exc:
            _fail(i18n.t("cli.common.fail_matter_not_ready", url=url, exc=exc))
        await runtime.start()
        # Since 8 September 2026 the startup sequence and the rebuild share
        # one place (`matter.supervisor.attach`) - see there for why.
        gained = await attach(client, store, runtime)
        if gained:
            typer.echo(i18n.t("cli.run.echo_commands_backfilled", count=gained))
        # The supervisor runs for as long as the service runs: if the
        # websocket to matter-server dies, it rebuilds the connection and
        # lets `attach` run again. Without it the bridge stays mute after a
        # restart of matter-server, without reporting it - exactly the
        # outage of 8 September 2026.
        supervisor_task = asyncio.ensure_future(supervise(client, store, runtime))

        # `log_handler` arrives already finished (see the docstring above,
        # "Log ring" section) - `install_log_buffer()` itself has, since
        # task 7 (fix 1), lived only in `run()`, BEFORE this entire setup.
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
                update_dir=update_dir,
            ),
            host=host,
            port=listen,
            log_level="info",
        )
        await uvicorn.Server(config).serve()
    finally:
        if supervisor_task is not None:
            supervisor_task.cancel()
            try:
                await supervisor_task
            except asyncio.CancelledError:
                # Two different cancellations arrive here as the same exception,
                # and only one of them is the expected one.
                # `supervisor_task.cancelled()` tells them apart: if the
                # supervisor itself was cancelled, it was our `cancel()` one line
                # above - exactly what we expected, nothing to report. If it was
                # NOT, then the cancellation hit the surrounding `_run` task while
                # we were waiting for it (a second Ctrl-C in the middle of the
                # shutdown), and that one MUST keep travelling - the docstring
                # above says the same for every other cleanup step.
                if not supervisor_task.cancelled():
                    raise
            except Exception:
                # Its own `try` like every neighbouring block: if the supervisor
                # ended earlier on some other exception, `await` delivers it here -
                # and without this `except` the whole rest of the cleanup would be
                # skipped, `store.close()` included.
                logger.exception("Supervisor of the matter-server connection ended with an error")
        try:
            await runtime.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Runtime could not be stopped cleanly on shutdown")
        try:
            await sender.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("UDP sender could not be closed cleanly on shutdown")
        try:
            await client.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Connection to matter-server could not be disconnected cleanly on shutdown"
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
    # Deliberately without the password in the output - not even truncated.
    typer.echo(i18n.t("cli.set_password.echo_success"))


@app.command(name="set-language", help=i18n.t("cli.set_language.help"))
def set_language_cmd(
    language: str = typer.Argument(..., help=i18n.t("cli.set_language.help_language")),
    store_path: Path | None = typer.Option(  # noqa: B008
        None,
        help=i18n.t("cli.common.help_store_path_short"),  # noqa: B008
    ),
) -> None:
    """Sets the shared language setting (CLI and, from phase B on, WebUI).

    Requires, like `set_password`, an EXISTING database, and for the same
    reason: creating a new, empty stray database on the host would, in a
    containerised installation (`LOXMATTER_STORE` reachable only inside
    the container), be a silent failure reported as success."""
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
    """Stands in for the Miniserver: records every datagram.

    `--template` is already checked here, instead of surprising the user
    with an error only after waiting for Ctrl-C (the path is only read in
    `_fake_miniserver`'s `finally`) — as with the other commands of this
    module, a wrong path should end immediately as a CLI error (review fix
    minor #5).
    """
    if template is not None and not template.is_file():
        _fail(i18n.t("cli.fake_miniserver.fail_template_not_found", path=template))
    asyncio.run(_fake_miniserver(port, template))


def _silent_keys_report(template_name: str, announced: set[str], silent: list[str]) -> str:
    """Phrases the closing message of `fake-miniserver --template`.

    Three cases to distinguish: `announced` empty means the template
    carries no `Check` attribute at all (e.g. a VO_ file or an empty
    template) — then there is nothing to check, and that is different
    from "everything was seen". Only when `announced` is non-empty and
    `silent` is empty was the check actually successful (review fix minor
    #4).
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
    # datetime.now() without a tz is intentional here: this is the local
    # time for a human watching the terminal - not a time that gets
    # stored or compared.
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
        await asyncio.Event().wait()  # blocks until Ctrl-C cancels the task
    finally:
        await fake.stop()
        if template is not None:
            announced = fake.announced_keys(template)
            silent = fake.silent_keys(template)
            typer.echo(f"\n{_silent_keys_report(template.name, announced, silent)}")
