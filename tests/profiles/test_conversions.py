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

from loxmatter.profiles.conversions import CONVERSIONS, kelvin_from_mired, lux_from_log


def test_lux_from_log_inverts_the_zcl_encoding():
    """ZCL and Matter both encode 10000 * log10(lux) + 1."""
    assert lux_from_log(1) == pytest.approx(1.0)
    assert lux_from_log(10001) == pytest.approx(10.0)
    assert lux_from_log(20001) == pytest.approx(100.0)
    assert lux_from_log(30001) == pytest.approx(1000.0)


def test_zero_is_too_dark_to_measure_and_means_zero_lux():
    """Both specifications reserve 0 for "too low to be measured". The
    formula would give 0.99977 lx - a reading the sensor never made.

    Fault to prove it: drop the `value == 0` branch."""
    assert lux_from_log(0) == 0.0


def test_kelvin_from_mired_is_the_reciprocal():
    assert kelvin_from_mired(370) == pytest.approx(2702.7, abs=0.1)
    assert kelvin_from_mired(250) == pytest.approx(4000.0)


@pytest.mark.parametrize("mired", [0, -1])
def test_a_mired_outside_the_specified_range_is_not_sendable(mired):
    """1-65279 is the specified range. 0 would divide by zero.

    Fault to prove it: drop the `value <= 0` guard - this raises."""
    assert kelvin_from_mired(mired) is None


def test_the_registry_names_both_functions():
    assert CONVERSIONS["lux_from_log"] is lux_from_log
    assert CONVERSIONS["kelvin_from_mired"] is kelvin_from_mired
