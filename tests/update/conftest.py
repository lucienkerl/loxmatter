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

"""Fixtures for update protocol tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_tmp_path_for_update_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Update protocol tests check for exact files in tmp_path. The parent
    conftest fixture creates fake-home there to isolate the store. We need
    a clean tmp_path for these tests, so we override the parent fixture's
    behavior by still setting the environment variable but cleaning up
    the fake-home directory that was just created."""
    fake_home = tmp_path / "fake-home"
    if fake_home.exists():
        import shutil

        shutil.rmtree(fake_home)
