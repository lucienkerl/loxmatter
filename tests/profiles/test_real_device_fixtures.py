# loxmatter - connects Matter devices to a Loxone Miniserver.
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

"""Checks the table against the real devices from phase 1."""

import json
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.table import Exportability, command_control, lookup

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def controls_of_kind(name: str, kind: str) -> list[str]:
    """The slugs of the controls this fixture would actually be given.

    Goes through `extract_commands` and `command_control`, i.e. the same two
    steps `api.control.controls` takes before the WebUI draws anything - not
    through the raw AcceptedCommandList. Two tests below used to read that
    list instead, and thereby reported a protection they never measured (see
    their docstrings).
    """
    snapshot = load(name)
    return [
        command.slug
        for command in extract_commands(snapshot)
        if command_control(command.cluster_id, command.command_id) == kind
    ]


def test_plug_matches_the_breakdown_recorded_in_spec_6_6():
    """Spec 6.6, table: 102 analog, 7 digital, 13 text, 37 not mappable -
    plus task 5: the meter reading (2/145/1) is a struct with a numeric
    element and has since moved from NONE to ANALOG (103/36)."""
    snap = load("ikea_grillplats_plug.json")
    signals = extract_signals(snap)
    counts = {kind: 0 for kind in Exportability}
    for ref in signals:
        counts[lookup(ref, snap.attributes.get(ref.path)).exportability] += 1

    assert len(signals) == 159
    assert counts[Exportability.ANALOG] == 103
    assert counts[Exportability.DIGITAL] == 7
    assert counts[Exportability.TEXT] == 13
    assert counts[Exportability.NONE] == 36  # 32 lists/structs - 1 + 5 null values


def test_only_110_of_the_plugs_signals_reach_a_udp_input():
    """Not 45 but 49 drop out - the 5 null values are added to the
    remaining 44 lists/structs (task 5 pulls the meter reading out of its
    struct and makes it mappable)."""
    snap = load("ikea_grillplats_plug.json")
    exportable = [
        ref
        for ref in extract_signals(snap)
        if lookup(ref, snap.attributes.get(ref.path)).exportability
        in (Exportability.ANALOG, Exportability.DIGITAL)
    ]
    assert len(exportable) == 110


def test_plug_power_attribute_carries_kw():
    snap = load("ikea_grillplats_plug.json")
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 8)
    assert lookup(ref, snap.attributes.get(ref.path)).unit == "kW"


def test_every_button_event_is_named():
    snap = load("ikea_bilresa_button.json")
    events = [s for s in extract_signals(snap) if s.cluster_id == 59 and s.kind.value == "event"]
    assert len(events) == 12
    assert all(not lookup(e, None).slug.startswith("c59_e") for e in events)


def test_rgbw_lamp_is_given_both_colour_controls():
    """The positive case for the capability gate: a real colour lamp keeps
    every colour control it had.

    This lamp declares FeatureMap 31 (HS|EHUE|ColorLoop|XY|CT), so both
    MoveToHueAndSaturation (6) and MoveToColor (7) clear
    `profiles.capabilities`. The xy half matters because it is what the
    Zigbee work of 2026-09-12 turned on; the colour-space conversion the
    older wording here called missing has existed since then
    (`commands.color.rgb_to_cie_xy`).

    Asserted on the generated controls, not on the AcceptedCommandList: the
    list said nothing about what the UI builds, which is how the regression
    this test now covers went unnoticed."""
    assert controls_of_kind("ikea_kajplats_cws_lamp.json", "hue_sat") == ["color", "color_xy"]
    assert 6 in load("ikea_kajplats_cws_lamp.json").attributes["1/768/65529"]


def test_both_lamps_report_their_physical_colour_temperature_limits():
    """Without these two attributes, `range` would stay empty and the
    kelvin slider unbounded (spec 6.4)."""
    for name in ("ikea_kajplats_ws_lamp.json", "ikea_kajplats_cws_lamp.json"):
        snap = load(name)
        assert isinstance(snap.attributes["1/768/16395"], int)
        assert isinstance(snap.attributes["1/768/16396"], int)


def test_the_ws_lamp_is_given_no_colour_control():
    """Proves the distinction from spec 6.3: the white-spectrum lamp gets no
    colour tab - not because the code knows its model, but because its
    ColorControl declares colour temperature beside XY and no hue/saturation,
    which is what a lamp that tunes white declares.

    Its AcceptedCommandList carries MoveToColor (7) and its FeatureMap
    declares XY (24 = XY|CT), so from 2026-09-12 until the capability gate
    landed it DID get a colour picker: `clusters.yaml` had begun naming
    command 7, and nothing in `extract_commands` asked whether the lamp could
    show a colour. Two white channels cannot, and the picker could not even
    read its own position back - CurrentHue (768/0) and CurrentSaturation
    (768/1) are absent from this lamp's AttributeList.

    The old version of this test asserted on the AcceptedCommandList alone
    and stayed green throughout that regression, which is why it now goes
    through the generated controls."""
    assert controls_of_kind("ikea_kajplats_ws_lamp.json", "hue_sat") == []
    # The Kelvin control is untouched - the gate takes away colour, not the
    # capability this lamp actually has.
    assert controls_of_kind("ikea_kajplats_ws_lamp.json", "kelvin") == ["colortemp"]

    accepted = load("ikea_kajplats_ws_lamp.json").attributes["1/768/65529"]
    assert 6 not in accepted
    assert 7 in accepted  # accepted by the lamp, and still withheld
    assert 10 in accepted


def test_the_cws_lamp_advertises_the_full_colour_feature_set():
    """FeatureMap 31 = HS|EHUE|ColorLoop|XY|CT - the basis for
    exactly this lamp getting both tabs and the WS lamp not."""
    assert load("ikea_kajplats_cws_lamp.json").attributes["1/768/65532"] == 31


def test_the_same_lamp_declaring_xy_without_colour_temperature_gets_the_xy_picker():
    """The WS lamp's own snapshot with its declaration changed to XY alone
    (FeatureMap and ColorCapabilities 8): what a Matter lamp that takes
    colour only as xy looks like. Such a lamp is not a white-spectrum lamp -
    one that tunes white declares CT - so MoveToColor is its colour command,
    and it gets exactly one colour control, `color_xy`.

    Until 13 September 2026 (768, 7) required hue/saturation as well, and
    this lamp got no colour control at all, while the design and the change
    notes promised colour on lamps that only accept XY.

    The endpoint's device type is taken out of the edited snapshot, so what
    is measured here is the bits alone, with no device type to add colour.

    Fault to prove it: require HS for (768, 7) again (the list comes out
    empty)."""
    snapshot = load("ikea_kajplats_ws_lamp.json")
    attributes = {
        **{path: value for path, value in snapshot.attributes.items() if path != "1/29/0"},
        "1/768/65532": 8,
        "1/768/16394": 8,
    }
    xy_only = NodeSnapshot(
        technology="matter",
        address=snapshot.address,
        vendor_name=snapshot.vendor_name,
        product_name=snapshot.product_name,
        unique_id=snapshot.unique_id,
        attributes=attributes,
    )
    hue_sat = [
        command.slug
        for command in extract_commands(xy_only)
        if command_control(command.cluster_id, command.command_id) == "hue_sat"
    ]
    assert hue_sat == ["color_xy"]
