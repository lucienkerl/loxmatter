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

"""Tests for the translated texts of UnknownDeviceError/UnknownCommandError/
CategoryMismatchError - str(exc) passes this text through unchanged into an
HTTP response (see api/control.py, api/devices.py, api/export.py, api/
groups.py), but these tests only check the exception itself, independent of
the API."""

from __future__ import annotations

import json
from pathlib import Path

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import (
    CategoryMismatchError,
    Store,
    UnknownCommandError,
    UnknownDeviceError,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


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


def test_group_category_mismatch_error_is_english_by_default(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        lamp_id = store.register_device(load("ikea_kajplats_cws_lamp.json"))
        plug_id = store.register_device(load("ikea_grillplats_plug.json"))
        try:
            store.create_group("Mixed", [lamp_id, plug_id])
        except CategoryMismatchError as exc:
            assert str(exc) == f"device {plug_id} is a socket, the group takes light"
        else:
            raise AssertionError("expected CategoryMismatchError")
    finally:
        store.close()


def test_group_category_mismatch_error_is_german_when_set(tmp_path):
    """Review finding, final fix pass (item 3): this message used to
    interpolate the raw `Category.value` identifiers ("light", "socket")
    straight into an otherwise German sentence - a German reader saw
    "Gerät 4 ist ein light, die Gruppe nimmt socket", the one untranslated
    island in the sentence, and out of step with the WebUI's own pre-check
    for the very same refusal (`web.groups.other_category`, via
    `categoryLabel()` in app.js), which already named the category
    properly. `_check_members` (store.py) now looks both `actual` and
    `expected` up through `api.categories.*` before building this message,
    so a German reader sees German words for both."""
    i18n.set_language("de")
    store = Store(tmp_path / "t.sqlite")
    try:
        lamp_id = store.register_device(load("ikea_kajplats_cws_lamp.json"))
        plug_id = store.register_device(load("ikea_grillplats_plug.json"))
        try:
            store.create_group("Mixed", [lamp_id, plug_id])
        except CategoryMismatchError as exc:
            assert str(exc) == f"Gerät {plug_id} ist ein Steckdose, die Gruppe nimmt Licht"
        else:
            raise AssertionError("expected CategoryMismatchError")
    finally:
        store.close()
