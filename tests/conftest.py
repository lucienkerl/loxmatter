"""Gemeinsame Fixtures für die gesamte Testsuite."""

from pathlib import Path

import pytest


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
