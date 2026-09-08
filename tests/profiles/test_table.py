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

import re
from pathlib import Path

import pytest

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles import table
from loxmatter.profiles.table import (
    _UNIT_DECIMALS,
    MAX_LOXONE_DECIMALS,
    Exportability,
    classify,
    command_control,
    is_exportable,
    known_attribute_section,
    known_command_pairs,
    lookup,
    names_element,
    scale_factor,
    unit_format,
)


def test_bool_is_digital():
    assert classify(True) is Exportability.DIGITAL


def test_numbers_are_analog():
    assert classify(0) is Exportability.ANALOG
    assert classify(-42) is Exportability.ANALOG
    assert classify(1.5) is Exportability.ANALOG


def test_strings_are_text():
    assert classify("IKEA of Sweden") is Exportability.TEXT


def test_lists_and_structs_are_not_exportable():
    """Spec 6.6: Loxone has no equivalent for nested values."""
    assert classify([29, 31, 40]) is Exportability.NONE
    assert classify([{"0": 5, "1": True}]) is Exportability.NONE
    assert classify({"0": 5}) is Exportability.NONE


def test_null_is_not_exportable():
    """Spec 6.6: delivered null values are their own category."""
    assert classify(None) is Exportability.NONE


def test_known_attribute_gets_name_and_unit():
    ref = SignalRef(1, 1026, 0, SignalKind.ATTRIBUTE)  # TemperatureMeasurement
    profile = lookup(ref, 2150)
    assert profile.slug == "temp"
    assert profile.unit == "°C"
    assert profile.exportability is Exportability.ANALOG


def test_power_is_named_and_carries_kw():
    """Spec 7.3: the target unit is the Loxone block's, not the SI unit."""
    ref = SignalRef(2, 144, 8, SignalKind.ATTRIBUTE)  # ActivePower
    profile = lookup(ref, 5000)
    assert profile.slug == "power"
    assert profile.unit == "kW"


def test_unknown_cluster_still_gets_a_profile():
    """Spec 3.5: the table is enrichment, not a gatekeeper."""
    ref = SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 42)
    assert profile.exportability is Exportability.ANALOG
    assert profile.slug == "c64999_a7"
    assert profile.unit == ""


def test_unknown_cluster_with_unmappable_value_is_not_exportable():
    ref = SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)
    assert lookup(ref, [1, 2, 3]).exportability is Exportability.NONE


def test_events_are_digital_regardless_of_value():
    """Spec 6.3: an event becomes a pulse, it has no value."""
    ref = SignalRef(1, 59, 1, SignalKind.EVENT)
    assert lookup(ref, None).exportability is Exportability.DIGITAL


def test_power_gets_the_finest_resolution_loxone_accepts():
    """This test used to require `<v.6>`, for good reason: mW to kW spans
    six orders of magnitude, and with three digits a 300 mW standby
    consumer disappears as 0.000 (Spec 7.3).

    But the Miniserver doesn't accept six digits - checked on the device
    on 2026-09-03. Three is thus not the desired but the achievable
    resolution, and a format string the Miniserver rejects would be worse
    than a coarse display.

    All that's lost is the DISPLAY below one watt: the format string
    describes how Loxone shows the number, not which number arrives.
    Blocks and statistics keep computing with the full value.
    """
    assert unit_format("kW") == "<v.3> kW"
    assert unit_format("kWh") == "<v.3> kWh"


def test_unit_format_uses_one_decimal_for_the_common_units():
    assert unit_format("°C") == "<v.1> °C"
    assert unit_format("%") == "<v.1>%"
    assert unit_format("V") == "<v.1> V"
    assert unit_format("A") == "<v.1> A"


def test_unit_format_for_empty_unit_is_empty():
    assert unit_format("") == ""


def test_names_element_separates_named_from_generic_within_a_known_cluster():
    """The table knows cluster 6 and names only attribute 0 there. This
    exact distinction is what the fine-grained selection relies on:
    `onoff` is wanted, StartUpOnOff (0x4003) is not."""
    known = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    generic = SignalRef(1, 6, 0x4003, SignalKind.ATTRIBUTE)
    assert names_element(known) is True
    assert names_element(generic) is False


