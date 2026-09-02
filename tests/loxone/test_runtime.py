import asyncio
import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    """Merkt sich, was gesendet wurde, statt es zu verschicken."""

    def __init__(self) -> None:
        self.gesendet: list[tuple[str, object, bool]] = []

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self.gesendet.append((key, value, force))
        return True

    async def close(self) -> None:
        return None

    def keys(self) -> list[str]:
        return [k for k, _, _ in self.gesendet]


class FlakySender(FakeSender):
    """Wie FakeSender, wirft aber beim n-ten Aufruf einen RuntimeError - fuer
    Tests, die einen fehlgeschlagenen Sendeversuch nachstellen wollen."""

    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self._fail_on_call = fail_on_call
        self._calls = 0

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise RuntimeError("Sender kaputt")
        return await super().send(key, value, force=force)


@pytest.fixture
def umgebung(tmp_path):
    """Zwei Geraete in einem Store: die Steckdose liefert das Attribut fuer
    die Skalierungs-Tests (2/144/4), der Taster liefert das Event fuer die
    Impuls-Tests (1/59/1) — die Steckdose hat keinen Switch-Cluster und kann
    kein Event liefern."""
    store = Store(tmp_path / "t.sqlite")

    plug_raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    plug_snap = NodeSnapshot.from_raw(plug_raw["node_id"], plug_raw)
    device_id = store.register_device(plug_snap)
    store.register_signals(device_id, plug_snap)
    store.register_commands(device_id, extract_commands(plug_snap), plug_snap.node_id)

    button_raw = json.loads((FIXTURES / "ikea_bilresa_button.json").read_text(encoding="utf-8"))
    button_snap = NodeSnapshot.from_raw(button_raw["node_id"], button_raw)
    button_device_id = store.register_device(button_snap)
    store.register_signals(button_device_id, button_snap)

    sender = FakeSender()
    runtime = Runtime(store, sender)
    yield runtime, sender, store, device_id, button_device_id
    store.close()


async def test_attribute_change_becomes_a_scaled_datagram(umgebung):
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sender.gesendet == [(f"d{device_id}_2_voltage", pytest.approx(230.0), False)]


async def test_unmappable_attribute_is_not_sent(umgebung):
    """Spec 6.6: Listen werden nie zu einem Datagramm."""
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "0/29/1", [29, 31, 40])
    assert sender.gesendet == []


async def test_unknown_path_is_ignored_not_raised(umgebung):
    """Ein Gerät kann Attribute melden, die beim Export nicht dabei waren."""
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "9/9999/9", 1)
    assert sender.gesendet == []


async def test_event_sends_a_pulse_and_a_counter(umgebung):
    """Spec 6.3: der Impuls erzeugt die Flanke, der Zaehler ueberlebt ein verlorenes Paket."""
    runtime, sender, _, _, button_device_id = umgebung
    await runtime.on_event(button_device_id, "1/59/1")
    keys = sender.keys()
    assert f"d{button_device_id}_1_press" in keys
    assert f"d{button_device_id}_1_press_n" in keys


async def test_pulse_falls_back_to_zero(umgebung):
    runtime, sender, _, _, button_device_id = umgebung
    await runtime.on_event(button_device_id, "1/59/1")
    await asyncio.sleep(Runtime.PULSE_MILLISECONDS / 1000 + 0.1)
    impulse = [(k, v) for k, v, _ in sender.gesendet if k == f"d{button_device_id}_1_press"]
    assert impulse == [
        (f"d{button_device_id}_1_press", True),
        (f"d{button_device_id}_1_press", False),
    ]


async def test_counter_increases_monotonically(umgebung):
    runtime, sender, _, _, button_device_id = umgebung
    for _ in range(3):
        await runtime.on_event(button_device_id, "1/59/1")
    zaehler = [v for k, v, _ in sender.gesendet if k == f"d{button_device_id}_1_press_n"]
    assert zaehler == [1, 2, 3]


async def test_online_signal_is_sent(umgebung):
    runtime, sender, _, device_id, _ = umgebung
    await runtime.set_online(device_id, False)
    assert (f"d{device_id}_online", False, False) in sender.gesendet


