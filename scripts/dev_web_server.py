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
Bestand wiederzuverwenden.

`--update-dir` (added for the updater's web UI card, design "Applying
updates through the web UI", 2026-09-08): `build_app`'s own default for
this parameter is `/data/update`, which matches the production volume
mount but does not exist on a developer's machine - so without this flag
the update card had no directory to read from at all and permanently
showed "no updater", regardless of what a developer tried to put in
`state.json`. This flag gives it a real, writable location instead, the
same shape as `--store-path` above: pass your own path to drive the
card's four states by hand (drop a `state.json`/`log.txt` there between
runs), or omit it for a default directory under the temp dir that this
script creates for you. `--demo` seeds that default directory itself
(unless you also override it) with a completed update and switches
update checking off, for the same reason `--demo` already freezes
`exported_at`/`bridge_settings_saved_at` above: a screenshot of this
card must not depend on the wall clock or a live GitHub response, or two
runs would never produce the same picture."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
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

# Fester Zeitpunkt fuer alles, was im Demo-Modus einen Zeitstempel bekommt.
# Ohne ihn trugen `settings.png` und `export.png` die Wanduhr
# des jeweiligen Laufs, und `capture_screenshots.py` erzeugte bei jedem
# Aufruf neue Bilddateien, die sich einzig in dieser Uhrzeit unterschieden -
# rund 700 KB Binaerrauschen pro Lauf, in dem eine echte Layout-Aenderung
# untergegangen waere. Der Wert selbst ist beliebig, nur eben konstant; er
# liegt bewusst weit in der Vergangenheit, damit "zuletzt exportiert" in der
# Oberflaeche nicht wie "gerade eben" aussieht.
DEMO_TIMESTAMP = "2026-01-15T09:30:00+00:00"

# Reihenfolge bestimmt die Reihenfolge in der Geraeteliste - die Steckdose
# zuerst, weil ihre Signalliste den Unterschied funktional/Experte am besten
# zeigt (ueber hundert Signale, davon eine Handvoll funktional).
# Der dritte Eintrag ist der Raum (Entwurf Geraete-Tab, 2026-09-05). Ohne
# ihn blieben alle vier Geraete unter "No room", und die Raumleiste zeigte
# sich gar nicht - sie erscheint erst, sobald mindestens ein Geraet einen
# Raum traegt. Der Screenshot haette dann ausgerechnet das nicht gezeigt,
# wofuer die Ansicht umgebaut wurde.
#
# Die Kueche bekommt bewusst ZWEI Geraete: nur so ist an einer Gruppe
# ablesbar, dass innerhalb eines Raums nach Kategorie sortiert wird - die
# Leuchte (Rang 0) steht vor der Steckdose (Rang 1), unabhaengig von der
# Reihenfolge in dieser Liste.
DEMO_DEVICES = [
    ("ikea_grillplats_plug.json", "Coffee machine", "Kitchen"),
    ("example_light.json", "Living room lamp", "Living room"),
    ("ikea_kajplats_cws_lamp.json", "Kitchen spots", "Kitchen"),
    ("ikea_bilresa_button.json", "Hallway button", "Hallway"),
]


