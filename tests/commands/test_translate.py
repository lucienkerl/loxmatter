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

import pytest

from loxmatter import i18n
from loxmatter.commands.color import kelvin_to_mireds
from loxmatter.commands.translate import (
    _PAYLOAD_BUILDERS,
    MatterCall,
    UnsupportedValueError,
    to_matter_calls,
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
    call = to_matter_calls(cmd(6, 1), "1")[0]
    assert call == MatterCall(node_id=3, endpoint=1, cluster_id=6, command_id=1, payload={})


def test_level_is_scaled_from_percent_to_254():
    call = to_matter_calls(cmd(8, 4, takes_value=True), "50")[0]
    assert call.payload["level"] == 127


def test_level_hundred_percent_is_full():
    assert to_matter_calls(cmd(8, 4, takes_value=True), "100")[0].payload["level"] == 254


def test_level_is_clamped_not_wrapped():
    """Loxone kann durch Rundung 100.4 schicken - das darf nicht zu 255 werden."""
    assert to_matter_calls(cmd(8, 4, takes_value=True), "100.4")[0].payload["level"] == 254
    assert to_matter_calls(cmd(8, 4, takes_value=True), "-3")[0].payload["level"] == 0


def test_non_numeric_value_raises_a_clear_error():
    with pytest.raises(UnsupportedValueError, match="is not a number"):
        to_matter_calls(cmd(8, 4, takes_value=True), "hell")


def test_non_numeric_value_raises_in_german():
    """Deutsches Gegenstueck zu `test_non_numeric_value_raises_a_clear_error`
    oben."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_calls(cmd(8, 4, takes_value=True), "hell")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
def test_non_finite_value_raises_a_clear_error(value: str):
    """`float()` akzeptiert "nan"/"inf" anstandslos - das darf nicht bis zu
    `round()` durchrutschen, wo es als englischer `ValueError` explodiert,
    statt als `UnsupportedValueError` mit klarer Meldung."""
    with pytest.raises(UnsupportedValueError, match="is not a number"):
        to_matter_calls(cmd(8, 4, takes_value=True), value)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
def test_non_finite_value_raises_in_german(value: str):
    """Deutsches Gegenstueck zu `test_non_finite_value_raises_a_clear_error`
    oben."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="keine Zahl"):
        to_matter_calls(cmd(8, 4, takes_value=True), value)


def test_color_temperature_converts_kelvin_to_mireds():
    call = to_matter_calls(cmd(768, 10, takes_value=True), "2700")[0]
    assert call.payload["colorTemperatureMireds"] == 370


def test_unknown_cluster_command_raises_rather_than_guessing():
    """Lieber ein klarer Fehler als ein Kommando mit erfundener Nutzlast."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_calls(cmd(64999, 3, takes_value=True), "1")


def test_unknown_cluster_command_raises_rather_than_guessing_in_german():
    """Deutsches Gegenstueck zu
    `test_unknown_cluster_command_raises_rather_than_guessing` oben."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_calls(cmd(64999, 3, takes_value=True), "1")


def test_known_cluster_with_unknown_command_raises():
    """Cluster 768 (ColorControl) ist bekannt, Kommando 7 (MoveToColor, xy) ist
    es hier (noch) nicht - die Bedienflaeche setzt Farbton und Saettigung ueber
    Kommando 6, weiteres waere unbelegte Flaeche (siehe Moduldocstring von
    translate.py). Der Fehler darf nicht nur beim voellig unbekannten Cluster
    greifen, sondern auch bei einem bekannten Cluster mit unbekanntem
    Kommando."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_calls(cmd(768, 7, takes_value=True), "255,0,0")


def test_known_cluster_with_unknown_command_raises_in_german():
    """Deutsches Gegenstueck zu `test_known_cluster_with_unknown_command_raises`
    oben."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_calls(cmd(768, 7, takes_value=True), "255,0,0")


def test_onoff_cluster_with_unknown_command_raises():
    """Cluster 6 (OnOff) ist bekannt, aber nur Kommando 0/1/2 sind es. Der
    Dispatch darf nicht schon beim Cluster stehen bleiben - sonst bekaeme ein
    unbekanntes OnOff-Kommando eine erfundene leere Nutzlast statt eines
    Fehlers."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_calls(cmd(6, 99, takes_value=True), "1")


def test_onoff_cluster_with_unknown_command_raises_in_german():
    """Deutsches Gegenstueck zu `test_onoff_cluster_with_unknown_command_raises`
    oben."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_calls(cmd(6, 99, takes_value=True), "1")


def test_payload_builders_match_clusters_yaml_commands():
    """Review-Fix C2, 2026-09-02: `_PAYLOAD_BUILDERS` und `clusters.yaml`
    sind zwei unabhaengig gepflegte Erlaubnislisten fuer dasselbe - ein
    Kommando, das die eine bedient, muss die andere kennen, sonst driften
    sie auseinander (wie hier: (768, 10) stand in `_PAYLOAD_BUILDERS`, fehlte
    aber in `clusters.yaml`, wodurch der Rohexport ein digitales `c768_cmd10`
    baute, dessen Builder in Wirklichkeit einen Wert erwartete - siehe
    Cluster-768-Eintrag in `clusters.yaml`). Dieser Test ist der Punkt: ohne
    ihn kehrt genau diese Drift unbemerkt zurueck."""
    assert set(_PAYLOAD_BUILDERS) == known_command_pairs()


def test_level_cluster_with_unknown_command_raises():
    """Cluster 8 (LevelControl) ist bekannt, aber nur Kommando 0/4 sind es hier
    bedient. Move/Step/Stop (u. a. Kommando-IDs 1, 2, 3, 5, 6, 7) sind reale
    LevelControl-Kommandos, die z. B. bei Rohexport (`raw`) ohne Eintrag in
    `clusters.yaml` auftauchen koennen - ihnen faelschlich eine
    MoveToLevelWithOnOff-Nutzlast (level/transitionTime) unterzuschieben waere
    genau der Fehler, den dieses Modul verhindern soll."""
    with pytest.raises(UnsupportedValueError, match="is not supported"):
        to_matter_calls(cmd(8, 1, takes_value=True), "50")


def test_level_cluster_with_unknown_command_raises_in_german():
    """Deutsches Gegenstueck zu `test_level_cluster_with_unknown_command_raises`
    oben."""
    i18n.set_language("de")
    with pytest.raises(UnsupportedValueError, match="nicht unterstuetzt"):
        to_matter_calls(cmd(8, 1, takes_value=True), "50")


def test_a_packed_loxone_colour_becomes_hue_and_saturation():
    """Reines Rot: Farbton 0, volle Saettigung (254). Der Weg ist
    Loxone-Zahl -> RGB -> Hue/Sat, damit WebUI und Loxone denselben
    Uebersetzer benutzen (Entwurf 2026-09-07, Abschnitt 6.5)."""
    command = cmd(768, 6, takes_value=True)
    call = to_matter_calls(command, "100")[0]
    assert call.cluster_id == 768
    assert call.command_id == 6
    assert call.payload["hue"] == 0
    assert call.payload["saturation"] == 254
    assert call.payload["transitionTime"] == 0


def test_white_has_no_saturation():
    command = cmd(768, 6, takes_value=True)
    call = to_matter_calls(command, "100100100")[0]
    assert call.payload["saturation"] == 0


def test_an_impossible_colour_number_is_rejected():
    """Ein Kanal ueber 100 % kommt als 400 zurueck, nicht als erfundene
    Farbe am Geraet."""
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_calls(command, "999999999")


def test_colour_rejects_text():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError):
        to_matter_calls(command, "rot")


