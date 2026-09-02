"""Uebersetzt einen Wunschzustand in ein Matter-Kommando.

Dieses Modul hat spaeter zwei Aufrufer: den HTTP-Endpoint fuer die virtuellen
Ausgaenge (Task 6) und die WebUI (Phase 5). Laege die Logik in einem von
beiden, gaebe es die Umrechnung zweimal - mit garantiert auseinanderdriftendem
Verhalten (Spec 4.2).

Was nicht in der Tabelle steht, wirft. Ein Kommando mit erfundener Nutzlast an
ein echtes Geraet zu schicken ist schlechter als ein klarer Fehler. Das gilt
nicht nur fuer einen voellig unbekannten Cluster, sondern auch fuer ein
bekanntes Cluster mit einer unbekannten Kommando-ID darin - siehe
`test_known_cluster_with_unknown_command_raises` in
`tests/commands/test_translate.py`: Cluster 768 (ColorControl) ist bekannt,
Kommando 6 (Hue/Saturation) wird hier bewusst nicht bedient, weil die
Loxone-seitige RGB-Zahl nicht verlaesslich dokumentiert ist (siehe
`color.py`). Faelschlich ein Kommando fuer Kommando 6 zu bauen, nur weil der
Cluster bekannt ist, waere genau der Fehler, den diese Funktion vermeiden
soll.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loxmatter.commands.color import kelvin_to_mireds
from loxmatter.model.store import StoredCommand

LEVEL_MAX = 254

_CLUSTER_ONOFF = 6
_CLUSTER_LEVEL = 8
_CLUSTER_COLOR = 768
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
        return float(value)
    except ValueError as exc:
        raise UnsupportedValueError(f"Wert {value!r} ist keine Zahl") from exc


def _level(value: str) -> int:
    percent = _as_number(value)
    return max(0, min(LEVEL_MAX, round(percent * LEVEL_MAX / 100)))


def to_matter_call(command: StoredCommand, value: str) -> MatterCall:
    """Baut den Matter-Aufruf zu einem exportierten Kommando-Schluessel."""

    def build(payload: dict[str, object]) -> MatterCall:
        return MatterCall(
            node_id=command.node_id,
            endpoint=command.endpoint,
            cluster_id=command.cluster_id,
            command_id=command.command_id,
            payload=payload,
        )

    if command.cluster_id == _CLUSTER_ONOFF:
        return build({})
    if command.cluster_id == _CLUSTER_LEVEL:
        return build({"level": _level(value), "transitionTime": 0})
    if command.cluster_id == _CLUSTER_COLOR and command.command_id == _COMMAND_COLOR_TEMPERATURE:
        return build({"colorTemperatureMireds": kelvin_to_mireds(_as_number(value))})

    raise UnsupportedValueError(
        f"Cluster {command.cluster_id} Kommando {command.command_id} wird nicht unterstuetzt"
    )
