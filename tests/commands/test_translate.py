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

import pytest

from loxmatter import i18n
from loxmatter.commands.translate import (
    _PAYLOAD_BUILDERS,
    MatterCall,
    UnsupportedValueError,
    to_matter_call,
)
from loxmatter.model.store import StoredCommand
from loxmatter.profiles.table import known_command_pairs


def cmd(cluster: int, command: int, takes_value: bool = False) -> StoredCommand:
    return StoredCommand(
        key="d1_1_test",
        node_id=3,
        endpoint=1,
        cluster_id=cluster,
        command_id=command,
        takes_value=takes_value,
        slug="test",
        device_id=1,
    )


def test_onoff_needs_no_payload():
    call = to_matter_call(cmd(6, 1), "1")
    assert call == MatterCall(node_id=3, endpoint=1, cluster_id=6, command_id=1, payload={})


def test_level_is_scaled_from_percent_to_254():
    call = to_matter_call(cmd(8, 4, takes_value=True), "50")
    assert call.payload["level"] == 127


def test_level_hundred_percent_is_full():
    assert to_matter_call(cmd(8, 4, takes_value=True), "100").payload["level"] == 254


def test_level_is_clamped_not_wrapped():
    """Loxone can send 100.4 due to rounding - that must not become 255."""
    assert to_matter_call(cmd(8, 4, takes_value=True), "100.4").payload["level"] == 254
    assert to_matter_call(cmd(8, 4, takes_value=True), "-3").payload["level"] == 0


def test_non_numeric_value_raises_a_clear_error():
    with pytest.raises(UnsupportedValueError, match="is not a number"):
        to_matter_call(cmd(8, 4, takes_value=True), "hell")


def test_non_numeric_value_raises_in_german():
    """German counterpart to `test_non_numeric_value_raises_a_clear_error`
    above."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_call(cmd(8, 4, takes_value=True), "hell")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
def test_non_finite_value_raises_a_clear_error(value: str):
    """`float()` accepts "nan"/"inf" without complaint - that must not slip
    through to `round()`, where it explodes as an English `ValueError`
    instead of as an `UnsupportedValueError` with a clear message."""
    with pytest.raises(UnsupportedValueError, match="is not a number"):
        to_matter_call(cmd(8, 4, takes_value=True), value)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
def test_non_finite_value_raises_in_german(value: str):
    """German counterpart to `test_non_finite_value_raises_a_clear_error`
    above."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_call(cmd(8, 4, takes_value=True), value)


def test_color_temperature_converts_kelvin_to_mireds():
    call = to_matter_call(cmd(768, 10, takes_value=True), "2700")
    assert call.payload["colorTemperatureMireds"] == 370


def test_unknown_cluster_command_raises_rather_than_guessing():
    """A clear error is better than a command with a made-up payload."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_call(cmd(64999, 3, takes_value=True), "1")


def test_unknown_cluster_command_raises_rather_than_guessing_in_german():
    """German counterpart to
    `test_unknown_cluster_command_raises_rather_than_guessing` above."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(64999, 3, takes_value=True), "1")


def test_known_cluster_with_unknown_command_raises():
    """Cluster 768 (ColorControl) ist bekannt, Kommando 7 (MoveToColor, xy) ist
    es hier (noch) nicht - die Bedienflaeche setzt Farbton und Saettigung ueber
    Kommando 6, weiteres waere unbelegte Flaeche (siehe Moduldocstring von
    translate.py). Der Fehler darf nicht nur beim voellig unbekannten Cluster
    greifen, sondern auch bei einem bekannten Cluster mit unbekanntem
    Kommando."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_call(cmd(768, 7, takes_value=True), "255,0,0")


def test_known_cluster_with_unknown_command_raises_in_german():
    """German counterpart to `test_known_cluster_with_unknown_command_raises`
    above."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(768, 7, takes_value=True), "255,0,0")


def test_onoff_cluster_with_unknown_command_raises():
    """Cluster 6 (OnOff) is known, but only commands 0/1/2 are handled. The
    dispatch must not stop at the cluster level - otherwise an unknown
    OnOff command would get a made-up empty payload instead of an
    error."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_call(cmd(6, 99, takes_value=True), "1")


def test_onoff_cluster_with_unknown_command_raises_in_german():
    """German counterpart to `test_onoff_cluster_with_unknown_command_raises`
    above."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(6, 99, takes_value=True), "1")


