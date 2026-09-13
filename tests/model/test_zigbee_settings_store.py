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

"""Which stick is the Zigbee coordinator, as the `setting` table holds it."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import ZigbeeRadioSettings, ZigbeeSettingsStore
from loxmatter.radios.fingerprints import DEFAULT_UNKNOWN

ITEAD = "/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2-if00-port0"


class _FailingConnection:
    """A real `sqlite3.Connection` that fails on its Nth `execute`.

    Everything except that one failure is delegated, transaction handling
    included: `with connection:` here enters and leaves SQLite's own
    context manager, so what the test measures is SQLite rolling the
    half-written setting back, not a fake pretending to."""

    def __init__(self, db: sqlite3.Connection, *, fail_on: int) -> None:
        self._db = db
        self._fail_on = fail_on
        self.calls = 0

    def execute(self, sql: str, *args: Any) -> Any:
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError("the bridge was killed mid-write")
        return self._db.execute(sql, *args)

    def __enter__(self) -> Any:
        return self._db.__enter__()

    def __exit__(self, *exc: object) -> Any:
        return self._db.__exit__(*exc)


def _settings(path: str | None = ITEAD, **fields: object) -> ZigbeeRadioSettings:
    base: dict[str, object] = {
        "path": path,
        "radio_type": "ezsp",
        "baudrate": 115200,
        "flow_control": "software",
        "saved_at": "2026-09-12T10:00:00Z",
    }
    base.update(fields)
    return ZigbeeRadioSettings(**base)  # type: ignore[arg-type]


def test_nothing_stored_reads_as_no_stick_with_the_unknown_defaults(tmp_path: Path) -> None:
    """A fresh install, which is every install until somebody picks a
    stick. It must not raise, and it must not invent a path.

    Fault to prove it: return `None` from `get()` when no row exists. Every
    caller - the startup builder included - then has to handle two shapes
    for the same "nothing configured" state."""
    store = Store(tmp_path / "t.sqlite")
    try:
        stored = store.zigbee_settings.get()
        assert stored.path is None
        assert stored.saved_at is None
        assert (stored.radio_type, stored.baudrate, stored.flow_control) == (
            DEFAULT_UNKNOWN.radio_type,
            DEFAULT_UNKNOWN.baudrate,
            DEFAULT_UNKNOWN.flow_control,
        )
    finally:
        store.close()


def test_the_setting_survives_a_restart(tmp_path: Path) -> None:
    """It lives in loxmatter's own `setting` table, so a container
    recreation - an ordinary update - keeps it.

    Fault to prove it: hold it on the source object in memory. The second
    `Store` below, which is what a recreated container has, then reads
    nothing back and the bridge comes up with no radio at all."""
    first = Store(tmp_path / "t.sqlite")
    first.zigbee_settings.save(_settings(radio_type="znp", baudrate=115200))
    first.close()

    second = Store(tmp_path / "t.sqlite")
    try:
        stored = second.zigbee_settings.get()
        assert stored.path == ITEAD
        assert stored.radio_type == "znp"
        assert stored.baudrate == 115200
        assert stored.saved_at == "2026-09-12T10:00:00Z"
    finally:
        second.close()


def test_saving_no_path_clears_the_path_without_losing_the_rest(tmp_path: Path) -> None:
    """ "No Zigbee stick" is a value, not a missing row: it is what a user
    who removed their stick has chosen.

    Fault to prove it: write the string "None" for a `None` path. `get()`
    then answers a path that is not a device and the supervisor spends
    forever failing to open it."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store.zigbee_settings.save(_settings())
        store.zigbee_settings.save(_settings(path=None, saved_at="2026-09-12T11:00:00Z"))
        stored = store.zigbee_settings.get()
        assert stored.path is None
        assert stored.radio_type == "ezsp"
        assert stored.saved_at == "2026-09-12T11:00:00Z"
    finally:
        store.close()


def test_clearing_forgets_every_key(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.sqlite")
    try:
        store.zigbee_settings.save(_settings(radio_type="deconz", baudrate=38400))
        store.zigbee_settings.clear()
        stored = store.zigbee_settings.get()
        assert stored.path is None
        assert stored.saved_at is None
        assert stored.radio_type == DEFAULT_UNKNOWN.radio_type
        assert stored.baudrate == DEFAULT_UNKNOWN.baudrate
    finally:
        store.close()


def test_a_baud_rate_edited_to_nonsense_from_outside_does_not_stop_the_bridge(
    tmp_path: Path,
) -> None:
    """The rule `ResendSettingsStore.get_interval_seconds` already follows:
    this runs on the startup path that builds the source, so a value
    somebody edited in the database by hand must fall back, not raise.

    Fault to prove it: `int(row["value"])` with no `except`. The bridge then
    does not come up at all, and the reason is a `ValueError` in a store
    nobody would think to look at."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store.zigbee_settings.save(_settings())
        store._db.execute("UPDATE setting SET value = ? WHERE key = ?", ("fast", "zigbee_baudrate"))
        store._db.commit()
        assert store.zigbee_settings.get().baudrate == DEFAULT_UNKNOWN.baudrate
    finally:
        store.close()


def test_a_half_written_setting_cannot_be_read_back(tmp_path: Path) -> None:
    """The five keys are written in ONE transaction, and this is why: a
    bridge killed between two of them would come back with the new stick's
    path and the old stick's baud rate, and open the new stick at the wrong
    speed - which looks exactly like a broken stick.

    The interruption is simulated the only honest way an in-process test
    can: a failure part-way through the writes, through a connection
    wrapper that delegates `__enter__`/`__exit__` to the REAL
    `sqlite3.Connection` - so the transaction semantics under test are
    SQLite's own, not a stand-in's. (`Connection.execute` itself cannot be
    monkeypatched: the attribute is read-only, which is how the first
    version of this test failed.)

    With one transaction the store still reads the OLD, complete setting;
    with five commits it would read a mixture.

    Fault to prove it: commit after every key instead of once at the end.
    `path` below is then the ITEAD stick while `baudrate` is still 38400."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store.zigbee_settings.save(
            _settings(path="/dev/serial/by-id/usb-Old-if00", radio_type="znp", baudrate=38400)
        )

        interrupted = ZigbeeSettingsStore(_FailingConnection(store._db, fail_on=3))
        try:
            interrupted.save(_settings(radio_type="ezsp", baudrate=115200))
        except RuntimeError:
            pass

        stored = store.zigbee_settings.get()
        assert (stored.path, stored.radio_type, stored.baudrate) == (
            "/dev/serial/by-id/usb-Old-if00",
            "znp",
            38400,
        )
    finally:
        store.close()
