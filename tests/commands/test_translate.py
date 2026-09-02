import pytest

from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.model.store import StoredCommand


def cmd(cluster: int, command: int, takes_value: bool = False) -> StoredCommand:
    return StoredCommand(
        key="d1_1_test",
        node_id=3,
        endpoint=1,
        cluster_id=cluster,
        command_id=command,
        takes_value=takes_value,
        slug="test",
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
    """Loxone kann durch Rundung 100.4 schicken - das darf nicht zu 255 werden."""
    assert to_matter_call(cmd(8, 4, takes_value=True), "100.4").payload["level"] == 254
    assert to_matter_call(cmd(8, 4, takes_value=True), "-3").payload["level"] == 0


def test_non_numeric_value_raises_in_german():
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_call(cmd(8, 4, takes_value=True), "hell")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
def test_non_finite_value_raises_in_german(value: str):
    """`float()` akzeptiert "nan"/"inf" anstandslos - das darf nicht bis zu
    `round()` durchrutschen, wo es als englischer `ValueError` explodiert,
    statt als `UnsupportedValueError` mit deutscher Meldung."""
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_call(cmd(8, 4, takes_value=True), value)


def test_color_temperature_converts_kelvin_to_mireds():
    call = to_matter_call(cmd(768, 10, takes_value=True), "2700")
    assert call.payload["colorTemperatureMireds"] == 370


def test_unknown_cluster_command_raises_rather_than_guessing():
    """Lieber ein klarer Fehler als ein Kommando mit erfundener Nutzlast."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(64999, 3, takes_value=True), "1")


def test_known_cluster_with_unknown_command_raises():
    """Cluster 768 (ColorControl) ist bekannt, Kommando 6 (Hue/Saturation) ist es
    hier (noch) nicht - siehe color.py: die Loxone-seitige RGB-Zahl ist nicht
    verlaesslich belegt. Der Fehler darf nicht nur beim voellig unbekannten
    Cluster greifen, sondern auch bei einem bekannten Cluster mit unbekanntem
    Kommando."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(768, 6, takes_value=True), "255,0,0")


def test_onoff_cluster_with_unknown_command_raises():
    """Cluster 6 (OnOff) ist bekannt, aber nur Kommando 0/1/2 sind es. Der
    Dispatch darf nicht schon beim Cluster stehen bleiben - sonst bekaeme ein
    unbekanntes OnOff-Kommando eine erfundene leere Nutzlast statt eines
    Fehlers."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(6, 99, takes_value=True), "1")


def test_level_cluster_with_unknown_command_raises():
    """Cluster 8 (LevelControl) ist bekannt, aber nur Kommando 0/4 sind es hier
    bedient. Move/Step/Stop (u. a. Kommando-IDs 1, 2, 3, 5, 6, 7) sind reale
    LevelControl-Kommandos, die z. B. bei Rohexport (`raw`) ohne Eintrag in
    `clusters.yaml` auftauchen koennen - ihnen faelschlich eine
    MoveToLevelWithOnOff-Nutzlast (level/transitionTime) unterzuschieben waere
    genau der Fehler, den dieses Modul verhindern soll."""
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_call(cmd(8, 1, takes_value=True), "50")
