"""Uebersetzt gespeicherte Signale in Loxone-Eingangsobjekte.

Zwei Regeln aus der Spec pragen dieses Modul:

Spec 6.3 — ein Matter-Event hat in Loxone kein Zuhause. Ein virtueller
UDP-Eingang kennt nur Werte. Jedes Event wird deshalb zu zwei Objekten: einem
digitalen Impuls, der die Flanke erzeugt, und einem monotonen Zaehler, der ein
verlorenes UDP-Paket ueberlebt, weil er dann nur springt statt zu verschlucken.

Spec 6.6 — Listen, Strukturen, Nullwerte und Texte werden hier verworfen. Sie
bleiben in der Ablage und in der Oberflaeche sichtbar, aber sie koennen kein
Loxone-Objekt werden.

Spec 7.3 — die Einheit eines Signals wandert nicht mehr in den Kommentar,
sondern wird ueber `profiles.table.unit_format` in einen Loxone-Formatstring
uebersetzt (`unit_format`-Feld). Digitale Eingaenge und Events tragen dort
immer `""`: ein Formatstring mit Nachkommastellen ergibt fuer einen Impuls
oder einen Zaehler keinen Sinn.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter.matter.models import SignalKind
from loxmatter.model.store import StoredSignal
from loxmatter.profiles.table import Exportability, unit_format

_DEVICE_PREFIX = re.compile(r"^(d\d+)_")


@dataclass(frozen=True)
class LoxoneInput:
    key: str
    title: str
    comment: str
    analog: bool
    unit_format: str


def _device_prefix(signals: Sequence[StoredSignal]) -> str:
    for signal in signals:
        match = _DEVICE_PREFIX.match(signal.key)
        if match:
            return match.group(1)
    return "d1"


def to_inputs(signals: Sequence[StoredSignal], device_label: str) -> list[LoxoneInput]:
    """Erzeugt die Eingangsobjekte eines Geraets, inklusive Online-Signal."""
    inputs: list[LoxoneInput] = []

    for signal in signals:
        comment = f"{device_label} · {signal.ref.path}"

        if signal.ref.kind is SignalKind.EVENT:
            inputs.append(LoxoneInput(signal.key, signal.title, f"{comment} · Impuls", False, ""))
            inputs.append(
                LoxoneInput(
                    f"{signal.key}_n", f"{signal.title} Zähler", f"{comment} · Zähler", True, ""
                )
            )
            continue

        if signal.exportability is Exportability.ANALOG:
            inputs.append(
                LoxoneInput(signal.key, signal.title, comment, True, unit_format(signal.unit))
            )
        elif signal.exportability is Exportability.DIGITAL:
            inputs.append(LoxoneInput(signal.key, signal.title, comment, False, ""))

    prefix = _device_prefix(signals)
    inputs.append(
        LoxoneInput(f"{prefix}_online", f"{device_label} erreichbar", device_label, False, "")
    )
    return inputs
