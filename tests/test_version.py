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

"""Tests for build identity - draft "Deploy updates via the UI"
(2026-09-08), section 4.

The four cases below cover exactly the four ways these
values could be wrong: not set at all (development checkout),
set to empty (Docker Compose interpolates a missing .env variable to an
empty string), set correctly, and - most importantly - the
schema version, which cannot be forged from the environment."""

from __future__ import annotations

from loxmatter.model import store as store_module
from loxmatter.version import build_info


def test_without_environment_the_bridge_reports_as_development_version(monkeypatch):
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None
    assert info.built_at is None


def test_empty_variables_are_treated_as_missing(monkeypatch):
    # Docker Compose interpolates a missing variable from .env to an
    # EMPTY string, not to "not set" - the same trap that once caught
    # LOXMATTER_API_TOKEN (see Compose file).
    monkeypatch.setenv("LOXMATTER_VERSION", "")
    monkeypatch.setenv("LOXMATTER_COMMIT", "   ")
    monkeypatch.delenv("LOXMATTER_BUILT_AT", raising=False)
    info = build_info()
    assert info.version == "dev"
    assert info.commit is None


def test_set_variables_pass_through_unchanged(monkeypatch):
    monkeypatch.setenv("LOXMATTER_VERSION", "0.3.0")
    monkeypatch.setenv("LOXMATTER_COMMIT", "a3f91c2")
    monkeypatch.setenv("LOXMATTER_BUILT_AT", "2026-09-08T10:00:00Z")
    info = build_info()
    assert info.version == "0.3.0"
    assert info.commit == "a3f91c2"
    assert info.built_at == "2026-09-08T10:00:00Z"


def test_schema_version_cannot_be_forged_from_the_environment(monkeypatch):
    """The only value that does NOT come from the environment.

    It also stands in the image as an ENV - but for the updater from
    stage 2, who reads it with `docker inspect` from an image that hasn't started yet.
    The running process already has it in memory anyway, and a
    second source would be a source that claims something different
    at some point."""
    monkeypatch.setenv("LOXMATTER_SCHEMA_VERSION", "999")
    assert build_info().schema_version == store_module._SCHEMA_VERSION
