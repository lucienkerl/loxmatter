"""Benennt Signale und entscheidet, ob sie nach Loxone exportierbar sind.

Grundsatz aus Spec 3.5: die Tabelle reichert an, sie filtert nicht. Ein
unbekannter Cluster bekommt einen generischen Namen und wird trotzdem
exportiert, sofern sein Wert ueberhaupt auf einen Loxone-Eingang passt.

Spec 6.6: Listen, Strukturen und Nullwerte passen nicht. Sie bleiben Signale
und sind in der Oberflaeche sichtbar, werden aber nie zu Loxone-Objekten.

Spec 7.3: `Unit` in der Vorlage ist ein Formatstring fuer die Loxone-Oberflaeche
(`<v.N> Einheit`), keine Einheitenbezeichnung. `unit_format` traegt diese
Abbildung als Datentabelle, nicht als Verzweigung im Exporter.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from loxmatter.matter.models import SignalKind, SignalRef

_TABLE_PATH = Path(__file__).with_name("clusters.yaml")


class Exportability(str, Enum):
    ANALOG = "analog"
    DIGITAL = "digital"
    TEXT = "text"
    NONE = "none"


@dataclass(frozen=True)
class Profile:
    slug: str
    unit: str
    exportability: Exportability


def classify(value: object) -> Exportability:
    """Entscheidet allein am Wert, ob Loxone ihn aufnehmen kann."""
    if isinstance(value, bool):
        return Exportability.DIGITAL
    if isinstance(value, (int, float)):
        return Exportability.ANALOG
    if isinstance(value, str):
        return Exportability.TEXT
    return Exportability.NONE


@functools.cache
def _table() -> dict[int, dict[str, Any]]:
    raw = yaml.safe_load(_TABLE_PATH.read_text(encoding="utf-8"))
    return {int(k): v for k, v in (raw.get("clusters") or {}).items()}


def lookup(ref: SignalRef, value: object) -> Profile:
    """Liefert Name, Einheit und Exportierbarkeit fuer ein Signal."""
    cluster = _table().get(ref.cluster_id, {})
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    entry = (cluster.get(section) or {}).get(ref.element_id)

    if ref.kind is SignalKind.EVENT:
        slug = entry["slug"] if entry else f"c{ref.cluster_id}_e{ref.element_id}"
        return Profile(slug=slug, unit="", exportability=Exportability.DIGITAL)

    if entry:
        return Profile(
            slug=entry["slug"], unit=entry.get("unit", ""), exportability=classify(value)
        )
    return Profile(
        slug=f"c{ref.cluster_id}_a{ref.element_id}",
        unit="",
        exportability=classify(value),
    )


# Nachkommastellen je Einheit fuer den Loxone-Formatstring (Spec 7.3). Leistung
# steht bewusst nicht bei den uebrigen physikalischen Groessen mit 1 Dezimale:
# von mW nach kW sind sechs Groessenordnungen, und mit <v.3> verschwindet ein
# 300-mW-Standby-Verbraucher als 0.000 auf der Oberflaeche.
_UNIT_DECIMALS: dict[str, int] = {
    "kW": 6,
    "kWh": 6,
    "°C": 1,
    "%": 1,
    "V": 1,
    "A": 1,
}

# Loxone schreibt vor Prozent keine Leerstelle (`<v>%`), vor jeder anderen
# Einheit dagegen schon (`<v.3> kW`, `<v.1> °C`) — belegt an den 26 realen
# Vorlagen aus Spec 6.1.
_UNITS_WITHOUT_LEADING_SPACE: frozenset[str] = frozenset({"%"})


def unit_format(unit: str) -> str:
    """Loxone-Formatstring fuer eine Einheit, oder "" wenn keine bekannt ist."""
    decimals = _UNIT_DECIMALS.get(unit)
    if decimals is None:
        return ""
    separator = "" if unit in _UNITS_WITHOUT_LEADING_SPACE else " "
    return f"<v.{decimals}>{separator}{unit}"


# Cluster, deren Kommandos nie als Loxone-Ausgang erscheinen duerfen. Diese Liste
# gilt auch im Rohmodus. Jeder Eintrag ist eine bewusste Entscheidung, keine
# Aufzaehlung - die Liste ist absichtlich konservativ: ein zu Unrecht gesperrter
# Cluster kostet einen fehlenden Befehl, ein zu Unrecht vergessener kann das Geraet
# aus dem Netz werfen.
ADMINISTRATIVE_CLUSTERS: frozenset[int] = frozenset(
    {
        31,  # AccessControl - regelt, wer mit dem Geraet ueberhaupt sprechen darf
        41,  # OtaSoftwareUpdateProvider - ApplyUpdateRequest kann ein Firmware-Update erzwingen
        42,  # OtaSoftwareUpdateRequestor - AnnounceOtaProvider stoesst eine Update-Suche an
        48,  # GeneralCommissioning - Kommissionierung
        49,  # NetworkCommissioning - Kommissionierung
        50,  # DiagnosticLogs - RetrieveLogsRequest
        51,  # GeneralDiagnostics - TestEventTrigger
        52,  # SoftwareDiagnostics - ResetWatermarks setzt Diagnosezaehler zurueck
        53,  # ThreadNetworkDiagnostics - ResetCounts setzt Diagnosezaehler zurueck
        54,  # WiFiNetworkDiagnostics - ResetCounts setzt Diagnosezaehler zurueck
        55,  # EthernetNetworkDiagnostics - ResetCounts setzt Diagnosezaehler zurueck
        56,  # TimeSynchronization - SetUTCTime, SetTrustedTimeSource: eine falsche Uhr
        #     bricht Zeitplaene und Zertifikatspruefung
        60,  # AdministratorCommissioning - OpenCommissioningWindow oeffnet das Geraet fuer
        #     eine weitere Fabric
        62,  # OperationalCredentials - RemoveFabric
        63,  # GroupKeyManagement - verwaltet die Sicherheitsschluessel der Gruppen
        70,  # IcdManagement - RegisterClient steuert, wer das Geraet aufwecken darf
    }
)


def scale_factor(ref: SignalRef) -> float:
    """Faktor, mit dem ein roher Matter-Wert in die Loxone-Einheit uebergeht.

    1.0, wenn die Tabelle nichts sagt - unbekannte Cluster werden roh
    durchgereicht, nicht verworfen (Spec 3.5).
    """
    cluster = _table().get(ref.cluster_id, {})
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry:
        return 1.0
    return float(entry.get("scale", 1.0))


def command_slug(cluster_id: int, command_id: int) -> str | None:
    """Name eines Kommandos, oder None wenn es nicht in der Tabelle steht."""
    entry = (_table().get(cluster_id, {}).get("commands") or {}).get(command_id)
    return entry["slug"] if entry else None


def command_takes_value(cluster_id: int, command_id: int) -> bool:
    """Ob das Kommando einen Wert erwartet (z.B. MoveToLevel), oder keinen (z.B. Off)."""
    entry = (_table().get(cluster_id, {}).get("commands") or {}).get(command_id)
    return bool(entry and entry.get("takes_value"))


def known_command_pairs() -> set[tuple[int, int]]:
    """Alle (Cluster-ID, Kommando-ID)-Paare, die `clusters.yaml` unter
    `commands` fuehrt.

    Existiert einzig fuer den Konsistenz-Test gegen
    `commands.translate._PAYLOAD_BUILDERS` (siehe dort) - haelt beide
    Tabellen synchron, statt sie stillschweigend auseinanderlaufen zu lassen.
    Genau das ist Review-Fix C2 (2026-09-02) passiert: Cluster 768 Kommando
    10 stand in `_PAYLOAD_BUILDERS`, fehlte aber hier - der Rohexport baute
    daraus ein digitales Kommando, dessen Payload-Builder in Wirklichkeit
    einen Wert erwartete."""
    return {
        (cluster_id, command_id)
        for cluster_id, cluster in _table().items()
        for command_id in (cluster.get("commands") or {})
    }