def test_payload_builders_match_clusters_yaml_commands():
    """Review-Fix C2, 2026-09-02: `_PAYLOAD_BUILDERS` and `clusters.yaml`
    are two independently maintained allow-lists for the same thing - a
    command served by one must be known to the other, or the two drift
    apart (as happened here: (768, 10) was in `_PAYLOAD_BUILDERS` but
    missing from `clusters.yaml`, so the raw export built a digital
    `c768_cmd10`, whose builder actually expected a value - see the
    cluster-768 entry in `clusters.yaml`). This test is the point: without
    it, exactly this drift returns unnoticed."""
    assert set(_PAYLOAD_BUILDERS) == known_command_pairs()


def test_level_cluster_with_unknown_command_raises():
    """Cluster 8 (LevelControl) is known, but only commands 0/4 are handled
    here. Move/Step/Stop (among others, command IDs 1, 2, 3, 5, 6, 7) are
    real LevelControl commands that can, for instance, turn up in a raw
    export (`raw`) without an entry in `clusters.yaml` - mistakenly giving
    them a MoveToLevelWithOnOff payload (level/transitionTime) would be
    exactly the error this module is meant to prevent."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_call(cmd(8, 1, takes_value=True), "50")


def test_level_cluster_with_unknown_command_raises_in_german():
    """German counterpart to `test_level_cluster_with_unknown_command_raises`
    above."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(8, 1, takes_value=True), "50")


def test_a_packed_loxone_colour_becomes_hue_and_saturation():
    """Reines Rot: Farbton 0, volle Saettigung (254). Der Weg ist
    Loxone-Zahl -> RGB -> Hue/Sat, damit WebUI und Loxone denselben
    Uebersetzer benutzen (Entwurf 2026-09-07, Abschnitt 6.5)."""
    command = cmd(768, 6, takes_value=True)
    call = to_matter_call(command, "100")
    assert call.cluster_id == 768
    assert call.command_id == 6
    assert call.payload["hue"] == 0
    assert call.payload["saturation"] == 254
    assert call.payload["transitionTime"] == 0


def test_white_has_no_saturation():
    command = cmd(768, 6, takes_value=True)
    call = to_matter_call(command, "100100100")
    assert call.payload["saturation"] == 0


def test_an_impossible_colour_number_is_rejected():
    """Ein Kanal ueber 100 % kommt als 400 zurueck, nicht als erfundene
    Farbe am Geraet."""
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_call(command, "999999999")


def test_colour_rejects_text():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_call(command, "rot")


def test_channel_over_100_percent_names_channel_and_value():
    """Review-Fix 2026-09-07: die Meldung darf beim Uebersetzen keine
    Genauigkeit verlieren - eine allgemeine "ungueltiger Farbwert" waere
    hier ausdruecklich NICHT ausreichend. 100100100 + 1 im gruenen Kanal
    (Bit 1000) macht Gruen zu 101 %, waehrend Rot und Blau bei 100 % bleiben
    - siehe `commands/color.py::loxone_rgb_to_rgb`."""
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="green") as excinfo:
        to_matter_call(command, "100101100")
    assert "101" in str(excinfo.value)


def test_channel_over_100_percent_names_channel_and_value_in_german():
    """Deutsches Gegenstueck zu
    `test_channel_over_100_percent_names_channel_and_value` oben - vor
    diesem Review-Fix war die Meldung immer Deutsch, unabhaengig von der
    Spracheinstellung (`_payload_hue_saturation` reichte `str(exc)` aus
    `color.py` unveraendert durch)."""
    i18n.set_language("de")
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="gruen") as excinfo:
        to_matter_call(command, "100101100")
    assert "101" in str(excinfo.value)


def test_negative_colour_number_raises_a_clear_error():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="not be negative"):
        to_matter_call(command, "-1")


def test_negative_colour_number_raises_in_german():
    """Deutsches Gegenstueck zu
    `test_negative_colour_number_raises_a_clear_error` oben."""
    i18n.set_language("de")
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="nicht negativ"):
        to_matter_call(command, "-1")


def test_fractional_colour_number_raises_a_clear_error():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="must be an integer"):
        to_matter_call(command, "20040060.5")


def test_fractional_colour_number_raises_in_german():
    """Deutsches Gegenstueck zu
    `test_fractional_colour_number_raises_a_clear_error` oben."""
    i18n.set_language("de")
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="ganzzahlig"):
        to_matter_call(command, "20040060.5")
