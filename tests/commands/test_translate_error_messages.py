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

"""Tests fuer die uebersetzten UnsupportedValueError-Texte in
commands/translate.py."""

from __future__ import annotations

import pytest

from loxmatter import i18n
from loxmatter.commands.translate import UnsupportedValueError, _as_number, to_matter_call
from loxmatter.model.store import StoredCommand


def test_as_number_error_is_english_by_default():
    with pytest.raises(UnsupportedValueError, match="value 'abc' is not a number"):
        _as_number("abc")


def test_as_number_error_is_german_when_set():
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="Wert 'abc' ist keine Zahl"):
        _as_number("abc")


def test_unsupported_command_error_is_english_by_default():
    command = StoredCommand(
        key="d1_c99_cmd0",
        slug="cmd0",
        node_id=1,
        endpoint=1,
        cluster_id=99,
        command_id=0,
        takes_value=False,
        device_id=1,
    )
    with pytest.raises(UnsupportedValueError, match="Cluster 99 command 0 is not supported"):
        to_matter_call(command, "")
