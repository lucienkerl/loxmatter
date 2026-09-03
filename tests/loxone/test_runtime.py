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

import asyncio
import json
from collections.abc import Callable
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
        self.sent: list[tuple[str, object, bool]] = []

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self.sent.append((key, value, force))
        return True

    async def close(self) -> None:
        return None

    def keys(self) -> list[str]:
        return [k for k, _, _ in self.sent]


class MutatingSender(FakeSender):
    """Wie FakeSender, ruft aber beim ersten Aufruf MIT `force=True` einmalig
    `mutate` auf - steht fuer eine gleichzeitige Aktualisierung, die waehrend
    eines laufenden `resend_all()` eintrifft (Review-Fix I4). Reagiert
    bewusst nur auf `force=True`: nur `resend_all()` setzt das, ein
    regulaerer `on_attribute()`-Aufruf beim Testaufbau (der ebenfalls
    `send()` ruft) soll die Mutation nicht vorzeitig - und damit an der
    falschen Stelle - ausloesen."""

    def __init__(self, mutate: Callable[[], None]) -> None:
        super().__init__()
        self._mutate = mutate
        self._mutated = False

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        if force and not self._mutated:
            self._mutated = True
            self._mutate()
        return await super().send(key, value, force=force)


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
def environment(tmp_path):
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


