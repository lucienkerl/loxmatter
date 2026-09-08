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

import pytest

from loxmatter.loxone.values import datagram, format_value, to_loxone_value
from loxmatter.matter.models import SignalKind, SignalRef


def attr(cluster: int, element: int, endpoint: int = 1) -> SignalRef:
    return SignalRef(endpoint, cluster, element, SignalKind.ATTRIBUTE)


def test_temperature_is_hundredths_of_a_degree():
    """Spec 7.3: TemperatureMeasurement delivers 0.01 °C."""
    assert to_loxone_value(attr(1026, 0), 2150) == pytest.approx(21.5)


def test_power_goes_from_milliwatt_to_kilowatt():
    """Spec 7.3: Loxone counts power in kW, not W."""
    assert to_loxone_value(attr(144, 8, endpoint=2), 5_000_000) == pytest.approx(5.0)


def test_small_power_survives_the_conversion():
    """300 mW is 0.0003 kW - exactly the standby load you want to see."""
    assert to_loxone_value(attr(144, 8, endpoint=2), 300) == pytest.approx(0.0003)


def test_level_is_scaled_from_254_to_percent():
    assert to_loxone_value(attr(8, 0), 254) == pytest.approx(100.0)
    assert to_loxone_value(attr(8, 0), 127) == pytest.approx(50.0, abs=0.2)


def test_boolean_passes_through_unscaled():
    assert to_loxone_value(attr(6, 0), True) is True


def test_unknown_cluster_passes_through_unscaled():
    """Spec 3.5: the table enriches, it does not filter."""
    assert to_loxone_value(attr(64999, 7), 42) == pytest.approx(42.0)


def test_unmappable_values_yield_none():
    """Spec 6.6: lists, structs, text and null never become a datagram."""
    assert to_loxone_value(attr(29, 1), [1, 2, 3]) is None
    assert to_loxone_value(attr(40, 1), "IKEA of Sweden") is None
    assert to_loxone_value(attr(49, 7), None) is None


def test_format_trims_trailing_zeros():
    assert format_value(21.5) == "21.5"
    assert format_value(21.0) == "21"
    assert format_value(0.0) == "0"


def test_format_keeps_six_decimals_for_small_values():
    """Without this, every load under 10 W disappears into zero."""
    assert format_value(0.0003) == "0.0003"
    assert format_value(0.000001) == "0.000001"


def test_format_renders_booleans_as_one_and_zero():
    assert format_value(True) == "1"
    assert format_value(False) == "0"


def test_datagram_matches_the_exported_check_pattern():
    """The template recognizes "<key>:\\v" - the datagram must match it (spec 6.1)."""
    assert datagram("d1_2_power", 0.0003) == b"d1_2_power:0.0003"


def test_format_keeps_negative_values_intact():
    """A negative sign is not a rounding error and must not disappear."""
    assert format_value(-21.5) == "-21.5"
    assert format_value(-0.5) == "-0.5"
    assert format_value(-1234567.89) == "-1234567.89"


def test_format_rounds_negative_near_zero_to_plain_zero():
    """ "-0" is simply wrong in a Loxone visualization - no matter how it arises."""
    assert format_value(-1e-07) == "0"
    assert format_value(-0.0) == "0"


def test_negative_temperature_end_to_end():
    """TemperatureMeasurement in hundredths of a degree below zero - the everyday winter case."""
    ref = attr(1026, 0)
    value = to_loxone_value(ref, -1270)
    assert value == pytest.approx(-12.7)
    assert format_value(value) == "-12.7"


def test_format_never_renders_scientific_notation_for_negative_values():
    """Counterpart to test_no_value_formats_to_scientific_notation, with a negative sign."""
    assert "e" not in format_value(-0.000001).lower()
    assert "e" not in format_value(-1234567.89).lower()


def test_the_energy_counter_arrives_in_kilowatt_hours():
    """Matter counts in mWh, Loxone wants kWh (main document 7.3)."""
    ref = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)
    raw = {"0": 2_500_000_000, "1": 1_700_000_000}
    assert to_loxone_value(ref, raw) == pytest.approx(2500.0)


def test_a_struct_without_the_named_member_yields_none_at_runtime():
    """Runtime and decomposition must make the same decision - otherwise
    the UI reports a value that the export does not know."""
    ref = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)
    assert to_loxone_value(ref, {"1": 1_700_000_000}) is None
