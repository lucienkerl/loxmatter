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

import pytest

from loxmatter.matter.paths import (
    ATTRIBUTE_LIST_ID,
    EVENT_LIST_ID,
    GLOBAL_ATTRIBUTE_IDS,
    parse_attribute_path,
)


def test_parses_endpoint_cluster_attribute():
    assert parse_attribute_path("1/6/0") == (1, 6, 0)


def test_parses_multi_digit_values():
    assert parse_attribute_path("2/1030/65531") == (2, 1030, 65531)


@pytest.mark.parametrize("bad", ["1/6", "1/6/0/9", "", "a/6/0", "1//0"])
def test_rejects_malformed_paths(bad):
    with pytest.raises(ValueError, match="Attributpfad"):
        parse_attribute_path(bad)


def test_global_attribute_ids_cover_the_matter_reserved_range():
    assert ATTRIBUTE_LIST_ID == 0xFFFB
    assert EVENT_LIST_ID == 0xFFFA
    assert GLOBAL_ATTRIBUTE_IDS == {0xFFF8, 0xFFF9, 0xFFFA, 0xFFFB, 0xFFFC, 0xFFFD}
