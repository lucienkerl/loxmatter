"""Gemeinsame Fixtures fuer die WebUI-API-Tests (Phase 5).

Jede Task dieser Phase, die einen `httpx2`-Client gegen `build_app` aufbaut,
braucht dieselben drei Dinge: einen Invoker, der nie wirklich ein
Matter-Kommando verschickt, eine `Runtime`, die keinen echten UDP-Sender
braucht, und einen Matter-Client, der ohne Netzwerk auskommt. `no_invoke`,
`fake_runtime` und `fake_client` sind dafuer als eigenstaendige
`@pytest.fixture`-Funktionen gebaut, nicht als Modul-Funktionen zum manuellen
Importieren: Pytest liefert Wiederverwendbarkeit ueber die eingebaute
Fixture-Vererbung kostenlos - jede Testdatei unter `tests/api/` bekommt sie
automatisch als Parameter, ganz ohne Import.

`load_snapshot` ist die eine Ausnahme: eine Fixture kann keinen Dateinamen
entgegennehmen, deshalb bleibt sie eine gewoehnliche Funktion, importiert per
`from conftest import load_snapshot` - das funktioniert, weil Pytest das
Verzeichnis dieser Datei (`tests/api/`, ohne `__init__.py`) beim Einlesen von
Testdateien bereits vorn in `sys.path` einreiht (siehe restliche Testsuite,
die ebenfalls ohne `__init__.py`-Pakete auskommt).

Erweiterung fuer spaetere Tasks dieser Phase: `fake_runtime` nimmt bereits
`store` entgegen wie die echte `Runtime`, und `FakeMatterClient` sammelt
seine Aufrufe in Listen wie `FakeUpstream` in
`tests/matter/test_client_commissioning.py` - fuer einen Test, der eine
Fehlschlag-Simulation braucht, reicht `fake_client.fail_commission_with =
CommissioningError(...)` vor dem Aufruf zu setzen, ganz ohne diese Datei
anzufassen. Ein Taster-Geraet laedt sich ueber `load_snapshot
("ikea_bilresa_button.json")`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.commands.translate import MatterCall
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    """Laedt ein aufgezeichnetes Geraet aus `tests/fixtures/nodes/`."""
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def no_invoke():
    """Ein Invoker, der `build_app` erfuellt, aber nie wirklich gebraucht
    wird - die Geraete-API loest keine `/cmd`-Aufrufe aus. Ruft ein Test ihn
    doch auf, tut er nichts, statt gegen ein echtes Geraet zu senden."""

    async def _invoke(call: MatterCall) -> None:
        return None

    return _invoke


class FakeRuntime:
    """Erfuellt `api.devices.RuntimeValues`, ohne einen UdpSender oder eine
    Matter-Subscription aufzubauen - fuer Tests, die nur die Geraete-API
    pruefen wollen. Die volle `Runtime` (Sender, Impulse, Heartbeat) hat
    ihre eigene Testsuite unter `tests/loxone/test_runtime.py`.

    `store` wird entgegengenommen, aber (noch) nicht benutzt - allein damit
    die Fabrik dieselbe Form wie `Runtime(store, sender)` hat, falls eine
    spaetere Task hier doch einmal nachschlagen muss."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._values: dict[str, float | bool] = {}

    def seed(self, key: str, value: float | bool) -> None:
        """Traegt einen Wert ein, als haette eine Subscription ihn gerade gemeldet."""
        self._values[key] = value

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}


@pytest.fixture
def fake_runtime():
    """Fabrik statt fertigem Objekt: der Store steht erst innerhalb des
    jeweiligen Tests fest (siehe `api`-Fixture in `test_devices.py`)."""
    return FakeRuntime


class FakeMatterClient:
    """Erfuellt genau die drei `BridgeMatterClient`-Methoden, die die
    Geraete-API aufruft: Einlernen, Entfernen, Thread-Datensatz. Dasselbe
    Aufzeichnungs-Muster wie `FakeUpstream` in
    `tests/matter/test_client_commissioning.py`, nur auf der Ebene von
    `BridgeMatterClient` statt seines `session_factory`-Seams - die
    Geraete-API ruft `BridgeMatterClient` direkt auf, nicht dessen Upstream.
    """

    def __init__(self) -> None:
        self.commissioned: list[str] = []
        self.removed: list[int] = []
        self.datasets: list[str] = []
        self.fail_commission_with: Exception | None = None
        self.fail_remove_with: Exception | None = None
        self._next_node_id = 100

    async def commission_with_code(self, code: str) -> NodeSnapshot:
        if self.fail_commission_with is not None:
            raise self.fail_commission_with
        self.commissioned.append(code)
        node_id = self._next_node_id
        self._next_node_id += 1
        return NodeSnapshot(
            node_id=node_id,
            vendor_name="Fake",
            product_name="Geraet",
            unique_id=f"fake-{node_id}",
            attributes={},
        )

    async def remove_node(self, node_id: int) -> None:
        if self.fail_remove_with is not None:
            raise self.fail_remove_with
        self.removed.append(node_id)

    async def set_thread_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)


@pytest.fixture
def fake_client():
    return FakeMatterClient()
