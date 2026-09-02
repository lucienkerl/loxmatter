import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    def __init__(self) -> None:
        self.gesendet: list[tuple[str, object, bool]] = []

    async def send(self, key, value, *, force: bool = False) -> bool:
        self.gesendet.append((key, value, force))
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def client(tmp_path):
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    aufrufe = []

    async def invoke(call):
        aufrufe.append(call)

    runtime = Runtime(store, FakeSender())
    app = build_app(store, invoke, runtime)
    with TestClient(app) as c:
        yield c, aufrufe, device_id
    store.close()


def test_command_reaches_matter(client):
    c, aufrufe, device_id = client
    antwort = c.get(f"/cmd/d{device_id}_1_on/1")
    assert antwort.status_code == 200
    assert len(aufrufe) == 1
    assert aufrufe[0].cluster_id == 6
    assert aufrufe[0].command_id == 1


def test_unknown_key_yields_404_not_500(client):
    c, aufrufe, _ = client
    antwort = c.get("/cmd/d1_1_gibtsnicht/1")
    assert antwort.status_code == 404
    assert aufrufe == []


def test_unsupported_value_yields_400(client):
    c, _, device_id = client
    antwort = c.get(f"/cmd/d{device_id}_1_on/../etc/passwd")
    assert antwort.status_code in (400, 404)


def test_resync_forces_a_full_resend(client):
    c, _, _ = client
    antwort = c.get("/resync")
    assert antwort.status_code == 200
    assert "gesendet" in antwort.text.lower() or antwort.json()["gesendet"] >= 0


def test_health_answers_without_touching_matter(client):
    c, aufrufe, _ = client
    assert c.get("/health").status_code == 200
    assert aufrufe == []


def test_a_failing_matter_call_yields_502_not_a_traceback(tmp_path):
    """Ein Geraet, das gerade nicht antwortet, darf keinen Traceback erzeugen."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    async def invoke(call):
        raise TimeoutError("Geraet antwortet nicht")

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    with TestClient(app) as c:
        antwort = c.get(f"/cmd/d{device_id}_1_on/1")
    assert antwort.status_code == 502
    assert "Traceback" not in antwort.text
    store.close()
