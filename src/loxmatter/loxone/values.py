"""Rechnet rohe Matter-Werte in das um, was der Miniserver erwartet.

Zwei Regeln aus Spec 7.3 pragen dieses Modul:

Zieleinheit ist die des Loxone-Bausteins, nicht die SI-Einheit. Der
Energiemanager erwartet kW, also liefern wir kW - auch wenn Matter in
Milliwatt misst.

Und daraus folgt das Zahlenformat: von mW nach kW sind sechs
Groessenordnungen. Wer hier auf zwei Nachkommastellen rundet, laesst jeden
Verbraucher unter 10 W als 0 erscheinen - und gerade die kleinen
Dauerverbraucher sind oft der Grund, eine messende Steckdose einzubauen.
"""

from __future__ import annotations

from loxmatter.matter.models import SignalRef
from loxmatter.profiles.table import Exportability, classify, scale_factor

MAX_DECIMALS = 6


def to_loxone_value(ref: SignalRef, raw: object) -> float | bool | None:
    """Skalierter Wert, oder None wenn Loxone ihn nicht aufnehmen kann."""
    kind = classify(raw)
    if kind is Exportability.DIGITAL:
        return bool(raw)
    if kind is not Exportability.ANALOG:
        return None
    assert isinstance(raw, (int, float))
    return float(raw) * scale_factor(ref)


def format_value(value: float | bool) -> str:
    """Textform fuer das Datagramm: bis zu sechs Nachkommastellen, ohne Nullen am Ende."""
    if isinstance(value, bool):
        return "1" if value else "0"
    text = f"{value:.{MAX_DECIMALS}f}".rstrip("0").rstrip(".")
    return text or "0"


def datagram(key: str, value: float | bool) -> bytes:
    """Ein UDP-Datagramm in der Form, die die exportierte Vorlage erkennt."""
    return f"{key}:{format_value(value)}".encode()