def test_names_element_is_false_for_a_cluster_the_table_does_not_know():
    """An unknown cluster names nothing. The caller (relevance) must NOT
    conclude 'everything off' from that - see there."""
    assert names_element(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is False


def test_names_element_covers_events_too():
    """Cluster 59 names its events; the fine-grained selection must not
    discard a button press as unnamed."""
    assert names_element(SignalRef(1, 59, 1, SignalKind.EVENT)) is True


def test_known_attribute_section_true_for_a_cluster_that_names_attributes():
    assert known_attribute_section(6) is True


def test_known_attribute_section_false_for_a_cluster_the_table_does_not_know():
    assert known_attribute_section(4711) is False


def test_known_attribute_section_false_for_a_cluster_known_only_for_its_commands(
    monkeypatch,
):
    """Review-Fix 1b (follow-up fix, phase 6): a cluster can be in the
    table without saying anything about its attributes - exactly the
    shape cluster 768 (ColorControl) was in until this fix (only
    `commands:`, no `attributes:`). Checked with a synthetic table instead
    of on cluster 768 itself, so this test captures the trap as such and
    doesn't go silent now that 768 has an `attributes:` section."""
    monkeypatch.setattr(
        "loxmatter.profiles.table._table",
        lambda: {
            999: {
                "name": "commands_only",
                "commands": {1: {"slug": "go", "takes_value": False}},
            }
        },
    )
    assert known_attribute_section(999) is False


def test_the_battery_level_is_named_and_scaled_to_percent():
    """Matter counts BatPercentRemaining in half percent (0-200). Without
    the factor, Loxone would show 200% at a full battery."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 190)
    assert profile.slug == "battery"
    assert profile.unit == "%"
    assert scale_factor(ref) == pytest.approx(0.5)


def test_a_generic_signal_keeps_its_slug_but_gains_a_readable_title():
    """The key stays generic - it is the wiring in Loxone and must never
    move. Only the display becomes readable."""
    ref = SignalRef(0, 51, 1, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 3)
    assert profile.slug == "c51_a1"
    assert profile.title != "c51_a1"


def test_a_table_named_signal_uses_its_own_name_for_both():
    """Where the project's own table knows something, it wins: `onoff` is
    more descriptive than `OnOff`, and the SDK doesn't know the unit
    anyway."""
    profile = lookup(SignalRef(1, 6, 0, SignalKind.ATTRIBUTE), True)
    assert profile.slug == "onoff"
    assert profile.title == "onoff"


def test_a_signal_the_catalog_does_not_know_falls_back_to_the_slug():
    ref = SignalRef(1, 4711, 3, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 1)
    assert profile.slug == "c4711_a3"
    assert profile.title == "c4711_a3"


_ENERGY = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)


def test_a_struct_member_becomes_an_analog_signal():
    """Matter delivers the meter reading as a struct of value and
    timestamps. Without pulling it out, it falls through as 'not
    mappable' - and that's the value someone buys a metering plug for."""
    raw = {"0": 12_345_678, "1": 1_700_000_000, "2": 1_700_003_600}
    assert lookup(_ENERGY, raw).exportability is Exportability.ANALOG
    assert lookup(_ENERGY, raw).slug == "energy_imported"


def test_a_struct_without_the_named_member_stays_unexportable():
    """Don't guess. A made-up number on a real energy block would be
    worse than a missing value."""
    assert lookup(_ENERGY, {"1": 1_700_000_000}).exportability is Exportability.NONE


def test_a_struct_member_that_is_not_a_number_stays_unexportable():
    """Deviation from the task sheet: the extracted value runs through the
    unchanged `classify` (task requirement), and that classifies a string
    as TEXT, not as NONE - but both count as not exportable per
    `is_exportable` (Spec 6.6). The original assertion
    `is Exportability.NONE` could only have held if `struct_member` itself
    distinguished between number and text - that's `classify`'s job, not
    `struct_member`'s, or the classification logic would exist twice."""
    assert not is_exportable(lookup(_ENERGY, {"0": "viel"}).exportability)


def test_a_null_value_stays_unexportable_even_with_a_field():
    assert lookup(_ENERGY, None).exportability is Exportability.NONE


def test_an_integer_key_is_accepted_as_well_as_a_string_key():
    """The string is what matter-server delivers today; a different
    serialization of the same struct would be just as plausible with a
    number."""
    assert lookup(_ENERGY, {0: 5_000_000}).exportability is Exportability.ANALOG


def test_a_cluster_without_a_field_entry_still_sees_the_whole_value():
    """Only a cluster the table knows may name an element. An unknown
    struct stays unknown."""
    ref = SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)
    assert lookup(ref, {"0": 5}).exportability is Exportability.NONE


