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

"""The update channel: which stream this installation follows."""

from __future__ import annotations

from loxmatter.model.store import Store
from loxmatter.model.update_settings_store import UpdateChannel


def test_the_default_channel_is_stable(tmp_path):
    """An installation that has never chosen follows released versions.

    The other way round would be the dangerous default: an operator who
    never opened the setting would be riding every commit on main.
    """
    store = Store(tmp_path / "s.sqlite")
    try:
        assert store.update_settings.get_channel() is UpdateChannel.STABLE
    finally:
        store.close()


def test_a_saved_channel_comes_back(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    try:
        store.update_settings.set_channel(UpdateChannel.DEV)
        assert store.update_settings.get_channel() is UpdateChannel.DEV
    finally:
        store.close()


def test_an_unknown_stored_value_falls_back_to_stable(tmp_path):
    """A row written by a newer version, or by hand, must not crash the
    bridge on start - and must not silently mean `dev`.

    Falling back to `stable` is the conservative reading: whatever this
    value was meant to be, following released versions is the choice that
    cannot surprise anyone.
    """
    store = Store(tmp_path / "s.sqlite")
    try:
        store._db.execute(
            "INSERT OR REPLACE INTO setting (key, value) VALUES (?, ?)",
            ("update_channel", "nightly"),
        )
        store._db.commit()
        assert store.update_settings.get_channel() is UpdateChannel.STABLE
    finally:
        store.close()