def test_channel_over_100_percent_names_channel_and_value():
    """Review-Fix 2026-09-07: die Meldung darf beim Uebersetzen keine
    Genauigkeit verlieren - eine allgemeine "ungueltiger Farbwert" waere
    hier ausdruecklich NICHT ausreichend. 100100100 + 1 im gruenen Kanal
    (Bit 1000) macht Gruen zu 101 %, waehrend Rot und Blau bei 100 % bleiben
    - siehe `commands/color.py::loxone_rgb_to_rgb`."""
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="green") as excinfo:
        to_matter_calls(command, "100101100")
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
        to_matter_calls(command, "100101100")
    assert "101" in str(excinfo.value)


def test_negative_colour_number_raises_a_clear_error():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="not be negative"):
        to_matter_calls(command, "-1")


def test_negative_colour_number_raises_in_german():
    """Deutsches Gegenstueck zu
    `test_negative_colour_number_raises_a_clear_error` oben."""
    i18n.set_language("de")
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="nicht negativ"):
        to_matter_calls(command, "-1")


def test_fractional_colour_number_raises_a_clear_error():
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="must be an integer"):
        to_matter_calls(command, "20040060.5")


def test_fractional_colour_number_raises_in_german():
    """Deutsches Gegenstueck zu
    `test_fractional_colour_number_raises_a_clear_error` oben."""
    i18n.set_language("de")
    command = cmd(768, 6, takes_value=True)
    with pytest.raises(UnsupportedValueError, match="ganzzahlig"):
        to_matter_calls(command, "20040060.5")


