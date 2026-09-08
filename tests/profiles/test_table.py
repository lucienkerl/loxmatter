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
        assert decimals <= MAX_LOXONE_DECIMALS, f"{unit!r} ergibt {rendered!r}"


def test_a_cluster_with_a_rank_reports_it():
    """Der Rang entscheidet, was auf der Kachel als Leitwert erscheint -
    er muss deshalb aus der Tabelle kommen und nicht aus einer Annahme."""
    assert table.rank_for(6) == 10  # OnOff
    assert table.rank_for(59) == 10  # Switch
    assert table.rank_for(47) == 90  # PowerSource


def test_a_cluster_without_a_rank_gets_the_default():
    """Cluster 3 (Identify) steht nicht in der Tabelle. Er darf weder vorn
    landen noch hinter der Batterie: die Vorgabe ist die Mitte, damit ein
    neuer Geraetetyp nie versehentlich mit seinem Batteriestand fuehrt und
    sein Hauptmerkmal trotzdem vor Verwaltungsangaben steht (Entwurf 4)."""
    assert table.rank_for(3) == table.DEFAULT_RANK
    assert table.DEFAULT_RANK == 50


def test_the_utility_clusters_rank_behind_everything_functional():
    """Die eine Regel, wegen der dieser Entwurf ueberhaupt entstand."""
    functional = [table.rank_for(c) for c in (6, 8, 59, 144, 145, 768, 1026, 1029)]
    assert max(functional) < table.rank_for(47)
    assert table.rank_for(47) < table.rank_for(40)


def test_an_element_can_carry_its_own_rank():
    """Der Taster war der konkrete Fall, der diese Ebene noetig gemacht hat:
    innerhalb von Cluster 59 muss der Tastendruck vor die statische Angabe
    `NumberOfPositions`, sonst fuehrt die Kachel mit einer Zahl, die sich nie
    aendert."""
    press = SignalRef(1, 59, 1, SignalKind.EVENT)
    positions = SignalRef(1, 59, 0, SignalKind.ATTRIBUTE)

    assert table.element_rank_for(press) < table.element_rank_for(positions)


def test_an_element_without_a_rank_gets_the_default():
    """Dieselbe Vorgabe wie auf Clusterebene, und aus demselben Grund: die
    Mitte, damit ein nicht eingetragenes Element weder nach vorn noch ganz
    nach hinten faellt."""
    longpress = SignalRef(1, 59, 2, SignalKind.EVENT)
    assert table.element_rank_for(longpress) == table.DEFAULT_RANK


def test_an_element_of_an_unknown_cluster_gets_the_default():
    """Cluster 3 (Identify) steht nicht in der Tabelle - es gibt dort weder
    einen Abschnitt noch ein Element, in dem ein Rang stehen koennte."""
    assert table.element_rank_for(SignalRef(1, 3, 0, SignalKind.ATTRIBUTE)) == table.DEFAULT_RANK


def test_every_rank_in_the_table_is_an_integer():
    """Fund (Abschlusspruefung): die alte Schleife lief nur ueber
    `cluster["rank"]` - die ELEMENTRAENGE unter `attributes:`/`events:`
    (z. B. `events: {1: {slug: press, rank: 10}}`, siehe Cluster 59 in
    `clusters.yaml`) sah sie nie, obwohl der Name "every rank in the
    table" das verspricht. Ein `rank: 10.5` an einem Element (jemand will
    es zwischen zwei andere schieben) wuerde von `int(10.5)` in
    `element_rank_for` still zu 10 - die Reihenfolge weicht von der
    Absicht ab, der alte Test blieb aber gruen, weil er die Elementebene
    gar nicht ansah.

    Die alte Begruendung war ausserdem falsch: `rank_for` und
    `element_rank_for` rufen beide `int(rank)` - ein `rank: "10"` aus
    einem Tippfehler wuerde also NICHT "beim Sortieren gegen eine Zahl
    werfen", sondern klaglos zu 10 werden. Der tatsaechliche Schaden ist
    eine stille Abweichung von der Sortierabsicht, kein Absturz.

    Fund (Nachpruefung vor dem Merge): `isinstance(x, int)` ist fuer
    `True`/`False` ebenfalls wahr, weil `bool` in Python von `int` erbt -
    ein `rank: true` (YAML-Tippfehler fuer eine Zahl) waere also
    unentdeckt durchgerutscht. Bestand schon auf Clusterebene, ist mit der
    Ausweitung auf die Elementebene nur mitgewandert. `not isinstance(x,
    bool)` schliesst genau diesen Fall auf beiden Ebenen aus."""
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
    """Ein Eintrag ohne `control` erschiene in der Oberflaeche als nacktes
    Zahlenfeld, ohne dass jemand das entschieden haette (Entwurf
    2026-09-07, Abschnitt 5.5). Dieser Test macht das Vergessen sichtbar,
    statt es durchgehen zu lassen."""
    for cluster_id, command_id in known_command_pairs():
        assert command_control(cluster_id, command_id) != "unknown"


