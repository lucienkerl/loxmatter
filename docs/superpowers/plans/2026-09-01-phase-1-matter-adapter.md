# Phase 1: Matter-Adapter und Signal-Extraktion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine CLI, die für ein reales Matter-Gerät jedes Attribut und jedes Event auflistet und meldet, was sie *nicht* zerlegen konnte — damit die Grundannahme aus Spec 3.5 belegt oder widerlegt ist.

**Architecture:** Ein Paket `loxmatter.matter` mit vier Modulen ohne gegenseitige Zyklen: `paths` (Pfad-Parsing, reine Funktionen), `models` (unveränderliche Datenklassen), `discovery` (Zerlegung eines Node-Abbilds in Signale, rein), `client` (der einzige Teil mit I/O, dünne Hülle um `matter_server.client.MatterClient`). Die Zerlegung ist bewusst von der Verbindung getrennt: sie arbeitet auf einem JSON-Abbild und ist damit gegen eingecheckte Fixtures echter Geräte testbar, ohne Hardware und ohne Netz.

**Tech Stack:** Python 3.12, `uv` als Paketmanager, `python-matter-server>=8.1.2`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `typer` für die CLI.

## Global Constraints

Aus der Spec, gelten für jede Task:

- **Tests laufen ohne Hardware und ohne Netzwerkzugriff.** Ein Test, der ein echtes Gerät braucht, wird übersprungen und verrottet (Spec 10.1).
- **Generisch, nicht kuratiert.** Jedes lesbare Attribut und jedes Event wird zum Signal. Unbekannte Cluster werden roh durchgereicht, nie verworfen (Spec 3.5).
- **Deutsch in Fehlermeldungen und Logs**, Englisch in Bezeichnern und Commit-Präfixen.
- **Alle Datenklassen unveränderlich** (`frozen=True`), solange kein Grund dagegen spricht.
- Zieleinheiten und Formatierung (kW, 6 Nachkommastellen) sind **Phase 3**, nicht hier. Diese Phase liefert Rohwerte.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `pyproject.toml` | Projekt, Abhängigkeiten, Tool-Konfiguration |
| `src/loxmatter/matter/paths.py` | Attributpfade parsen, globale Attribut-IDs kennen. Reine Funktionen |
| `src/loxmatter/matter/models.py` | `SignalKind`, `SignalRef`, `NodeSnapshot`. Nur Daten |
| `src/loxmatter/matter/discovery.py` | `extract_signals`, `find_unreported_attributes`. Rein, kein I/O |
| `src/loxmatter/matter/client.py` | Verbindung zu matter-server, liefert `NodeSnapshot` |
| `src/loxmatter/cli.py` | `loxmatter inspect` |
| `scripts/record_node.py` | Node-Abbild von echter Hardware als Fixture speichern |
| `tests/fixtures/nodes/*.json` | Eingecheckte Abbilder echter Geräte |
| `deploy/testhost/` | Compose-Datei und Protokoll der Testumgebung (Task 6, ursprünglich `deploy/testvm/` — Umzug auf den Raspberry Pi wegen fehlendem Bluetooth auf der VM, siehe README dort) |

---

### Task 1: Projektgerüst und Pfad-Parsing

Das Gerüst wird hier eingerichtet, weil `paths.py` das erste Modul ist, das es braucht.

**Files:**
- Create: `pyproject.toml`
- Create: `src/loxmatter/__init__.py`
- Create: `src/loxmatter/matter/__init__.py`
- Create: `src/loxmatter/matter/paths.py`
- Test: `tests/matter/test_paths.py`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `parse_attribute_path(path: str) -> tuple[int, int, int]` — gibt `(endpoint, cluster_id, attribute_id)`, wirft `ValueError` bei allem anderen
  - `GLOBAL_ATTRIBUTE_IDS: frozenset[int]`
  - `ATTRIBUTE_LIST_ID: int` (`0xFFFB`), `EVENT_LIST_ID: int` (`0xFFFA`)

- [ ] **Step 1: Projektgerüst anlegen**

`pyproject.toml`:

```toml
[project]
name = "loxmatter"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "python-matter-server>=8.1.2",
    "typer>=0.12",
]

[project.scripts]
loxmatter = "loxmatter.cli:app"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "mypy>=1.11"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true
files = ["src"]
```

Dann:

```bash
mkdir -p src/loxmatter/matter tests/matter tests/fixtures/nodes scripts
touch src/loxmatter/__init__.py src/loxmatter/matter/__init__.py
uv sync
```

- [ ] **Step 2: Write the failing test**

`tests/matter/test_paths.py`:

```python
import pytest

from loxmatter.matter.paths import (
    ATTRIBUTE_LIST_ID,
    EVENT_LIST_ID,
    GLOBAL_ATTRIBUTE_IDS,
    parse_attribute_path,
)


def test_parses_endpoint_cluster_attribute():
    assert parse_attribute_path("1/6/0") == (1, 6, 0)


def test_parses_multi_digit_values():
    assert parse_attribute_path("2/1030/65531") == (2, 1030, 65531)


@pytest.mark.parametrize("bad", ["1/6", "1/6/0/9", "", "a/6/0", "1//0"])
def test_rejects_malformed_paths(bad):
    with pytest.raises(ValueError, match="Attributpfad"):
        parse_attribute_path(bad)


def test_global_attribute_ids_cover_the_matter_reserved_range():
    assert ATTRIBUTE_LIST_ID == 0xFFFB
    assert EVENT_LIST_ID == 0xFFFA
    assert GLOBAL_ATTRIBUTE_IDS == {0xFFF8, 0xFFF9, 0xFFFA, 0xFFFB, 0xFFFC, 0xFFFD}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_paths.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.matter.paths'`

- [ ] **Step 4: Write minimal implementation**

`src/loxmatter/matter/paths.py`:

