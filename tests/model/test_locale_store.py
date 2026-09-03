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

"""Tests fuer `LocaleStore` - die gemeinsame Spracheinstellung, gehalten in
derselben `setting`-Tabelle wie `AuthStore.password_hash` (siehe dortiges
test_auth_store.py fuer das gleiche Muster)."""

from __future__ import annotations

import pytest

from loxmatter.model.store import Store


def test_language_defaults_to_english_on_a_fresh_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.locale.get_language() == "en"
    finally:
        store.close()


def test_set_language_persists_and_is_read_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.locale.set_language("de")
        assert store.locale.get_language() == "de"
    finally:
        store.close()


def test_set_language_can_be_changed_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.locale.set_language("de")
        store.locale.set_language("en")
        assert store.locale.get_language() == "en"
    finally:
        store.close()


def test_set_language_rejects_an_unsupported_value(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        with pytest.raises(ValueError):
            store.locale.set_language("fr")
        # Kein Teil-Erfolg: der Vorgabewert gilt weiterhin.
        assert store.locale.get_language() == "en"
    finally:
        store.close()


def test_language_survives_reopening_the_same_database(tmp_path):
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.locale.set_language("de")
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.locale.get_language() == "de"
    finally:
        reopened.close()