async def test_attribute_change_becomes_a_scaled_datagram(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sender.sent == [(f"d{device_id}_2_voltage", pytest.approx(230.0), False)]


async def test_unmappable_attribute_is_not_sent(environment):
    """Spec 6.6: Listen werden nie zu einem Datagramm."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "0/29/1", [29, 31, 40])
    assert sender.sent == []


async def test_unknown_path_is_ignored_not_raised(environment):
    """Ein Gerät kann Attribute melden, die beim Export nicht dabei waren."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "9/9999/9", 1)
    assert sender.sent == []


async def test_event_sends_a_pulse_and_a_counter(environment):
    """Spec 6.3: der Impuls erzeugt die Flanke, der Zaehler ueberlebt ein verlorenes Paket."""
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    keys = sender.keys()
    assert f"d{button_device_id}_1_press" in keys
    assert f"d{button_device_id}_1_press_n" in keys


async def test_pulse_falls_back_to_zero(environment):
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    await asyncio.sleep(Runtime.PULSE_MILLISECONDS / 1000 + 0.1)
    pulses = [(k, v) for k, v, _ in sender.sent if k == f"d{button_device_id}_1_press"]
    assert pulses == [
        (f"d{button_device_id}_1_press", True),
        (f"d{button_device_id}_1_press", False),
    ]


async def test_counter_increases_monotonically(environment):
    runtime, sender, _, _, button_device_id = environment
    for _ in range(3):
        await runtime.on_event(button_device_id, "1/59/1")
    counters = [v for k, v, _ in sender.sent if k == f"d{button_device_id}_1_press_n"]
    assert counters == [1, 2, 3]


async def test_online_signal_is_sent(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.set_online(device_id, False)
    assert (f"d{device_id}_online", False, False) in sender.sent


async def test_resend_forces_every_known_value(environment):
    """Spec 6.4: nach einem Miniserver-Neustart muss die Entprellung umgangen werden."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()
    count = await runtime.resend_all()
    assert count == 1
    assert sender.sent[0][2] is True


async def test_resend_sends_the_freshest_value_not_a_stale_snapshot(environment):
    """Review-Fix I4, 2026-09-02: `resend_all()` erfasste Schluessel UND Wert
    gemeinsam als Momentaufnahme und wartete danach - durch die Entprellung
    im echten `UdpSender` ausgebremst - je Schluessel. Eine Aktualisierung,
    die waehrend dieser Wartezeit fuer einen ANDEREN, noch nicht abgearbeiteten
    Schluessel eintraf, wurde vom verspaeteten Resend anschliessend mit ihrem
    laengst veralteten Wert wieder ueberschrieben. Dieser Test simuliert das:
    `MutatingSender` schreibt beim ersten `send()` (fuer `voltage_key`) einen
    neuen Wert fuer `current_key` - einen Schluessel, den `resend_all()` noch
    vor sich hat."""
    _, _, store, device_id, _ = environment
    current_key = f"d{device_id}_2_current"

    def mutate() -> None:
        runtime._last_values[current_key] = 555.0

    sender = MutatingSender(mutate)
    runtime = Runtime(store, sender)
    await runtime.on_attribute(device_id, "2/144/4", 230000)  # fuellt voltage_key
    await runtime.on_attribute(device_id, "2/144/5", 100)  # fuellt current_key, danach im Dict
    sender.sent.clear()

    await runtime.resend_all()

    sent_currents = [v for k, v, _ in sender.sent if k == current_key]
    assert sent_currents[-1] == 555.0


async def test_resend_of_an_empty_runtime_sends_nothing(environment):
    runtime, _, _, _, _ = environment
    assert await runtime.resend_all() == 0


async def test_heartbeat_toggles(environment):
    """Spec 6.5: bridge_alive deckt "Container tot" und "Netz weg" gleichermassen ab."""
    _, sender, store, _, _ = environment
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.16)
    await runtime.stop()
    values = [v for k, v, _ in sender.sent if k == "bridge_alive"]
    assert len(values) >= 2
    assert values[0] != values[1]


async def test_heartbeat_survives_a_failed_send(environment):
    """Review-Fix Important #1: der Heartbeat deckt laut Modul-Docstring
    "Container tot" und "Netz weg" gleichermassen ab - ein einzelner
    fehlgeschlagener Sendeversuch darf die Watchdog-Schleife deshalb nicht
    beenden, sonst friert der Loxone-Watchdog auf dem letzten Wert ein,
    waehrend die Bruecke laengst schweigt."""
    _, _, store, _, _ = environment
    sender = FlakySender(fail_on_call=2)
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.22)
    await runtime.stop()
    values = [v for k, v, _ in sender.sent if k == "bridge_alive"]
    # Der zweite Aufruf schlaegt fehl (siehe FlakySender) - ohne den Fix
    # stuerbe die Schleife dort und es kaemen nie weitere Werte an.
    assert len(values) >= 3


async def test_stop_completes_even_if_a_task_already_died(environment):
    """Review-Fix Important #1, Begleitfehler: contextlib.suppress(CancelledError)
    unterdrueckt nur eine Cancellation, keine andere Exception, an der ein
    Task schon vor `stop()` gestorben ist. Die alte Implementierung liess
    `stop()` mit genau dieser Exception abbrechen und ueberspringt dabei das
    Leeren der Task-Liste."""
    runtime, _, _, _, _ = environment

    async def boom() -> None:
        raise RuntimeError("Task ist schon vor stop() gestorben")

    dead_task = asyncio.create_task(boom())
    await asyncio.sleep(0)  # den Task tatsaechlich sterben lassen
    assert dead_task.done()
    runtime._tasks.append(dead_task)

    await runtime.start()
    await runtime.stop()  # darf nicht an der bereits toten Task scheitern

    assert runtime._tasks == []
    assert runtime._pulse_tasks == set()


async def test_stop_lowers_an_in_flight_pulse(environment):
    """Review-Fix Important #2: eine Cancellation waehrend des Impuls-Schlafs
    ueberspringt sonst den `send(key, False)` - das digitale Signal bliebe
    bis zum naechsten Ereignis auf diesem Schluessel auf 1 haengen."""
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    await runtime.stop()
    key = f"d{button_device_id}_1_press"
    values = [v for k, v, _ in sender.sent if k == key]
    assert values[-1] is False


async def test_stop_completes_even_if_lowering_a_pulse_fails(environment):
    """Review-Fix M11, 2026-09-02: die Schleife, die jeden gerade high
    stehenden Impuls senkt, lief vor dem Fix ungeschuetzt vor dem
    Task-Abbruch. Scheiterte ein Sendeversuch dort (z. B. ein bereits
    geschlossener `UdpSender`), brach die ganze Methode dort ab - JEDES
    `task.cancel()` und beide `.clear()`-Aufrufe wurden uebersprungen.
    `stop()` ist selbst der Aufraeum-Pfad; ein fehlgeschlagener Sendeversuch
    darf ihn nicht mitreissen."""
    runtime, _, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    assert runtime._pulses_high  # der Impuls steht noch auf True

    runtime._sender = FlakySender(fail_on_call=1)  # der naechste send() scheitert
    await runtime.start()

    await runtime.stop()  # darf nicht am fehlschlagenden Sender scheitern

    assert runtime._pulses_high == set()
    assert runtime._tasks == []
    assert runtime._pulse_tasks == set()


async def test_invalidate_index_lets_a_newly_registered_signal_through(environment, monkeypatch):
    """Review-Fix Important #3: `Store.register_signals` kann jederzeit ein
    neues Signal zu einem schon indizierten Geraet hinzufuegen (z. B. nach
    einem Firmware-Update). Ohne `invalidate_index` bleibt dieses Signal fuer
    die Laufzeit unsichtbar, weil `_signal_for` nur einmal pro Geraet aus der
    Datenbank liest."""
    runtime, sender, store, device_id, _ = environment
    plug_raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    plug_snap = NodeSnapshot.from_raw(plug_raw["node_id"], plug_raw)

    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # Erstmaliges Indizieren durch die Laufzeit - der Pfad existiert noch nicht.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)
    store.register_signals(device_id, plug_snap)

    # Der Cache der Laufzeit weiss noch nichts vom neuen Signal.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    runtime.invalidate_index(device_id)
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.keys() == [key]


def _plug_snapshot() -> NodeSnapshot:
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


async def test_seed_from_snapshot_populates_cache_without_sending(environment):
    """Live-Lauf vom 2026-09-02 (Spec 6.4): `resend_all()` schickt beim Start
    nichts, weil `_last_values` leer ist - ein Wert landet dort sonst nur
    ueber eine Subscription, die *sich aendernde* Werte meldet. Ein Stecker
    ohne Last meldet z. B. nie eine sich aendernde Spannung. Das Saeen fuellt
    den Cache direkt aus dem aktuellen Geraetezustand, sendet dabei aber
    selbst nichts - siehe Docstring von `seed_from_snapshot`. 110 Attribut-
    signale plus 1 Online-Signal (Review-Fix C1, 2026-09-02)."""
    runtime, sender, _, device_id, _ = environment

    seeded = await runtime.seed_from_snapshot([_plug_snapshot()])

    assert seeded == 111
    assert len(runtime._last_values) == 111
    assert runtime._last_values[f"d{device_id}_online"] is True
    assert sender.sent == []


async def test_seed_from_snapshot_seeds_an_unavailable_node_as_offline(environment):
    """Review-Fix C1, 2026-09-02: der Kern des Fehlers. `start_listening()`
    fuellt den initialen Node-Cache OHNE NODE_ADDED zu feuern, und
    NODE_UPDATED kommt nur bei einer Node-Daten-Nachricht - der einzige
    Schreiber von `d<id>_online` (`set_online`) liefe also nach einem
    Bruecken-Start nie, und der Schluessel bliebe auf seinem `DefVal="0"`
    haengen (liest sich in Loxone als "nicht erreichbar"). Das Saeen muss die
    Erreichbarkeit deshalb selbst aus dem Snapshot uebernehmen."""
    runtime, sender, _, device_id, _ = environment
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    raw = dict(raw)
    raw["available"] = False
    offline_snap = NodeSnapshot.from_raw(raw["node_id"], raw)

    await runtime.seed_from_snapshot([offline_snap])

    assert runtime._last_values[f"d{device_id}_online"] is False
    assert sender.sent == []


async def test_resend_after_seeding_sends_every_seeded_value(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.seed_from_snapshot([_plug_snapshot()])

    count = await runtime.resend_all()

    assert count == 111
    assert len(sender.sent) == 111
    assert all(force for _, _, force in sender.sent)
    assert (f"d{device_id}_online", True, True) in sender.sent


async def test_seed_from_snapshot_skips_attribute_without_a_stored_signal(environment):
    """Ein Snapshot kann Attribute enthalten, die beim Export nicht dabei
    waren (z. B. ein neuer Cluster nach einem Firmware-Update, der noch nicht
    exportiert wurde) - das darf das Saeen nicht mit einem Fehler abbrechen,
    sondern wird genau wie bei `on_attribute` uebersprungen."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    raw = dict(raw)
    attributes = dict(raw["attributes"])
    attributes["9/9999/9"] = 42
    raw["attributes"] = attributes
    plug_snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    runtime, _, _, device_id, _ = environment

    seeded = await runtime.seed_from_snapshot([plug_snap])

    assert seeded == 111
    assert f"d{device_id}_9_c9999_a9" not in runtime._last_values


async def test_seeding_twice_does_not_double_anything(environment):
    runtime, sender, _, _, _ = environment
    snap = _plug_snapshot()

    await runtime.seed_from_snapshot([snap])
    await runtime.seed_from_snapshot([snap])

    assert len(runtime._last_values) == 111
    count = await runtime.resend_all()
    assert count == 111
    assert len(sender.sent) == 111


async def test_seed_from_snapshot_skips_an_unknown_node_without_aborting(environment):
    """Ein Snapshot kann einen Node melden, den `Store` (noch) nicht kennt -
    etwa ein Geraet, das noch nie exportiert wurde. Das darf den Start nicht
    abbrechen; nur dieser Node wird uebersprungen, alle anderen werden trotzdem
    gesaet."""
    runtime, sender, _, _, _ = environment
    unknown = NodeSnapshot(
        node_id=999_999,
        vendor_name="",
        product_name="",
        unique_id="",
        attributes={"2/144/4": 230000},
    )

    seeded = await runtime.seed_from_snapshot([unknown, _plug_snapshot()])

    assert seeded == 111
    assert sender.sent == []


async def test_last_values_for_returns_only_that_devices_keys(environment):
    """Fuer die Geraete-API (Task 2, Phase 5): `last_values_for` darf Werte
    eines anderen Geraets nicht mit einsammeln - auch nicht, wenn dessen
    device_id als Ziffernfolge die eigene device_id als Praefix enthaelt."""
    runtime, _, _, device_id, button_device_id = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    await runtime.set_online(button_device_id, True)

    values = runtime.last_values_for(device_id)

    assert values == {f"d{device_id}_2_voltage": pytest.approx(230.0)}
    assert f"d{button_device_id}_online" not in values


async def test_last_values_for_is_empty_before_anything_is_known(environment):
    runtime, _, _, device_id, _ = environment
    assert runtime.last_values_for(device_id) == {}