def test_a_lumitech_value_becomes_a_colour_temperature_command():
    """Betriebsbefund vom 8. September 2026: der Lichtsteuerungs-Baustein
    schickt Farbe UND Weiss ueber denselben Analogausgang. Ein Weisswert
    muss deshalb aus DEMSELBEN Loxone-Schluessel ein anderes
    Matter-Kommando ausloesen - MoveToColorTemperature (10) statt
    MoveToHueAndSaturation (6).

    201002700 = Kennung 20 | Helligkeit 100 % | 2700 K. Gemessener Wert aus
    einer echten Anlage."""
    call = to_matter_calls(cmd(768, 6, takes_value=True), "201002700")[0]
    assert call.cluster_id == 768
    assert call.command_id == 10
    assert call.payload["colorTemperatureMireds"] == kelvin_to_mireds(2700)


def test_an_rgb_value_still_becomes_a_hue_saturation_command():
    """Die Gegenprobe: derselbe Schluessel, eine RGB-Zahl, unveraendertes
    Verhalten. Ohne diesen Test koennte die Weiche den Farbweg kapern, ohne
    dass es auffaellt."""
    call = to_matter_calls(cmd(768, 6, takes_value=True), "100")[0]
    assert call.command_id == 6
    assert call.payload["hue"] == 0
    assert call.payload["saturation"] == 254


@pytest.mark.parametrize(
    ("packed", "kelvin"),
    [("200283057", 3057), ("201004324", 4324), ("201006500", 6500)],
)
def test_measured_lumitech_values_reach_their_kelvin(packed, kelvin):
    call = to_matter_calls(cmd(768, 6, takes_value=True), packed)[0]
    assert call.command_id == 10
    assert call.payload["colorTemperatureMireds"] == kelvin_to_mireds(kelvin)


def test_a_malformed_lumitech_value_is_rejected_not_guessed():
    """20|101|2700 - eine Helligkeit ueber 100 %. Das Format ist verletzt,
    und eine Farbtemperatur daraus zu rechnen hiesse raten."""
    with pytest.raises(UnsupportedValueError):
        to_matter_calls(cmd(768, 6, takes_value=True), "201012700")


def test_a_colour_value_also_carries_its_brightness():
    """Betriebsbefund vom 8. September 2026: der Helligkeitsregler der
    Loxone-App bewirkte nichts. Loxone codiert die Helligkeit im Betrag der
    RGB-Zahl, und die Bruecke schickte nur Hue und Saturation.

    85019094 = (94,19,85) - Farbton 307 Grad bei 94 % Helligkeit. Erwartet
    werden ZWEI Kommandos: die Farbe und der Pegel."""
    calls = to_matter_calls(cmd(768, 6, takes_value=True), "85019094")
    assert [c.command_id for c in calls] == [6, 4]
    assert calls[0].cluster_id == 768
    assert calls[1].cluster_id == 8
    assert calls[1].payload["level"] == pytest.approx(round(94 * 254 / 100), abs=2)


def test_the_same_colour_dimmed_differs_only_in_the_level_command():
    """Die beiden gemessenen Werte derselben Farbe: die Farbnutzlast muss
    gleich bleiben, nur der Pegel sich unterscheiden. Faellt dieser Test,
    faerbt die Helligkeit auf die Farbe ab."""
    dunkel = to_matter_calls(cmd(768, 6, takes_value=True), "18004020")
    hell = to_matter_calls(cmd(768, 6, takes_value=True), "85019094")
    assert dunkel[0].payload["hue"] == pytest.approx(hell[0].payload["hue"], abs=2)
    assert dunkel[1].payload["level"] < hell[1].payload["level"]


def test_a_lumitech_value_also_carries_its_brightness():
    """200283057 = Kennung 20 | 28 % | 3057 K - zwei Kommandos, nicht eins."""
    calls = to_matter_calls(cmd(768, 6, takes_value=True), "200283057")
    assert [c.command_id for c in calls] == [10, 4]
    assert calls[0].payload["colorTemperatureMireds"] == kelvin_to_mireds(3057)
    assert calls[1].payload["level"] == pytest.approx(round(28 * 254 / 100), abs=2)


def test_the_colour_command_comes_before_the_level_command():
    """Die Reihenfolge ist nicht beliebig: `MoveToLevelWithOnOff` schaltet
    eine ausgeschaltete Leuchte EIN. Kaeme der Pegel zuerst, ginge sie in
    der alten Farbe an und wechselte sichtbar nach - erst faerben, dann
    einschalten."""
    for wert in ("85019094", "200283057"):
        calls = to_matter_calls(cmd(768, 6, takes_value=True), wert)
        assert calls[0].cluster_id == 768
        assert calls[1].cluster_id == 8


