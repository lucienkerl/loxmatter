"""Prueft die Ausgabeform gegen echte Loxone-Vorlagen.

Die Referenzdateien unter ``tests/fixtures/loxone/`` sind bereinigte
Ableitungen aus einer echten Loxone-Config-Installation (siehe
``tests/fixtures/VirtualIn/`` und ``VirtualOut/``, die deshalb .gitignored
bleiben). Dieser Test prueft nur die Dateiform, nicht den Inhalt: BOM,
Zeilenenden und die erste Zeile muessen zu dem passen, was
``loxmatter.export.xml`` erzeugt.
"""

from pathlib import Path

import pytest

from loxmatter.export.xml import DECLARATION

FIXTURES = Path(__file__).parents[1] / "fixtures" / "loxone"
FIXTURE_FILES = sorted(FIXTURES.glob("*.xml"))


def test_fixture_directory_is_not_empty():
    """Waechter: eine leer geraeumte Vorlagenmappe darf die Tests unten nicht
    stillschweigend bestehen lassen."""
    assert FIXTURE_FILES, f"Keine *.xml-Vorlagen gefunden unter {FIXTURES}"


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_reference_file_starts_with_utf8_bom(path: Path):
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_reference_file_uses_pure_crlf_line_endings(path: Path):
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_reference_file_first_line_is_the_declaration(path: Path):
    text = path.read_bytes().decode("utf-8-sig")
    assert text.splitlines()[0] == DECLARATION
