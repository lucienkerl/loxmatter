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

"""Die Bau-Identitaet ueber die API - Entwurf "Updates ueber die
Oberflaeche einspielen" (2026-09-08), Abschnitt 4.

`build_version_router` baut einen `APIRouter` mit Praefix `/api`, genau wie
`api.settings.build_settings_router` - eingebunden in
`loxone.server.build_app` hinter demselben `api_guard`.

Anders als `GET /api/i18n` ist diese Route NICHT von der Anmeldepflicht
ausgenommen: die Anmeldeseite braucht sie nicht, um sich anzuzeigen. Wer
die Version wissen will, soll angemeldet sein - eine Versionsnummer ist
fuer jemanden, der ohnehin schon im Netz steht, ein brauchbarer Hinweis
darauf, welche bekannten Luecken diese Installation noch hat.

Keine Zwischenspeicherung: `build_info()` liest `os.environ`, und das ist
im laufenden Prozess unveraenderlich - aber die Tests setzen die Variablen
mit `monkeypatch` pro Testfall, und ein Cache machte genau diese Tests
voneinander abhaengig."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from loxmatter.version import build_info


class VersionOut(BaseModel):
    version: str
    commit: str | None
    built_at: str | None
    schema_version: int


def build_version_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/version")
    async def get_version() -> VersionOut:
        info = build_info()
        return VersionOut(
            version=info.version,
            commit=info.commit,
            built_at=info.built_at,
            schema_version=info.schema_version,
        )

    return router
