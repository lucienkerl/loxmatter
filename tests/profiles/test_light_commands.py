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

"""The default export rule for light commands (design 2026-09-24, 4.2)."""

from __future__ import annotations

import pytest

from loxmatter.profiles.categories import is_light_endpoint
from loxmatter.profiles.light_commands import (
    COLOUR_HS,
    COLOUR_TEMPERATURE,
    COLOUR_XY,
    LEVEL,
    LEVEL_ONOFF,
    LIGHT_COMMAND_PAIRS,
    LUMITECH,
    OFF,
    ON,
    TOGGLE,
    is_expert_light_command,
)

_SINGLE = [OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE]


def test_lumitech_is_a_light_pair_no_real_command_can_have():
    assert LUMITECH in LIGHT_COMMAND_PAIRS
    assert LUMITECH[0] < 0


@pytest.mark.parametrize("pair", _SINGLE)
def test_a_single_light_command_beside_lumitech_is_expert(pair):
    """Fault to prove it: return False unconditionally - this fails."""
    assert is_expert_light_command(pair, endpoint_has_lumitech=True)


@pytest.mark.parametrize("pair", _SINGLE)
def test_a_single_light_command_without_lumitech_stays_functional(pair):
    """An endpoint without the output (a plug) keeps every command it has."""
    assert not is_expert_light_command(pair, endpoint_has_lumitech=False)


def test_lumitech_itself_is_never_expert():
    assert not is_expert_light_command(LUMITECH, endpoint_has_lumitech=True)


def test_a_non_light_pair_is_never_expert():
    assert not is_expert_light_command((258, 0), endpoint_has_lumitech=True)


@pytest.mark.parametrize("device_type", [0x0100, 0x0101, 0x010C, 0x010D, 0x010F, 0x0110])
def test_light_device_types_make_a_light_endpoint(device_type):
    assert is_light_endpoint(frozenset({device_type}))


@pytest.mark.parametrize("device_type", [0x010A, 0x010B, 0x000F, 0x0510])
def test_other_device_types_do_not(device_type):
    assert not is_light_endpoint(frozenset({device_type}))