def test_no_unit_format_exceeds_what_loxone_accepts():
    """The Miniserver accepts at most three decimal places; `<v.4>` and
    higher doesn't work (checked on the device, 2026-09-03).

    The check runs over ALL entries of the table, not a selection: the bug
    arose because someone - rightly - wanted more digits for power and
    nobody knew the limit. A format string the Miniserver rejects would
    otherwise only surface at import time, i.e. for the user.
    """
    for unit in _UNIT_DECIMALS:
        rendered = unit_format(unit)
        decimals = int(rendered.split("<v.")[1].split(">")[0])
        assert decimals <= MAX_LOXONE_DECIMALS, f"{unit!r} produces {rendered!r}"


def test_a_cluster_with_a_rank_reports_it():
    """The rank decides what appears on the tile as the primary signal -
    it must therefore come from the table and not from an assumption."""
    assert table.rank_for(6) == 10  # OnOff
    assert table.rank_for(59) == 10  # Switch
    assert table.rank_for(47) == 90  # PowerSource


def test_a_cluster_without_a_rank_gets_the_default():
    """Cluster 3 (Identify) is not in the table. It must land neither in
    front nor behind the battery: the default is the middle, so that a
    new device type never accidentally leads with its battery level while
    its main feature still comes before administrative data (design 4)."""
    assert table.rank_for(3) == table.DEFAULT_RANK
    assert table.DEFAULT_RANK == 50


def test_the_utility_clusters_rank_behind_everything_functional():
    """The one rule this design exists for in the first place."""
    functional = [table.rank_for(c) for c in (6, 8, 59, 144, 145, 768, 1026, 1029)]
    assert max(functional) < table.rank_for(47)
    assert table.rank_for(47) < table.rank_for(40)


def test_an_element_can_carry_its_own_rank():
    """The button was the concrete case that made this level necessary:
    within cluster 59, the button press must come before the static
    `NumberOfPositions` value, otherwise the tile leads with a number that
    never changes."""
    press = SignalRef(1, 59, 1, SignalKind.EVENT)
    positions = SignalRef(1, 59, 0, SignalKind.ATTRIBUTE)

    assert table.element_rank_for(press) < table.element_rank_for(positions)


def test_an_element_without_a_rank_gets_the_default():
    """The same default as at the cluster level, and for the same reason: the
    middle, so that an unlisted element falls neither to the front nor all
    the way to the back."""
    longpress = SignalRef(1, 59, 2, SignalKind.EVENT)
    assert table.element_rank_for(longpress) == table.DEFAULT_RANK


def test_an_element_of_an_unknown_cluster_gets_the_default():
    """Cluster 3 (Identify) is not in the table - there is neither a
    section nor an element there in which a rank could sit."""
    assert table.element_rank_for(SignalRef(1, 3, 0, SignalKind.ATTRIBUTE)) == table.DEFAULT_RANK


def test_every_rank_in_the_table_is_an_integer():
    """Finding (final review): the old loop only ran over
    `cluster["rank"]` - it never saw the ELEMENT RANKS under
    `attributes:`/`events:` (e.g. `events: {1: {slug: press, rank: 10}}`,
    see cluster 59 in `clusters.yaml`), even though the name "every rank in
    the table" promises that. A `rank: 10.5` on an element (someone wants
    to slot it between two others) would silently become 10 via
    `int(10.5)` in `element_rank_for` - the order drifts from the
    intent, but the old test stayed green because it never looked at the
    element level at all.

    The old reasoning was also wrong: `rank_for` and
    `element_rank_for` both call `int(rank)` - a `rank: "10"` from
    a typo would therefore NOT "throw when sorting against a
    number", but silently become 10. The actual damage is
    a silent deviation from the sort intent, not a crash.

    Finding (re-check before the merge): `isinstance(x, int)` is also
    true for `True`/`False`, because `bool` in Python inherits from
    `int` - a `rank: true` (a YAML typo for a number) would therefore
    have slipped through undetected. This existed at the cluster level
    already and merely traveled along with the extension to the element
    level. `not isinstance(x, bool)` rules out exactly this case on both
    levels."""
    for cluster_id, cluster in table._table().items():
        if "rank" in cluster:
            rank = cluster["rank"]
            assert isinstance(rank, int) and not isinstance(rank, bool), cluster_id
        for section in ("attributes", "events"):
            for element_id, element in (cluster.get(section) or {}).items():
                if isinstance(element, dict) and "rank" in element:
                    rank = element["rank"]
                    assert isinstance(rank, int) and not isinstance(rank, bool), (
                        cluster_id,
                        section,
                        element_id,
                    )


