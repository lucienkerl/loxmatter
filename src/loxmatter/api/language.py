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

"""Die gemeinsame Spracheinstellung ueber die API - Phase B+C, Spec-Abschnitt 5.

Zwei getrennte Router, weil sie unterschiedlich geschuetzt werden muessen
(`loxone.server.build_app` bindet sie deshalb mit unterschiedlichem
`dependencies=`-Argument ein, siehe dort):

- `build_i18n_router`: `GET /api/i18n` - UNGESCHUETZT. Die Ersteinrichtungs-
  und Anmeldeseite braucht diese Texte, um sich ueberhaupt anzuzeigen, bevor
  jemand angemeldet sein kann - dieselbe Notwendigkeit wie bei `/auth-info`
  (siehe `api/auth.py`), nur fuer Uebersetzungen statt Zugangsstatus.
- `build_language_router`: `PATCH /api/language` - geschuetzt wie jede
  andere `/api`-Route, die den Zustand der Installation aendert.

Liest `i18n`s eigene, bereits durch die Namensraum-Konvention (`web.*`)
gefilterte Teilmenge - kein zweiter, eigener Satz Uebersetzungen fuer den
Client, dieselbe `strings.yaml` wie ueberall sonst."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loxmatter import i18n
from loxmatter.model.store import Store


class I18nOut(BaseModel):
    language: str
    strings: dict[str, str]


class LanguageIn(BaseModel):
    language: str


class LanguageOut(BaseModel):
    language: str


def _web_strings() -> dict[str, str]:
    """Alle `web.*`-Schluessel, unaufgeloest (mit noch unbefuellten
    {platzhaltern}) in der aktuellen Sprache - der Browser fuellt sie
    selbst (siehe app.js, t()). i18n.raw_template() statt i18n.t(), weil
    t() sofort mit KeyError abstuerzen wuerde, sobald ein web.*-Schluessel
    ueberhaupt einen Platzhalter traegt (Befund aus der Umsetzung der
    WebUI-Uebersetzungsmechanik)."""
    return {key: i18n.raw_template(key) for key in i18n.strings_with_prefix("web.")}


def build_i18n_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/i18n")
    async def get_i18n() -> I18nOut:
        return I18nOut(language=i18n.current_language(), strings=_web_strings())

    return router


def build_language_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.patch("/language")
    async def set_language(body: LanguageIn) -> LanguageOut:
        if body.language not in i18n.SUPPORTED_LANGUAGES:
            raise HTTPException(
                status_code=400,
                detail=i18n.t(
                    "api.language.fail_unsupported",
                    language=body.language,
                    supported=", ".join(sorted(i18n.SUPPORTED_LANGUAGES)),
                ),
            )
        store.locale.set_language(body.language)
        return LanguageOut(language=body.language)

    return router
