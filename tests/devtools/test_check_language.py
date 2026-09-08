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

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_language", Path(__file__).parents[2] / "scripts" / "check_language.py"
)
assert _SPEC and _SPEC.loader
check_language = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_language)


def test_a_german_sentence_is_reported():
    findings = check_language.scan_text("# Das Geraet wird nicht gefunden\n", "a.py")
    assert findings
    assert findings[0].line == 1


def test_an_english_sentence_is_clean():
    text = "# The device could not be found, so the export is skipped.\n"
    assert check_language.scan_text(text, "a.py") == []


def test_english_words_that_look_german_are_not_reported():
    # "die", "war", "hat", "bald", "gift", "also", "an", "in" are English too.
    text = "# The die is cast; also, an in-flight war does not hat a gift.\n"
    assert check_language.scan_text(text, "a.py") == []


def test_english_words_containing_ae_oe_ue_ss_are_not_reported():
    # This is why the transliteration check is a word list, not a pattern.
    text = "# It does go across the queue, and a true value passes the class.\n"
    assert check_language.scan_text(text, "a.py") == []


def test_a_transliterated_german_word_is_reported():
    findings = check_language.scan_text("# Uebersetzung missing\n", "a.py")
    assert findings


def test_a_literal_umlaut_is_reported():
    findings = check_language.scan_text("# Größe\n", "a.py")
    assert findings


def test_the_de_values_in_the_string_table_are_exempt():
    text = '  de: "Das Geraet ist nicht erreichbar"\n'
    assert check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml") == []


def test_the_en_values_in_the_string_table_are_still_checked():
    text = '  en: "Das Geraet ist nicht erreichbar"\n'
    assert check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml")


def test_vendored_files_are_exempt():
    text = "// Das ist fremder Code\n"
    assert check_language.scan_text(text, "src/loxmatter/web/vendor/alpine.min.js") == []


def test_a_known_clean_english_document_is_not_flagged():
    # docs/SETUP.md is English throughout and stays that way, so it is the
    # honest false-positive canary. The README is not: until the design
    # documents are renamed it still carries a German section anchor.
    setup = Path(__file__).parents[2] / "docs" / "SETUP.md"
    assert check_language.scan_text(setup.read_text(), "docs/SETUP.md") == []


def test_a_de_block_scalar_and_its_continuation_lines_are_exempt():
    # Block scalars (`de: |`) are how strings.yaml carries multi-paragraph
    # product content. Every indented line under it is still the de: value.
    text = (
        "web.export.zip_note:\n"
        "  en: |\n"
        "    This ZIP file contains Loxone templates.\n"
        "  de: |\n"
        "    Diese ZIP-Datei enthaelt Loxone-Vorlagen, erzeugt von loxmatter.\n"
        "\n"
        '    1. Dateien, die mit "VIU_" beginnen, gehoeren in Loxone Config nach:\n'
        "         Templates\\VirtualIn\\\n"
    )
    assert check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml") == []


def test_an_en_block_scalar_is_still_checked_in_full():
    # The block-scalar skip must be specific to `de:` - it must not leak
    # into an `en:` block that happens to (wrongly) contain German.
    text = "web.export.zip_note:\n  en: |\n    Diese Zeile ist versehentlich Deutsch.\n"
    findings = check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml")
    assert findings
    assert findings[0].line == 3


def test_a_line_after_a_de_block_at_the_same_indentation_is_still_checked():
    # The line that ends the de: block (next key, same/lower indentation)
    # must not be swallowed by the skip.
    text = (
        "web.export.zip_note:\n"
        "  de: |\n"
        "    Diese ZIP-Datei enthaelt Loxone-Vorlagen.\n"
        "web.export.next_key:\n"
        "  en: Noch ein deutscher Satz hier\n"
    )
    findings = check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml")
    assert findings
    assert findings[0].line == 5


def test_blank_lines_inside_a_de_block_do_not_end_it():
    text = (
        "web.export.zip_note:\n"
        "  de: |\n"
        "    Diese ZIP-Datei enthaelt Loxone-Vorlagen.\n"
        "\n"
        "    Noch ein Absatz auf Deutsch, getrennt durch eine Leerzeile.\n"
    )
    assert check_language.scan_text(text, "src/loxmatter/i18n/strings.yaml") == []