```python
"""Attributpfade von matter-server parsen.

matter-server adressiert Attribute als "<endpoint>/<cluster>/<attribute>",
z.B. "1/6/0" für OnOff.OnOff auf Endpoint 1.
"""

from __future__ import annotations

# Globale Attribute nach Matter-Spezifikation. Sie beschreiben das Gerät,
# statt einen Messwert zu tragen, und werden nicht zu Loxone-Signalen.
GENERATED_COMMAND_LIST_ID = 0xFFF8
ACCEPTED_COMMAND_LIST_ID = 0xFFF9
EVENT_LIST_ID = 0xFFFA
ATTRIBUTE_LIST_ID = 0xFFFB
FEATURE_MAP_ID = 0xFFFC
CLUSTER_REVISION_ID = 0xFFFD

GLOBAL_ATTRIBUTE_IDS: frozenset[int] = frozenset(
    {
        GENERATED_COMMAND_LIST_ID,
        ACCEPTED_COMMAND_LIST_ID,
        EVENT_LIST_ID,
        ATTRIBUTE_LIST_ID,
        FEATURE_MAP_ID,
        CLUSTER_REVISION_ID,
    }
)


def parse_attribute_path(path: str) -> tuple[int, int, int]:
    """Zerlegt "1/6/0" in (endpoint, cluster_id, attribute_id)."""
    parts = path.split("/")
    if len(parts) != 3:
        raise ValueError(f"unerwarteter Attributpfad: {path!r}")
    try:
        endpoint, cluster_id, attribute_id = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"unerwarteter Attributpfad: {path!r}") from exc
    return endpoint, cluster_id, attribute_id
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_paths.py -v`
Expected: PASS, 8 Tests

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/loxmatter tests/matter/test_paths.py
git commit -m "feat(matter): Projektgerüst und Attributpfad-Parsing"
```

---

### Task 2: Datenmodell für Signale

**Files:**
- Create: `src/loxmatter/matter/models.py`
- Test: `tests/matter/test_models.py`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `SignalKind` — `str`-Enum mit `ATTRIBUTE = "attribute"`, `EVENT = "event"`
  - `SignalRef(endpoint: int, cluster_id: int, element_id: int, kind: SignalKind)` — frozen, sortierbar, mit `.path -> str`
  - `NodeSnapshot(node_id: int, vendor_name: str, product_name: str, unique_id: str, attributes: dict[str, object])` — frozen, mit `.from_raw(node_id: int, raw: Mapping[str, object]) -> NodeSnapshot`

- [ ] **Step 1: Write the failing test**

`tests/matter/test_models.py`:

```python
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef


def test_signal_ref_renders_matter_path():
    ref = SignalRef(endpoint=1, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE)
    assert ref.path == "1/6/0"


def test_signal_refs_sort_by_endpoint_then_cluster_then_element():
    unsorted = [
        SignalRef(2, 6, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 1030, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 6, 16, SignalKind.ATTRIBUTE),
        SignalRef(1, 6, 0, SignalKind.ATTRIBUTE),
    ]
    assert [r.path for r in sorted(unsorted)] == ["1/6/0", "1/6/16", "1/1030/0", "2/6/0"]


def test_signal_ref_is_hashable_and_frozen():
    ref = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    assert len({ref, SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)}) == 1


def test_attribute_and_event_on_same_path_are_distinct():
    attribute = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    event = SignalRef(1, 6, 0, SignalKind.EVENT)
    assert attribute != event
    assert len({attribute, event}) == 2


def test_node_snapshot_reads_basic_information_cluster():
    raw = {
        "attributes": {
            "0/40/1": "IKEA of Sweden",
            "0/40/3": "TRADFRI bulb",
            "0/40/18": "ABC123",
            "1/6/0": True,
        }
    }
    snapshot = NodeSnapshot.from_raw(node_id=12, raw=raw)
    assert snapshot.node_id == 12
    assert snapshot.vendor_name == "IKEA of Sweden"
    assert snapshot.product_name == "TRADFRI bulb"
    assert snapshot.unique_id == "ABC123"
    assert snapshot.attributes["1/6/0"] is True


def test_node_snapshot_tolerates_missing_basic_information():
    snapshot = NodeSnapshot.from_raw(node_id=3, raw={"attributes": {"1/6/0": False}})
    assert snapshot.vendor_name == ""
    assert snapshot.product_name == ""
    assert snapshot.unique_id == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_models.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.matter.models'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/matter/models.py`:

```python
"""Unveränderliches Abbild dessen, was matter-server über ein Gerät weiß."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# BasicInformation-Cluster auf Endpoint 0.
_VENDOR_NAME_PATH = "0/40/1"
_PRODUCT_NAME_PATH = "0/40/3"
_UNIQUE_ID_PATH = "0/40/18"


class SignalKind(str, Enum):
    ATTRIBUTE = "attribute"
    EVENT = "event"


@dataclass(frozen=True, order=True)
class SignalRef:
    """Verweis auf genau eine Datenquelle eines Geräts.

    Attribut und Event können dieselben Zahlen tragen und sind trotzdem
    verschiedene Dinge — `kind` gehört deshalb zur Identität.
    """

    endpoint: int
    cluster_id: int
    element_id: int
    kind: SignalKind

    @property
    def path(self) -> str:
        return f"{self.endpoint}/{self.cluster_id}/{self.element_id}"


@dataclass(frozen=True)
class NodeSnapshot:
    node_id: int
    vendor_name: str
    product_name: str
    unique_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, node_id: int, raw: Mapping[str, Any]) -> NodeSnapshot:
        attributes: Mapping[str, Any] = raw.get("attributes") or {}

        def text(path: str) -> str:
            value = attributes.get(path)
            return value if isinstance(value, str) else ""

        return cls(
            node_id=node_id,
            vendor_name=text(_VENDOR_NAME_PATH),
            product_name=text(_PRODUCT_NAME_PATH),
            unique_id=text(_UNIQUE_ID_PATH),
            attributes=dict(attributes),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_models.py -v`
Expected: PASS, 6 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/matter/models.py tests/matter/test_models.py
git commit -m "feat(matter): Datenmodell für Signale und Node-Abbilder"
```

---

### Task 3: Zerlegung eines Node-Abbilds in Signale

Das Kernstück der Phase. `extract_signals` beantwortet „welche Werte hat dieses Gerät",
`find_unreported_attributes` beantwortet „und welche haben wir übersehen" — die zweite
Funktion ist der eigentliche Prüfstein für Spec 3.5.

**Files:**
- Create: `src/loxmatter/matter/discovery.py`
- Test: `tests/matter/test_discovery.py`

**Interfaces:**
- Consumes: `parse_attribute_path`, `GLOBAL_ATTRIBUTE_IDS`, `EVENT_LIST_ID`, `ATTRIBUTE_LIST_ID` aus Task 1; `SignalRef`, `SignalKind`, `NodeSnapshot` aus Task 2
- Produces:
  - `extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]` — sortiert, Attribute und Events
  - `find_unreported_attributes(snapshot: NodeSnapshot) -> list[SignalRef]` — Attribute, die das Gerät in seiner `AttributeList` nennt, für die aber kein Wert vorliegt
  - `find_unparsable_paths(snapshot: NodeSnapshot) -> list[str]` — Pfade, an denen das Parsen scheiterte

- [ ] **Step 1: Write the failing test**

`tests/matter/test_discovery.py`:

```python
from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef


def snapshot(attributes: dict[str, object]) -> NodeSnapshot:
    return NodeSnapshot.from_raw(node_id=1, raw={"attributes": attributes})


def test_every_non_global_attribute_becomes_a_signal():
    signals = extract_signals(snapshot({"1/6/0": True, "1/8/0": 254}))
    assert signals == [
        SignalRef(1, 6, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 8, 0, SignalKind.ATTRIBUTE),
    ]


