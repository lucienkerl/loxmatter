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

import importlib.util
from pathlib import Path

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


def test_the_readme_is_already_clean():
    readme = Path(__file__).parents[2] / "README.md"
    assert check_language.scan_text(readme.read_text(), "README.md") == []
