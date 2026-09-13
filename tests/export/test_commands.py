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

import json
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID, FEATURE_MAP_ID
from loxmatter.profiles import table
from loxmatter.profiles.capabilities import COLOR_CAPABILITIES_ID
from loxmatter.profiles.table import ADMINISTRATIVE_CLUSTERS, command_slug

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_administrative_clusters_are_named():
    """These clusters must never appear as a Loxone output."""
    for cluster in (31, 41, 42, 48, 49, 50, 51, 56, 60, 62, 63):
        assert cluster in ADMINISTRATIVE_CLUSTERS


def test_known_command_has_a_slug():
    assert command_slug(6, 0) == "off"
    assert command_slug(6, 1) == "on"
    assert command_slug(6, 2) == "toggle"


def test_unknown_command_has_none():
    assert command_slug(6, 99) is None
    assert command_slug(64999, 0) is None


def test_plug_yields_only_the_onoff_commands():
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert {(c.cluster_id, c.command_id) for c in commands} == {(6, 0), (6, 1), (6, 2)}
    assert all(c.endpoint == 1 for c in commands)


def test_button_yields_no_commands():
    """A button is an input device."""
    assert extract_commands(load("ikea_bilresa_button.json")) == []


def test_administrative_commands_never_appear():
    """Sanity check against the real plug fixture - not proof of the gate.

    In normal mode `command_slug()` returns `None` for every administrative
    cluster anyway, because none of them has a `commands` entry in
    `clusters.yaml`. So this test would still be green even if the
    ADMINISTRATIVE_CLUSTERS gate in `extract_commands()` were removed
    entirely. The actual proof of the gate lives in
    `test_raw_mode_adds_unknown_clusters_but_not_administrative_ones` (raw
    mode) and in `test_gate_blocks_administrative_cluster_even_with_table_entry`
    below, which pins the gate independently of fixture data.
    """
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in commands)


def test_raw_mode_adds_unknown_clusters_but_not_administrative_ones():
    """Raw mode extends the allow list - it does not lift the safety rule."""
    plug = load("ikea_grillplats_plug.json")
    roh = extract_commands(plug, raw=True)
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in roh)
    assert len(roh) > len(extract_commands(plug))
    assert any(c.cluster_id == 4 for c in roh)  # Groups, unknown but harmless


def test_raw_mode_names_unknown_commands_generically():
    roh = extract_commands(load("ikea_grillplats_plug.json"), raw=True)
    unbekannt = next(c for c in roh if c.cluster_id == 4)
    assert unbekannt.slug.startswith("c4_cmd")


def test_gate_blocks_administrative_cluster_even_with_table_entry(monkeypatch):
    """Pins the ADMINISTRATIVE_CLUSTERS gate itself, independently of fixture data.

    Cluster 62 (OperationalCredentials) gets a real `commands` entry in the
    profile table for this test - in normal mode `command_slug()` would then
    find the command and `extract_commands()` would emit it, were the
    ADMINISTRATIVE_CLUSTERS check in `extract_commands()` no longer there. If
    this test fails, the gate was removed or bypassed - regardless of whether
    `clusters.yaml` happens to stay empty for administrative clusters.
    """
    assert 62 in ADMINISTRATIVE_CLUSTERS
    patched = dict(table._table())
    patched[62] = {"commands": {10: {"slug": "remove_fabric", "takes_value": False}}}
    monkeypatch.setattr(table, "_table", lambda: patched)

    path = f"1/62/{ACCEPTED_COMMAND_LIST_ID}"
    snapshot = NodeSnapshot(
        technology="matter",
        address="999",
        vendor_name="test",
        product_name="test",
        unique_id="test",
        attributes={path: [10]},
    )

    assert extract_commands(snapshot) == []
    assert extract_commands(snapshot, raw=True) == []


# --- The ColorControl capability gate (profiles/capabilities.py) -------------
#
# Every case below uses the SAME AcceptedCommandList and varies only what the
# cluster declares about itself. That is the point: the gate must read the
# device's declaration, never the fixture it came from.

_COLOUR_CLUSTER = 768
_COLOUR_COMMANDS = [6, 7, 10]


