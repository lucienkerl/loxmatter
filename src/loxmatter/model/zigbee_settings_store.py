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

"""Which USB stick is the Zigbee coordinator, and how to open it.

Own module and own class, following `locale_store.py`,
`resend_settings_store.py` and `update_settings_store.py`: the `setting`
table is generic by design, exactly so that configuration like this can go
through it instead of earning a table of its own. This class is another
view onto that same table and the store's existing connection, not a second
connection - and therefore no schema bump either, which matters here for
the reason `update_settings_store.py` gives: a schema rise is the one
change an updater rollback cannot undo for free.

**Why the setting lives here rather than on the source object.** A
container recreation - an ordinary update - builds a new process with new
objects, and the stick the user picked has to survive that. It also has to
survive a bridge killed halfway through applying it: the stored row IS the
recovery record, and startup reads it and connects to it, which is the
same place the interrupted apply would have ended up.

**This is a bridge-owned setting, not a sidecar request** (spec correction
3). zigpy runs in this process, so nothing about choosing a Zigbee stick
travels through `radios-request.json`, no container is recreated, and
Thread and Bluetooth are untouched by construction.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from loxmatter.radios.fingerprints import DEFAULT_UNKNOWN

_PATH_KEY = "zigbee_path"
_RADIO_TYPE_KEY = "zigbee_radio_type"
_BAUDRATE_KEY = "zigbee_baudrate"
_FLOW_CONTROL_KEY = "zigbee_flow_control"
_SAVED_AT_KEY = "zigbee_settings_saved_at"

_KEYS = (_PATH_KEY, _RADIO_TYPE_KEY, _BAUDRATE_KEY, _FLOW_CONTROL_KEY, _SAVED_AT_KEY)


@dataclass(frozen=True)
class ZigbeeRadioSettings:
    """The stored radio setting, complete enough to open a stick with.

    `path` of `None` is "no Zigbee stick in this installation" - the state
    of every fresh install, and the state a user returns to by clearing the
    setting. The other three fields keep their values in that case rather
    than being nulled: they are what the Advanced disclosure shows next
    time, and a user who corrected a baud rate by hand should not have to
    do it again after clearing the path once.

    `radio_type`, `baudrate` and `flow_control` are typed as plain `str`
    and `int` rather than as `fingerprints.RadioType` / `FlowControl`: they
    come back out of a SQLite text column, where nothing guarantees the
    literal. `ZigbeeSource` reads them through a `Fingerprint`, and a value
    that is not a radio type fails there with the translated
    "could not be started" message, which is the sentence the user needs -
    not a `ValueError` out of the store on a route that was only listing
    sticks.
    """

    path: str | None
    radio_type: str
    baudrate: int
    flow_control: str
    saved_at: str | None


class ZigbeeSettingsStore:
    """Access to `setting` over the store's connection - like
    `LocaleStore`, just for the five `zigbee_*` keys."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def _value(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def get(self) -> ZigbeeRadioSettings:
        """The stored setting, with `DEFAULT_UNKNOWN`'s values standing in
        for anything unwritten.

        Never raises on a value it cannot read, for the reason
        `ResendSettingsStore.get_interval_seconds` gives: this runs on the
        startup path that builds the source, and a baud rate somebody
        edited to nonsense from outside the application must not stop the
        bridge from starting. A genuine database error - a locked file -
        still propagates.
        """
        raw_baudrate = self._value(_BAUDRATE_KEY)
        try:
            baudrate = DEFAULT_UNKNOWN.baudrate if raw_baudrate is None else int(raw_baudrate)
        except ValueError:
            baudrate = DEFAULT_UNKNOWN.baudrate
        return ZigbeeRadioSettings(
            path=self._value(_PATH_KEY),
            radio_type=self._value(_RADIO_TYPE_KEY) or DEFAULT_UNKNOWN.radio_type,
            baudrate=baudrate,
            flow_control=self._value(_FLOW_CONTROL_KEY) or DEFAULT_UNKNOWN.flow_control,
            saved_at=self._value(_SAVED_AT_KEY),
        )

    def save(self, settings: ZigbeeRadioSettings) -> None:
        """Writes all five keys in ONE transaction.

        One commit rather than five, and that is the point rather than
        tidiness: a bridge killed between two of them would come back with
        a path from the new stick and a baud rate from the old one, and
        open the new stick at the wrong speed - a failure that looks
        exactly like a broken stick and is invisible in the setting the
        user can see.
        """
        rows = (
            (_PATH_KEY, settings.path),
            (_RADIO_TYPE_KEY, settings.radio_type),
            (_BAUDRATE_KEY, str(settings.baudrate)),
            (_FLOW_CONTROL_KEY, settings.flow_control),
            (_SAVED_AT_KEY, settings.saved_at),
        )
        with self._db:
            for key, value in rows:
                if value is None:
                    self._db.execute("DELETE FROM setting WHERE key = ?", (key,))
                    continue
                self._db.execute(
                    "INSERT INTO setting (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    def clear(self) -> None:
        """Forgets the setting entirely - the state of a fresh install.

        Distinct from `save()` with a `path` of `None`, which keeps the
        radio type and baud rate for next time. This one is the reset.
        """
        with self._db:
            for key in _KEYS:
                self._db.execute("DELETE FROM setting WHERE key = ?", (key,))
