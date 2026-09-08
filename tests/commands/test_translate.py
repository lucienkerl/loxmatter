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
    """Cluster 768 (ColorControl) is known, command 6 (Hue/Saturation) is not
    (yet) handled here - see color.py: the Loxone-side RGB number is not
    reliably populated. The error must not only apply to a completely
    unknown cluster, but also to a known cluster with an unknown
    command."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_call(cmd(768, 6, takes_value=True), "255,0,0")


def test_known_cluster_with_unknown_command_raises_in_german():
    """German counterpart to `test_known_cluster_with_unknown_command_raises`
    above."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(768, 6, takes_value=True), "255,0,0")


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