def test_global_attributes_are_not_signals():
    signals = extract_signals(
        snapshot({"1/6/0": True, "1/6/65533": 6, "1/6/65532": 0, "1/6/65531": [0]})
    )
    assert signals == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]


def test_event_list_produces_event_signals():
    signals = extract_signals(snapshot({"1/59/65530": [0, 1, 2]}))
    assert signals == [
        SignalRef(1, 59, 0, SignalKind.EVENT),
        SignalRef(1, 59, 1, SignalKind.EVENT),
        SignalRef(1, 59, 2, SignalKind.EVENT),
    ]


def test_empty_or_absent_event_list_produces_nothing():
    assert extract_signals(snapshot({"1/59/65530": []})) == []
    assert extract_signals(snapshot({"1/59/65530": None})) == []


def test_unknown_cluster_is_still_extracted():
    """Spec 3.5: profiles/ ist Anreicherung, kein Gatekeeper."""
    signals = extract_signals(snapshot({"1/64999/7": 42}))
    assert signals == [SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)]


def test_signals_are_sorted_deterministically():
    signals = extract_signals(snapshot({"2/6/0": True, "1/1030/0": 1, "1/6/0": False}))
    assert [s.path for s in signals] == ["1/6/0", "1/1030/0", "2/6/0"]


def test_finds_attributes_the_device_claims_but_did_not_report():
    # AttributeList (65531) nennt 0 und 16, geliefert wurde nur 0.
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 16], "1/6/0": True}))
    assert missing == [SignalRef(1, 6, 16, SignalKind.ATTRIBUTE)]


def test_reports_nothing_missing_when_device_is_complete():
    assert find_unreported_attributes(snapshot({"1/6/65531": [0], "1/6/0": True})) == []


def test_global_attributes_are_not_counted_as_missing():
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 65533], "1/6/0": True}))
    assert missing == []


def test_unparsable_paths_are_collected_not_raised():
    snap = snapshot({"kaputt": 1, "1/6/0": True})
    assert find_unparsable_paths(snap) == ["kaputt"]
    assert extract_signals(snap) == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_discovery.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.matter.discovery'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/matter/discovery.py`:

```python
"""Zerlegt ein Node-Abbild in einzelne Signale.

Rein funktional und ohne I/O — arbeitet auf einem NodeSnapshot und ist damit
gegen eingecheckte Fixtures echter Geräte testbar.

Grundsatz aus Spec 3.5: hier wird nichts verworfen. Unbekannte Cluster werden
genauso zu Signalen wie bekannte; die Anreicherung um Namen und Skalierung
passiert später in profiles/.
"""

from __future__ import annotations

from collections.abc import Iterable

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.matter.paths import (
    ATTRIBUTE_LIST_ID,
    EVENT_LIST_ID,
    GLOBAL_ATTRIBUTE_IDS,
    parse_attribute_path,
)


def _parsed_paths(snapshot: NodeSnapshot) -> Iterable[tuple[int, int, int, object]]:
    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        yield endpoint, cluster_id, attribute_id, value


def _as_id_list(value: object) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    return [int(item) for item in value if isinstance(item, (int, float))]


def extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Jedes nicht-globale Attribut und jedes gelistete Event wird ein Signal."""
    signals: set[SignalRef] = set()

    for endpoint, cluster_id, attribute_id, value in _parsed_paths(snapshot):
        if attribute_id == EVENT_LIST_ID:
            for event_id in _as_id_list(value):
                signals.add(SignalRef(endpoint, cluster_id, event_id, SignalKind.EVENT))
            continue
        if attribute_id in GLOBAL_ATTRIBUTE_IDS:
            continue
        signals.add(SignalRef(endpoint, cluster_id, attribute_id, SignalKind.ATTRIBUTE))

    return sorted(signals)


def find_unreported_attributes(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Attribute, die das Gerät in seiner AttributeList nennt, aber nicht geliefert hat.

    Das ist der Prüfstein für Spec 3.5: eine nicht-leere Liste bedeutet, dass die
    generische Zerlegung Werte übersieht, die das Gerät eigentlich anbietet.
    """
    present: set[tuple[int, int, int]] = set()
    claimed: set[tuple[int, int, int]] = set()

    for endpoint, cluster_id, attribute_id, value in _parsed_paths(snapshot):
        present.add((endpoint, cluster_id, attribute_id))
        if attribute_id == ATTRIBUTE_LIST_ID:
            for claimed_id in _as_id_list(value):
                if claimed_id not in GLOBAL_ATTRIBUTE_IDS:
                    claimed.add((endpoint, cluster_id, claimed_id))

    return sorted(
        SignalRef(endpoint, cluster_id, attribute_id, SignalKind.ATTRIBUTE)
        for endpoint, cluster_id, attribute_id in claimed - present
    )


def find_unparsable_paths(snapshot: NodeSnapshot) -> list[str]:
    """Pfade, die nicht dem erwarteten Format entsprachen. Sollte leer sein."""
    broken: list[str] = []
    for path in snapshot.attributes:
        try:
            parse_attribute_path(path)
        except ValueError:
            broken.append(path)
    return sorted(broken)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_discovery.py -v`
Expected: PASS, 10 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/matter/discovery.py tests/matter/test_discovery.py
git commit -m "feat(matter): generische Zerlegung eines Node-Abbilds in Signale"
```

---

### Task 4: Verbindung zu matter-server

Die einzige Stelle mit I/O. Bewusst dünn: sie holt Node-Rohdaten und macht
`NodeSnapshot` daraus, mehr nicht. Alles Interessante ist in Task 3 schon getestet.

**Files:**
- Create: `src/loxmatter/matter/client.py`
- Test: `tests/matter/test_client.py`

**Interfaces:**
- Consumes: `NodeSnapshot` aus Task 2
- Produces:
  - `class BridgeMatterClient` mit `async def connect(self) -> None`, `async def disconnect(self) -> None`, `async def snapshots(self) -> list[NodeSnapshot]`, `async def snapshot(self, node_id: int) -> NodeSnapshot`
  - `MatterUnavailableError(RuntimeError)` — geworfen, wenn keine Verbindung besteht
  - Konstruktor: `BridgeMatterClient(url: str, session_factory: Callable[[Any], Any] | None = None, http_session_factory: Callable[[], Any] | None = None)` — `http_session_factory` baut die aiohttp-`ClientSession`, `session_factory` bekommt diese Session und baut daraus den Upstream-`MatterClient`. `BridgeMatterClient` erzeugt die Session selbst und schließt sie auch selbst wieder (in `disconnect()` und bei einem gescheiterten `connect()`) — `MatterClientConnection.disconnect()` aus python-matter-server schließt nur das Websocket, nicht die ihr übergebene Session.

- [ ] **Step 1: Write the failing test**

Die Tests fahren gegen Attrappen des Upstream-Clients und der aiohttp-Session
— kein Netz, kein Server. Die Attrappe der Session zählt ihre `close()`-Aufrufe,
damit die Tests das Leck aus der Praxis (Session wird nie geschlossen) auch
wirklich erkennen.

`tests/matter/test_client.py`:

```python
import asyncio

