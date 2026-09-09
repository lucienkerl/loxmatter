# loxmatter - connects Matter devices to a Loxone Miniserver.
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

"""The shared language setting across the API - Phase B+C, spec section 5.

Two separate routers, because they need to be protected differently
(`loxone.server.build_app` therefore wires them in with a different
`dependencies=` argument, see there):

- `build_i18n_router`: `GET /api/i18n` - UNPROTECTED. The initial-setup and
  login page needs these texts to display itself at all before anyone can be
  logged in - the same necessity as with `/auth-info` (see `api/auth.py`),
  just for translations instead of access status.
- `build_language_router`: `PATCH /api/language` - protected like every
  other `/api` route that changes the installation's state.

Reads `i18n`'s own subset, already filtered by the namespace convention
(`web.*`) - not a second, separate set of translations for the client, the
same `strings.yaml` as everywhere else."""

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
    """All `web.*` keys, unresolved (with {placeholders} still unfilled) in
    the current language - the browser fills them in itself (see app.js,
    t()). i18n.raw_template() instead of i18n.t(), because t() would crash
    immediately with a KeyError as soon as a web.* key carries a placeholder
    at all (a finding from implementing the WebUI translation mechanism)."""
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
