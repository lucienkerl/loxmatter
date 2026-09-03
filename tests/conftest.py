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

"""Gemeinsame Fixtures für die gesamte Testsuite."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from loxmatter import i18n


@pytest.fixture(autouse=True)
def isolate_loxmatter_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verhindert, dass irgendein Test die echte Datenbank im Home-Verzeichnis
    des Nutzers anfasst.

    `export` legt seine Signalschlüssel-Datenbank standardmäßig unter
    `~/.loxmatter/loxmatter.sqlite` an (siehe `loxmatter.cli._resolve_store_path`).
    Ohne dieses Fixture würde jeder Test, der `export` über die CLI aufruft
    und `--store-path` nicht selbst setzt, in die echte Home-Datenbank
    schreiben. Zwei Absicherungen: `LOXMATTER_STORE` zeigt auf ein
    Test-Verzeichnis, und zusätzlich zeigt `Path.home()` selbst auf ein
    Fake-Home unterhalb von `tmp_path` — auch falls ein Test die
    Rangfolge aus `_resolve_store_path` einmal falsch nutzt, bleibt die
    echte Home unberührt.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("LOXMATTER_STORE", str(tmp_path / "autouse-loxmatter.sqlite"))


@pytest.fixture(autouse=True)
def reset_language() -> Iterator[None]:
    """Setzt die globale Spracheinstellung nach jedem Test zurueck.

    `loxmatter.i18n.set_language` haelt die aktuelle Sprache in einer
    prozessweiten Variable (eine gemeinsame Einstellung fuer die ganze
    Installation, siehe i18n/__init__.py) - ein Test, der `set_language("de")`
    aufruft und nicht zuruecksetzt, wuerde jeden nachfolgenden Test in
    derselben Session mit deutscher statt englischer Ausgabe konfrontieren."""
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