import pytest

from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError


class FakeNode:
    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.attributes = attributes


class FakeUpstream:
    """Steht für matter_server.client.MatterClient."""

    def __init__(
        self,
        nodes: list[FakeNode],
        fail_connect: bool = False,
        fail_disconnect: bool = False,
    ):
        self._nodes = nodes
        self.connected = False
        self.disconnect_calls = 0
        self._fail_connect = fail_connect
        self._fail_disconnect = fail_disconnect

    async def connect(self) -> None:
        if self._fail_connect:
            raise RuntimeError("Verbindung fehlgeschlagen")
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self._fail_disconnect:
            raise RuntimeError("Trennung fehlgeschlagen")
        self.connected = False

    def get_nodes(self) -> list[FakeNode]:
        return self._nodes


class FakeSession:
    """Steht für aiohttp.ClientSession — zählt, wie oft close() lief."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def make_client(
    nodes: list[FakeNode] | None = None,
    *,
    fail_connect: bool = False,
    fail_disconnect: bool = False,
) -> tuple[BridgeMatterClient, FakeSession]:
    """Baut einen BridgeMatterClient mit Attrappen für HTTP-Session und Upstream."""
    session = FakeSession()
    upstream = FakeUpstream(nodes or [], fail_connect=fail_connect, fail_disconnect=fail_disconnect)
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: session,
    )
    return bridge, session


@pytest.fixture
def client() -> BridgeMatterClient:
    bridge, _ = make_client(
        [
            FakeNode(12, {"0/40/1": "IKEA of Sweden", "1/6/0": True}),
            FakeNode(13, {"0/40/1": "IKEA of Sweden", "1/1026/0": 2150}),
        ]
    )
    return bridge


async def test_snapshots_requires_a_connection(client):
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await client.snapshots()


async def test_snapshots_maps_every_node(client):
    await client.connect()
    snapshots = await client.snapshots()
    assert [s.node_id for s in snapshots] == [12, 13]
    assert snapshots[0].vendor_name == "IKEA of Sweden"
    assert snapshots[1].attributes["1/1026/0"] == 2150


async def test_snapshot_selects_by_node_id(client):
    await client.connect()
    assert (await client.snapshot(13)).attributes["1/1026/0"] == 2150


async def test_snapshot_raises_for_unknown_node(client):
    await client.connect()
    with pytest.raises(MatterUnavailableError, match="unbekannter Node 99"):
        await client.snapshot(99)


async def test_disconnect_is_idempotent(client):
    await client.connect()
    await client.disconnect()
    await client.disconnect()
    with pytest.raises(MatterUnavailableError):
        await client.snapshots()


async def test_connect_disconnect_closes_session_exactly_once():
    """BridgeMatterClient erzeugt die Session selbst und muss sie wieder schließen."""
    bridge, session = make_client([FakeNode(1, {})])
    await bridge.connect()
    assert session.close_calls == 0
    await bridge.disconnect()
    assert session.close_calls == 1


async def test_disconnect_twice_closes_session_once_and_does_not_raise():
    bridge, session = make_client([FakeNode(1, {})])
    await bridge.connect()
    await bridge.disconnect()
    await bridge.disconnect()
    assert session.close_calls == 1


async def test_failed_connect_closes_session_and_allows_retry():
    """Ein scheiternder connect() darf die Session nicht leaken und muss einen
    späteren, erfolgreichen connect() zulassen."""
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    attempts = {"n": 0}

    def session_factory(_session: FakeSession) -> FakeUpstream:
        attempts["n"] += 1
        return FakeUpstream([FakeNode(1, {})], fail_connect=attempts["n"] == 1)

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    with pytest.raises(RuntimeError, match="Verbindung fehlgeschlagen"):
        await bridge.connect()

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()

    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert [s.node_id for s in snapshots] == [1]
    assert sessions[1].close_calls == 0


async def test_connect_twice_closes_previous_session_and_does_not_leak():
    """Ein zweiter connect() ohne dazwischenliegendes disconnect() darf die
    erste Session nicht unerreichbar hinterlassen — sie muss geschlossen
    werden, bevor die zweite Session entsteht."""
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    def session_factory(_session: FakeSession) -> FakeUpstream:
        return FakeUpstream([FakeNode(1, {})])

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    await bridge.connect()
    await bridge.connect()

    assert len(sessions) == 2
    assert sessions[0].close_calls == 1
    assert sessions[1].close_calls == 0
    snapshots = await bridge.snapshots()
    assert [s.node_id for s in snapshots] == [1]


async def test_disconnect_closes_session_even_if_upstream_disconnect_raises():
    """Wirft der Upstream in disconnect(), muss die Session trotzdem
    geschlossen und der Client danach als nicht verbunden erkennbar sein."""
    bridge, session = make_client([FakeNode(1, {})], fail_disconnect=True)
    await bridge.connect()

    with pytest.raises(RuntimeError, match="Trennung fehlgeschlagen"):
        await bridge.disconnect()

    assert session.close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()


async def test_connect_cancelled_closes_session_and_propagates_cancellation():
    """asyncio.CancelledError erbt von BaseException, nicht Exception — ein
    während des Verbindungsaufbaus abgebrochener connect() darf die Session
    trotzdem nicht leaken und muss den Abbruch weiterreichen."""
    session = FakeSession()

    class CancellingUpstream:
        async def connect(self) -> None:
            raise asyncio.CancelledError()

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: CancellingUpstream(),
        http_session_factory=lambda: session,
    )

    with pytest.raises(asyncio.CancelledError):
        await bridge.connect()

    assert session.close_calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_client.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.matter.client'`

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/matter/client.py`:

```python
"""Verbindung zu python-matter-server.

Bewusst dünn gehalten: holt Rohdaten und macht NodeSnapshots daraus. Die
Zerlegung in Signale passiert in discovery.py und ist dort ohne Netz getestet.

BridgeMatterClient erzeugt die aiohttp-ClientSession selbst und bleibt damit
ihr alleiniger Besitzer: MatterClientConnection.disconnect() aus
python-matter-server schließt nur das Websocket, nicht die Session, die ihr
übergeben wurde — laut aiohttp-Konvention muss das tun, wer die Session
erzeugt hat. Deshalb hält diese Klasse die Session-Referenz selbst und
schließt sie in disconnect() bzw. bei einem gescheiterten connect().
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loxmatter.matter.models import NodeSnapshot


class MatterUnavailableError(RuntimeError):
    """matter-server ist nicht verbunden oder kennt den gefragten Node nicht."""


