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

"""Tests for the translated texts of UnknownDeviceError/UnknownCommandError -
str(exc) passes this text through unchanged into an HTTP response (see
api/control.py, api/devices.py, api/export.py), but these tests only check
the exception itself, independent of the API."""

from __future__ import annotations

from loxmatter import i18n
from loxmatter.model.store import Store, UnknownCommandError, UnknownDeviceError


def test_unknown_device_error_is_english_by_default(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        try:
            store.device(999)
        except UnknownDeviceError as exc:
            assert str(exc) == "unknown device 999"
        else:
            raise AssertionError("expected UnknownDeviceError")
    finally:
        store.close()


def test_unknown_device_error_is_german_when_set(tmp_path):
    i18n.set_language("de")
    store = Store(tmp_path / "t.sqlite")
    try:
        try:
            store.device(999)
        except UnknownDeviceError as exc:
            assert str(exc) == "unbekanntes Geraet 999"
        else:
            raise AssertionError("expected UnknownDeviceError")
    finally:
        store.close()


def test_unknown_command_error_is_english_by_default(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        try:
            store.resolve_command("nope")
        except UnknownCommandError as exc:
            assert str(exc) == "unknown command key 'nope'"
        else:
            raise AssertionError("expected UnknownCommandError")
    finally:
        store.close()