def _colour_snapshot(declaration: dict[str, object]) -> NodeSnapshot:
    """A lamp that accepts both colour commands plus the colour temperature,
    with whatever `declaration` says about its ColorControl features."""
    return NodeSnapshot(
        technology="matter",
        address="999",
        vendor_name="test",
        product_name="test",
        unique_id="test",
        attributes={
            f"1/{_COLOUR_CLUSTER}/{ACCEPTED_COMMAND_LIST_ID}": _COLOUR_COMMANDS,
            **declaration,
        },
    )


def _colour_command_ids(snapshot: NodeSnapshot, *, raw: bool = False) -> set[int]:
    return {c.command_id for c in extract_commands(snapshot, raw=raw) if c.cluster_id == 768}


def test_colour_commands_need_the_declared_feature():
    """FeatureMap 31 (HS|EHUE|CL|XY|CT) clears both colour commands."""
    snapshot = _colour_snapshot({f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 31})
    assert _colour_command_ids(snapshot) == {6, 7, 10}


def test_a_white_only_lamp_is_denied_both_colour_commands():
    """FeatureMap 24 = XY|CT, the real KAJPLATS white-spectrum lamp.

    Command 6 fails on the missing hue/saturation bit. Command 7 is the
    deliberate part: its payload needs only XY, which this lamp declares, but
    XY beside CT and without HS is what a lamp that tunes white declares,
    and the control built on command 7 is a full-gamut colour picker. See
    `profiles/capabilities.py`.

    The colour temperature is untouched: it has no entry in the gate at all.
    """
    snapshot = _colour_snapshot({f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 24})
    assert _colour_command_ids(snapshot) == {10}


def test_a_lamp_declaring_xy_alone_takes_colour_as_xy():
    """FeatureMap 8 = XY, no HS, no CT: a colour lamp whose one colour
    command is MoveToColor. It is not a white-spectrum lamp - that one
    declares CT - so command 7 clears the gate and command 6 does not.

    The first version of the gate required HS for command 7 too, and gave
    this lamp no colour control at all.

    Fault to prove it: require `XY | HS` for (768, 7) alone again."""
    snapshot = _colour_snapshot({f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 8})
    assert _colour_command_ids(snapshot) == {7, 10}


def test_colour_capabilities_answers_when_there_is_no_feature_map():
    """ColorCapabilities (0x400A) carries the same bits and is what a Zigbee
    lamp brings - a ZCL cluster has no FeatureMap at all. Without this second
    source the gate would deny every Zigbee colour lamp."""
    snapshot = _colour_snapshot({f"1/{_COLOUR_CLUSTER}/{COLOR_CAPABILITIES_ID}": 31})
    assert _colour_command_ids(snapshot) == {6, 7, 10}


def test_a_lamp_that_declares_nothing_gains_no_colour_control():
    """Silence is not consent. A snapshot carrying neither FeatureMap nor
    ColorCapabilities makes no claim about colour, and a gate that reads that
    as permission is not a gate."""
    snapshot = _colour_snapshot({})
    assert _colour_command_ids(snapshot) == {10}


def test_raw_mode_does_not_lift_the_capability_gate():
    """Raw mode widens what gets a NAME, not what a device can do - the same
    stance ADMINISTRATIVE_CLUSTERS takes."""
    snapshot = _colour_snapshot({f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 24})
    assert _colour_command_ids(snapshot, raw=True) == {10}


# The two cases below exist because the five above could not tell the gate's
# two rules apart. (Written when (768, 7) had one rule, `XY | HS`; it now
# also permits `XY` without `CT`, and the XY-only cases above pin that
# second rule. Both mutations below still fail one of these two cases.) The reviewer of 12 September 2026 mutated
# `profiles/capabilities.py` twice and the whole suite stayed green:
# `COLOUR_FEATURE_XY = 0x08` changed to `0x10` (the XY constant made to point
# at the colour-temperature bit), and `(768, 7)`'s requirement reduced from
# `XY | HS` to plain `HS` (the deliberate half deleted). Every gated case
# used FeatureMap 24, 31, or nothing - none of them separates XY from CT, and
# none of them has HS without XY, so nothing measured either rule.
#
# It takes both cases to close both mutations, and neither closes the other:
#
# | FeatureMap | correct | XY -> 0x10 | (768,7) -> HS |
# | 3  = HS|EHUE | {6, 10}    | {6, 10} (passes) | {6, 7, 10} (fails) |
# | 9  = HS|XY   | {6, 7, 10} | {6, 10} (fails)  | {6, 7, 10} (passes) |


