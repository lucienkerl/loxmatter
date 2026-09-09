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

"""Tests for the translation mechanism itself - not for individual
CLI strings (those are added in tests/test_cli.py and
tests/test_cli_language.py, once cli.py actually uses them).

`test.*` keys in strings.yaml are deliberately part of the real table,
not a separate test file: `t()` is tied to exactly one file, and
`test.english_only` needs a real, permanently missing `de` entry
to prove the fallback case.
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
    # A failed call must not change the current language.
    assert i18n.current_language() == "en"


def test_supported_languages_are_exactly_en_and_de():
    assert i18n.SUPPORTED_LANGUAGES == frozenset({"en", "de"})


def test_strings_with_prefix_returns_only_matching_keys():
    keys = i18n.strings_with_prefix("test.")
    assert "test.greeting" in keys
    assert "test.english_only" in keys
    assert not any(not k.startswith("test.") for k in keys)


# -----------------------------------------------------------------------------
# raw_template() - regression tests for the finding from the task-8 report
# (see web.test.smoke in strings.yaml as well as api/language.py:_web_strings()):
# t() ALWAYS calls .format(**values), even with an empty values - for
# GET /api/i18n, which must hand the browser the UNRESOLVED template (the
# browser fills in {placeholder} itself, with values like error.message or
# device.label that the server cannot know), that is the wrong building
# block. raw_template() provides the same fallback as t(), just without
# the .format() at the end.
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


# -----------------------------------------------------------------------------
# web.* - regression tests for the complete WebUI translation table
# (task 9): they are meant to catch an accidentally incomplete or broken
# insert, not check every single string - that is what the binding
# task 10+ does, through text pattern matching on the shipped
# source.
# -----------------------------------------------------------------------------


def test_web_namespace_has_no_missing_english_fallback_gaps():
    """Every web.* key must carry at least 'en' - raw_template()
    raises KeyError if even 'en' is missing (see its implementation,
    added in the bugfix before this task: GET /api/i18n used to crash
    at exactly this point, because t() here - with .format() and
    no placeholder values - would have raised a KeyError for EVERY
    web.* key with a {placeholder}. raw_template() is the right
    function for this check: it only checks "does an en entry exist
    at all", not "are all placeholders filled in" - the latter is
    client-side app.js's job, never the server's."""
    for key in i18n.strings_with_prefix("web."):
        assert i18n.raw_template(key)  # raises only if 'en' is missing - no .format() trap


def test_web_namespace_key_count_is_substantial():
    """Rough safeguard against an accidentally incomplete insert -
    not an exact threshold, just a minimum."""
    assert len(i18n.strings_with_prefix("web.")) > 100


def test_no_value_is_wrapped_in_typographic_quotes():
    """No entry in strings.yaml may be wrapped as a whole in typographic
    quotation marks - neither „...“ (German)
    nor “...” (English). An entry that is itself only a quote
    does not occur in this table; a value with exactly this pattern
    is therefore always a bug: YAML does not recognize „ “ ” as
    scalar delimiters (only straight ASCII quotation marks \" or '
    delimit a scalar), so typographic quotation marks that
    accidentally stand in place of the YAML delimiters become literally
    part of the string. That is exactly what happened to web.devices.menu
    and web.devices.menu_room_heading - and nothing in this suite
    would have noticed, because
    test_web_namespace_has_no_missing_english_fallback_gaps only checks THAT
    an 'en' entry exists, never WHAT it contains."""
    opening_quotes = {"„", "“"}
    closing_quotes = {"“", "”"}
    offenders = [
        f"{key}.{lang} = {value!r}"
        for key, translations in i18n._STRINGS.items()
        for lang, value in translations.items()
        if len(value) >= 2 and value[0] in opening_quotes and value[-1] in closing_quotes
    ]
    assert not offenders, (
        "Value entirely wrapped in typographic quotation marks - "
        "likely used as YAML delimiters instead of straight ASCII "
        "quotation marks: " + ", ".join(offenders)
    )
