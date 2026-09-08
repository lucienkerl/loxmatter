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

"""Tests for the CLI's language resolution (`cli._resolve_cli_language`) and
the `set-language` command.

`_resolve_cli_language` is tested as a pure function, not through a
module reload of `cli.py` - the module is imported exactly once per
test session (see `tests/test_cli.py`'s `from loxmatter.cli import app`),
so its module-top-level resolution cannot be repeated per test with
different environment variables. The ONE test that proves the
actual `--help` output in a language other than the one resolved at
session start deliberately starts a real subprocess for that (see
`test_help_text_is_german_when_loxmatter_lang_is_set`, marked `slow`
like `tests/api/test_live_smoke.py`)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from loxmatter import i18n
from loxmatter.cli import _resolve_cli_language, app
from loxmatter.model.store import Store


def test_env_override_wins_even_with_a_stored_setting(tmp_path):
    store_path = tmp_path / "t.sqlite"
    store = Store(store_path)
    try:
        store.locale.set_language("de")
    finally:
        store.close()
    assert _resolve_cli_language(store_path, {"LOXMATTER_LANG": "en"}) == "en"


def test_env_override_is_case_insensitive(tmp_path):
    store_path = tmp_path / "missing.sqlite"
    assert _resolve_cli_language(store_path, {"LOXMATTER_LANG": "DE"}) == "de"


def test_invalid_env_override_warns_and_falls_back(tmp_path, capsys):
    store_path = tmp_path / "missing.sqlite"
    result = _resolve_cli_language(store_path, {"LOXMATTER_LANG": "fr"})
    assert result == i18n.DEFAULT_LANGUAGE
    assert "LOXMATTER_LANG" in capsys.readouterr().err


def test_falls_back_to_default_when_no_database_exists(tmp_path):
    store_path = tmp_path / "missing.sqlite"
    assert _resolve_cli_language(store_path, {}) == i18n.DEFAULT_LANGUAGE


def test_reads_the_stored_setting_when_no_override_is_given(tmp_path):
    store_path = tmp_path / "t.sqlite"
    store = Store(store_path)
    try:
        store.locale.set_language("de")
    finally:
        store.close()
    assert _resolve_cli_language(store_path, {}) == "de"


def test_falls_back_to_default_when_the_database_file_is_not_a_database(tmp_path):
    store_path = tmp_path / "not-a-database.sqlite"
    store_path.write_text("not a sqlite file")
    assert _resolve_cli_language(store_path, {}) == i18n.DEFAULT_LANGUAGE


def test_set_language_command_persists_the_choice(tmp_path):
    store_path = tmp_path / "t.sqlite"
    Store(store_path).close()  # set-language deliberately does not create a new database
    result = CliRunner().invoke(app, ["set-language", "de", "--store-path", str(store_path)])
    assert result.exit_code == 0, result.stdout

    store = Store(store_path)
    try:
        assert store.locale.get_language() == "de"
    finally:
        store.close()


def test_set_language_command_confirms_in_the_newly_set_language(tmp_path):
    # set_language_cmd also updates the process-wide language before
    # printing the confirmation - otherwise the confirmation itself would
    # still appear in the language that is being left.
    store_path = tmp_path / "t.sqlite"
    Store(store_path).close()
    result = CliRunner().invoke(app, ["set-language", "de", "--store-path", str(store_path)])
    assert result.exit_code == 0, result.stdout
    assert "Sprache auf 'de' gesetzt." in result.stdout


def test_set_language_command_rejects_a_missing_database(tmp_path):
    store_path = tmp_path / "does-not-exist.sqlite"
    result = CliRunner().invoke(app, ["set-language", "de", "--store-path", str(store_path)])
    assert result.exit_code != 0


def test_set_language_command_rejects_an_unsupported_language(tmp_path):
    store_path = tmp_path / "t.sqlite"
    Store(store_path).close()
    result = CliRunner().invoke(app, ["set-language", "fr", "--store-path", str(store_path)])
    assert result.exit_code != 0


@pytest.mark.slow
def test_help_text_is_german_when_loxmatter_lang_is_set(tmp_path):
    """The only proof that `--help` text actually goes through the
    module-import-time resolution - `_resolve_cli_language` (above)
    only tests the resolution function on its own, not its
    wiring into `cli.py`'s module top level."""
    env = dict(os.environ)
    env["LOXMATTER_LANG"] = "de"
    env["LOXMATTER_STORE"] = str(tmp_path / "unused.sqlite")
    result = subprocess.run(
        [sys.executable, "-c", "from loxmatter.cli import app; app()", "inspect", "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Statt matter-server ein gespeichertes Abbild" in result.stdout

    # Additionally the app-level description (`--help` without a
    # subcommand): it depends on the file-ordering-dependent wiring from
    # an earlier fix, which per-command help texts like the ones above do
    # not cover - a revert of that wiring would not be noticed by the
    # test above alone.
    app_result = subprocess.run(
        [sys.executable, "-c", "from loxmatter.cli import app; app()", "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert app_result.returncode == 0, app_result.stderr
    assert "Matter → Loxone Bridge" in app_result.stdout