def test_brightness_zero_switches_the_lamp_off():
    """Loxone-Wert 0 heisst aus. Vorher wurde daraus Saettigung 0, also
    WEISS statt aus - der Fehler, den Entwurf Abschnitt 10 Punkt 5
    beschrieb. `MoveToLevelWithOnOff` mit Pegel 0 schaltet wirklich ab."""
    calls = to_matter_calls(cmd(768, 6, takes_value=True), "0")
    assert calls[-1].cluster_id == 8
    assert calls[-1].command_id == 4
    assert calls[-1].payload["level"] == 0


def test_commands_without_a_brightness_still_yield_exactly_one_call():
    """Nur der Farb-Ausgang traegt zwei Bedeutungen. Alles andere bleibt
    ein Kommando - ein zweites waere hier erfunden."""
    for cluster, command, wert in [(6, 1, "1"), (8, 4, "50"), (768, 10, "2700")]:
        assert len(to_matter_calls(cmd(cluster, command, takes_value=True), wert)) == 1


def test_colour_commands_apply_even_while_the_lamp_is_off():
    """An der Leuchte gemessen (8. September 2026): ein Farbbefehl an eine
    AUSGESCHALTETE Leuchte verpufft. Der Wert 60100060 (gruen bei 60 %)
    brachte sie weiss und auf 100 % hoch - die Farbe kam nie an.

    Das ist Matter-Spezifikation, kein Geraetefehler: ColorControl-Befehle
    wirken bei ausgeschaltetem Geraet nur, wenn das Bit `ExecuteIfOff`
    gesetzt ist (`OptionsBitmap.kExecuteIfOff` = 1). Ohne dieses Bit
    muesste der Pegel zuerst kommen - dann ginge die Leuchte sichtbar in
    der ALTEN Farbe an und wechselte danach. Mit dem Bit bleibt die
    Reihenfolge Farbe-dann-Pegel richtig und der Wechsel unsichtbar."""
    for wert, erwartet in [("85019094", 6), ("200283057", 10)]:
        farbe = to_matter_calls(cmd(768, 6, takes_value=True), wert)[0]
        assert farbe.command_id == erwartet
        assert farbe.payload["optionsMask"] == 1
        assert farbe.payload["optionsOverride"] == 1


def test_the_plain_colour_temperature_output_also_applies_while_off():
    """Derselbe Grund fuer den getrennten `colortemp`-Ausgang: auch er
    setzt eine Farbe, und auch er soll nicht verpuffen, nur weil die
    Leuchte gerade aus ist."""
    call = to_matter_calls(cmd(768, 10, takes_value=True), "2700")[0]
    assert call.payload["optionsMask"] == 1
    assert call.payload["optionsOverride"] == 1


def test_switching_off_sends_no_colour_command():
    """Betriebsbefund vom 8. September 2026: beim Ausschalten blitzte die
    Leuchte kurz sehr hell WEISS auf.

    Loxone schickt zum Ausschalten den Wert 0. In der RGB-Codierung ist das
    (0,0,0) - Farbton 0, **Saettigung 0**, also WEISS - und Helligkeit 0.
    Daraus wurden zwei Kommandos: erst "faerbe weiss", dann "schalte aus".
    Die Leuchte gehorchte dem ersten, waehrend sie noch leuchtete; weiss
    nutzt alle LEDs, gesaettigtes Rot nur die roten, also war der Blitz
    sogar heller als das Bild davor.

    Bei Helligkeit 0 gibt es keine Farbe zu setzen. Es bleibt genau ein
    Kommando: ausschalten."""
    calls = to_matter_calls(cmd(768, 6, takes_value=True), "0")
    assert len(calls) == 1
    assert calls[0].cluster_id == 8
    assert calls[0].command_id == 4
    assert calls[0].payload["level"] == 0


def test_lumitech_at_zero_brightness_also_sends_no_colour_command():
    """Derselbe Fall auf dem Weiss-Weg: 200003691 = Kennung 20 | 0 % |
    3691 K. Auch hier taucht im Log der Anlage die Helligkeit 0 auf."""
    calls = to_matter_calls(cmd(768, 6, takes_value=True), "200003691")
    assert len(calls) == 1
    assert calls[0].cluster_id == 8
    assert calls[0].payload["level"] == 0


def test_a_barely_dimmed_value_still_carries_its_colour():
    """Die Gegenprobe: 1 ist (1,0,0) - dunkelrot bei 1 %, NICHT aus. Wer
    die Bedingung auf 'fast null' aufweicht, verliert hier die Farbe."""
    calls = to_matter_calls(cmd(768, 6, takes_value=True), "1")
    assert len(calls) == 2
    assert calls[0].cluster_id == 768
    assert calls[1].payload["level"] > 0
