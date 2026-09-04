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

"""Uebersetzt einen Wunschzustand in ein Matter-Kommando.

Dieses Modul hat spaeter zwei Aufrufer: den HTTP-Endpoint fuer die virtuellen
Ausgaenge (Task 6) und die WebUI (Phase 5). Laege die Logik in einem von
beiden, gaebe es die Umrechnung zweimal - mit garantiert auseinanderdriftendem
Verhalten (Spec 4.2).

Was nicht in `_PAYLOAD_BUILDERS` steht, wirft. Ein Kommando mit erfundener
Nutzlast an ein echtes Geraet zu schicken ist schlechter als ein klarer
Fehler. Das gilt nicht nur fuer einen voellig unbekannten Cluster, sondern
auch fuer ein bekanntes Cluster mit einer unbekannten Kommando-ID darin: der
Dispatch schluesselt auf das Paar (Cluster-ID, Kommando-ID), nie auf die
Cluster-ID allein - siehe `test_known_cluster_with_unknown_command_raises`
(Cluster 768/ColorControl, Kommando 6),
`test_onoff_cluster_with_unknown_command_raises` (Cluster 6) und
`test_level_cluster_with_unknown_command_raises` (Cluster 8) in
`tests/commands/test_translate.py`. Cluster 768 Kommando 6 (Hue/Saturation)
wird bewusst nicht bedient, weil die Loxone-seitige RGB-Zahl nicht
verlaesslich dokumentiert ist (siehe `color.py`); Cluster 6 und 8 kennen
jenseits von Off/On/Toggle bzw. MoveToLevel(WithOnOff) hier schlicht keine
weiteren Kommandos - das ist besonders beim Rohexport (`raw`) relevant, der
auch Kommandos ohne Eintrag in `clusters.yaml` durchlaesst, etwa
LevelControl Move/Step/Stop. Faelschlich ein Kommando zu bauen, nur weil der
Cluster bekannt ist, waere genau der Fehler, den diese Funktion vermeiden
soll.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from loxmatter import i18n
from loxmatter.commands.color import kelvin_to_mireds
from loxmatter.model.store import StoredCommand

LEVEL_MAX = 254

_CLUSTER_ONOFF = 6
_CLUSTER_LEVEL = 8
_CLUSTER_COLOR = 768

_COMMAND_OFF = 0
_COMMAND_ON = 1
_COMMAND_TOGGLE = 2
_COMMAND_MOVE_TO_LEVEL = 0
_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF = 4
_COMMAND_COLOR_TEMPERATURE = 10


class UnsupportedValueError(ValueError):
    """Der Wert passt nicht zu diesem Kommando."""


@dataclass(frozen=True)
class MatterCall:
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    payload: dict[str, object] = field(default_factory=dict)


def _as_number(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise UnsupportedValueError(i18n.t("api.errors.value_not_a_number", value=value)) from exc
    if not math.isfinite(result):
        # float() liest "nan"/"inf"/"-inf" anstandslos ein. Liesse man das
        # durch, wuerde spaeter `round()` mit einem englischen `ValueError`
        # abstuerzen (nan) oder `kelvin_to_mireds` seine <=0-Pruefung
        # unbemerkt umgehen (nan ist nie <= 0) - beides ist hier keine Zahl,
        # die ein Kommando tragen kann.
        raise UnsupportedValueError(i18n.t("api.errors.value_not_a_number", value=value))
    return result


def _level(value: str) -> int:
    percent = _as_number(value)
    return max(0, min(LEVEL_MAX, round(percent * LEVEL_MAX / 100)))


def _payload_none(_value: str) -> dict[str, object]:
    return {}


def _payload_level(value: str) -> dict[str, object]:
    return {"level": _level(value), "transitionTime": 0}


def _payload_color_temperature(value: str) -> dict[str, object]:
    return {"colorTemperatureMireds": kelvin_to_mireds(_as_number(value))}


# Einziger Ort, an dem festgelegt ist, welche (Cluster-ID, Kommando-ID)-Paare
# bedient werden. Der Dispatch in `to_matter_call` liest diese Zuordnung nur
# noch aus - ein weiteres Kommando zu unterstuetzen ist eine Datenaenderung
# hier, keine neue Verzweigung dort, und die Menge der bedienten Paare ist auf
# einen Blick vollstaendig.
_PAYLOAD_BUILDERS: dict[tuple[int, int], Callable[[str], dict[str, object]]] = {
    (_CLUSTER_ONOFF, _COMMAND_OFF): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_ON): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_TOGGLE): _payload_none,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL): _payload_level,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF): _payload_level,
    (_CLUSTER_COLOR, _COMMAND_COLOR_TEMPERATURE): _payload_color_temperature,
}


def to_matter_call(command: StoredCommand, value: str) -> MatterCall:
    """Baut den Matter-Aufruf zu einem exportierten Kommando-Schluessel."""

    build_payload = _PAYLOAD_BUILDERS.get((command.cluster_id, command.command_id))
    if build_payload is None:
        raise UnsupportedValueError(
            i18n.t(
                "api.errors.command_unsupported",
                cluster_id=command.cluster_id,
                command_id=command.command_id,
            )
        )

    return MatterCall(
        node_id=command.node_id,
        endpoint=command.endpoint,
        cluster_id=command.cluster_id,
        command_id=command.command_id,
        payload=build_payload(value),
    )
