from pathlib import Path

from typer.testing import CliRunner

from loxmatter.cli import app

FIXTURES = Path(__file__).parent / "fixtures" / "nodes"


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
    assert "_press:\\v" in text
    assert "_press_n:\\v" in text


def test_non_exportable_attributes_do_not_appear(tmp_path):
    """Spec 6.6: von 159 Attributen erreichen nur 109 einen UDP-Eingang."""
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
    assert commands == 109 + 1  # abbildbare Attribute plus Online-Signal


def test_plug_gets_only_the_onoff_commands(tmp_path):
    """Task 6: Ausgangsbefehle stammen aus AcceptedCommandList, nicht aus Attributen."""
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
    assert text.count("<VirtualOutCmd ") == 3


def test_button_gets_no_output_commands(tmp_path):
    """Ein Taster ist ein Eingabegeraet - die VO_-Datei bleibt leer."""
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
    text = next(tmp_path.glob("VO_*.xml")).read_text(encoding="utf-8-sig")
    assert text.count("<VirtualOutCmd ") == 0


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
    assert "50" in result.stdout
    assert "nicht exportierbar" in result.stdout


def test_export_fails_cleanly_when_the_second_file_cannot_be_written(tmp_path, monkeypatch):
    """Fix Important #2: ein OSError beim zweiten write_bytes darf keinen
    Traceback zeigen, sondern muss ueber _fail() laufen — und dabei sagen,
    welche Datei bereits geschrieben wurde und welche fehlt."""
    original_write_bytes = Path.write_bytes

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        if self.name.startswith("VO_"):
            raise OSError("Kein Speicherplatz mehr auf dem Geraet")
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
    # Die Meldung nennt die fehlgeschlagene Datei und sagt, was bereits da ist.
    assert "VO_" in result.stderr
    assert "VIU_" in result.stderr


def test_export_requires_node_or_fixture(tmp_path):
    """Fix Minor #4: export teilt sich _load_snapshot mit inspect — dessen
    Fehlerpfade sind bislang nur ueber inspect getestet, nicht ueber export
    selbst."""
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
    assert "entweder --node oder --fixture angeben" in result.output


def test_export_reports_malformed_fixture_missing_node_id(tmp_path):
    """Fix Minor #4: dieselbe deutsche Meldung wie bei inspect (test_cli.py),
    hier ueber den export-Einstiegspunkt ausgeloest."""
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
