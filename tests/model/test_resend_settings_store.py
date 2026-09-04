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

"""Tests fuer `ResendSettingsStore` - das Intervall des periodischen
Resends, gehalten in derselben `setting`-Tabelle wie `LocaleStore.language`
(siehe dortiges test_locale_store.py fuer das gleiche Muster)."""

from __future__ import annotations

import pytest

from loxmatter.model.resend_settings_store import (
    DEFAULT_RESEND_INTERVAL_SECONDS,
    MIN_RESEND_INTERVAL_SECONDS,
)
from loxmatter.model.store import Store


def test_interval_defaults_on_a_fresh_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.resend_settings.get_interval_seconds() == DEFAULT_RESEND_INTERVAL_SECONDS
    finally:
        store.close()


def test_set_interval_persists_and_is_read_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.resend_settings.set_interval_seconds(60.0)
        assert store.resend_settings.get_interval_seconds() == 60.0
    finally:
        store.close()


def test_set_interval_rejects_a_value_below_the_minimum(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        with pytest.raises(ValueError):
            store.resend_settings.set_interval_seconds(MIN_RESEND_INTERVAL_SECONDS - 1)
        # Kein Teil-Erfolg: der Vorgabewert gilt weiterhin.
        assert store.resend_settings.get_interval_seconds() == DEFAULT_RESEND_INTERVAL_SECONDS
    finally:
        store.close()


def test_an_unparsable_stored_value_falls_back_to_the_default(tmp_path):
    """Kann nur durch eine manuelle Aenderung der Datenbank entstehen (der
    einzige Schreibpfad, set_interval_seconds, validiert vorher) - aber
    get_interval_seconds soll trotzdem nie werfen (finaler Review)."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store._db.execute(
            "INSERT INTO setting (key, value) VALUES ('resend_interval_seconds', 'nicht-numerisch')"
        )
        store._db.commit()
        assert store.resend_settings.get_interval_seconds() == DEFAULT_RESEND_INTERVAL_SECONDS
    finally:
        store.close()


def test_interval_survives_reopening_the_same_database(tmp_path):
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.resend_settings.set_interval_seconds(120.0)
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.resend_settings.get_interval_seconds() == 120.0
    finally:
        reopened.close()
