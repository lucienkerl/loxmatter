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

"""Starts the WebUI with two example devices, without matter-server - for
manually viewing the device dashboard changes in the browser (see
docs/superpowers/plans/2026-09-03-device-dashboard-and-export.md, Task 4).

Usage: uv run python scripts/dev_web_server.py
Then: open http://127.0.0.1:8420, set any password
(first-run setup, only applies to this test run).

The database lives in a fixed file in the temp directory - a second run
finds the same set of data again, instead of commissioning everything anew
each time.

With `--demo`, the mode for the README screenshots starts instead: four
devices with English names, password and bridge settings already prefilled,
and the database is freshly created on every start instead of reusing the
existing one."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import uvicorn

from loxmatter.auth.passwords import hash_password
from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store, StoredSignal
from loxmatter.profiles.table import Exportability

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "nodes"

DEMO_PASSWORD = "loxmatter-demo"

# Fixed point in time for everything that gets a timestamp in demo mode.
# Without it, `settings.png` and `export.png` carried the wall clock of
# each run, and `capture_screenshots.py` produced new image files on every
# call that differed only in this timestamp - around 700 KB of binary
# noise per run, in which a real layout change would have gone unnoticed.
# The value itself is arbitrary, just constant; it deliberately lies far
# in the past so "last exported" in the interface doesn't look like "just
# now".
DEMO_TIMESTAMP = "2026-01-15T09:30:00+00:00"

# Order determines the order in the device list - the plug first, because
# its signal list best shows the functional/expert distinction (over a
# hundred signals, a handful of them functional).
# The third entry is the room (device tab design, 2026-09-05). Without it
# all four devices would stay under "No room", and the room bar wouldn't
# show up at all - it only appears once at least one device carries a
# room. The screenshot would then have failed to show the very thing the
# view was rebuilt for.
#
# The kitchen deliberately gets TWO devices: only that way can a group
# show that devices are sorted by category within a room - the lamp
# (rank 0) comes before the plug (rank 1), regardless of the order in
# this list.
DEMO_DEVICES = [
    ("ikea_grillplats_plug.json", "Coffee machine", "Kitchen"),
    ("example_light.json", "Living room lamp", "Living room"),
    ("ikea_kajplats_cws_lamp.json", "Kitchen spots", "Kitchen"),
    ("ikea_bilresa_button.json", "Hallway button", "Hallway"),
]


def _ensure_demo_devices(store: Store) -> list[int]:
    """Like `_ensure_devices`, but four devices with English names: the
    README screenshots show an English interface, German device names in
    it would look like an oversight."""
    if store.devices():
        return [device.id for device in store.devices()]

    device_ids: list[int] = []
    for filename, label, room in DEMO_DEVICES:
        snapshot = _load_snapshot(filename)
        device_id = store.register_device(snapshot, room=room)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        store.rename_device(device_id, label)
        device_ids.append(device_id)

    # One device counts as already exported, so the export preview shows
    # both cases side by side instead of four identical-looking rows.
    store.mark_exported(device_ids[0])
    # ... but with a fixed time instead of "now", see DEMO_TIMESTAMP. The
    # store deliberately always writes `now_iso()` - that's correct in
    # production and shouldn't become configurable there just so a demo
    # mode can exist. So it's overwritten here after the fact instead of
    # cutting a seam into the production class.
    store._db.execute(
        "UPDATE device SET exported_at = ? WHERE id = ?", (DEMO_TIMESTAMP, device_ids[0])
    )
    store._db.commit()
    return device_ids


def _load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


class _SeededRuntime:
    """Fulfils `loxone.server._RuntimeDependency` - everything `build_app`
    itself and the routers it hands `runtime` to need:
    `api.devices.RuntimeValues.last_values_for`/`last_heard_for` with a
    handful of made-up but plausible values (enough that the device cards don't just show
    "-"), `api.live.ObservableRuntime.add_observer`/`remove_observer` as
    no-ops (`/api/live` calls them on every connection open and close,
    regardless of whether this service ever delivers a live value), and
    `resend_all` as a no-op for `/resync`. Not a substitute for `Runtime`:
    there is no real live connection, the set values stay fixed until this
    process restarts - an observer registered here simply never gets a
    notification, and `/resync` sends nothing."""

    def __init__(self, values: dict[str, float | bool]) -> None:
        self._values = values

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}

    def last_heard_for(self, device_id: int) -> str | None:
        """This service never hears anything real (no Matter client) - so
        `None` here is not merely the minimum value that satisfies the
        protocol, but the honest statement."""
        return None

    async def set_online(self, device_id: int, online: bool) -> None:
        """Like `Runtime.set_online`, just without the UDP send: holds the
        value under the same key the device card reads. Needed since
        commissioning itself seeds the reachability of a freshly
        commissioned device (`api/devices.py`) - this service never
        actually commissions anything (no Matter client), but still has
        to fully satisfy `RuntimeValues`."""
        self._values[f"d{device_id}_online"] = online

    def add_observer(self, callback: Callable[[str, object], None]) -> None:
        return None

    def remove_observer(self, callback: Callable[[str, object], None]) -> None:
        return None

    async def resend_all(self) -> int:
        return 0


async def _invoke(call: MatterCall) -> None:
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store-path",
        type=Path,
        default=None,
        help="Database file (default: a fixed file in the temp directory).",
    )
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Four devices with English names, password and bridge settings "
            "prefilled, database fresh on every start - for the README screenshots."
        ),
    )
    return parser.parse_args()


def _ensure_devices(store: Store) -> list[int]:
    if store.devices():
        return [device.id for device in store.devices()]

    plug = _load_snapshot("ikea_grillplats_plug.json")
    plug_id = store.register_device(plug)
    store.register_signals(plug_id, plug)
    store.register_commands(plug_id, extract_commands(plug), plug.node_id)
    store.rename_device(plug_id, "Living room plug")

    button = _load_snapshot("ikea_bilresa_button.json")
    button_id = store.register_device(button)
    store.register_signals(button_id, button)
    store.register_commands(button_id, extract_commands(button), button.node_id)
    store.rename_device(button_id, "Hallway button")

    return [plug_id, button_id]


# Plausible analog values per unit, for `_plausible_value` below - a unit
# alone already fixes the value, except for "%" and "kWh", which measure
# quite different things depending on the signal (brightness vs. battery
# level, import vs. export, saturation). There the key suffix decides
# additionally: `d<id>_<endpoint>_<slug>` is the normal case from
# `Store._assign_key`, an element-id suffix appended due to a collision
# doesn't bother `endswith` below, it then simply falls back to the old
# placeholder value. "°" and "mired" each occur only once in
# `clusters.yaml` (hue and colour temperature respectively), so they need
# no key-based distinction like "%".
_UNIT_VALUES: dict[str, float] = {
    "V": 230.0,  # mains voltage
    "A": 0.4,  # current draw of a small device
    "kW": 0.092,  # ~92 W, matches 230 V * 0.4 A
    "°": 35.0,  # hue - warm orange
    "mired": 370.0,  # colour temperature, ~2700 K (warm white)
}


def _plausible_value(signal: StoredSignal) -> float | None:
    """A made-up but unit-appropriate value for an analog signal - see
    review: 12.4 kW "power" for a plug looked like a placeholder, not a
    demo. Anything not recognized here keeps the old placeholder value.

    Special case colour mode (`colormode`, cluster 768 attribute 8, see
    `clusters.yaml`): an enumeration, not a physical quantity - so there
    is no made-up fractional value for it (the same review finding: 12.4
    as "colour mode" looked broken, not like a demo). `None` leaves the
    signal unset, `_seed_values` below then sets no value for it - the
    device card shows the same neutral dash as for
    `VendorName`/`ProductName`."""
    if signal.unit in _UNIT_VALUES:
        return _UNIT_VALUES[signal.unit]
    if signal.unit == "kWh" and signal.key.endswith("_energy_imported"):
        return 41.7
    if signal.unit == "%" and signal.key.endswith("_level"):
        return 60.0
    if signal.unit == "%" and signal.key.endswith("_saturation"):
        return 80.0
    if signal.unit == "" and signal.key.endswith("_colormode"):
        return None
    return 12.4


def _seed_values(store: Store, device_ids: list[int]) -> dict[str, float | bool]:
    values: dict[str, float | bool] = {}
    for device_id in device_ids:
        values[f"d{device_id}_online"] = True
        for signal in store.signals(device_id):
            if not signal.functional:
                continue
            if signal.exportability == Exportability.DIGITAL:
                values[signal.key] = True
            elif signal.exportability == Exportability.ANALOG:
                value = _plausible_value(signal)
                if value is not None:
                    values[signal.key] = value
    return values


def main() -> None:
    args = _parse_args()

    # Dedicated database file for demo operation, freshly created on every
    # start: only that way does the same call twice produce the same
    # screenshots. Normal development use keeps its existing data.
    default_name = "loxmatter-demo-web.sqlite" if args.demo else "loxmatter-dev-web.sqlite"
    store_path = args.store_path or Path(tempfile.gettempdir()) / default_name
    if args.demo and args.store_path is None:
        store_path.unlink(missing_ok=True)

    store = Store(store_path)
    if args.demo:
        store.auth.reset_password(hash_password(DEMO_PASSWORD))
        store.settings.save(bridge_ip="192.168.1.50", udp_port=7000, listen_port=8080)
        # Same treatment as `exported_at` above and for the same reason:
        # `BridgeSettingsStore.save` sets `saved_at` to now, which is
        # correct in production but changes the screenshot on every run.
        store._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("bridge_settings_saved_at", DEMO_TIMESTAMP),
        )
        store._db.commit()
        device_ids = _ensure_demo_devices(store)
    else:
        device_ids = _ensure_devices(store)

    values = _seed_values(store, device_ids)
    runtime = _SeededRuntime(values)
    app = build_app(store, _invoke, runtime)
    print(f"Database: {store_path}")
    print(f"WebUI: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
