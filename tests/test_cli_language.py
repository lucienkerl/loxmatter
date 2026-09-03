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

"""Tests fuer die Sprachaufloesung der CLI (`cli._resolve_cli_language`) und
das `set-language`-Kommando.

`_resolve_cli_language` wird als reine Funktion getestet, nicht ueber einen
Modul-Reload von `cli.py` - das Modul wird pro Testsession genau einmal
importiert (siehe `tests/test_cli.py`s `from loxmatter.cli import app`),
seine Modul-Top-Level-Aufloesung laesst sich deshalb nicht pro Test mit
unterschiedlichen Umgebungsvariablen wiederholen. Der EINE Test, der die
tatsaechliche `--help`-Ausgabe in einer anderen Sprache als der beim
Session-Start aufgeloesten belegt, startet dafuer bewusst einen echten
Unterprozess (siehe `test_help_text_is_german_when_loxmatter_lang_is_set`,
markiert `slow` wie `tests/api/test_live_smoke.py`)."""

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
    Store(store_path).close()  # set-language legt absichtlich keine neue Datenbank an
    result = CliRunner().invoke(app, ["set-language", "de", "--store-path", str(store_path)])
    assert result.exit_code == 0, result.stdout

    store = Store(store_path)
    try:
        assert store.locale.get_language() == "de"
    finally:
        store.close()


def test_set_language_command_confirms_in_the_newly_set_language(tmp_path):
    # set_language_cmd aktualisiert auch die prozessweite Sprache, bevor es
    # die Bestaetigung ausgibt - sonst wuerde die Bestaetigung selbst noch
    # in der Sprache erscheinen, die gerade verlassen wird.
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
    """Der einzige Beleg dafuer, dass `--help`-Text tatsaechlich die
    Modul-Import-Zeit-Aufloesung durchlaeuft - `_resolve_cli_language`
    (oben) prueft nur die Aufloesungsfunktion fuer sich, nicht ihre
    Verdrahtung in `cli.py`s Modul-Top-Level."""
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

    # Zusaetzlich die App-Level-Beschreibung (`--help` ohne Unterkommando):
    # sie haengt an der dateiordnungsabhaengigen Verdrahtung aus einer
    # frueheren Nachbesserung, die pro-Kommando-Hilfetexte wie oben nicht
    # abdecken - ein Revert dieser Verdrahtung wuerde vom Test oben allein
    # nicht bemerkt.
    app_result = subprocess.run(
        [sys.executable, "-c", "from loxmatter.cli import app; app()", "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert app_result.returncode == 0, app_result.stderr
    assert "Matter → Loxone Bridge" in app_result.stdout
