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

"""Startet die WebUI mit zwei Beispielgeraeten, ohne matter-server - fuer die
manuelle Ansicht der Geraete-Dashboard-Aenderungen im Browser (siehe
docs/superpowers/plans/2026-09-03-geraete-dashboard-und-export.md, Task 4).

Aufruf: uv run python scripts/dev_web_server.py
Danach: http://127.0.0.1:8420 oeffnen, ein beliebiges Passwort vergeben
(Ersteinrichtung, gilt nur fuer diesen Testlauf).

Die Datenbank liegt in einer festen Datei im Temp-Verzeichnis - ein zweiter
Lauf findet denselben Bestand wieder, statt jedes Mal neu einzulernen."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import uvicorn

from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.profiles.table import Exportability

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "nodes"


def _load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


class _SeededRuntime:
    """Erfuellt `api.devices.RuntimeValues` mit ein paar erfundenen, aber
    plausiblen Werten - genug, damit die Geraetekarten nicht nur "-" zeigen.
    Kein Ersatz fuer `Runtime`: es gibt keine Live-Verbindung, die Werte
    stehen fest, bis dieser Prozess neu startet."""

    def __init__(self, values: dict[str, float | bool]) -> None:
        self._values = values

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}


async def _invoke(call: MatterCall) -> None:
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store-path",
        type=Path,
        default=Path(tempfile.gettempdir()) / "loxmatter-dev-web.sqlite",
        help="Datenbankdatei (Default: eine feste Datei im Temp-Verzeichnis).",
    )
    parser.add_argument("--port", type=int, default=8420)
    return parser.parse_args()


def _ensure_devices(store: Store) -> list[int]:
    if store.devices():
        return [device.id for device in store.devices()]

    plug = _load_snapshot("ikea_grillplats_plug.json")
    plug_id = store.register_device(plug)
    store.register_signals(plug_id, plug)
    store.register_commands(plug_id, extract_commands(plug), plug.node_id)
    store.rename_device(plug_id, "Steckdose Wohnzimmer")

    button = _load_snapshot("ikea_bilresa_button.json")
    button_id = store.register_device(button)
    store.register_signals(button_id, button)
    store.register_commands(button_id, extract_commands(button), button.node_id)
    store.rename_device(button_id, "Taster Flur")

    return [plug_id, button_id]


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
                values[signal.key] = 12.4
    return values


def main() -> None:
    args = _parse_args()
    store = Store(args.store_path)
    device_ids = _ensure_devices(store)
    values = _seed_values(store, device_ids)

    runtime = _SeededRuntime(values)
    app = build_app(store, _invoke, runtime)
    print(f"Datenbank: {args.store_path}")
    print(f"WebUI: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
