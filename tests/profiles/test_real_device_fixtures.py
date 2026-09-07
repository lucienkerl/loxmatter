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

"""Prueft die Tabelle an den echten Geraeten aus Phase 1."""

import json
from pathlib import Path

from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.table import Exportability, lookup

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_plug_matches_the_breakdown_recorded_in_spec_6_6():
    """Spec 6.6, Tabelle: 102 analog, 7 digital, 13 Text, 37 nicht abbildbar -
    plus Aufgabe 5: der Zaehlerstand (2/145/1) ist eine Struktur mit
    numerischem Element und wandert seither von NONE zu ANALOG (103/36)."""
    snap = load("ikea_grillplats_plug.json")
    signals = extract_signals(snap)
    zaehlung = {kind: 0 for kind in Exportability}
    for ref in signals:
        zaehlung[lookup(ref, snap.attributes.get(ref.path)).exportability] += 1

    assert len(signals) == 159
    assert zaehlung[Exportability.ANALOG] == 103
    assert zaehlung[Exportability.DIGITAL] == 7
    assert zaehlung[Exportability.TEXT] == 13
    assert zaehlung[Exportability.NONE] == 36  # 32 Listen/Structs - 1 + 5 Nullwerte


def test_only_110_of_the_plugs_signals_reach_a_udp_input():
    """Nicht 45, sondern 49 fallen weg - die 5 Nullwerte kommen zu den
    verbleibenden 44 Listen/Structs dazu (Aufgabe 5 zieht den Zaehlerstand
    aus seiner Struktur und macht ihn abbildbar)."""
    snap = load("ikea_grillplats_plug.json")
    abbildbar = [
        ref
        for ref in extract_signals(snap)
        if lookup(ref, snap.attributes.get(ref.path)).exportability
        in (Exportability.ANALOG, Exportability.DIGITAL)
    ]
    assert len(abbildbar) == 110


def test_plug_power_attribute_carries_kw():
    snap = load("ikea_grillplats_plug.json")
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 8)
    assert lookup(ref, snap.attributes.get(ref.path)).unit == "kW"


def test_every_button_event_is_named():
    snap = load("ikea_bilresa_button.json")
    events = [s for s in extract_signals(snap) if s.cluster_id == 59 and s.kind.value == "event"]
    assert len(events) == 12
    assert all(not lookup(e, None).slug.startswith("c59_e") for e in events)


def test_rgbw_lamp_accepts_move_to_hue_and_saturation():
    """Der Beleg, auf dem die Freischaltung von (768, 6) steht (Spec 4.1).

    Schlaegt dieser Test fehl, ist der Entwurf falsch - dann erwartet die
    Leuchte MoveToColor (7, xy) und es fehlt eine Farbraumumrechnung, die
    es im Projekt nirgends gibt (Spec 10.2)."""
    snap = load("ikea_kajplats_cws_lamp.json")
    accepted = snap.attributes["1/768/65529"]
    assert 6 in accepted


def test_both_lamps_report_their_physical_colour_temperature_limits():
    """Ohne diese beiden Attribute bliebe `range` leer und der
    Kelvin-Regler unbegrenzt (Spec 6.4)."""
    for name in ("ikea_kajplats_ws_lamp.json", "ikea_kajplats_cws_lamp.json"):
        snap = load(name)
        assert isinstance(snap.attributes["1/768/16395"], int)
        assert isinstance(snap.attributes["1/768/16396"], int)


def test_the_ws_lamp_has_no_hue_saturation_command():
    """Belegt die Abstufung aus Spec 6.3: die WS-Leuchte bekommt keine
    Tableiste, weil sie kein Hue/Sat-Kommando hat - nicht, weil der Code
    ihr Modell kennt.

    Sie fuehrt sehr wohl MoveToColor (7) und damit den XY-Farbraum
    (FeatureMap 24 = XY|CT). Der bleibt bewusst ungenutzt: eine
    xy-Umrechnung gibt es im Projekt nicht, und fuer eine Weisston-Leuchte
    waere sie ein Bedienelement fuer eine Faehigkeit, die niemand von ihr
    erwartet."""
    accepted = load("ikea_kajplats_ws_lamp.json").attributes["1/768/65529"]
    assert 6 not in accepted
    assert 10 in accepted
    assert 7 in accepted  # XY vorhanden, aber nicht freigeschaltet


def test_the_cws_lamp_advertises_the_full_colour_feature_set():
    """FeatureMap 31 = HS|EHUE|ColorLoop|XY|CT - die Grundlage dafuer, dass
    genau diese Leuchte beide Reiter bekommt und die WS-Leuchte nicht."""
    assert load("ikea_kajplats_cws_lamp.json").attributes["1/768/65532"] == 31
