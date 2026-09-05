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
Lauf findet denselben Bestand wieder, statt jedes Mal neu einzulernen.

Mit `--demo` startet stattdessen der Modus fuer die README-Screenshots: vier
Geraete mit englischen Namen, Passwort und Bridge-Einstellungen bereits
vorbelegt, und die Datenbank wird bei jedem Start frisch angelegt, statt den
Bestand wiederzuverwenden."""

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

# Reihenfolge bestimmt die Reihenfolge in der Geraeteliste - die Steckdose
# zuerst, weil ihre Signalliste den Unterschied funktional/Experte am besten
# zeigt (ueber hundert Signale, davon eine Handvoll funktional).
DEMO_DEVICES = [
    ("ikea_grillplats_plug.json", "Coffee machine"),
    ("example_light.json", "Living room lamp"),
    ("synthetic_color_light.json", "Kitchen spots"),
    ("ikea_bilresa_button.json", "Hallway button"),
]


def _ensure_demo_devices(store: Store) -> list[int]:
    """Wie `_ensure_devices`, aber vier Geraete mit englischen Namen: die
    README-Screenshots zeigen eine englische Oberflaeche, deutsche
    Geraetenamen darin saehen nach Versehen aus."""
    if store.devices():
        return [device.id for device in store.devices()]

    device_ids: list[int] = []
    for filename, label in DEMO_DEVICES:
        snapshot = _load_snapshot(filename)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        store.rename_device(device_id, label)
        device_ids.append(device_id)

    # Ein Geraet gilt als bereits exportiert, damit die Export-Vorschau beide
    # Faelle nebeneinander zeigt statt vier gleich aussehender Zeilen.
    store.mark_exported(device_ids[0])
    return device_ids


def _load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


class _SeededRuntime:
    """Erfuellt `loxone.server._RuntimeDependency` - alles, was `build_app`
    selbst und die Router, denen es `runtime` weiterreicht, brauchen:
    `api.devices.RuntimeValues.last_values_for` mit ein paar erfundenen,
    aber plausiblen Werten (genug, damit die Geraetekarten nicht nur "-"
    zeigen), `api.live.ObservableRuntime.add_observer`/`remove_observer`
    als No-Ops (`/api/live` ruft sie bei jedem Verbindungsaufbau bzw.
    -abbau auf, egal ob dieser Dienst je einen Wert live nachliefert), und
    `resend_all` als No-Op fuer `/resync`. Kein Ersatz fuer `Runtime`: es
    gibt keine echte Live-Verbindung, die gesetzten Werte stehen fest, bis
    dieser Prozess neu startet - ein Beobachter, der hier angemeldet wird,
    bekommt schlicht nie eine Benachrichtigung, und `/resync` verschickt
    nichts."""

    def __init__(self, values: dict[str, float | bool]) -> None:
        self._values = values

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}

    async def set_online(self, device_id: int, online: bool) -> None:
        """Wie `Runtime.set_online`, nur ohne UDP-Versand: haelt den Wert
        unter demselben Schluessel, den die Geraetekarte liest. Gebraucht,
        seit das Einlernen die Erreichbarkeit eines frisch eingelernten
        Geraets selbst saeet (`api/devices.py`) - dieser Dienst lernt zwar
        nie etwas ein (kein Matter-Client), muss `RuntimeValues` aber
        vollstaendig erfuellen."""
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
        help="Datenbankdatei (Default: eine feste Datei im Temp-Verzeichnis).",
    )
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Vier Geraete mit englischen Namen, Passwort und Bridge-Einstellungen "
            "vorbelegt, Datenbank bei jedem Start frisch - fuer die README-Screenshots."
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
    store.rename_device(plug_id, "Steckdose Wohnzimmer")

    button = _load_snapshot("ikea_bilresa_button.json")
    button_id = store.register_device(button)
    store.register_signals(button_id, button)
    store.register_commands(button_id, extract_commands(button), button.node_id)
    store.rename_device(button_id, "Taster Flur")

    return [plug_id, button_id]


# Plausible Analogwerte je Einheit, fuer `_plausible_value` unten - eine
# Einheit allein legt den Wert schon fest, ausser bei "%" und "kWh", die je
# nach Signal ganz Verschiedenes messen (Helligkeit vs. Batteriestand,
# Bezug vs. Einspeisung, Saettigung). Dort entscheidet zusaetzlich das
# Schluesselende: `d<id>_<endpoint>_<slug>` ist der Normalfall aus
# `Store._assign_key`, ein kollisionsbedingt angehaengtes Element-Id-Suffix
# stoert `endswith` unten nicht, es faellt dann einfach auf den alten
# Platzhalterwert zurueck. "°" und "mired" kommen in `clusters.yaml` nur je
# einmal vor (Farbton bzw. Farbtemperatur), brauchen also keine
# Schluessel-Unterscheidung wie "%".
_UNIT_VALUES: dict[str, float] = {
    "V": 230.0,  # Netzspannung
    "A": 0.4,  # Stromaufnahme eines kleinen Geraets
    "kW": 0.092,  # ~92 W, passt zu 230 V * 0.4 A
    "°": 35.0,  # Farbton (Hue) - warmes Orange
    "mired": 370.0,  # Farbtemperatur, ~2700 K (warmweiss)
}


def _plausible_value(signal: StoredSignal) -> float | None:
    """Ein erfundener, aber zur Einheit passender Wert fuer ein Analogsignal -
    siehe Review: 12,4 kW "Leistung" fuer eine Steckdose sah nach
    Platzhalter aus, nicht nach Demo. Alles, was hier nicht erkannt wird,
    behaelt den alten Platzhalterwert.

    Sonderfall Farbmodus (`colormode`, Cluster 768 Attribut 8, siehe
    `clusters.yaml`): eine Aufzaehlung, keine physikalische Groesse - dafuer
    gibt es keinen erfundenen Bruchwert (derselbe Review-Fund: 12,4 als
    "Farbmodus" sah kaputt aus, nicht nach Demo). `None` laesst das Signal
    unbesetzt, `_seed_values` unten setzt dafuer keinen Wert - die
    Geraetekarte zeigt denselben neutralen Strich wie bei
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

    # Eigene Datenbankdatei fuer den Demo-Betrieb, und die faellt bei jedem
    # Start neu an: nur so entstehen aus demselben Aufruf zweimal dieselben
    # Screenshots. Der normale Entwicklungsbetrieb behaelt seinen Bestand.
    default_name = "loxmatter-demo-web.sqlite" if args.demo else "loxmatter-dev-web.sqlite"
    store_path = args.store_path or Path(tempfile.gettempdir()) / default_name
    if args.demo and args.store_path is None:
        store_path.unlink(missing_ok=True)

    store = Store(store_path)
    if args.demo:
        store.auth.reset_password(hash_password(DEMO_PASSWORD))
        store.settings.save(bridge_ip="192.168.1.50", udp_port=7000, listen_port=8080)
        device_ids = _ensure_demo_devices(store)
    else:
        device_ids = _ensure_devices(store)

    values = _seed_values(store, device_ids)
    runtime = _SeededRuntime(values)
    app = build_app(store, _invoke, runtime)
    print(f"Datenbank: {store_path}")
    print(f"WebUI: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