def _control_kinds_known_to_the_ui() -> set[str]:
    """Liest `KNOWN_CONTROL_KINDS` aus der ausgelieferten `web/app.js`.

    Bewusst gelesen statt hier dupliziert: eine zweite, von Hand gepflegte
    Liste koennte selbst von der Oberflaeche wegdriften - und dann prueft
    dieser Test nur noch sich selbst. Genau diese Sorte Duplikat ist das,
    wogegen `known_command_pairs` und `_PAYLOAD_BUILDERS` an anderer Stelle
    schon einmal abgesichert wurden (siehe `commands/translate.py`).

    Findet die Suche das Feld nicht, ist das ein Fehler und kein leeres
    Ergebnis: eine leere Menge liesse jeden `control`-Wert durchfallen und
    saehe nach einem Befund aus, wo in Wahrheit nur der Zugriff kaputt ist.
    """
    source = (Path(__file__).parents[2] / "src/loxmatter/web/app.js").read_text(encoding="utf-8")
    match = re.search(r"const KNOWN_CONTROL_KINDS = \[(.*?)\];", source, re.DOTALL)
    if match is None:
        raise AssertionError(
            "KNOWN_CONTROL_KINDS nicht in web/app.js gefunden - wurde das Feld "
            "umbenannt? Ohne es kann dieser Test nichts pruefen."
        )
    return set(re.findall(r'"([^"]+)"', match.group(1)))


_CONTROL_KINDS_KNOWN_TO_THE_UI = _control_kinds_known_to_the_ui()


def test_every_control_value_is_known_to_the_shipped_ui():
    """Befund I-2 (Abschluss-Review 2026-09-08): `command_control` liefert
    einen freien `str`, und die Oberflaeche vergleicht nur auf die
    Wortlaute, fuer die sie tatsaechlich ein Bedienelement gebaut hat.
    Traegt jemand in `clusters.yaml` einen `control`-Wert ein, den
    `web/app.js` (noch) nicht kennt - ein Tippfehler oder ein neu
    erdachtes Bedienelement, das noch niemand gebaut hat -, rendert das
    Modal fuer dieses Kommando nichts: kein Regler, kein Zahlenfeld, kein
    Hinweis, obwohl der "Steuern"-Knopf bereits erscheint
    (`hasAdjustableControls`). Die Oberflaeche selbst faengt das seit
    diesem Fix zwar ueber ihren Rueckfall auf das schlichte Zahlenfeld ab
    (`unhandledControls` in app.js) - aber dieser Test soll den Fehler
    schon hier, in Python, sichtbar machen, bevor jemand ueberhaupt bis
    zum Browser kommt, und sagen WELCHER Wert unbekannt ist."""
    for cluster_id, command_id in known_command_pairs():
        control = command_control(cluster_id, command_id)
        assert control in _CONTROL_KINDS_KNOWN_TO_THE_UI, (
            f"{cluster_id}/{command_id}: control={control!r} kennt die Oberflaeche nicht "
            "(siehe KNOWN_CONTROL_KINDS in web/app.js)"
        )


def test_the_colour_temperature_limits_remain_exportable():
    """Nicht vorausgewaehlt heisst nicht gesperrt: im Expertenblock muss
    man sie weiterhin von Hand waehlen koennen."""
    ref = SignalRef(1, 768, 16395, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 250)
    assert profile.unit == "mired"
    assert is_exportable(profile.exportability)
    assert not profile.slug.startswith("c768_a")
