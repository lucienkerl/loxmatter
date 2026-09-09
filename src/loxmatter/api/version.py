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

"""Build identity via the API - draft "Deploy updates via the UI"
(2026-09-08), section 4.

`build_version_router` builds an `APIRouter` with prefix `/api`, just like
`api.settings.build_settings_router` - included in
`loxone.server.build_app` behind the same `api_guard`.

Unlike `GET /api/i18n`, this route is NOT exempted from login requirements:
the login page doesn't need it to display. Whoever
wants to know the version should be logged in - a version number is
for someone who's already on the network anyway, a useful hint
about which known gaps this installation still has.

No caching: `build_info()` reads `os.environ`, and that's
immutable in the running process - but tests set the variables
with `monkeypatch` per test case, and a cache would make exactly these tests
dependent on each other."""

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
