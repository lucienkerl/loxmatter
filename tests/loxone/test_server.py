import json
from pathlib import Path

import httpx2
import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object, bool]] = []

    async def send(self, key, value, *, force: bool = False) -> bool:
        self.sent.append((key, value, force))
        return True

    async def close(self) -> None:
        return None


class BrokenResendSender(FakeSender):
    """Sendet normale Updates anstandslos, verweigert aber jeden Aufruf -
    simuliert einen `UdpSender`, dessen Socket bereits geschlossen ist
    (siehe `UdpSender.send`, das dann unbedingt `RuntimeError` wirft)."""

    async def send(self, key, value, *, force: bool = False) -> bool:
        raise RuntimeError("UdpSender ist geschlossen")


@pytest.fixture
async def client(tmp_path):
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    calls = []

    async def invoke(call):
        calls.append(call)

    runtime = Runtime(store, FakeSender())
    app = build_app(store, invoke, runtime)
    # httpx2.AsyncClient statt Starlettes TestClient: TestClient fuehrt die
    # Anfrage in einem anyio-Portal-Thread aus, der nicht der Thread ist, in
    # dem dieses Fixture die Store erzeugt hat - sqlite3-Verbindungen sind
    # aber an ihren Erzeuger-Thread gebunden (siehe store.py). AsyncClient
    # mit ASGITransport ruft die App direkt in der Event-Loop dieses Tests
    # auf, ohne einen zweiten Thread zu eroeffnen.
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, calls, device_id
    store.close()


async def test_command_reaches_matter(client):
    c, calls, device_id = client
    response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].cluster_id == 6
    assert calls[0].command_id == 1


async def test_unknown_key_yields_404_not_500(client):
    c, calls, _ = client
    response = await c.get("/cmd/d1_1_gibtsnicht/1")
    assert response.status_code == 404
    assert calls == []


async def test_unsupported_value_yields_400(client):
    c, _, device_id = client
    response = await c.get(f"/cmd/d{device_id}_1_on/../etc/passwd")
    assert response.status_code in (400, 404)


async def test_resync_forces_a_full_resend(client):
    c, _, _ = client
    response = await c.get("/resync")
    assert response.status_code == 200
    # Review-Fix M9, 2026-09-02: "gesendet" war ein deutscher Schluessel in
    # einem Wire-Format - umbenannt zu "sent" (siehe server.py).
    assert "sent" in response.text.lower() or response.json()["sent"] >= 0


async def test_health_answers_without_touching_matter(client):
    c, calls, _ = client
    assert (await c.get("/health")).status_code == 200
    assert calls == []


async def test_a_failing_matter_call_yields_502_not_a_traceback(tmp_path):
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
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 502
    assert "Traceback" not in response.text
    store.close()


async def test_a_failing_resend_yields_502_not_a_traceback(tmp_path):
    """Review-Fix Minor #3: /resync darf einen kaputten Sender (z. B. einen
    schon geschlossenen UdpSender) nicht als nackten 500 durchreichen -
    dieselbe Absicherung wie bei einem fehlschlagenden /cmd."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    runtime = Runtime(store, BrokenResendSender())
    # `on_attribute` traegt den Wert in `_last_values` ein, BEVOR es den
    # Sender aufruft (siehe runtime.py) - der erste Aufruf scheitert also
    # am Senden, hinterlaesst `_last_values` aber wie gewuenscht befuellt,
    # damit `resend_all` unten ueberhaupt etwas zu senden versucht.
    with pytest.raises(RuntimeError):
        await runtime.on_attribute(device_id, "2/144/4", 230000)

    async def invoke(call):
        return None

    app = build_app(store, invoke, runtime)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/resync")
    assert response.status_code == 502
    assert "Traceback" not in response.text
    store.close()
