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

"""Tests fuer die Bau-Identitaet - Entwurf "Updates ueber die Oberflaeche
einspielen" (2026-09-08), Abschnitt 4.

Die vier Faelle unten decken genau die vier Wege ab, auf denen diese
Angaben falsch sein koennten: gar nicht gesetzt (Entwicklungscheckout),
leer gesetzt (Docker Compose interpoliert eine fehlende .env-Variable zu
einem leeren String), richtig gesetzt, und - der wichtigste - die
Schema-Version, die sich aus der Umgebung NICHT faelschen laesst."""

from __future__ import annotations

from loxmatter.model import store as store_module
from loxmatter.version import build_info


def test_ohne_umgebung_meldet_sich_die_bruecke_als_entwicklungsstand(monkeypatch):
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None
    assert info.built_at is None


def test_leere_variablen_gelten_wie_fehlende(monkeypatch):
    # Docker Compose interpoliert eine in .env fehlende Variable zu einem
    # LEEREN String, nicht zu "nicht gesetzt" - dieselbe Falle, die bei
    # LOXMATTER_API_TOKEN schon einmal zuschlug (siehe Compose-Datei).
    monkeypatch.setenv("LOXMATTER_VERSION", "")
    monkeypatch.setenv("LOXMATTER_COMMIT", "   ")
    monkeypatch.delenv("LOXMATTER_BUILT_AT", raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None


def test_gesetzte_variablen_kommen_unveraendert_durch(monkeypatch):
    monkeypatch.setenv("LOXMATTER_VERSION", "0.3.0")
    monkeypatch.setenv("LOXMATTER_COMMIT", "a3f91c2")
    monkeypatch.setenv("LOXMATTER_BUILT_AT", "2026-09-08T10:00:00Z")
    info = build_info()
    assert info.version == "0.3.0"
    assert info.commit == "a3f91c2"
    assert info.built_at == "2026-09-08T10:00:00Z"


def test_die_schema_version_laesst_sich_aus_der_umgebung_nicht_faelschen(monkeypatch):
    """Die einzige Angabe, die NICHT aus der Umgebung kommt.

    Im Image steht sie zusaetzlich als ENV - aber fuer den Updater aus
    Stufe 2, der sie mit `docker inspect` aus einem noch nicht gestarteten
    Image liest. Der laufende Prozess hat sie ohnehin im Speicher, und eine
    zweite Quelle waere eine Quelle, die irgendwann etwas anderes
    behauptet."""
    monkeypatch.setenv("LOXMATTER_SCHEMA_VERSION", "999")
    assert build_info().schema_version == store_module._SCHEMA_VERSION
