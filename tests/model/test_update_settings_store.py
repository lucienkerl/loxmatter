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

"""Tests for `UpdateSettingsStore` - the update channel and the check
toggle, held in the same `setting` table as `LocaleStore.language` and
`ResendSettingsStore` (see their tests for the same pattern). Design
"Applying updates through the web UI" (2026-09-08), section 9."""

from __future__ import annotations

import pytest

from loxmatter.model.store import Store


def test_channel_defaults_to_stable_on_a_fresh_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.update_settings.get_channel() == "stable"
    finally:
        store.close()


def test_set_channel_persists_and_is_read_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.update_settings.set_channel("dev")
        assert store.update_settings.get_channel() == "dev"
    finally:
        store.close()


def test_set_channel_rejects_an_unknown_value(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        with pytest.raises(ValueError):
            store.update_settings.set_channel("beta")
        # No partial success: the default still holds.
        assert store.update_settings.get_channel() == "stable"
    finally:
        store.close()


def test_check_enabled_defaults_to_true_on_a_fresh_store(tmp_path):
    # A device running in a home is reachable by nobody unless it phones
    # out itself; checking must therefore start ON so people actually
    # learn about a fix - see the class docstring for why it can still be
    # turned off.
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.update_settings.get_check_enabled() is True
    finally:
        store.close()


def test_set_check_enabled_false_persists_and_is_read_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.update_settings.set_check_enabled(False)
        assert store.update_settings.get_check_enabled() is False
    finally:
        store.close()


def test_set_check_enabled_true_after_false_persists(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.update_settings.set_check_enabled(False)
        store.update_settings.set_check_enabled(True)
        assert store.update_settings.get_check_enabled() is True
    finally:
        store.close()


def test_settings_survive_reopening_the_same_database(tmp_path):
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.update_settings.set_channel("dev")
        store.update_settings.set_check_enabled(False)
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.update_settings.get_channel() == "dev"
        assert reopened.update_settings.get_check_enabled() is False
    finally:
        reopened.close()


# The known table set of a freshly created store, spelled out here
# instead of derived from `store.py` at import time: this test's entire
# purpose is to catch a table appearing in the schema that this list does
# not already name, so it must not source that list from the same place
# a regression would extend.
_KNOWN_TABLES = frozenset({"command", "device", "session", "setting", "signal", "sqlite_sequence"})


def test_settings_live_in_the_existing_setting_table_not_a_new_one(tmp_path):
    # The design's whole point (section 9, and the class docstring): both
    # settings live in the pre-existing `setting` table from schema 5,
    # not behind a schema bump, so a rollback to the previous release
    # stays consequence-free. This checks the actual on-disk table set -
    # a regression that quietly added a migration and a new table for
    # these two settings would still leave the accessors working (so the
    # other tests here would keep passing), but would defeat the point
    # of this design.
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.update_settings.set_channel("dev")
        store.update_settings.set_check_enabled(False)
        tables = {
            row["name"]
            for row in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert tables == _KNOWN_TABLES
    finally:
        store.close()