async def test_resend_forces_every_known_value(umgebung):
    """Spec 6.4: nach einem Miniserver-Neustart muss die Entprellung umgangen werden."""
    runtime, sender, _, device_id, _ = umgebung
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.gesendet.clear()
    anzahl = await runtime.resend_all()
    assert anzahl == 1
    assert sender.gesendet[0][2] is True


async def test_resend_of_an_empty_runtime_sends_nothing(umgebung):
    runtime, _, _, _, _ = umgebung
    assert await runtime.resend_all() == 0


async def test_heartbeat_toggles(umgebung):
    """Spec 6.5: bridge_alive deckt "Container tot" und "Netz weg" gleichermassen ab."""
    _, sender, store, _, _ = umgebung
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.16)
    await runtime.stop()
    werte = [v for k, v, _ in sender.gesendet if k == "bridge_alive"]
    assert len(werte) >= 2
    assert werte[0] != werte[1]


async def test_heartbeat_survives_a_failed_send(umgebung):
    """Review-Fix Important #1: der Heartbeat deckt laut Modul-Docstring
    "Container tot" und "Netz weg" gleichermassen ab - ein einzelner
    fehlgeschlagener Sendeversuch darf die Watchdog-Schleife deshalb nicht
    beenden, sonst friert der Loxone-Watchdog auf dem letzten Wert ein,
    waehrend die Bruecke laengst schweigt."""
    _, _, store, _, _ = umgebung
    sender = FlakySender(fail_on_call=2)
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.22)
    await runtime.stop()
    values = [v for k, v, _ in sender.gesendet if k == "bridge_alive"]
    # Der zweite Aufruf schlaegt fehl (siehe FlakySender) - ohne den Fix
    # stuerbe die Schleife dort und es kaemen nie weitere Werte an.
    assert len(values) >= 3


async def test_stop_completes_even_if_a_task_already_died(umgebung):
    """Review-Fix Important #1, Begleitfehler: contextlib.suppress(CancelledError)
    unterdrueckt nur eine Cancellation, keine andere Exception, an der ein
    Task schon vor `stop()` gestorben ist. Die alte Implementierung liess
    `stop()` mit genau dieser Exception abbrechen und ueberspringt dabei das
    Leeren der Task-Liste."""
    runtime, _, _, _, _ = umgebung

    async def boom() -> None:
        raise RuntimeError("Task ist schon vor stop() gestorben")

    dead_task = asyncio.create_task(boom())
    await asyncio.sleep(0)  # den Task tatsaechlich sterben lassen
    assert dead_task.done()
    runtime._aufgaben.append(dead_task)

    await runtime.start()
    await runtime.stop()  # darf nicht an der bereits toten Task scheitern

    assert runtime._aufgaben == []
    assert runtime._impuls_aufgaben == set()


async def test_stop_lowers_an_in_flight_pulse(umgebung):
    """Review-Fix Important #2: eine Cancellation waehrend des Impuls-Schlafs
    ueberspringt sonst den `send(key, False)` - das digitale Signal bliebe
    bis zum naechsten Ereignis auf diesem Schluessel auf 1 haengen."""
    runtime, sender, _, _, button_device_id = umgebung
    await runtime.on_event(button_device_id, "1/59/1")
    await runtime.stop()
    key = f"d{button_device_id}_1_press"
    values = [v for k, v, _ in sender.gesendet if k == key]
    assert values[-1] is False


async def test_invalidate_index_lets_a_newly_registered_signal_through(umgebung, monkeypatch):
    """Review-Fix Important #3: `Store.register_signals` kann jederzeit ein
    neues Signal zu einem schon indizierten Geraet hinzufuegen (z. B. nach
    einem Firmware-Update). Ohne `invalidate_index` bleibt dieses Signal fuer
    die Laufzeit unsichtbar, weil `_signal_fuer` nur einmal pro Geraet aus der
    Datenbank liest."""
    runtime, sender, store, device_id, _ = umgebung
    plug_raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    plug_snap = NodeSnapshot.from_raw(plug_raw["node_id"], plug_raw)

    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # Erstmaliges Indizieren durch die Laufzeit - der Pfad existiert noch nicht.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.gesendet == []

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)
    store.register_signals(device_id, plug_snap)

    # Der Cache der Laufzeit weiss noch nichts vom neuen Signal.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.gesendet == []

    runtime.invalidate_index(device_id)
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.keys() == [key]