def test_the_umlaut_transliteration_table_is_exempt_as_german_data():
    text = (
        '_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}\n'
    )
    assert check_language.scan_text(text, "src/loxmatter/export/documents.py") == []


def test_the_loxone_caption_titles_are_exempt_as_german_data():
    text = 'title = "Virtuelle Eingänge" if kind == "input" else "Virtuelle Ausgänge"\n'
    assert check_language.scan_text(text, "src/loxmatter/projectsync/schema.py") == []


def test_ordinary_german_in_the_same_file_is_still_reported():
    # Proves the GERMAN_AS_DATA exemption is narrow, not file-wide: schema.py
    # still gets checked for ordinary German prose elsewhere in the file.
    text = "# Diese Zeile ist ganz gewoehnliches deutsches Prosa und muss auffallen.\n"
    findings = check_language.scan_text(text, "src/loxmatter/projectsync/schema.py")
    assert findings


def test_german_inside_a_fenced_code_block_is_not_reported():
    # A fenced block quotes what the code/command/UI actually contained;
    # translating it would make the document claim something that never
    # happened.
    text = "prose before\n```\ngit commit -m 'Geraet hinzugefuegt'\n```\nprose after\n"
    assert check_language.scan_text(text, "docs/example.md") == []


def test_german_prose_before_and_after_a_fenced_block_is_still_reported():
    text = (
        "Dieser Satz davor ist deutsche Prosa.\n"
        "```\n"
        "German command output goes here unfazed\n"
        "```\n"
        "Dieser Satz danach ist ebenfalls deutsche Prosa.\n"
    )
    findings = check_language.scan_text(text, "docs/example.md")
    assert [f.line for f in findings] == [1, 5]


def test_an_unterminated_fence_is_reported_not_silently_skipped():
    # Chosen behaviour: an unmatched ``` cannot be told apart from a closed
    # one that simply never appears, so scan_text refuses to guess whether
    # the remaining lines are code or prose. It raises instead of silently
    # skipping to EOF - a loud failure the caller cannot miss, rather than
    # the detector quietly losing coverage over the rest of the file.
    text = (
        "prose before\n```\nDeutscher Text der nie durch einen schliessenden Fence beendet wird\n"
    )
    with pytest.raises(check_language.UnterminatedFenceError):
        check_language.scan_text(text, "docs/example.md")


def test_german_inside_an_inline_backtick_span_is_not_reported():
    text = "See the `Geraet` value shown by the old UI.\n"
    assert check_language.scan_text(text, "docs/example.md") == []


def test_german_outside_backticks_on_a_line_with_a_backtick_span_is_reported():
    text = "the `Geräte` tab shows alle Signale\n"
    findings = check_language.scan_text(text, "docs/example.md")
    assert findings
    assert findings[0].line == 1


def test_python_file_backticks_around_german_are_still_reported():
    # The backtick-quotation rule is Markdown-only. A backtick in a .py
    # comment is not a Markdown inline-code marker, so it must not exempt
    # the German inside it.
    text = "# The `Geraet` lookup failed\n"
    findings = check_language.scan_text(text, "a.py")
    assert findings


def test_a_quoted_string_in_a_test_file_is_data_not_prose():
    # The suite still asserts on German the product emits under the de
    # locale, and on fixture values like room names. Those are data.
    text = 'assert "Das Geraet ist nicht erreichbar" in body\n'
    assert check_language.scan_text(text, "tests/api/test_web.py") == []


def test_german_prose_outside_the_quotes_in_a_test_file_is_still_reported():
    text = 'assert "Ein/Aus" in body  # das Geraet wird nicht gefunden\n'
    assert check_language.scan_text(text, "tests/api/test_web.py")


def test_the_quoting_rule_does_not_apply_outside_tests():
    text = 'raise RuntimeError("Das Geraet ist nicht erreichbar")\n'
    assert check_language.scan_text(text, "src/loxmatter/loxone/sender.py")


def test_captured_fixtures_are_exempt():
    text = '<VirtualOut Comment="erzeugt von loxmatter" />\n'
    assert check_language.scan_text(text, "tests/fixtures/loxone/VO_working.xml") == []


def test_a_one_line_docstring_in_a_test_file_is_still_checked():
    # The quoting rule must not eat a docstring: the empty pair around
    # """...""" would otherwise swallow the prose between them.
    text = '    """Prueft, dass das Geraet nicht erreichbar ist."""\n'
    assert check_language.scan_text(text, "tests/api/test_web.py")
