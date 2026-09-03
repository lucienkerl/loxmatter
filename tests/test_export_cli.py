from pathlib import Path

from typer.testing import CliRunner

from loxmatter.cli import app
from loxmatter.model.store import Store

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
    """Spec 6.6: von 159 Attributen sind nur 110 technisch abbildbar. Seit
    Aufgabe 6 exportiert `loxmatter export` zusaetzlich nur, was
    `profiles.relevance.is_functional` als tatsaechlich gewollt einstuft -
    bei dieser Steckdose bleiben davon 5 uebrig (siehe
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
    assert commands == 5 + 1  # relevante Attribute plus Online-Signal


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


def test_listen_option_reaches_the_command_url(tmp_path):
    """Review-Fix I3, 2026-09-02: `export` hatte den HTTP-Port der Kommando-URL
    fest auf 8080 verdrahtet, unabhaengig von `run --listen`. Ohne `--listen`
    bleibt der Default 8080 (Rueckwaertskompatibilitaet), mit einem
    abweichenden Wert muss er in der VO-Vorlage ankommen."""
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
    assert "49" in result.stdout
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


def test_export_fails_cleanly_when_a_system_file_cannot_be_written(tmp_path, monkeypatch):
    """Review-Fix Important #1 (2026-09-02): die beiden Systemvorlagen-Schreibvorgaenge
    waren bare `write_bytes`-Aufrufe ohne try/except — anders als die drei
    Geraetevorlagen-Schreibvorgaenge, die laengst ueber `_fail()` laufen. Ein
    OSError beim Schreiben von VO_Matter_System.xml (Platte voll, schreibgeschuetztes
    Volume — beides realistisch fuer die kommende Container-Bereitstellung) durfte
    keinen Traceback zeigen."""
    original_write_bytes = Path.write_bytes

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        if self.name.startswith("VO_Matter_System"):
            raise OSError("Kein Speicherplatz mehr auf dem Geraet")
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
    # Die Meldung nennt die fehlgeschlagene Datei und sagt, was bereits da ist.
    assert "VO_Matter_System" in result.stderr
    assert "VIU_Matter_System" in result.stderr


def test_export_fails_cleanly_when_the_output_directory_cannot_be_created(tmp_path, monkeypatch):
    """Review-Fix Important #3: `out.mkdir` war einer von drei ungeschuetzten
    Fehlerpunkten neben den beiden `write_bytes`-Aufrufen — ein `--out` unter
    einem schreibgeschuetzten Verzeichnis (ein Templates-Ordner ohne
    Schreibrechte, eine eingehaengte Freigabe) darf keinen Traceback zeigen,
    sondern muss ueber `_fail()` laufen."""
    original_mkdir = Path.mkdir

    def flaky_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "gesperrt":
            raise OSError("Keine Schreibrechte")
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
            str(tmp_path / "gesperrt"),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "gesperrt" in result.stderr


def test_export_creates_no_directory_when_neither_system_nor_source_is_given(tmp_path):
    """Review-Fix Minor #3 (2026-09-02): `out.mkdir` lief frueher vor der
    Parametervalidierung — ein Aufruf ohne --system, --node oder --fixture legte
    das Zielverzeichnis trotzdem an, bevor der Nutzungsfehler geworfen wurde."""
    out = tmp_path / "wuerde_sonst_entstehen"

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


def test_export_marks_the_device_as_exported(tmp_path):
    """Task 5, Phase 5: `GET /api/export/status` der WebUI soll "wann
    zuletzt exportiert" unabhaengig davon beantworten, ob der letzte Export
    per CLI oder per API lief - beide schreiben dieselbe Datenbank (siehe
    `Store.mark_exported` und `api/export.py`)."""
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
