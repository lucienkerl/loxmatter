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

import json
from pathlib import Path

import httpx2
import pytest

from loxmatter import i18n
from loxmatter.auth.passwords import hash_password
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
    # Task 6: teilt sich api.errors.device_unreachable mit control.py's
    # execute_command (siehe test_control.py::
    # test_a_device_that_does_not_answer_yields_502).
    assert response.json()["detail"] == "device unreachable: Geraet antwortet nicht"
    store.close()


async def test_a_failing_matter_call_yields_502_with_the_german_detail_text(tmp_path):
    """Deutscher Begleittest zu
    test_a_failing_matter_call_yields_502_not_a_traceback (Task 6) -
    `store.locale.set_language`, nicht `i18n.set_language` direkt: die
    sync_language-Middleware liest bei jeder Anfrage aus dem Store neu."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)
    store.locale.set_language("de")

    async def invoke(call):
        raise TimeoutError("Geraet antwortet nicht")

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 502
    assert response.json()["detail"] == "Geraet nicht erreichbar: Geraet antwortet nicht"
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
    assert response.json()["detail"] == "Full resend failed: UdpSender ist geschlossen"
    store.close()


async def test_a_failing_resend_yields_502_with_the_german_detail_text(tmp_path):
    """Deutscher Begleittest zu test_a_failing_resend_yields_502_not_a_traceback
    (Task 6) - `store.locale.set_language`, nicht `i18n.set_language` direkt."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)
    store.locale.set_language("de")

    runtime = Runtime(store, BrokenResendSender())
    with pytest.raises(RuntimeError):
        await runtime.on_attribute(device_id, "2/144/4", 230000)

    async def invoke(call):
        return None

    app = build_app(store, invoke, runtime)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/resync")
    assert response.status_code == 502
    assert response.json()["detail"] == "Full-Resend fehlgeschlagen: UdpSender ist geschlossen"
    store.close()


def test_sync_language_is_the_outermost_middleware(tmp_path):
    """Review-Fix Important (Whole-Branch-Review, 2026-09-04): Starlette
    fuegt jede per `@app.middleware("http")` registrierte Schicht VORNE in
    `app.user_middleware` ein und baut den Stack aus `reversed(...)` auf -
    die ZULETZT registrierte Funktion landet auf Index 0 und wird zur
    AEUSSEREN Schicht (durch eine `TestClient`-Probe verifiziert, siehe
    Docstring von `_sync_language` in server.py). `_sync_language` muss
    deshalb ALS LETZTE registriert werden, damit sie vor `_record_command`
    und vor jeder Route laeuft - dieser Test haelt das fest, damit eine
    kuenftige Umsortierung der beiden `@app.middleware("http")`-Bloecke in
    `build_app` sofort auffaellt."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    store.register_device(snap)

    async def invoke(call):
        return None

    app = build_app(store, invoke, Runtime(store, FakeSender()))
    assert app.user_middleware[0].kwargs["dispatch"].__name__ == "_sync_language"
    store.close()


async def test_loxmatter_lang_override_survives_multiple_requests(tmp_path, monkeypatch):
    """Review-Fix Important (Whole-Branch-Review, 2026-09-04): `cli.py`
    dokumentiert `LOXMATTER_LANG` als Override mit striktem Vorrang vor
    `store.locale` - bislang ueberschrieb `_sync_language` die
    Prozess-Sprache aber bei JEDER eingehenden Anfrage wieder aus dem Store,
    sodass schon die allererste HTTP-Anfrage den vom CLI-Bootstrap
    gesetzten Override verwarf. Dieser Test setzt `LOXMATTER_LANG=de` UND
    laesst den Store auf Englisch (dem Default) stehen - simuliert damit
    genau die widerspruechliche Situation aus dem Review-Befund - und
    prueft, dass zwei aufeinanderfolgende Anfragen die per Bootstrap
    gesetzte Sprache NICHT verwerfen."""
    monkeypatch.setenv("LOXMATTER_LANG", "de")
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    store.register_device(snap)
    # Store bleibt beim Default (Englisch) - genau das widerspruechliche
    # Szenario, das den Bug ausloest: ohne den Fix flippt die erste Anfrage
    # `i18n.current_language()` zurueck auf "en".
    assert store.locale.get_language() == "en"

    async def invoke(call):
        return None

    # Simuliert cli.py's Modul-Import-Bootstrap (`i18n.set_language(
    # _resolve_cli_language(...))`), das VOR `build_app()` laeuft.
    i18n.set_language("de")
    app = build_app(store, invoke, Runtime(store, FakeSender()))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/health")
        assert i18n.current_language() == "de"
        await c.get("/health")
        assert i18n.current_language() == "de"
    store.close()


async def test_a_crashing_route_still_appears_in_the_command_log_and_still_raises(tmp_path):
    """Review-Fix Important #2 (2026-09-02): `_record_command` rief
    `call_next` bislang UNGESCHUETZT auf - eine unbehandelte Ausnahme aus
    einer Route (kein `HTTPException`, ein echter Programmfehler) verliess
    `call_next`, bevor das try/except um das Anhaengen an den Ringpuffer je
    erreicht wurde. Der Aufruf, der den Dienst zu Fall bringt, fehlte
    deshalb ausgerechnet dort, wo ein Diagnostiker ihn am dringendsten
    braucht (`GET /api/diagnostics/commands`). Diese Route hier (`/__boom__`)
    steht fuer genau so einen Programmfehler - keine der bestehenden Routen
    wirft unbehandelt, `/cmd` und `/resync` fangen jede `Exception` bereits
    zu einem sauberen 502 ab (siehe die beiden Tests oben)."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    store.register_commands(device_id, extract_commands(snap), snap.node_id)

    async def invoke(call):
        return None

    app = build_app(store, invoke, Runtime(store, FakeSender()))

    @app.get("/__boom__")
    async def boom() -> None:
        raise RuntimeError("Simulierter Programmfehler in einer Route")

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        # Seit Task 8 verlangt auch `/api/diagnostics/commands` eine
        # Anmeldung - ohne sie waere die folgende Antwort ein 401 statt der
        # erwarteten Kommando-Liste, ganz unabhaengig vom hier eigentlich
        # untersuchten Absturz-Pfad.
        store.auth.set_password_hash(hash_password("test-passwort"))
        login = await c.post("/auth/login", json={"password": "test-passwort"})
        assert login.status_code == 200
        with pytest.raises(RuntimeError, match="Simulierter Programmfehler"):
            await c.get("/__boom__")
        entries = (await c.get("/api/diagnostics/commands")).json()
    store.close()

    boom_entries = [e for e in entries if e["path"] == "/__boom__"]
    assert len(boom_entries) == 1
    # Kein echter HTTP-Statuscode (siehe `_CRASHED_STATUS` in server.py) -
    # unterscheidbar von jeder Antwort, die die Route tatsaechlich sendet.
    assert boom_entries[0]["status"] == 0
