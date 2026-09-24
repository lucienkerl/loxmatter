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

"""Tests for the translated UnsupportedValueError texts in
commands/translate.py."""

from __future__ import annotations

import pytest

from loxmatter import i18n
from loxmatter.commands.translate import (
    UnsupportedValueError,
    _as_number,
    parse_kelvin,
    to_device_calls,
)
from loxmatter.model.store import StoredCommand


def test_as_number_error_is_english_by_default():
    with pytest.raises(UnsupportedValueError, match="value 'abc' is not a number"):
        _as_number("abc")


def test_as_number_error_is_german_when_set():
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="Wert 'abc' ist keine Zahl"):
        _as_number("abc")


@pytest.mark.parametrize("value", ["0", "-5"])
def test_kelvin_not_positive_error_is_english_by_default(value):
    with pytest.raises(
        UnsupportedValueError, match=f"colour temperature '{value}' must be above 0 Kelvin"
    ):
        parse_kelvin(value)


@pytest.mark.parametrize("value", ["0", "-5"])
def test_kelvin_not_positive_error_is_german_when_set(value):
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match=f"Farbtemperatur '{value}' muss über 0 Kelvin"):
        parse_kelvin(value)


def test_parse_kelvin_still_reports_a_non_number_as_a_non_number():
    with pytest.raises(UnsupportedValueError, match="value 'abc' is not a number"):
        parse_kelvin("abc")


def test_unsupported_command_error_is_english_by_default():
    command = StoredCommand(
        key="d1_c99_cmd0",
        slug="cmd0",
        technology="matter",
        address="1",
        endpoint=1,
        cluster_id=99,
        command_id=0,
        takes_value=False,
        device_id=1,
    )
    with pytest.raises(UnsupportedValueError, match="Cluster 99 command 0 is not supported"):
        to_device_calls(command, "")


def test_a_lumitech_value_on_colortemp_is_rejected_not_sent_as_zero_mired():
    """Design 2026-09-24, 3.3: before, 201002700 became 0 mired.

    Fault to prove it: remove the `is_lumitech` check - no error is raised."""
    with pytest.raises(UnsupportedValueError, match="Lumitech value '201002700'"):
        parse_kelvin("201002700")


def test_the_lumitech_on_colortemp_error_is_german_when_set():
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="Lumitech-Wert '201002700'"):
        parse_kelvin("201002700")


def test_a_plain_kelvin_value_still_passes():
    assert parse_kelvin("2700") == 2700


def test_a_lumitech_value_with_a_spurious_fraction_is_still_rejected_as_lumitech():
    """A Lumitech number carries no fraction, but the old guard
    (`kelvin == int(kelvin) and is_lumitech(...)`) let "200002700.5" slip
    past the Lumitech check and on into `kelvin_to_mireds`, which truncated
    it to 0 mired.

    Fault to prove it: put the equality condition back
    (`if kelvin == int(kelvin) and is_lumitech(int(kelvin)):`) - this test
    fails because the value is instead accepted as a (huge) Kelvin value."""
    with pytest.raises(UnsupportedValueError, match="Lumitech value '200002700.5'"):
        parse_kelvin("200002700.5")


def test_a_kelvin_value_above_one_million_is_rejected_not_sent_as_zero_mired():
    """1,000,001 K and above is outside the Lumitech range but still turns
    into 0 mired via `int(1_000_000 / kelvin)` - just as unsendable.

    Fault to prove it: remove the `kelvin > 1_000_000` check - no error is
    raised, and the value flows on to `kelvin_to_mireds`."""
    with pytest.raises(UnsupportedValueError, match="colour temperature '2000000' is above"):
        parse_kelvin("2000000")


def test_the_kelvin_too_high_error_is_german_when_set():
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="Farbtemperatur '2000000' liegt über"):
        parse_kelvin("2000000")


def test_one_million_kelvin_is_the_last_value_that_still_passes():
    """`int(1_000_000 / 1_000_000) == 1` mired - the boundary itself still
    works, only values above it are rejected."""
    assert int(1_000_000 / 1_000_000) == 1
    assert parse_kelvin("1000000") == 1000000
