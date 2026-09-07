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

"""Der sprechende Name EINES Endpunkts (Entwurf 2026-09-07, Abschnitt 7.4).

Steht neben `categories.py`, nicht darin, und das ist die ganze
Begruendung dieses Moduls: `category_for` beantwortet "was fuer ein Ding
ist das GERAET" - Leuchte, Steckdose, Schalter -, diese Datei "wie heisst
dieser eine Endpunkt DARIN". Eine Fernbedienung ist EIN Schalter mit ZWEI
Tasten; `CATEGORY_BY_DEVICE_TYPE` auf ihre Endpunkte angewandt ergaebe
"Schalter 1" und "Schalter 2", also zweimal denselben falschen Begriff.

Die Tabelle unten ist bewusst KLEIN. Sie fuehrt die Geraetetypen, die an
den eingecheckten Abbildern in tests/fixtures/nodes/ tatsaechlich
vorkommen, und sonst nichts - derselbe Anspruch wie bei
`UTILITY_ENDPOINT_KEEP_CLUSTERS` in `relevance.py`: ein neuer Eintrag
braucht eine konkrete Belegung, nicht die Annahme, die Tabelle sei von
sich aus vollstaendig. Alles Uebrige faellt auf "Endpunkt N" zurueck, und
das ist ein Name, der immer stimmt.
"""

from __future__ import annotations

from collections.abc import Mapping

from loxmatter import i18n
from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES

# Geraetetyp -> Uebersetzungsschluessel. Die Nummern stammen aus
# `matter_server.client.models.device_types` wie in `categories.py`; die
# Kommentare nennen den dortigen Klassennamen.
ENDPOINT_NAME_KEY_BY_DEVICE_TYPE: dict[int, str] = {
    0x000F: "web.signals.endpoint_button",  # GenericSwitch (IKEA BILRESA, Ep 1+2)
    0x010A: "web.signals.endpoint_socket",  # OnOffPlugInUnit (IKEA GRILLPLATS, Ep 1)
    0x010D: "web.signals.endpoint_light",  # ExtendedColorLight (synthetic_color_light, Ep 1)
    0x0510: "web.signals.endpoint_metering",  # ElectricalSensor (GRILLPLATS, Ep 2)
}

# Ein Endpunkt, der nur Verwaltung traegt, heisst schlicht "Geraet" - dort
# sitzt der Batteriestand, und "Endpunkt 0" waere fuer den Bedienenden eine
# Zahl ohne Bedeutung. PowerSource zaehlt hier mit, weil er allein noch
# keinen Nutz-Endpunkt macht (dieselbe Ueberlegung wie
# `_IGNORED_DEVICE_TYPES` in categories.py).
_DEVICE_ENDPOINT_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}


def _name_key(declared: frozenset[int]) -> str | None:
    """Der Schluessel fuer diesen Endpunkt, oder `None` fuer den Ruecktritt.

    Ein Nutz-Typ schlaegt den Verwaltungs-Typ: Endpunkt 0 der Fernbedienung
    deklariert Root Node UND Power Source UND OTA Requestor - er heisst
    "Geraet". Traegt ein Endpunkt dagegen beides, Verwaltung und einen
    benannten Nutz-Typ, gewinnt der Nutz-Typ, weil er mehr aussagt.
    """
    for device_type in sorted(declared):
        key = ENDPOINT_NAME_KEY_BY_DEVICE_TYPE.get(device_type)
        if key is not None:
            return key
    if declared & _DEVICE_ENDPOINT_TYPES:
        return "web.signals.endpoint_device"
    return None


def endpoint_labels(device_types: Mapping[int, frozenset[int]] | None) -> dict[int, str]:
    """Endpunktnummer -> fertiger, uebersetzter Name.

    Nummeriert wird nur, wo es etwas zu unterscheiden gibt: zwei
    Taster-Endpunkte ergeben "Taste 1" und "Taste 2", ein einzelner
    Steckdosen-Endpunkt bleibt "Steckdose" - eine "1" ohne "2" ist eine
    Nummer ohne Gegenstueck.

    `None` (Geraetetypen noch nicht nachgetragen, siehe
    `Store.backfill_device_types`) ergibt eine leere Zuordnung; der
    Aufrufer faellt dann fuer jeden Endpunkt auf `endpoint_plain` zurueck -
    dieselbe wortlose Behandlung, die `category_for(None)` mit `OTHER`
    bekommt. Ist `device_types` dagegen bekannt, aber ein einzelner
    Endpunkt traegt keinen der Typen aus `ENDPOINT_NAME_KEY_BY_DEVICE_TYPE`
    (Tabelle bewusst klein, siehe Moduldocstring), traegt schon DIESE
    Funktion "Endpunkt N" ein - nicht der Aufrufer. Nur so bleibt jeder in
    `device_types` bekannte Endpunkt im Ergebnis vertreten.
    """
    if not device_types:
        return {}

    keys: dict[int, str] = {}
    for endpoint, declared in device_types.items():
        key = _name_key(declared)
        if key is not None:
            keys[endpoint] = key

    counts: dict[str, int] = {}
    for key in keys.values():
        counts[key] = counts.get(key, 0) + 1

    # Jeder Endpunkt aus `device_types` bekommt einen Eintrag - auch einer
    # ohne aufgeloesten Schluessel faellt HIER schon auf "Endpunkt N"
    # zurueck, nicht erst beim Aufrufer. Nur so bleibt `labels[endpoint]`
    # fuer jeden bekannten Endpunkt verlaesslich; ein leeres Ergebnis (kein
    # Eintrag) bleibt allein dem Fall "device_types komplett None" (siehe
    # oben) vorbehalten.
    labels: dict[int, str] = {}
    seen: dict[str, int] = {}
    for endpoint in sorted(device_types):
        key = keys.get(endpoint)
        if key is None:
            labels[endpoint] = i18n.t("web.signals.endpoint_plain", endpoint=endpoint)
            continue
        name = i18n.t(key)
        if counts[key] == 1:
            labels[endpoint] = name
            continue
        seen[key] = seen.get(key, 0) + 1
        labels[endpoint] = i18n.t("web.signals.endpoint_numbered", name=name, index=seen[key])
    return labels