def test_hue_without_xy_keeps_the_hue_command_and_loses_the_xy_one():
    """FeatureMap 3 = HS|EnhancedHue: colour, declared without xy.

    Command 6 clears the gate on the HS bit. Command 7 does not, and only
    this shape of lamp proves it: 7 asks for XY **and** HS, so a lamp
    declaring HS alone is the only one whose answer differs between that
    rule and the plain `HS` a careless edit would leave behind.

    Such a lamp exists in the Matter specification - the hue/saturation
    feature stands on its own and xy is not implied by it - though not in
    this repository's fixture set, which is why it is written out by hand
    here rather than loaded."""
    snapshot = _colour_snapshot({f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 3})
    assert _colour_command_ids(snapshot) == {6, 10}


def test_hue_with_xy_and_no_colour_temperature_keeps_both_colour_commands():
    """FeatureMap 9 = HS|XY: a colour lamp that cannot do white at all.

    The counterpart to the case above, and the one that pins WHICH bit XY
    is. Every other cleared case here declares CT as well (24 and 31 both
    carry it), so a mix-up of the XY bit (0x08) with the colour-temperature
    bit (0x10) changes none of their answers. This lamp declares XY and not
    CT, so it clears command 7 with the constant right and fails it with
    the constant wrong.

    Command 10 rides along untouched, as everywhere else here: the gate is
    an exception list and has no entry for the colour temperature.
    """
    snapshot = _colour_snapshot({f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 9})
    assert _colour_command_ids(snapshot) == {6, 7, 10}


def test_a_feature_map_of_zero_does_not_silence_the_colour_capabilities():
    """Review finding Minor, 12 September 2026: the fallback took the first
    source PRESENT, so a device reporting `FeatureMap = 0` next to a full
    `ColorCapabilities = 31` was denied on the strength of the zero.

    Matter requires the two attributes to carry the same bits, so this
    device is non-conformant either way - but a zero is the one shape of
    disagreement that reads as an unpopulated attribute rather than as a
    claim, and the charitable reading costs nothing."""
    snapshot = _colour_snapshot(
        {
            f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 0,
            f"1/{_COLOUR_CLUSTER}/{COLOR_CAPABILITIES_ID}": 31,
        }
    )
    assert _colour_command_ids(snapshot) == {6, 7, 10}


def test_a_non_zero_feature_map_still_outranks_the_colour_capabilities():
    """The other half of the same decision, and the reason this is not an
    OR across the two sources: a lamp whose FeatureMap declares its own,
    non-zero set is judged by that set alone.

    These are the real white-spectrum lamp's 24 = XY|CT with an imagined
    sloppy ColorCapabilities beside it. OR-ing would hand it 31 and draw
    the colour picker that `profiles/capabilities.py` exists to withhold;
    reading past a zero only, and never past a declaration, does not."""
    snapshot = _colour_snapshot(
        {
            f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 24,
            f"1/{_COLOUR_CLUSTER}/{COLOR_CAPABILITIES_ID}": 31,
        }
    )
    assert _colour_command_ids(snapshot) == {10}


def test_every_source_declaring_zero_is_still_a_denial():
    """A zero is skipped so a later source can answer - not discarded. With
    nothing else to answer, the declaration of no features stands and the
    colour commands stay withheld, exactly as for a device that declares
    nothing at all."""
    snapshot = _colour_snapshot(
        {
            f"1/{_COLOUR_CLUSTER}/{FEATURE_MAP_ID}": 0,
            f"1/{_COLOUR_CLUSTER}/{COLOR_CAPABILITIES_ID}": 0,
        }
    )
    assert _colour_command_ids(snapshot) == {10}
