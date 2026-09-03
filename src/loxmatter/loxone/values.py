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
from loxmatter.profiles.table import Exportability, classify, scale_factor, struct_member

MAX_DECIMALS = 6


def to_loxone_value(ref: SignalRef, raw: object) -> float | bool | None:
    """Skalierter Wert, oder None wenn Loxone ihn nicht aufnehmen kann."""
    raw = struct_member(ref, raw)
    kind = classify(raw)
    if kind is Exportability.DIGITAL:
        return bool(raw)
    if kind is not Exportability.ANALOG:
        return None
    assert isinstance(raw, (int, float))
    return float(raw) * scale_factor(ref)


def format_value(value: float | bool) -> str:
    """Textform fuer das Datagramm: bis zu sechs Nachkommastellen, ohne Nullen am Ende.

    Ein Wert, der auf null rundet, wird immer als "0" ausgegeben - unabhaengig vom
    Vorzeichen. Sonst liesse ein negativer Rundungsrest wie -1e-07 ein "-0" durch,
    das in der Loxone-Visualisierung schlicht falsch waere.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    text = f"{value:.{MAX_DECIMALS}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def datagram(key: str, value: float | bool) -> bytes:
    """Ein UDP-Datagramm in der Form, die die exportierte Vorlage erkennt."""
    return f"{key}:{format_value(value)}".encode()
