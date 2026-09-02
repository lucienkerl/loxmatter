"""Leitet aus AcceptedCommandList ab, was Loxone einem Geraet sagen darf.

Nicht aus den Attributen: Matter-Attribute sind ganz ueberwiegend nur lesbar,
und ein Ausgangsbefehl je lesbarem Attribut waere zu 95 Prozent wirkungslos.

Erlaubnisliste statt Sperrliste. Bei Attributen wird Unbekanntes grosszuegig
durchgereicht; bei Kommandos waere das falsch herum, weil zu den akzeptierten
Kommandos die Verwaltungscluster gehoeren - RemoveFabric, Kommissionierung,
TestEventTrigger. ADMINISTRATIVE_CLUSTERS bleibt auch im Rohmodus gesperrt.
"""

from __future__ import annotations

from dataclasses import dataclass

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID, parse_attribute_path
from loxmatter.profiles.table import (
    ADMINISTRATIVE_CLUSTERS,
    command_slug,
    command_takes_value,
)


@dataclass(frozen=True, order=True)
class DeviceCommand:
    endpoint: int
    cluster_id: int
    command_id: int
    slug: str
    takes_value: bool


def extract_commands(snapshot: NodeSnapshot, *, raw: bool = False) -> list[DeviceCommand]:
    """Alle Kommandos, die als Loxone-Ausgang erscheinen duerfen."""
    commands: list[DeviceCommand] = []

    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        if attribute_id != ACCEPTED_COMMAND_LIST_ID:
            continue
        if cluster_id in ADMINISTRATIVE_CLUSTERS:
            continue
        if not isinstance(value, (list, tuple)):
            continue

        for command_id in (int(c) for c in value if isinstance(c, (int, float))):
            slug = command_slug(cluster_id, command_id)
            if slug is None:
                if not raw:
                    continue
                slug = f"c{cluster_id}_cmd{command_id}"
            commands.append(
                DeviceCommand(
                    endpoint=endpoint,
                    cluster_id=cluster_id,
                    command_id=command_id,
                    slug=slug,
                    takes_value=command_takes_value(cluster_id, command_id),
                )
            )

    return sorted(commands)
