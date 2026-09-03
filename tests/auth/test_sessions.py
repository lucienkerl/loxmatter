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

"""Tests fuer die Sitzungsverwaltung (Spec 7).

`now` ist in beiden Funktionen ein Parameter, damit diese Tests Zeit
vergehen lassen koennen, ohne zu schlafen - eine Sitzung mit 30 Tagen
Laufzeit liesse sich sonst gar nicht pruefen.
"""

from __future__ import annotations

from loxmatter.auth.sessions import (
    SESSION_LIFETIME_SECONDS,
    open_session,
    session_is_valid,
)
from loxmatter.model.store import Store


def _store(tmp_path):
    return Store(tmp_path / "t.sqlite")


def test_a_fresh_session_is_valid(tmp_path):
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        assert session_is_valid(store.auth, session_id, now=1000) is True
    finally:
        store.close()


def test_two_sessions_never_share_an_id(tmp_path):
    store = _store(tmp_path)
    try:
        assert open_session(store.auth, now=1000) != open_session(store.auth, now=1000)
    finally:
        store.close()


def test_an_unknown_id_is_not_valid(tmp_path):
    store = _store(tmp_path)
    try:
        assert session_is_valid(store.auth, "erfunden", now=1000) is False
    finally:
        store.close()


def test_a_session_expires(tmp_path):
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        later = 1000 + SESSION_LIFETIME_SECONDS + 1
        assert session_is_valid(store.auth, session_id, now=later) is False
    finally:
        store.close()


def test_an_expired_session_is_removed_when_it_is_checked(tmp_path):
    """Sonst blieben abgelaufene Zeilen liegen, bis zufaellig jemand eine
    neue Sitzung anlegt."""
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        later = 1000 + SESSION_LIFETIME_SECONDS + 1
        session_is_valid(store.auth, session_id, now=later)
        assert store.auth.session_expires_at(session_id) is None
    finally:
        store.close()


def test_a_session_is_extended_only_after_a_day(tmp_path):
    """Gleitende Verlaengerung ohne Schreibzugriff bei JEDEM Aufruf: eine
    Oberflaeche mit Live-Ansicht stellt viele Anfragen je Minute, und jede
    davon eine SQLite-Schreiboperation waere reine Verschwendung."""
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        first = store.auth.session_expires_at(session_id)

        session_is_valid(store.auth, session_id, now=1000 + 60)
        assert store.auth.session_expires_at(session_id) == first

        session_is_valid(store.auth, session_id, now=1000 + 2 * 24 * 60 * 60)
        assert store.auth.session_expires_at(session_id) > first
    finally:
        store.close()


def test_opening_a_session_purges_expired_ones(tmp_path):
    store = _store(tmp_path)
    try:
        alt = open_session(store.auth, now=1000)
        open_session(store.auth, now=1000 + SESSION_LIFETIME_SECONDS + 1)
        assert store.auth.session_expires_at(alt) is None
    finally:
        store.close()