def _ensure_demo_devices(store: Store) -> list[int]:
    """Wie `_ensure_devices`, aber vier Geraete mit englischen Namen: die
    README-Screenshots zeigen eine englische Oberflaeche, deutsche
    Geraetenamen darin saehen nach Versehen aus."""
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

    # Ein Geraet gilt als bereits exportiert, damit die Export-Vorschau beide
    # Faelle nebeneinander zeigt statt vier gleich aussehender Zeilen.
    store.mark_exported(device_ids[0])
    # ... aber mit fester Uhrzeit statt "jetzt", siehe DEMO_TIMESTAMP. Der
    # Store schreibt bewusst immer `now_iso()` - das ist im Betrieb richtig
    # und soll dort nicht konfigurierbar werden, nur damit ein Demo-Modus
    # existiert. Deshalb wird hier nachtraeglich ueberschrieben statt eine
    # Naht in die Produktionsklasse zu schneiden.
    store._db.execute(
        "UPDATE device SET exported_at = ? WHERE id = ?", (DEMO_TIMESTAMP, device_ids[0])
    )
    store._db.commit()
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
    parser.add_argument(
        "--update-dir",
        type=Path,
        default=None,
        help=(
            "Directory for the update sidecar's state.json/log.txt (default: a "
            "directory under the temp dir, NOT build_app's own production default "
            "/data/update, which does not exist here). --demo seeds this directory "
            "with a finished update unless you also pass this flag yourself."
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


def _seed_demo_update_dir(update_dir: Path) -> None:
    """Writes a `state.json` for a completed update - the one state of
    the four (design "Applying updates through the web UI", 2026-09-08,
    section 9) that needs nothing beyond this one file: no live job to
    poll, and no dependence on `GET /api/update/check`'s real GitHub
    round trip, which the other three depend on and which would make a
    screenshot non-reproducible run to run (see this module's docstring).

    `updater_seen_at` is deliberately `datetime.now(UTC)`, not a frozen
    constant like `DEMO_TIMESTAMP` above: `update.updater_present` treats
    a heartbeat older than 30 seconds as "nobody is reading this volume
    anymore" (see `update.py`'s own `_MAX_SILENT_SECONDS`), so a fixed
    past timestamp would make the card fall back to "no updater" the
    moment it went stale. Nothing in the interface displays this value -
    only `updater_present`'s boolean depends on it - so a fresh timestamp
    on every run costs the reproducibility nothing."""
    update_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "id": "demo-update-job",
        "phase": "done",
        "from": "0.2.0",
        "to": "0.3.0",
        "error": None,
        "rolled_back": False,
        "healthy": True,
        "updater_seen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (update_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def main() -> None:
    args = _parse_args()

    # Eigene Datenbankdatei fuer den Demo-Betrieb, und die faellt bei jedem
    # Start neu an: nur so entstehen aus demselben Aufruf zweimal dieselben
    # Screenshots. Der normale Entwicklungsbetrieb behaelt seinen Bestand.
    default_name = "loxmatter-demo-web.sqlite" if args.demo else "loxmatter-dev-web.sqlite"
    store_path = args.store_path or Path(tempfile.gettempdir()) / default_name
    if args.demo and args.store_path is None:
        store_path.unlink(missing_ok=True)

    # Same "own default path, wiped only when that default is actually
    # used" shape as `store_path` above - an explicitly passed
    # `--update-dir` is the developer taking manual control of the
    # directory's contents (to drive a specific one of the four states by
    # hand), and `--demo` must not override files it did not put there
    # itself.
    default_update_dir_name = "loxmatter-demo-update" if args.demo else "loxmatter-dev-update"
    update_dir = args.update_dir or Path(tempfile.gettempdir()) / default_update_dir_name
    if args.demo and args.update_dir is None:
        shutil.rmtree(update_dir, ignore_errors=True)
    update_dir.mkdir(parents=True, exist_ok=True)

    store = Store(store_path)
    if args.demo:
        store.auth.reset_password(hash_password(DEMO_PASSWORD))
        store.settings.save(bridge_ip="192.168.1.50", udp_port=7000, listen_port=8080)
        # Dieselbe Behandlung wie bei `exported_at` oben und aus demselben
        # Grund: `BridgeSettingsStore.save` setzt `saved_at` auf jetzt, was im
        # Betrieb stimmt, den Screenshot aber bei jedem Lauf veraendert.
        store._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("bridge_settings_saved_at", DEMO_TIMESTAMP),
        )
        store._db.commit()
        # Checking against the real GitHub API would make the "up to
        # date"/"update available" line depend on this repository's
        # actual release state and this machine's network access at
        # capture time - exactly the kind of run-to-run noise this
        # script's demo mode otherwise refuses to introduce (see
        # DEMO_TIMESTAMP above). Switched off, that line always reads
        # "the search for updates is switched off", and the seeded
        # `state.json` below is the only thing the update card renders.
        store.update_settings.set_check_enabled(False)
        if args.update_dir is None:
            _seed_demo_update_dir(update_dir)
        device_ids = _ensure_demo_devices(store)
    else:
        device_ids = _ensure_devices(store)

    values = _seed_values(store, device_ids)
    runtime = _SeededRuntime(values)
    app = build_app(store, _invoke, runtime, update_dir=update_dir)
    print(f"Datenbank: {store_path}")
    print(f"Update directory: {update_dir}")
    print(f"WebUI: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
