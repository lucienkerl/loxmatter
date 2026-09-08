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

import re
from pathlib import Path

from typer.testing import CliRunner

from loxmatter import i18n
from loxmatter.cli import app
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "nodes"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Removes ANSI sequences from CLI output before checking message text.

    Incident: `test_export_requires_node_or_fixture(_in_german)` were five
    consecutive CI runs on main that were red, even though they ran green locally in 1.8s.
    Cause is `typer.rich_utils.FORCE_TERMINAL`, which
    evaluates `GITHUB_ACTIONS` and forces Rich to color output under Actions - locally,
    Rich sees the same stream without a TTY as "not a terminal" and leaves the message plain.
    Rich colors option names like `--node` individually ("-" and "-node"
    get separate escape sequences), so the pure substring
    check fails even with `NO_COLOR`: `NO_COLOR` suppresses
    only color, not the bold formatting that breaks up the option name.
    More robust than environment variables (which `typer.rich_utils.FORCE_TERMINAL`
    only evaluates at the very first import anyway) is to
    remove the Rich formatting from the captured output
    before the message text is checked - regardless of whether and how
    a specific Rich version is currently coloring."""
    return _ANSI_ESCAPE.sub("", output)


def test_export_writes_both_templates_per_device(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    written = sorted(p.name for p in tmp_path.glob("*.xml"))
    assert len(written) == 2
    assert any(n.startswith("VIU_") for n in written)
    assert any(n.startswith("VO_") for n in written)


def test_exported_file_is_utf8_with_bom_and_crlf(tmp_path):
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    raw = next(tmp_path.glob("VIU_*.xml")).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_button_events_appear_as_pulse_and_counter(tmp_path):
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_bilresa_button.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VIU_*.xml")).read_text(encoding="utf-8-sig")
    # The pulse is detected on the rising edge, not via the value:
    # otherwise a button press would fire twice, because `...:\v` matches
    # both `press:1` and the `press:0` of the pulse's end (observed on
    # the Miniserver, 2026-09-03).
    assert "_press:1" in text
    assert "_press:\\v" not in text
    # The counter is a value and continues to be read as such.
    assert "_press_n:\\v" in text


def test_non_exportable_attributes_do_not_appear(tmp_path):
    """Spec 6.6: of 159 attributes, only 110 can be technically mapped. Since
    task 6, `loxmatter export` additionally exports only what
    `profiles.relevance.is_functional` classifies as actually intended -
    for this plug, 5 of those remain (see
    `tests/export/test_signals.py::test_plug_fixture_yields_6_inputs_with_the_relevance_default`)."""
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VIU_*.xml")).read_text(encoding="utf-8-sig")
    commands = text.count("<VirtualInUdpCmd ")
    assert commands == 5 + 1  # relevant attributes plus the online signal


def test_plug_gets_only_the_onoff_commands(tmp_path):
    """Task 6: output commands come from AcceptedCommandList, not from attributes."""
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VO_*.xml")).read_text(encoding="utf-8-sig")
    # Three commands (on, off, toggle) plus the combined on/off
    # output (2026-09-03): Loxone knows both CmdOn AND CmdOff for a
    # digital virtual output, and a switch can be wired to that
    # directly. The individual ones remain alongside - for
    # devices that can also be switched outside Loxone, where you
    # want to trigger on and off individually instead of relying on an
    # edge that might not come.
    assert text.count("<VirtualOutCmd ") == 4
    assert 'CmdOn="/cmd/d1_1_on/1" CmdOnHTTP="" CmdOnPost="" CmdOff="/cmd/d1_1_off/1"' in text


def test_listen_option_reaches_the_command_url(tmp_path):
    """Review-Fix I3, 2026-09-02: `export` had the command URL's HTTP port
    hardwired to 8080, independent of `run --listen`. Without `--listen`
    the default stays 8080 (backward compatibility); with a
    different value, it must arrive in the VO template."""
    CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--listen",
            "9090",
            "--out",
            str(tmp_path),
        ],
    )
    text = next(tmp_path.glob("VO_*.xml")).read_text(encoding="utf-8-sig")
    assert 'Address="http://192.168.1.50:9090"' in text
    assert "8080" not in text


def test_button_gets_no_output_commands(tmp_path):
    """A button is an input device - without output commands, no VO_
    file is created at all. Previously an empty template was written; it
    would have been imported into Loxone Config without containing anything."""
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_bilresa_button.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    written = sorted(p.name for p in tmp_path.glob("*.xml"))
    assert any(n.startswith("VIU_") for n in written)
    assert not any(n.startswith("VO_") for n in written)
    assert "skipped" in result.stdout  # cli.export.echo_vo_skipped


def test_button_gets_no_output_commands_in_german(tmp_path):
    i18n.set_language("de")
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_bilresa_button.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    written = sorted(p.name for p in tmp_path.glob("*.xml"))
    assert any(n.startswith("VIU_") for n in written)
    assert not any(n.startswith("VO_") for n in written)
    assert "übersprungen" in result.stdout


def test_export_reports_what_it_skipped(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert "49" in result.stdout
    assert "not exportable" in result.stdout  # cli.export.echo_skipped_signals


def test_export_reports_what_it_skipped_in_german(tmp_path):
    i18n.set_language("de")
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert "49" in result.stdout
    assert "nicht exportierbar" in result.stdout


def test_export_reports_how_many_signals_are_held_back_as_expert(tmp_path):
    """Fix 3 (final review): previously `export` only reported
    "6 inputs" and "49 signals not exportable" for a device with
    159 signals - nobody said anything about the remaining 104, nor where
    to turn them on. Behavior tested, not the internal
    calculation: the number (154, see `ExportDeviceOut.hidden_count`'s
    docstring for the same device) and a hint at the place where they
    can be enabled individually must appear in the output."""
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert "154" in result.stdout
    assert "expert" in result.stdout  # cli.export.echo_hidden_signals


def test_export_reports_how_many_signals_are_held_back_as_expert_in_german(tmp_path):
    i18n.set_language("de")
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )
    assert "154" in result.stdout
    assert "Experte" in result.stdout


def test_export_fails_cleanly_when_the_second_file_cannot_be_written(tmp_path, monkeypatch):
    """Fix Important #2: an OSError on the second write_bytes must not show
    a traceback, but must go through _fail() - and while doing so say
    which file was already written and which is missing."""
    original_write_bytes = Path.write_bytes

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        if self.name.startswith("VO_"):
            raise OSError("No disk space left on the device")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    written = sorted(p.name for p in tmp_path.glob("*.xml"))
    assert len(written) == 1
    assert written[0].startswith("VIU_")
    # The message names the file that failed and says what is already there.
    assert "VO_" in result.stderr
    assert "VIU_" in result.stderr


def test_export_fails_cleanly_when_a_system_file_cannot_be_written(tmp_path, monkeypatch):
    """Review-Fix Important #1 (2026-09-02): the two system-template write
    operations were bare `write_bytes` calls without try/except - unlike
    the three device-template write operations, which have long gone
    through `_fail()`. An OSError while writing VO_Matter_System.xml (disk full,
    read-only volume - both realistic for the upcoming container deployment) was
    not allowed to show a traceback."""
    original_write_bytes = Path.write_bytes

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        if self.name.startswith("VO_Matter_System"):
            raise OSError("No disk space left on the device")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--system",
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    written = sorted(p.name for p in tmp_path.glob("*.xml"))
    assert written == ["VIU_Matter_System.xml"]
    # The message names the file that failed and says what is already there.
    assert "VO_Matter_System" in result.stderr
    assert "VIU_Matter_System" in result.stderr


def test_export_fails_cleanly_when_the_output_directory_cannot_be_created(tmp_path, monkeypatch):
    """Review-Fix Important #3: `out.mkdir` was one of three unguarded
    failure points besides the two `write_bytes` calls - an `--out` under
    a read-only directory (a templates folder without
    write permission, a mounted share) must not show a traceback,
    but must go through `_fail()`."""
    original_mkdir = Path.mkdir

    def flaky_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "locked":
            raise OSError("No write permission")
        return original_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", flaky_mkdir)

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path / "locked"),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "locked" in result.stderr


def test_export_creates_no_directory_when_neither_system_nor_source_is_given(tmp_path):
    """Review-Fix Minor #3 (2026-09-02): `out.mkdir` used to run before
    parameter validation - a call without --system, --node, or --fixture would
    still create the target directory before the usage error was raised."""
    out = tmp_path / "would_otherwise_be_created"

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code != 0
    assert not out.exists()


def test_export_requires_node_or_fixture(tmp_path):
    """Fix Minor #4: export shares _load_snapshot with inspect - its
    error paths have so far only been tested via inspect, not via export
    itself."""
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    # cli.common.error_need_node_or_fixture
    assert "specify either --node or --fixture" in _plain(result.output)


def test_export_requires_node_or_fixture_in_german(tmp_path):
    i18n.set_language("de")
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "entweder --node oder --fixture angeben" in _plain(result.output)


def test_export_reports_malformed_fixture_missing_node_id(tmp_path):
    """Fix Minor #4: the same German message as with inspect (test_cli.py),
    triggered here via the export entry point."""
    broken = tmp_path / "broken.json"
    broken.write_text('{"attributes": {}}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(broken),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "node_id" in result.stderr


def test_export_marks_the_device_as_exported(tmp_path):
    """Task 5, Phase 5: the WebUI's `GET /api/export/status` must answer
    "when last exported" independent of whether the last export ran
    via the CLI or via the API - both write to the same database (see
    `Store.mark_exported` and `api/export.py`)."""
    db_path = tmp_path / "store.sqlite"
    result = CliRunner().invoke(
        app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(tmp_path / "out"),
            "--store-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output

    store = Store(db_path)
    try:
        (device,) = store.devices()
        assert device.exported_at is not None
    finally:
        store.close()