@pytest.mark.parametrize(
    ("cluster_id", "command_id", "control"),
    [
        (6, 0, "none"),
        (6, 1, "none"),
        (6, 2, "none"),
        (8, 0, "percent"),
        (8, 4, "percent"),
        (768, 10, "kelvin"),
        (768, 6, "hue_sat"),
    ],
)
def test_every_known_command_names_its_widget(cluster_id, command_id, control):
    assert command_control(cluster_id, command_id) == control


def test_a_command_outside_the_table_is_unknown():
    assert command_control(768, 7) == "unknown"


def test_every_table_command_carries_a_control():
    """An entry without `control` would appear in the UI as a bare
    number field, without anyone having decided that (design
    2026-09-07, section 5.5). This test makes the oversight visible,
    instead of letting it slide."""
    for cluster_id, command_id in known_command_pairs():
        assert command_control(cluster_id, command_id) != "unknown"


def _control_kinds_known_to_the_ui() -> set[str]:
    """Reads `KNOWN_CONTROL_KINDS` from the shipped `web/app.js`.

    Deliberately read instead of duplicated here: a second, hand-maintained
    list could itself drift away from the UI - and then this test would
    only be checking itself. This is exactly the kind of duplicate that
    `known_command_pairs` and `_PAYLOAD_BUILDERS` elsewhere have already
    been guarded against once before (see `commands/translate.py`).

    If the search does not find the field, that is an error and not an
    empty result: an empty set would let every `control` value fail and
    look like a finding, when in truth only the access is broken.
    """
    source = (Path(__file__).parents[2] / "src/loxmatter/web/app.js").read_text(encoding="utf-8")
    match = re.search(r"const KNOWN_CONTROL_KINDS = \[(.*?)\];", source, re.DOTALL)
    if match is None:
        raise AssertionError(
            "KNOWN_CONTROL_KINDS not found in web/app.js - was the field "
            "renamed? Without it, this test cannot check anything."
        )
    return set(re.findall(r'"([^"]+)"', match.group(1)))


_CONTROL_KINDS_KNOWN_TO_THE_UI = _control_kinds_known_to_the_ui()


def test_every_control_value_is_known_to_the_shipped_ui():
    """Finding I-2 (final review 2026-09-08): `command_control` returns
    a free-form `str`, and the UI only compares against the exact
    literals it has actually built a control for. If someone enters a
    `control` value in `clusters.yaml` that `web/app.js` does not (yet)
    know - a typo or a newly conceived control that nobody has built
    yet -, the modal renders nothing for this command: no slider, no
    number field, no hint, even though the "Control" button already
    appears (`hasAdjustableControls`). The UI itself has, since
    this fix, caught this via its fallback to the plain number field
    (`unhandledControls` in app.js) - but this test is meant to make the
    error visible here, in Python, before anyone even gets to
    the browser, and to say WHICH value is unknown."""
    for cluster_id, command_id in known_command_pairs():
        control = command_control(cluster_id, command_id)
        assert control in _CONTROL_KINDS_KNOWN_TO_THE_UI, (
            f"{cluster_id}/{command_id}: control={control!r} is not known to the UI "
            "(see KNOWN_CONTROL_KINDS in web/app.js)"
        )


def test_the_colour_temperature_limits_remain_exportable():
    """Not preselected does not mean locked: in the expert block,
    it must still be possible to select them by hand."""
    ref = SignalRef(1, 768, 16395, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 250)
    assert profile.unit == "mired"
    assert is_exportable(profile.exportability)
    assert not profile.slug.startswith("c768_a")
