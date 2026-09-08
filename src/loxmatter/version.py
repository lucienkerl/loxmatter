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

"""Woher die laufende Fassung ihre Identitaet kennt - Entwurf "Updates
ueber die Oberflaeche einspielen" (2026-09-08), Abschnitt 4.

Die Angaben kommen aus der UMGEBUNG, nicht aus dem Checkout auf dem Host.
`Dockerfile` legt sie beim Bau als `ENV` ab, gespeist aus Build-Argumenten,
die die CI setzt. Der Grund fuer diese Richtung: ein Checkout auf dem Host
kann inzwischen woanders stehen, weitergewandert oder umgezogen sein, ohne
dass das je ausgeliefert wurde - das Image dagegen IST, was laeuft.

Ausserhalb eines Images - im Entwicklungscheckout, wo `uv run loxmatter`
direkt startet - fehlen die Variablen. Das ist kein Fehlerfall, sondern
der Normalfall beim Entwickeln: `version` heisst dann "dev",
`commit`/`built_at` sind None. Wer daraus eine Ausnahme machte, koennte
die Bruecke ausserhalb von Docker nicht mehr starten.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loxmatter.model.store import schema_version


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str | None
    built_at: str | None
    schema_version: int


def _clean(name: str) -> str | None:
    """Leere Umgebungsvariablen wie fehlende behandeln.

    Docker Compose interpoliert eine in `.env` fehlende Variable zu einem
    LEEREN String, nicht zu "nicht gesetzt". Genau diese Falle hat bei
    `LOXMATTER_API_TOKEN` schon einmal zugeschlagen (siehe die ausfuehrliche
    Begruendung in deploy/testhost/docker-compose.yml); ohne diese Funktion
    hiesse die Version auf einem Host ohne gesetzten Wert "" statt "dev".
    """
    value = os.environ.get(name, "").strip()
    return value or None


def build_info() -> BuildInfo:
    return BuildInfo(
        version=_clean("LOXMATTER_VERSION") or "dev",
        commit=_clean("LOXMATTER_COMMIT"),
        built_at=_clean("LOXMATTER_BUILT_AT"),
        # Bewusst NICHT aus der Umgebung: siehe Docstring von
        # `test_die_schema_version_laesst_sich_aus_der_umgebung_nicht_faelschen`.
        schema_version=schema_version(),
    )