class BridgeMatterClient:
    def __init__(
        self,
        url: str,
        session_factory: Callable[[Any], Any] | None = None,
        http_session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._url = url
        self._session_factory = session_factory or self._default_session_factory
        self._http_session_factory = http_session_factory or self._default_http_session_factory
        self._upstream: Any | None = None
        self._http_session: Any | None = None

    def _default_session_factory(self, session: Any) -> Any:
        # Lazy importiert, damit Tests matter_server nie laden müssen.
        from matter_server.client.client import MatterClient

        return MatterClient(self._url, session)

    @staticmethod
    def _default_http_session_factory() -> Any:
        # Lazy importiert, damit Tests aiohttp nie laden müssen.
        import aiohttp

        return aiohttp.ClientSession()

    async def connect(self) -> None:
        # Ein bereits verbundener Client wird bei erneutem connect() sauber
        # getrennt, bevor neu verbunden wird — sonst würde die alte, noch
        # offene Session beim Überschreiben von self._upstream/self._http_session
        # unerreichbar und nie geschlossen.
        if self._upstream is not None:
            await self.disconnect()
        http_session = self._http_session_factory()
        try:
            upstream = self._session_factory(http_session)
            await upstream.connect()
        except BaseException:
            # BaseException statt Exception: asyncio.CancelledError erbt von
            # BaseException, nicht von Exception. Ein während des Verbindungs-
            # aufbaus abgebrochenes connect() (z. B. durch asyncio.wait_for)
            # muss die Session trotzdem schließen und den Abbruch weiterreichen.
            await http_session.close()
            raise
        self._http_session = http_session
        self._upstream = upstream

    async def disconnect(self) -> None:
        if self._upstream is None:
            return
        upstream = self._upstream
        http_session = self._http_session
        # Felder vor dem await auf None setzen: so ist der Client sofort als
        # nicht verbunden erkennbar, auch wenn upstream.disconnect() unten
        # eine Ausnahme wirft — disconnect() bleibt idempotent und der
        # Objektzustand sauber, ganz gleich, wie die Trennung ausgeht.
        self._upstream = None
        self._http_session = None
        if http_session is None:
            # Invariante: Ist _upstream gesetzt, ist auch _http_session gesetzt
            # (beide werden nur gemeinsam in connect() gesetzt). Als expliziter
            # Fehler statt assert, damit die Prüfung auch unter `python -O`
            # greift.
            msg = "interner Fehler: _http_session fehlt trotz aktivem _upstream"
            raise RuntimeError(msg)
        try:
            await upstream.disconnect()
        finally:
            await http_session.close()

    def _require_upstream(self) -> Any:
        if self._upstream is None:
            raise MatterUnavailableError("nicht verbunden mit matter-server")
        return self._upstream

    async def snapshots(self) -> list[NodeSnapshot]:
        upstream = self._require_upstream()
        return [
            NodeSnapshot.from_raw(node.node_id, {"attributes": node.attributes})
            for node in upstream.get_nodes()
        ]

    async def snapshot(self, node_id: int) -> NodeSnapshot:
        for candidate in await self.snapshots():
            if candidate.node_id == node_id:
                return candidate
        raise MatterUnavailableError(f"unbekannter Node {node_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_client.py -v`
Expected: PASS, 11 Tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/matter/client.py tests/matter/test_client.py
git commit -m "feat(matter): Client für matter-server mit Snapshot-Abbildung"
```

---

### Task 5: CLI `loxmatter inspect`

Der sichtbare Ertrag der Phase. Arbeitet wahlweise gegen einen laufenden
matter-server oder gegen eine Fixture-Datei — Letzteres macht sie im Test
netzfrei benutzbar.

**Files:**
- Create: `src/loxmatter/cli.py`
- Test: `tests/test_cli.py`
- Create: `tests/fixtures/nodes/example_light.json`

**Interfaces:**
- Consumes: `BridgeMatterClient`, `NodeSnapshot`, `extract_signals`, `find_unreported_attributes`, `find_unparsable_paths`
- Produces: `app: typer.Typer` mit Kommando `inspect`; `render_report(snapshot: NodeSnapshot) -> str`

- [ ] **Step 1: Fixture anlegen**

`tests/fixtures/nodes/example_light.json`:

```json
{
  "node_id": 12,
  "attributes": {
    "0/40/1": "IKEA of Sweden",
    "0/40/3": "TRADFRI bulb",
    "0/40/18": "ABC123",
    "1/6/65531": [0, 16],
    "1/6/0": true,
    "1/8/0": 254,
    "1/59/65530": [0, 1]
  }
}
```

- [ ] **Step 2: Write the failing test**

`tests/test_cli.py`:

```python
import json
from pathlib import Path
from typing import Any

from matter_server.client.exceptions import CannotConnect
from typer.testing import CliRunner

from loxmatter import cli
from loxmatter.cli import app, render_report
from loxmatter.matter.client import BridgeMatterClient
from loxmatter.matter.models import NodeSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "nodes" / "example_light.json"


def load() -> NodeSnapshot:
    raw = json.loads(FIXTURE.read_text())
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_report_names_the_device():
    report = render_report(load())
    assert "IKEA of Sweden" in report
    assert "TRADFRI bulb" in report


def test_report_lists_attribute_and_event_signals():
    report = render_report(load())
    assert "1/6/0" in report
    assert "1/8/0" in report
    assert "1/59/0" in report  # Event aus der EventList
    assert "1/59/1" in report


def test_report_hides_global_attributes():
    assert "65531" not in render_report(load())


def test_report_flags_attributes_the_device_claimed_but_did_not_report():
    # AttributeList nennt 0 und 16, geliefert wurde nur 0.
    report = render_report(load())
    assert "NICHT GELIEFERT" in report
    assert "1/6/16" in report


def test_cli_reads_a_fixture_without_network():
    result = CliRunner().invoke(app, ["inspect", "--fixture", str(FIXTURE)])
    assert result.exit_code == 0
    assert "TRADFRI bulb" in result.stdout


class _FakeUpstream:
    """Attrappe für matter_server.client.MatterClient — offline, kein Socket."""

    def __init__(
        self,
        nodes: list[Any] | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self._nodes = nodes or []
        self._connect_error = connect_error

    async def connect(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    async def disconnect(self) -> None:
        pass

    def get_nodes(self) -> list[Any]:
        return self._nodes


class _FakeHttpSession:
    async def close(self) -> None:
        pass


def _fake_client(
    *,
    nodes: list[Any] | None = None,
    connect_error: BaseException | None = None,
) -> BridgeMatterClient:
    upstream = _FakeUpstream(nodes=nodes, connect_error=connect_error)
    return BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=_FakeHttpSession,
    )


def test_cli_reports_malformed_fixture_missing_node_id(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"attributes": {}}), encoding="utf-8")

    result = CliRunner().invoke(app, ["inspect", "--fixture", str(broken)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "node_id" in result.stderr


def test_cli_reports_fixture_that_is_not_valid_json(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not valid json", encoding="utf-8")

    result = CliRunner().invoke(app, ["inspect", "--fixture", str(broken)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "JSON" in result.stderr


def test_cli_reports_node_not_found(monkeypatch):
    monkeypatch.setattr(cli, "_build_client", lambda url: _fake_client(nodes=[]))

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://test/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "1" in result.stderr


def test_cli_reports_unreachable_server(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_build_client",
        lambda url: _fake_client(connect_error=CannotConnect("boom")),
    )

    result = CliRunner().invoke(app, ["inspect", "--node", "1", "--url", "ws://10.0.1.215:5580/ws"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "nicht erreichbar" in result.stderr
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'loxmatter.cli'`

- [ ] **Step 4: Write minimal implementation**

`src/loxmatter/cli.py`:

```python
"""Kommandozeile der Bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import NoReturn

import typer
from matter_server.client.exceptions import CannotConnect

from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind

app = typer.Typer(help="Matter → Loxone Bridge")


@app.callback()
def main() -> None:
    """Ohne diesen Callback macht Typer bei genau einem Kommando aus
    `loxmatter inspect ...` ein `loxmatter ...` — der Unterbefehl verschwindet."""


def render_report(snapshot: NodeSnapshot) -> str:
    lines = [
        f"Node {snapshot.node_id}: {snapshot.vendor_name} {snapshot.product_name}".rstrip(),
        f"Unique ID: {snapshot.unique_id or '—'}",
        "",
    ]

    signals = extract_signals(snapshot)
    attributes = [s for s in signals if s.kind is SignalKind.ATTRIBUTE]
    events = [s for s in signals if s.kind is SignalKind.EVENT]

    lines.append(f"Attribute ({len(attributes)}):")
    for ref in attributes:
        lines.append(f"  {ref.path:<16} = {snapshot.attributes.get(ref.path)!r}")

    lines.append("")
    lines.append(f"Events ({len(events)}):")
    for ref in events:
        lines.append(f"  {ref.path}")

    missing = find_unreported_attributes(snapshot)
    if missing:
        lines += [
            "",
            f"NICHT GELIEFERT ({len(missing)}) — vom Gerät gelistet, aber ohne Wert:",
        ]
        lines += [f"  {ref.path}" for ref in missing]

    broken = find_unparsable_paths(snapshot)
    if broken:
        lines += ["", f"NICHT LESBAR ({len(broken)}):"] + [f"  {p}" for p in broken]

    return "\n".join(lines)


def _fail(message: str) -> NoReturn:
    """Meldet einen erwarteten CLI-Fehler: eine Zeile auf stderr, danach
    Programmende mit Exit-Code ≠ 0 — statt eines Tracebacks."""
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _load_fixture(path: Path) -> NodeSnapshot:
    """Lädt eine Fixture-Datei; meldet kaputten Inhalt als CLI-Fehler statt
    mit einem rohen KeyError/JSONDecodeError abzubrechen."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"Fixture {path} enthält kein gültiges JSON: {exc}")
    try:
        node_id = raw["node_id"]
    except (KeyError, TypeError):
        _fail(f"Fixture {path} hat kein Feld 'node_id'.")
    return NodeSnapshot.from_raw(node_id, raw)


def _build_client(url: str) -> BridgeMatterClient:
    """Eigener Konstruktions-Schritt, damit Tests den Client per Monkeypatch
    durch eine mit Fake-Factories bestückte Instanz ersetzen können — ohne
    Netzwerk zu berühren (siehe BridgeMatterClient.session_factory)."""
    return BridgeMatterClient(url)


@app.command()
def inspect(
    node: int | None = typer.Option(None, help="Node-ID am laufenden matter-server"),
    fixture: Path | None = typer.Option(  # noqa: B008 — typer-Idiom, `Path` gilt Ruff nicht als unveränderlich
        None, help="Statt matter-server ein gespeichertes Abbild"
    ),
    url: str = typer.Option("ws://localhost:5580/ws", help="Adresse von matter-server"),
) -> None:
    """Listet alle Attribute und Events eines Geräts auf."""
    if fixture is not None:
        typer.echo(render_report(_load_fixture(fixture)))
        return

    if node is None:
        raise typer.BadParameter("entweder --node oder --fixture angeben")

    async def run() -> str:
        client = _build_client(url)
        try:
            await client.connect()
        except CannotConnect:
            _fail(f"matter-server unter {url} nicht erreichbar — läuft der Dienst?")
        try:
            snapshot = await client.snapshot(node)
        except MatterUnavailableError:
            _fail(f"Node {node} ist am matter-server ({url}) nicht bekannt — kommissioniert?")
        finally:
            await client.disconnect()
        return render_report(snapshot)

    typer.echo(asyncio.run(run()))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 9 Tests

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/cli.py tests/test_cli.py tests/fixtures/nodes/example_light.json
git commit -m "feat(cli): loxmatter inspect listet Signale eines Geräts"
```

Eine spätere Fehlerbehebung ergänzte drei Fehlerpfade — kaputte Fixture (ungültiges
JSON oder fehlendes `node_id`), unbekannter Node, unerreichbarer matter-server —, die
zuvor rohe Tracebacks statt sauberer deutscher Meldungen erzeugten. Der obige Code und
die vier zusätzlichen Tests spiegeln bereits diesen Stand.

---

### Task 6: matter-server und OTBR auf der Test-VM

Diese Task stand ursprünglich in Phase 6. Sie musste vorgezogen werden, weil Task 7
ohne laufenden Controller nicht ausführbar ist — die Annahme aus Spec 3.5 lässt sich
nur an echten Geräten prüfen, und an echte Geräte kommt man nur über einen Controller.

Ziel ist ausdrücklich **nicht** der fertige Produktions-Stack aus Spec 4.1. Es ist die
Testumgebung, die Phase 1 abschließen kann. Phase 6 baut darauf auf.

**Files:**
- Create: `deploy/testvm/docker-compose.yml` (später umbenannt zu `deploy/testhost/docker-compose.yml`
  — Umzug auf den Raspberry Pi, siehe README dort)
- Create: `deploy/testvm/.env.example` (später `deploy/testhost/.env.example`)
- Create: `deploy/testvm/README.md` (später `deploy/testhost/README.md`)

**Interfaces:**
- Consumes: nichts aus früheren Tasks
- Produces: eine erreichbare WebSocket-URL `ws://10.0.1.215:5580/ws`, die Task 7 als `--url` benutzt
  (nach dem Umzug auf den Raspberry Pi: `ws://10.0.1.56:5580/ws`, siehe `deploy/testhost/README.md`)

#### Die Umgebung, bereits erhoben

Nicht erneut ermitteln — diese Werte sind auf der VM nachgesehen:

| | Wert |
|---|---|
| Host | `lucienkerl@10.0.1.215`, SSH-Key eingerichtet |
| OS | Ubuntu 26.04 LTS, x86_64, 4 Kerne, 5,3 GB RAM |
| Backbone-Interface | `ens18` |
| Funkmodul | SONOFF Dongle Plus MG24 (Silicon Labs CP210x), Thread-Firmware bereits aufgespielt |
| Geräteknoten | `/dev/ttyUSB0`, Gruppe `dialout` |
| IPv6 | nur link-local (`fe80::be24:11ff:fe23:ed85`), `accept_ra=0`, `forwarding=0` |
| Docker | nicht installiert |

**Zum fehlenden globalen IPv6:** für Thread-Geräte unkritisch. Der OTBR spannt auf
`wpan0` ein eigenes ULA-Präfix auf, und matter-server läuft mit `network_mode: host`
daneben und erreicht die Geräte über die Route dorthin. Erst Matter-über-WLAN bräuchte
globales IPv6 im LAN. Notwendig ist lediglich `forwarding=1`.

#### Schritt 0: Root-Schritte (vom Menschen auszuführen)

`sudo` verlangt auf dieser VM ein Passwort, das der Agent weder erfragen noch benutzen
darf. Diese Befehle führt der Betreiber selbst aus, danach übernimmt der Agent:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker,dialout "$USER"
printf 'net.ipv6.conf.all.forwarding=1\nnet.ipv4.ip_forward=1\n' | sudo tee /etc/sysctl.d/99-matter.conf
sudo sysctl --system
```

Danach **neu anmelden** (Gruppenmitgliedschaften greifen erst in einer neuen Sitzung).

- [ ] **Step 1: Voraussetzungen bestätigen**

```bash
ssh lucienkerl@10.0.1.215 'id -nG; docker ps >/dev/null && echo docker-ok; ls -l /dev/ttyUSB0; sysctl net.ipv6.conf.all.forwarding'
```

Erwartet: `docker` und `dialout` in den Gruppen, `docker-ok`, `forwarding = 1`.
Fehlt etwas, ist Schritt 0 unvollständig — melde das, statt es mit `sudo` zu umgehen.

- [ ] **Step 2: Compose-Dateien schreiben**

`deploy/testvm/.env.example`:

```
RADIO_DEVICE=/dev/ttyUSB0
RADIO_BAUDRATE=460800
BACKBONE_IF=ens18
```

`deploy/testvm/docker-compose.yml`:

```yaml
# Testumgebung fuer Phase 1 - NICHT der Produktions-Stack aus Spec 4.1.
services:
  otbr:
    image: openthread/otbr:latest
    container_name: otbr
    network_mode: host
    privileged: true
    restart: unless-stopped
    devices:
      - ${RADIO_DEVICE}:${RADIO_DEVICE}
    environment:
      RADIO_URL: spinel+hdlc+uart://${RADIO_DEVICE}?uart-baudrate=${RADIO_BAUDRATE}
    command: --backbone-interface ${BACKBONE_IF}

  matter-server:
    image: ghcr.io/home-assistant-libs/python-matter-server:stable
    container_name: matter-server
    network_mode: host
    restart: unless-stopped
    security_opt:
      - apparmor=unconfined
    volumes:
      - ./data:/data
      - /run/dbus:/run/dbus:ro
    depends_on:
      - otbr
```

Kopiere `.env.example` auf der VM nach `.env`. Die Compose-Syntax von
`openthread/otbr` ändert sich zwischen Versionen — prüfe sie gegen die Dokumentation
des Images, bevor du Fehler suchst, die keine sind, und trage Abweichungen im README ein.

**Baudrate:** 460800 ist der wahrscheinlichste Wert für den SONOFF MG24. Verbindet
sich der RCP nicht, ist 115200 der nächste Kandidat. Rate nicht mehr als zweimal —
danach lies die Firmware-Dokumentation des Dongles.

- [ ] **Step 3: Starten und Thread-Netz bilden**

```bash
cd ~/loxmatter-testvm && docker compose up -d
docker compose logs -f otbr        # bis der RCP verbunden ist
```

```bash
docker exec -it otbr ot-ctl dataset init new
docker exec -it otbr ot-ctl dataset commit active
docker exec -it otbr ot-ctl ifconfig up
docker exec -it otbr ot-ctl thread start
docker exec -it otbr ot-ctl state          # erwartet: leader
docker exec -it otbr ot-ctl dataset active -x
```

Den ausgegebenen aktiven Datensatz sichern — er wird zum Einlernen von Thread-Geräten
gebraucht. **Nicht ins Repository committen**, er ist ein Netzwerk-Credential.

- [ ] **Step 4: Erreichbarkeit vom Entwicklungsrechner prüfen**

Auf dem Mac, im Projektverzeichnis:

```bash
uv run loxmatter inspect --node 1 --url ws://10.0.1.215:5580/ws
```

Erwartet: ein Bericht oder `unbekannter Node 1`. Beides beweist, dass die Verbindung
steht. Ein Verbindungsfehler bedeutet, dass Port 5580 nicht erreichbar ist — dann
Firewall und `network_mode: host` prüfen.

- [ ] **Step 5: README schreiben**

`deploy/testvm/README.md` hält fest, was tatsächlich funktionierte: die konkrete
Baudrate, jede Abweichung von Step 2, den Befehl zum Sichern des Fabric-Volumes unter
`./data`, und den Hinweis, dass der Thread-Datensatz nicht ins Repository gehört.
Dieses Dokument ist der Rohstoff für den Deployment-Guide in Phase 6 — schreib auf,
was schiefging, nicht nur was am Ende lief.

- [ ] **Step 6: Commit**

```bash
git add deploy/testvm
git commit -m "feat(deploy): Testumgebung mit matter-server und OTBR"
```

---

### Task 7: Fixtures echter Geräte aufnehmen und Annahme prüfen

Der Zweck der ganzen Phase. Hier wird Spec 3.5 belegt oder widerlegt.

**Files:**
- Create: `scripts/record_node.py`
- Create: `tests/fixtures/nodes/<hersteller>_<produkt>.json` (je Gerät)
- Create: `tests/matter/test_real_devices.py`
- Modify: `docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md` (nur falls die Annahme bricht)

**Interfaces:**
- Consumes: `BridgeMatterClient`, `extract_signals`, `find_unreported_attributes`, `find_unparsable_paths`
- Produces: Fixture-Dateien im Format aus Task 5 (`{"node_id": int, "attributes": {...}}`)

- [ ] **Step 1: Aufnahmewerkzeug schreiben**

`scripts/record_node.py`:

```python
"""Speichert das Abbild eines echten Geräts als Fixture.

Aufruf: uv run python scripts/record_node.py 12 tests/fixtures/nodes/ikea_bulb.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from loxmatter.matter.client import BridgeMatterClient


async def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Aufruf: record_node.py <node_id> <zieldatei>")

    node_id, target = int(sys.argv[1]), Path(sys.argv[2])
    client = BridgeMatterClient("ws://localhost:5580/ws")
    await client.connect()
    try:
        snapshot = await client.snapshot(node_id)
    finally:
        await client.disconnect()

    target.write_text(
        json.dumps(
            {"node_id": snapshot.node_id, "attributes": dict(snapshot.attributes)},
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{target} geschrieben, {len(snapshot.attributes)} Attribute")


asyncio.run(main())
```

- [ ] **Step 2: Echte Geräte aufnehmen**

Mit laufendem matter-server und eingelernten IKEA-Geräten je Gerät einmal ausführen.
Mindestens ein Gerät pro Klasse aus Spec 3.5, sonst prüft die Phase nur die halbe Annahme.

**Die Node-IDs unten sind Platzhalter** — die echten stehen in der matter-server-Oberfläche
oder kommen aus `uv run loxmatter inspect --node <id>`, bis eine passt. Die Dateinamen
dagegen bitte genau so, `test_real_devices.py` findet Fixtures über `*.json` und schließt
nur `example_*` aus:

```bash
uv run python scripts/record_node.py 12 tests/fixtures/nodes/ikea_bulb_color.json
uv run python scripts/record_node.py 13 tests/fixtures/nodes/ikea_plug_energy.json
uv run python scripts/record_node.py 14 tests/fixtures/nodes/ikea_button.json
uv run python scripts/record_node.py 15 tests/fixtures/nodes/ikea_sensor.json
```

- [ ] **Step 3: Write the failing test**

`tests/matter/test_real_devices.py`:

```python
"""Prüft Spec 3.5 gegen Abbilder echter Geräte.

Schlägt einer dieser Tests fehl, ist nicht der Test falsch — dann trägt die
generische Zerlegung nicht, und die Spec muss geändert werden.
"""

import json
from pathlib import Path

import pytest

from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "nodes"
REAL_DEVICES = sorted(p for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("example_"))


def load(path: Path) -> NodeSnapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_real_device_fixtures_exist():
    assert REAL_DEVICES, "Task 6 Schritt 2 wurde nicht ausgeführt — keine echten Abbilder da"


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_every_path_is_parsable(path):
    assert find_unparsable_paths(load(path)) == []


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_no_claimed_attribute_is_missing(path):
    assert find_unreported_attributes(load(path)) == []


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_device_yields_at_least_one_signal(path):
    assert extract_signals(load(path))


def test_at_least_one_fixture_carries_events():
    """Taster sind der Sonderfall aus Spec 6.3 — ohne sie ist die Annahme halb geprüft."""
    with_events = [
        p for p in REAL_DEVICES if any(s.kind is SignalKind.EVENT for s in extract_signals(load(p)))
    ]
    assert with_events, "kein aufgenommenes Gerät liefert Events — Taster fehlt"


def test_at_least_one_fixture_carries_energy_measurement():
    """Spec 7.3: messende Steckdose, Cluster 144 ElectricalPowerMeasurement."""
    with_energy = [
        p for p in REAL_DEVICES if any(s.cluster_id == 144 for s in extract_signals(load(p)))
    ]
    assert with_energy, "kein aufgenommenes Gerät misst Leistung"
```

- [ ] **Step 4: Run tests and read the result carefully**

Run: `uv run pytest tests/matter/test_real_devices.py -v`

Erwartung: PASS. Diese Tests sind das Experiment der Phase, nicht Formsache.

Bei Fehlschlag **nicht den Test anpassen**, sondern den Befund festhalten:

- `test_every_path_is_parsable` schlägt fehl → matter-server benutzt Pfadformen, die
  `parse_attribute_path` nicht kennt. `paths.py` erweitern, Task 1 nachziehen.
- `test_no_claimed_attribute_is_missing` schlägt fehl → das Gerät bietet Attribute an,
  die nicht im Snapshot landen. Ursache klären: liest matter-server sie gar nicht, oder
  fehlt eine Subscription? **Das ist der Fall, der Spec 3.5 gefährdet.**
- `test_at_least_one_fixture_carries_events` schlägt fehl → Events stehen nicht in der
  EventList, sondern kommen nur über Subscriptions. Dann braucht `discovery` eine
  zweite Quelle und die Spec einen Zusatz in 6.3.

- [ ] **Step 5: Befund in der Spec festhalten**

Ergänze in der Spec unter 3.5 einen Absatz mit dem Ergebnis — auch wenn es positiv ist:

```markdown
**Validierung (Phase 1, <Datum>).** Geprüft an <n> realen IKEA-Geräten
(<Liste>). Alle Attributpfade parsebar, keine vom Gerät gelisteten Attribute
fehlten, Events über die EventList auffindbar. Die generische Zerlegung trägt.
```

Bei negativem Befund stattdessen beschreiben, was nicht trägt und wie 3.5 sich ändert.

- [ ] **Step 6: Commit**

```bash
git add scripts/record_node.py tests/fixtures/nodes tests/matter/test_real_devices.py docs/
git commit -m "test(matter): Spec 3.5 an echten IKEA-Geräten validiert"
```

---

### Task 8: CI und Qualitätsschranke

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `README.md`

**Interfaces:**
- Consumes: alle vorherigen Tasks
- Produces: nichts für spätere Tasks

- [ ] **Step 1: CI anlegen**

`.github/workflows/ci.yml`:

```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run pytest -v
```

- [ ] **Step 2: README schreiben**

`README.md`:

```markdown
# loxmatter

Bindet Matter-Geräte (Thread und WiFi) an einen Loxone Miniserver an.

Design: [`docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md)

## Stand

Phase 1 von 6: Matter-Adapter und Signal-Extraktion.

## Entwickeln

```bash
uv sync
uv run pytest
```

Die Testsuite läuft ohne Hardware und ohne Netzwerkzugriff.

## Ein Gerät ansehen

```bash
uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json
uv run loxmatter inspect --node 12          # gegen laufenden matter-server
```
```

- [ ] **Step 3: Alles lokal grün bekommen**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest -v
```

Expected: alle Prüfungen ohne Befund, alle Tests PASS.

- [ ] **Step 4: Commit**

```bash
git add .github README.md
git commit -m "ci: Lint, Typprüfung und Tests bei jedem Push"
```

---

## Abschluss der Phase

Die Phase ist fertig, wenn:

1. `uv run pytest` ohne Hardware und ohne Netz durchläuft,
2. `uv run loxmatter inspect --node <id>` für jedes echte IKEA-Gerät eine
   vollständige Signalliste druckt,
3. der Befund zu Spec 3.5 in der Spec steht — positiv oder negativ.

Erst dann wird der Plan für Phase 2 geschrieben. Fällt der Befund negativ aus, wird
vorher die Spec überarbeitet: sie bleibt das maßgebliche Dokument, nicht dieser Plan.
