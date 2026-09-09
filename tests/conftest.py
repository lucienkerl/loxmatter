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

"""Shared fixtures for the entire test suite."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from loxmatter import i18n


@pytest.fixture(autouse=True)
def isolate_loxmatter_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevents any test from touching the real database in the user's
    home directory.

    `export` places its signal-key database by default at
    `~/.loxmatter/loxmatter.sqlite` (see `loxmatter.cli._resolve_store_path`).
    Without this fixture, any test that calls `export` through the CLI
    and doesn't set `--store-path` itself would write to the real home
    database. Two safeguards: `LOXMATTER_STORE` points at a test
    directory, and additionally `Path.home()` itself points at a
    fake home below `tmp_path` - even if a test ever gets the
    precedence from `_resolve_store_path` wrong, the real home
    stays untouched.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("LOXMATTER_STORE", str(tmp_path / "autouse-loxmatter.sqlite"))


@pytest.fixture(autouse=True)
def reset_language() -> Iterator[None]:
    """Resets the global language setting before AND after every test.

    Resetting only after the test isn't enough: `cli.py`'s module-import
    bootstrap (see there) runs BEFORE any fixture and reads the real
    environment while doing so (LOXMATTER_LANG, a real saved setting) -
    without resetting BEFORE the test, the result of the very first test
    run in a session depends on the language state of the development
    environment pytest runs in (finding from the final review: with
    `LOXMATTER_LANG=de` in the environment, a test run individually and
    on purpose failed)."""
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
