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

"""Tests fuer den Uebersetzungsmechanismus selbst - nicht fuer einzelne
CLI-Zeichenketten (die kommen in tests/test_cli.py bzw.
tests/test_cli_language.py dazu, sobald cli.py sie tatsaechlich nutzt).

`test.*`-Schluessel in strings.yaml sind absichtlich Teil der echten Tabelle,
nicht einer separaten Testdatei: `t()` haengt an genau einer Datei, und
`test.english_only` braucht einen echten, dauerhaft fehlenden `de`-Eintrag,
um den Ruecksicherungsfall zu belegen.
"""

from __future__ import annotations

import pytest

from loxmatter import i18n


def test_default_language_is_english():
    assert i18n.current_language() == "en"
    assert i18n.DEFAULT_LANGUAGE == "en"


def test_t_returns_english_by_default():
    assert i18n.t("test.greeting", name="Ada") == "Hello, Ada!"


def test_t_returns_german_after_set_language():
    i18n.set_language("de")
    assert i18n.t("test.greeting", name="Ada") == "Hallo, Ada!"


def test_t_falls_back_to_english_when_german_is_missing():
    i18n.set_language("de")
    assert i18n.t("test.english_only") == "English only"


def test_t_raises_for_an_unknown_key():
    with pytest.raises(KeyError):
        i18n.t("test.does_not_exist")


def test_set_language_rejects_an_unsupported_value():
    with pytest.raises(ValueError):
        i18n.set_language("fr")
    # Ein fehlgeschlagener Aufruf darf die aktuelle Sprache nicht aendern.
    assert i18n.current_language() == "en"


def test_supported_languages_are_exactly_en_and_de():
    assert i18n.SUPPORTED_LANGUAGES == frozenset({"en", "de"})


def test_strings_with_prefix_returns_only_matching_keys():
    keys = i18n.strings_with_prefix("test.")
    assert "test.greeting" in keys
    assert "test.english_only" in keys
    assert not any(not k.startswith("test.") for k in keys)


# -----------------------------------------------------------------------------
# raw_template() - Regressionstests fuer den Befund aus dem Aufgabe-8-Bericht
# (siehe web.test.smoke in strings.yaml sowie api/language.py:_web_strings()):
# t() ruft IMMER .format(**values) auf, auch mit einem leeren values - fuer
# GET /api/i18n, das dem Browser die UNAUFGELOESTE Vorlage liefern muss (der
# Browser fuellt {platzhalter} selbst, mit Werten wie error.message oder
# device.label, die der Server nicht kennen kann), ist das der falsche
# Baustein. raw_template() liefert dieselbe Ruecksicherung wie t(), nur ohne
# das .format() am Ende.
# -----------------------------------------------------------------------------


def test_raw_template_returns_the_unformatted_template_in_english_by_default():
    assert i18n.raw_template("test.greeting") == "Hello, {name}!"


def test_raw_template_returns_the_unformatted_template_in_german_after_set_language():
    i18n.set_language("de")
    assert i18n.raw_template("test.greeting") == "Hallo, {name}!"


def test_raw_template_falls_back_to_english_when_german_is_missing():
    i18n.set_language("de")
    assert i18n.raw_template("test.english_only") == "English only"


def test_raw_template_raises_for_an_unknown_key():
    with pytest.raises(KeyError):
        i18n.raw_template("test.does_not_exist")
